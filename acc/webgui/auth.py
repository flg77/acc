"""Pluggable authentication for acc-webgui (proposal acc-webgui PR-5).

acc-webgui is the first network-exposed *human* surface in ACC — it
cannot ship unauthenticated.  Auth is **capability-tiered**, mirroring
proposal 014: the mode is detected from configuration, never compiled
in.

| mode          | mechanism                                              |
|---------------|--------------------------------------------------------|
| ``oauth-proxy``| trust the identity headers an OpenShift oauth-proxy   |
|               | sidecar injects (``X-Forwarded-Email``)                |
| ``oidc``      | validate a bearer JWT against a configured OIDC issuer |
| ``htpasswd``  | a username/password login (Apache htpasswd file) that |
|               | mints a short-lived signed session JWT — the dev tier  |
| ``mtls``      | client-certificate auth: a TLS-terminating front layer |
|               | verifies the cert and injects the subject as a header  |
| ``token``     | a static operator/viewer bearer token — the floor      |
| ``none``      | only permitted on a loopback bind (dev)                |

RBAC is intentionally minimal — two roles: **viewer** (read + tracing)
and **operator** (also infuse / prompt / oversight / test-llm).

When no auth is configured the server **refuses to bind a non-loopback
address** (`enforce_bind_safety`) — fail loud, never an open port.
"""

from __future__ import annotations

import dataclasses
import ipaddress
import logging
import os
import secrets
import time
from typing import Mapping

from fastapi import HTTPException, Request

logger = logging.getLogger("acc.webgui.auth")

ROLE_VIEWER = "viewer"
ROLE_OPERATOR = "operator"

MODE_OAUTH_PROXY = "oauth-proxy"
MODE_OIDC = "oidc"
MODE_HTPASSWD = "htpasswd"
MODE_MTLS = "mtls"
MODE_TOKEN = "token"
MODE_NONE = "none"

_DEFAULT_SESSION_TTL = 43200  # 12h


@dataclasses.dataclass
class Principal:
    """An authenticated human."""
    user: str
    role: str  # ROLE_VIEWER | ROLE_OPERATOR


@dataclasses.dataclass
class AuthConfig:
    """Resolved auth configuration (from the environment)."""
    mode: str
    operator_token: str = ""
    viewer_token: str = ""
    operator_users: tuple[str, ...] = ()
    oidc_issuer: str = ""
    # htpasswd mode
    htpasswd_path: str = ""
    session_secret: str = ""
    session_ttl: int = _DEFAULT_SESSION_TTL
    # mtls mode
    mtls_header: str = "x-client-cert-subject"
    mtls_verify_header: str = "x-client-cert-verify"


def resolve_auth_config() -> AuthConfig:
    """Build the `AuthConfig` from `ACC_WEBGUI_*` environment variables."""
    mode = os.environ.get("ACC_WEBGUI_AUTH_MODE", "").strip().lower() or MODE_NONE
    operator_users = tuple(
        u.strip() for u in os.environ.get("ACC_WEBGUI_OPERATOR_USERS", "").split(",")
        if u.strip()
    )

    # htpasswd: a session-signing secret is required.  When unset we
    # generate an ephemeral one — every session then dies on restart.
    session_secret = os.environ.get("ACC_WEBGUI_SESSION_SECRET", "").strip()
    if mode == MODE_HTPASSWD and not session_secret:
        session_secret = secrets.token_urlsafe(32)
        logger.warning(
            "webgui: ACC_WEBGUI_SESSION_SECRET unset — generated an ephemeral "
            "secret; all sessions are invalidated on restart. Set it explicitly "
            "for session continuity / multiple replicas."
        )
    try:
        session_ttl = int(os.environ.get("ACC_WEBGUI_SESSION_TTL",
                                         str(_DEFAULT_SESSION_TTL)))
    except ValueError:
        session_ttl = _DEFAULT_SESSION_TTL

    return AuthConfig(
        mode=mode,
        operator_token=os.environ.get("ACC_WEBGUI_OPERATOR_TOKEN", ""),
        viewer_token=os.environ.get("ACC_WEBGUI_VIEWER_TOKEN", ""),
        operator_users=operator_users,
        oidc_issuer=os.environ.get("ACC_WEBGUI_OIDC_ISSUER", ""),
        htpasswd_path=os.environ.get("ACC_WEBGUI_HTPASSWD_PATH", "").strip(),
        session_secret=session_secret,
        session_ttl=session_ttl,
        mtls_header=os.environ.get(
            "ACC_WEBGUI_MTLS_HEADER", "x-client-cert-subject").strip().lower(),
        mtls_verify_header=os.environ.get(
            "ACC_WEBGUI_MTLS_VERIFY_HEADER", "x-client-cert-verify").strip().lower(),
    )


