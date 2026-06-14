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
ROLE_PUBLISHER = "publisher"  # 023/027 — author + publish signed packs

# Tier ladder: a higher rank satisfies every lower gate (publisher ⊇ operator
# ⊇ viewer).  Group→tier mapping picks the HIGHEST tier a principal's groups
# match.  Whether a platform-admin group ALSO publishes is purely a config
# choice (add it to the publisher mapping) — the code keeps a linear ladder.
_ROLE_RANK = {ROLE_VIEWER: 0, ROLE_OPERATOR: 1, ROLE_PUBLISHER: 2}


def _role_satisfies(have: str, need: str) -> bool:
    """True when the *have* tier is at least the *need* tier on the ladder."""
    return _ROLE_RANK.get(have, -1) >= _ROLE_RANK.get(need, 99)


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
    # OIDC / Keycloak — audience (the Keycloak client_id) is validated when set;
    # groups_claim names the token claim carrying group/role names; the
    # group→tier map drives RBAC from Keycloak realm roles + groups (023/027).
    oidc_audience: str = ""
    oidc_groups_claim: str = "groups"
    # tier -> frozenset of group/role names that grant it. The highest tier
    # whose set intersects the principal's groups wins; empty map => fall back
    # to the operator_users static list (role_for).
    group_mappings: Mapping[str, frozenset[str]] = dataclasses.field(default_factory=dict)
    # htpasswd mode
    htpasswd_path: str = ""
    session_secret: str = ""
    session_ttl: int = _DEFAULT_SESSION_TTL
    # mtls mode
    mtls_header: str = "x-client-cert-subject"
    mtls_verify_header: str = "x-client-cert-verify"


