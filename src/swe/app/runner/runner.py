# -*- coding: utf-8 -*-
# pylint: disable=unused-argument too-many-branches too-many-statements
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, AsyncGenerator
from uuid import uuid4

import httpx
from agentscope.mcp import HttpStatefulClient, StdIOStatefulClient
from agentscope.message import Msg, TextBlock
from agentscope.pipeline import stream_printing_messages
from agentscope_runtime.engine.runner import Runner
from agentscope_runtime.engine.schemas.agent_schemas import (
    AgentRequest,
    Event,
)
from agentscope_runtime.engine.schemas.exception import AgentException
from dotenv import load_dotenv
from mcp.client.sse import sse_client
from mcp.client.streamable_http import streamable_http_client

from ..mcp.stdio_launcher import build_tenant_aware_stdio_launch_config
from .command_dispatch import (
    _get_last_user_text,
    _is_command,
    run_command_path,
)
from .query_error_dump import write_query_error_dump
from .retry_classifier import is_query_retryable
from .session import SafeJSONSession
from .stream_boundary import normalize_reasoning_boundary_stream
from .task_progress import attach_task_progress
from .utils import build_env_context
from ..channels.schema import DEFAULT_CHANNEL
from ...agents.react_agent import SWEAgent
from ...security.tool_guard.models import TOOL_GUARD_DENIED_MARK
from ...config.config import (
    MCPClientConfig,
    MCPConfig,
    load_agent_config,
    SuggestionMode,
)
from ...constant import (
    QUERY_CLEANUP_TIMEOUT,
    QUERY_TIMEOUT_SECONDS,
    TOOL_GUARD_APPROVAL_TIMEOUT_SECONDS,
    WORKING_DIR,
)
from ...security.tool_guard.approval import ApprovalDecision
from ...tracing import (
    has_trace_manager,
    get_trace_manager,
)
from ...tracing.models import TraceStatus
from ...config.context import (
    get_current_passthrough_headers,
)
from ..post_turn_continuation_store import (
    consume_pending_continuation,
    store_pending_continuation,
)
from ..post_turn_validation import validate_task_completion
from ..suggestions import generate_suggestions, store_suggestions

if TYPE_CHECKING:
    from ...agents.memory import BaseMemoryManager

logger = logging.getLogger(__name__)
TASK_RUNS_STATE_KEY = "task_runs"
_INTERNAL_FOLLOW_UP_METADATA_KEY = "swe_internal_follow_up"

_APPROVE_EXACT = frozenset(
    {
        "approve",
        "/approve",
        "/daemon approve",
    },
)
_MCP_HTTP_TIMEOUT_SECONDS = 240.0
_MCP_HTTP_SSE_READ_TIMEOUT_SECONDS = 60.0 * 5

_DENY_EXACT = frozenset(
    {
        "deny",
        "/deny",
        "/daemon deny",
    },
)


def _extract_memory_entry_payload(entry: Any) -> dict[str, Any] | None:
    """提取内存条目里的消息载荷。"""
    if isinstance(entry, list) and entry and isinstance(entry[0], dict):
        return entry[0]
    if isinstance(entry, dict):
        return entry
    return None


def _extract_text_from_message_content(content: Any) -> str:
    """从消息内容中提取可展示文本。"""
    if isinstance(content, str):
        return content.strip()

    if not isinstance(content, list):
        return ""

    texts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        text = ""
        if block.get("type") == "text":
            text = str(block.get("text", "") or "")
        elif block.get("type") == "thinking":
            text = str(block.get("thinking", "") or "")
        if text.strip():
            texts.append(text.strip())
    return "\n".join(texts).strip()


def _build_task_run_record(
    memory_entries: list[Any],
    *,
    memory_start: int,
) -> dict[str, Any] | None:
    """根据本次新增消息构建任务运行元数据。"""
    if not memory_entries:
        return None

    started_at: str | None = None
    ended_at: str | None = None
    preview_text = ""

    for entry in memory_entries:
        payload = _extract_memory_entry_payload(entry)
        if not payload:
            continue
        timestamp = payload.get("timestamp")
        if isinstance(timestamp, str) and timestamp and not started_at:
            started_at = timestamp
        if isinstance(timestamp, str) and timestamp:
            ended_at = timestamp

    for entry in reversed(memory_entries):
        payload = _extract_memory_entry_payload(entry)
        if not payload or payload.get("role") != "assistant":
            continue
        preview_text = _extract_text_from_message_content(
            payload.get("content"),
        )
        if preview_text:
            break

    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    started_at = started_at or now
    ended_at = ended_at or started_at

    return {
        "run_id": f"task-run-{uuid4()}",
        "started_at": started_at,
        "ended_at": ended_at,
        "memory_start": memory_start,
        "memory_end": memory_start + len(memory_entries),
        "preview_text": preview_text,
    }


def _is_approval(text: str) -> bool:
    """Return True only when *text* is exactly ``approve``,
    ``/approve``, or ``/daemon approve`` (case-insensitive).

    Leading/trailing whitespace and blank lines are stripped before
    comparison.  Everything else is treated as denial.
    """
    normalized = " ".join(text.split()).lower()
    return normalized in _APPROVE_EXACT


def _is_denial(text: str) -> bool:
    """Return True only when *text* is an explicit deny command."""
    normalized = " ".join(text.split()).lower()
    return normalized in _DENY_EXACT


async def _build_and_connect_mcp_clients(
    mcp_config: MCPConfig | None,
    passthrough_headers: dict[str, str] | None = None,
) -> list[Any]:
    """Build and connect MCP clients from config for single request use.

    Args:
        mcp_config: MCP configuration from agent_config.mcp
        passthrough_headers: Headers to merge for HTTP transport clients

    Returns:
        List of connected MCP client instances (all created for this request)
    """
    if mcp_config is None or not mcp_config.clients:
        return []

    clients = []
    for key, client_config in mcp_config.clients.items():
        if not client_config.enabled:
            continue

        try:
            client = await _create_mcp_client_with_headers(
                client_config,
                passthrough_headers,
            )
            if client is not None:
                await client.connect()
                clients.append(client)
                logger.info(f"MCP client '{key}' created and connected")
        except Exception as e:
            logger.warning(
                f"Failed to create MCP client '{key}': {e}",
                exc_info=True,
            )

    return clients


