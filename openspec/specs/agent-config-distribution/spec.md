## API Specification

### GET /config/agent/distribution/tenants

获取可分发的目标租户列表（排除源租户自身）。

**Request:**
- Method: GET
- Path: `/config/agent/distribution/tenants`
- Headers: `X-Tenant-Id` (源租户 ID)

**Response 200:**
```json
{
  "tenant_ids": ["tenant-a", "tenant-b"]
}
```

**Error 403:**
```json
{
  "detail": "Only default tenant can distribute configuration"
}
```

---

### POST /config/agent/distribute

将源租户的 Agent 运行配置按配置组分发到目标租户。

**Request:**
- Method: POST
- Path: `/config/agent/distribute`
- Headers: `X-Tenant-Id` (源租户 ID)
- Body:
```json
{
  "config_groups": ["llm_retry", "llm_rate_limiter"],
  "target_tenant_ids": ["tenant-a", "tenant-b"],
  "overwrite": true
}
```

**config_groups 枚举值:**
`react_agent`, `llm_retry`, `query_retry`, `llm_rate_limiter`, `context_compact`, `tool_result_compact`, `memory_summary`, `embedding_config`

**Response 200:**
```json
{
  "results": [
    {
      "tenant_id": "tenant-a",
      "success": true,
      "updated_groups": ["llm_retry", "llm_rate_limiter"]
    },
    {
      "tenant_id": "tenant-b",
      "success": false,
      "error": "Tenant workspace not initialized"
    }
  ]
}
```

**Error 400:**
```json
{
  "detail": "Invalid config group: unknown_group"
}
```

**Error 403:**
```json
{
  "detail": "Only default tenant can distribute configuration"
}
```

---

## UI Specification

### 卡片分发按钮

- **位置**: 每张卡片右上角，与编辑按钮并列
- **图标**: `SendOutlined` 或 `ShareAltOutlined`
- **可见性**: 仅 `default` 租户可见（通过 `tenantId === 'default'` 判断）
- **Tooltip**: "分发配置到租户"

### 分发 Modal

- **标题**: "分发 {卡片名称} 配置"
- **内容区域**:
  1. `TenantTargetPicker` 组件（复用现有）
  2. 分发策略 Radio:
     - "覆盖目标配置" (overwrite, 默认选中)
     - "仅填充空值配置" (fill_empty)
  3. 提示文案: "分发后目标租户的 Agent 将自动重载，正在执行的对话可能受影响"
- **操作按钮**: "取消" / "确认分发"
- **结果展示**: 分发完成后展示成功/失败列表

### 配置组名称映射（中文显示）

| config_group | 显示名称 |
|-------------|---------|
| react_agent | React Agent 配置 |
| llm_retry | LLM 重试配置 |
| query_retry | Query 重试配置 |
| llm_rate_limiter | LLM 限流配置 |
| context_compact | 上下文压缩配置 |
| tool_result_compact | 工具结果压缩配置 |
| memory_summary | 记忆摘要配置 |
| embedding_config | Embedding 配置 |

---

## Data Flow

```
用户点击卡片分发按钮
  → 打开分发 Modal（传入 config_group）
  → TenantTargetPicker 加载租户列表 (GET /config/agent/distribution/tenants)
  → 用户选择目标租户 + 分发策略
  → 点击确认
  → POST /config/agent/distribute
  → 后端遍历目标租户:
      1. 加载目标租户 agent.json 的 running 配置
      2. 按 config_group 选择性合并源配置字段
      3. 保存到目标租户 agent.json
      4. 调用 schedule_agent_reload() 触发热重载
  → 返回分发结果
  → 前端展示成功/失败明细
```
