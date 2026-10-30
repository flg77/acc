"""ACC as an MCP server -- the coding agents report in (CS-02).

`20261030-acc-mcp-server` (vault proposal 062, backlog CS-02).  Claude Code,
Codex and Cursor all consume MCP servers.  Each of them starts this one for
itself over stdio::

    acc-cli mcp serve --executor codex

and gets nine tools that read and write ACC's view of the coding work: the
external work items (:mod:`acc.external_work`) on the work board, the notes
the agents leave each other, and a question to ACC's assistant.

Nothing here executes anything on the ACC side or reads a file: the tools
only move work items, keep notes and ask.  Writes go to ACC's Redis (the
durable record the arbiter's heartbeat replays) and are announced by an
``EXTERNAL_WORK`` signal; a bus that is down only delays the board, it loses
nothing.

The protocol is MCP over stdio -- newline-delimited JSON-RPC 2.0 -- written
out by hand with :mod:`acc.a2a.jsonrpc`, like ACC's own MCP client, so the
server needs no new dependency.  stdout carries the protocol and nothing else;
logs go to stderr.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import sys
import time
import uuid
from typing import Any, Awaitable, Callable

from acc.a2a import jsonrpc
from acc.external_work import (
    DONE,
    FAILED,
    QUEUED,
    RUNNING,
    ExternalNote,
    ExternalNoteStore,
    ExternalWorkItem,
    ExternalWorkStore,
    new_item_id,
)

logger = logging.getLogger("acc.mcp.server")

#: Protocol revisions this server speaks, newest first.
PROTOCOL_VERSIONS: tuple[str, ...] = ("2025-06-18", "2025-03-26", "2024-11-05")

_MAX_TITLE = 200
_MAX_TEXT = 4000
_MAX_TAGS = 10

INSTRUCTIONS = (
    "ACC coordinates the coding agents on this machine. On start call `register`. "
    "Call `recall` before you design. `claim` an item before you work on it, "
    "`progress` at milestones, `complete` when done, `handoff` when another agent "
    "should continue. `remember` what the others should know."
)


class ToolError(Exception):
    """A tool call that cannot be done; the message goes back to the agent."""


def _s(params: dict, key: str, limit: int = _MAX_TEXT) -> str:
    return str(params.get(key, "") or "").strip()[:limit]


def _tags(params: dict) -> list[str]:
    raw = params.get("tags") or []
    if isinstance(raw, str):
        raw = [raw]
    return [str(t).strip()[:40] for t in raw if str(t).strip()][:_MAX_TAGS]


def _schema(props: dict, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": props, "required": required or [],
            "additionalProperties": False}


_STR = {"type": "string"}

TOOLS: list[dict] = [
    {"name": "register",
     "description": "Start of a session: records it, returns this repo's open items, "
                    "items handed over to you and recent notes.",
     "inputSchema": _schema({"purpose": {**_STR, "description": "What this session is for."}})},
    {"name": "board",
     "description": "The coding agents' work items, newest first.",
     "inputSchema": _schema({
         "repo": {**_STR, "description": "Only this repo ('' = all)."},
         "executor": {**_STR, "description": "Only this agent ('' = all)."},
         "status": {"type": "string", "enum": ["", QUEUED, RUNNING, DONE, FAILED]},
         "limit": {"type": "integer", "minimum": 1, "maximum": 100}})},
    {"name": "claim",
     "description": "Start working on an item: an existing one by item_id (queued or handed "
                    "to you), or a new one by title. It becomes RUNNING under your name.",
     "inputSchema": _schema({"item_id": _STR, "title": _STR})},
    {"name": "progress",
     "description": "Add one progress line to an item you are working on.",
     "inputSchema": _schema({"item_id": _STR, "note": _STR}, ["item_id", "note"])},
    {"name": "handoff",
     "description": "Hand an item to another agent with a brief; it goes back to QUEUED.",
     "inputSchema": _schema({"item_id": _STR,
                             "to_executor": {**_STR, "description": "claude-vertex, codex, cursor, ..."},
                             "brief": _STR}, ["item_id", "to_executor", "brief"])},
    {"name": "complete",
     "description": "Finish an item: DONE or FAILED, with the outcome. Also kept as a note.",
     "inputSchema": _schema({
         "item_id": _STR, "outcome": _STR,
         "status": {"type": "string", "enum": [DONE, FAILED]},
         "pr": _STR, "diffstat": _STR,
         "cost_usd": {"type": "number", "minimum": 0},
         "tokens": {"type": "integer", "minimum": 0}}, ["item_id", "outcome"])},
    {"name": "remember",
     "description": "Keep a note the other agents and ACC's assistant can recall "
                    "(a decision, a dead end, how something works).",
     "inputSchema": _schema({"text": _STR, "tags": {"type": "array", "items": _STR},
                             "item_id": _STR}, ["text"])},
    {"name": "recall",
     "description": "Notes matching your words (all recent ones when empty), newest first.",
     "inputSchema": _schema({"query": _STR, "tags": {"type": "array", "items": _STR},
                             "repo": _STR, "limit": {"type": "integer", "minimum": 1, "maximum": 20}})},
    {"name": "ask",
     "description": "Ask ACC's assistant (the chief of staff). Waits up to wait_s seconds; "
                    "if the answer is not ready, call again with the returned task_id.",
     "inputSchema": _schema({"question": _STR, "task_id": _STR,
                             "wait_s": {"type": "number", "minimum": 1, "maximum": 600}})},
]


def _git(cwd: str, *args: str) -> str:
    try:
        out = subprocess.run(["git", "-C", cwd, *args], capture_output=True, text=True, timeout=5)
        return out.stdout.strip() if out.returncode == 0 else ""
    except Exception:  # noqa: BLE001 -- no git, no repo: the fields stay empty
        return ""


def workspace_of(cwd: str) -> tuple[str, str]:
    """``(repo, branch)`` of the git checkout at *cwd*, or ``("", "")``."""
    top = _git(cwd, "rev-parse", "--show-toplevel")
    if not top:
        return "", ""
    return os.path.basename(top), _git(cwd, "rev-parse", "--abbrev-ref", "HEAD")


Publish = Callable[[str, dict], Awaitable[None]]
Ask = Callable[[str, str, float], Awaitable[dict]]


class Coordinator:
    """The nine tools, over the stores and the bus -- no protocol in here."""

    def __init__(
        self, *, executor: str, collective_id: str, work: ExternalWorkStore,
        notes: ExternalNoteStore, publish: Publish | None = None, ask: Ask | None = None,
        session_id: str = "", cwd: str = "", repo: str = "", branch: str = "",
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.executor = executor
        self.collective_id = collective_id
        self.work = work
        self.notes = notes
        self._publish = publish
        self._ask = ask
        self.session_id = session_id or f"{executor}-{uuid.uuid4().hex[:8]}"
        self.cwd = cwd
        self.repo = repo
        self.branch = branch
        self._clock = clock
        self._purpose = ""

    async def call(self, name: str, params: dict) -> dict:
        fn = getattr(self, f"tool_{name}", None)
        if fn is None or name not in {t["name"] for t in TOOLS}:
            raise KeyError(name)
        return await fn(params or {})

    # -- helpers -------------------------------------------------------

    async def _announce(self, item: ExternalWorkItem) -> None:
        if self._publish is None:
            return
        from acc.signals import SIG_EXTERNAL_WORK, subject_external_work  # noqa: PLC0415
        try:
            await self._publish(subject_external_work(self.collective_id), {
                "signal_type": SIG_EXTERNAL_WORK, "agent_id": f"ext:{self.executor}",
                "collective_id": self.collective_id, "ts": self._clock(), "item": item.summary(),
            })
        except Exception:  # noqa: BLE001 -- the heartbeat replays the store
            logger.warning("mcp server: EXTERNAL_WORK not published", exc_info=True)

    async def _item(self, params: dict) -> ExternalWorkItem:
        iid = _s(params, "item_id", 64)
        if not iid:
            raise ToolError("item_id is required")
        item = await self.work.get(iid)
        if item is None:
            raise ToolError(f"no item {iid!r}; `board` lists them")
        return item

    def _mine(self, item: ExternalWorkItem) -> None:
        """Only the agent working on an item moves it."""
        if item.status in (DONE, FAILED):
            raise ToolError(f"{item.item_id} is already {item.status}")
        if item.status == RUNNING and item.executor != self.executor:
            raise ToolError(f"{item.item_id} is being worked on by {item.executor}; "
                            "ask for a handoff instead")
        if item.status == QUEUED:
            raise ToolError(f"{item.item_id} is queued; `claim` it first")

    async def _save(self, item: ExternalWorkItem) -> dict:
        item.updated_ts = item.updated_ts or self._clock()
        await self.work.save(item)
        await self._announce(item)
        return item.summary()

    async def _note(self, text: str, tags: list[str], item_id: str = "", note_id: str = "") -> ExternalNote:
        note = ExternalNote(note_id=note_id or f"note-{uuid.uuid4().hex[:10]}", text=text,
                            tags=tags, executor=self.executor, session_id=self.session_id,
                            repo=self.repo, item_id=item_id, ts=self._clock())
        await self.notes.put(note)
        return note

    # -- the tools -----------------------------------------------------

    async def tool_register(self, params: dict) -> dict:
        self._purpose = _s(params, "purpose", 300) or self._purpose
        now = self._clock()
        await self.work.touch_session({
            "session_id": self.session_id, "executor": self.executor, "repo": self.repo,
            "branch": self.branch, "cwd": self.cwd, "purpose": self._purpose, "last_seen_ts": now,
        })
        items = await self.work.all()
        open_here = [it.summary() for it in items
                     if it.status in (QUEUED, RUNNING) and (not self.repo or it.repo == self.repo)][:20]
        for_me = [it.summary() for it in items if it.status == QUEUED and it.executor == self.executor][:20]
        notes = await self.notes.search(repo=self.repo, limit=5)
        return {"session_id": self.session_id, "executor": self.executor, "repo": self.repo,
                "branch": self.branch, "open_items": open_here, "handed_to_you": for_me,
                "recent_notes": [n.to_dict() for n in notes]}

    async def tool_board(self, params: dict) -> dict:
        repo, who = _s(params, "repo", 200), _s(params, "executor", 64)
        status = _s(params, "status", 16).upper()
        limit = max(1, min(int(params.get("limit", 30) or 30), 100))
        rows = [it.summary() for it in await self.work.all()
                if (not repo or it.repo == repo) and (not who or it.executor == who)
                and (not status or it.status == status)]
        return {"items": rows[:limit], "total": len(rows)}

    async def tool_claim(self, params: dict) -> dict:
        now = self._clock()
        if _s(params, "item_id", 64):
            item = await self._item(params)
            if item.status in (DONE, FAILED):
                raise ToolError(f"{item.item_id} is already {item.status}")
            if item.status == RUNNING and item.executor != self.executor:
                raise ToolError(f"{item.item_id} is being worked on by {item.executor}")
            if item.status == QUEUED and item.executor not in ("", self.executor):
                raise ToolError(f"{item.item_id} was handed to {item.executor}")
        else:
            title = _s(params, "title", _MAX_TITLE)
            if not title:
                raise ToolError("give an item_id to take, or a title for new work")
            item = ExternalWorkItem(item_id=new_item_id(), title=title, created_ts=now,
                                    repo=self.repo, branch=self.branch)
        item.status, item.executor, item.session_id = RUNNING, self.executor, self.session_id
        item.repo = item.repo or self.repo
        item.branch = self.branch or item.branch
        item.add_progress(f"claimed by {self.executor}", now=now)
        return {"item": await self._save(item)}

    async def tool_progress(self, params: dict) -> dict:
        item = await self._item(params)
        self._mine(item)
        note = _s(params, "note", 500)
        if not note:
            raise ToolError("note is required")
        item.add_progress(note, now=self._clock())
        return {"item": await self._save(item)}

    async def tool_handoff(self, params: dict) -> dict:
        item = await self._item(params)
        self._mine(item)
        to, brief = _s(params, "to_executor", 64), _s(params, "brief")
        if not to or not brief:
            raise ToolError("to_executor and brief are required")
        item.status, item.handoff_from, item.executor, item.brief = QUEUED, self.executor, to, brief
        item.add_progress(f"handed to {to} by {self.executor}", now=self._clock())
        return {"item": await self._save(item)}

    async def tool_complete(self, params: dict) -> dict:
        item = await self._item(params)
        self._mine(item)
        outcome = _s(params, "outcome", 1000)
        if not outcome:
            raise ToolError("outcome is required")
        status = _s(params, "status", 16).upper() or DONE
        if status not in (DONE, FAILED):
            raise ToolError("status is DONE or FAILED")
        item.status, item.outcome = status, outcome
        item.pr = _s(params, "pr", 300) or item.pr
        item.diffstat = _s(params, "diffstat", 2000) or item.diffstat
        try:
            item.cost_usd = float(params.get("cost_usd", item.cost_usd) or 0.0)
            item.tokens = int(params.get("tokens", item.tokens) or 0)
        except (TypeError, ValueError) as exc:
            raise ToolError("cost_usd and tokens are numbers") from exc
        item.add_progress(f"{status.lower()}: {outcome.splitlines()[0][:200]}", now=self._clock())
        row = await self._save(item)
        await self._note(f"{item.title} — {status}: {outcome}" + (f" ({item.pr})" if item.pr else ""),
                         ["outcome", status.lower()], item_id=item.item_id)
        return {"item": row}

    async def tool_remember(self, params: dict) -> dict:
        text = _s(params, "text")
        if not text:
            raise ToolError("text is required")
        note = await self._note(text, _tags(params), item_id=_s(params, "item_id", 64))
        return {"note": note.to_dict()}

    async def tool_recall(self, params: dict) -> dict:
        limit = max(1, min(int(params.get("limit", 5) or 5), 20))
        found = await self.notes.search(_s(params, "query", 500), tags=_tags(params),
                                        repo=_s(params, "repo", 200), limit=limit)
        return {"notes": [n.to_dict() for n in found]}

    async def tool_ask(self, params: dict) -> dict:
        if self._ask is None:
            raise ToolError("asking the assistant needs ACC's bus; it is not connected")
        question, task_id = _s(params, "question"), _s(params, "task_id", 128)
        if not question and not task_id:
            raise ToolError("give a question, or the task_id of one asked before")
        try:
            wait = float(params.get("wait_s", 60) or 60)
        except (TypeError, ValueError) as exc:
            raise ToolError("wait_s is a number") from exc
        if question:
            question = f"[from {self.executor}, repo {self.repo or '?'}] {question}"
        return await self._ask(question, task_id, max(1.0, min(wait, 600.0)))


class _BusAsk:
    """``ask`` over the bus, through the same channel ``acc-cli ask`` uses.

    The observer is opened on the first question and kept for the session, so
    a second call with the returned task_id keeps waiting for the same answer."""

    def __init__(self, nats_url: str, collective_id: str) -> None:
        self._url, self._cid = nats_url, collective_id
        self._channel: Any = None
        self._observer: Any = None
        self._drain: asyncio.Task | None = None
        self._pending: dict[str, asyncio.Task] = {}

    async def _open(self) -> None:
        if self._channel is not None:
            return
        from acc.channels.tui import TUIPromptChannel  # noqa: PLC0415
        from acc.tui.client import NATSObserver  # noqa: PLC0415
        updates: asyncio.Queue = asyncio.Queue(maxsize=200)
        observer = NATSObserver(nats_url=self._url, collective_id=self._cid, update_queue=updates)
        await observer.connect()
        await observer.subscribe()

        async def drain() -> None:
            while True:
                await updates.get()

        self._drain = asyncio.create_task(drain())
        self._observer = observer
        self._channel = TUIPromptChannel(observer, collective_id=self._cid)

    #: How long one question may take before its answer is given up on.
    ANSWER_TIMEOUT_S = 1800.0

    async def __call__(self, question: str, task_id: str, wait_s: float) -> dict:
        await self._open()
        if question:
            task_id = await self._channel.send(prompt=question, target_role="assistant")
            # One receive per question for its whole life: a call that stops
            # waiting must not drop the listener the next call waits on.
            self._pending[task_id] = asyncio.create_task(
                self._channel.receive(task_id, timeout=self.ANSWER_TIMEOUT_S))
        pending = self._pending.get(task_id)
        if pending is None:
            raise ToolError(f"no question {task_id!r} asked in this session")
        try:
            reply = await asyncio.wait_for(asyncio.shield(pending), timeout=wait_s)
        except (TimeoutError, asyncio.TimeoutError):
            if not pending.done():
                return {"task_id": task_id, "status": "waiting",
                        "hint": "call ask again with this task_id to keep waiting"}
            self._pending.pop(task_id, None)
            raise ToolError(f"no answer to {task_id} within {self.ANSWER_TIMEOUT_S:.0f}s") from None
        self._pending.pop(task_id, None)
        return {"task_id": task_id, "status": "blocked" if reply.blocked else "answered",
                "answer": reply.output, "block_reason": getattr(reply, "block_reason", "")}

    async def close(self) -> None:
        for task in [*self._pending.values(), self._drain]:
            if task is not None:
                task.cancel()
        for obj in (self._channel, self._observer):
            if obj is not None:
                try:
                    await obj.close()
                except Exception:  # noqa: BLE001
                    pass


class McpServer:
    """MCP over stdio around a :class:`Coordinator`."""

    def __init__(self, coordinator: Coordinator, *, version: str = "") -> None:
        self.coordinator = coordinator
        self.version = version

    async def handle(self, message: Any) -> dict | None:
        """One JSON-RPC message in, the response out (``None`` for a notification)."""
        err, method, params, req_id = jsonrpc.parse_request(message)
        is_request = isinstance(message, dict) and "id" in message
        if err is not None:
            return jsonrpc.error(req_id, jsonrpc.INVALID_REQUEST, err) if is_request else None
        if not is_request:
            return None                    # notifications/initialized, cancelled, ...
        if method == "initialize":
            asked = str(params.get("protocolVersion", "") or "")
            return jsonrpc.success(req_id, {
                "protocolVersion": asked if asked in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0],
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "acc", "version": self.version},
                "instructions": INSTRUCTIONS,
            })
        if method == "ping":
            return jsonrpc.success(req_id, {})
        if method == "tools/list":
            return jsonrpc.success(req_id, {"tools": TOOLS})
        if method == "tools/call":
            name = str(params.get("name", "") or "")
            args = params.get("arguments") or {}
            if not isinstance(args, dict):
                return jsonrpc.error(req_id, jsonrpc.INVALID_PARAMS, "arguments must be an object")
            try:
                result = await self.coordinator.call(name, args)
            except KeyError:
                return jsonrpc.error(req_id, jsonrpc.INVALID_PARAMS, f"unknown tool {name!r}")
            except ToolError as exc:
                return jsonrpc.success(req_id, _content({"error": str(exc)}, is_error=True))
            except Exception as exc:  # noqa: BLE001 -- a tool fault is the agent's to read
                logger.exception("mcp server: tool %s failed", name)
                return jsonrpc.success(req_id, _content(
                    {"error": f"{type(exc).__name__}: {exc}"}, is_error=True))
            return jsonrpc.success(req_id, _content(result))
        return jsonrpc.error(req_id, jsonrpc.METHOD_NOT_FOUND, f"method {method!r} not supported")

    async def serve(self, reader: Callable[[], Awaitable[bytes]],
                    write: Callable[[str], None]) -> None:
        """Read lines until EOF, answer each request on one line."""
        while True:
            line = await reader()
            if not line:
                return
            text = line.decode("utf-8", "replace").strip() if isinstance(line, bytes) else line.strip()
            if not text:
                continue
            try:
                message = json.loads(text)
            except json.JSONDecodeError:
                write(json.dumps(jsonrpc.error(None, jsonrpc.PARSE_ERROR, "invalid JSON")))
                continue
            response = await self.handle(message)
            if response is not None:
                write(json.dumps(response, default=str))


def _content(result: dict, *, is_error: bool = False) -> dict:
    return {"content": [{"type": "text", "text": json.dumps(result, indent=1, default=str)}],
            "structuredContent": result, "isError": is_error}


async def run_stdio(*, executor: str, collective_id: str, nats_url: str, redis_url: str,
                    session_id: str = "") -> None:
    """Serve on this process's stdin/stdout until the client closes it."""
    import redis as redis_lib  # noqa: PLC0415

    from acc import __version__  # noqa: PLC0415

    redis_client = redis_lib.from_url(redis_url, password=os.environ.get("ACC_REDIS_PASSWORD") or None)
    try:
        redis_client.ping()
    except Exception as exc:  # noqa: BLE001
        # No silent in-process fallback: the work would vanish with the session.
        raise SystemExit(f"acc mcp serve: ACC's Redis at {redis_url} is not reachable ({exc}); "
                         "set ACC_REDIS_URL") from exc

    nc: Any = None

    async def publish(subject: str, payload: dict) -> None:
        nonlocal nc
        from acc.cli._common import encode_payload  # noqa: PLC0415
        if nc is None or not getattr(nc, "is_connected", False):
            import nats  # noqa: PLC0415
            nc = await nats.connect(nats_url, connect_timeout=3, max_reconnect_attempts=1)
        await nc.publish(subject, encode_payload(payload))

    cwd = os.getcwd()
    repo, branch = workspace_of(cwd)
    ask = _BusAsk(nats_url, collective_id)
    coordinator = Coordinator(
        executor=executor, collective_id=collective_id,
        work=ExternalWorkStore(redis_client, collective_id),
        notes=ExternalNoteStore(redis_client, collective_id),
        publish=publish, ask=ask, session_id=session_id, cwd=cwd, repo=repo, branch=branch,
    )
    server = McpServer(coordinator, version=__version__)
    loop = asyncio.get_running_loop()

    async def reader() -> bytes:
        return await loop.run_in_executor(None, sys.stdin.buffer.readline)

    def write(text: str) -> None:
        sys.stdout.write(text + "\n")
        sys.stdout.flush()

    logger.info("acc mcp serve: executor=%s collective=%s repo=%s session=%s",
                executor, collective_id, repo or "-", coordinator.session_id)
    try:
        await server.serve(reader, write)
    finally:
        await ask.close()
        if nc is not None:
            try:
                await nc.close()
            except Exception:  # noqa: BLE001
                pass


__all__ = ["Coordinator", "McpServer", "TOOLS", "ToolError", "run_stdio", "workspace_of"]
