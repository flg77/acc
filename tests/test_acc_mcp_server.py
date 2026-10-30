"""`20261030-acc-mcp-server` -- ACC as an MCP server for the coding agents.

CS-02: nine tools over the external work store and the note store, behind MCP
over stdio.  CS-05: ``acc-cli ingest session`` writes one note per session and
closes what the session left running.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from acc.external_work import (
    DONE,
    FAILED,
    QUEUED,
    RUNNING,
    ExternalNote,
    ExternalNoteStore,
    ExternalWorkItem,
    ExternalWorkStore,
)
from acc.mcp.server import TOOLS, Coordinator, McpServer, ToolError
from tests.test_058_k1_ask_once_phase0 import _FakeRedis


#: A child ``acc-cli`` run from a scratch directory imports this checkout.
_CHILD_ENV = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])}


class _Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        self.t += 1.0
        return self.t


def _coord(executor="codex", redis=None, published=None, ask=None, **kw) -> Coordinator:
    redis = redis if redis is not None else _FakeRedis()

    async def publish(subject, payload):
        if published is not None:
            published.append((subject, payload))

    return Coordinator(executor=executor, collective_id="sol-01",
                       work=ExternalWorkStore(redis, "sol-01"), notes=ExternalNoteStore(redis, "sol-01"),
                       publish=publish, ask=ask, session_id=kw.pop("session_id", f"{executor}-s1"),
                       repo=kw.pop("repo", "tui-designer"), branch=kw.pop("branch", "feat/x"),
                       clock=_Clock(), **kw)


# ---------------------------------------------------------------------------
# the tools
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_claim_new_work_progress_and_complete():
    published: list = []
    c = _coord(published=published)
    item = (await c.call("claim", {"title": "fix the login redirect"}))["item"]
    assert item["status"] == RUNNING and item["executor"] == "codex"
    assert (item["repo"], item["branch"], item["session_id"]) == ("tui-designer", "feat/x", "codex-s1")

    row = (await c.call("progress", {"item_id": item["item_id"], "note": "found the cause"}))["item"]
    assert row["last_progress"] == "found the cause"

    done = (await c.call("complete", {"item_id": item["item_id"], "outcome": "fixed", "pr": "#12",
                                      "tokens": 1200, "cost_usd": 0.4}))["item"]
    assert done["status"] == DONE and done["pr"] == "#12" and done["tokens"] == 1200

    subjects = {s for s, _ in published}
    assert subjects == {"acc.sol-01.external.work"}
    assert [p["item"]["status"] for _, p in published] == [RUNNING, RUNNING, DONE]
    assert published[0][1]["signal_type"] == "EXTERNAL_WORK"

    # the outcome is kept as a note the others can recall
    [note] = (await c.call("recall", {"query": "login redirect"}))["notes"]
    assert "fixed" in note["text"] and note["item_id"] == item["item_id"]


@pytest.mark.asyncio
async def test_handoff_queues_for_the_other_agent_who_can_then_claim_it():
    redis = _FakeRedis()
    codex, cursor = _coord("codex", redis), _coord("cursor", redis)
    iid = (await codex.call("claim", {"title": "port the parser"}))["item"]["item_id"]
    row = (await codex.call("handoff", {"item_id": iid, "to_executor": "cursor",
                                        "brief": "tests in tests/parser; keep the API"}))["item"]
    assert (row["status"], row["executor"], row["handoff_from"]) == (QUEUED, "cursor", "codex")

    with pytest.raises(ToolError, match="handed to cursor"):
        await _coord("claude-vertex", redis).call("claim", {"item_id": iid})
    reg = await cursor.call("register", {"purpose": "parser"})
    assert [r["item_id"] for r in reg["handed_to_you"]] == [iid]

    taken = (await cursor.call("claim", {"item_id": iid}))["item"]
    assert taken["status"] == RUNNING and taken["executor"] == "cursor"
    with pytest.raises(ToolError, match="being worked on by cursor"):
        await codex.call("progress", {"item_id": iid, "note": "still mine?"})


@pytest.mark.asyncio
async def test_only_open_items_move():
    c = _coord()
    with pytest.raises(ToolError, match="no item"):
        await c.call("progress", {"item_id": "ext-nope", "note": "x"})
    with pytest.raises(ToolError, match="item_id is required"):
        await c.call("complete", {"outcome": "x"})
    iid = (await c.call("claim", {"title": "t"}))["item"]["item_id"]
    await c.call("complete", {"item_id": iid, "outcome": "gave up", "status": FAILED})
    for tool, args in (("progress", {"note": "x"}), ("handoff", {"to_executor": "cursor", "brief": "b"}),
                       ("complete", {"outcome": "again"}), ("claim", {})):
        with pytest.raises(ToolError, match="already FAILED"):
            await c.call(tool, {"item_id": iid, **args})


@pytest.mark.asyncio
async def test_required_arguments_and_bad_values_are_refused():
    c = _coord()
    with pytest.raises(ToolError, match="title"):
        await c.call("claim", {})
    iid = (await c.call("claim", {"title": "t"}))["item"]["item_id"]
    with pytest.raises(ToolError, match="note is required"):
        await c.call("progress", {"item_id": iid, "note": "  "})
    with pytest.raises(ToolError, match="DONE or FAILED"):
        await c.call("complete", {"item_id": iid, "outcome": "x", "status": "PARKED"})
    with pytest.raises(ToolError, match="numbers"):
        await c.call("complete", {"item_id": iid, "outcome": "x", "tokens": "many"})
    with pytest.raises(ToolError, match="text is required"):
        await c.call("remember", {"text": ""})
    with pytest.raises(KeyError):
        await c.call("shell", {})


@pytest.mark.asyncio
async def test_register_records_the_session_and_shows_this_repos_open_work():
    redis = _FakeRedis()
    c = _coord("claude-vertex", redis)
    await _coord("codex", redis, repo="other-repo").call("claim", {"title": "elsewhere"})
    mine = (await c.call("claim", {"title": "here"}))["item"]["item_id"]
    await c.call("remember", {"text": "the TUI tests need TERM=xterm", "tags": ["tests"]})

    out = await c.call("register", {"purpose": "tidy the layout code"})
    assert [r["item_id"] for r in out["open_items"]] == [mine]
    assert out["recent_notes"][0]["text"].startswith("the TUI tests")
    [session] = await c.work.sessions()
    assert session["executor"] == "claude-vertex" and session["purpose"] == "tidy the layout code"
    assert redis.ttl["acc:sol-01:ext_session:claude-vertex-s1"] == 7 * 24 * 3600


@pytest.mark.asyncio
async def test_board_filters():
    redis = _FakeRedis()
    a, b = _coord("codex", redis), _coord("cursor", redis, repo="r2")
    await a.call("claim", {"title": "one"})
    iid = (await b.call("claim", {"title": "two"}))["item"]["item_id"]
    await b.call("complete", {"item_id": iid, "outcome": "ok"})
    assert (await a.call("board", {}))["total"] == 2
    assert [r["title"] for r in (await a.call("board", {"executor": "cursor"}))["items"]] == ["two"]
    assert [r["title"] for r in (await a.call("board", {"status": RUNNING}))["items"]] == ["one"]
    assert (await a.call("board", {"repo": "r2", "limit": 1}))["total"] == 1


@pytest.mark.asyncio
async def test_recall_matches_words_and_tags_newest_first():
    c = _coord()
    await c.call("remember", {"text": "Vertex needs ADC mounted read-only", "tags": ["vertex"]})
    await c.call("remember", {"text": "the parser keeps its API", "tags": ["parser"]})
    await c.call("remember", {"text": "vertex region is europe-west4", "tags": ["vertex"]})
    found = (await c.call("recall", {"query": "vertex ADC"}))["notes"]
    assert found[0]["text"].startswith("Vertex needs ADC")         # two words beat one
    assert [n["text"] for n in (await c.call("recall", {"tags": ["parser"]}))["notes"]] == \
        ["the parser keeps its API"]
    assert len((await c.call("recall", {}))["notes"]) == 3
    assert (await c.call("recall", {"query": "kubernetes"}))["notes"] == []


@pytest.mark.asyncio
async def test_ask_prefixes_who_asks_and_needs_a_bus():
    seen = []

    async def ask(question, task_id, wait_s):
        seen.append((question, task_id, wait_s))
        return {"task_id": "t-1", "status": "answered", "answer": "codex should take ext-1"}

    out = await _coord(ask=ask).call("ask", {"question": "who takes the parser?", "wait_s": 5000})
    assert out["answer"].startswith("codex")
    assert seen == [("[from codex, repo tui-designer] who takes the parser?", "", 600.0)]
    with pytest.raises(ToolError, match="question"):
        await _coord(ask=ask).call("ask", {})
    with pytest.raises(ToolError, match="not connected"):
        await _coord().call("ask", {"question": "x"})


@pytest.mark.asyncio
async def test_a_bus_that_is_down_does_not_fail_the_tool():
    async def broken(subject, payload):
        raise ConnectionError("nats down")

    c = _coord()
    c._publish = broken
    item = (await c.call("claim", {"title": "t"}))["item"]
    assert (await c.work.get(item["item_id"])).status == RUNNING


@pytest.mark.asyncio
async def test_the_bus_ask_keeps_waiting_across_calls():
    from acc.mcp.server import _BusAsk

    class _Reply:
        blocked, output = False, "the answer"

    class _Channel:
        def __init__(self):
            self.future = asyncio.get_running_loop().create_future()

        async def send(self, *, prompt, target_role):
            assert target_role == "assistant"
            return "t-9"

        async def receive(self, task_id, timeout):
            return await self.future

    ask = _BusAsk("nats://x", "sol-01")
    ask._channel = channel = _Channel()
    first = await ask("q", "", 0.01)
    assert first == {"task_id": "t-9", "status": "waiting",
                     "hint": "call ask again with this task_id to keep waiting"}
    channel.future.set_result(_Reply())
    second = await ask("", "t-9", 1.0)
    assert second["status"] == "answered" and second["answer"] == "the answer"
    with pytest.raises(ToolError, match="no question"):
        await ask("", "t-9", 1.0)


# ---------------------------------------------------------------------------
# the protocol
# ---------------------------------------------------------------------------


def _rpc(method, params=None, rid=1):
    msg = {"jsonrpc": "2.0", "method": method}
    if params is not None:
        msg["params"] = params
    if rid is not None:
        msg["id"] = rid
    return msg


@pytest.mark.asyncio
async def test_initialize_negotiates_the_protocol_version():
    server = McpServer(_coord(), version="0.29.0")
    out = await server.handle(_rpc("initialize", {"protocolVersion": "2025-03-26"}))
    assert out["result"]["protocolVersion"] == "2025-03-26"
    assert out["result"]["capabilities"] == {"tools": {"listChanged": False}}
    assert out["result"]["serverInfo"] == {"name": "acc", "version": "0.29.0"}
    newer = await server.handle(_rpc("initialize", {"protocolVersion": "2099-01-01"}))
    assert newer["result"]["protocolVersion"] == "2025-06-18"
    assert await server.handle(_rpc("notifications/initialized", rid=None)) is None
    assert (await server.handle(_rpc("ping")))["result"] == {}


@pytest.mark.asyncio
async def test_tools_list_and_call():
    server = McpServer(_coord())
    listed = (await server.handle(_rpc("tools/list")))["result"]["tools"]
    assert [t["name"] for t in listed] == ["register", "board", "claim", "progress", "handoff",
                                           "complete", "remember", "recall", "ask"]
    assert all(t["inputSchema"]["type"] == "object" for t in listed) and listed is TOOLS

    ok = (await server.handle(_rpc("tools/call", {"name": "claim", "arguments": {"title": "t"}})))["result"]
    assert ok["isError"] is False and json.loads(ok["content"][0]["text"])["item"]["status"] == RUNNING
    assert ok["structuredContent"]["item"]["title"] == "t"

    refused = (await server.handle(_rpc("tools/call", {"name": "claim", "arguments": {}})))["result"]
    assert refused["isError"] is True and "title" in refused["structuredContent"]["error"]

    unknown = await server.handle(_rpc("tools/call", {"name": "rm", "arguments": {}}))
    assert unknown["error"]["code"] == -32602
    bad = await server.handle(_rpc("tools/call", {"name": "claim", "arguments": [1]}))
    assert bad["error"]["code"] == -32602
    assert (await server.handle(_rpc("resources/list")))["error"]["code"] == -32601


@pytest.mark.asyncio
async def test_a_tool_fault_is_an_error_result_not_a_crash():
    c = _coord()

    async def boom(params):
        raise RuntimeError("redis went away")

    c.tool_board = boom
    out = (await McpServer(c).handle(_rpc("tools/call", {"name": "board", "arguments": {}})))["result"]
    assert out["isError"] is True and "redis went away" in out["structuredContent"]["error"]


@pytest.mark.asyncio
async def test_the_stdio_loop_answers_requests_line_by_line():
    lines = [json.dumps(_rpc("initialize", {"protocolVersion": "2025-06-18"})).encode() + b"\n",
             json.dumps(_rpc("notifications/initialized", rid=None)).encode() + b"\n",
             b"\n", b"not json\n",
             json.dumps(_rpc("tools/list", rid=2)).encode() + b"\n", b""]
    out: list[str] = []

    async def reader():
        return lines.pop(0)

    await McpServer(_coord()).serve(reader, out.append)
    replies = [json.loads(x) for x in out]
    assert [r.get("id") for r in replies] == [1, None, 2]
    assert replies[1]["error"]["code"] == -32700


def test_serve_without_redis_exits_with_a_reason_and_a_clean_stdout(tmp_path):
    proc = subprocess.run(
        [sys.executable, "-m", "acc.cli", "mcp", "serve", "--executor", "codex",
         "--redis-url", "redis://127.0.0.1:1"],
        input="", capture_output=True, text=True, timeout=60, cwd=tmp_path, env=_CHILD_ENV,
    )
    assert proc.returncode != 0
    assert proc.stdout == ""                       # stdout belongs to the protocol
    assert "not reachable" in proc.stderr


# ---------------------------------------------------------------------------
# CS-05 -- acc-cli ingest session
# ---------------------------------------------------------------------------


def test_transcripts_of_all_three_shapes_parse():
    from acc.session_ingest import parse_transcript

    claude = "\n".join(json.dumps(r) for r in [
        {"type": "user", "message": {"role": "user", "content": "fix the redirect"}},
        {"type": "assistant", "message": {"role": "assistant",
                                          "content": [{"type": "text", "text": "done"},
                                                      {"type": "tool_use", "name": "Edit"}]}},
        {"type": "summary", "summary": "x"},
    ])
    codex = json.dumps({"type": "response_item", "payload": {
        "type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "ok"}]}})
    assert [(t.role, t.text) for t in parse_transcript(claude)] == [("user", "fix the redirect"),
                                                                    ("assistant", "done")]
    assert [(t.role, t.text) for t in parse_transcript(codex)] == [("assistant", "ok")]
    assert [(t.role, t.text) for t in parse_transcript("plain notes\nmore")] == [("user", "plain notes\nmore")]
    assert parse_transcript("") == []


def test_digest_and_codex_notify_turns():
    from acc.session_ingest import Turn, digest, turns_from_event

    turns = turns_from_event({"type": "agent-turn-complete", "input-messages": ["port the parser", "and test it"],
                              "last-assistant-message": "ported; 12 tests pass"})
    assert [t.role for t in turns] == ["user", "user", "assistant"]
    text = digest(turns)
    assert "goal: port the parser" in text and "last ask: and test it" in text
    assert "ended with: ported; 12 tests pass" in text
    assert digest([]).startswith("goal: (no user turn)")
    assert digest([Turn("user", "x" * 1000)]).count("x") == 300


@pytest.mark.asyncio
async def test_ingest_writes_one_note_per_session_and_closes_its_running_items():
    from acc.session_ingest import Turn, ingest_session

    redis = _FakeRedis()
    work, notes = ExternalWorkStore(redis, "sol-01"), ExternalNoteStore(redis, "sol-01")
    await work.save(ExternalWorkItem(item_id="ext-a", title="by id", status=RUNNING, session_id="S1",
                                     executor="codex"))
    await work.save(ExternalWorkItem(item_id="ext-b", title="same stream", status=RUNNING, session_id="mcp-x",
                                     executor="codex", repo="r", branch="feat/x"))
    await work.save(ExternalWorkItem(item_id="ext-c", title="other branch", status=RUNNING,
                                     executor="codex", repo="r", branch="main"))
    await work.save(ExternalWorkItem(item_id="ext-d", title="other tool", status=RUNNING,
                                     executor="cursor", repo="r", branch="feat/x"))
    turns = [Turn("user", "fix it"), Turn("assistant", "fixed")]
    out = await ingest_session(session_id="S1", executor="codex", turns=turns, work=work, notes=notes,
                               repo="r", branch="feat/x", diffstat=" a.py | 2 +-", now=50.0)
    assert sorted(out["closed_items"]) == ["ext-a", "ext-b"] and out["summarized_by"] == "digest"
    a = await work.get("ext-a")
    assert a.status == DONE and "without `complete`" in a.outcome and a.diffstat == " a.py | 2 +-"
    assert (await work.get("ext-c")).status == RUNNING and (await work.get("ext-d")).status == RUNNING

    # a second run (Codex notify fires per turn) replaces the note
    await ingest_session(session_id="S1", executor="codex", turns=turns + [Turn("assistant", "and tested")],
                         work=work, notes=notes, repo="r", branch="feat/x", now=60.0)
    [note] = await notes.recent()
    assert note.note_id == "session-S1" and "and tested" in note.text and note.tags == ["session", "codex"]


@pytest.mark.asyncio
async def test_ingest_uses_the_model_when_named_and_falls_back_on_an_empty_answer():
    from acc.session_ingest import SUMMARY_SYSTEM, Turn, ingest_session

    calls = []

    async def summarize(system, prompt):
        calls.append((system, prompt))
        return "goal: X\nchanged: y.py"

    notes = ExternalNoteStore(None)
    out = await ingest_session(session_id="S", executor="cursor", turns=[Turn("user", "do X")],
                               work=ExternalWorkStore(None), notes=notes, summarize=summarize)
    assert out["summarized_by"] == "model" and calls[0][0] == SUMMARY_SYSTEM
    assert calls[0][1] == "USER: do X"

    async def empty(system, prompt):
        return "  "

    out = await ingest_session(session_id="S2", executor="cursor", turns=[Turn("user", "do X")],
                               work=ExternalWorkStore(None), notes=notes, summarize=empty)
    assert out["summarized_by"] == "digest" and out["summary"].startswith("goal: do X")


def test_ingest_cli_reads_the_event_and_never_fails_the_hook(tmp_path):
    from acc.cli import ingest_cmd

    assert ingest_cmd._first({"thread-id": "T", "cwd": "/w"}, "session_id", "thread-id") == "T"
    assert ingest_cmd._event('{"session_id": "S"}') == {"session_id": "S"}
    assert ingest_cmd._event("not json") == {} and ingest_cmd._event(None) == {}

    proc = subprocess.run(
        [sys.executable, "-m", "acc.cli", "ingest", "session", "--executor", "codex",
         "--event", '{"session_id": "S"}', "--redis-url", "redis://127.0.0.1:1"],
        capture_output=True, text=True, timeout=60, cwd=tmp_path, env=_CHILD_ENV,
    )
    assert proc.returncode == 0 and "acc ingest session" in proc.stderr
    strict = subprocess.run(
        [sys.executable, "-m", "acc.cli", "ingest", "session", "--executor", "codex", "--strict",
         "--event", "{}"], capture_output=True, text=True, timeout=60, cwd=tmp_path, env=_CHILD_ENV,
    )
    assert strict.returncode == 1 and "no session id" in strict.stderr


@pytest.mark.asyncio
async def test_note_store_keeps_the_newest_and_replaces_in_place():
    redis = _FakeRedis()
    store = ExternalNoteStore(redis, "sol-01")
    store.KEEP = 3
    for i in range(5):
        await store.put(ExternalNote(note_id=f"n{i}", text=f"note {i}", ts=float(i + 1)))
    assert [n.note_id for n in await store.recent()] == ["n4", "n3", "n2"]
    await store.put(ExternalNote(note_id="n2", text="note 2 again", ts=9.0))
    assert [n.note_id for n in await store.recent()] == ["n2", "n4", "n3"]

    mem = ExternalNoteStore(None)
    mem.KEEP = 2
    for i in range(3):
        await mem.put(ExternalNote(note_id=f"m{i}", text="t"))
    assert [n.note_id for n in await mem.recent()] == ["m2", "m1"]
