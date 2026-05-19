## Context

Configuration 页面（`/agent-config`）包含 8 张卡片，对应 `AgentsRunningConfig` 的不同配置域。当前这些配置仅保存到当前租户的 `agent.json`，无法批量分发到其他租户。系统中已有 5 个配置域具备租户分发能力（MCP、Skill Pool、Models、Channels、Workspace Files），均遵循相同的分发模式：`GET /tenants` 列表 + `POST /distribute` 分发 + `TenantTargetPicker` UI。

`AgentsRunningConfig` 是一个扁平的 Pydantic model，包含约 30 个字段，按卡片可划分为 8 个配置组。分发需要支持按卡片维度选择性分发，而非全量覆盖。

## Goals / Non-Goals

**Goals:**
- 为 Configuration 页面的每张卡片添加"分发到租户"按钮和分发 Modal
- 新增后端 API 支持按配置组（卡片维度）分发 `AgentsRunningConfig` 到目标租户
- 复用 `TenantTargetPicker` 和现有分发模式，保持 UI/API 一致性
- 支持全量覆盖（overwrite）和仅填充空值（fill_empty）两种分发策略

**Non-Goals:**
- 不改变 `AgentsRunningConfig` 的数据模型结构
- 不新增全局"一键分发所有配置"功能（按卡片分发即可）
- 不处理 `AgentProfileConfig`（非运行配置）的分发
- 不修改其他页面的分发逻辑

## Decisions

### 1. 配置组映射：卡片 → 字段组

每张卡片对应 `AgentsRunningConfig` 中的一组字段，分发时按组选择性地合并到目标租户：

| 卡片 | 配置组 key | 字段 |
|------|-----------|------|
| ReactAgentCard | `react_agent` | `max_iters`, `max_input_length`, `memory_manager_backend` |
| LlmRetryCard | `llm_retry` | `llm_retry_enabled`, `llm_max_retries`, `llm_backoff_base`, `llm_backoff_cap` |
| QueryRetryCard | `query_retry` | `query_retry` (整个子对象) |
| LlmRateLimiterCard | `llm_rate_limiter` | `llm_max_concurrent`, `llm_chat_max_concurrent`, `llm_cron_max_concurrent`, `llm_max_qpm`, `llm_rate_limit_pause`, `llm_rate_limit_jitter`, `llm_acquire_timeout`, `llm_chat_acquire_timeout`, `llm_cron_acquire_timeout` |
| ContextCompactCard | `context_compact` | `context_compact` (整个子对象) |
| ToolResultCompactCard | `tool_result_compact` | `tool_result_compact` (整个子对象) |
| MemorySummaryCard | `memory_summary` | `memory_summary` (整个子对象) |
| EmbeddingConfigCard | `embedding_config` | `embedding_config` (整个子对象) |

**理由**: 子对象类型的配置组（`query_retry`, `context_compact` 等）直接整体替换；扁平字段组（`react_agent`, `llm_retry`, `llm_rate_limiter`）按字段列表选择性合并。这避免了全量覆盖导致目标租户其他卡片配置被意外重写。

### 2. 分发策略：overwrite vs fill_empty

- **overwrite**: 源租户的配置值覆盖目标租户对应字段（默认模式）
- **fill_empty**: 仅填充目标租户中值为默认值/null 的字段，保留目标已有配置

**理由**: Channel 分发已实现 fill_empty 模式，运维场景中经常需要"只补缺不覆盖"。Agent 配置同理——不同租户可能有意调整了 rate limiter 参数，不应被覆盖。

### 3. 后端 API 设计

遵循现有模式，新增两个端点：

- `GET /config/agent/distribution/tenants` — 调用 `list_logical_tenant_ids(source_id, source_filter=True)`
- `POST /config/agent/distribute` — 接收 `{config_groups: list[str], target_tenant_ids: list[str], overwrite: bool}`

分发逻辑：对每个目标租户，加载其 `agent.json` 的 `running` 配置，按 `config_groups` 选择性地合并源配置字段，保存并热重载。

**理由**: 与 MCP/Skill/Channel 分发端点保持一致的 URL 命名和请求结构。`config_groups` 参数替代了其他端点的 `client_keys`/`skill_names`，表达"按卡片维度分发"的语义。

### 4. 前端 UI 设计

每张卡片右上角添加"分发"图标按钮（仅 `default` 租户可见），点击后打开分发 Modal：
- Modal 内包含 `TenantTargetPicker`、overwrite/fill_empty 切换、当前卡片名称提示
- 提交后显示分发结果（成功/失败明细）

**理由**: 按卡片维度分发比全局分发更精细，运维可以只分发需要统一的配置项（如 rate limiter），不影响其他租户自主调整的配置（如 memory summary）。

### 5. 配置合并实现

后端新增 `_merge_config_group()` 辅助函数：
- 对子对象组：整体替换（overwrite）或仅填充默认值字段（fill_empty）
- 对扁平字段组：逐字段替换或仅填充默认值/null 字段

**理由**: `AgentsRunningConfig` 的 Pydantic model 定义了每个字段的 `default` 值，可用于 fill_empty 判断——如果目标值等于 Pydantic default，视为"空"可填充。

## Risks / Trade-offs

- **[部分字段无明确 default]** → `react_agent` 组中的 `max_iters` default=100, `memory_manager_backend` default="remelight"，但 `max_input_length` 的 default 可能不适合所有场景。fill_empty 模式下需谨慎判断"空"的定义，建议仅对值为 `None` 或 `0` 的字段填充。
- **[热重载时机]** → 分发后需调用 `schedule_agent_reload()` 触发目标租户 Agent 重载。如果目标租户正在执行 query，重载可能导致中断。建议在分发 Modal 中提示此风险。
- **[语言/时区不分发]** → `language` 和 `timezone` 属于用户偏好而非运行配置，不应分发。`ReactAgentCard` 的分发组排除这两个字段。