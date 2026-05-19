## tenant-scoped-skill-pool-management

### 变更说明

修改技能池管理中 `upload_from_workspace`、`create_skill`、`import_from_zip` 三条写入路径的冲突处理策略，从"拒绝+建议重命名"变更为"默认覆盖+保留配置"。

### 受影响行为

#### `SkillPoolService.upload_from_workspace()`

- **原行为**：`overwrite=False`（默认），同名技能冲突时返回建议重命名的错误信息
- **新行为**：`overwrite=True`（默认），同名非内置技能覆盖更新，保留 `config` 和 `protected`

#### `SkillPoolService.create_skill()`

- **原行为**：同名技能无条件拒绝，返回 `None`
- **新行为**：新增 `overwrite: bool = True` 参数，同名非内置技能覆盖创建，保留 `config` 和 `protected`

#### `SkillPoolService.import_from_zip()`

- **原行为**：`overwrite=False`（默认），任何同名冲突均导致整体拒绝
- **新行为**：`overwrite=True`（默认），非内置冲突直接覆盖，内置冲突列入 `conflicts` 返回，不再整体拒绝

### 不变行为

- `download_to_workspace`：池→工作空间方向，冲突处理不变
- `save_pool_skill`：编辑/重命名流程，不变
- `broadcast`：已无条件覆盖，不变
- `import_pool_skill_from_hub`：Hub 导入有独立冲突处理，不变
- 内置技能保护：所有路径中内置技能均不可覆盖，不变
