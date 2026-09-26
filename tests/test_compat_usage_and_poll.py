"""F1 — the compat endpoint's remaining gaps: real usage, a poll that says what
it waits on, a doctor check, and a round trip with the unmodified client.

Usage was zero on every compat response until now, for three separate reasons:
the channel reply had no ``prompt_tokens``/``completion_tokens`` for the route
to read, ``completion_response`` read ``input_tokens``/``output_tokens`` while
the route passed the other names, and the one count ``TASK_COMPLETE`` did carry
was the agent's lifetime total. Each is pinned here, because fixing any two
still leaves the client billing on a wrong number.

Change: ``openspec/changes/20260829-openai-compat-server`` (F1 section).
"""

from __future__ import annotations

import asyncio
import hashlib
import socket
import threading
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

import acc.compat_endpoint as compat
from acc import preflight
from acc.agent import _summed_usage, _usage_field
from acc.channels.base import PromptResponse
from acc.channels.tui import _payload_to_response
from acc.cognitive_core import CognitiveCore, _add_usage
from acc.config import RoleDefinitionConfig
from acc.preflight import Context, Severity
from acc.webgui import routes_compat as rc

KEY = "test-key-f1-usage"
DIGEST = hashlib.sha256(KEY.encode()).hexdigest()


@pytest.fixture
def keyed_env(monkeypatch):
    monkeypatch.setenv(compat.KEYS_VAR, f"{DIGEST}:ci-tester")
    return {compat.KEYS_VAR: f"{DIGEST}:ci-tester"}


# ---------------------------------------------------------------------------
# The count, at the source
# ---------------------------------------------------------------------------


class TestAddUsage:
    def test_openai_shape(self):
        tally: dict = {}
        _add_usage(tally, {"usage": {"prompt_tokens": 7, "completion_tokens": 3}})
        assert tally == {"prompt_tokens": 7, "completion_tokens": 3, "cache_read_tokens": 0}

    def test_anthropic_shape(self):
        tally: dict = {}
        _add_usage(tally, {"usage": {"input_tokens": 9, "output_tokens": 2,
                                     "cache_read_input_tokens": 4}})
        assert tally == {"prompt_tokens": 9, "completion_tokens": 2, "cache_read_tokens": 4}

    def test_no_usage_leaves_the_tally_empty(self):
        """Empty means "not reported" -- the route turns it into null, not 0."""
        tally: dict = {}
        _add_usage(tally, {"content": "hi"})
        _add_usage(tally, {"content": "hi", "usage": {}})
        _add_usage(tally, "not a dict")
        assert tally == {}

    def test_calls_add_up(self):
        tally: dict = {}
        for _ in range(2):
            _add_usage(tally, {"usage": {"prompt_tokens": 5, "completion_tokens": 1}})
        assert tally["prompt_tokens"] == 10 and tally["completion_tokens"] == 2


def _role():
    return RoleDefinitionConfig.model_validate(
        {"purpose": "p", "persona": "concise", "version": "0.1.0",
         "memory_retrieval": False},
    )


def _core(llm):
    return CognitiveCore(
        agent_id="a", collective_id="c", llm=llm, vector=None,
        redis_client=None, role_label="analyst",
    )


class TestPerTaskUsage:
    @pytest.mark.asyncio
    async def test_each_task_reports_its_own_count_not_the_running_total(self):
        llm = MagicMock()
        llm.complete = AsyncMock(return_value={
            "content": "ok", "usage": {"prompt_tokens": 100, "completion_tokens": 10},
        })
        llm.embed = AsyncMock(return_value=[0.0] * 384)
        core = _core(llm)
        first = await core.process_task({"content": "one"}, role=_role())
        second = await core.process_task({"content": "two"}, role=_role())
        assert first.usage["prompt_tokens"] == 100
        assert second.usage["prompt_tokens"] == 100, "the second task billed for the first"
        assert second.usage["completion_tokens"] == 10

    @pytest.mark.asyncio
    async def test_concurrent_tasks_do_not_mix(self):
        """An agent runs tasks concurrently; a tally on ``self`` would mix them."""
        async def complete(system, user, **kw):
            n = 300 if "big" in user else 30
            await asyncio.sleep(0.05 if n == 300 else 0.01)
            return {"content": "ok", "usage": {"prompt_tokens": n, "completion_tokens": 1}}

        llm = MagicMock()
        llm.complete = complete
        llm.embed = AsyncMock(return_value=[0.0] * 384)
        core = _core(llm)
        big, small = await asyncio.gather(
            core.process_task({"content": "big task"}, role=_role()),
            core.process_task({"content": "small task"}, role=_role()),
        )
        assert big.usage["prompt_tokens"] == 300
        assert small.usage["prompt_tokens"] == 30

    @pytest.mark.asyncio
    async def test_a_backend_that_does_not_count_reports_nothing(self):
        llm = MagicMock()
        llm.complete = AsyncMock(return_value={"content": "ok"})
        llm.embed = AsyncMock(return_value=[0.0] * 384)
        result = await _core(llm).process_task({"content": "x"}, role=_role())
        assert result.usage == {}


