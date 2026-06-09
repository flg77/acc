"""Tests for acc.tui.session_store — TUI session save/detach/resume (#162)."""

from __future__ import annotations

import time

from acc.tui import session_store as ss


def _use_tmp(monkeypatch, tmp_path):
    monkeypatch.setenv("ACC_SESSIONS_DIR", str(tmp_path))


def test_sessions_dir_env_override(monkeypatch, tmp_path):
    _use_tmp(monkeypatch, tmp_path)
    assert ss.sessions_dir() == tmp_path


def test_save_load_roundtrip(monkeypatch, tmp_path):
    _use_tmp(monkeypatch, tmp_path)
    payload = {
        "history": [{"role": "operator", "text": "hi"},
                    {"role": "agent", "text": "hello"}],
        "operating_mode": "ASK_PERMISSIONS",
        "target_role": "assistant",
        "workspace_project": "demo",
        "collective_id": "acc-e2e",
    }
    p = ss.save_session("sess-a", payload)
    assert p is not None and p.is_file()
    loaded = ss.load_session("sess-a")
    assert loaded["operating_mode"] == "ASK_PERMISSIONS"
    assert loaded["target_role"] == "assistant"
    assert len(loaded["history"]) == 2
    assert loaded["schema_version"] == ss.SCHEMA_VERSION
    assert "saved_at" in loaded


def test_latest_and_resume_sentinel(monkeypatch, tmp_path):
    _use_tmp(monkeypatch, tmp_path)
    ss.save_session("old", {"history": [{"role": "operator", "text": "1"}]})
    time.sleep(0.02)
    ss.save_session("new", {"history": [{"role": "operator", "text": "2"}]})
    assert ss.latest_session_id() == "new"
    # the "latest" sentinel resolves to the newest session
    assert ss.load_session("latest")["session_id"] == "new"


def test_list_sessions_summaries(monkeypatch, tmp_path):
    _use_tmp(monkeypatch, tmp_path)
    ss.save_session("one", {"history": [{"role": "operator", "text": "a"}],
                            "collective_id": "c1"})
    time.sleep(0.02)
    ss.save_session("two", {"history": [{"role": "x"}, {"role": "y"}]})
    rows = ss.list_sessions()
    assert [r["session_id"] for r in rows] == ["two", "one"]  # newest first
    assert rows[0]["entries"] == 2
    assert rows[1]["collective_id"] == "c1"


def test_missing_and_empty(monkeypatch, tmp_path):
    _use_tmp(monkeypatch, tmp_path)
    assert ss.load_session("nope") is None
    assert ss.load_session("latest") is None       # none saved yet
    assert ss.latest_session_id() is None
    assert ss.list_sessions() == []


def test_id_is_path_traversal_safe(monkeypatch, tmp_path):
    _use_tmp(monkeypatch, tmp_path)
    p = ss.session_path("../../etc/passwd")
    # the file must stay inside the sessions dir, regardless of the id
    assert p.parent == tmp_path
    assert "/" not in p.name and "\\" not in p.name


def test_save_is_atomic_no_tmp_left(monkeypatch, tmp_path):
    _use_tmp(monkeypatch, tmp_path)
    ss.save_session("atomic", {"history": [{"role": "operator", "text": "z"}]})
    leftovers = list(tmp_path.glob("*.tmp"))
    assert leftovers == [], f"temp files left behind: {leftovers}"