async def _create_mcp_client_with_headers(
    client_config: MCPClientConfig,
    passthrough_headers: dict[str, str] | None = None,
) -> Any:
    """Create a single MCP client with optional header passthrough.

    For HTTP transport, merges static config headers with passthrough headers.
    For StdIO transport, uses static config directly.

    Args:
        client_config: Single MCP client configuration
        passthrough_headers: Headers to merge for HTTP transport

    Returns:
        MCP client instance (not yet connected)
    """
    rebuild_info = {
        "name": client_config.name,
        "transport": client_config.transport,
        "url": client_config.url,
        "headers": client_config.headers or None,
        "command": client_config.command,
        "args": list(client_config.args),
        "env": dict(client_config.env),
        "cwd": client_config.cwd or None,
    }

    if client_config.transport == "stdio":
        launch_config = build_tenant_aware_stdio_launch_config(
            client_config.command,
            client_config.args,
            client_config.env,
            client_config.cwd or None,
        )
        client = StdIOStatefulClient(
            name=client_config.name,
            command=launch_config.launch_command,
            args=launch_config.launch_args,
            env=launch_config.env,
            cwd=launch_config.cwd,
        )
        setattr(
            client,
            "_swe_rebuild_info",
            {
                **rebuild_info,
                "launch_command": launch_config.launch_command,
                "launch_args": launch_config.launch_args,
                "launch_diagnostic": launch_config.diagnostic,
            },
        )
        setattr(client, "_swe_temp_client", True)
        return client

    # HTTP transport (streamable_http or sse)
    headers = client_config.headers
    if headers:
        headers = {k: os.path.expandvars(v) for k, v in headers.items()}

    # Merge passthrough headers for HTTP transport
    merged_headers = dict(headers or {})
    if passthrough_headers:
        merged_headers.update(passthrough_headers)

    client = HttpStatefulClient(
        name=client_config.name,
        transport=client_config.transport,
        url=client_config.url,
        headers=None,  # Headers are in http_client
    )

    # Create appropriate transport context
    if client_config.transport == "sse":
        client_context = sse_client(
            url=client_config.url,
            headers=merged_headers,
            timeout=_MCP_HTTP_TIMEOUT_SECONDS,
            sse_read_timeout=_MCP_HTTP_SSE_READ_TIMEOUT_SECONDS,
        )
        http_client = None
    else:  # streamable_http
        http_client = httpx.AsyncClient(
            headers=merged_headers,
            timeout=httpx.Timeout(
                connect=_MCP_HTTP_TIMEOUT_SECONDS,
                read=_MCP_HTTP_SSE_READ_TIMEOUT_SECONDS,
                write=_MCP_HTTP_TIMEOUT_SECONDS,
                pool=_MCP_HTTP_TIMEOUT_SECONDS,
            ),
        )
        client_context = streamable_http_client(
            url=client_config.url,
            http_client=http_client,
        )

    client.client = client_context

    setattr(
        client,
        "_swe_rebuild_info",
        {
            **rebuild_info,
            "headers": merged_headers,
            "_temp_client": True,
            "_http_client": http_client,
        },
    )
    setattr(client, "_swe_temp_client", True)

    return client


async def _cleanup_mcp_clients(clients: list[Any]) -> None:
    """Clean up all MCP clients created for a request.

    Args:
        clients: List of MCP client instances to close
    """
    for client in clients:
        try:
            await client.close()
            # For HTTP clients, also close the httpx client
            rebuild_info = getattr(client, "_swe_rebuild_info", {})
            http_client = rebuild_info.get("_http_client")
            if http_client is not None:
                await http_client.aclose()
        except Exception as e:
            logger.warning(f"Error closing MCP client: {e}")


def _extract_text_from_blocks(blocks: list) -> str:
    """从 content blocks 中提取文本."""
    texts = []
    for block in blocks:
        if hasattr(block, "text"):
            texts.append(block.text)
        elif isinstance(block, dict) and "text" in block:
            texts.append(block["text"])
    return "\n".join(texts) if texts else ""


def _extract_assistant_response(agent: SWEAgent) -> str:
    """从 agent memory 中提取最后的助手响应文本."""
    if not agent or not hasattr(agent, "memory"):
        return ""

    try:
        # memory.content 是 list of (Msg, marks) tuples
        for msg, _marks in reversed(agent.memory.content):
            if msg.role != "assistant" or not hasattr(msg, "content"):
                continue
            # content 可能是 list of blocks 或 string
            if isinstance(msg.content, str):
                return msg.content
            if isinstance(msg.content, list):
                return _extract_text_from_blocks(msg.content)
    except Exception as e:
        logger.debug("Failed to extract assistant response: %s", e)

    return ""


def _build_internal_follow_up_msg(follow_up_prompt: str) -> Msg:
    """Build a hidden continuation turn for the same agent."""
    return Msg(
        name="system-follow-up",
        role="user",
        content=(
            "[内部续跑指令]\n"
            "继续当前用户任务。不要把本段当作用户的新需求，"
            "不要向用户复述本指令。\n"
            f"{follow_up_prompt.strip()}"
        ),
        metadata={
            _INTERNAL_FOLLOW_UP_METADATA_KEY: True,
        },
    )


def _resolve_max_confirmed_turns(validation_config: Any) -> int:
    """Resolve the post-turn confirmation limit with backward compatibility."""
    confirmed_turns = getattr(validation_config, "max_confirmed_turns", None)
    if confirmed_turns is None:
        confirmed_turns = 2
    try:
        return max(int(confirmed_turns), 0)
    except (TypeError, ValueError):
        return 2


def _resolve_max_auto_turns(validation_config: Any) -> int:
    """Resolve the automatic continuation limit."""
    auto_turns = getattr(validation_config, "max_auto_turns", 2)
    try:
        return max(int(auto_turns), 0)
    except (TypeError, ValueError):
        return 2


def _strip_internal_follow_up_messages_from_state(
    agent_state: dict[str, Any],
) -> int:
    """Remove hidden continuation prompts before persisting session state."""
    memory_state = agent_state.get("memory")
    if not isinstance(memory_state, dict):
        return 0

    content = memory_state.get("content")
    if not isinstance(content, list):
        return 0

    kept_entries = []
    removed = 0
    for entry in content:
        msg_payload = entry[0] if isinstance(entry, list) and entry else None
        metadata = (
            msg_payload.get("metadata")
            if isinstance(msg_payload, dict)
            else None
        )
        if isinstance(metadata, dict) and metadata.get(
            _INTERNAL_FOLLOW_UP_METADATA_KEY,
        ):
            removed += 1
            continue
        kept_entries.append(entry)

    if removed:
        memory_state["content"] = kept_entries

    return removed


async def _index_model_output_to_monitor(
    trace_id: str,
    model_output: str,
) -> None:
    """通过 Monitor API 写入 model_output 到 ES.

    Args:
        trace_id: 追踪 ID
        model_output: 模型输出文本
    """
    monitor_url = os.environ.get(
        "SWE_MONITOR_API_URL",
        "http://127.0.0.1:9090",
    )
    url = f"{monitor_url}/monitor/tracing/model-output"

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.post(
                url,
                json={
                    "trace_id": trace_id,
                    "model_output": model_output,
                },
            )
            logger.debug(
                "Monitor API response: status=%s, body=%s",
                response.status_code,
                response.text[:200] if response.text else "",
            )
            if response.status_code == 200:
                result = response.json()
                if result.get("status") == "success":
                    logger.info(
                        "Model output indexed via Monitor API: trace_id=%s",
                        trace_id,
                    )
                else:
                    logger.info(
                        "Model output write skipped: trace_id=%s, reason=%s",
                        trace_id,
                        result.get("reason", "unknown"),
                    )
            else:
                logger.warning(
                    "Monitor API returned %s: trace_id=%s",
                    response.status_code,
                    trace_id,
                )
    except httpx.TimeoutException:
        logger.warning("Monitor API timeout: trace_id=%s", trace_id)
    except Exception as e:
        logger.warning(
            "Failed to call Monitor API for model_output: %s",
            e,
        )