class TestTaskComplete:
    def test_both_turns_are_summed(self):
        """The tool-result turn is a second model call the task paid for."""
        total = _summed_usage(
            {"prompt_tokens": 100, "completion_tokens": 20, "cache_read_tokens": 0},
            {"prompt_tokens": 150, "completion_tokens": 30, "cache_read_tokens": 5},
        )
        assert total == {"prompt_tokens": 250, "completion_tokens": 50, "cache_read_tokens": 5}

    def test_nothing_reported_sums_to_nothing(self):
        assert _summed_usage({}, None) == {}

    def test_the_field_is_absent_when_nothing_was_reported(self):
        assert _usage_field({}) == {}
        assert _usage_field(None) == {}

    def test_the_field_is_the_standard_triple(self):
        assert _usage_field({"prompt_tokens": 4, "completion_tokens": 2, "cache_read_tokens": 1}) == {
            "usage": {"prompt_tokens": 4, "completion_tokens": 2, "total_tokens": 6},
        }

    def test_the_follow_up_turn_is_summed_before_it_replaces_the_answer(self):
        """The sum has to happen before ``result = second``, or the first
        turn's cost is lost with the first answer."""
        import inspect

        import acc.agent as agent_mod

        src = inspect.getsource(agent_mod)
        assert src.count("result = second") == 1
        replaced = src.index("result = second")
        summed = src.rfind("second.usage = _summed_usage(", 0, replaced)
        assert summed != -1 and replaced - summed < 400


class TestChannelReply:
    def test_usage_rides_the_reply(self):
        reply = _payload_to_response("t1", {
            "output": "hi", "usage": {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4},
        })
        assert reply.usage == {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4}

    def test_an_older_agent_reads_as_none_not_zero(self):
        reply = _payload_to_response("t1", {"output": "hi", "input_tokens": 999})
        assert reply.usage is None


# ---------------------------------------------------------------------------
# The response
# ---------------------------------------------------------------------------


