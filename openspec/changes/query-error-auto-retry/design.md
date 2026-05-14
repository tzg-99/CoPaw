## Context

当前项目在 LLM API 调用层已有完善的重试机制（`RetryChatModel`），但在查询执行层缺少重试能力。当 Agent 主循环（`_reasoning` / `_acting`）抛出异常时，错误直接传播给用户。

**现有机制**：
- `RetryChatModel` 包装所有 LLM 调用，支持指数退避重试
- `LLMRateLimiter` 提供并发控制和 QPM 限制
- MCP 客户端恢复机制（`_recover_mcp_client`）
- 工具执行超时保护（`_run_tool_call_with_hard_timeout`）
- 看门狗机制（`AGENT_WATCHDOG_TIMEOUT`）
- 后校验与自动续跑（`PostTurnValidationConfig`）

**关键文件**：
- `src/swe/app/runner/runner.py` - query_handler 入口
- `src/swe/agents/react_agent.py` - Agent 主循环
- `src/swe/providers/retry_chat_model.py` - LLM 重试
- `src/swe/config/config.py` - 配置模型

## Goals / Non-Goals

**Goals:**
- 在查询执行层增加自动重试能力，覆盖瞬时错误
- 支持 432 Token 速率限制、网络超时、连接中断等错误类型
- 重试时从持久化的会话状态恢复，保证上下文连续性
- 提供可配置的重试策略（次数、退避时间等）
- 前端 UI 支持配置查询重试参数

**Non-Goals:**
- 不改变现有 LLM API 调用层的重试机制
- 不处理非瞬时性错误（如配置错误、权限问题）
- 不实现跨查询级别的重试（仅限单次查询内）
- 不修改 Agent 主循环内部的错误处理逻辑

## Decisions

### 决策 1：重试位置选择在 query_handler 层

**选择**：在 `runner.py` 的 `query_handler` 方法中，Agent 执行块外层包裹重试循环

**备选方案**：
- A: 在 `SWEAgent.reply()` 内部重试 - 混淆了看门狗和工具守卫的职责
- B: 在 post-turn-validation 循环中重试 - 范围太窄，只覆盖 happy path
- C: 在整个 query_handler 级别重试 - 会重复执行 MCP 客户端初始化等昂贵操作

**理由**：
- Agent 实例可以安全重建（会话状态已持久化）
- MCP 客户端等昂贵资源只初始化一次
- 重试范围覆盖完整的 Agent 执行流程

### 决策 2：重试时重建 Agent 实例

**选择**：每次重试时创建新的 `SWEAgent` 实例，从 `SafeJSONSession` 恢复状态

**理由**：
- 避免复杂的部分状态回滚逻辑
- 会话状态已通过 `SafeJSONSession` 持久化，可安全恢复
- 看门狗、MCP 工具注册等状态随新实例自然重置

### 决策 3：可重试错误识别采用白名单 + 消息匹配

**选择**：
- 通过 `status_code` 属性识别 HTTP 错误（429, 432, 5xx）
- 通过异常类型识别网络错误（TimeoutError, ConnectionError 等）
- 通过正则匹配错误消息中的速率限制关键词

**理由**：
- 保守策略：明确列出可重试类型，避免误重试
- 432 是非标准状态码，需要额外的消息匹配兜底
- 消息匹配覆盖自定义异常类型

### 决策 4：前端配置使用嵌套表单字段

**选择**：使用 Ant Design 的 `name={["query_retry", "enabled"]}` 数组语法

**理由**：
- `QueryRetryConfig` 是嵌套的 Pydantic 模型
- 与现有 `LlmRetryCard` 的扁平字段名模式不同
- Ant Design 原生支持嵌套表单数据

## Risks / Trade-offs

| 风险 | 影响 | 缓解措施 |
|------|------|----------|
| 重试时会话状态丢失 | 用户丢失最近一轮对话 | `finally` 块中保存状态，最多丢失一轮 |
| 重试导致重复消息 | 前端显示重复内容 | 发送重试通知消息，前端可识别 |
| 432 错误非瞬时 | 重试无意义 | 通过消息匹配识别，保守策略不重试 |
| 重试次数过多 | 用户等待时间长 | 默认最多 2 次，退避上限 30s |
| Agent 重建开销 | 重试延迟增加 | MCP 客户端复用，只重建 Agent 实例 |
