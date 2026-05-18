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

from fastapi import Depends, HTTPException, Request

logger = logging.getLogger("acc.webgui.auth")

ROLE_VIEWER = "viewer"
ROLE_OPERATOR = "operator"

MODE_OAUTH_PROXY = "oauth-proxy"
MODE_OIDC = "oidc"
MODE_TOKEN = "token"
MODE_NONE = "none"


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


def resolve_auth_config() -> AuthConfig:
    """Build the `AuthConfig` from `ACC_WEBGUI_*` environment variables."""
    mode = os.environ.get("ACC_WEBGUI_AUTH_MODE", "").strip().lower() or MODE_NONE
    operator_users = tuple(
        u.strip() for u in os.environ.get("ACC_WEBGUI_OPERATOR_USERS", "").split(",")
        if u.strip()
    )
    return AuthConfig(
        mode=mode,
        operator_token=os.environ.get("ACC_WEBGUI_OPERATOR_TOKEN", ""),
        viewer_token=os.environ.get("ACC_WEBGUI_VIEWER_TOKEN", ""),
        operator_users=operator_users,
        oidc_issuer=os.environ.get("ACC_WEBGUI_OIDC_ISSUER", ""),
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
    """
    if cfg.mode == MODE_NONE and not _is_loopback(host):
        raise RuntimeError(
            f"acc-webgui refuses to bind {host!r} with no authentication. "
            f"Set ACC_WEBGUI_AUTH_MODE (oauth-proxy|oidc|token), or bind "
            f"127.0.0.1 for local-only development."
        )


# ---------------------------------------------------------------------------
# Principal extraction per mode
# ---------------------------------------------------------------------------


def _bearer(request: Request) -> str:
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return ""


def _principal(request: Request, cfg: AuthConfig) -> Principal | None:
    """Resolve the request's `Principal`, or None if unauthenticated."""
    if cfg.mode == MODE_NONE:
        # Loopback-only dev (enforce_bind_safety guaranteed loopback).
        return Principal(user="dev:localhost", role=ROLE_OPERATOR)

    if cfg.mode == MODE_OAUTH_PROXY:
        # The oauth-proxy sidecar has already authenticated the user and
        # injects identity headers; acc-webgui trusts them.
        user = (request.headers.get("X-Forwarded-Email")
                or request.headers.get("X-Forwarded-User") or "")
        if not user:
            return None
        role = ROLE_OPERATOR if user in cfg.operator_users else ROLE_VIEWER
        return Principal(user=user, role=role)

    if cfg.mode == MODE_TOKEN:
        token = _bearer(request)
        if token and token == cfg.operator_token:
            return Principal(user="token:operator", role=ROLE_OPERATOR)
        if token and token == cfg.viewer_token:
            return Principal(user="token:viewer", role=ROLE_VIEWER)
        return None

    if cfg.mode == MODE_OIDC:
        token = _bearer(request)
        if not token:
            return None
        claims = _verify_oidc(token, cfg)
        if claims is None:
            return None
        user = claims.get("email") or claims.get("sub") or "oidc:unknown"
        role = ROLE_OPERATOR if user in cfg.operator_users else ROLE_VIEWER
        return Principal(user=user, role=role)

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
    principal = _principal(request, cfg)
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
