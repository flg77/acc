"""The agent's RoleStore resolves roles from ``ACC_ROLES_ROOT``.

On the operator path the roles tree is delivered as ConfigMaps at
``/etc/acc/roles`` and ``ACC_ROLES_ROOT`` points there; the agent image has no
``/app/roles``.  ``RoleStore`` used to be built with its cwd-relative default
(``roles``), so every role not served by an installed pack — the CONTROL roles
included — fell back to the generic default and booted DORMANT forever
(found on bb3, 2026-09-27, PB-13 Part D).  The rest of the agent already
resolves ``ACC_ROLES_ROOT`` via ``resolve_manifest_root``; RoleStore must too.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from unittest.mock import MagicMock, patch

from acc.config import ACCConfig

REPO_ROLES = Path(__file__).resolve().parent.parent / "roles"


def _build_agent(role: str, *, patch_role_store: bool):
    """Construct a real Agent with I/O backends mocked (the test_redis_wiring pattern)."""
    patches = [
        patch("acc.agent.load_config"),
        patch("acc.agent.build_backends"),
        patch("acc.agent._build_redis_client", return_value=None),
        patch("acc.agent.CognitiveCore"),
    ]
    if patch_role_store:
        patches.append(patch("acc.agent.RoleStore"))
    started = [p.start() for p in patches]
    try:
        mock_cfg, mock_backends = started[0], started[1]
        mock_cfg.return_value = ACCConfig.model_validate({"agent": {"role": role}})
        bundle = MagicMock()
        bundle.vector = MagicMock()
        mock_backends.return_value = bundle
        if patch_role_store:
            store = MagicMock()
            store.load_at_startup.return_value = MagicMock(version="0.1.0")
            store.loaded_from_default = False
            started[-1].return_value = store
        from acc.agent import Agent

        return Agent(), (started[-1] if patch_role_store else None)
    finally:
        for p in patches:
            p.stop()


def _no_installed_packs(tmp_path, monkeypatch) -> None:
    """No package serves any role (the operator case for in-tree roles); an
    installed pack on the test host would otherwise win over the in-tree file."""
    empty = tmp_path / "packages"
    empty.mkdir()
    monkeypatch.setenv("ACC_PACKAGES_ROOT", str(empty))


def test_agent_passes_acc_roles_root_to_role_store(tmp_path, monkeypatch) -> None:
    _no_installed_packs(tmp_path, monkeypatch)
    roles_root = tmp_path / "etc-acc-roles"
    roles_root.mkdir()
    monkeypatch.setenv("ACC_ROLES_ROOT", str(roles_root))
    _, MockRoleStore = _build_agent("analyst", patch_role_store=True)
    _, kwargs = MockRoleStore.call_args
    assert Path(kwargs["roles_root"]) == roles_root


def test_operator_layout_in_tree_role_boots_active_not_dormant(tmp_path, monkeypatch) -> None:
    """Roles only under ACC_ROLES_ROOT (outside the cwd), like the operator's
    /etc/acc/roles: an in-tree role must load from there and not boot DORMANT."""
    _no_installed_packs(tmp_path, monkeypatch)
    roles_root = tmp_path / "etc-acc-roles"
    shutil.copytree(REPO_ROLES, roles_root)
    workdir = tmp_path / "app"  # the image's /app: no roles/ here
    workdir.mkdir()
    monkeypatch.chdir(workdir)
    monkeypatch.setenv("ACC_ROLES_ROOT", str(roles_root))

    agent, _ = _build_agent("analyst", patch_role_store=False)

    assert agent._role_store.loaded_from_default is False
    assert agent._dormant_pending_pack is False
    assert "Analyse and summarise" in (agent._active_role.purpose or "")
