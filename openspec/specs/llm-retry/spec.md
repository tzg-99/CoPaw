## MODIFIED Requirements

### Requirement: 可重试状态码集合

`RetryChatModel` SHALL 将 432 Token 速率限制错误加入可重试状态码集合。

#### Scenario: 432 错误触发重试

- **WHEN** LLM API 调用返回 HTTP 432 状态码
- **THEN** `RetryChatModel` 识别为可重试错误
- **AND** 按照指数退避策略执行重试

#### Scenario: 432 错误消息匹配

- **WHEN** LLM API 调用返回 432 错误
- **AND** 错误消息包含 "输入Token数已达到每分钟上限"
- **THEN** 系统正确识别为 Token 速率限制错误
