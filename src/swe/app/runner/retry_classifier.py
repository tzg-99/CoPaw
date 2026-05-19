# -*- coding: utf-8 -*-
"""查询级可重试错误分类器

判断 Agent 执行过程中抛出的异常是否属于可重试的瞬时错误，
供 query_handler 的重试循环使用。
"""

import asyncio
import errno
import re

# Token 速率限制及类似错误的消息关键词
_TOKEN_LIMIT_PATTERNS = [
    re.compile(r"token.*上限", re.IGNORECASE),
    re.compile(r"输入.*已达.*上限", re.IGNORECASE),
    re.compile(r"rate.?limit", re.IGNORECASE),
    re.compile(r"too many requests", re.IGNORECASE),
    re.compile(r"quota exceeded", re.IGNORECASE),
    re.compile(r"throttl", re.IGNORECASE),
]

# RuntimeError 中可重试的关键词
_RETRYABLE_RUNTIME_KEYWORDS = [
    "rate limiter",
    "timed out",
]

# 可重试的 HTTP 状态码
_RETRYABLE_STATUS_CODES = {429, 432, 433, 500, 502, 503, 504, 529}

# 可重试的 OSError errno
_RETRYABLE_ERRNOS = {
    errno.ECONNRESET,
    errno.ECONNREFUSED,
    errno.ETIMEDOUT,
    errno.EPIPE,
    errno.ENETUNREACH,
}

# 可重试的异常类型（不含 OSError，OSError 需单独检查 errno）
_RETRYABLE_EXCEPTION_TYPES: tuple[type[BaseException], ...] = (
    asyncio.TimeoutError,
    ConnectionError,
    ConnectionResetError,
    BrokenPipeError,
)

# httpx 异常类型（延迟导入，运行时检测）
_HTTPX_EXCEPTION_TYPES: tuple[type[BaseException], ...] = ()
try:
    import httpx

    _HTTPX_EXCEPTION_TYPES = (
        httpx.ConnectError,
        httpx.TimeoutException,
        httpx.RemoteProtocolError,
    )
except ImportError:
    pass


def _has_retryable_status_in_chain(exc: BaseException) -> bool:
    """检查异常及其异常链（__cause__ / __context__）中是否存在可重试的 status_code。

    当异常被上层框架包装为 RuntimeError 等通用类型时，原始的
    APIStatusError(status_code=432) 会作为 __cause__ 或 __context__
    存在。遍历异常链可以穿透包装层识别真正的可重试错误。
    """
    visited: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in visited:
        visited.add(id(current))
        status_code = getattr(current, "status_code", None)
        if status_code is not None and status_code in _RETRYABLE_STATUS_CODES:
            return True
        current = getattr(current, "__cause__", None) or getattr(
            current,
            "__context__",
            None,
        )
    return False


def is_query_retryable(exc: BaseException) -> bool:
    """判断异常是否为可重试的瞬时错误

    识别规则（任一匹配即返回 True）：
    1. CancelledError → 不可重试（返回 False）
    2. 异常或其异常链（__cause__ / __context__）具有 status_code
       属性且值在可重试集合中
    3. 异常类型为网络超时/连接错误
    4. OSError 且 errno 在可重试集合中
    5. httpx 连接/超时/协议错误
    6. RuntimeError 且消息含可重试关键词
    7. 异常消息匹配速率限制关键词
    """
    # CancelledError 永远不重试
    if isinstance(exc, asyncio.CancelledError):
        return False

    # 通过 status_code 属性识别 HTTP 错误（含异常链穿透）
    if _has_retryable_status_in_chain(exc):
        return True

    # 通过异常类型识别网络错误
    if isinstance(exc, _RETRYABLE_EXCEPTION_TYPES):
        return True

    # OSError 检查 errno，仅特定网络相关 errno 可重试
    if isinstance(exc, OSError):
        if getattr(exc, "errno", None) in _RETRYABLE_ERRNOS:
            return True

    # httpx 异常类型
    if _HTTPX_EXCEPTION_TYPES and isinstance(exc, _HTTPX_EXCEPTION_TYPES):
        return True

    # RuntimeError 关键词匹配
    if isinstance(exc, RuntimeError):
        exc_message = str(exc).lower()
        for keyword in _RETRYABLE_RUNTIME_KEYWORDS:
            if keyword in exc_message:
                return True

    # 通用消息匹配：速率限制、配额等
    exc_message = str(exc)
    for pattern in _TOKEN_LIMIT_PATTERNS:
        if pattern.search(exc_message):
            return True

    return False
