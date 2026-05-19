## ADDED Requirements

### Requirement: 查询级自动重试

系统 SHALL 在 `query_handler` 层面捕获瞬时错误并自动重试 Agent 执行。

#### Scenario: 网络超时后重试成功

- **WHEN** Agent 执行过程中抛出 `asyncio.TimeoutError`
- **AND** 查询重试功能已启用
- **AND** 重试次数未达到上限
- **THEN** 系统等待退避时间后重建 Agent 实例
- **AND** 从持久化的会话状态恢复上下文
- **AND** 重新执行查询并返回成功结果

#### Scenario: 432 Token 限制后重试成功

- **WHEN** Agent 执行过程中抛出 `APIStatusError(status_code=432)`
- **AND** 错误消息包含 "输入Token数已达到每分钟上限"
- **AND** 查询重试功能已启用
- **THEN** 系统识别为可重试错误并执行重试

#### Scenario: 连接中断后重试成功

- **WHEN** Agent 执行过程中抛出 `ConnectionResetError`
- **AND** 查询重试功能已启用
- **THEN** 系统识别为可重试错误并执行重试

#### Scenario: 不可重试错误不重试

- **WHEN** Agent 执行过程中抛出 `ValueError`
- **THEN** 系统直接向上传播异常，不执行重试

#### Scenario: 用户取消不重试

- **WHEN** Agent 执行过程中抛出 `asyncio.CancelledError`
- **THEN** 系统直接向上传播异常，不执行重试

#### Scenario: 重试次数耗尽

- **WHEN** Agent 执行过程中抛出可重试错误
- **AND** 已达到最大重试次数
- **THEN** 系统向上传播最后一次异常

### Requirement: 可重试错误分类

系统 SHALL 通过 `is_query_retryable(exc)` 函数判断异常是否为可重试的瞬时错误。

#### Scenario: 通过 status_code 识别 432 错误

- **WHEN** 异常具有 `status_code` 属性且值为 432
- **THEN** 函数返回 `True`

#### Scenario: 通过 status_code 识别 429 错误

- **WHEN** 异常具有 `status_code` 属性且值为 429
- **THEN** 函数返回 `True`

#### Scenario: 通过 status_code 识别 5xx 错误

- **WHEN** 异常具有 `status_code` 属性且值在 {500, 502, 503, 504, 529}
- **THEN** 函数返回 `True`

#### Scenario: 通过消息匹配识别 Token 限制

- **WHEN** 异常消息包含 "输入Token数已达到每分钟上限"
- **THEN** 函数返回 `True`

#### Scenario: 通过异常类型识别网络超时

- **WHEN** 异常类型为 `asyncio.TimeoutError`
- **THEN** 函数返回 `True`

#### Scenario: 通过异常类型识别连接错误

- **WHEN** 异常类型为 `ConnectionError`、`ConnectionResetError` 或 `BrokenPipeError`
- **THEN** 函数返回 `True`

#### Scenario: CancelledError 不可重试

- **WHEN** 异常类型为 `asyncio.CancelledError`
- **THEN** 函数返回 `False`

### Requirement: Agent 重建与状态恢复

系统 SHALL 在重试时重建 Agent 实例并从持久化会话状态恢复。

#### Scenario: 重试时创建新 Agent 实例

- **WHEN** 触发查询重试
- **THEN** 系统创建新的 `SWEAgent` 实例
- **AND** 调用 `register_mcp_clients()` 注册 MCP 工具
- **AND** 调用 `get_state_loaded()` 恢复会话状态
- **AND** 调用 `rebuild_sys_prompt()` 重建系统提示词

#### Scenario: MCP 客户端复用

- **WHEN** 触发查询重试
- **THEN** 系统复用已构建的 MCP 客户端实例
- **AND** 不重新执行 `_build_and_connect_mcp_clients()`

### Requirement: 重试通知

系统 SHALL 在重试时向前端发送通知消息。

#### Scenario: 重试时发送通知

- **WHEN** 触发查询重试
- **AND** 重试次数 > 1
- **THEN** 系统向前端发送包含重试信息的 `Msg`
- **AND** 消息内容包含 "正在重试 ({attempt}/{max_attempts})"

### Requirement: 退避策略

系统 SHALL 使用指数退避策略控制重试间隔。

#### Scenario: 指数退避计算

- **WHEN** 第 N 次重试（N >= 1）
- **THEN** 退避时间 = min(backoff_cap, backoff_base * 2^(N-1))

#### Scenario: 退避上限

- **WHEN** 计算的退避时间超过 backoff_cap
- **THEN** 实际退避时间等于 backoff_cap

### Requirement: 查询重试配置

系统 SHALL 支持通过配置控制查询重试行为。

#### Scenario: 默认禁用

- **WHEN** 未配置 query_retry
- **THEN** 查询重试功能默认禁用

#### Scenario: 通过环境变量配置

- **WHEN** 设置环境变量 `SWE_QUERY_RETRY_MAX_RETRIES=3`
- **THEN** 最大重试次数为 3

#### Scenario: 通过 Agent 配置覆盖

- **WHEN** Agent 配置中 `query_retry.max_retries=5`
- **THEN** 该 Agent 的最大重试次数为 5
