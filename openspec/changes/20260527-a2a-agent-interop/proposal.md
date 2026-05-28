# Proposal: A2A (Agent-to-Agent) interop — ACC ⇄ Kagenti mesh

| Field      | Value                                                                       |
|------------|-----------------------------------------------------------------------------|
| Change ID  | `20260527-a2a-agent-interop`                                                |
| Date       | 2026-05-27                                                                  |
| Status     | **Landed** (Phases 1, 1b, 2, 3, 4, 5)                                       |
| Depends on | SPIFFE/SPIRE PR-1..5 (workload identity + JWT-SVID); ACC-9 cross-collective bridge (kept as the edge/standalone fallback); the paired AgentCard change below for the operator-side label |
| Cross-refs | Code: `acc/a2a/{card,server,jsonrpc,client,signing}.py`, `acc/agent.py` (`_maybe_start_a2a_server`, `_maybe_delegate_via_a2a`, `_forward_bridge_result`), `acc/config.py` (`AgentConfig.peer_a2a_urls`). Docs: [`docs/a2a-interop.md`](../../../docs/a2a-interop.md), [`docs/kagenti-discovery.md`](../../../docs/kagenti-discovery.md). Pair: [`20260527-agentcard-discovery/`](../20260527-agentcard-discovery/) |

---

## Problem statement

ACC's intra-collective comms are **NATS** (msgpack), and cross-collective work
goes over the **ACC-9 `[DELEGATE:cid:reason]` bridge** — also NATS. Kagenti, the
Red Hat agent platform ACC targets on RHOAI, speaks **A2A**: an agent serves
`/.well-known/agent-card.json` (capability description), and peers call it over
**JSON-RPC 2.0 / HTTPS / TLS 1.3** with the card optionally **SPIRE-signed**.
Without an A2A adapter ACC is an island next to the mesh — peers can't discover
or call ACC roles; ACC can't call mesh peers via the standard protocol.

The *headline risk* of building this is **A2A becoming a softer governance
path**: a JSON-RPC entry point that bypasses Cat-A/B + the human-oversight
queue would be the opposite of ACC's value. The design constraint is therefore
that **every inbound A2A call funnels through the same `CognitiveCore.process_task`
pipeline a NATS TASK_ASSIGN does** — governance is structural, not bolted on.

The *connected* corollary: edge and standalone deployments cannot speak A2A
(no Istio Ambient, no Keycloak, no AgentCard CRD, intermittent connectivity).
A2A is the *boundary* gateway at the rhoai hub; the NATS bridge stays as the
edge/standalone transport. See the vault note `ACC RHOAI/Edge-Hub-A2A topology.md`
(rationale + diagram) and `ACC Openspec/scope-and-risk/A2A scope — ACC-9 bridge
deprecation path.md` (the demote-not-delete decision).

## Approach (as landed)

Phased so each piece is safe to ship alone and unblocks the next. All five
phases shipped on spearhead.

### Phase 1 — Agent Card generator (data mapping)
Pure Python function `acc.a2a.build_agent_card(role, *, role_label,
collective_id, agent_id, base_url) -> dict` producing a valid **A2A Agent
Card v1**: `schemaVersion`, `name` (`<role>@<collective>`), `description`
(from `role.purpose`), `url` (caller-supplied), `version`, `capabilities`
(streaming/push/state — all `false` in v1; honest defaults), default I/O
modes, `skills` (one per `task_type`, with role/persona/domain/skill tags),
`authentication.schemes` (empty until Phase 5), plus an **`acc` vendor
extension** carrying role/collective/agent identity, persona, domain, role
flags (`reasoningTrace`, `memoryRetrieval`, `canRoute`, `workspaceAccess`,
`maxParallelTasks`), governance ceilings (`maxSkillRiskLevel`,
`maxMcpRiskLevel`), `defaultOperatingMode`, and the OpenSpec change id.
`A2A_CARD_SCHEMA_VERSION` constant pinned in one place for drift control.

### Phase 1b — `/.well-known/agent-card.json` HTTP endpoint
`acc.a2a.server.build_app` constructs an `aiohttp` Application embedded
alongside the agent's NATS subscriber. Opt-in via `ACC_A2A_PORT`. Agent
`run()` starts the server when the env is set + the agent has a real role
+ CognitiveCore (skips for dormant workers, no-op if `acc[a2a]` extra
absent). `aiohttp` is shipped as a new optional `a2a` extra; existing
deployments are untouched.

### Phase 2 — JSON-RPC inbound, governance preserved
The same server accepts `POST /` JSON-RPC 2.0 `message/send` calls. Each call
is translated into a `task` dict shaped exactly like a NATS TASK_ASSIGN
(`task_id`, `content`, `target_role=<this role>`, `source: "a2a"`) and
dispatched **directly to `CognitiveCore.process_task(task, role)`** — the same
pipeline used by NATS-originated tasks (pre-gate → memory → prompt → LLM →
post-gate → persist → drift). Cat-A/B governance + oversight queue
*structurally* apply. A `blocked` result is surfaced as a **structured
JSON-RPC error code `GOVERNANCE_BLOCKED` (`-32001`)** with the missing
permission in `error.data.blockReason` — never a silent bypass. JSON-RPC
parse / invalid-request / method-not-found / invalid-params / internal-error
follow the standard codes (-32700 / -32600 / -32601 / -32602 / -32603).