def _is_loopback(host: str) -> bool:
    if host in ("localhost", ""):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def enforce_bind_safety(host: str, cfg: AuthConfig) -> None:
    """Refuse a non-loopback bind when no auth is configured.

    Raises `RuntimeError` — the server must not start.  This is the
    acc-webgui equivalent of proposal 014's `CNIDoesNotEnforce`: fail
    loud rather than expose an unauthenticated UI on the network.

    `mtls` mode is authenticated, so it is *allowed* to bind a
    non-loopback address — but it trusts a header injected by the
    front layer, so doing so without a proxy in front is unsafe; warn.
    """
    if cfg.mode == MODE_NONE and not _is_loopback(host):
        raise RuntimeError(
            f"acc-webgui refuses to bind {host!r} with no authentication. "
            f"Set ACC_WEBGUI_AUTH_MODE (oauth-proxy|oidc|htpasswd|mtls|token), "
            f"or bind 127.0.0.1 for local-only development."
        )
    if cfg.mode == MODE_MTLS and not _is_loopback(host):
        logger.warning(
            "webgui: mtls mode bound to non-loopback %r — acc-webgui trusts "
            "the %r header, so it MUST be reachable only through the "
            "TLS-terminating front layer that verifies the client cert. "
            "Bind 127.0.0.1 and expose it via that proxy.",
            host, cfg.mtls_header,
        )


# ---------------------------------------------------------------------------
# htpasswd — bcrypt password verification
# ---------------------------------------------------------------------------

# A decoy hash so an unknown-user login still spends a bcrypt
# verification — no timing oracle for which usernames exist.  Computed
# once on first use (bcrypt is an optional [webgui] dependency, so it
# must not be imported at module load).
_dummy_bcrypt: bytes | None = None


def _normalise_bcrypt(h: str) -> str:
    """`htpasswd -B` emits the `$2y$` ident; the `bcrypt` package wants
    `$2a$`/`$2b$`.  The algorithm is identical — rewrite the prefix."""
    if h.startswith("$2y$"):
        return "$2b$" + h[4:]
    return h