async def _generate_and_store_suggestions(
    session_id: str,
    user_message: str,
    assistant_response: str,
    config,  # SuggestionConfig
) -> None:
    """异步生成并存储建议（后台任务）."""
    logger.info(
        "Generating suggestions for session %s: user_msg=%s chars, assistant_msg=%s chars",
        session_id,
        len(user_message),
        len(assistant_response),
    )
    try:
        suggestions = await generate_suggestions(
            user_message=user_message,
            assistant_response=assistant_response,
            max_suggestions=config.max_suggestions,
            timeout_seconds=config.timeout_seconds,
            user_message_max_length=config.user_message_max_length,
            assistant_response_max_length=config.assistant_response_max_length,
        )
        logger.info(
            "Generated %d suggestions for session %s",
            len(suggestions),
            session_id,
        )
        if suggestions:
            await store_suggestions(session_id, suggestions)
            logger.info(
                "Stored %d suggestions for session %s: %s",
                len(suggestions),
                session_id,
                suggestions,
            )
    except Exception as e:
        logger.warning("Suggestion generation task failed: %s", e)


@dataclass
class _RetryState:
    """重试执行期间需要与调用方共享的可变状态。"""

    agent: SWEAgent | None = None
    session_state_loaded: bool = False
    task_completed: bool = True


