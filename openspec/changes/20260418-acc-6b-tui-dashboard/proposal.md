# Proposal: ACC-6b — TUI + Role Infusion Dashboard

| Field      | Value                                              |
|------------|----------------------------------------------------|
| Change ID  | ACC-6b                                             |
| Date       | 2026-04-18                                         |
| Status     | Draft                                              |
| Branch     | `feature/ACC-6b-tui-dashboard`                     |
| Depends on | ACC-6a (`feature/ACC-6a-cognitive-core-role-infusion`) |

---

## Problem Statement

After ACC-6a, agents have a cognitive core and role definitions, but there is no
user-facing interface to infuse those definitions or observe agent behaviour in real
time. A user deploying ACC must edit YAML files and read raw NATS messages to
understand what the collective is doing. There is no visible trace of drift scores,
governance trigger counts, or reprogramming ladder state.

## Current Behavior

No interactive interface exists. Role definitions are set via `acc-config.yaml` only.
Observability is limited to log output and raw OTel spans. `StressIndicators` are
present in HEARTBEAT payloads (from ACC-6a) but not surfaced to the user.

## Desired Behavior

A terminal application (`acc-tui`) built with Textual provides two screens:

1. **Infuse screen** — structured form for composing and applying role definitions to a
   running collective. Submitting the form publishes a `ROLE_UPDATE` signal on NATS.
   A history panel shows past role versions from LanceDB `role_audit`.

2. **Dashboard screen** — live view of the collective sourced from NATS HEARTBEAT
   and TASK_COMPLETE payloads. Panels: agent state grid, governance trigger counts
   (Cat-A/B/C), memory indicators (ICL episodes, patterns, Cat-C rules), LLM metrics
   (p95 latency, token utilisation per agent).

The TUI connects to NATS as an observer-class client. It reads all collective subjects
(`acc.<collective_id}.>`) for dashboard data and writes only to `role_update` and
`role_approval` subjects.

## Success Criteria

- [ ] `acc-tui` CLI entry point launches the Textual application
- [ ] Infuse screen renders all `RoleDefinitionConfig` fields as editable widgets
- [ ] Applying a role from the TUI results in a `ROLE_UPDATE` signal on NATS
- [ ] Dashboard updates within 2 heartbeat intervals of a state change
- [ ] All 5 agent role cards display `drift_score`, `reprogramming_level`, and state
- [ ] Governance panel shows per-agent Cat-A/B/C trigger counts
- [ ] TUI runs identically inside a container (`acc-tui` Deployment on K8s)
- [ ] `pip install agentic-cell-corpus[tui]` installs all required dependencies

## Scope

### In Scope
- `acc/tui/` Python package with Textual app, screens, and NATS observer client
- `acc-tui` CLI entry point in `pyproject.toml`
- `deploy/Containerfile.tui` — UBI10 + Python 3.12 TUI container image
- `operator/config/samples/acc_tui_deployment.yaml` — optional K8s Deployment sample

### Out of Scope
- Web UI (separate roadmap branch, separate change)
- Role definition diff visualisation (future polish)
- Arbiter countersign flow inside TUI (ACC-6b publishes ROLE_UPDATE; arbiter approval
  happens in the agent via ACC-6a RoleStore — TUI does not sign)
- `kubectl acc` plugin (separate change)
- Grafana dashboard ConfigMap (separate observability change)

## Assumptions

1. The TUI process connects to NATS using the same URL from `acc-config.yaml` (or
   `ACC_NATS_URL` env var). It does not need Redis or LanceDB access — all data is
   sourced from NATS payloads.
2. Textual ≥ 0.80 is used. It is an optional dependency group `[tui]` so existing
   deployments without TUI are unaffected.
3. The dashboard polls `StressIndicators` from HEARTBEAT payloads introduced in ACC-6a.
   The TUI will not render stress panels if connected to a pre-ACC-6a agent.
4. On K8s, the `acc-tui` pod runs in the same namespace as the collective and connects
   to the NATS service by internal DNS.
