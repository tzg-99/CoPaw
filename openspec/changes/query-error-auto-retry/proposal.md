## Why

Agent 会话中经常出现各类瞬时报错（网络超时、连接中断、432 Token 速率限制等），当前项目在 LLM API 调用层已有完善的重试机制（`RetryChatModel`），但在**查询执行层**缺少重试能力。当 Agent 主循环（`_reasoning` / `_acting`）抛出异常时，错误直接传播给用户，没有恢复尝试，导致用户体验不佳。

## What Changes

- 新增查询级自动重试机制，在 `query_handler` 层面捕获瞬时错误并自动重试
- 重试时重新创建 Agent 实例，从持久化的会话状态恢复上下文
- 新增可重试错误分类器，支持识别 432 Token 限制、网络超时、连接中断等瞬时错误
- 扩展 LLM 重试状态码集合，加入 432（Token 速率限制）
- 新增 `QueryRetryConfig` 配置模型，支持通过环境变量和前端 UI 配置重试策略
- 前端新增查询重试配置卡片，支持启用/禁用、最大重试次数、退避策略等配置

## Capabilities

### New Capabilities

- `query-error-retry`: 查询级自动重试能力，包括错误分类、重试循环、Agent 重建、会话状态恢复

### Modified Capabilities

- `llm-retry`: 扩展可重试状态码集合，加入 432 Token 速率限制

## Impact

**后端代码**：
- `src/swe/constant.py` - 新增 3 个查询重试常量
- `src/swe/config/config.py` - 新增 `QueryRetryConfig` 配置模型
- `src/swe/app/runner/retry_classifier.py` - 新建可重试错误分类器
- `src/swe/app/runner/runner.py` - 核心：在 query_handler 中增加重试循环
- `src/swe/providers/retry_chat_model.py` - 扩展 `RETRYABLE_STATUS_CODES`

**前端代码**：
- `console/src/api/types/agent.ts` - 新增 TypeScript 类型
- `console/src/pages/Agent/Config/components/QueryRetryCard.tsx` - 新建配置卡片
- `console/src/locales/*/translation.json` - i18n 翻译

**测试**：
- `tests/unit/app/test_runner_query_retry.py` - 新建单元测试

**API**：
- Agent 配置 API 响应中新增 `query_retry` 字段