### Phase 3 — Outbound A2A client + transport resolver
- `acc.a2a.client.call_peer(base_url, content, *, task_id, timeout, session)`
  issues a JSON-RPC `message/send` to a peer's A2A endpoint and returns the
  result dict, or raises **`A2AClientError`** (with `.code` + `.data`) on any
  failure. `is_governance_blocked` flags `GOVERNANCE_BLOCKED` distinctly so
  the caller does NOT retry that on the NATS bridge (a denial is a denial).
- `acc.a2a.client.select_transport(*, deploy_mode, target_cid, peer_urls,
  prefer_a2a=True) -> "a2a"|"nats"`: the **bridge-deprecation policy** as a
  pure function. `rhoai` + a reachable peer URL → `"a2a"`; everything else
  (edge / standalone / no-peer-URL / explicit override) → `"nats"`.
- Reachability fallback is the *caller's* responsibility (catch
  `A2AClientError` from the chosen A2A call, retry on NATS) — keeps the
  resolver pure and testable.

### Phase 4 — Hub-as-gateway wiring
- `AgentConfig.peer_a2a_urls: dict[str, str]` (env-friendly:
  `ACC_PEER_A2A_URLS='cid1=url1,cid2=url2'`). Empty default preserves legacy.
- `acc.a2a.client.try_a2a_delegation(...)` composes `select_transport` +
  `call_peer` + returns a *bridge-result-shaped* dict on success or peer
  governance denial, **`None`** on transport failure (caller falls back to
  NATS). This is the hub-as-gateway helper.
- `acc.agent.Agent._delegate_task` tries A2A *first* via
  `_maybe_delegate_via_a2a` before the existing NATS publish + future-wait
  path. On A2A success/denial, `_forward_bridge_result` emits the same
  `TASK_COMPLETE` shape NATS would have produced — observability stays
  identical regardless of transport. On A2A transport failure, the NATS
  path runs as before.

### Phase 5 — SPIRE JWT-SVID card signing
ACC reuses the **JWT-SVID** SPIRE issues via the `spiffe-helper` sidecar (already
wired by PR-1..5 / proposal 011) — no new key material handling.
- `acc.a2a.signing.sign_card(card, jwt_svid)` → `{"card": ..., "svid": <jwt>}`.
- `acc.a2a.signing.verify_signed_card(envelope, *, issuer_key,
  expected_trust_domain, expected_audience=None, algorithms=None,
  leeway_s=30) -> card`. Enforces: JWT signature against `issuer_key`,
  expiry / nbf / iat, required claims (`exp`/`iat`/`sub`), the SPIFFE id's
  trust domain matches `expected_trust_domain`, optional audience. Distinct
  `CardVerificationError` exception so a peer denial is a denial — not
  retried on a different transport.
- `acc.a2a.signing.spire_x5c_scheme(trust_domain)` produces the entry that
  goes into `card.authentication.schemes` so a peer knows how to verify.
- Server opt-in via `ACC_A2A_JWT_SVID_PATH` + `ACC_A2A_TRUST_DOMAIN`. When set,
  `GET /.well-known/agent-card.json` returns the **signed envelope** and the
  card's `authentication.schemes` advertises `spire-jwt-svid`. The SVID is
  **re-read on every GET** — `spiffe-helper` rotates it; a cached one would
  fail a peer's verify. Unreadable file → log + serve the *unsigned* card
  (graceful degrade beats a hard failure).
- No new deps: `pyjwt` is already in core deps for the wider
  `signing_mode=spiffe` work; `cryptography` is too.

## Out of scope (deferred follow-ups)

- **Operator-side per-role Kubernetes `Service`** to expose the in-pod A2A
  port to cluster mesh peers. The server itself is fully functional; only
  externally-reachable discovery needs the Service. Small follow-up.
- **AgentCard CRD discovery on the outbound side** — using Kagenti's CRD
  index to resolve peer URLs instead of the static `peer_a2a_urls` config.
  Builds on the AgentCard label from the paired change.
- **Peer-side card verification** wired into the outbound `call_peer` (the
  `verify_signed_card` helper is shipped + tested; the *call site* still
  trusts the configured URL). A few lines to wire; recommended next step.
