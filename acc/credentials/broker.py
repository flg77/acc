"""CredentialBroker — per-operator OAuth 2.1 delegated-auth for MCP integrations.

Design (PR-PROPOSAL-B):

* The **human operator** performs the OAuth consent in the provider's own UI
  (the agent NEVER enters credentials — that is a prohibited action). ACC only
  ever receives the resulting tokens.
* Tokens are keyed by ``(provider, operator_id)`` — agent A acting for operator
  X can never use operator Y's tokens.
* Only the **refresh token** is persisted (encrypted at rest); short-lived
  **access tokens** are minted on demand and refreshed transparently.
* PKCE (RFC 7636) is used so a public client needs no stored secret.

The actual HTTP token exchange/refresh goes through one mockable seam
(:func:`_token_request`) so the whole broker is unit-testable without a live
provider.
"""

from __future__ import annotations

import base64
import dataclasses
import hashlib
import json
import os
import secrets
import time
from pathlib import Path
from typing import Any, Protocol


class NotConnectedError(RuntimeError):
    """No stored credential for ``(provider, operator_id)`` — the operator has
    not completed the consent flow (or it was revoked)."""


# ---------------------------------------------------------------------------
# Token model
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class OAuthToken:
    provider: str
    operator_id: str
    access_token: str
    refresh_token: str
    expires_at: float          # epoch seconds; 0 = unknown/never
    scopes: tuple[str, ...] = ()

    def is_expired(self, *, skew_s: float = 60.0, now: float | None = None) -> bool:
        """True when the access token is at/near expiry (default 60 s skew)."""
        if not self.expires_at:
            return True
        return (now if now is not None else time.time()) >= (self.expires_at - skew_s)

    def to_dict(self) -> dict[str, Any]:
        d = dataclasses.asdict(self)
        d["scopes"] = list(self.scopes)
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "OAuthToken":
        return cls(
            provider=d["provider"], operator_id=d["operator_id"],
            access_token=d.get("access_token", ""),
            refresh_token=d.get("refresh_token", ""),
            expires_at=float(d.get("expires_at", 0) or 0),
            scopes=tuple(d.get("scopes", []) or []),
        )


# ---------------------------------------------------------------------------
# Token stores
# ---------------------------------------------------------------------------


class TokenStore(Protocol):
    def get(self, provider: str, operator_id: str) -> OAuthToken | None: ...
    def put(self, token: OAuthToken) -> None: ...
    def delete(self, provider: str, operator_id: str) -> None: ...


def _key(provider: str, operator_id: str) -> str:
    return f"{provider}::{operator_id}"


class MemoryTokenStore:
    """In-process store — the default for dev + tests. NOT persisted."""

    def __init__(self) -> None:
        self._d: dict[str, OAuthToken] = {}

    def get(self, provider: str, operator_id: str) -> OAuthToken | None:
        return self._d.get(_key(provider, operator_id))

    def put(self, token: OAuthToken) -> None:
        self._d[_key(token.provider, token.operator_id)] = token

    def delete(self, provider: str, operator_id: str) -> None:
        self._d.pop(_key(provider, operator_id), None)


