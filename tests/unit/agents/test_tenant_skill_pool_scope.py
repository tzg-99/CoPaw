# -*- coding: utf-8 -*-
"""技能池租户隔离与覆盖同步测试。"""

from pathlib import Path

import pytest

from src.swe.agents.skills_manager import (
    SkillPoolService,
    get_skill_pool_dir,
    get_workspace_skill_manifest_path,
    get_workspace_skills_dir,
    read_skill_pool_manifest,
    reconcile_pool_manifest,
)


def _write_skill(skill_dir: Path, description: str) -> None:
    """创建一个最小技能目录。"""
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {skill_dir.name}\ndescription: {description}\n---\n",
        encoding="utf-8",
    )


# --- 原有租户隔离测试 ---


def test_pool_manifest_is_tenant_scoped(tmp_path: Path) -> None:
    """不同租户的技能池 manifest 互不影响。"""
    tenant_a = tmp_path / "tenant-a"
    tenant_b = tmp_path / "tenant-b"

    svc_a = SkillPoolService(working_dir=tenant_a)
    svc_b = SkillPoolService(working_dir=tenant_b)

    svc_a.create_skill(
        name="skill-a",
        content="---\nname: skill-a\ndescription: A\n---\n",
    )

    manifest_a = read_skill_pool_manifest(
        reconcile=False,
        working_dir=tenant_a,
    )
    manifest_b = read_skill_pool_manifest(
        reconcile=False,
        working_dir=tenant_b,
    )

    assert "skill-a" in manifest_a.get("skills", {})
    assert "skill-a" not in manifest_b.get("skills", {})


def test_pool_dir_is_tenant_scoped(tmp_path: Path) -> None:
    """不同租户的技能池目录互不影响。"""
    tenant_a = tmp_path / "tenant-a"
    tenant_b = tmp_path / "tenant-b"

    svc_a = SkillPoolService(working_dir=tenant_a)
    svc_a.create_skill(
        name="shared-name",
        content="---\nname: shared-name\ndescription: from A\n---\n",
    )

    svc_b = SkillPoolService(working_dir=tenant_b)
    svc_b.create_skill(
        name="shared-name",
        content="---\nname: shared-name\ndescription: from B\n---\n",
    )

    text_a = (tenant_a / "skill_pool" / "shared-name" / "SKILL.md").read_text(
        encoding="utf-8",
    )
    text_b = (tenant_b / "skill_pool" / "shared-name" / "SKILL.md").read_text(
        encoding="utf-8",
    )

    assert "from A" in text_a
    assert "from B" in text_b


def test_upload_from_workspace_is_tenant_scoped(tmp_path: Path) -> None:
    """upload_from_workspace 写入租户自己的技能池。"""
    tenant_dir = tmp_path / "tenant-a"
    workspace_dir = tenant_dir / "workspaces" / "alpha"

    _write_skill(
        get_workspace_skills_dir(workspace_dir) / "ws-skill",
        "workspace skill",
    )

    svc = SkillPoolService(working_dir=tenant_dir)
    result = svc.upload_from_workspace(
        workspace_dir=workspace_dir,
        skill_name="ws-skill",
    )

    assert result["success"] is True
    assert (tenant_dir / "skill_pool" / "ws-skill" / "SKILL.md").exists()


def test_import_from_zip_is_tenant_scoped(tmp_path: Path) -> None:
    """import_from_zip 写入租户自己的技能池。"""
    import io
    import zipfile

    tenant_dir = tmp_path / "tenant-a"
    svc = SkillPoolService(working_dir=tenant_dir)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            "zip-skill/SKILL.md",
            "---\nname: zip-skill\ndescription: from zip\n---\n",
        )
    data = buf.getvalue()

    result = svc.import_from_zip(data=data)

    assert "zip-skill" in result["imported"]
    assert (tenant_dir / "skill_pool" / "zip-skill" / "SKILL.md").exists()


