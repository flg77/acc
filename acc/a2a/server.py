"""A2A inbound HTTP server — Phases 1b + 2 of OpenSpec 20260527-a2a-agent-interop.

Embeds a small ``aiohttp`` server alongside the ACC agent's NATS subscriber to:

- **Phase 1b** — serve the agent's :func:`acc.a2a.build_agent_card` document at
  ``GET /.well-known/agent-card.json``.
- **Phase 2** — accept **JSON-RPC 2.0** ``message/send`` calls at ``POST /``
  and translate each into a direct :meth:`CognitiveCore.process_task` invocation.

A2A is *transport*, not *authorisation*: every inbound call funnels through the
**same** CognitiveCore pipeline an in-process / NATS TASK_ASSIGN would (pre-gate
→ memory → prompt → LLM → post-gate → persist → drift).  Cat-A/B governance and
the human-oversight queue therefore apply identically; the JSON-RPC handler
surfaces a ``blocked`` result as a structured error rather than masking it.

Opt-in: the agent starts this server only when ``ACC_A2A_PORT`` is set.  No
default port binding — existing deployments are unaffected.  TLS / SPIRE x5c
signing arrive in Phase 5; Phase 1b/2 ship plain HTTP behind the
Istio Ambient mesh's mTLS where present.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Optional, TYPE_CHECKING

from .card import build_agent_card
from .jsonrpc import (
    GOVERNANCE_BLOCKED,
    INTERNAL_ERROR,
    INVALID_PARAMS,
    INVALID_REQUEST,
    METHOD_NOT_FOUND,
    PARSE_ERROR,
    error,
    parse_request,
    success,
)

if TYPE_CHECKING:
    from acc.cognitive_core import CognitiveCore
    from acc.config import RoleDefinitionConfig

logger = logging.getLogger(__name__)

# The A2A method we honour.  Recent A2A specs converge on ``message/send`` for
# the "send a request, wait for a single result" pattern; we ship one method
# in Phase 2 and add streaming / tasks/* methods alongside in later phases.
METHOD_MESSAGE_SEND = "message/send"


def build_app(
    *,
    core: "CognitiveCore",
    role: "RoleDefinitionConfig",
    role_label: str,
    collective_id: str,
    agent_id: str,
    base_url: str,
):
    """Construct the aiohttp Application for an ACC agent's A2A endpoint.

    Kept import-light: ``aiohttp`` is imported lazily so the rest of ACC
    (which does not depend on ``aiohttp``) is not forced to install the extra.
    """
    import aiohttp.web as web  # noqa: PLC0415 — lazy: extra-gated

    card = build_agent_card(
        role=role,
        role_label=role_label,
        collective_id=collective_id,
        agent_id=agent_id,
        base_url=base_url,
    )

    # Phase 5 — SPIRE JWT-SVID card signing.  When ACC_A2A_JWT_SVID_PATH +
    # ACC_A2A_TRUST_DOMAIN are set, the card is returned as a signed envelope
    # {"card": ..., "svid": <JWT>} and the spire-jwt-svid scheme is advertised
    # in authentication.schemes so a peer knows how to verify.  The JWT-SVID
    # is read FRESH on every GET — spiffe-helper rotates it; we never cache.
    jwt_svid_path = os.environ.get("ACC_A2A_JWT_SVID_PATH", "").strip()
    trust_domain = os.environ.get("ACC_A2A_TRUST_DOMAIN", "").strip()
    if jwt_svid_path and trust_domain:
        from .signing import spire_x5c_scheme  # noqa: PLC0415
        card.setdefault("authentication", {}).setdefault("schemes", []).append(
            spire_x5c_scheme(trust_domain),
        )
        app_signing = {"jwt_svid_path": jwt_svid_path, "trust_domain": trust_domain}
    else:
        app_signing = None

    app = web.Application()
    app["card"] = card
    app["core"] = core
    app["role"] = role
    app["role_label"] = role_label
    app["signing"] = app_signing

    async def handle_card(request):
        # Sign on-demand so the SVID is always the latest spiffe-helper write
        # (SVIDs rotate; serving a stale one would fail a peer's verify).
        signing_cfg = request.app.get("signing")
        if signing_cfg is None:
            return web.json_response(request.app["card"])
        from .signing import read_jwt_svid_file, sign_card  # noqa: PLC0415
        try:
            svid = read_jwt_svid_file(signing_cfg["jwt_svid_path"])
        except OSError as exc:
            logger.warning(
                "a2a: signing enabled but JWT-SVID unreadable (%s); serving unsigned",
                exc,
            )
            return web.json_response(request.app["card"])
        signed = sign_card(request.app["card"], svid)
        return web.json_response(signed)

    async def handle_jsonrpc(request):
        try:
            body = await request.json()
        except Exception:
            return web.json_response(
                error(None, PARSE_ERROR, "Parse error: body is not valid JSON"),
                status=400,
            )

        err_msg, method, params, req_id = parse_request(body)
        if err_msg is not None:
            return web.json_response(error(req_id, INVALID_REQUEST, err_msg), status=400)

        if method != METHOD_MESSAGE_SEND:
            return web.json_response(
                error(req_id, METHOD_NOT_FOUND, f"Method not found: {method!r}"),
                status=404,
            )

        # A2A ``message/send`` params: minimal contract is a ``message`` object
        # with ``content`` (text).  We accept either ``params.content`` (flat,
        # convenient for testing) or ``params.message.content`` (A2A spec).
        msg_obj = params.get("message") if isinstance(params, dict) else None
        content = (params.get("content") if isinstance(params, dict) else None) \
            or (msg_obj.get("content") if isinstance(msg_obj, dict) else None)
        if not content or not isinstance(content, str):
            return web.json_response(
                error(req_id, INVALID_PARAMS,
                      "Invalid params: 'content' (str) is required, "
                      "directly or under 'message.content'"),
                status=400,
            )

        task_id = (params.get("taskId") if isinstance(params, dict) else None) \
            or _synth_task_id(req_id)

        # Translate to the SAME payload shape the NATS TASK_ASSIGN path uses.
        # ``source: a2a`` tags the entry point for observability; it does not
        # influence governance — Cat-A/B + oversight gates run identically.
        # target_role pins this agent's own role so the cognitive core's
        # downstream filters behave as if the call came from a directed
        # TASK_ASSIGN to this role.
        task = {
            "task_id": task_id,
            "content": content,
            "target_role": request.app["role_label"],
            "source": "a2a",
        }

        try:
            result = await request.app["core"].process_task(task, role=request.app["role"])
        except Exception as exc:  # noqa: BLE001 — surface as JSON-RPC server error
            logger.exception("a2a: process_task raised: %s", exc)
            return web.json_response(
                error(req_id, INTERNAL_ERROR, f"Server error: {exc}"),
                status=500,
            )

        # Governance: blocked → structured JSON-RPC error, NOT a silent bypass.
        # Distinct error code lets the caller distinguish "denied by ACC's
        # governance" from "internal failure".  See OpenSpec scope-and-risk
        # analysis "A2A risk — governance bypass" — this is the design
        # constraint that proves A2A is not a softer path.
        if getattr(result, "blocked", False):
            return web.json_response(
                error(
                    req_id, GOVERNANCE_BLOCKED,
                    "Blocked by ACC governance",
                    data={
                        "taskId": task_id,
                        "blockReason": getattr(result, "block_reason", "") or "",
                        "reasoning": getattr(result, "reasoning", "") or "",
                    },
                ),
                status=403,
            )

        return web.json_response(success(req_id, {
            "taskId": task_id,
            "output": getattr(result, "output", "") or "",
            "reasoning": getattr(result, "reasoning", "") or "",
            "routeTo": getattr(result, "route_to", "") or "",
        }))

    app.router.add_get("/.well-known/agent-card.json", handle_card)
    app.router.add_post("/", handle_jsonrpc)
    return app


def _synth_task_id(req_id: Any) -> str:
    """Synthesize a stable task id from a JSON-RPC id when the caller didn't
    pass one — keeps the ACC-side correlation traceable to the A2A request."""
    import uuid  # noqa: PLC0415
    if req_id is None:
        return f"a2a-{uuid.uuid4().hex[:12]}"
    return f"a2a-{req_id}-{uuid.uuid4().hex[:8]}"


# --------------------------------------------------------------------------
# Lifecycle helpers — start / stop the server on the agent's event loop.
# --------------------------------------------------------------------------


async def start_server(app, host: str, port: int):
    """Start the aiohttp app on ``host:port`` and return the runner.

    The caller is expected to ``await runner.cleanup()`` at shutdown.
    """
    import aiohttp.web as web  # noqa: PLC0415

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host=host, port=port)
    await site.start()
    logger.info("a2a: serving on http://%s:%d (Phase 1b/2 — plain HTTP)", host, port)
    return runner


def env_port() -> Optional[int]:
    """Return ``ACC_A2A_PORT`` parsed as int, or ``None`` if unset/invalid.

    The agent uses this to decide whether to start the A2A server at all —
    opt-in, default off.
    """
    raw = os.environ.get("ACC_A2A_PORT", "").strip()
    if not raw:
        return None
    try:
        port = int(raw)
        if 1 <= port <= 65535:
            return port
    except ValueError:
        pass
    logger.warning("a2a: ACC_A2A_PORT=%r is not a valid port; A2A server disabled", raw)
    return None


def env_host() -> str:
    return os.environ.get("ACC_A2A_HOST", "0.0.0.0").strip() or "0.0.0.0"


def env_base_url(default_host: str, default_port: int) -> str:
    """Card ``url`` (the JSON-RPC endpoint).  ``ACC_A2A_BASE_URL`` overrides;
    otherwise we publish ``http://<host>:<port>`` (Phase 1b/2 ships plain HTTP;
    Phase 5 adds TLS via SPIRE-issued certs and this URL becomes https://)."""
    override = os.environ.get("ACC_A2A_BASE_URL", "").strip()
    if override:
        return override
    return f"http://{default_host}:{default_port}"
