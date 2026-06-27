"""CredentialBroker — per-operator OAuth 2.1 delegated-auth (integrations B).

Hermetic: the single network seam ``acc.credentials.broker._token_request`` is
monkeypatched, so no provider is contacted. Covers the token model, both
stores, the connect→complete→mint→refresh lifecycle, per-operator isolation,
and the MCP transport's oauth-bearer injection.
"""

from __future__ import annotations

import pytest

from acc.credentials import (
    CredentialBroker,
    MemoryTokenStore,
    NotConnectedError,
    OAuthToken,
    ProviderConfig,
    SealedFileStore,
)
from acc.credentials import broker as broker_mod


def _cfg() -> dict[str, ProviderConfig]:
    return {
        "google": ProviderConfig(
            name="google",
            auth_url="https://accounts.google.com/o/oauth2/v2/auth",
            token_url="https://oauth2.googleapis.com/token",
            client_id="cid.apps.googleusercontent.com",
            scopes=("https://www.googleapis.com/auth/calendar.readonly",),
        )
    }


# ---- token model -----------------------------------------------------------

def test_token_expiry_and_roundtrip():
    t = OAuthToken("google", "op1", "at", "rt", expires_at=1000.0, scopes=("s",))
    assert t.is_expired(now=999.0) is True       # within 60s skew
    assert t.is_expired(now=900.0) is False
    assert OAuthToken.from_dict(t.to_dict()) == t
    assert OAuthToken("g", "o", "a", "r", 0.0).is_expired(now=0.0) is True  # unknown expiry


def test_memory_store_isolation():
    s = MemoryTokenStore()
    s.put(OAuthToken("google", "alice", "a-tok", "r", 0))
    assert s.get("google", "bob") is None
    assert s.get("google", "alice").access_token == "a-tok"
    s.delete("google", "alice")
    assert s.get("google", "alice") is None


def test_sealed_file_store_roundtrip(tmp_path):
    from cryptography.fernet import Fernet
    store = SealedFileStore(tmp_path, key=Fernet.generate_key().decode())
    tok = OAuthToken("google", "op1", "secret-at", "secret-rt", 1234.0, ("s1", "s2"))
    store.put(tok)
    # On-disk bytes must be encrypted (the secret must not appear in plaintext).
    blob = next(tmp_path.glob("*.tok")).read_bytes()
    assert b"secret-at" not in blob and b"secret-rt" not in blob
    assert store.get("google", "op1") == tok


# ---- broker lifecycle ------------------------------------------------------

def test_start_connect_builds_pkce_url():
    b = CredentialBroker(MemoryTokenStore(), _cfg())
    ch = b.start_connect("google", "op1")
    assert "code_challenge=" in ch.auth_url and "code_challenge_method=S256" in ch.auth_url
    assert ch.state.startswith("op1:") and ch.code_verifier


@pytest.mark.asyncio
async def test_complete_then_mint_no_refresh(monkeypatch):
    async def fake_token_request(url, data, timeout_s=20.0):
        assert data["grant_type"] == "authorization_code"
        return {"access_token": "AT1", "refresh_token": "RT1", "expires_in": 3600}

    monkeypatch.setattr(broker_mod, "_token_request", fake_token_request)
    b = CredentialBroker(MemoryTokenStore(), _cfg())
    tok = await b.complete("google", "op1", code="auth-code", code_verifier="v", now=0.0)
    assert tok.access_token == "AT1" and tok.expires_at == 3600.0
    # mint before expiry returns the SAME token (no refresh call).
    assert await b.mint("google", "op1", now=10.0) == "AT1"


@pytest.mark.asyncio
async def test_mint_refreshes_when_expired(monkeypatch):
    calls = {"n": 0}

    async def fake_token_request(url, data, timeout_s=20.0):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"access_token": "AT1", "refresh_token": "RT1", "expires_in": 100}
        assert data["grant_type"] == "refresh_token" and data["refresh_token"] == "RT1"
        return {"access_token": "AT2", "expires_in": 3600}  # no new refresh_token

    monkeypatch.setattr(broker_mod, "_token_request", fake_token_request)
    b = CredentialBroker(MemoryTokenStore(), _cfg())
    await b.complete("google", "op1", "c", "v", now=0.0)
    # now past expiry → refresh; new access token; prior refresh token retained.
    assert await b.mint("google", "op1", now=10_000.0) == "AT2"
    assert b._store.get("google", "op1").refresh_token == "RT1"


@pytest.mark.asyncio
async def test_mint_without_connection_raises():
    b = CredentialBroker(MemoryTokenStore(), _cfg())
    with pytest.raises(NotConnectedError):
        await b.mint("google", "nobody")


# ---- MCP transport oauth seam ---------------------------------------------

@pytest.mark.asyncio
async def test_http_transport_injects_oauth_bearer(monkeypatch):
    from acc.mcp.manifest import MCPManifest
    from acc.mcp.transports import HTTPTransport

    man = MCPManifest(
        server_id="google_workspace", purpose="x",
        url="http://acc-mcp-google:8080/rpc", auth="oauth", oauth_provider="google",
    )

    async def resolver():
        return "MINTED-TOKEN"

    tr = HTTPTransport(man, bearer_resolver=resolver)

    captured = {}

    class _Resp:
        status_code = 200
        def json(self):  # noqa: D401
            return {"jsonrpc": "2.0", "id": 1, "result": {}}

    async def fake_post(path, json=None, headers=None):
        captured["headers"] = headers
        return _Resp()

    monkeypatch.setattr(tr._client, "post", fake_post)
    await tr.send_rpc({"jsonrpc": "2.0", "id": 1, "method": "ping"})
    assert captured["headers"] == {"Authorization": "Bearer MINTED-TOKEN"}
    await tr.close()
