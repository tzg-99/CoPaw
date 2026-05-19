## skill-pool-overwrite-sync

技能池同步时同名技能覆盖更新能力。

### 行为规范

1. **覆盖触发条件**：当 `overwrite=True`（默认）且目标技能池中已存在同名非内置技能时，执行覆盖更新
2. **内置技能保护**：内置技能（`is_builtin=True`）始终不可被覆盖，无论 `overwrite` 取值
3. **配置保留**：覆盖时保留原技能的 `config` 和 `protected` 字段，仅更新以下内容：
   - `files`：技能文件列表
   - `signature`：技能签名
   - `description`：技能描述
   - `version`：版本号
   - `updated_at`：更新时间
4. **覆盖返回值**：覆盖成功时返回更新后的技能元数据，与新建返回值结构一致

### API 变更

#### `POST /skills/pool/upload`

新增查询参数 `overwrite`（`bool`，默认 `true`）：
- `true`：同名非内置技能覆盖更新
- `false`：同名技能冲突时返回 409

#### `POST /skills/pool/create`

新增请求体字段 `overwrite`（`bool`，默认 `true`）：
- `true`：同名非内置技能覆盖创建
- `false`：同名技能冲突时返回 409

#### `POST /skills/pool/upload-zip`

新增查询参数 `overwrite`（`bool`，默认 `true`）：
- `true`：同名非内置技能覆盖，内置技能冲突列入 `conflicts`
- `false`：任何同名冲突均列入 `conflicts`

### 错误处理

| 场景 | overwrite=True | overwrite=False |
|------|---------------|-----------------|
| 同名非内置技能 | 覆盖更新 | 返回 409 |
| 同名内置技能 | 返回 409 | 返回 409 |
| 无同名技能 | 正常创建 | 正常创建 |

### 前端适配

- `uploadToPool` API 调用增加 `overwrite: true` 参数
- `createPoolSkill` API 调用增加 `overwrite: true` 参数
- `uploadSkillPoolZip` API 调用增加 `overwrite: true` 参数
