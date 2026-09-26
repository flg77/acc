"""An attached image reaches the model, or the turn is refused -- never dropped.

`20260830-attachment-delivery-path`. ``acc.attachments`` was a finished
component with no socket: it stored an upload and built provider blocks, and
nothing downstream could carry them. These tests pin the socket, and above all
the one property that would be hardest to notice if it regressed: a backend
that cannot carry an image **refuses** the turn. A backend that ignored the
blocks would produce a confident answer about a picture the model never saw.

Also pinned: the image store now lives under the session retention policy, the
removal is journaled first, and a surface that does not share the store with
the agents is told so rather than blamed on retention.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import struct
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from acc import attachments as A
from acc import sessions as S
from acc import tracelog
from acc.backends import ContentNotSupported, LLMCallError

PNG = (
    b"\x89PNG\r\n\x1a\n"
    + b"\x00\x00\x00\rIHDR"
    + struct.pack(">II", 120, 80)
    + b"\x08\x06\x00\x00\x00"
    + b"\x00" * 32
)
BLOCK = {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "AAAA"}}


@pytest.fixture
def store(tmp_path, monkeypatch):
    """One directory for the tracelog, the image store and the journal -- the
    layout of a deployment where the web GUI and the agents share /logs."""
    monkeypatch.delenv(A.STORE_VAR, raising=False)
    monkeypatch.setenv("ACC_TRACELOG_DIR", str(tmp_path))
    monkeypatch.setenv("ACC_TRACELOG", "1")
    monkeypatch.setenv(S.RETENTION_PATH_VAR, str(tmp_path / "retention.yaml"))
    return tmp_path


# ---------------------------------------------------------------------------
# Phase 1 -- each backend either carries the blocks or refuses
# ---------------------------------------------------------------------------


class TestTextOnlyBackendsRefuse:
    @pytest.mark.parametrize("make", [
        lambda: __import__("acc.backends.llm_ollama", fromlist=["x"]).OllamaBackend("http://unused", "m"),
        lambda: __import__("acc.backends.llm_vllm", fromlist=["x"]).VLLMBackend("http://unused", "m"),
        lambda: __import__("acc.backends.llm_llama_stack", fromlist=["x"]).LlamaStackBackend("http://unused", "/x"),
    ], ids=["ollama", "vllm", "llama_stack"])
    def test_refuses_before_any_request(self, make):
        backend = make()
        with pytest.raises(ContentNotSupported) as info:
            asyncio.run(backend.complete("sys", "look at this", content=[BLOCK]))
        assert "cannot accept images" in str(info.value)
        assert info.value.retryable is False, "a failover chain must stop, not walk on"


class TestAnthropicCarriesThem:
    def _backend(self, monkeypatch):
        from acc.backends.llm_anthropic import AnthropicBackend

        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-not-a-key")
        backend = AnthropicBackend("claude-x", "/unused")
        message = MagicMock()
        message.content = [MagicMock(text="a cat")]
        message.usage = MagicMock(input_tokens=5, output_tokens=2,
                                  cache_creation_input_tokens=0, cache_read_input_tokens=0)
        backend._client = MagicMock()
        backend._client.messages.create = AsyncMock(return_value=message)
        return backend

    def test_text_then_images(self, monkeypatch):
        backend = self._backend(monkeypatch)
        asyncio.run(backend.complete("sys", "what is this?", content=[BLOCK]))
        (msg,) = backend._client.messages.create.call_args.kwargs["messages"]
        assert msg["content"] == [{"type": "text", "text": "what is this?"}, BLOCK]

    def test_no_content_is_the_plain_string_as_before(self, monkeypatch):
        backend = self._backend(monkeypatch)
        asyncio.run(backend.complete("sys", "hello"))
        (msg,) = backend._client.messages.create.call_args.kwargs["messages"]
        assert msg["content"] == "hello"


class TestOpenAICompatCarriesThem:
    def test_images_become_data_urls(self):
        from acc.backends.llm_openai_compat import _user_parts

        parts = _user_parts("what is this?", [BLOCK])
        assert parts[0] == {"type": "text", "text": "what is this?"}
        assert parts[1] == {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}

    def test_no_content_is_the_plain_string(self):
        from acc.backends.llm_openai_compat import _user_parts

        assert _user_parts("hello", None) == "hello"

    def test_the_request_carries_the_parts(self, monkeypatch):
        import httpx

        from acc.backends.llm_openai_compat import OpenAICompatBackend

        sent = {}

        class _Client:
            def __init__(self, *a, **k):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def post(self, url, headers=None, json=None):
                sent.update(json)
                return httpx.Response(200, json={
                    "choices": [{"message": {"content": "a cat"}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 9, "completion_tokens": 2},
                })

        monkeypatch.setattr(httpx, "AsyncClient", _Client)
        # F2b -- only a model declared to take images gets them.
        backend = OpenAICompatBackend("http://unused/v1", "m", accepts_images=True)
        asyncio.run(backend.complete("sys", "what is this?", content=[BLOCK]))
        user = sent["messages"][1]["content"]
        assert user[1]["type"] == "image_url"


class TestFailover:
    def _chain(self, *clients):
        from acc.llm_failover import AllowAllGate, FailoverBackend
        from acc.models import ModelEntry

        by_id = {c.name: c for c in clients}
        return FailoverBackend(
            role="assistant",
            entries=[ModelEntry(model_id=c.name, backend="ollama", model="m") for c in clients],
            build=lambda e: by_id[e.model_id],
            gate=AllowAllGate(),
        )

    class _Legacy:
        """A chain entry that predates ``content``."""

        def __init__(self, name):
            self.name = name
            self.seen = []

        async def complete(self, system, user, response_schema=None, cache_prefix=False):
            self.seen.append(user)
            return {"content": "ok"}

    def test_no_content_calls_an_old_entry_exactly_as_before(self):
        legacy = self._Legacy("a")
        asyncio.run(self._chain(legacy).complete("s", "u"))
        assert legacy.seen == ["u"]

    def test_content_is_forwarded(self):
        seen = {}

        class _MM:
            name = "mm"

            async def complete(self, system, user, response_schema=None, cache_prefix=False, *, content=None):
                seen["content"] = content
                return {"content": "ok"}

        asyncio.run(self._chain(_MM()).complete("s", "u", content=[BLOCK]))
        assert seen["content"] == [BLOCK]

    def test_a_text_only_fallback_stops_the_chain(self):
        class _Down:
            name = "primary"

            async def complete(self, *a, **k):
                raise LLMCallError("HTTP 503", retryable=True, status_code=503)

        class _TextOnly:
            name = "fallback"

            async def complete(self, system, user, response_schema=None, cache_prefix=False, *, content=None):
                from acc.backends import refuse_content

                refuse_content("ollama", content)
                return {"content": "an answer without the picture"}

        with pytest.raises(ContentNotSupported):
            asyncio.run(self._chain(_Down(), _TextOnly()).complete("s", "u", content=[BLOCK]))


# ---------------------------------------------------------------------------
# Resolving a reference
# ---------------------------------------------------------------------------


class TestResolve:
    def test_a_stored_image_resolves_from_its_bytes(self, store):
        ref = A.accept(PNG).sha256
        (att,) = A.resolve([ref])
        assert att.media_type == "image/png" and (att.width, att.height) == (120, 80)
        (block,) = A.image_blocks([att])
        assert block["source"]["media_type"] == "image/png"

    def test_not_a_reference(self, store):
        with pytest.raises(A.AttachmentError, match="not an attachment reference"):
            A.resolve(["../../etc/passwd"])

    def test_bytes_that_no_longer_match_their_name(self, store):
        ref = A.accept(PNG).sha256
        (A.store_dir() / ref).write_bytes(PNG + b"tampered")
        with pytest.raises(A.AttachmentError, match="does not match its digest"):
            A.resolve([ref])

    def test_never_in_this_store_says_the_store_is_not_shared(self, store):
        """Separate pods, or a web GUI without the /logs mount -- not retention."""
        ref = hashlib.sha256(b"uploaded somewhere else").hexdigest()
        with pytest.raises(A.AttachmentError, match="must share that store") as info:
            A.resolve([ref])
        assert "retention" not in str(info.value)

    def test_removed_by_retention_says_so(self, store):
        ref = A.accept(PNG).sha256
        (A.store_dir() / ref).unlink()
        S._append_journal({"kind": "attachment_removed", "sha256": ref}, root=store)
        with pytest.raises(A.AttachmentError, match="retention"):
            A.resolve([ref])


# ---------------------------------------------------------------------------
# The core: blocks reach the backend, or the turn is refused
# ---------------------------------------------------------------------------


def _role():
    from acc.config import RoleDefinitionConfig

    return RoleDefinitionConfig.model_validate(
        {"purpose": "p", "persona": "concise", "version": "0.1.0", "memory_retrieval": False},
    )


def _core(llm):
    from acc.cognitive_core import CognitiveCore

    llm.embed = AsyncMock(return_value=[0.0] * 384)
    return CognitiveCore(agent_id="a", collective_id="c", llm=llm, vector=None,
                         redis_client=None, role_label="analyst")


class TestCore:
    def test_the_blocks_reach_the_backend(self, store):
        ref = A.accept(PNG).sha256
        seen = {}

        class _MM:
            async def complete(self, system, user, response_schema=None, cache_prefix=False, *, content=None):
                seen["content"] = content
                return {"content": "a small png"}

        result = asyncio.run(_core(_MM()).process_task(
            {"content": "what is this?", "attachments": [ref]}, role=_role(),
        ))
        assert not result.blocked
        (block,) = seen["content"]
        assert block["type"] == "image" and block["source"]["media_type"] == "image/png"

    def test_no_attachments_calls_the_backend_as_before(self, store):
        llm = MagicMock()
        llm.complete = AsyncMock(return_value={"content": "ok"})
        asyncio.run(_core(llm).process_task({"content": "hi"}, role=_role()))
        assert "content" not in llm.complete.call_args.kwargs

    def test_a_backend_that_does_not_know_content_is_refused_not_retried_without(self, store):
        """The legacy TypeError fallback must not become the silent drop."""
        ref = A.accept(PNG).sha256
        calls = []

        class _Old:
            async def complete(self, system, user, response_schema=None, cache_prefix=False):
                calls.append(user)
                return {"content": "an answer without the picture"}

        result = asyncio.run(_core(_Old()).process_task(
            {"content": "what is this?", "attachments": [ref]}, role=_role(),
        ))
        assert result.blocked and "cannot accept images" in result.block_reason
        assert calls == [], "the prompt went out without its image"

    def test_a_text_only_backend_blocks_the_turn(self, store):
        ref = A.accept(PNG).sha256
        llm = MagicMock()
        llm.complete = AsyncMock(side_effect=ContentNotSupported("ollama"))
        result = asyncio.run(_core(llm).process_task(
            {"content": "what is this?", "attachments": [ref]}, role=_role(),
        ))
        assert result.blocked and result.block_reason.startswith("attachment:")

    def test_an_unresolvable_reference_never_reaches_the_model(self, store):
        llm = MagicMock()
        llm.complete = AsyncMock(return_value={"content": "ok"})
        result = asyncio.run(_core(llm).process_task(
            {"content": "what is this?", "attachments": ["0" * 64]}, role=_role(),
        ))
        assert result.blocked and "attachment" in result.block_reason
        assert not llm.complete.called


# ---------------------------------------------------------------------------
# The path in: route -> channel -> TASK_ASSIGN
# ---------------------------------------------------------------------------


class _Obs:
    def __init__(self):
        self.published = []

    def register_task_listener(self, *a):
        pass

    def register_task_progress_listener(self, *a):
        pass

    def unregister_task_listener(self, *a):
        pass

    def unregister_task_progress_listener(self, *a):
        pass

    async def publish(self, subject, payload):
        self.published.append(payload)


class TestChannel:
    def test_references_ride_the_task_assign(self):
        from acc.channels.tui import TUIPromptChannel

        obs = _Obs()
        ref = "a" * 64

        async def run():
            ch = TUIPromptChannel(obs, collective_id="sol-01")
            await ch.send("look", target_role="analyst", attachments=[ref])

        asyncio.run(run())
        assert obs.published[0]["attachments"] == [ref]

    def test_absent_when_there_are_none(self):
        from acc.channels.tui import TUIPromptChannel

        obs = _Obs()

        async def run():
            await TUIPromptChannel(obs, collective_id="sol-01").send("hi", target_role="analyst")

        asyncio.run(run())
        assert "attachments" not in obs.published[0]


class TestWebRoute:
    def _client(self, monkeypatch, sent):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        import acc.channels.webgui as webgui_channel
        from acc.channels.base import PromptResponse
        from acc.webgui import routes_action
        from acc.webgui.auth import Principal, require_operator
        from acc.webgui.deps import get_hub

        class _Ch:
            def __init__(self, *a, **k):
                pass

            async def send(self, **kw):
                sent.update(kw)
                return "t1"

            async def receive(self, task_id, timeout):
                return PromptResponse(task_id="t1", agent_id="a", output="ok")

            async def close(self):
                pass

        monkeypatch.setattr(webgui_channel, "WebPromptChannel", _Ch)
        monkeypatch.setattr(routes_action, "_require_observer", lambda hub, cid: object())
        app = FastAPI()
        app.include_router(routes_action.router)
        app.dependency_overrides[require_operator] = lambda: Principal(user="alice", role="operator")
        app.dependency_overrides[get_hub] = lambda: object()
        return TestClient(app)

    def test_a_stored_reference_is_passed_on(self, store, monkeypatch):
        ref = A.accept(PNG).sha256
        sent: dict = {}
        resp = self._client(monkeypatch, sent).post("/api/prompt", json={
            "collective_id": "sol-01", "target_role": "analyst", "content": "look",
            "attachments": [ref],
        })
        assert resp.status_code == 200
        assert sent["attachments"] == [ref]

    def test_an_unknown_reference_is_a_400_now_not_a_refusal_later(self, store, monkeypatch):
        sent: dict = {}
        resp = self._client(monkeypatch, sent).post("/api/prompt", json={
            "collective_id": "sol-01", "target_role": "analyst", "content": "look",
            "attachments": ["f" * 64],
        })
        assert resp.status_code == 400 and "upload it first" in resp.text
        assert sent == {}, "nothing was sent"


# ---------------------------------------------------------------------------
# Phase 0 -- retention
# ---------------------------------------------------------------------------


def _session_naming(root, session_id, refs, *, age_days=0.0):
    tracelog.emit(session_id, "session_start", root=root)
    tracelog.emit(session_id, "prompt_in", root=root, task_id="t1", role="analyst",
                  prompt="look", attachments=refs)
    tracelog.emit(session_id, "reply_out", root=root, task_id="t1", role="analyst", reply="ok")
    if age_days:
        path = tracelog.session_path(session_id, root=root)
        old = time.time() - age_days * 86400
        lines = []
        for line in path.read_text(encoding="utf-8").splitlines():
            rec = json.loads(line)
            rec["ts"] = old
            lines.append(json.dumps(rec))
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        os.utime(path, (old, old))


def _age(path, days):
    old = time.time() - days * 86400
    os.utime(path, (old, old))


def _policy(root, days):
    (root / "retention.yaml").write_text(f"retention:\n  keep_days: {days}\n", encoding="utf-8")


class TestRetention:
    def _orphan(self, payload, days):
        ref = A.accept(PNG + payload).sha256 if payload else A.accept(PNG).sha256
        _age(A.store_dir() / ref, days)
        return ref

    def test_keep_forever_keeps_every_image(self, store):
        ref = self._orphan(b"", 400)
        assert S.apply_retention(root=store) == []
        assert (A.store_dir(store) / ref).is_file()

    def test_an_old_orphan_goes_and_is_journaled(self, store):
        _policy(store, 30)
        ref = self._orphan(b"", 60)
        removed = S.apply_retention(root=store)
        assert removed == [{"attachment": ref, "sha256": ref}]
        assert not (A.store_dir(store) / ref).exists()
        (entry,) = [e for e in S.removals(root=store) if e["kind"] == "attachment_removed"]
        assert entry["sha256"] == ref and entry["policy"]["keep_days"] == 30

    def test_an_image_a_surviving_session_names_is_kept_whatever_its_age(self, store):
        _policy(store, 30)
        ref = self._orphan(b"", 60)
        _session_naming(store, "recent", [ref])
        assert S.apply_retention(root=store) == []
        assert (A.store_dir(store) / ref).is_file()

    def test_the_image_goes_with_the_last_session_naming_it(self, store):
        _policy(store, 30)
        ref = self._orphan(b"", 60)
        _session_naming(store, "old", [ref], age_days=90)
        removed = S.apply_retention(root=store)
        assert {"session_id"} <= set(removed[0]) and removed[-1]["attachment"] == ref

    def test_a_young_orphan_is_kept(self, store):
        """An upload not yet sent must not vanish between attach and send."""
        _policy(store, 30)
        ref = self._orphan(b"", 1)
        assert S.apply_retention(root=store) == []
        assert (A.store_dir(store) / ref).is_file()

    def test_dry_run_removes_nothing(self, store):
        _policy(store, 30)
        ref = self._orphan(b"", 60)
        assert S.apply_retention(root=store, dry_run=True) == [{"attachment": ref, "sha256": ref}]
        assert (A.store_dir(store) / ref).is_file()
        assert S.removals(root=store) == []


class TestDoctor:
    def test_reports_count_and_governance(self, store):
        from acc import preflight
        from acc.preflight import Context, Severity

        A.accept(PNG)
        (r,) = preflight.run(Context(environ={}), only="attachments")
        assert r.severity is Severity.OK
        assert "1 stored image" in r.summary and "kept forever" in r.summary

    def test_an_empty_store(self, store):
        from acc import preflight
        from acc.preflight import Context

        (r,) = preflight.run(Context(environ={}), only="attachments")
        assert r.summary == "no stored images"


# ---------------------------------------------------------------------------
# Phase 4 -- the TUI shows that a turn had an image
# ---------------------------------------------------------------------------


class TestTUI:
    def test_the_signal_log_carries_short_references(self):
        from acc.tui.client import _attachment_refs

        assert _attachment_refs({"attachments": ["a" * 64, "b" * 64]}) == {
            "attachments": ["a" * 12, "b" * 12],
        }
        assert _attachment_refs({}) == {}

    def test_the_prompt_in_record_names_them(self, store):
        """What retention reads back is what the agent wrote."""
        from types import SimpleNamespace

        from acc.agent import Agent

        ref = "c" * 64
        fake = SimpleNamespace(config=SimpleNamespace(agent=SimpleNamespace(role="analyst")),
                               _active_role=None, _agent_id="a1")
        Agent._tracelog_prompt_in(fake, {"task_id": "t9", "session_id": "s9", "content": "look",
                                         "attachments": [ref]}, "sol-01")
        (rec,) = [r for r in tracelog.load_session("s9") if r["kind"] == "prompt_in"]
        assert rec["attachments"] == [ref]
        assert S.referenced_attachments(root=store) == {ref}

    def test_a_turn_without_images_writes_no_field(self, store):
        from types import SimpleNamespace

        from acc.agent import Agent

        fake = SimpleNamespace(config=SimpleNamespace(agent=SimpleNamespace(role="analyst")),
                               _active_role=None, _agent_id="a1")
        Agent._tracelog_prompt_in(fake, {"task_id": "t8", "session_id": "s8", "content": "hi"}, "sol-01")
        (rec,) = [r for r in tracelog.load_session("s8") if r["kind"] == "prompt_in"]
        assert "attachments" not in rec
