"""ACC ↔ A2A (Agent-to-Agent protocol) interop.

OpenSpec: ``20260527-a2a-agent-interop``.

The A2A boundary gateway that makes ACC agents first-class citizens of the
Kagenti / RHOAI agent mesh.  Phased build:

- **Phase 1 (this module — ``card.py``):** the pure card generator.  Produces
  an A2A Agent Card v1 document from a :class:`RoleDefinitionConfig` plus
  collective/agent context.  No I/O — feeds future phases (HTTP server,
  JSON-RPC translator, outbound client).
- **Phase 1b (next):** serve ``/.well-known/agent-card.json`` from the agent
  process.
- **Phase 2:** JSON-RPC 2.0 inbound endpoint translating to NATS TASK_ASSIGN,
  governance gates intact.
- **Phase 3:** outbound A2A client mapping ACC-9 ``[DELEGATE:cid:reason]`` to
  A2A calls on a discovered peer.
- **Phase 4:** the hub-as-gateway translation NATS-bridge ⇄ A2A.
- **Phase 5:** SPIRE x5c card signing + identity convergence.

See also ``docs/kagenti-discovery.md`` (the operator-side AgentCard label) and
the openspec note linked at the top.
"""

from .card import build_agent_card

# Phases 1b/2: the inbound HTTP + JSON-RPC server.  Lazy-imported via the
# ``a2a`` extra so installing acc without the extra never pulls aiohttp.
# Users import these directly: ``from acc.a2a.server import build_app, start_server``.

__all__ = ["build_agent_card"]