def test_create_skill_is_tenant_scoped(tmp_path: Path) -> None:
    """create_skill 写入租户自己的技能池。"""
    tenant_dir = tmp_path / "tenant-a"

    svc = SkillPoolService(working_dir=tenant_dir)
    created = svc.create_skill(
        name="new-skill",
        content="---\nname: new-skill\ndescription: new\n---\n",
    )

    assert created == "new-skill"
    assert (tenant_dir / "skill_pool" / "new-skill" / "SKILL.md").exists()


def test_download_to_workspace_is_tenant_scoped(tmp_path: Path) -> None:
    """download_to_workspace 从租户自己的技能池读取。"""
    tenant_dir = tmp_path / "tenant-a"
    workspace_dir = tenant_dir / "workspaces" / "alpha"

    svc = SkillPoolService(working_dir=tenant_dir)
    svc.create_skill(
        name="pool-skill",
        content="---\nname: pool-skill\ndescription: from pool\n---\n",
    )

    result = svc.download_to_workspace(
        workspace_dir=workspace_dir,
        skill_name="pool-skill",
    )

    assert result["success"] is True
    assert (
        get_workspace_skills_dir(workspace_dir) / "pool-skill" / "SKILL.md"
    ).exists()


def test_reconcile_pool_manifest_is_tenant_scoped(tmp_path: Path) -> None:
    """reconcile_pool_manifest 只影响租户自己的 manifest。"""
    tenant_a = tmp_path / "tenant-a"
    tenant_b = tmp_path / "tenant-b"

    _write_skill(
        get_skill_pool_dir(working_dir=tenant_a) / "orphan-a",
        "orphan A",
    )
    _write_skill(
        get_skill_pool_dir(working_dir=tenant_b) / "orphan-b",
        "orphan B",
    )

    reconcile_pool_manifest(working_dir=tenant_a)

    manifest_a = read_skill_pool_manifest(
        reconcile=False,
        working_dir=tenant_a,
    )
    manifest_b = read_skill_pool_manifest(
        reconcile=False,
        working_dir=tenant_b,
    )

    assert "orphan-a" in manifest_a.get("skills", {})
    assert "orphan-b" not in manifest_a.get("skills", {})
    assert "orphan-b" not in manifest_b.get("skills", {})


def test_list_pool_skills_is_tenant_scoped(tmp_path: Path) -> None:
    """list_skills 只返回租户自己的技能。"""
    tenant_a = tmp_path / "tenant-a"
    tenant_b = tmp_path / "tenant-b"

    svc_a = SkillPoolService(working_dir=tenant_a)
    svc_a.create_skill(
        name="skill-a",
        content="---\nname: skill-a\ndescription: A\n---\n",
    )

    svc_b = SkillPoolService(working_dir=tenant_b)
    svc_b.create_skill(
        name="skill-b",
        content="---\nname: skill-b\ndescription: B\n---\n",
    )

    manifest_a = read_skill_pool_manifest(
        reconcile=False,
        working_dir=tenant_a,
    )
    manifest_b = read_skill_pool_manifest(
        reconcile=False,
        working_dir=tenant_b,
    )

    assert "skill-a" in manifest_a.get("skills", {})
    assert "skill-b" not in manifest_a.get("skills", {})
    assert "skill-b" in manifest_b.get("skills", {})
    assert "skill-a" not in manifest_b.get("skills", {})


def test_upload_overwrite_does_not_affect_other_tenant(tmp_path: Path) -> None:
    """覆盖更新不影响其他租户的同名技能。"""
    tenant_a = tmp_path / "tenant-a"
    tenant_b = tmp_path / "tenant-b"

    svc_a = SkillPoolService(working_dir=tenant_a)
    svc_a.create_skill(
        name="shared-name",
        content="---\nname: shared-name\ndescription: from A\n---\n",
    )

    svc_b = SkillPoolService(working_dir=tenant_b)
    svc_b.create_skill(
        name="shared-name",
        content="---\nname: shared-name\ndescription: from B\n---\n",
    )

    # 覆盖 A 的技能
    svc_a.create_skill(
        name="shared-name",
        content="---\nname: shared-name\ndescription: updated A\n---\n",
    )

    text_a = (tenant_a / "skill_pool" / "shared-name" / "SKILL.md").read_text(
        encoding="utf-8",
    )
    text_b = (tenant_b / "skill_pool" / "shared-name" / "SKILL.md").read_text(
        encoding="utf-8",
    )

    assert "updated A" in text_a
    assert "from B" in text_b


