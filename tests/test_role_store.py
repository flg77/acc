"""Tests for acc/role_store.py — load precedence, update, rejection, history."""

from __future__ import annotations

import json
import os
import time
import uuid
from unittest.mock import MagicMock, patch, mock_open

import pytest

from acc.config import ACCConfig, RoleDefinitionConfig
from acc.role_store import RoleStore, RoleUpdateRejectedError


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

COLLECTIVE_ID = "sol-01"
AGENT_ID = "analyst-9c1d"


def _make_store(
    redis_client=None,
    vector=None,
    role_def: dict | None = None,
) -> RoleStore:
    """Build a RoleStore with an in-config role definition."""
    config_data: dict = {}
    if role_def:
        config_data["role_definition"] = role_def
    config = ACCConfig.model_validate(config_data)
    return RoleStore(
        config=config,
        agent_id=AGENT_ID,
        redis_client=redis_client,
        vector=vector,
    )


def _mock_vector(rows: list[dict] | None = None):
    """Return a mock vector backend whose insert() and open_table() work."""
    v = MagicMock()
    tbl = MagicMock()
    tbl.search.return_value = tbl
    tbl.where.return_value = tbl
    tbl.limit.return_value = tbl
    tbl.to_list.return_value = rows or []
    v._db.open_table.return_value = tbl
    return v


def _mock_redis(role_json: str | None = None, registry_json: str | None = None):
    r = MagicMock()
    def _get(key):
        if "role" in key and role_json is not None:
            return role_json.encode()
        if "registry" in key and registry_json is not None:
            return registry_json.encode()
        return None
    r.get.side_effect = _get
    return r


# ---------------------------------------------------------------------------
# Load precedence tests (REQ-STORE-001, REQ-STORE-002)
# ---------------------------------------------------------------------------

class TestLoadPrecedence:
    def test_file_wins_over_redis_and_lancedb(self, tmp_path):
        role_file = tmp_path / "acc-role.yaml"
        role_file.write_text("purpose: from-file\nversion: '1.0.0'\n")

        redis = _mock_redis(role_json=json.dumps({"purpose": "from-redis", "version": "2.0.0"}))
        vector = _mock_vector()
        store = _make_store(redis_client=redis, vector=vector)

        with patch.dict(os.environ, {"ACC_ROLE_CONFIG_PATH": str(role_file)}):
            role = store.load_at_startup()

        assert role.purpose == "from-file"
        assert role.version == "1.0.0"

    def test_redis_wins_over_lancedb_when_no_file(self, tmp_path):
        redis = _mock_redis(role_json=json.dumps({"purpose": "from-redis", "version": "2.0.0"}))
        vector = _mock_vector()
        store = _make_store(redis_client=redis, vector=vector)

        with patch.dict(os.environ, {"ACC_ROLE_CONFIG_PATH": str(tmp_path / "absent.yaml")}):
            role = store.load_at_startup()

        assert role.purpose == "from-redis"
        assert role.version == "2.0.0"

    def test_lancedb_wins_over_config_when_no_file_no_redis(self, tmp_path):
        vector = _mock_vector(rows=[{
            "purpose": "from-lancedb",
            "version": "3.0.0",
            "persona": "analytical",
            "seed_context": "",
            "task_types_json": "[]",
            "allowed_actions_json": "[]",
            "category_b_overrides_json": "{}",
        }])
        store = _make_store(vector=vector)

        with patch.dict(os.environ, {"ACC_ROLE_CONFIG_PATH": str(tmp_path / "absent.yaml")}):
            role = store.load_at_startup()

        assert role.purpose == "from-lancedb"
        assert role.persona == "analytical"

    def test_config_default_used_when_all_sources_absent(self, tmp_path):
        store = _make_store(
            role_def={"purpose": "from-config", "version": "0.1.0"}
        )
        with patch.dict(os.environ, {"ACC_ROLE_CONFIG_PATH": str(tmp_path / "absent.yaml")}):
            role = store.load_at_startup()

        assert role.purpose == "from-config"

    def test_redis_unreachable_falls_through(self, tmp_path):
        redis = MagicMock()
        redis.get.side_effect = ConnectionError("redis down")
        store = _make_store(redis_client=redis, role_def={"purpose": "default", "version": "0.1.0"})

        with patch.dict(os.environ, {"ACC_ROLE_CONFIG_PATH": str(tmp_path / "absent.yaml")}):
            role = store.load_at_startup()  # should not raise

        assert role.purpose == "default"  # fell through to config default


