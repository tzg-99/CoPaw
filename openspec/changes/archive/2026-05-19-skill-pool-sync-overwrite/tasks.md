## Tasks

### T1: `SkillPoolService.upload_from_workspace()` 覆盖逻辑改造

**文件**: `src/swe/agents/skills_manager.py`

- 将 `upload_from_workspace` 方法的 `overwrite` 参数默认值从 `False` 改为 `True`
- 修改 `_update` 回调逻辑：当 `overwrite=True` 且技能已存在且非内置时，从旧条目中读取 `config` 和 `protected`，合并到 `_build_skill_metadata` 生成的新条目中
- 确保内置技能冲突时仍返回错误，不受 `overwrite` 影响

**验证**: 单元测试覆盖：overwrite=True 覆盖非内置技能、overwrite=True 内置技能冲突、overwrite=False 同名冲突

### T2: `SkillPoolService.create_skill()` 增加 `overwrite` 参数

**文件**: `src/swe/agents/skills_manager.py`

- 给 `create_skill` 方法增加 `overwrite: bool = True` 参数
- 当 `overwrite=True` 且技能已存在且非内置时，执行覆盖创建（保留 `config` 和 `protected`）
- 当 `overwrite=False` 或技能为内置时，保持原有拒绝逻辑
- 覆盖创建时复用与 `upload_from_workspace` 相同的配置保留逻辑

**验证**: 单元测试覆盖：overwrite=True 覆盖创建、overwrite=False 拒绝、内置技能拒绝

### T3: `SkillPoolService.import_from_zip()` 覆盖逻辑改造

**文件**: `src/swe/agents/skills_manager.py`

- 将 `import_from_zip` 方法的 `overwrite` 参数默认值从 `False` 改为 `True`
- 修改冲突处理逻辑：当 `overwrite=True` 时，非内置冲突直接覆盖更新（保留 `config` 和 `protected`），内置冲突列入 `conflicts` 返回
- 不再因内置冲突而整体拒绝导入

**验证**: 单元测试覆盖：overwrite=True 部分导入（非内置覆盖+内置冲突）、overwrite=False 整体拒绝

### T4: API 端点增加 `overwrite` 参数

**文件**: `src/swe/app/routers/skills.py`

- `upload_workspace_skill_to_pool` 端点：增加 `overwrite: bool = True` 查询参数，传递给 `upload_from_workspace`
- `create_pool_skill` 端点：在请求体模型中增加 `overwrite: bool = True` 字段，传递给 `create_skill`
- `upload_skill_pool_zip` 端点：增加 `overwrite: bool = True` 查询参数，传递给 `import_from_zip`

**验证**: 手动测试或 API 测试验证参数传递正确

### T5: 前端 API 调用适配

**文件**: `console/src/api/modules/skill.ts`

- `uploadToPool` 函数：增加 `overwrite: true` 参数
- `createPoolSkill` 函数：增加 `overwrite: true` 参数
- `uploadSkillPoolZip` 函数：增加 `overwrite: true` 参数

**验证**: 前端构建通过，功能测试验证同步行为

### T6: 单元测试补充

**文件**: `tests/unit/agents/test_tenant_skill_pool_scope.py`

- 新增 `test_upload_from_workspace_overwrite`：验证覆盖非内置技能时保留 `config` 和 `protected`
- 新增 `test_upload_from_workspace_overwrite_builtin_rejected`：验证内置技能不可覆盖
- 新增 `test_create_skill_overwrite`：验证覆盖创建保留 `config` 和 `protected`
- 新增 `test_create_skill_overwrite_false_rejected`：验证 `overwrite=False` 时拒绝同名
- 新增 `test_import_from_zip_overwrite_partial`：验证部分导入（非内置覆盖+内置冲突）
- 新增 `test_import_from_zip_overwrite_false_rejected`：验证 `overwrite=False` 时整体拒绝

**验证**: `venv/bin/python -m pytest tests/unit/agents/test_tenant_skill_pool_scope.py -v` 全部通过