class TestCompletionUsage:
    def _req(self):
        return compat.parse_request({"model": "analyst", "messages": [{"role": "user", "content": "x"}]})

    def test_null_when_not_reported(self):
        assert compat.completion_response(self._req(), "r", usage=None)["usage"] is None
        assert compat.completion_response(self._req(), "r", usage={})["usage"] is None

    def test_standard_names_are_read(self):
        """Until F1 these were dropped: the route passed prompt_tokens, the
        builder read input_tokens."""
        body = compat.completion_response(
            self._req(), "r", usage={"prompt_tokens": 12, "completion_tokens": 3},
        )
        assert body["usage"] == {"prompt_tokens": 12, "completion_tokens": 3, "total_tokens": 15}

    def test_backend_names_are_read_too(self):
        body = compat.completion_response(
            self._req(), "r", usage={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
        )
        assert body["usage"] == {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}


class _FakeChannel:
    """Stands in for WebPromptChannel: the bus is not what is under test."""

    reply: PromptResponse | None = None

    def __init__(self, obs, **kw):
        pass

    async def send(self, **kw):
        return "task-f1"

    async def receive(self, task_id, timeout):
        return type(self).reply

    async def close(self):
        pass


class _Hub:
    def __init__(self, snapshot=None):
        self.snapshot = snapshot or {}

    def collective_ids(self):
        return ["sol-01"]

    def observer(self, cid):
        return object()

    def latest(self, cid):
        return self.snapshot


class TestDispatchReadsTheReply:
    def test_the_route_passes_the_agents_count_through(self, monkeypatch, keyed_env):
        import acc.channels.webgui as webgui_channel

        _FakeChannel.reply = PromptResponse(
            task_id="task-f1", agent_id="a", output="done",
            usage={"prompt_tokens": 21, "completion_tokens": 4, "total_tokens": 25},
        )
        monkeypatch.setattr(webgui_channel, "WebPromptChannel", _FakeChannel)

        async def run():
            dispatch = await rc._make_dispatch("sol-01", _Hub())
            req = compat.parse_request({"model": "analyst", "messages": [{"role": "user", "content": "x"}]})
            caller = compat.authenticate(KEY)
            return await dispatch(req, caller, caller.attribution())

        output, usage = asyncio.run(run())
        assert output == "done"
        assert usage == {"prompt_tokens": 21, "completion_tokens": 4, "total_tokens": 25}


# ---------------------------------------------------------------------------
# The poll route
# ---------------------------------------------------------------------------


def _client(monkeypatch, hub):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.include_router(rc.router)
    monkeypatch.setattr(rc, "get_hub", lambda request: hub)
    return TestClient(app)


class TestPollSaysWhatItWaitsOn:
    def _put(self, task_id="task-p1", status="running"):
        rc._pending.put(task_id, {
            "status": status, "oversight_id": "",
            "subject": "compat:ci-tester", "model": "analyst",
        })

    def test_a_held_task_names_its_oversight_row(self, monkeypatch, keyed_env):
        self._put()
        hub = _Hub({"oversight_pending_items": [
            {"oversight_id": "ov-other", "task_id": "someone-else", "summary": "no", "risk_level": "LOW"},
            {"oversight_id": "ov-1", "task_id": "task-p1", "summary": "shell_exec — ls",
             "risk_level": "MEDIUM", "timeout_ms": 1_790_000_000_000},
        ]})
        body = _client(monkeypatch, hub).get(
            "/v1/tasks/task-p1", headers={"Authorization": f"Bearer {KEY}"},
        ).json()
        assert body["status"] == "awaiting_approval"
        assert body["oversight_id"] == "ov-1"
        assert body["waiting_on"] == {"summary": "shell_exec — ls", "risk_level": "MEDIUM",
                                      "decide_by": 1_790_000_000}

    def test_no_deadline_is_omitted_not_invented(self, monkeypatch, keyed_env):
        """An arbiter heartbeat without the panel's fields has no timeout_ms."""
        self._put()
        hub = _Hub({"oversight_pending_items": [
            {"oversight_id": "ov-1", "task_id": "task-p1", "summary": "s", "risk_level": "LOW"},
        ]})
        body = _client(monkeypatch, hub).get(
            "/v1/tasks/task-p1", headers={"Authorization": f"Bearer {KEY}"},
        ).json()
        assert "decide_by" not in body["waiting_on"]

    def test_running_stays_running_when_no_row_holds_it(self, monkeypatch, keyed_env):
        self._put()
        body = _client(monkeypatch, _Hub()).get(
            "/v1/tasks/task-p1", headers={"Authorization": f"Bearer {KEY}"},
        ).json()
        assert body["status"] == "running"
        assert "waiting_on" not in body

    def test_the_handle_says_when_it_expires(self, monkeypatch, keyed_env):
        self._put()
        before = time.time()
        body = _client(monkeypatch, _Hub()).get(
            "/v1/tasks/task-p1", headers={"Authorization": f"Bearer {KEY}"},
        ).json()
        assert before + rc.PENDING_TTL_S - 5 <= body["expires_at"] <= time.time() + rc.PENDING_TTL_S

    def test_a_finished_task_is_not_relabelled(self, monkeypatch, keyed_env):
        """A stale heartbeat row must not turn a completed task back into a wait."""
        self._put(task_id="task-done", status="completed")
        hub = _Hub({"oversight_pending_items": [
            {"oversight_id": "ov-1", "task_id": "task-done", "summary": "s", "risk_level": "LOW"},
        ]})
        body = _client(monkeypatch, hub).get(
            "/v1/tasks/task-done", headers={"Authorization": f"Bearer {KEY}"},
        ).json()
        assert body["status"] == "completed"

    def test_another_principals_task_is_still_404(self, monkeypatch, keyed_env):
        rc._pending.put("task-theirs", {"status": "running", "subject": "compat:someone-else"})
        resp = _client(monkeypatch, _Hub()).get(
            "/v1/tasks/task-theirs", headers={"Authorization": f"Bearer {KEY}"},
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# doctor
# ---------------------------------------------------------------------------


class TestDoctorCheck:
    def _run(self, environ):
        return [r for r in preflight.run(Context(environ=environ), only="compat")]

    def test_off(self):
        (r,) = self._run({})
        assert r.severity is Severity.OK and "off" in r.summary

    def test_on_names_subjects_never_digests(self):
        (r,) = self._run({compat.KEYS_VAR: f"{DIGEST}:reporting-bot"})
        assert r.severity is Severity.OK
        assert "reporting-bot" in r.summary and "1 key" in r.summary
        assert DIGEST not in r.summary + r.detail

    def test_a_raw_key_where_a_digest_belongs_is_broken_and_not_echoed(self):
        raw = "the-key-itself-not-its-digest"
        results = self._run({compat.KEYS_VAR: f"{raw}:oops,{DIGEST}:fine"})
        broken = [r for r in results if r.severity is Severity.BROKEN]
        assert [r.subject for r in broken] == ["oops"]
        assert all(raw not in (r.summary + r.detail) for r in results)
        assert any(r.severity is Severity.OK and "fine" in r.summary for r in results)

    def test_an_uppercase_digest_can_never_match_and_says_so(self):
        """``authenticate`` compares against hexdigest(), which is lowercase."""
        results = self._run({compat.KEYS_VAR: f"{DIGEST.upper()}:shouty"})
        assert [r.severity for r in results] == [Severity.BROKEN]

    def test_set_but_unparseable_is_broken(self):
        (r,) = self._run({compat.KEYS_VAR: "no-colon-here"})
        assert r.severity is Severity.BROKEN


# ---------------------------------------------------------------------------
# HG-24's actual claim: an unmodified client completes a round trip
# ---------------------------------------------------------------------------


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def served(monkeypatch, keyed_env):
    """The router on a real socket, so the client uses its own HTTP stack."""
    uvicorn = pytest.importorskip("uvicorn")
    import acc.channels.webgui as webgui_channel
    from fastapi import FastAPI

    monkeypatch.setattr(webgui_channel, "WebPromptChannel", _FakeChannel)
    monkeypatch.setattr(rc, "get_hub", lambda request: _Hub())
    app = FastAPI()
    app.include_router(rc.router)
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 10
    while not server.started and time.time() < deadline:
        time.sleep(0.02)
    assert server.started, "test server did not start"
    yield f"http://127.0.0.1:{port}/v1"
    server.should_exit = True
    thread.join(timeout=10)


class TestUnmodifiedClient:
    def test_a_completion_with_usage(self, served):
        openai = pytest.importorskip("openai")
        _FakeChannel.reply = PromptResponse(
            task_id="task-f1", agent_id="a", output="the answer",
            usage={"prompt_tokens": 40, "completion_tokens": 8, "total_tokens": 48},
        )
        client = openai.OpenAI(base_url=served, api_key=KEY, max_retries=0)
        reply = client.chat.completions.create(
            model="analyst", messages=[{"role": "user", "content": "hello"}],
        )
        assert reply.choices[0].message.content == "the answer"
        assert reply.usage.prompt_tokens == 40
        assert reply.usage.completion_tokens == 8
        assert reply.usage.total_tokens == 48

    def test_unreported_usage_arrives_as_none(self, served):
        openai = pytest.importorskip("openai")
        _FakeChannel.reply = PromptResponse(task_id="task-f1", agent_id="a", output="ok")
        client = openai.OpenAI(base_url=served, api_key=KEY, max_retries=0)
        reply = client.chat.completions.create(
            model="analyst", messages=[{"role": "user", "content": "hello"}],
        )
        assert reply.usage is None

    def test_a_wrong_key_is_the_clients_own_auth_error(self, served):
        openai = pytest.importorskip("openai")
        client = openai.OpenAI(base_url=served, api_key="wrong", max_retries=0)
        with pytest.raises(openai.AuthenticationError):
            client.chat.completions.create(
                model="analyst", messages=[{"role": "user", "content": "hello"}],
            )
