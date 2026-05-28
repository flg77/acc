"""Tests for the A2A outbound client + transport resolver (Phase 3 of OpenSpec
``20260527-a2a-agent-interop``)."""

from __future__ import annotations

import pytest

# select_transport is pure (no aiohttp); test it always.
from acc.a2a.client import select_transport

# aiohttp is the optional `a2a` extra — only the call_peer tests need it.
# Detect once at import time, gate the relevant tests with skipif so the
# pure select_transport tests still run when the extra is absent.
try:
    import aiohttp as _aiohttp_mod  # noqa: F401
    _HAVE_AIOHTTP = True
except ImportError:
    _HAVE_AIOHTTP = False

aiohttp_required = pytest.mark.skipif(
    not _HAVE_AIOHTTP, reason="aiohttp not installed (acc[a2a] extra)",
)


# -----------------------------------------------------------------------
# select_transport — pure decision matrix
# -----------------------------------------------------------------------


def test_rhoai_with_peer_url_picks_a2a():
    assert select_transport(
        deploy_mode="rhoai", target_cid="sol-02",
        peer_urls={"sol-02": "https://peer.sol-02.svc:8443"},
    ) == "a2a"


def test_rhoai_without_peer_url_falls_back_to_nats():
    assert select_transport(
        deploy_mode="rhoai", target_cid="sol-02",
        peer_urls={},
    ) == "nats"


def test_rhoai_with_empty_peer_url_falls_back_to_nats():
    """A peer URL configured as empty string is the same as not configured."""
    assert select_transport(
        deploy_mode="rhoai", target_cid="sol-02",
        peer_urls={"sol-02": ""},
    ) == "nats"


def test_edge_always_nats():
    assert select_transport(
        deploy_mode="edge", target_cid="sol-02",
        peer_urls={"sol-02": "https://peer:8443"},
    ) == "nats"


def test_standalone_always_nats():
    assert select_transport(
        deploy_mode="standalone", target_cid="sol-02",
        peer_urls={"sol-02": "https://peer:8443"},
    ) == "nats"


def test_prefer_a2a_false_forces_nats():
    """Operational override: even on rhoai with a peer URL, prefer_a2a=False
    forces the legacy bridge — useful during cutover."""
    assert select_transport(
        deploy_mode="rhoai", target_cid="sol-02",
        peer_urls={"sol-02": "https://peer:8443"},
        prefer_a2a=False,
    ) == "nats"


def test_peer_urls_none_treated_as_empty():
    assert select_transport(
        deploy_mode="rhoai", target_cid="sol-02", peer_urls=None,
    ) == "nats"


# -----------------------------------------------------------------------
# call_peer — aiohttp-gated.  Exercise it against an in-process server.
# -----------------------------------------------------------------------

if _HAVE_AIOHTTP:
    from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

    from acc.a2a.client import A2AClientError, call_peer  # noqa: E402
    from acc.a2a.jsonrpc import (  # noqa: E402
        GOVERNANCE_BLOCKED, INVALID_PARAMS, error, success,
    )

    async def _stub_peer(handler):
        """Build an aiohttp app with one handler on POST '/' (the A2A endpoint)."""
        import aiohttp.web as web
        app = web.Application()
        app.router.add_post("/", handler)
        return app


@aiohttp_required
async def test_call_peer_returns_jsonrpc_result_on_success():
    captured: dict = {}

    async def handler(request):
        import aiohttp.web as web
        body = await request.json()
        captured["body"] = body
        return web.json_response(success(body["id"], {
            "taskId": body["params"]["taskId"], "output": "42",
        }))

    app = await _stub_peer(handler)
    async with TestClient(TestServer(app)) as client:
        url = str(client.make_url("/"))
        result = await call_peer(url, content="what is the answer?", task_id="t-1")

    assert result == {"taskId": "t-1", "output": "42"}
    # Wire shape: message/send + content + taskId.
    assert captured["body"]["method"] == "message/send"
    assert captured["body"]["params"]["content"] == "what is the answer?"
    assert captured["body"]["params"]["taskId"] == "t-1"
    assert captured["body"]["id"] == "t-1"


@aiohttp_required
async def test_call_peer_synthesises_task_id_when_omitted():
    async def handler(request):
        import aiohttp.web as web
        body = await request.json()
        return web.json_response(success(body["id"], {"taskId": body["params"]["taskId"]}))

    app = await _stub_peer(handler)
    async with TestClient(TestServer(app)) as client:
        url = str(client.make_url("/"))
        result = await call_peer(url, content="x")
    assert result["taskId"].startswith("out-")


@aiohttp_required
async def test_call_peer_raises_on_jsonrpc_error_with_code_and_data():
    async def handler(request):
        import aiohttp.web as web
        body = await request.json()
        return web.json_response(
            error(body["id"], INVALID_PARAMS, "Invalid params: 'content' required"),
            status=400,
        )

    app = await _stub_peer(handler)
    async with TestClient(TestServer(app)) as client:
        url = str(client.make_url("/"))
        with pytest.raises(A2AClientError) as exc_info:
            await call_peer(url, content="x")
    assert exc_info.value.code == INVALID_PARAMS
    assert "Invalid params" in str(exc_info.value)


@aiohttp_required
async def test_call_peer_flags_governance_blocked():
    """Governance denials must surface distinctly so the caller does NOT
    retry on the NATS bridge (a denial is a denial, not a transport failure)."""
    async def handler(request):
        import aiohttp.web as web
        body = await request.json()
        return web.json_response(
            error(body["id"], GOVERNANCE_BLOCKED,
                  "Blocked by ACC governance",
                  data={"blockReason": "Cat-A: write_workspace denied"}),
            status=403,
        )

    app = await _stub_peer(handler)
    async with TestClient(TestServer(app)) as client:
        url = str(client.make_url("/"))
        with pytest.raises(A2AClientError) as exc_info:
            await call_peer(url, content="write a file")
    err = exc_info.value
    assert err.is_governance_blocked
    assert err.code == GOVERNANCE_BLOCKED
    assert err.data["blockReason"] == "Cat-A: write_workspace denied"


@aiohttp_required
async def test_call_peer_raises_on_http_error():
    async def handler(request):
        import aiohttp.web as web
        return web.Response(status=502, text="bad gateway")

    app = await _stub_peer(handler)
    async with TestClient(TestServer(app)) as client:
        url = str(client.make_url("/"))
        with pytest.raises(A2AClientError) as exc_info:
            await call_peer(url, content="x")
    # 502 + non-JSON body → non-JSON-body error path.
    assert "non-JSON" in str(exc_info.value) or "bad gateway" in str(exc_info.value)
    assert exc_info.value.code is None   # HTTP failure, not a JSON-RPC error
    assert not exc_info.value.is_governance_blocked


@aiohttp_required
async def test_call_peer_raises_on_connection_refused():
    """Unreachable peer → transport failure (not governance).  Caller may
    fall back to NATS in this case."""
    # Port 1 is reserved (tcpmux) — nothing should be listening.
    with pytest.raises(A2AClientError) as exc_info:
        await call_peer("http://127.0.0.1:1/", content="x", timeout=1.0)
    assert not exc_info.value.is_governance_blocked
