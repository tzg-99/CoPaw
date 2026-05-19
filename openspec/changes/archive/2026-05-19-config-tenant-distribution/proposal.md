## Why

Configuration 页面的 8 张卡片（ReAct Agent、LLM Retry、Query Retry、LLM Rate Limiter、Context Compact、Tool Result Compact、Memory Summary、Embedding Config）当前仅保存到当前租户的 `agent.json`，无法批量分发到其他租户。其他配置页面（MCP、Skill Pool、Models、Channels、Workspace Files）已具备租户分发能力，Configuration 页面是唯一缺失分发的配置入口，运维人员需要逐个租户手动修改配置，效率低且易出错。

## What Changes

- 为 Configuration 页面的每张卡片添加"分发到租户"功能，支持按卡片维度选择性地将配置项分发到目标租户
- 新增后端 API 端点，支持 Agent 运行配置的租户分发
- 复用现有 `TenantTargetPicker` 组件和分发 Modal 模式，保持 UI 一致性
- 支持全量覆盖和仅填充空值两种分发模式

## Capabilities

### New Capabilities
- `agent-config-distribution`: Agent 运行配置（AgentsRunningConfig）按卡片维度分发到目标租户的能力，包括 API 端点、前端分发 UI 和配置合并逻辑

### Modified Capabilities

## Impact

- **后端**: `src/swe/app/routers/` 新增配置分发端点，`src/swe/app/workspace/tenant_initializer.py` 可能需要扩展
- **前端**: `console/src/pages/Agent/Config/` 各卡片组件添加分发按钮，新增分发 Modal 组件
- **API**: 新增 `/config/agent/distribution/tenants` 和 `/config/agent/distribute` 端点
- **依赖**: 复用 `TenantTargetPicker`、`list_logical_tenant_ids()`、`TenantInitializer` 等现有基础设施
