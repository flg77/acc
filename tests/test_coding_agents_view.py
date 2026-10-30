"""`20261030-chief-of-staff-view` -- the assistant sees the coding agents' work (CS-11)."""

from __future__ import annotations

import pytest

from acc import coding_agents_view as cav
from acc.config import RoleDefinitionConfig
from acc.external_work import (
    DONE,
    QUEUED,
    RUNNING,
    STALE_AFTER_S,
    ExternalNote,
    ExternalNoteStore,
    ExternalWorkItem,
    ExternalWorkStore,
)
from tests.test_058_k1_ask_once_phase0 import _FakeRedis
from tests.test_cognitive_core import _make_core, _mock_llm

NOW = 1_000_000.0


def _item(iid, status=RUNNING, **kw) -> ExternalWorkItem:
    progress = kw.pop("progress", [])
    it = ExternalWorkItem(item_id=iid, title=kw.pop("title", f"work {iid}"), status=status,
                          updated_ts=kw.pop("updated_ts", NOW - 600), **kw)
    it.progress = list(progress)
    return it


def test_nothing_reported_renders_nothing():
    assert cav.render(cav.CodingView(), now=NOW) == ""


def test_the_block_lists_open_finished_sessions_and_notes():
    view = cav.CodingView(
        items=[
            _item("ext-aaaaaaaaaa", executor="codex", repo="tui", branch="feat/x",
                  progress=["claimed", "tests green locally"]),
            _item("ext-bbbbbbbbbb", executor="cursor", updated_ts=NOW - STALE_AFTER_S - 60),
            _item("ext-cccccccccc", QUEUED, executor="claude-vertex", handoff_from="codex",
                  brief="keep the public API"),
            _item("ext-dddddddddd", DONE, executor="codex", outcome="fixed the redirect", pr="#12"),
            _item("ext-eeeeeeeeee", DONE, executor="codex", updated_ts=NOW - 3 * 86400),
        ],
        sessions=[{"executor": "cursor", "repo": "tui", "purpose": "layout", "last_seen_ts": NOW - 7200},
                  {"executor": "codex", "repo": "old", "last_seen_ts": NOW - 3 * 86400}],
        notes=[ExternalNote(note_id="n1", text="ADC must be mounted read-only\nmore", tags=["vertex"],
                            executor="claude-vertex", repo="acc")],
    )
    out = cav.render(view, now=NOW)
    assert out.startswith(cav.HEADING)
    assert ("- ext-aaaaaaaaaa RUNNING by codex [tui@feat/x]: work ext-aaaaaaaaaa — last 10m ago: "
            "tests green locally") in out
    assert "ext-bbbbbbbbbb RUNNING by cursor" in out and "STALE" in out
    assert "ext-cccccccccc QUEUED for claude-vertex (handed over by codex)" in out
    assert "brief: keep the public API" in out
    assert "ext-dddddddddd DONE by codex: work ext-dddddddddd — fixed the redirect (#12)" in out
    assert "ext-eeeeeeeeee" not in out                    # finished days ago
    assert "- cursor in tui (2h ago): layout" in out and "old" not in out
    assert "- claude-vertex in acc [vertex]: ADC must be mounted read-only" in out
    assert "more" not in out


def test_the_block_is_bounded():
    items = [_item(f"ext-{i:010d}", title="x" * 300, progress=["y" * 400]) for i in range(40)]
    out = cav.render(cav.CodingView(items=items), now=NOW)
    assert out.count("RUNNING by") <= cav.MAX_OPEN
    assert len(out) <= cav.MAX_CHARS + 40


def test_a_long_block_is_cut_at_a_line_and_says_so(monkeypatch):
    monkeypatch.setattr(cav, "MAX_CHARS", 400)
    items = [_item(f"ext-{i:010d}") for i in range(10)]
    out = cav.render(cav.CodingView(items=items), now=NOW)
    assert out.endswith("- … (more on the work board)") and len(out) < 460


@pytest.mark.asyncio
async def test_gather_reads_the_stores():
    redis = _FakeRedis()
    await ExternalWorkStore(redis, "sol-01").save(_item("ext-aaaaaaaaaa"))
    await ExternalWorkStore(redis, "sol-01").touch_session({"session_id": "s", "last_seen_ts": NOW})
    await ExternalNoteStore(redis, "sol-01").put(ExternalNote(note_id="n", text="t"))
    view = await cav.gather(redis, "sol-01")
    assert [it.item_id for it in view.items] == ["ext-aaaaaaaaaa"]
    assert [s["session_id"] for s in view.sessions] == ["s"] and [n.note_id for n in view.notes] == ["n"]


def test_the_kill_switch(monkeypatch):
    assert cav.enabled()
    monkeypatch.setenv("ACC_CODING_AGENTS_VIEW", "0")
    assert not cav.enabled()


# ---------------------------------------------------------------------------
# the prompt
# ---------------------------------------------------------------------------


def _role(**kw) -> RoleDefinitionConfig:
    d = {"purpose": "Drive requests to outcomes.", "persona": "concise", "seed_context": "",
         "version": "0.1.0"}
    d.update(kw)
    return RoleDefinitionConfig.model_validate(d)


def test_the_system_prompt_carries_the_block_only_when_there_is_one():
    core = _make_core(role_label="assistant")
    role = _role(perception_profile="control")
    before = core.build_system_prompt(role)
    assert cav.HEADING not in before
    core._coding_agents_block = f"{cav.HEADING}\n- ext-aaaaaaaaaa RUNNING by codex: x"
    assert cav.HEADING in core.build_system_prompt(role)
    core._coding_agents_block = ""
    assert core.build_system_prompt(role) == before        # byte-identical when unused


@pytest.mark.asyncio
async def test_the_assistant_sees_reported_work_in_its_task():
    redis = _FakeRedis()
    await ExternalWorkStore(redis, "sol-01").save(_item("ext-aaaaaaaaaa", executor="codex", title="port the parser"))
    llm = _mock_llm()
    core = _make_core(llm=llm, redis_client=redis, role_label="assistant")
    core._collective_id = "sol-01"
    await core.process_task({"content": "who is on what?"}, role=_role(perception_profile="control"))
    system = llm.complete.call_args.args[0]
    assert cav.HEADING in system and "ext-aaaaaaaaaa RUNNING by codex" in system


@pytest.mark.asyncio
async def test_other_profiles_and_the_kill_switch_keep_it_out(monkeypatch):
    redis = _FakeRedis()
    await ExternalWorkStore(redis, "sol-01").save(_item("ext-aaaaaaaaaa"))
    for profile, env in (("none", "1"), ("control", "0")):
        monkeypatch.setenv("ACC_CODING_AGENTS_VIEW", env)
        llm = _mock_llm()
        core = _make_core(llm=llm, redis_client=redis, role_label="assistant")
        core._collective_id = "sol-01"
        await core.process_task({"content": "x"}, role=_role(perception_profile=profile))
        assert cav.HEADING not in llm.complete.call_args.args[0], profile
