# Proposal: ACC TUI Evolution — Multi-Screen Operator Console

| Field      | Value                                               |
|------------|-----------------------------------------------------|
| Change ID  | 20260427-acc-tui-evolution                          |
| Date       | 2026-04-27                                          |
| Status     | Draft                                               |
| Branch     | `feature/ACC-tui-evolution`                         |
| Depends on | ACC-10, ACC-11, ACC-12, Enterprise Roles            |

---

## Problem Statement

The current TUI (ACC-6b) is a functional but minimal 2-screen application. Since its
design, three significant platform generations have landed: ACC-10 added 8 new signal
types (TASK_PROGRESS, QUEUE_STATUS, BACKPRESSURE, PLAN, KNOWLEDGE_SHARE, EVAL_OUTCOME,
CENTROID_UPDATE, EPISODE_NOMINATE); ACC-11 introduced domain-aware roles with per-domain
drift scores; ACC-12 added compliance guardrails with OWASP grading and a human oversight
queue. None of these capabilities are surfaced in the TUI.

The requirements document further extends the scope: the TUI should be the single operator
console for LLM endpoints, compliance & governance observability, red teaming, A2A
communication analysis, roles/profiles, skills, MCP management, and performance monitoring
— across both standalone/edge (podman-compose) and RHOAI (operator-managed) deployments.
The existing 2-screen flat architecture cannot accommodate this breadth without becoming
unmanageable.

## Current Behavior

- `acc/tui/app.py` — 2-screen Textual app (`DashboardScreen`, `InfuseScreen`)
- `acc/tui/client.py` — `NATSObserver` routes 3 signal types (HEARTBEAT, TASK_COMPLETE, ALERT_ESCALATE)
- `acc/tui/models.py` — `CollectiveSnapshot` / `AgentSnapshot` missing ACC-10/11/12 fields
- `acc/tui/screens/infuse.py` — `_ROLES` and `_TASK_TYPES` hardcoded (5 roles, 3 task types)
- `container/production/Containerfile.tui` — correct UBI10 production build; exists but not yet wired into operator
- TUI is not reachable via the RHOAI operator; no WebUI integration path defined

## Desired Behavior

A **6-screen console** organised around the biological metaphor: each screen maps to a
distinct functional layer of the ACC organism. Navigation is always accessible from a
top-level bar. The NATS observer handles all 11 current signal types via a pluggable
registry. Snapshot models carry ACC-10/11/12 fields. Role discovery is dynamic (from
`roles/`). A lightweight HTTP JSON bridge allows a future WebUI to consume live snapshot
data without embedding a browser in the TUI container. The operator supports a `TUISpec`
to deploy the TUI as an optional component alongside the collective on OpenShift.

## Success Criteria

- [ ] 6 screens accessible from a persistent top navigation bar
- [ ] `NATSObserver` handles all 11 signal types with zero if/elif chain (registry pattern)
- [ ] `CollectiveSnapshot` carries ACC-10 queue, progress, plan data; ACC-11 domain drift; ACC-12 compliance health
- [ ] `InfuseScreen` role list populated dynamically from `RoleLoader` (no hardcoding)
- [ ] `ComplianceScreen` shows OWASP grading table and HumanOversightQueue pending items
- [ ] `PerformanceScreen` shows per-role queue depth, TASK_PROGRESS, and backpressure state
- [ ] `CommunicationsScreen` shows live PLAN DAG, KNOWLEDGE_SHARE feed, EPISODE_NOMINATE queue
- [ ] `LLMEndpointScreen` shows active backend, model, health, live token utilisation
- [ ] `EcosystemScreen` lists all loaded roles from `roles/` directory
- [ ] Optional `WebBridge` HTTP server exposes live `CollectiveSnapshot` as JSON
- [ ] `spec.tui` in AgentCorpusSpec CRD; operator creates TUI Deployment when enabled
- [ ] Multi-collective: `ACC_COLLECTIVE_IDS` env var observes N collectives simultaneously
- [ ] Inline CSS migrated to `acc/tui/app.tcss`; all new screens have own `.tcss` files

## Scope

### In Scope
- 4 new screens: `ComplianceScreen`, `PerformanceScreen`, `CommunicationsScreen`, `LLMEndpointScreen`
- Enhanced existing screens: `DashboardScreen` (domain drift, compliance badge), `InfuseScreen` (dynamic roles/task types from `EcosystemScreen`)
- `EcosystemScreen` replacing the hardcoded role list in InfuseScreen
- Signal handler registry pattern in `NATSObserver`
- Extended `CollectiveSnapshot` / `AgentSnapshot` data models
- `WebBridge` HTTP adapter (asyncio + simple JSON, no additional framework)
- `NavigationBar` widget; `CSS_PATH` migration
- RHOAI operator `TUISpec` CRD extension
- Multi-collective `NATSObserver` fan-out
- `tests/test_tui_client.py` extension; new `tests/test_tui_screens.py`

### Out of Scope
- Full WebUI implementation (roadmap — WebBridge provides the integration point)
- Red teaming screen (Phase 2 of this track — complex guardrail pipeline integration)
- SPIRE/mTLS identity for TUI NATS connection (ACC-7 security track)
- Granular NATS subject ACLs per TUI screen (ACC-7 track)
- OpenShift Dynamic Console plugin (requires OCP Console Plugin SDK — separate effort)
- Streaming / SSE on the WebBridge (polling JSON initially)

## Assumptions

1. The `RoleLoader` public API (`RoleLoader('roles', name).load()`) can enumerate all available role names by scanning the `roles/` directory — needs a `list_roles()` utility method added.
2. `acc/compliance/` and `acc/audit.py` (ACC-12) expose data via HEARTBEAT payload fields `compliance_health_score`, `owasp_violation_count`, `oversight_pending_count` — already the case per ACC-12 implementation.
3. The WebBridge runs on a non-privileged port (default 8765) and listens on localhost only inside the container; external access requires an explicit port mapping or a sidecar proxy.
4. The RHOAI operator `TUISpec` creates a standard Kubernetes Deployment (not a Pod) so the TUI can be restarted independently of the collective.
5. Textual 0.80+ is already a pinned dependency in `pyproject.toml[tui]`.