class SealedFileStore:
    """Fernet-encrypted on-disk store (edge / standalone).

    The symmetric key comes from ``ACC_CRED_KEY`` (a urlsafe-base64 32-byte
    Fernet key) — never auto-generated silently, so a misconfigured prod deploy
    fails closed instead of writing tokens the next boot can't read. One file
    per ``(provider, operator_id)`` under ``root``.
    """

    def __init__(self, root: str | Path, *, key: str | None = None) -> None:
        from cryptography.fernet import Fernet  # noqa: PLC0415 — optional dep

        k = key or os.environ.get("ACC_CRED_KEY", "")
        if not k:
            raise RuntimeError(
                "SealedFileStore: ACC_CRED_KEY is not set (a urlsafe-base64 "
                "32-byte Fernet key). Generate with "
                "`python -c 'from cryptography.fernet import Fernet; "
                "print(Fernet.generate_key().decode())'` and store it as a "
                "deploy secret."
            )
        self._fernet = Fernet(k.encode() if isinstance(k, str) else k)
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)

    def _path(self, provider: str, operator_id: str) -> Path:
        safe = hashlib.sha256(_key(provider, operator_id).encode()).hexdigest()[:32]
        return self._root / f"{safe}.tok"

    def get(self, provider: str, operator_id: str) -> OAuthToken | None:
        p = self._path(provider, operator_id)
        if not p.is_file():
            return None
        raw = self._fernet.decrypt(p.read_bytes())
        return OAuthToken.from_dict(json.loads(raw.decode("utf-8")))

    def put(self, token: OAuthToken) -> None:
        p = self._path(token.provider, token.operator_id)
        blob = self._fernet.encrypt(json.dumps(token.to_dict()).encode("utf-8"))
        p.write_bytes(blob)

    def delete(self, provider: str, operator_id: str) -> None:
        self._path(provider, operator_id).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Provider config + connect challenge
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class ProviderConfig:
    name: str
    auth_url: str
    token_url: str
    client_id: str
    scopes: tuple[str, ...]
    client_secret: str = ""     # "" for a public PKCE client

    @classmethod
    def from_env(cls, provider: str) -> "ProviderConfig":
        """Build from ``<PROVIDER>_OAUTH_*`` env vars (operator-supplied).

        e.g. GOOGLE_OAUTH_CLIENT_ID / _CLIENT_SECRET / _SCOPES (space-sep).
        Auth/token URLs default to the well-known provider endpoints.
        """
        up = provider.upper()
        defaults = _WELL_KNOWN.get(provider, {})
        scopes = (os.environ.get(f"{up}_OAUTH_SCOPES") or defaults.get("scopes", "")).split()
        return cls(
            name=provider,
            auth_url=os.environ.get(f"{up}_OAUTH_AUTH_URL") or defaults.get("auth_url", ""),
            token_url=os.environ.get(f"{up}_OAUTH_TOKEN_URL") or defaults.get("token_url", ""),
            client_id=os.environ.get(f"{up}_OAUTH_CLIENT_ID", ""),
            client_secret=os.environ.get(f"{up}_OAUTH_CLIENT_SECRET", ""),
            scopes=tuple(scopes),
        )


_WELL_KNOWN: dict[str, dict[str, str]] = {
    "google": {
        "auth_url": "https://accounts.google.com/o/oauth2/v2/auth",
        "token_url": "https://oauth2.googleapis.com/token",
        # read-first default scopes (gogcli posture)
        "scopes": ("https://www.googleapis.com/auth/calendar.readonly "
                   "https://www.googleapis.com/auth/gmail.readonly "
                   "https://www.googleapis.com/auth/drive.readonly"),
    },
    "microsoft": {
        "auth_url": "https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
        "token_url": "https://login.microsoftonline.com/common/oauth2/v2.0/token",
        "scopes": "offline_access Mail.Read Calendars.Read Files.Read",
    },
}


@dataclasses.dataclass
class ConnectChallenge:
    auth_url: str
    code_verifier: str
    state: str


def _pkce_pair() -> tuple[str, str]:
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(48)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    ).rstrip(b"=").decode()
    return verifier, challenge


# ---------------------------------------------------------------------------
# HTTP seam (mockable) + broker
# ---------------------------------------------------------------------------


async def _token_request(token_url: str, data: dict[str, str], timeout_s: float = 20.0) -> dict[str, Any]:
    """POST a form-encoded token request; return the JSON body. The single
    network seam — unit tests monkeypatch this to avoid hitting a provider."""
    import httpx  # noqa: PLC0415

    async with httpx.AsyncClient(timeout=timeout_s) as client:
        resp = await client.post(token_url, data=data)
        resp.raise_for_status()
        return resp.json()