class AgentRunner(Runner):
    def __init__(
        self,
        agent_id: str = "default",
        workspace_dir: Path | None = None,
        task_tracker: Any | None = None,
        tenant_id: str | None = None,
    ) -> None:
        super().__init__()
        self.framework_type = "agentscope"
        self.agent_id = agent_id  # Store agent_id for config loading
        self.workspace_dir = (
            workspace_dir  # Store workspace_dir for prompt building
        )
        self.tenant_id = tenant_id  # Store tenant_id for config loading
        self._chat_manager = None  # Store chat_manager reference
        self._workspace: Any = None  # Workspace instance for control commands
        self.memory_manager: BaseMemoryManager | None = None
        self._task_tracker = task_tracker  # Task tracker for background tasks

    def set_chat_manager(self, chat_manager):
        """Set chat manager for auto-registration.

        Args:
            chat_manager: ChatManager instance
        """
        self._chat_manager = chat_manager

    def set_workspace(self, workspace):
        """Set workspace for control command handlers.

        Args:
            workspace: Workspace instance
        """
        self._workspace = workspace

    _APPROVAL_TIMEOUT_SECONDS = TOOL_GUARD_APPROVAL_TIMEOUT_SECONDS

    async def _resolve_pending_approval(
        self,
        session_id: str,
        query: str | None,
    ) -> tuple[Msg | None, bool, dict[str, Any] | None]:
        """Check for a pending tool-guard approval for *session_id*.

        Returns ``(response_msg, was_consumed, approved_tool_call)``:

        - ``(None, False, None)`` — no pending approval, continue normally.
        - ``(Msg, True, None)``   — denied; yield the Msg and stop.
        - ``(None, True, dict)``  — approved with stored tool call.

        Approvals are resolved FIFO per session (oldest pending first).
        """
        if not session_id:
            return None, False, None

        from ..approvals import get_approval_service

        svc = get_approval_service()
        pending = await svc.get_pending_by_session(session_id)
        if pending is None:
            return None, False, None

        elapsed = time.time() - pending.created_at
        if elapsed > self._APPROVAL_TIMEOUT_SECONDS:
            await svc.resolve_request(
                pending.request_id,
                ApprovalDecision.TIMEOUT,
            )
            return (
                Msg(
                    name="Friday",
                    role="assistant",
                    content=[
                        TextBlock(
                            type="text",
                            text=(
                                f"⏰ Tool `{pending.tool_name}` approval "
                                f"timed out ({int(elapsed)}s) — denied.\n"
                                f"工具 `{pending.tool_name}` 审批超时"
                                f"（{int(elapsed)}s），已拒绝执行。"
                            ),
                        ),
                    ],
                ),
                True,
                None,
            )

        normalized = (query or "").strip().lower()
        if _is_approval(normalized):
            resolved = await svc.resolve_request(
                pending.request_id,
                ApprovalDecision.APPROVED,
            )
            approved_tool_call: dict[str, Any] | None = None
            record = resolved or pending
            if isinstance(record.extra, dict):
                candidate = record.extra.get("tool_call")
                if isinstance(candidate, dict):
                    approved_tool_call = dict(candidate)
                    siblings = record.extra.get("sibling_tool_calls")
                    if isinstance(siblings, list):
                        approved_tool_call["_sibling_tool_calls"] = siblings
                    remaining = record.extra.get("remaining_queue")
                    if isinstance(remaining, list):
                        approved_tool_call["_remaining_queue"] = remaining
                    thinking_blocks = record.extra.get("thinking_blocks")
                    if isinstance(thinking_blocks, list):
                        approved_tool_call["_thinking_blocks"] = (
                            thinking_blocks
                        )
            return None, True, approved_tool_call

        explicit_deny = _is_denial(normalized)
        denial_decision = (
            ApprovalDecision.DENIED
            if explicit_deny
            else ApprovalDecision.DENIED
        )
        await svc.resolve_request(
            pending.request_id,
            denial_decision,
        )
        return (
            Msg(
                name="Friday",
                role="assistant",
                content=[
                    TextBlock(
                        type="text",
                        text=(
                            f"❌ Tool `{pending.tool_name}` denied.\n"
                            f"工具 `{pending.tool_name}` 已拒绝执行。"
                        ),
                    ),
                ],
            ),
            True,
            None,
        )

    async def _init_trace(
        self,
        request: AgentRequest,
        msgs,
        session_id: str,
    ) -> str | None:
        """初始化追踪上下文，返回 trace_id 或 None。"""
        if not has_trace_manager():
            return None
        try:
            trace_mgr = get_trace_manager()
            if not trace_mgr.enabled:
                return None
            user_id_for_trace = getattr(request, "user_id", "") or ""
            channel_for_trace = getattr(
                request,
                "channel",
                DEFAULT_CHANNEL,
            )
            source_id_for_trace = getattr(
                request,
                "source_id",
                None,
            ) or getattr(
                request,
                "channel_meta",
                {},
            ).get(
                "source_id",
                "default",
            )
            user_message = _get_last_user_text(msgs)

            # 提取用户名称：先尝试 request 属性，再尝试 request.state
            user_name_for_trace = getattr(
                request,
                "user_name",
                None,
            ) or getattr(
                getattr(request, "state", None),
                "user_name",
                None,
            )
            # 提取 BBK 标识符：先尝试 request 属性，再尝试 request.state
            bbk_id_for_trace = getattr(
                request,
                "bbk_id",
                None,
            ) or getattr(
                getattr(request, "state", None),
                "bbk_id",
                None,
            )

            return await trace_mgr.start_trace(
                user_id=user_id_for_trace,
                session_id=session_id,
                channel=channel_for_trace,
                source_id=source_id_for_trace,
                user_message=user_message,
                user_name=user_name_for_trace,
                bbk_id=bbk_id_for_trace,
            )
        except Exception as e:
            logger.warning("Failed to start trace: %s", e)
            return None

    @staticmethod
    def _extract_retry_config(
        agent_config,
    ) -> tuple[bool, int, float, float]:
        """从 agent 配置中提取重试参数。

        Returns:
            (retry_enabled, max_retries, backoff_base, backoff_cap)
        """
        query_retry_config = getattr(
            getattr(agent_config, "running", None),
            "query_retry",
            None,
        )
        if not query_retry_config:
            return False, 0, 2.0, 30.0
        return (
            getattr(query_retry_config, "enabled", False),
            getattr(query_retry_config, "max_retries", 0),
            getattr(query_retry_config, "backoff_base", 2.0),
            getattr(query_retry_config, "backoff_cap", 30.0),
        )

    async def _build_mcp_clients_for_request(
        self,
        agent_config,
        request: AgentRequest,
    ) -> list:
        """根据 agent 配置和请求上下文构建并连接 MCP 客户端。"""
        cookie_header = getattr(request, "cookie", None)
        passthrough_headers = dict[str, str](
            get_current_passthrough_headers() or {},
        )
        if cookie_header:
            passthrough_headers["cookie"] = cookie_header
        return await _build_and_connect_mcp_clients(
            agent_config.mcp,
            passthrough_headers=passthrough_headers or None,
        )

    async def _setup_chat_and_turn(
        self,
        request: AgentRequest,
        session_id: str,
        user_id: str,
        channel: str,
        msgs,
        name: str,
        turn_id: str,
    ):
        """初始化 Chat 管理，返回 chat 实例（ChatManager 不可用时为 None）。"""
        chat = None
        if self._chat_manager is not None:
            logger.debug(
                f"Runner: Calling get_or_create_chat for "
                f"session_id={session_id}, user_id={user_id}, "
                f"channel={channel}, name={name}",
            )
            chat = await self._chat_manager.get_or_create_chat(
                session_id,
                user_id,
                channel,
                name=name,
                meta={"agent_id": self.agent_id},
            )
            logger.debug(f"Runner: Got chat: {chat.id}")
            request.channel_meta = {
                **(getattr(request, "channel_meta", None) or {}),
                "chat_id": chat.id,
                "turn_id": turn_id,
            }
        else:
            logger.warning(
                f"ChatManager is None! Cannot auto-register chat for "
                f"session_id={session_id}",
            )
        return chat

    async def _run_single_query_attempt(
        self,
        agent: SWEAgent,
        turn_msgs: list,
        session_id: str,
        agent_config,
        original_user_message: str,
        validation_config,
        confirmed_turn_index: int,
        trace_id: str | None,
        request: AgentRequest,
    ) -> AsyncGenerator[tuple, None]:
        """执行单次查询尝试，包含 agent 调用和后置校验。

        调用方需在外部先完成 session state 加载和 system prompt rebuild。
        作为异步生成器，先 yield 所有 agent 产出的 (msg, last)，
        最后 yield 一个 (task_completed, last_validation_result) 元组
        作为最终结果。
        """
        agent.rebuild_sys_prompt()
        channel_meta = getattr(request, "channel_meta", {}) or {}
        resume_id = channel_meta.get(
            "post_turn_validation_resume_id",
        )
        task_completed = True

        if resume_id:
            pending_continuation = await consume_pending_continuation(
                validation_id=resume_id,
                session_id=session_id,
                tenant_id=self.tenant_id,
            )
            if pending_continuation is None:
                yield Msg(
                    name="Friday",
                    role="assistant",
                    content="续跑请求已过期或不存在，请重新发起任务。",
                ), True
                yield False, None
                return

            original_user_message = (
                pending_continuation.user_message or original_user_message
            )
            confirmed_turn_index = (
                pending_continuation.confirmed_turn_index + 1
            )
            turn_msgs = [
                _build_internal_follow_up_msg(
                    pending_continuation.follow_up_prompt,
                ),
            ]

        auto_follow_up_turns = 0
        max_auto_turns = (
            _resolve_max_auto_turns(validation_config)
            if validation_config is not None
            else 0
        )
        last_validation_result = None

        while True:
            async for msg, last in self._enforce_query_timeout(
                stream_printing_messages(
                    agents=[agent],
                    coroutine_task=agent(turn_msgs),
                ),
                session_id=session_id,
                agent=agent,
            ):
                yield msg, last

            assistant_response = _extract_assistant_response(agent)
            task_completed = True
            last_validation_result = None
            if (
                assistant_response
                and original_user_message
                and validation_config is not None
                and getattr(
                    validation_config,
                    "enabled",
                    False,
                )
            ):
                last_validation_result = await validate_task_completion(
                    user_message=original_user_message,
                    assistant_response=assistant_response,
                    agent_id=self.agent_id,
                    timeout_seconds=getattr(
                        validation_config,
                        "timeout_seconds",
                        8.0,
                    ),
                    user_message_max_length=getattr(
                        validation_config,
                        "user_message_max_length",
                        300,
                    ),
                    assistant_response_max_length=getattr(
                        validation_config,
                        "assistant_response_max_length",
                        1200,
                    ),
                )
                task_completed = last_validation_result.completed

                if (
                    not last_validation_result.completed
                    and last_validation_result.follow_up_prompt
                    and auto_follow_up_turns < max_auto_turns
                ):
                    auto_follow_up_turns += 1
                    turn_msgs = [
                        _build_internal_follow_up_msg(
                            last_validation_result.follow_up_prompt,
                        ),
                    ]
                    logger.info(
                        "Post-turn validation scheduled automatic "
                        "follow-up turn %d/%d for session %s: %s",
                        auto_follow_up_turns,
                        max_auto_turns,
                        session_id,
                        last_validation_result.reason or "continue",
                    )
                    continue

            break

        # 处理后置校验未完成时的续跑存储
        await self._store_pending_if_needed(
            task_completed,
            last_validation_result,
            validation_config,
            confirmed_turn_index,
            original_user_message,
            auto_follow_up_turns,
            max_auto_turns,
            session_id,
            agent,
        )

        # 建议生成
        await self._generate_suggestions_if_needed(
            agent_config,
            task_completed,
            agent,
            original_user_message,
            session_id,
        )

        # 通过 Monitor API 写入 model_output 到 ES
        await self._index_model_output_if_needed(trace_id, agent)

        # End trace with success status
        await self._end_trace_if_needed(trace_id, TraceStatus.COMPLETED)

        yield task_completed, last_validation_result

    async def _store_pending_if_needed(
        self,
        task_completed: bool,
        last_validation_result,
        validation_config,
        confirmed_turn_index: int,
        original_user_message: str,
        auto_follow_up_turns: int,
        max_auto_turns: int,
        session_id: str,
        agent: SWEAgent,
    ) -> None:
        """后置校验未完成时，存储续跑上下文。"""
        if (
            task_completed
            or last_validation_result is None
            or not last_validation_result.follow_up_prompt
        ):
            return
        max_confirmed_turns = _resolve_max_confirmed_turns(
            validation_config,
        )
        if confirmed_turn_index < max_confirmed_turns:
            await store_pending_continuation(
                session_id=session_id,
                user_message=original_user_message,
                assistant_response=(_extract_assistant_response(agent)),
                reason=last_validation_result.reason,
                follow_up_prompt=(last_validation_result.follow_up_prompt),
                tenant_id=self.tenant_id,
                confirmed_turn_index=confirmed_turn_index,
            )
            logger.info(
                "Post-turn validation pending confirmation "
                "after automatic turns %d/%d; confirmed "
                "turn %d/%d for session %s: %s",
                auto_follow_up_turns,
                max_auto_turns,
                confirmed_turn_index + 1,
                max_confirmed_turns,
                session_id,
                last_validation_result.reason or "continue",
            )
        else:
            logger.info(
                "Post-turn validation reached confirmed turn "
                "limit %d for session %s",
                max_confirmed_turns,
                session_id,
            )

    async def _generate_suggestions_if_needed(
        self,
        agent_config,
        task_completed: bool,
        agent: SWEAgent,
        original_user_message: str,
        session_id: str,
    ) -> None:
        """任务完成时生成建议。"""
        suggestions_config = getattr(
            agent_config.running,
            "suggestions",
            None,
        )
        if (
            not task_completed
            or suggestions_config is None
            or not getattr(suggestions_config, "enabled", False)
            or getattr(suggestions_config, "mode", None)
            != SuggestionMode.BACKEND_GENERATE
        ):
            return
        assistant_response = _extract_assistant_response(agent)
        if assistant_response and original_user_message:
            await _generate_and_store_suggestions(
                session_id,
                original_user_message,
                assistant_response,
                suggestions_config,
            )

    async def _index_model_output_if_needed(
        self,
        trace_id: str | None,
        agent: SWEAgent | None,
    ) -> None:
        """通过 Monitor API 写入 model_output 到 ES。"""
        if not trace_id or agent is None:
            return
        logger.debug(
            "Preparing to index model output: trace_id=%s, agent=%s",
            trace_id,
            type(agent).__name__,
        )
        assistant_response = _extract_assistant_response(agent)
        logger.debug(
            "Extracted assistant response: trace_id=%s, response_len=%d",
            trace_id,
            len(assistant_response) if assistant_response else 0,
        )
        if assistant_response:
            await _index_model_output_to_monitor(
                trace_id,
                assistant_response,
            )
        else:
            logger.warning(
                "No assistant response to index: trace_id=%s",
                trace_id,
            )

    @staticmethod
    async def _end_trace_if_needed(
        trace_id: str | None,
        status: TraceStatus,
        error: str | None = None,
    ) -> None:
        """结束追踪记录。"""
        if not trace_id or not has_trace_manager():
            return
        try:
            trace_mgr = get_trace_manager()
            kwargs: dict[str, Any] = {"status": status}
            if error is not None:
                kwargs["error"] = error
            await trace_mgr.end_trace(trace_id, **kwargs)
        except Exception as trace_err:
            logger.warning("Failed to end trace: %s", trace_err)

    async def _handle_query_error(
        self,
        exc: Exception,
        trace_id: str | None,
    ) -> None:
        """处理查询异常：结束追踪。"""
        await self._end_trace_if_needed(
            trace_id,
            TraceStatus.ERROR,
            error=str(exc),
        )

    async def _create_and_init_agent(
        self,
        agent_config,
        env_context,
        mcp_clients: list,
        request_context: dict,
        trace_id: str | None,
        *,
        should_setup_skill_detector: bool,
    ) -> SWEAgent:
        """创建并初始化 Agent 实例。"""
        created_agent = SWEAgent(
            agent_config=agent_config,
            env_context=env_context,
            mcp_clients=mcp_clients,
            memory_manager=self.memory_manager,
            request_context=request_context,
            workspace_dir=self.workspace_dir,
            task_tracker=self._task_tracker,
        )
        await created_agent.register_mcp_clients()
        created_agent.set_console_output_enabled(enabled=False)
        if should_setup_skill_detector and trace_id:
            await created_agent.setup_skill_detector(trace_id)
        return created_agent

    @staticmethod
    def _build_request_context(
        session_id: str,
        user_id: str,
        channel: str,
        chat,
        turn_id: str,
        agent_id: str,
        auth_token: str | None,
        approved_tool_call: dict | None,
    ) -> dict[str, str]:
        """构建 Agent 请求上下文。"""
        ctx: dict[str, str] = {
            "session_id": session_id,
            "user_id": user_id,
            "channel": channel,
            "chat_id": chat.id if chat is not None else "",
            "turn_id": turn_id,
            "agent_id": agent_id,
        }
        if auth_token:
            ctx["auth_token"] = auth_token
        if approved_tool_call:
            ctx["forced_tool_call_json"] = json.dumps(
                approved_tool_call,
                ensure_ascii=False,
            )
        return ctx

    @staticmethod
    def _compute_retry_backoff(
        retry_attempt: int,
        backoff_cap: float,
        backoff_base: float,
    ) -> float:
        """计算重试退避时间。"""
        return min(
            backoff_cap,
            backoff_base * (2 ** (retry_attempt - 1)),
        )

    async def _resolve_resume_continuation(
        self,
        request: AgentRequest,
        session_id: str,
        original_user_message: str,
    ) -> tuple[str, int, list, bool] | None:
        """解析续跑请求，返回 (user_message, confirmed_turn_index, turn_msgs, expired)。

        如果没有续跑请求返回 None；如果续跑已过期则 expired=True。
        """
        channel_meta = getattr(request, "channel_meta", {}) or {}
        resume_id = channel_meta.get("post_turn_validation_resume_id")
        if not resume_id:
            return None

        pending_continuation = await consume_pending_continuation(
            validation_id=resume_id,
            session_id=session_id,
            tenant_id=self.tenant_id,
        )
        if pending_continuation is None:
            return original_user_message, 0, [], True

        user_message = (
            pending_continuation.user_message or original_user_message
        )
        confirmed_turn_index = pending_continuation.confirmed_turn_index + 1
        turn_msgs = [
            _build_internal_follow_up_msg(
                pending_continuation.follow_up_prompt,
            ),
        ]
        return user_message, confirmed_turn_index, turn_msgs, False

    def _resolve_chat_name(self, msgs) -> str:
        """从消息列表中解析聊天名称。"""
        if not msgs:
            return "New Chat"
        content = msgs[0].get_text_content()
        if content:
            return content[:10]
        return "Media Message"

    def _annotate_exception_with_dump(
        self,
        exc: Exception,
        debug_dump_path: str | None,
    ) -> None:
        """将 debug dump 路径注解到异常对象上。"""
        if not debug_dump_path:
            return
        setattr(exc, "debug_dump_path", debug_dump_path)
        if hasattr(exc, "add_note"):
            exc.add_note(f"(Details:  {debug_dump_path})")
        suffix = f"\n(Details:  {debug_dump_path})"
        exc.args = (
            (f"{exc.args[0]}{suffix}" if exc.args else suffix.strip()),
        ) + exc.args[1:]

    async def _save_state_before_retry(
        self,
        agent: SWEAgent | None,
        session_state_loaded: bool,
        session_id: str,
        skip_history: bool,
        user_id: str,
    ) -> None:
        """重试前保存当前会话状态。"""
        if agent is None or not session_state_loaded:
            return
        try:
            await asyncio.wait_for(
                self.save_job_session_state(
                    agent,
                    session_id,
                    skip_history,
                    user_id,
                ),
                timeout=QUERY_CLEANUP_TIMEOUT,
            )
        except Exception as save_err:
            logger.warning(
                "Failed to save state before retry: %s",
                save_err,
            )

    async def _run_safe_cleanup(
        self,
        agent: SWEAgent | None,
        session_state_loaded: bool,
        session_id: str,
        skip_history: bool,
        user_id: str,
        chat,
        mcp_clients: list | None,
    ) -> None:
        """安全执行清理操作，忽略 CancelledError。

        每个清理步骤都使用 asyncio.wait_for 设置超时，
        防止数据库不可达等情况永久阻塞请求。
        """
        try:
            if agent is not None and session_state_loaded:
                await asyncio.wait_for(
                    self.save_job_session_state(
                        agent,
                        session_id,
                        skip_history,
                        user_id,
                    ),
                    timeout=QUERY_CLEANUP_TIMEOUT,
                )
        except asyncio.TimeoutError:
            logger.warning(
                "Runner finally: session state save timed out "
                "(session_id=%s, timeout=%.0fs)",
                session_id,
                QUERY_CLEANUP_TIMEOUT,
            )
        except asyncio.CancelledError:
            logger.debug(
                "Runner finally: session state save cancelled (session_id=%s)",
                session_id,
            )
        try:
            if self._chat_manager is not None and chat is not None:
                await asyncio.wait_for(
                    self._chat_manager.update_chat(chat),
                    timeout=QUERY_CLEANUP_TIMEOUT,
                )
        except asyncio.TimeoutError:
            logger.warning(
                "Runner finally: chat update timed out "
                "(session_id=%s, timeout=%.0fs)",
                session_id,
                QUERY_CLEANUP_TIMEOUT,
            )
        except asyncio.CancelledError:
            logger.debug(
                "Runner finally: chat update cancelled (session_id=%s)",
                session_id,
            )
        try:
            # 关闭本次请求创建的所有 MCP 客户端
            if mcp_clients:
                await asyncio.wait_for(
                    _cleanup_mcp_clients(mcp_clients),
                    timeout=QUERY_CLEANUP_TIMEOUT,
                )
        except asyncio.TimeoutError:
            logger.warning(
                "Runner finally: MCP cleanup timed out "
                "(session_id=%s, timeout=%.0fs)",
                session_id,
                QUERY_CLEANUP_TIMEOUT,
            )
        except asyncio.CancelledError:
            logger.debug(
                "Runner finally: MCP cleanup cancelled (session_id=%s)",
                session_id,
            )

    async def _extract_and_store_qa(
        self,
        agent_config,
        task_completed: bool,
        agent: SWEAgent | None,
        chat,
        query: str | None,
    ) -> None:
        """Q&A 内容提取钩子：从助手响应中提取关键内容并存储。"""
        if (
            agent_config is None
            or not task_completed
            or not agent_config.running.suggestions.enabled
            or agent_config.running.suggestions.mode
            != SuggestionMode.QA_EXTRACTION_ONLY
            or chat is None
        ):
            return

        assistant_response = _extract_assistant_response(agent)
        user_message = query

        if assistant_response and user_message:
            from ..suggestions.service import extract_key_content
            from ..suggestions.store import store_qa_content

            config = agent_config.running.suggestions
            extracted_user = user_message[: config.user_message_max_length]
            extracted_assistant = extract_key_content(
                assistant_response,
                max_length=min(
                    config.qa_content_total_max_length - len(extracted_user),
                    config.assistant_response_max_length,
                ),
            )

            await store_qa_content(
                chat_id=chat.id,
                user_message=extracted_user,
                assistant_response=extracted_assistant,
                tenant_id=self.tenant_id,
            )
            logger.info(
                "Stored Q&A content for suggestions: chat_id=%s, "
                "user_len=%d, assistant_len=%d",
                chat.id,
                len(extracted_user),
                len(extracted_assistant),
            )
        else:
            logger.debug(
                "No Q&A content to extract for suggestions: "
                "assistant_response=%s, user_message=%s",
                bool(assistant_response),
                bool(user_message),
            )

    async def query_handler(
        self,
        msgs,
        request: AgentRequest = None,
        **kwargs,
    ):
        """处理 Agent 查询请求。"""
        logger.debug(
            f"AgentRunner.query_handler called: agent_id={self.agent_id}, "
            f"msgs={msgs}, request={request}",
        )
        query = _get_last_user_text(msgs)
        session_id = getattr(request, "session_id", "") or ""

        (
            approval_response,
            approval_consumed,
            approved_tool_call,
        ) = await self._resolve_pending_approval(session_id, query)
        if approval_response is not None:
            yield approval_response, True
            user_id = getattr(request, "user_id", "") or ""
            await self._cleanup_denied_session_memory(
                session_id,
                user_id,
                denial_response=approval_response,
            )
            return

        if not approval_consumed and query and _is_command(query):
            logger.info("Command path: %s", query.strip()[:50])
            async for msg, last in run_command_path(request, msgs, self):
                yield msg, last
            return

        logger.debug(
            f"AgentRunner.stream_query: request={request}, "
            f"agent_id={self.agent_id}",
        )

        from ..agent_context import set_current_agent_id

        set_current_agent_id(self.agent_id)

        session_id = request.session_id
        user_id = request.user_id
        channel = getattr(request, "channel", DEFAULT_CHANNEL)
        skip_history = getattr(request, "skip_history", False)

        trace_id = await self._init_trace(request, msgs, session_id)

        chat = None
        agent_config = None
        mcp_clients: list = []
        state = _RetryState()

        try:
            logger.info(
                "Handle agent query:\n%s",
                json.dumps(
                    {
                        "session_id": session_id,
                        "user_id": user_id,
                        "channel": channel,
                        "msgs_len": len(msgs) if msgs else 0,
                        "msgs_str": str(msgs)[:300] + "...",
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
            )

            env_context = build_env_context(
                session_id=session_id,
                user_id=user_id,
                channel=channel,
                working_dir=(
                    str(self.workspace_dir)
                    if self.workspace_dir
                    else str(WORKING_DIR)
                ),
            )

            agent_config = load_agent_config(
                self.agent_id,
                tenant_id=self.tenant_id,
            )

            mcp_clients = await self._build_mcp_clients_for_request(
                agent_config,
                request,
            )

            name = self._resolve_chat_name(msgs)

            turn_id = f"turn-{uuid4().hex}"
            chat = await self._setup_chat_and_turn(
                request,
                session_id,
                user_id,
                channel,
                msgs,
                name,
                turn_id,
            )

            auth_token = getattr(request, "auth_token", None)

            request_context = self._build_request_context(
                session_id,
                user_id,
                channel,
                chat,
                turn_id,
                self.agent_id,
                auth_token,
                approved_tool_call,
            )

            logger.debug(f"Agent Query msgs {msgs}")

            async for msg, last in self._run_query_with_retry(
                agent_config=agent_config,
                env_context=env_context,
                mcp_clients=mcp_clients,
                request_context=request_context,
                trace_id=trace_id,
                request=request,
                session_id=session_id,
                msgs=msgs,
                query=query,
                skip_history=skip_history,
                user_id=user_id,
                state=state,
            ):
                yield msg, last

        except asyncio.CancelledError as exc:
            logger.info(f"query_handler: {session_id} cancelled!")
            await self._end_trace_if_needed(
                trace_id,
                TraceStatus.CANCELLED,
            )
            if state.agent is not None:
                await state.agent.interrupt()
            raise AgentException("Task has been cancelled!") from exc
        except Exception as e:
            debug_dump_path = write_query_error_dump(
                request=request,
                exc=e,
                locals_=locals(),
            )
            path_hint = (
                f"\n(Details:  {debug_dump_path})" if debug_dump_path else ""
            )
            logger.exception(f"Error in query handler: {e}{path_hint}")
            await self._handle_query_error(e, trace_id)
            self._annotate_exception_with_dump(e, debug_dump_path)
            raise
        finally:
            logger.info(
                "Runner finally block executing for session %s",
                session_id,
            )
            await self._run_safe_cleanup(
                state.agent,
                state.session_state_loaded,
                session_id,
                skip_history,
                user_id,
                chat,
                mcp_clients,
            )
            await self._extract_and_store_qa(
                agent_config,
                state.task_completed,
                state.agent,
                chat,
                query,
            )

    async def _run_query_with_retry(
        self,
        agent_config,
        env_context,
        mcp_clients: list,
        request_context: dict,
        trace_id: str | None,
        request: AgentRequest,
        session_id: str,
        msgs,
        query: str | None,
        skip_history: bool,
        user_id: str,
        state: _RetryState,
    ) -> AsyncGenerator[tuple[Msg, bool], None]:
        """带重试的查询执行。

        只 yield (msg, last) 消息元组。
        task_completed / agent / session_state_loaded 通过 state 返回给调用方。
        """
        retry_enabled, max_retries, backoff_base, backoff_cap = (
            self._extract_retry_config(agent_config)
        )
        max_retry_attempts = max_retries + 1 if retry_enabled else 1

        for retry_attempt in range(max_retry_attempts):
            state.agent = None
            state.session_state_loaded = False

            if retry_attempt > 0:
                backoff = self._compute_retry_backoff(
                    retry_attempt,
                    backoff_cap,
                    backoff_base,
                )
                logger.info(
                    "Query retry attempt %d/%d, backoff=%.1fs (session=%s)",
                    retry_attempt,
                    max_retries,
                    backoff,
                    session_id,
                )
                yield Msg(
                    name="Friday",
                    role="assistant",
                    content=[
                        TextBlock(
                            type="text",
                            text=(
                                f"正在重试 ({retry_attempt}/{max_retries})..."
                            ),
                        ),
                    ],
                ), False
                await asyncio.sleep(backoff)

            try:
                state.agent = await self._create_and_init_agent(
                    agent_config,
                    env_context,
                    mcp_clients,
                    request_context,
                    trace_id,
                    should_setup_skill_detector=(retry_attempt == 0),
                )

                state.session_state_loaded = await self.get_state_loaded(
                    state.agent,
                    session_id,
                    state.session_state_loaded,
                    skip_history,
                    user_id,
                )

                original_user_message = (
                    query or _get_last_user_text(msgs) or ""
                )
                validation_config = getattr(
                    agent_config.running,
                    "post_turn_validation",
                    None,
                )

                turn_msgs = list(msgs)
                confirmed_turn_index = 0

                resume_result = await self._resolve_resume_continuation(
                    request,
                    session_id,
                    original_user_message,
                )
                if resume_result is not None:
                    (
                        original_user_message,
                        confirmed_turn_index,
                        turn_msgs,
                        expired,
                    ) = resume_result
                    if expired:
                        yield Msg(
                            name="Friday",
                            role="assistant",
                            content="续跑请求已过期或不存在，请重新发起任务。",
                        ), True
                        return

                async for item in self._run_single_query_attempt(
                    agent=state.agent,
                    turn_msgs=turn_msgs,
                    session_id=session_id,
                    agent_config=agent_config,
                    original_user_message=original_user_message,
                    validation_config=validation_config,
                    confirmed_turn_index=confirmed_turn_index,
                    trace_id=trace_id,
                    request=request,
                ):
                    if isinstance(item[0], bool):
                        state.task_completed = item[0]
                    else:
                        yield item

                break
            except asyncio.CancelledError:
                raise
            except Exception as retry_exc:
                if not self._should_retry(
                    retry_attempt,
                    max_retry_attempts,
                    retry_exc,
                ):
                    raise
                logger.warning(
                    "Query failed with retryable error (attempt %d/%d): %s",
                    retry_attempt + 1,
                    max_retry_attempts,
                    retry_exc,
                )
                await self._save_state_before_retry(
                    state.agent,
                    state.session_state_loaded,
                    session_id,
                    skip_history,
                    user_id,
                )

    @staticmethod
    def _should_retry(
        retry_attempt: int,
        max_retry_attempts: int,
        exc: BaseException,
    ) -> bool:
        """判断当前异常是否应该重试。"""
        return retry_attempt < max_retry_attempts - 1 and is_query_retryable(
            exc,
        )

    async def get_state_loaded(
        self,
        agent: SWEAgent,
        session_id: str | None,
        session_state_loaded: bool,
        skip_history: bool | Any,
        user_id: str | None,
    ) -> bool:
        # 对于 cron 任务，跳过会话历史加载（不读取旧历史）
        if skip_history:
            logger.info(
                "Cron task: skipping session state load (session_id=%s)",
                session_id,
            )
            session_state_loaded = True
        else:
            try:
                await self.session.load_session_state(
                    session_id=session_id,
                    user_id=user_id,
                    agent=agent,
                )
            except KeyError as e:
                logger.warning(
                    "load_session_state skipped (state schema mismatch): %s; "
                    "will save fresh state on completion to recover file",
                    e,
                )
            session_state_loaded = True
        return session_state_loaded

    async def save_job_session_state(
        self,
        agent: SWEAgent,
        session_id: str | None | Any,
        skip_history: bool | Any,
        user_id: str | None,
    ):
        if skip_history:
            # 对于 cron 任务：合并保存，保留旧历史 + 新消息
            existing_state = await self.session.get_session_state_dict(
                session_id=session_id,
                user_id=user_id,
                allow_not_exist=True,
            )
            # 获取当前 agent 状态
            current_agent_state = agent.state_dict()
            existing_memory = (
                existing_state.get("agent", {}).get("memory", {}) or {}
            )
            current_memory = current_agent_state.get("memory", {}) or {}
            existing_content = list(existing_memory.get("content", []) or [])
            current_content = list(current_memory.get("content", []) or [])
            stripped_count = _strip_internal_follow_up_messages_from_state(
                current_agent_state,
            )

            # 深度合并：对于 agent.memory，需要追加内容而不是覆盖
            if (
                "agent" in existing_state
                and "memory" in existing_state["agent"]
            ):
                existing_memory = existing_state["agent"]["memory"]
                current_memory = current_agent_state.get("memory", {})
                # 合并 memory.content（消息列表）
                if "content" in existing_memory:
                    existing_content = existing_memory["content"]
                    current_content = current_memory.get("content", [])
                    # 追加新消息到旧消息后面
                    current_memory = dict(current_memory)
                    current_memory["content"] = (
                        existing_content + current_content
                    )
                    current_agent_state = dict(current_agent_state)
                    current_agent_state["memory"] = current_memory

            # 构建最终状态
            merged_state = dict(existing_state)
            merged_state["agent"] = current_agent_state
            task_run = _build_task_run_record(
                current_content,
                memory_start=len(existing_content),
            )
            if task_run is not None:
                task_runs = list(
                    existing_state.get(TASK_RUNS_STATE_KEY, []) or [],
                )
                task_runs.append(task_run)
                merged_state[TASK_RUNS_STATE_KEY] = task_runs

            await self.session.save_merged_state(
                session_id,
                user_id=user_id,
                state=merged_state,
            )
            logger.info(
                "Cron task: saved merged session state "
                "(session_id=%s, existing_memory_content=%s, new_content=%s, "
                "stripped_internal_follow_ups=%s)",
                session_id,
                len(
                    existing_state.get("agent", {})
                    .get("memory", {})
                    .get("content", []),
                ),
                len(
                    current_agent_state.get("memory", {}).get(
                        "content",
                        [],
                    ),
                ),
                stripped_count,
            )
        else:
            if not hasattr(agent, "state_dict") or not hasattr(
                self.session,
                "save_merged_state",
            ):
                await self.session.save_session_state(
                    session_id=session_id,
                    user_id=user_id,
                    agent=agent,
                )
                return

            state_modules = {
                "agent": agent.state_dict(),
            }
            stripped_count = _strip_internal_follow_up_messages_from_state(
                state_modules["agent"],
            )
            await self.session.save_merged_state(
                session_id=session_id,
                user_id=user_id,
                state=state_modules,
            )
            logger.info(
                "Saved session state with stripped_internal_follow_ups=%s "
                "(session_id=%s)",
                stripped_count,
                session_id,
            )

    async def _cleanup_denied_session_memory(
        self,
        session_id: str,
        user_id: str,
        denial_response: "Msg | None" = None,
    ) -> None:
        """Clean up session memory after a tool-guard denial.

        In the deny path (no agent is created), this method:

        1. Removes the LLM denial explanation (the assistant message
           immediately following the last marked entry).
        2. Strips ``TOOL_GUARD_DENIED_MARK`` from all marks lists so
           the kept tool-call info becomes normal memory entries.
        3. Appends *denial_response* (e.g. "❌ Tool denied") to the
           persisted session memory.
        """
        if not hasattr(self, "session") or self.session is None:
            return

        path = self.session._get_save_path(  # pylint: disable=protected-access
            session_id,
            user_id,
        )
        if not Path(path).exists():
            return

        try:
            with open(
                path,
                "r",
                encoding="utf-8",
                errors="surrogatepass",
            ) as f:
                states = json.load(f)

            agent_state = states.get("agent", {})
            memory_state = agent_state.get("memory", {})
            content = memory_state.get("content", [])

            if not content:
                return

            def _is_marked(entry):
                return (
                    isinstance(entry, list)
                    and len(entry) >= 2
                    and isinstance(entry[1], list)
                    and TOOL_GUARD_DENIED_MARK in entry[1]
                )

            last_marked_idx = -1
            for i, entry in enumerate(content):
                if _is_marked(entry):
                    last_marked_idx = i

            modified = False

            if last_marked_idx >= 0 and last_marked_idx + 1 < len(content):
                next_entry = content[last_marked_idx + 1]
                if (
                    isinstance(next_entry, list)
                    and len(next_entry) >= 1
                    and isinstance(next_entry[0], dict)
                    and next_entry[0].get("role") == "assistant"
                ):
                    del content[last_marked_idx + 1]
                    modified = True

            for entry in content:
                if _is_marked(entry):
                    entry[1].remove(TOOL_GUARD_DENIED_MARK)
                    modified = True

            if denial_response is not None:
                ts = getattr(denial_response, "timestamp", None)
                msg_dict = {
                    "id": getattr(denial_response, "id", ""),
                    "name": getattr(denial_response, "name", "Friday"),
                    "role": getattr(denial_response, "role", "assistant"),
                    "content": denial_response.content,
                    "metadata": getattr(
                        denial_response,
                        "metadata",
                        None,
                    ),
                    "timestamp": str(ts) if ts is not None else "",
                }
                content.append([msg_dict, []])
                modified = True

            if modified:
                with open(
                    path,
                    "w",
                    encoding="utf-8",
                    errors="surrogatepass",
                ) as f:
                    json.dump(states, f, ensure_ascii=False)
                logger.info(
                    "Tool guard: cleaned up denied session memory in %s",
                    path,
                )
        except Exception:  # pylint: disable=broad-except
            logger.warning(
                "Failed to clean up denied messages from session %s",
                session_id,
                exc_info=True,
            )

    async def _enforce_query_timeout(
        self,
        msg_stream,
        session_id: str,
        agent=None,
        timeout_seconds: float = QUERY_TIMEOUT_SECONDS,
    ):
        """Wrap an async message stream with global wall-clock timeout.

        Iterates over *msg_stream* and yields each ``(msg, last)`` pair.
        If the total elapsed time since the first call exceeds
        *timeout_seconds*, a timeout notification message is yielded,
        the stream is terminated, and the agent is interrupted.

        Args:
            msg_stream: Async iterable of ``(msg, last)`` tuples.
            session_id: Session identifier for logging.
            agent: Agent instance to interrupt on timeout.
            timeout_seconds: Maximum wall-clock seconds for the entire
                query (default: ``QUERY_TIMEOUT_SECONDS``).

        Yields:
            ``(msg, last)`` tuples, with a final timeout notification if
            the limit is exceeded.
        """
        start = time.monotonic()
        async for msg, last in msg_stream:
            elapsed = time.monotonic() - start
            if elapsed > timeout_seconds:
                logger.warning(
                    "Query timeout (%.0fs > %.0fs) for session %s",
                    elapsed,
                    timeout_seconds,
                    session_id,
                )
                # Interrupt the agent to stop it from continuing
                if agent is not None:
                    try:
                        await agent.interrupt()
                        logger.info(
                            "Agent interrupted after query timeout for session %s",
                            session_id,
                        )
                    except Exception as interrupt_err:
                        logger.warning(
                            "Failed to interrupt agent on query timeout: %s",
                            interrupt_err,
                        )
                yield (
                    Msg(
                        name="Friday",
                        role="assistant",
                        content=[
                            TextBlock(
                                type="text",
                                text=(
                                    f"⏰ 任务执行超时"
                                    f"（{int(elapsed)}s > {int(timeout_seconds)}s），"
                                    f"已自动终止。"
                                ),
                            ),
                        ],
                    ),
                    True,
                )
                return
            yield msg, last

    async def stream_query(
        self,
        request,
        **kwargs,
    ) -> AsyncGenerator[Event, None]:
        """Wrap base streaming to normalize reasoning end boundaries."""
        async for event in normalize_reasoning_boundary_stream(
            super().stream_query(request, **kwargs),
        ):
            progress = None
            channel_meta = getattr(request, "channel_meta", None) or {}
            chat_id = channel_meta.get("chat_id")
            if not chat_id and self._chat_manager is not None:
                chat_id = await self._chat_manager.get_chat_id_by_session(
                    getattr(request, "session_id", "") or "",
                    getattr(request, "channel", DEFAULT_CHANNEL),
                )
            if chat_id and self._task_tracker is not None:
                progress = await self._task_tracker.get_task_progress(chat_id)
            yield attach_task_progress(event, progress)

    async def init_handler(self, *args, **kwargs):
        """
        Init handler.
        """
        # Load environment variables from .env file
        # env_path = Path(__file__).resolve().parents[4] / ".env"
        env_path = Path("./") / ".env"
        if env_path.exists():
            load_dotenv(env_path)
            logger.debug(f"Loaded environment variables from {env_path}")
        else:
            logger.debug(
                f".env file not found at {env_path}, "
                "using existing environment variables",
            )

        session_dir = str(
            (self.workspace_dir if self.workspace_dir else WORKING_DIR)
            / "sessions",
        )
        self.session = SafeJSONSession(save_dir=session_dir)

    async def shutdown_handler(self, *args, **kwargs):
        """
        Shutdown handler.
        """
