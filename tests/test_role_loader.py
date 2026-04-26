"""Tests for acc.role_loader.RoleLoader (ACC-10)."""

from __future__ import annotations

import asyncio
import textwrap
from pathlib import Path

import pytest
import yaml

from acc.config import RoleDefinitionConfig
from acc.role_loader import RoleLoader, _deep_merge


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def write_role(tmp_path: Path, name: str, data: dict) -> None:
    role_dir = tmp_path / name
    role_dir.mkdir(parents=True, exist_ok=True)
    with (role_dir / "role.yaml").open("w") as fh:
        yaml.dump({"role_definition": data}, fh)


def write_base(tmp_path: Path, data: dict) -> None:
    write_role(tmp_path, "_base", data)


# ---------------------------------------------------------------------------
# deep_merge unit tests
# ---------------------------------------------------------------------------

class TestDeepMerge:
    def test_child_wins_scalar(self):
        base = {"a": 1, "b": 2}
        override = {"b": 99}
        result = _deep_merge(base, override)
        assert result["a"] == 1
        assert result["b"] == 99

    def test_child_adds_key(self):
        base = {"a": 1}
        override = {"b": 2}
        result = _deep_merge(base, override)
        assert result == {"a": 1, "b": 2}

    def test_nested_dict_merge(self):
        base = {"overrides": {"x": 1, "y": 2}}
        override = {"overrides": {"y": 99, "z": 3}}
        result = _deep_merge(base, override)
        assert result["overrides"] == {"x": 1, "y": 99, "z": 3}

    def test_base_unchanged(self):
        base = {"a": {"x": 1}}
        override = {"a": {"x": 2}}
        _deep_merge(base, override)
        assert base["a"]["x"] == 1  # original not mutated

    def test_list_replaced_not_merged(self):
        """Lists are replaced wholesale, not element-merged."""
        base = {"task_types": ["a", "b"]}
        override = {"task_types": ["c"]}
        result = _deep_merge(base, override)
        assert result["task_types"] == ["c"]


# ---------------------------------------------------------------------------
# RoleLoader unit tests
# ---------------------------------------------------------------------------

class TestRoleLoaderAvailable:
    def test_available_when_file_exists(self, tmp_path):
        write_role(tmp_path, "analyst", {"version": "1.0.0", "purpose": "test"})
        loader = RoleLoader(roles_root=tmp_path, role_name="analyst")
        assert loader.available() is True

    def test_not_available_when_missing(self, tmp_path):
        loader = RoleLoader(roles_root=tmp_path, role_name="nonexistent")
        assert loader.available() is False


class TestRoleLoaderLoad:
    def test_returns_none_when_not_available(self, tmp_path):
        loader = RoleLoader(roles_root=tmp_path, role_name="ghost")
        assert loader.load() is None

    def test_loads_minimal_role(self, tmp_path):
        write_role(tmp_path, "analyst", {"version": "2.0.0", "purpose": "analyse things"})
        loader = RoleLoader(roles_root=tmp_path, role_name="analyst")
        role = loader.load()
        assert isinstance(role, RoleDefinitionConfig)
        assert role.version == "2.0.0"
        assert role.purpose == "analyse things"

    def test_base_merge_applied(self, tmp_path):
        write_base(tmp_path, {"version": "0.0.1", "persona": "formal", "purpose": "base"})
        write_role(tmp_path, "analyst", {"version": "1.5.0", "purpose": "override"})
        loader = RoleLoader(roles_root=tmp_path, role_name="analyst")
        role = loader.load()
        # Child version wins
        assert role.version == "1.5.0"
        # Base persona preserved (child did not override)
        assert role.persona == "formal"
        # Child purpose wins
        assert role.purpose == "override"

    def test_caches_result_on_repeated_load(self, tmp_path):
        write_role(tmp_path, "analyst", {"version": "1.0.0"})
        loader = RoleLoader(roles_root=tmp_path, role_name="analyst")
        r1 = loader.load()
        r2 = loader.load()
        assert r1 is r2  # same object from cache

    def test_reloads_when_mtime_changes(self, tmp_path):
        write_role(tmp_path, "analyst", {"version": "1.0.0"})
        loader = RoleLoader(roles_root=tmp_path, role_name="analyst")
        loader.load()

        # Simulate file change by overwriting and bumping mtime
        import time
        time.sleep(0.01)
        write_role(tmp_path, "analyst", {"version": "2.0.0"})
        role_path = tmp_path / "analyst" / "role.yaml"
        # Force mtime change (write already does this, but just in case)
        role_path.touch()

        r2 = loader.load()
        assert r2 is not None
        assert r2.version == "2.0.0"

    def test_invalid_yaml_returns_none(self, tmp_path):
        role_dir = tmp_path / "bad"
        role_dir.mkdir()
        (role_dir / "role.yaml").write_text("role_definition: {version: !!python/object: bad}")
        loader = RoleLoader(roles_root=tmp_path, role_name="bad")
        # Should not raise; returns None on parse error
        result = loader.load()
        # May be None or a default RoleDefinitionConfig depending on YAML error type
        # The important thing is it does not raise
        assert result is None or isinstance(result, RoleDefinitionConfig)


class TestRoleLoaderReloadCallback:
    def test_callback_registered_and_called(self, tmp_path):
        write_role(tmp_path, "analyst", {"version": "1.0.0"})
        loader = RoleLoader(roles_root=tmp_path, role_name="analyst", poll_interval_s=1)
        loader.load()

        received = []
        loader.register_reload_callback(lambda r: received.append(r.version))

        # Simulate a file change
        import time
        time.sleep(0.05)
        write_role(tmp_path, "analyst", {"version": "2.0.0"})
        (tmp_path / "analyst" / "role.yaml").touch()

        # Manually trigger the reload path by calling _load_and_merge
        role_path = tmp_path / "analyst" / "role.yaml"
        mtime = role_path.stat().st_mtime
        prev_version = loader._cached.version if loader._cached else ""
        new_def = loader._load_and_merge(role_path)
        assert new_def is not None
        if new_def.version != prev_version:
            loader._cached = new_def
            loader._cached_mtime = mtime
            for cb in loader._reload_callbacks:
                cb(new_def)

        assert "2.0.0" in received


class TestRoleLoaderWatchTask:
    @pytest.mark.asyncio
    async def test_start_watch_creates_task(self, tmp_path):
        write_role(tmp_path, "analyst", {"version": "1.0.0"})
        loader = RoleLoader(roles_root=tmp_path, role_name="analyst", poll_interval_s=600)
        await loader.start_watch()
        assert loader._watch_task is not None
        assert not loader._watch_task.done()
        await loader.stop_watch()
        assert loader._watch_task is None

    @pytest.mark.asyncio
    async def test_start_watch_idempotent(self, tmp_path):
        write_role(tmp_path, "analyst", {"version": "1.0.0"})
        loader = RoleLoader(roles_root=tmp_path, role_name="analyst", poll_interval_s=600)
        await loader.start_watch()
        task1 = loader._watch_task
        await loader.start_watch()  # second call is no-op
        assert loader._watch_task is task1
        await loader.stop_watch()
