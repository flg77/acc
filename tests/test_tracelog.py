"""acc.tracelog — durable session tracelog + post-session governance verify.

Pure-stdlib module (no acc deps), so these run anywhere.  Cover the emit →
load round-trip, the best-effort/never-raise contract, and verify_governance
(the machine-checkable "Cat A/B/C present + verifiable post-session" gate).
"""

from __future__ import annotations

import json

import pytest

from acc import tracelog


@pytest.fixture
def tdir(tmp_path, monkeypatch):
    monkeypatch.setenv("ACC_TRACELOG_DIR", str(tmp_path))
    monkeypatch.delenv("ACC_TRACELOG_ENABLED", raising=False)
    return tmp_path


def _emit_full_turn(session, task_id, *, blocked=False, cats=("A", "B", "C"),
                    tool_ok=True, redteam=False):
    tracelog.log_prompt_in(session, task_id=task_id, role="assistant",
                           prompt="hello", agent_id="a1", collective_id="sol-01")
    tracelog.log_tool_call(session, task_id=task_id, kind="skill",
                           target="fs_write", args={"p": "x"}, ok=tool_ok,
                           output='{"ok": true}', error="" if tool_ok else "denied")
    for c in cats:
        verdict = "block" if (blocked and c == "A") else (
            "AUTO" if c == "B" else "present" if c == "C" else "allow")
        tracelog.log_governance(session, task_id=task_id, category=c, verdict=verdict)
    if redteam:
        tracelog.log_redteam(session, task_id=task_id,
                             challenge="cat_a_self_challenge", outcome="2 findings")
    tracelog.log_reply_out(session, task_id=task_id, role="assistant",
                           reply="hi", blocked=blocked)


def test_emit_appends_jsonl_and_loads_in_order(tdir):
    _emit_full_turn("s1", "t1")
    recs = tracelog.load_session("s1")
    kinds = [r["kind"] for r in recs]
    assert kinds == [
        tracelog.KIND_PROMPT_IN, tracelog.KIND_TOOL_CALL,
        tracelog.KIND_GOVERNANCE, tracelog.KIND_GOVERNANCE,
        tracelog.KIND_GOVERNANCE, tracelog.KIND_REPLY_OUT,
    ]
    # Each line is independently valid JSON with the envelope fields.
    raw = (tdir / "s1.jsonl").read_text().splitlines()
    assert all(json.loads(ln)["session_id"] == "s1" for ln in raw)
    assert all("ts" in json.loads(ln) for ln in raw)


def test_prompt_in_and_reply_out_capture_both_directions(tdir):
    tracelog.log_prompt_in("s", task_id="t", role="coding_agent",
                           prompt="write a scraper")
    tracelog.log_reply_out("s", task_id="t", role="coding_agent",
                           reply="import httpx", latency_ms=1234)
    recs = tracelog.load_session("s")
    pin = next(r for r in recs if r["kind"] == "prompt_in")
    pout = next(r for r in recs if r["kind"] == "reply_out")
    assert pin["prompt"] == "write a scraper" and pin["role"] == "coding_agent"
    assert pout["reply"] == "import httpx" and pout["latency_ms"] == 1234


def test_tool_call_records_execution_and_output(tdir):
    tracelog.log_tool_call("s", task_id="t", kind="mcp",
                           target="web_fetch.get", args={"url": "x"}, ok=True,
                           output='{"status": 200}')
    r = tracelog.load_session("s")[0]
    assert r["tool_kind"] == "mcp" and r["target"] == "web_fetch.get"
    assert r["ok"] is True and "200" in r["output"]


def test_verify_governance_complete_when_all_cats_present(tdir):
    _emit_full_turn("s", "t1")
    _emit_full_turn("s", "t2")
    v = tracelog.verify_governance(tracelog.load_session("s"))
    assert v["cat_abc_complete"] is True
    assert v["any_blocked"] is False
    assert len(v["turns"]) == 2
    assert v["turns"][0]["categories_present"] == ["A", "B", "C"]
    assert v["turns"][0]["missing"] == []


def test_verify_governance_flags_missing_category(tdir):
    _emit_full_turn("s", "t1", cats=("A", "B"))  # no Cat-C
    v = tracelog.verify_governance(tracelog.load_session("s"))
    assert v["cat_abc_complete"] is False
    assert v["turns"][0]["missing"] == ["C"]


def test_verify_governance_detects_block_from_verdict_and_tool_refusal(tdir):
    _emit_full_turn("s", "t1", blocked=True)              # Cat-A verdict=block
    _emit_full_turn("s", "t2", tool_ok=False, cats=("A", "B", "C"))  # tool refused
    v = tracelog.verify_governance(tracelog.load_session("s"))
    assert v["any_blocked"] is True
    assert v["turns"][0]["blocked"] is True   # from Cat-A block verdict
    assert v["turns"][1]["blocked"] is True   # from the A-017/A-018 tool refusal


def test_verify_governance_counts_redteam_turns(tdir):
    _emit_full_turn("s", "t1", redteam=True)
    _emit_full_turn("s", "t2", redteam=False)
    v = tracelog.verify_governance(tracelog.load_session("s"))
    assert v["redteam_turns"] == 1
    assert v["turns"][0]["redteam"] is True and v["turns"][1]["redteam"] is False


def test_disabled_writes_nothing(tdir, monkeypatch):
    monkeypatch.setenv("ACC_TRACELOG_ENABLED", "0")
    tracelog.log_prompt_in("s", task_id="t", role="r", prompt="x")
    assert tracelog.load_session("s") == []


def test_emit_never_raises_on_bad_target(monkeypatch):
    # A read-only / nonexistent-parent dir must not raise — best-effort contract.
    monkeypatch.setenv("ACC_TRACELOG_DIR", "/proc/nonexistent/cannot/create")
    monkeypatch.delenv("ACC_TRACELOG_ENABLED", raising=False)
    tracelog.log_prompt_in("s", task_id="t", role="r", prompt="x")  # must not raise


def test_safe_id_prevents_path_escape(tdir):
    tracelog.emit("../../etc/evil", tracelog.KIND_SESSION_START)
    # The crafted id is sanitized: path separators are stripped, so the write
    # stays inside tdir (the real safety property — a literal ".." in the flat
    # filename can't traverse without a separator).
    files = list(tdir.glob("*.jsonl"))
    assert len(files) == 1
    assert "/" not in files[0].name
    assert files[0].parent == tdir


def test_list_sessions(tdir):
    tracelog.log_session_start("alpha")
    tracelog.log_session_start("beta")
    assert set(tracelog.list_sessions()) == {"alpha", "beta"}