class CredentialBroker:
    """Per-operator OAuth 2.1 delegated-auth broker."""

    def __init__(
        self,
        store: TokenStore,
        providers: dict[str, ProviderConfig],
        *,
        redirect_uri: str = "http://localhost:8765/oauth/callback",
    ) -> None:
        self._store = store
        self._providers = providers
        self._redirect_uri = redirect_uri

    def _provider(self, provider: str) -> ProviderConfig:
        cfg = self._providers.get(provider)
        if cfg is None:
            raise KeyError(f"no provider config registered for {provider!r}")
        return cfg

    def start_connect(self, provider: str, operator_id: str) -> ConnectChallenge:
        """Build the consent URL + PKCE verifier. The operator opens auth_url in
        THEIR browser and consents; the redirect returns ``code`` to complete()."""
        cfg = self._provider(provider)
        verifier, challenge = _pkce_pair()
        state = f"{operator_id}:{secrets.token_urlsafe(12)}"
        from urllib.parse import urlencode  # noqa: PLC0415

        params = {
            "client_id": cfg.client_id,
            "redirect_uri": self._redirect_uri,
            "response_type": "code",
            "scope": " ".join(cfg.scopes),
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": state,
            "access_type": "offline",
            "prompt": "consent",
        }
        return ConnectChallenge(
            auth_url=f"{cfg.auth_url}?{urlencode(params)}",
            code_verifier=verifier,
            state=state,
        )

    async def complete(
        self, provider: str, operator_id: str, code: str, code_verifier: str,
        *, now: float | None = None,
    ) -> OAuthToken:
        """Exchange the consent ``code`` for tokens and persist them."""
        cfg = self._provider(provider)
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self._redirect_uri,
            "client_id": cfg.client_id,
            "code_verifier": code_verifier,
        }
        if cfg.client_secret:
            data["client_secret"] = cfg.client_secret
        body = await _token_request(cfg.token_url, data)
        token = self._token_from_response(provider, operator_id, body, cfg, now=now)
        self._store.put(token)
        return token

    async def mint(self, provider: str, operator_id: str, *, now: float | None = None) -> str:
        """Return a valid access token for ``(provider, operator_id)``.

        Refreshes transparently when the stored access token is expired. Raises
        :class:`NotConnectedError` when the operator has not connected.
        """
        token = self._store.get(provider, operator_id)
        if token is None:
            raise NotConnectedError(
                f"{provider} not connected for operator {operator_id!r} — "
                f"run the consent flow first."
            )
        if not token.is_expired(now=now):
            return token.access_token
        # Refresh.
        cfg = self._provider(provider)
        data = {
            "grant_type": "refresh_token",
            "refresh_token": token.refresh_token,
            "client_id": cfg.client_id,
        }
        if cfg.client_secret:
            data["client_secret"] = cfg.client_secret
        body = await _token_request(cfg.token_url, data)
        refreshed = self._token_from_response(
            provider, operator_id, body, cfg,
            fallback_refresh=token.refresh_token, now=now,
        )
        self._store.put(refreshed)
        return refreshed.access_token

    def is_connected(self, provider: str, operator_id: str) -> bool:
        return self._store.get(provider, operator_id) is not None

    def disconnect(self, provider: str, operator_id: str) -> None:
        self._store.delete(provider, operator_id)

    def bearer_resolver(self, provider: str, operator_id: str):
        """An ``async () -> str`` the MCP transport calls per request to inject
        a fresh OAuth bearer (used when an mcp.yaml declares ``auth: oauth``)."""
        async def _resolve() -> str:
            return await self.mint(provider, operator_id)
        return _resolve

    @staticmethod
    def _token_from_response(
        provider: str, operator_id: str, body: dict[str, Any], cfg: ProviderConfig,
        *, fallback_refresh: str = "", now: float | None = None,
    ) -> OAuthToken:
        base = now if now is not None else time.time()
        expires_in = float(body.get("expires_in", 0) or 0)
        return OAuthToken(
            provider=provider,
            operator_id=operator_id,
            access_token=body.get("access_token", ""),
            # Providers omit refresh_token on refresh → keep the prior one.
            refresh_token=body.get("refresh_token") or fallback_refresh,
            expires_at=(base + expires_in) if expires_in else 0.0,
            scopes=tuple((body.get("scope") or " ".join(cfg.scopes)).split()),
        )