# --- 技能池覆盖同步测试 ---


def test_upload_from_workspace_overwrite_preserves_config_and_protected(
    tmp_path: Path,
) -> None:
    """覆盖非内置技能时保留原技能的 config 和 protected。"""
    import json

    tenant_dir = tmp_path / "tenant-a"
    workspace_dir = tenant_dir / "workspaces" / "alpha"
    service = SkillPoolService(working_dir=tenant_dir)

    _write_skill(
        get_skill_pool_dir(working_dir=tenant_dir) / "my-skill",
        "original description",
    )
    reconcile_pool_manifest(working_dir=tenant_dir)

    manifest_path = tenant_dir / "skill_pool" / "skill.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["skills"]["my-skill"]["config"] = {"key": "value"}
    manifest["skills"]["my-skill"]["protected"] = True
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    _write_skill(
        get_workspace_skills_dir(workspace_dir) / "my-skill",
        "updated description",
    )

    result = service.upload_from_workspace(
        workspace_dir=workspace_dir,
        skill_name="my-skill",
    )

    assert result == {"success": True, "name": "my-skill"}

    skill_text = (
        tenant_dir / "skill_pool" / "my-skill" / "SKILL.md"
    ).read_text(encoding="utf-8")
    assert "updated description" in skill_text

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["skills"]["my-skill"]["config"] == {"key": "value"}
    assert manifest["skills"]["my-skill"]["protected"] is True


def test_upload_from_workspace_overwrite_builtin_allowed(
    tmp_path: Path,
) -> None:
    """overwrite=True 时内置技能可被覆盖。"""
    import json

    tenant_dir = tmp_path / "tenant-a"
    workspace_dir = tenant_dir / "workspaces" / "alpha"
    service = SkillPoolService(working_dir=tenant_dir)

    _write_skill(
        get_skill_pool_dir(working_dir=tenant_dir) / "guidance",
        "builtin guidance",
    )
    reconcile_pool_manifest(working_dir=tenant_dir)

    manifest_path = tenant_dir / "skill_pool" / "skill.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["skills"]["guidance"]["source"] = "builtin"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    _write_skill(
        get_workspace_skills_dir(workspace_dir) / "guidance",
        "custom guidance",
    )

    result = service.upload_from_workspace(
        workspace_dir=workspace_dir,
        skill_name="guidance",
        overwrite=True,
    )

    assert result["success"] is True
    # 覆盖后 source 变为 customized
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["skills"]["guidance"]["source"] == "customized"


def test_upload_from_workspace_overwrite_false_rejected(
    tmp_path: Path,
) -> None:
    """overwrite=False 时同名技能冲突被拒绝。"""
    tenant_dir = tmp_path / "tenant-a"
    workspace_dir = tenant_dir / "workspaces" / "alpha"
    service = SkillPoolService(working_dir=tenant_dir)

    _write_skill(
        get_skill_pool_dir(working_dir=tenant_dir) / "my-skill",
        "existing description",
    )
    reconcile_pool_manifest(working_dir=tenant_dir)

    _write_skill(
        get_workspace_skills_dir(workspace_dir) / "my-skill",
        "updated description",
    )

    result = service.upload_from_workspace(
        workspace_dir=workspace_dir,
        skill_name="my-skill",
        overwrite=False,
    )

    assert result["success"] is False
    assert result["reason"] == "conflict"


