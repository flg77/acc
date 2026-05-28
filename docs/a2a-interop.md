# A2A agent interop — phased build

Implements OpenSpec
[`20260527-a2a-agent-interop`](../openspec/changes/20260527-a2a-agent-interop/proposal.md)
([tasks](../openspec/changes/20260527-a2a-agent-interop/tasks.md))
— the A2A (Agent-to-Agent protocol) boundary gateway that makes ACC agents
first-class on the Kagenti / RHOAI agent mesh. Paired with
[`20260527-agentcard-discovery`](../openspec/changes/20260527-agentcard-discovery/proposal.md)
(the operator-side label that makes the card endpoint findable by Kagenti);
see [`docs/kagenti-discovery.md`](kagenti-discovery.md). Phased to land safely
without rushing the prerequisites.

## Phase status

| Phase | What it lands | Status |
|---|---|---|
| **1 — Agent Card generator** | `acc.a2a.build_agent_card()`: pure Python function turning a `RoleDefinitionConfig` + collective/agent context into a valid A2A Agent Card v1 dict. No I/O. | **Landed** |
| **1b — `/.well-known/agent-card.json` HTTP endpoint** | `acc.a2a.server.build_app` serves the card. Opt-in via `ACC_A2A_PORT`. Operator-side per-role K8s Service is a small follow-up (the in-pod endpoint is fully functional). | **Landed** |
| **2 — JSON-RPC inbound** | `POST /` accepts `message/send`; the handler calls `CognitiveCore.process_task` so Cat-A/B governance + oversight enforce identically. Blocked → JSON-RPC `GOVERNANCE_BLOCKED` (`-32001`), not a silent bypass. | **Landed** |
| **3 — Outbound A2A client** | `acc.a2a.client.call_peer` + the `select_transport` resolver. `try_a2a_delegation` composes them as the hub-gateway helper. | **Landed** |
| **4 — Hub-as-gateway** | `acc.agent._delegate_task` tries A2A first when `deploy_mode=rhoai` + a peer URL is configured (`AgentConfig.peer_a2a_urls`); falls back to the NATS bridge on transport failure; surfaces peer governance denials as blocked (no NATS fallback). | **Landed** |
| **5 — SPIRE JWT-SVID signing** | `acc.a2a.signing.{sign_card,verify_signed_card}` — wrap the card with a JWT-SVID, verify on the peer side against the SPIRE issuer key + trust domain (+ optional audience). Server opt-in via `ACC_A2A_JWT_SVID_PATH` + `ACC_A2A_TRUST_DOMAIN`. | **Landed** |

## What Phase 1 gives you

A reliable, vendor-neutral way to materialise the card document any future
phase will serve. It's pure: no env, no deps beyond `acc.config`, fully
unit-tested (`tests/test_a2a_card.py`).

```python
from acc.a2a import build_agent_card
from acc.role_loader import RoleLoader

role = RoleLoader(roles_root="roles", role_name="coding_agent").load()
card = build_agent_card(
    role=role,
    role_label="coding_agent",
    collective_id="sol-01",
    agent_id="coding-agent-9c1d",
    base_url="https://acc-coding-agent.sol-01.svc:8443",
)
# card is a JSON-serialisable dict ready for /.well-known/agent-card.json
```

## Card shape

A2A Agent Card v1 (the cross-vendor shape Kagenti / A2A clients consume) +
an `acc` vendor extension carrying ACC-specific metadata:

- `schemaVersion` — pinned (`"1.0"`), bumped explicitly on spec drift.
- `name` — `"<role_label>@<collective_id>"`.
- `description` — the role's `purpose`.
- `url` — the agent's JSON-RPC endpoint (caller-supplied; wired for real in
  Phase 1b/2).
- `version` — the role's `version`.
- `capabilities` — `{streaming, pushNotifications, stateTransitionHistory}`
  all `false` in Phase 1 (honest defaults; flip when actually wired).
- `defaultInputModes` / `defaultOutputModes` — `["text/plain"]`.
- `skills` — one entry per `task_types[i]` with role/persona/domain tags.
- `authentication.schemes` — `[]` until Phase 5 (SPIRE x5c / Keycloak).
- `acc` — vendor extension: role, collective id, agent id, persona, domain,
  flags (`reasoningTrace`, `memoryRetrieval`, `canRoute`, `workspaceAccess`),
  `governance.maxSkillRiskLevel` / `maxMcpRiskLevel`, `defaultOperatingMode`,
  and the OpenSpec change id.

## Honest caveats (after all phases)

- A2A is still **alpha**; `A2A_CARD_SCHEMA_VERSION` is the single point of
  truth — bump it (and re-validate against the spec) when A2A moves.
- The agent's HTTP endpoint is **plain HTTP** at the L4 layer. Production
  protection comes from (a) the Istio Ambient mesh's mTLS where present,
  and (b) Phase-5 JWT-SVID signing of the card payload itself. TLS at the
  HTTP layer (SPIRE-issued certs on the agent's listener) is a future add.
- The operator-side **per-role K8s Service** that exposes the in-pod port to
  cluster mesh peers is a small follow-up (the in-pod server is fully
  functional; only externally-reachable discovery needs the Service).
- Real **AgentCard CRD discovery** (peer URLs from Kagenti's CRD index
  rather than `peer_a2a_urls` config) is a separate change building on the
  Phase-1 operator label (see `docs/kagenti-discovery.md`).
- Peer-side **card verification** (using `verify_signed_card` to check a
  fetched card before talking to that peer) is a small client-side wiring
  step the outbound `call_peer` doesn't yet perform — currently it trusts
  the peer URL it was given. Recommended next addition.

## Cross-links

- Operator-side AgentCard label (Phase 1 of the discovery proposal):
  `docs/kagenti-discovery.md`.
- Mode-aware routing (NATS bridge stays for edge/standalone): vault note
  `A2A scope — ACC-9 bridge deprecation path`.
- Governance non-bypass requirement: vault note `A2A risk — governance bypass`.