- **TLS at the HTTP layer** (SPIRE-issued certs on the agent's listener).
  Today's transport is plain HTTP at L4 + Istio Ambient mTLS where present +
  JWT-SVID signing of the card payload. Real TLS termination is a future add.
- **Bridge deprecation** — A2A becomes the default cross-collective transport
  on rhoai, but the NATS bridge is retained indefinitely for edge / standalone
  (and as the reachability fallback in mixed clusters). See vault note
  `ACC Openspec/scope-and-risk/A2A scope — ACC-9 bridge deprecation path.md`.

## Risks (with mitigations as landed)

- **Governance bypass via the A2A entry point** — the day-one must-fix.
  *Mitigated*: the inbound JSON-RPC handler calls `CognitiveCore.process_task`
  directly; there is no second authorisation path. Blocked → JSON-RPC
  `GOVERNANCE_BLOCKED` (-32001) with the missing permission. Same on the
  outbound side: `A2AClientError.is_governance_blocked` is sticky (do not
  retry on NATS). Vault analysis: `A2A risk — governance bypass`.
- **A2A protocol drift (alpha)** — the spec + Kagenti CRD shape are moving
  (`v0.2.0-alpha.21` rebased AgentCard). *Mitigated*: `A2A_CARD_SCHEMA_VERSION`
  pinned in one place; schema knowledge isolated to `acc/a2a/`; conformance
  tests pin the wire shape (`name`, `description`, etc. are asserted exact).
- **Dual-protocol cost** — running NATS bridge + A2A simultaneously means two
  surfaces to test/secure. *Mitigated*: single seam (`[DELEGATE:cid:reason]`),
  one resolver (`select_transport`), and `_forward_bridge_result` so both
  paths produce identical `TASK_COMPLETE` signals on the local bus. Vault:
  `A2A risk — dual-protocol cost`.
- **Telemetry/cardinality** — per-call spans + retries multiply OTel volume
  in busy meshes. *Mitigated*: reuse the existing reasoning truncation +
  `BatchSpanProcessor`; advertise honest `capabilities` (`streaming`,
  `pushNotifications`, `stateTransitionHistory` all `false`) so peers don't
  expect higher-throughput modes.

## Verification (landed)

- **Phase 1 (card)** — `tests/test_a2a_card.py`, **20 tests** covering
  required A2A top-level fields, JSON-serialisability, pinned schema version,
  field sourcing from the role, honest Phase-1 defaults
  (`capabilities`/`auth`), skill mapping per `task_type` with role/persona/
  domain/default-skills tags, empty-task-types edge case, ACC vendor
  extension (identity, flags, governance, OpenSpec id), purpose stripping,
  optional domain tag, end-to-end shape against the shipped Assistant role.
- **Phase 1b + 2 (server)** — `tests/test_a2a_server.py`, **14 tests**
  (aiohttp-gated): GET card; JSON-RPC `message/send` success;
  `params.message.content` (A2A spec shape); task-id synthesis;
  **`GOVERNANCE_BLOCKED` contract** (blocked → 403 + structured error, no
  silent bypass); all standard JSON-RPC error codes; env helpers
  (`env_port`, `env_base_url`).
- **Phase 3 + 4 (client + hub-gateway)** — `tests/test_a2a_client.py`,
  **13 tests**: pure `select_transport` matrix (always run); aiohttp-gated
  `call_peer` (success, taskId synth, JSON-RPC error code/data propagation,
  `is_governance_blocked`, HTTP error, connection-refused); hub-gateway
  `try_a2a_delegation` (`None` on transport=nats, success shape, fallback to
  NATS on transport failure, blocked on governance denial — *not* fall back).
- **Phase 5 (signing)** — `tests/test_a2a_signing.py`, **14 tests** using
  `cryptography` + `pyjwt`: sign wraps correctly; rejects empty SVID;
  `read_jwt_svid_file` trims; `spire_x5c_scheme` carries trust domain; verify
  happy path; rejects wrong trust domain, expired, bad signature (different
  key), wrong audience, malformed envelope, empty SVID, missing `sub` claim;
  `spiffe_id_trust_domain` parser.
- **Aggregate**: **42 passed, 10 aiohttp-gated skipped** locally (CI /
  container with `acc[a2a]` runs them); `acc[a2a]` extra installs `aiohttp`
  only when wanted.
- **End-to-end against a live Kagenti operator** is still a deferred spike
  (out of scope above).

## See also

- User-facing docs: [`docs/a2a-interop.md`](../../../docs/a2a-interop.md),
  [`docs/kagenti-discovery.md`](../../../docs/kagenti-discovery.md).
- Paired change: [`20260527-agentcard-discovery`](../20260527-agentcard-discovery/)
  (the operator-side label that makes the card endpoint findable by Kagenti).
- Vault scope/risk analyses:
  `ACC Openspec/scope-and-risk/A2A risk — governance bypass.md`,
  `A2A scope — identity convergence (SPIRE + Keycloak).md`,
  `A2A scope — ACC-9 bridge deprecation path.md`,
  and the rest under `ACC Openspec/scope-and-risk/`.
- Topology + decision rationale: vault note `ACC RHOAI/Edge-Hub-A2A topology.md`.
