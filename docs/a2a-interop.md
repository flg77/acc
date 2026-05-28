# A2A agent interop — phased build

Implements OpenSpec `20260527-a2a-agent-interop` — the A2A (Agent-to-Agent
protocol) boundary gateway that makes ACC agents first-class on the Kagenti /
RHOAI agent mesh. Phased to land safely without rushing the prerequisites.

## Phase status

| Phase | What it lands | Status |
|---|---|---|
| **1 — Agent Card generator** *(this)* | `acc.a2a.build_agent_card()`: pure Python function turning a `RoleDefinitionConfig` + collective/agent context into a valid A2A Agent Card v1 dict. No I/O. | **Landed** |
| 1b — `/.well-known/agent-card.json` HTTP endpoint | Serve the card dict over HTTPS from the agent process; operator creates per-role K8s Service. | Planned |
| 2 — JSON-RPC inbound | JSON-RPC 2.0 endpoint translating to NATS `TASK_ASSIGN` (target_role), waits for `TASK_COMPLETE`, returns the result. Governance (Cat-A/B + oversight) enforced. | Planned |
| 3 — Outbound A2A client | Map ACC-9 `[DELEGATE:cid:reason]` to an A2A call on a peer discovered via AgentCard CRD. | Planned |
| 4 — Hub-as-gateway | NATS-bridge ⇄ A2A translation at the rhoai hub, so edge/standalone reach the mesh without speaking A2A. See [Edge ⇄ Hub ⇄ A2A topology](../../Notes/...AgenticCellCorpus/ACC%20RHOAI/Edge-Hub-A2A%20topology.md) in the vault. | Planned |
| 5 — Identity convergence | SPIRE x5c card signing + Keycloak token exchange; populate the card's `authentication.schemes`. | Planned |

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

## Honest caveats (Phase 1 only)

- The generator is *pure data mapping* — no HTTP server exists yet, so the
  card cannot be fetched from a running collective. Phase 1b adds that.
- `authentication.schemes` is intentionally empty — *do not* enable Kagenti
  auto-discovery in production until Phase 5 lands the SPIRE x5c signing.
- A2A is still **alpha**; the pinned `A2A_CARD_SCHEMA_VERSION` is the single
  point of truth — bump it (and re-validate against the spec) when A2A moves.

## Cross-links

- Operator-side AgentCard label (Phase 1 of the discovery proposal):
  `docs/kagenti-discovery.md`.
- Mode-aware routing (NATS bridge stays for edge/standalone): vault note
  `A2A scope — ACC-9 bridge deprecation path`.
- Governance non-bypass requirement: vault note `A2A risk — governance bypass`.
