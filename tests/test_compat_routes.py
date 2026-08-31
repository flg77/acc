"""The compat endpoint as it is actually served (HG-24).

`test_compat_endpoint.py` covers the module's decisions in isolation. This
covers the seam: that the router mounts only when configured, that the ordering
`handle_async` promises holds on the async path too, and that the poll route the
202 body advertises actually answers.

The governance test is the one that matters. `handle()` and `handle_async()`
carry the same ordering in two places, and if they drift, one of them becomes a
governance bypass rather than a bug — so the invariant is asserted against both.

Change: ``openspec/changes/20260829-openai-compat-server``.
"""

from __future__ import annotations

import asyncio
import hashlib

import pytest

import acc.compat_endpoint as compat
from acc.webgui import routes_compat as rc

KEY = "test-key-abc123"
DIGEST = hashlib.sha256(KEY.encode()).hexdigest()


@pytest.fixture
def keyed_env(monkeypatch):
    monkeypatch.setenv(compat.KEYS_VAR, f"{DIGEST}:ci-tester")
    return {compat.KEYS_VAR: f"{DIGEST}:ci-tester"}


def _body(model: str = "analyst", prompt: str = "hello") -> dict:
    return {"model": model, "messages": [{"role": "user", "content": prompt}]}


# ---------------------------------------------------------------------------
# Mounting — off unless configured
# ---------------------------------------------------------------------------


class TestEnablement:
    def test_disabled_without_keys(self):
        """Absent keys must mean the router is never mounted.

        Not 401. A 401 confirms ACC is listening here, which is a disclosure on
        the surface most likely to be probed by something nobody authorised.
        """
        assert rc.is_enabled({}) is False

    def test_enabled_with_a_key(self, keyed_env):
        assert rc.is_enabled(keyed_env) is True

    def test_malformed_key_spec_does_not_enable(self):
        """A typo'd env value must not half-open the endpoint."""
        assert rc.is_enabled({compat.KEYS_VAR: "no-colon-here"}) is False
        assert rc.is_enabled({compat.KEYS_VAR: ""}) is False


# ---------------------------------------------------------------------------
# The ordering invariant, on BOTH paths
# ---------------------------------------------------------------------------


class TestGatedRequestNeverDispatches:
    """The whole point of `handle`: nothing reaches dispatch ungated.

    Asserted on the mock rather than the response, because a 202 body proves
    only what was returned, not what was run.
    """

    def test_sync_path(self, keyed_env):
        called = []

        def dispatch(*a, **k):
            called.append(a)
            return "should not happen", {}

        result = compat.handle(
            _body(), KEY, environ=keyed_env,
            gate=lambda req, caller: "oversight-123",
            dispatch=dispatch,
        )
        assert result.status == 202
        assert result.dispatched is False
        assert called == [], "gated request reached dispatch"

    def test_async_path(self, keyed_env):
        called = []

        async def gate(req, caller):
            return "oversight-123"

        async def dispatch(*a, **k):
            called.append(a)
            return "should not happen", {}

        result = asyncio.run(compat.handle_async(
            _body(), KEY, environ=keyed_env, gate=gate, dispatch=dispatch,
        ))
        assert result.status == 202
        assert result.dispatched is False
        assert called == [], "gated request reached dispatch on the async path"

    def test_ungated_request_does_dispatch(self, keyed_env):
        async def gate(req, caller):
            return ""

        async def dispatch(req, caller, attribution):
            return "the answer", {"prompt_tokens": 3, "completion_tokens": 2,
                                  "total_tokens": 5}

        result = asyncio.run(compat.handle_async(
            _body(), KEY, environ=keyed_env, gate=gate, dispatch=dispatch,
        ))
        assert result.status == 200
        assert result.dispatched is True
        assert result.body["choices"][0]["message"]["content"] == "the answer"
        assert result.body["usage"]["total_tokens"] == 5


class TestAuthOnTheAsyncPath:
    def test_unauthenticated_is_refused_before_parsing(self, keyed_env):
        """A bad key must not even reveal whether the body was valid."""
        result = asyncio.run(compat.handle_async(
            {"garbage": True}, "wrong-key", environ=keyed_env,
        ))
        assert result.status == 401

    def test_no_dispatcher_is_a_server_error_not_a_silent_success(self, keyed_env):
        async def gate(req, caller):
            return ""

        with pytest.raises(compat.CompatError) as exc:
            asyncio.run(compat.handle_async(
                _body(), KEY, environ=keyed_env, gate=gate, dispatch=None,
            ))
        assert exc.value.status == 503


# ---------------------------------------------------------------------------
# What this endpoint refuses, and why
# ---------------------------------------------------------------------------