def _parse_group_mappings(raw: str) -> dict[str, frozenset[str]]:
    """Parse ``ACC_WEBGUI_GROUP_MAPPINGS`` into ``{tier: {groups}}``.

    Format (semicolon-separated tiers, comma-separated groups)::

        operator=acc-operators;publisher=acc-publishers,acc-release

    Only the known tiers (viewer/operator/publisher) are kept; unknown
    tier names are skipped with a warning.  Empty/blank → ``{}`` (the
    operator_users static list then governs roles, preserving pre-023
    behaviour).
    """
    out: dict[str, frozenset[str]] = {}
    for clause in raw.split(";"):
        clause = clause.strip()
        if not clause or "=" not in clause:
            continue
        tier, _, groups = clause.partition("=")
        tier = tier.strip().lower()
        if tier not in _ROLE_RANK:
            logger.warning("webgui: unknown tier %r in ACC_WEBGUI_GROUP_MAPPINGS — skipped", tier)
            continue
        names = frozenset(g.strip() for g in groups.split(",") if g.strip())
        if names:
            out[tier] = names
    return out


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

    # OpenSpec follow-up to v0.3.48 lighthouse smoke: warn loud at
    # startup when ``ACC_WEBGUI_HTPASSWD_PATH`` points at a path that
    # doesn't exist (typical bug: the operator exports the host path
    # rather than the in-container path; compose then propagates it and
    # every login silently 401s).  We do NOT raise here — an htpasswd
    # file can be created post-boot — but the WARNING line lands in
    # `podman logs` so the operator notices before the first login.
    htpasswd_path = os.environ.get("ACC_WEBGUI_HTPASSWD_PATH", "").strip()
    if mode == MODE_HTPASSWD and htpasswd_path:
        if not os.path.isfile(htpasswd_path):
            logger.warning(
                "webgui: ACC_WEBGUI_HTPASSWD_PATH=%r does not exist or is not "
                "a file at startup. If you mounted the htpasswd into the "
                "container at a different path, set the env var to the "
                "IN-CONTAINER path (typically /app/acc-webgui.htpasswd). "
                "Every login will return 401 until this is corrected.",
                htpasswd_path,
            )
        elif not os.access(htpasswd_path, os.R_OK):
            logger.warning(
                "webgui: ACC_WEBGUI_HTPASSWD_PATH=%r exists but is not "
                "readable by the webgui process (uid=%d). Login will "
                "return 401 until the file is readable.",
                htpasswd_path, os.geteuid(),
            )
    if mode == MODE_HTPASSWD and not htpasswd_path:
        logger.warning(
            "webgui: ACC_WEBGUI_AUTH_MODE=htpasswd but "
            "ACC_WEBGUI_HTPASSWD_PATH is unset; every login will return 401."
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
        oidc_audience=os.environ.get("ACC_WEBGUI_OIDC_AUDIENCE", "").strip(),
        oidc_groups_claim=os.environ.get("ACC_WEBGUI_OIDC_GROUPS_CLAIM", "groups").strip() or "groups",
        group_mappings=_parse_group_mappings(os.environ.get("ACC_WEBGUI_GROUP_MAPPINGS", "")),
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


def _extract_groups(claims: Mapping, cfg: AuthConfig) -> set[str]:
    """Collect group/role names from an OIDC token's claims.

    Handles Keycloak's three shapes at once: the configured groups claim
    (a "groups" mapper — paths like ``/acc-operators`` are normalised by
    stripping the leading slash), ``realm_access.roles`` (realm roles),
    and ``resource_access.<client>.roles`` (client roles for our
    audience).  Works for any standard OIDC ``groups`` claim too.
    """
    groups: set[str] = set()
    raw = claims.get(cfg.oidc_groups_claim)
    if isinstance(raw, list):
        groups.update(str(g).lstrip("/") for g in raw)
    elif isinstance(raw, str) and raw:
        groups.add(raw.lstrip("/"))
    realm = claims.get("realm_access")
    if isinstance(realm, dict) and isinstance(realm.get("roles"), list):
        groups.update(str(r) for r in realm["roles"])
    res = claims.get("resource_access")
    if isinstance(res, dict) and cfg.oidc_audience:
        client = res.get(cfg.oidc_audience)
        if isinstance(client, dict) and isinstance(client.get("roles"), list):
            groups.update(str(r) for r in client["roles"])
    return groups


def role_from_claims(claims: Mapping, user: str, cfg: AuthConfig) -> str:
    """Resolve a tier from an OIDC principal's group/role claims.

    When a group→tier map is configured (Keycloak/OIDC, 023/027), the
    HIGHEST tier whose group set the principal belongs to wins; no match
    → viewer.  When no map is configured, fall back to the
    ``operator_users`` static list (`role_for`) so pre-023 deployments
    are unchanged.
    """
    if not cfg.group_mappings:
        return role_for(user, cfg)
    groups = _extract_groups(claims, cfg)
    best = ROLE_VIEWER
    for tier, names in cfg.group_mappings.items():
        if names & groups and _ROLE_RANK.get(tier, -1) > _ROLE_RANK.get(best, -1):
            best = tier
    return best


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
        # injects identity headers; acc-webgui trusts them.  When the proxy
        # is configured to pass groups (`--pass-groups`, e.g. from a
        # Keycloak-backed OpenShift OAuth IdP) and a group→tier map exists,
        # derive the tier from those groups; else the operator_users list.
        user = (headers.get("X-Forwarded-Email")
                or headers.get("X-Forwarded-User") or "")
        if not user:
            return None
        fwd_groups = headers.get("X-Forwarded-Groups", "").strip()
        if fwd_groups and cfg.group_mappings:
            claims = {cfg.oidc_groups_claim:
                      [g.strip() for g in fwd_groups.split(",") if g.strip()]}
            return Principal(user=user, role=role_from_claims(claims, user, cfg))
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
        # Keycloak commonly carries the human id in preferred_username.
        user = (claims.get("email") or claims.get("preferred_username")
                or claims.get("sub") or "oidc:unknown")
        return Principal(user=user, role=role_from_claims(claims, user, cfg))

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


def _audience_ok(claims: Mapping, audience: str) -> bool:
    """True when *audience* (the Keycloak client_id) appears in the token's
    ``aud`` (list or string — ID tokens) or equals ``azp`` (access tokens).
    """
    aud = claims.get("aud")
    auds = aud if isinstance(aud, list) else ([aud] if aud else [])
    return audience in auds or audience == claims.get("azp")


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
        claims.validate()  # enforces exp/nbf/iat
        # Audience check (when a Keycloak client_id is configured): Keycloak
        # carries the client in `aud` for ID tokens and in `azp` for access
        # tokens, so accept either — but require one to match.  Without this,
        # a valid token for ANY client of the same realm would be accepted.
        if cfg.oidc_audience and not _audience_ok(claims, cfg.oidc_audience):
            logger.warning(
                "webgui: OIDC token audience mismatch (aud=%s azp=%s, want %s)",
                claims.get("aud"), claims.get("azp"), cfg.oidc_audience)
            return None
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
    """Dependency — a principal at the operator tier or above (publisher
    satisfies it on the ladder)."""
    principal = require_viewer(request)
    if not _role_satisfies(principal.role, ROLE_OPERATOR):
        raise HTTPException(status_code=403,
                            detail="operator role required for this action")
    return principal


def require_publisher(request: Request) -> Principal:
    """Dependency — a principal at the publisher tier (the top of the
    ladder).  Gates publishing signed packs to a catalog (020 WS-C3)."""
    principal = require_viewer(request)
    if not _role_satisfies(principal.role, ROLE_PUBLISHER):
        raise HTTPException(status_code=403,
                            detail="publisher role required to publish")
    return principal


def authenticate_websocket(websocket, cfg: AuthConfig) -> Principal | None:
    """Resolve the `Principal` for a WebSocket upgrade request.

    Reuses `_principal()` but also reads a `?token=` query parameter —
    browsers cannot set an `Authorization` header on a WebSocket.  The
    viewer role suffices: the WS streams read-only snapshots.
    """
    return _principal(websocket.headers, websocket.query_params, cfg)
