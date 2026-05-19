## 1. 后端 API 实现

- [x] 1.1 在 `src/swe/app/routers/agent.py` 中新增 `AgentConfigDistributionRequest`、`AgentConfigDistributionTenantResult`、`AgentConfigDistributionResponse`、`AgentConfigDistributionTenantListResponse` Pydantic 模型
- [x] 1.2 在 `src/swe/app/routers/agent.py` 中新增 `CONFIG_GROUP_FIELDS` 常量，定义 8 个配置组到字段的映射（react_agent, llm_retry, query_retry, llm_rate_limiter, context_compact, tool_result_compact, memory_summary, embedding_config）
- [x] 1.3 在 `src/swe/app/routers/agent.py` 中新增 `GET /config/agent/distribution/tenants` 端点，调用 `list_logical_tenant_ids(source_id, source_filter=True)`
- [x] 1.4 在 `src/swe/app/routers/agent.py` 中新增 `_merge_config_group()` 辅助函数，实现 overwrite 和 fill_empty 两种配置合并策略
- [x] 1.5 在 `src/swe/app/routers/agent.py` 中新增 `_distribute_config_to_tenant()` 辅助函数，实现单租户分发逻辑：bootstrap → 加载目标配置 → 合并 → 保存 → 热重载
- [x] 1.6 在 `src/swe/app/routers/agent.py` 中新增 `POST /config/agent/distribute` 端点，校验 config_groups 和 target_tenant_ids，遍历调用 `_distribute_config_to_tenant()`
- [x] 1.7 在 `src/swe/app/_app.py` 中注册新的路由（如需）— 已确认无需额外注册

## 2. 前端 API 层

- [x] 2.1 在 `console/src/api/types/agent.ts` 中新增 `AgentConfigDistributionRequest`、`AgentConfigDistributionTenantResult`、`AgentConfigDistributionResponse`、`AgentConfigDistributionTenantListResponse` 类型定义
- [x] 2.2 在 `console/src/api/modules/agent.ts` 中新增 `listAgentConfigDistributionTenants()` 和 `distributeAgentConfig()` API 调用函数

## 3. 前端分发 Modal 组件

- [x] 3.1 在 `console/src/pages/Agent/Config/components/` 中新增 `DistributeModal.tsx`，实现分发 Modal 组件：TenantTargetPicker + overwrite/fill_empty 切换 + 提示文案
- [x] 3.2 在 `console/src/pages/Agent/Config/components/` 中新增 `DistributeModal.module.less` 样式文件
- [x] 3.3 在 `console/src/pages/Agent/Config/components/index.ts` 中导出 `DistributeModal`

## 4. 前端卡片分发按钮集成

- [x] 4.1 在 `console/src/pages/Agent/Config/index.tsx` 中新增 `CONFIG_GROUP_LABELS` 常量（配置组 key → 中文显示名称映射）
- [x] 4.2 在 `console/src/pages/Agent/Config/index.tsx` 中为每张卡片添加分发按钮（仅 default 租户可见），点击打开 DistributeModal 并传入对应 config_group
- [x] 4.3 在 `console/src/pages/Agent/Config/useAgentConfig.tsx` 中新增分发相关状态和方法（distributeModalOpen, currentConfigGroup, handleDistribute 等）
- [x] 4.4 在 `console/src/locales/zh.json` 和 `en.json` 中添加分发相关 i18n 翻译

## 5. 验证与测试

- [x] 5.1 Console 构建通过（`cd console && npm run build`）— ✓ built in 33.40s
- [ ] 5.2 手动验证：以 default 租户登录，在 Configuration 页面每张卡片上看到分发按钮
- [ ] 5.3 手动验证：点击分发按钮，Modal 正确加载租户列表，选择目标租户后分发成功
- [ ] 5.4 手动验证：overwrite 模式下目标租户配置被覆盖；fill_empty 模式下仅空值被填充
- [ ] 5.5 手动验证：非 default 租户看不到分发按钮