def test_create_skill_overwrite_preserves_config_and_protected(
    tmp_path: Path,
) -> None:
    """覆盖创建时保留原技能的 config 和 protected。"""
    import json

    tenant_dir = tmp_path / "tenant-a"
    service = SkillPoolService(working_dir=tenant_dir)

    service.create_skill(
        name="my-skill",
        content="---\nname: my-skill\ndescription: original\n---\n",
        config={"key": "value"},
    )

    manifest_path = tenant_dir / "skill_pool" / "skill.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["skills"]["my-skill"]["protected"] = True
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    created = service.create_skill(
        name="my-skill",
        content="---\nname: my-skill\ndescription: updated\n---\n",
    )

    assert created == "my-skill"

    skill_text = (
        tenant_dir / "skill_pool" / "my-skill" / "SKILL.md"
    ).read_text(encoding="utf-8")
    assert "updated" in skill_text

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["skills"]["my-skill"]["config"] == {"key": "value"}
    assert manifest["skills"]["my-skill"]["protected"] is True


def test_create_skill_overwrite_false_rejected(
    tmp_path: Path,
) -> None:
    """overwrite=False 时同名技能创建被拒绝。"""
    tenant_dir = tmp_path / "tenant-a"
    service = SkillPoolService(working_dir=tenant_dir)

    service.create_skill(
        name="my-skill",
        content="---\nname: my-skill\ndescription: original\n---\n",
    )

    result = service.create_skill(
        name="my-skill",
        content="---\nname: my-skill\ndescription: updated\n---\n",
        overwrite=False,
    )

    assert result is None


def test_create_skill_overwrite_builtin_allowed(
    tmp_path: Path,
) -> None:
    """overwrite=True 时内置技能可被覆盖创建。"""
    import json

    tenant_dir = tmp_path / "tenant-a"
    service = SkillPoolService(working_dir=tenant_dir)

    _write_skill(
        get_skill_pool_dir(working_dir=tenant_dir) / "guidance",
        "builtin guidance",
    )
    reconcile_pool_manifest(working_dir=tenant_dir)

    manifest_path = tenant_dir / "skill_pool" / "skill.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["skills"]["guidance"]["source"] = "builtin"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    result = service.create_skill(
        name="guidance",
        content="---\nname: guidance\ndescription: custom\n---\n",
        overwrite=True,
    )

    assert result == "guidance"
    # 覆盖后 source 变为 customized
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["skills"]["guidance"]["source"] == "customized"


def test_import_from_zip_overwrite_includes_builtin(
    tmp_path: Path,
) -> None:
    """overwrite=True 时内置技能也可被覆盖导入。"""
    import io
    import json
    import zipfile

    tenant_dir = tmp_path / "tenant-a"
    service = SkillPoolService(working_dir=tenant_dir)

    _write_skill(
        get_skill_pool_dir(working_dir=tenant_dir) / "custom-skill",
        "existing custom",
    )
    _write_skill(
        get_skill_pool_dir(working_dir=tenant_dir) / "guidance",
        "builtin guidance",
    )
    reconcile_pool_manifest(working_dir=tenant_dir)

    manifest_path = tenant_dir / "skill_pool" / "skill.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["skills"]["guidance"]["source"] = "builtin"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            "custom-skill/SKILL.md",
            "---\nname: custom-skill\ndescription: updated custom\n---\n",
        )
        zf.writestr(
            "guidance/SKILL.md",
            "---\nname: guidance\ndescription: updated guidance\n---\n",
        )
    data = buf.getvalue()

    result = service.import_from_zip(data=data, overwrite=True)

    # 两个技能都应被覆盖导入
    assert "custom-skill" in result["imported"]
    assert "guidance" in result["imported"]


def test_import_from_zip_overwrite_false_rejected(
    tmp_path: Path,
) -> None:
    """overwrite=False 时任何同名冲突均导致整体拒绝。"""
    import io
    import zipfile

    tenant_dir = tmp_path / "tenant-a"
    service = SkillPoolService(working_dir=tenant_dir)

    _write_skill(
        get_skill_pool_dir(working_dir=tenant_dir) / "custom-skill",
        "existing custom",
    )
    reconcile_pool_manifest(working_dir=tenant_dir)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            "custom-skill/SKILL.md",
            "---\nname: custom-skill\ndescription: updated custom\n---\n",
        )
    data = buf.getvalue()

    result = service.import_from_zip(data=data, overwrite=False)

    assert result["imported"] == []
    assert len(result["conflicts"]) > 0
