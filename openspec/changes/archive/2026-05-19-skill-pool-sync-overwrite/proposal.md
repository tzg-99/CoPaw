## Why

当前技能从工作空间同步到技能池时，若技能池中已存在同名技能，`upload_from_workspace` 默认返回冲突错误并建议重命名（追加时间戳后缀）。用户需要先删除旧技能再重新上传，操作路径长且容易丢失技能池中的配置（如 `config`、`protected` 状态）。对于运维和日常技能更新场景，同名覆盖是更自然的操作语义。

## What Changes

- `SkillPoolService.upload_from_workspace()` 新增 `overwrite=True` 默认值，同名非内置技能直接覆盖更新
- `SkillPoolService.create_skill()` 新增 `overwrite` 参数，支持同名覆盖创建
- `SkillPoolService.import_from_zip()` 新增 `overwrite=True` 默认值，同名技能覆盖而非整体拒绝
- API 层 `/skills/pool/upload` 端点增加 `overwrite` 请求参数（默认 `true`）
- API 层 `/skills/pool/create` 端点增加 `overwrite` 请求参数（默认 `true`）
- API 层 `/skills/pool/upload-zip` 端点增加 `overwrite` 请求参数（默认 `true`）
- 覆盖时保留原技能的 `config` 配置和 `protected` 状态，仅更新内容、签名和描述等元数据

## Capabilities

### New Capabilities

- `skill-pool-overwrite-sync`: 技能池同步时同名技能覆盖更新能力，覆盖 upload、create、import-zip 三条路径

### Modified Capabilities

- `tenant-scoped-skill-pool-management`: upload/create/import-zip 操作的冲突处理策略从"拒绝+建议重命名"变更为"默认覆盖+保留配置"

## Impact

- **后端代码**：`src/swe/agents/skills_manager.py` 中 `SkillPoolService` 的 `upload_from_workspace`、`create_skill`、`import_from_zip` 方法
- **API 层**：`src/swe/app/routers/skills.py` 中 `upload_workspace_skill_to_pool`、`create_pool_skill`、`upload_skill_pool_zip` 端点
- **前端**：Console 中技能池上传/创建对话框可能需要适配 `overwrite` 参数
- **测试**：`tests/unit/agents/test_tenant_skill_pool_scope.py` 需要新增覆盖场景用例
