"""Tests for the A2A inbound HTTP + JSON-RPC server (Phases 1b/2 of OpenSpec
``20260527-a2a-agent-interop``).

Skip gracefully when ``aiohttp`` isn't installed — A2A is opt-in via the
``a2a`` extra, mirroring the metrics_otel / vector_milvus pattern.
"""

from __future__ import annotations

import pytest

# Gate the entire module: the A2A server *is* aiohttp.  Without it the tests
# can't run; with it they're a tight in-process exercise of the JSON-RPC
# handler + governance contract.
aiohttp = pytest.importorskip("aiohttp")
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

from acc.a2a.jsonrpc import (  # noqa: E402
    GOVERNANCE_BLOCKED,
    INVALID_PARAMS,
    INVALID_REQUEST,
    METHOD_NOT_FOUND,
    PARSE_ERROR,
)
from acc.a2a.server import build_app, METHOD_MESSAGE_SEND  # noqa: E402
from acc.cognitive_core import CognitiveResult  # noqa: E402
from acc.config import RoleDefinitionConfig  # noqa: E402


# --------------------------------------------------------------------------
# Fakes — process_task is the ONE thing the server depends on.
# --------------------------------------------------------------------------


class _FakeCore:
    """Stands in for CognitiveCore.  Captures the task payload + role it was
    called with, and returns a configurable :class:`CognitiveResult`."""

    def __init__(self, result: CognitiveResult | None = None, raises: Exception | None = None):
        self._result = result or CognitiveResult(output="ok")
        self._raises = raises
        self.calls: list[tuple[dict, RoleDefinitionConfig]] = []

    async def process_task(self, task, role=None, **_):
        self.calls.append((task, role))
        if self._raises is not None:
            raise self._raises
        return self._result


def _role(**overrides) -> RoleDefinitionConfig:
    base = {
        "purpose": "Help with code.",
        "persona": "analytical",
        "task_types": ["CODE_GENERATE"],
        "version": "1.0.0",
        "domain_id": "software_engineering",
    }
    base.update(overrides)
    return RoleDefinitionConfig.model_validate(base)


def _app(core: _FakeCore, role: RoleDefinitionConfig | None = None):
    return build_app(
        core=core,
        role=role or _role(),
        role_label="coding_agent",
        collective_id="sol-01",
        agent_id="coding-agent-9c1d",
        base_url="http://coding-agent.sol-01.svc:8443",
    )


async def _client(app):
    """Return an aiohttp TestClient.  Caller manages its lifecycle."""
    return TestClient(TestServer(app))


# --------------------------------------------------------------------------
# Phase 1b — GET /.well-known/agent-card.json
# --------------------------------------------------------------------------


async def test_get_card_returns_valid_a2a_card():
    core = _FakeCore()
    async with await _client(_app(core)) as client:
        resp = await client.get("/.well-known/agent-card.json")
        assert resp.status == 200
        card = await resp.json()
    # Standard A2A fields.
    for k in ("name", "description", "url", "version", "capabilities",
              "defaultInputModes", "defaultOutputModes", "skills",
              "authentication", "schemaVersion"):
        assert k in card
    assert card["name"] == "coding_agent@sol-01"
    assert card["description"] == "Help with code."
    # ACC vendor extension: identity + governance + flags.
    acc = card["acc"]
    assert acc["role"] == "coding_agent"
    assert acc["collectiveId"] == "sol-01"
    assert "governance" in acc


# --------------------------------------------------------------------------
# Phase 2 — POST / : JSON-RPC message/send → CognitiveCore.process_task
# --------------------------------------------------------------------------


async def test_jsonrpc_message_send_succeeds_and_calls_process_task():
    core = _FakeCore(CognitiveResult(output="42", reasoning="I considered options."))
    async with await _client(_app(core)) as client:
        resp = await client.post("/", json={
            "jsonrpc": "2.0", "id": 7, "method": METHOD_MESSAGE_SEND,
            "params": {"content": "What is the answer?", "taskId": "t-7"},
        })
        assert resp.status == 200
        body = await resp.json()
    assert body["jsonrpc"] == "2.0"
    assert body["id"] == 7
    assert body["result"]["output"] == "42"
    assert body["result"]["taskId"] == "t-7"
    assert body["result"]["reasoning"] == "I considered options."
    # CognitiveCore was called once with our task + the agent's role.
    assert len(core.calls) == 1
    task, role = core.calls[0]
    assert task["content"] == "What is the answer?"
    assert task["task_id"] == "t-7"
    assert task["target_role"] == "coding_agent"
    assert task["source"] == "a2a"
    assert isinstance(role, RoleDefinitionConfig)


async def test_jsonrpc_accepts_message_content_nested():
    """A2A spec shape: params.message.content.  The handler accepts both."""
    core = _FakeCore()
    async with await _client(_app(core)) as client:
        resp = await client.post("/", json={
            "jsonrpc": "2.0", "id": "abc", "method": METHOD_MESSAGE_SEND,
            "params": {"message": {"content": "hi"}},
        })
        body = await resp.json()
    assert "result" in body
    assert core.calls[0][0]["content"] == "hi"


