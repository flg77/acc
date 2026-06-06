"""ACC ↔ A2A (Agent-to-Agent protocol) interop.

OpenSpec: `openspec/changes/20260527-a2a-agent-interop/`
(`proposal.md` + `tasks.md`).  User-facing docs: ``docs/a2a-interop.md``.
Paired operator-side change: ``openspec/changes/20260527-agentcard-discovery/``
(``docs/kagenti-discovery.md``).

The A2A boundary gateway that makes ACC agents first-class citizens of the
Kagenti / RHOAI agent mesh.  All five build phases shipped on spearhead:

- **Phase 1 — Agent Card generator** (:mod:`acc.a2a.card`): pure data mapping
  ``RoleDefinitionConfig + context → A2A Agent Card v1 dict``.  No I/O.
- **Phase 1b — Card HTTP endpoint** (:mod:`acc.a2a.server`): ``aiohttp`` server
  serving ``GET /.well-known/agent-card.json``.  Opt-in via ``ACC_A2A_PORT``.
- **Phase 2 — JSON-RPC inbound, governance intact** (same ``server``): ``POST /``
  ``message/send`` → ``CognitiveCore.process_task`` (the SAME pipeline NATS uses,
  so Cat-A/B + oversight enforce structurally).  Blocked → JSON-RPC
  ``GOVERNANCE_BLOCKED (-32001)`` — never a silent bypass.
- **Phase 3 — Outbound client + transport resolver** (:mod:`acc.a2a.client`):
  ``call_peer(...)`` + the pure ``select_transport(...)`` policy.
- **Phase 4 — Hub-as-gateway** (``acc.agent._delegate_task`` +
  :func:`acc.a2a.client.try_a2a_delegation`): mode-aware ``[DELEGATE:cid:reason]``
  routing — rhoai+peer → A2A; else / failure → NATS bridge; governance denials
  do NOT fall back.
- **Phase 5 — SPIRE JWT-SVID card signing** (:mod:`acc.a2a.signing`):
  ``sign_card`` / ``verify_signed_card`` enforce SPIFFE trust domain + expiry
  + audience.  Server opt-in via ``ACC_A2A_JWT_SVID_PATH`` +
  ``ACC_A2A_TRUST_DOMAIN``.

aiohttp is shipped as the optional ``acc[a2a]`` extra so the rest of ACC
imports without it; the server / client modules import ``aiohttp`` lazily
inside their functions.  ``pyjwt`` + ``cryptography`` (for signing) are in
core deps.
"""

from .card import build_agent_card
from .federation import (
    FederationCache,
    PeerCardEntry,
    discover_peer_cards,
)

__all__ = [
    "build_agent_card",
    "FederationCache",
    "PeerCardEntry",
    "discover_peer_cards",
]
