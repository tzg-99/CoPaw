## 1. 后端常量与配置

- [ ] 1.1 在 `src/swe/constant.py` 中新增 `QUERY_RETRY_MAX_RETRIES`、`QUERY_RETRY_BACKOFF_BASE`、`QUERY_RETRY_BACKOFF_CAP` 常量
- [ ] 1.2 在 `src/swe/config/config.py` 中新增 `QueryRetryConfig` 配置模型
- [ ] 1.3 在 `AgentsRunningConfig` 中添加 `query_retry` 字段

## 2. LLM 重试状态码扩展

- [ ] 2.1 在 `src/swe/providers/retry_chat_model.py` 中将 432 加入 `RETRYABLE_STATUS_CODES`

## 3. 可重试错误分类器

- [ ] 3.1 新建 `src/swe/app/runner/retry_classifier.py` 模块
- [ ] 3.2 实现 `is_query_retryable(exc)` 函数，支持 status_code、异常类型、消息匹配

## 4. Runner 重试循环

- [ ] 4.1 在 `src/swe/app/runner/runner.py` 的 `query_handler` 中读取 `query_retry` 配置
- [ ] 4.2 在 Agent 执行块外层包裹重试循环
- [ ] 4.3 实现重试时的 Agent 重建逻辑（创建新实例、注册 MCP、恢复状态）
- [ ] 4.4 实现重试通知消息发送
- [ ] 4.5 实现指数退避等待逻辑
- [ ] 4.6 处理 `CancelledError` 不重试的边界情况

## 5. 前端类型与组件

- [ ] 5.1 在 `console/src/api/types/agent.ts` 中新增 `QueryRetryConfig` TypeScript 接口
- [ ] 5.2 新建 `console/src/pages/Agent/Config/components/QueryRetryCard.tsx` 配置卡片
- [ ] 5.3 更新 `console/src/pages/Agent/Config/components/index.ts` 导出新组件
- [ ] 5.4 更新 `console/src/pages/Agent/Config/index.tsx` 引入配置卡片

## 6. i18n 国际化

- [ ] 6.1 在 `console/src/locales/en/translation.json` 中添加 `agentConfig.queryRetry*` 系列文案
- [ ] 6.2 在 `console/src/locales/zh/translation.json` 中添加中文翻译
- [ ] 6.3 在 `console/src/locales/ja/translation.json` 中添加日文翻译
- [ ] 6.4 在 `console/src/locales/ru/translation.json` 中添加俄文翻译

## 7. 单元测试

- [ ] 7.1 新建 `tests/unit/app/test_runner_query_retry.py` 测试文件
- [ ] 7.2 编写重试成功场景测试（ConnectionError 后成功）
- [ ] 7.3 编写不可重试错误测试（ValueError 不重试）
- [ ] 7.4 编写 CancelledError 不重试测试
- [ ] 7.5 编写重试次数耗尽测试
- [ ] 7.6 编写退避时间验证测试
- [ ] 7.7 编写默认禁用测试
- [ ] 7.8 编写 432 Token 限制错误重试测试
- [ ] 7.9 编写 Token 限制消息匹配测试