# ---------------------------------------------------------------------------
# apply_update — happy path (REQ-STORE-004 to REQ-STORE-008)
# ---------------------------------------------------------------------------

class TestApplyUpdate:
    def _make_payload(
        self,
        approver_id: str = "arbiter-7e3a",
        signature: str = "fake-sig",
        version: str = "0.2.0",
        purpose: str = "updated purpose",
    ) -> dict:
        return {
            "approver_id": approver_id,
            "signature": signature,
            "role_definition": {
                "purpose": purpose,
                "version": version,
                "persona": "formal",
            },
        }

    def _make_store_with_arbiter(self, arbiter_id: str = "arbiter-7e3a"):
        registry_json = json.dumps({"arbiter_id": arbiter_id})
        redis = _mock_redis(registry_json=registry_json)
        vector = _mock_vector()
        return _make_store(redis_client=redis, vector=vector), redis, vector

    def test_happy_path_updates_current_role(self):
        store, redis, vector = self._make_store_with_arbiter()
        payload = self._make_payload()
        store.apply_update(payload)
        assert store.get_current().version == "0.2.0"
        assert store.get_current().purpose == "updated purpose"

    def test_happy_path_writes_to_redis(self):
        store, redis, vector = self._make_store_with_arbiter()
        store.apply_update(self._make_payload())
        redis.set.assert_called_once()

    def test_happy_path_inserts_role_definition_to_lancedb(self):
        store, redis, vector = self._make_store_with_arbiter()
        store.apply_update(self._make_payload())
        # insert called for role_definitions (at minimum)
        insert_calls = [str(call) for call in vector.insert.call_args_list]
        assert any("role_definitions" in c for c in insert_calls)

    def test_rejection_on_missing_approver_id(self):
        store = _make_store()
        with pytest.raises(RoleUpdateRejectedError, match="approver_id"):
            store.apply_update(self._make_payload(approver_id=""))

    def test_rejection_on_missing_signature(self):
        store = _make_store()
        with pytest.raises(RoleUpdateRejectedError, match="signature"):
            store.apply_update(self._make_payload(signature=""))

    def test_rejection_on_wrong_approver(self):
        store, redis, vector = self._make_store_with_arbiter(arbiter_id="arbiter-real")
        with pytest.raises(RoleUpdateRejectedError, match="arbiter"):
            store.apply_update(self._make_payload(approver_id="arbiter-imposter"))

    def test_rejection_does_not_change_current_role(self, tmp_path):
        store = _make_store(role_def={"purpose": "original", "version": "0.1.0"})
        # load_at_startup sets _current from config default (no file / redis / lancedb)
        with patch.dict(os.environ, {"ACC_ROLE_CONFIG_PATH": str(tmp_path / "absent.yaml")}):
            store.load_at_startup()
        assert store._current.purpose == "original"
        with pytest.raises(RoleUpdateRejectedError):
            store.apply_update(self._make_payload(signature=""))
        # role unchanged after rejection
        assert store._current.purpose == "original"

    def test_rejection_appends_audit_row(self):
        vector = _mock_vector()
        store = _make_store(vector=vector)
        with pytest.raises(RoleUpdateRejectedError):
            store.apply_update(self._make_payload(approver_id=""))
        # audit insert should have been called
        insert_calls = [str(call) for call in vector.insert.call_args_list]
        assert any("role_audit" in c for c in insert_calls)


# ---------------------------------------------------------------------------
# get_history (REQ-STORE-002 / design.md task 2e)
# ---------------------------------------------------------------------------

class TestGetHistory:
    def test_returns_sorted_list(self):
        now = time.time()
        rows = [
            {"id": "a", "agent_id": AGENT_ID, "ts": now - 100, "event_type": "loaded",
             "old_version": "", "new_version": "0.1.0", "diff_summary": "", "approver_id": ""},
            {"id": "b", "agent_id": AGENT_ID, "ts": now - 10, "event_type": "updated",
             "old_version": "0.1.0", "new_version": "0.2.0", "diff_summary": "", "approver_id": "arb"},
        ]
        vector = _mock_vector(rows=rows)
        store = _make_store(vector=vector)
        history = store.get_history(n=10)
        # Most recent first
        assert history[0]["new_version"] == "0.2.0"

    def test_returns_empty_when_no_vector(self):
        store = _make_store()
        assert store.get_history() == []

    def test_handles_lancedb_error_gracefully(self):
        vector = MagicMock()
        vector._db.open_table.side_effect = Exception("table not found")
        store = _make_store(vector=vector)
        result = store.get_history()
        assert result == []