def _load_htpasswd(path: str) -> dict[str, str]:
    """Parse an Apache htpasswd file → ``{username: bcrypt_hash}``.

    Re-read on every login so edits need no restart.  Only bcrypt
    hashes are accepted (`htpasswd -B`); any other line is skipped
    with a warning.  A missing/unreadable file fails closed (``{}``).
    """
    entries: dict[str, str] = {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for lineno, raw in enumerate(fh, 1):
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                user, sep, h = line.partition(":")
                if not sep or not user or not h:
                    continue
                if not h.startswith(("$2a$", "$2b$", "$2y$")):
                    logger.warning(
                        "webgui: htpasswd line %d (user %r): non-bcrypt hash "
                        "skipped — regenerate with `htpasswd -B`", lineno, user)
                    continue
                entries[user] = h
    except OSError as exc:
        logger.error("webgui: cannot read htpasswd file %r: %s", path, exc)
    return entries


def verify_htpasswd(username: str, password: str, path: str) -> bool:
    """True when *username*/*password* match a bcrypt entry in the file."""
    import bcrypt  # noqa: PLC0415 — optional [webgui] dependency

    global _dummy_bcrypt
    if _dummy_bcrypt is None:
        _dummy_bcrypt = bcrypt.hashpw(b"acc-webgui-decoy", bcrypt.gensalt())

    stored = _load_htpasswd(path).get(username)
    pw = password.encode("utf-8")
    if stored is None:
        bcrypt.checkpw(pw, _dummy_bcrypt)  # constant-time decoy
        return False
    try:
        return bcrypt.checkpw(pw, _normalise_bcrypt(stored).encode("ascii"))
    except (ValueError, TypeError) as exc:
        logger.warning("webgui: htpasswd hash for %r is malformed: %s",
                        username, exc)
        return False


def role_for(user: str, cfg: AuthConfig) -> str:
    """Map a user identity to a role via `ACC_WEBGUI_OPERATOR_USERS`."""
    return ROLE_OPERATOR if user in cfg.operator_users else ROLE_VIEWER


# ---------------------------------------------------------------------------
# htpasswd — signed session JWT (HS256)
# ---------------------------------------------------------------------------

# A DEDICATED HS256-only codec.  It must never be shared with the OIDC
# RS256/ES256 path — an explicit single-algorithm allow-list blocks
# `alg:none` and RS256/HS256 confusion (a verifier that accepts both
# could be tricked into treating an RS256 public key as an HMAC key).
def _session_jwt():
    from authlib.jose import JsonWebToken  # noqa: PLC0415
    return JsonWebToken(["HS256"])


def mint_session_jwt(user: str, role: str, cfg: AuthConfig) -> str:
    """Mint a short-lived HS256 session token for an htpasswd login."""
    now = int(time.time())
    claims = {
        "iss": "acc-webgui",
        "sub": user,
        "role": role,
        "iat": now,
        "exp": now + cfg.session_ttl,
    }
    token = _session_jwt().encode({"alg": "HS256"}, claims, cfg.session_secret)
    return token.decode("ascii") if isinstance(token, bytes) else token


def _verify_session_jwt(token: str, cfg: AuthConfig) -> dict | None:
    """Validate an htpasswd session JWT; return its claims or None."""
    if not cfg.session_secret:
        return None
    try:
        claims = _session_jwt().decode(token, cfg.session_secret)
        claims.validate()  # enforces `exp`
        return dict(claims)
    except Exception as exc:  # noqa: BLE001 — any failure = unauthenticated
        logger.warning("webgui: session token rejected: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Principal extraction per mode
# ---------------------------------------------------------------------------


def _extract_token(headers: Mapping, query_params: Mapping) -> str:
    """Pull a bearer token from the `Authorization` header, falling back
    to a `?token=` query parameter.

    The query fallback exists only for the WebSocket — browsers cannot
    set headers on `new WebSocket()`.  REST callers pass an empty
    `query_params` so REST tokens never land in access logs.
    """
    auth = headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return (query_params.get("token") or "").strip()


def _principal(
    headers: Mapping, query_params: Mapping, cfg: AuthConfig,
) -> Principal | None:
    """Resolve a request's `Principal`, or None if unauthenticated."""
    if cfg.mode == MODE_NONE:
        # Loopback-only dev (enforce_bind_safety guaranteed loopback).
        return Principal(user="dev:localhost", role=ROLE_OPERATOR)

    if cfg.mode == MODE_OAUTH_PROXY:
        # The oauth-proxy sidecar has already authenticated the user and
        # injects identity headers; acc-webgui trusts them.
        user = (headers.get("X-Forwarded-Email")
                or headers.get("X-Forwarded-User") or "")
        if not user:
            return None
        return Principal(user=user, role=role_for(user, cfg))

    if cfg.mode == MODE_TOKEN:
        token = _extract_token(headers, query_params)
        if token and token == cfg.operator_token:
            return Principal(user="token:operator", role=ROLE_OPERATOR)
        if token and token == cfg.viewer_token:
            return Principal(user="token:viewer", role=ROLE_VIEWER)
        return None

    if cfg.mode == MODE_OIDC:
        token = _extract_token(headers, query_params)
        if not token:
            return None
        claims = _verify_oidc(token, cfg)
        if claims is None:
            return None
        user = claims.get("email") or claims.get("sub") or "oidc:unknown"
        return Principal(user=user, role=role_for(user, cfg))

    if cfg.mode == MODE_HTPASSWD:
        # The bearer token is a session JWT minted by POST /api/login.
        token = _extract_token(headers, query_params)
        if not token:
            return None
        claims = _verify_session_jwt(token, cfg)
        if claims is None:
            return None
        return Principal(user=claims.get("sub", "htpasswd:unknown"),
                         role=claims.get("role", ROLE_VIEWER))

    if cfg.mode == MODE_MTLS:
        # A TLS-terminating front layer verified the client cert and
        # injected the result.  Trust the subject ONLY on a SUCCESS.
        if headers.get(cfg.mtls_verify_header, "").upper() != "SUCCESS":
            return None
        user = (headers.get(cfg.mtls_header) or "").strip()
        if not user:
            return None
        return Principal(user=user, role=role_for(user, cfg))

    return None


def _verify_oidc(token: str, cfg: AuthConfig) -> dict | None:
    """Validate an OIDC bearer JWT against the configured issuer's JWKS.

    Best-effort: returns the claims on success, None on any failure.
    Uses authlib; the JWKS is fetched from the issuer's discovery
    document.
    """
    try:
        import httpx  # noqa: PLC0415
        from authlib.jose import JsonWebToken  # noqa: PLC0415

        disco = httpx.get(
            f"{cfg.oidc_issuer.rstrip('/')}/.well-known/openid-configuration",
            timeout=5.0,
        ).json()
        jwks = httpx.get(disco["jwks_uri"], timeout=5.0).json()
        claims = JsonWebToken(["RS256", "ES256"]).decode(token, jwks)
        claims.validate()
        return dict(claims)
    except Exception as exc:
        logger.warning("webgui: OIDC token validation failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# FastAPI dependencies
# ---------------------------------------------------------------------------


def _auth_cfg(request: Request) -> AuthConfig:
    return request.app.state.auth_config


def require_viewer(request: Request) -> Principal:
    """Dependency — any authenticated principal (viewer or operator)."""
    cfg = _auth_cfg(request)
    principal = _principal(request.headers, {}, cfg)
    if principal is None:
        raise HTTPException(status_code=401, detail="authentication required")
    return principal


def require_operator(request: Request) -> Principal:
    """Dependency — an authenticated principal with the operator role."""
    principal = require_viewer(request)
    if principal.role != ROLE_OPERATOR:
        raise HTTPException(status_code=403,
                            detail="operator role required for this action")
    return principal


def authenticate_websocket(websocket, cfg: AuthConfig) -> Principal | None:
    """Resolve the `Principal` for a WebSocket upgrade request.

    Reuses `_principal()` but also reads a `?token=` query parameter —
    browsers cannot set an `Authorization` header on a WebSocket.  The
    viewer role suffices: the WS streams read-only snapshots.
    """
    return _principal(websocket.headers, websocket.query_params, cfg)
