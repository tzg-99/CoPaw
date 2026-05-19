## Context

当前技能池的三条写入路径（create、upload_from_workspace、import_from_zip）对同名技能的处理策略不一致：

- `create_skill`：无条件拒绝，返回 `None`，API 层抛 409
- `upload_from_workspace`：有 `overwrite` 参数但默认 `False`，冲突时返回建议重命名
- `import_from_zip`：有 `overwrite` 参数但默认 `False`，冲突时整体拒绝导入

内置技能（builtin）在所有路径中均不可覆盖，这是正确的保护策略。但非内置技能的冲突处理过于保守，用户需要先删除再上传，丢失 `config` 和 `protected` 等配置。

## Goals / Non-Goals

**Goals:**

- 三条写入路径默认使用覆盖模式（`overwrite=True`），同名非内置技能直接覆盖更新
- 覆盖时保留原技能的 `config` 和 `protected` 状态，仅更新内容、签名、描述等元数据
- 内置技能仍不可被覆盖，保持现有保护逻辑
- 前端 API 调用传递 `overwrite: true` 默认值

**Non-Goals:**

- 不改变 `download_to_workspace` 的冲突处理（这是池→工作空间方向，属于不同流程）
- 不改变 `save_pool_skill` 的编辑/重命名逻辑（这是编辑流程，不是同步流程）
- 不改变 `broadcast` 的行为（broadcast 已经无条件覆盖）
- 不改变 `import_pool_skill_from_hub` 的行为（Hub 导入有独立的冲突处理）

## Decisions

### D1: `overwrite` 参数默认值从 `False` 改为 `True`

**选择**：将三条路径的 `overwrite` 默认值改为 `True`。

**理由**：同步到技能池是"更新"语义而非"首次创建"。用户同步同名技能时，期望的是内容更新而非创建新条目。默认覆盖减少操作步骤，避免丢失配置。

**替代方案**：保持默认 `False`，前端传 `overwrite: true`——这需要前端每次调用都显式传参，且 CLI 和其他调用方也需要适配，增加调用方负担。

### D2: 覆盖时保留 `config` 和 `protected`

**选择**：覆盖更新 manifest 条目时，从旧条目中读取 `config` 和 `protected`，合并到新条目中。

**理由**：`config` 是用户对技能的个性化配置（如参数值），`protected` 标记是否受保护。内容更新不应清除这些运维配置。

**实现方式**：在 `_update` 回调中，先读取 `existing` 条目的 `config` 和 `protected`，然后合并到 `_build_skill_metadata` 生成的新条目中。

### D3: `import_from_zip` 覆盖时允许部分导入

**选择**：当 `overwrite=True` 时，冲突的非内置技能直接覆盖，内置技能冲突仍拒绝。不再因任何冲突而整体拒绝。

**理由**：当前 `import_from_zip` 在有冲突时整体拒绝，即使 `overwrite=True` 也只覆盖非内置冲突，内置冲突仍导致整体失败。改为部分导入更合理：非内置覆盖，内置跳过并列入 `conflicts` 返回。

**替代方案**：保持整体拒绝——这会导致 zip 中多个技能时，一个内置冲突阻止所有非内置技能的覆盖更新，不合理。

### D4: `create_skill` 增加 `overwrite` 参数

**选择**：给 `SkillPoolService.create_skill()` 增加 `overwrite: bool = True` 参数。当 `overwrite=True` 且技能已存在且非内置时，执行覆盖创建（保留 `config` 和 `protected`）。

**理由**：`create_skill` 当前无条件拒绝同名，但"创建"和"覆盖创建"在技能池场景中是同一操作的不同策略。增加参数保持向后兼容。

## Risks / Trade-offs

- **[风险] 默认覆盖可能意外覆盖用户修改的技能** → 缓解：前端在覆盖前可弹出确认提示（可选）；内置技能始终不可覆盖，保护了核心技能
- **[风险] `import_from_zip` 部分导入导致返回结果复杂化** → 缓解：返回结构中 `imported` 和 `conflicts` 已有清晰分区，前端已有处理逻辑
- **[风险] 保留 `config` 可能导致旧配置与新内容不兼容** → 缓解：这是用户自行管理的配置，与内容版本不兼容时用户可通过 config API 修改