class TestHighRiskIsRefusedNotHandled:
    """ACC has no pre-execution oversight gate on a completion.

    It gates actions -- capabilities, assistant proposals -- while an ordinary
    prompt is risk-classified *after* the fact for the audit record. So a 202
    would point at an oversight item nobody created. Refusing is the honest
    half of the pair HG-24 offered.
    """

    def test_arbiter_is_refused(self, keyed_env):
        from acc.compliance.eu_ai_act import EUAIActClassifier

        classifier = EUAIActClassifier()
        risk = classifier.classify("arbiter", "TASK_ASSIGN")
        assert classifier.is_high_or_above(risk), (
            "test assumes arbiter is HIGH+; if the risk table changed, this "
            "test needs a different role, not deleting"
        )

        gate = asyncio.run(rc._make_gate("sol-01", object()))
        with pytest.raises(compat.CompatError) as exc:
            asyncio.run(gate(compat.ChatRequest(role="arbiter", prompt="x"),
                             compat.authenticate(KEY, environ=keyed_env)))
        assert exc.value.status == 403
        assert "was not run" in str(exc.value)

    def test_low_risk_role_passes_the_gate(self, keyed_env):
        gate = asyncio.run(rc._make_gate("sol-01", object()))
        result = asyncio.run(gate(
            compat.ChatRequest(role="ingester", prompt="x"),
            compat.authenticate(KEY, environ=keyed_env),
        ))
        assert result == "", "a MINIMAL-risk role must not be gated"


class TestStreamingIsRefusedExplicitly:
    def test_stream_true_is_a_clear_error(self):
        """Many clients set stream by default; silence would read as a hang."""
        with pytest.raises(compat.CompatError) as exc:
            compat.parse_request({**_body(), "stream": True})
        assert exc.value.status == 400
        assert "stream=false" in str(exc.value)


# ---------------------------------------------------------------------------
# The poll route's store
# ---------------------------------------------------------------------------


class TestPendingStore:
    def test_roundtrip(self):
        store = rc.PendingStore()
        store.put("t1", {"status": "awaiting_approval", "subject": "compat:a"})
        assert store.get("t1")["status"] == "awaiting_approval"

    def test_unknown_task_is_none(self):
        assert rc.PendingStore().get("nope") is None

    def test_expired_task_is_dropped(self, monkeypatch):
        """A task nobody actions must stop claiming to be pending.

        Polling `awaiting_approval` forever is indistinguishable from a queue
        that is merely slow, which is the failure this TTL exists to prevent.
        """
        store = rc.PendingStore()
        store.put("t1", {"status": "awaiting_approval", "subject": "compat:a"})
        store._items["t1"]["created"] -= rc.PENDING_TTL_S + 1
        assert store.get("t1") is None

    def test_resolve_updates_in_place(self):
        store = rc.PendingStore()
        store.put("t1", {"status": "awaiting_approval", "subject": "compat:a"})
        store.resolve("t1", status="completed", result={"ok": True})
        assert store.get("t1")["status"] == "completed"


# ---------------------------------------------------------------------------
# One history, not two
# ---------------------------------------------------------------------------


class TestSessionDecidesWhichTurnsTravel:
    """`acc.thread_continuity` makes the tracelog the single source of truth:
    a channel may *name* a thread but never supply its content, because a
    client-supplied transcript is model-visible text with no durable origin.

    An OpenAI client resends the whole array every turn, so honouring both
    would put the same turns in front of the model twice and charge them twice
    against the context budget. These tests pin that it is one or the other.
    """

    def _multi_turn(self) -> dict:
        return {
            "model": "analyst",
            "messages": [
                {"role": "system", "content": "be terse"},
                {"role": "user", "content": "first question"},
                {"role": "assistant", "content": "first answer"},
                {"role": "user", "content": "follow-up"},
            ],
        }

    def test_named_thread_sends_only_the_latest_turn(self):
        req = compat.parse_request(self._multi_turn(), session_id="thread-1")
        assert req.prompt == "follow-up"
        assert "first question" not in req.prompt, (
            "prior turns must come from the tracelog, not the client"
        )
        assert req.session_id == "thread-1"

    def test_unnamed_thread_keeps_the_clients_context(self):
        """A client that cannot name a thread must not silently lose context."""
        req = compat.parse_request(self._multi_turn())
        assert "first question" in req.prompt
        assert "follow-up" in req.prompt
        assert req.session_id == ""

    def test_system_message_survives_either_way(self):
        """The system turn is not conversation history and is always sent."""
        for session in ("", "thread-1"):
            req = compat.parse_request(self._multi_turn(), session_id=session)
            assert req.system == "be terse"

    def test_a_single_turn_is_identical_either_way(self):
        """The common case must not change shape just because a thread is named."""
        body = {"model": "analyst", "messages": [{"role": "user", "content": "hi"}]}
        assert compat.parse_request(body).prompt == "hi"
        assert compat.parse_request(body, session_id="t").prompt == "hi"

    def test_session_reaches_the_request_through_handle_async(self, keyed_env):
        seen = {}

        async def gate(req, caller):
            return ""

        async def dispatch(req, caller, attribution):
            seen["session_id"] = req.session_id
            seen["prompt"] = req.prompt
            return "ok", {}

        asyncio.run(compat.handle_async(
            self._multi_turn(), KEY, environ=keyed_env,
            gate=gate, dispatch=dispatch, session_id="thread-9",
        ))
        assert seen["session_id"] == "thread-9"
        assert seen["prompt"] == "follow-up"

    def test_absent_session_is_empty_not_none(self, keyed_env):
        """`""` is the documented 'no thread' value; None would reach the
        channel and be published as a null session_id."""
        seen = {}

        async def gate(req, caller):
            return ""

        async def dispatch(req, caller, attribution):
            seen["session_id"] = req.session_id
            return "ok", {}

        asyncio.run(compat.handle_async(
            self._multi_turn(), KEY, environ=keyed_env,
            gate=gate, dispatch=dispatch,
        ))
        assert seen["session_id"] == ""