async def test_jsonrpc_synthesises_task_id_when_omitted():
    core = _FakeCore()
    async with await _client(_app(core)) as client:
        resp = await client.post("/", json={
            "jsonrpc": "2.0", "id": 1, "method": METHOD_MESSAGE_SEND,
            "params": {"content": "x"},
        })
        body = await resp.json()
    assert body["result"]["taskId"].startswith("a2a-")


# --------------------------------------------------------------------------
# Governance: blocked result MUST surface as a structured JSON-RPC error,
# NOT silently as a success — A2A is not a softer path (OpenSpec scope-and-
# risk: "A2A risk — governance bypass").
# --------------------------------------------------------------------------


async def test_blocked_task_returns_governance_jsonrpc_error():
    core = _FakeCore(CognitiveResult(
        blocked=True,
        block_reason="Cat-A: write_workspace not granted (skill fs_write disallowed)",
        reasoning="(skipped — pre-gate denied)",
    ))
    async with await _client(_app(core)) as client:
        resp = await client.post("/", json={
            "jsonrpc": "2.0", "id": 99, "method": METHOD_MESSAGE_SEND,
            "params": {"content": "write a file please", "taskId": "deny-1"},
        })
        assert resp.status == 403
        body = await resp.json()
    assert "result" not in body
    err = body["error"]
    assert err["code"] == GOVERNANCE_BLOCKED
    assert "Blocked by ACC governance" in err["message"]
    assert err["data"]["taskId"] == "deny-1"
    assert "fs_write" in err["data"]["blockReason"]
    # The denial *did* reach the pipeline — CognitiveCore was called; the
    # response just refused to deliver the result.  Proves no early-bypass.
    assert len(core.calls) == 1


# --------------------------------------------------------------------------
# JSON-RPC error handling — the standard error codes.
# --------------------------------------------------------------------------


async def test_parse_error_on_invalid_json():
    async with await _client(_app(_FakeCore())) as client:
        resp = await client.post("/", data="not json{",
                                 headers={"Content-Type": "application/json"})
        assert resp.status == 400
        body = await resp.json()
    assert body["error"]["code"] == PARSE_ERROR


async def test_invalid_request_on_missing_jsonrpc_version():
    async with await _client(_app(_FakeCore())) as client:
        resp = await client.post("/", json={"method": METHOD_MESSAGE_SEND, "id": 1})
        assert resp.status == 400
        body = await resp.json()
    assert body["error"]["code"] == INVALID_REQUEST


async def test_method_not_found():
    async with await _client(_app(_FakeCore())) as client:
        resp = await client.post("/", json={
            "jsonrpc": "2.0", "id": 1, "method": "nonsuch/method",
        })
        assert resp.status == 404
        body = await resp.json()
    assert body["error"]["code"] == METHOD_NOT_FOUND


async def test_invalid_params_when_content_missing():
    async with await _client(_app(_FakeCore())) as client:
        resp = await client.post("/", json={
            "jsonrpc": "2.0", "id": 1, "method": METHOD_MESSAGE_SEND,
            "params": {},
        })
        assert resp.status == 400
        body = await resp.json()
    assert body["error"]["code"] == INVALID_PARAMS


async def test_internal_error_when_process_task_raises():
    core = _FakeCore(raises=RuntimeError("boom"))
    async with await _client(_app(core)) as client:
        resp = await client.post("/", json={
            "jsonrpc": "2.0", "id": 1, "method": METHOD_MESSAGE_SEND,
            "params": {"content": "x"},
        })
        assert resp.status == 500
        body = await resp.json()
    assert "boom" in body["error"]["message"]


# --------------------------------------------------------------------------
# Env helpers
# --------------------------------------------------------------------------


def test_env_port_disabled_when_unset(monkeypatch):
    from acc.a2a.server import env_port
    monkeypatch.delenv("ACC_A2A_PORT", raising=False)
    assert env_port() is None


def test_env_port_parses_valid(monkeypatch):
    from acc.a2a.server import env_port
    monkeypatch.setenv("ACC_A2A_PORT", "8443")
    assert env_port() == 8443


def test_env_port_rejects_invalid(monkeypatch):
    from acc.a2a.server import env_port
    monkeypatch.setenv("ACC_A2A_PORT", "abc")
    assert env_port() is None
    monkeypatch.setenv("ACC_A2A_PORT", "0")
    assert env_port() is None
    monkeypatch.setenv("ACC_A2A_PORT", "99999")
    assert env_port() is None


def test_env_base_url_default_and_override(monkeypatch):
    from acc.a2a.server import env_base_url
    monkeypatch.delenv("ACC_A2A_BASE_URL", raising=False)
    assert env_base_url("0.0.0.0", 8443) == "http://0.0.0.0:8443"
    monkeypatch.setenv("ACC_A2A_BASE_URL", "https://override.example/agent")
    assert env_base_url("0.0.0.0", 8443) == "https://override.example/agent"
