# Proposal: acc-webgui — signaling wiring + observer crash-resilience

## Problem

On the RHOAI / operator deployment, the `acc-webgui` container CrashLoopBackOff'd
(116 restarts, exit 3) while the `oauth2-proxy` sidecar stayed healthy. Two
independent defects combine to produce this:

1. **The operator never wires the signaling config into the webgui container.**
   `WebGUIReconciler.buildDeployment` (`operator/internal/reconcilers/ui/webgui.go`)
   sets only the five auth env vars. It omits `ACC_NATS_URL` and
   `ACC_COLLECTIVE_IDS`, so the app falls back to its hardcoded defaults
   `nats://nats:4222` / `sol-01`. But NATS is namespaced per corpus
   (`<corpus>-nats`, e.g. `acc-demo-coding-nats`), so the default never resolves.
   Every other ACC workload (agents via `acc-config.yaml`, the TUI via
   `tui.go`) is wired to the right NATS; webgui is the one surface that isn't.

2. **The webgui treats a boot-time NATS outage as fatal.** `create_app()`'s
   FastAPI `lifespan` does `await hub.start()`, and `ObserverHub.start()` does a
   blocking `await obs.connect()` per collective. A `NoServersError` propagates
   out of startup → uvicorn exits → CrashLoopBackOff. acc-webgui is a *read-only
   observability surface*; a transient or misconfigured data source should not
   take the whole UI down. The acc-tui tolerates this (it boots and shows
   "disconnected"); the webgui should too.

Defect 1 is the proximate cause here; defect 2 is the design flaw that turned a
recoverable misconfiguration into a hard crash loop.

## Current behavior

- `webgui.go` webgui container env: `ACC_WEBGUI_HOST/PORT/AUTH_MODE/
  OIDC_GROUPS_CLAIM/GROUP_MAPPINGS` only — no `ACC_NATS_URL`, no
  `ACC_COLLECTIVE_IDS`.
- `acc/webgui/observers.py` `ObserverHub.start()` connects every observer
  synchronously and lets any connect error propagate; `lifespan` has no guard,
  so startup fails hard.

## Desired behavior

1. **Operator wires signaling.** `buildDeployment` injects
   `ACC_NATS_URL = nats://<corpus>-nats:4222` (mirroring `tui.go`) and, when the
   corpus has resolvable collectives, `ACC_COLLECTIVE_IDS` = the comma-joined
   `CollectiveID` of every `AgentCollective` the corpus manages. Resolution is
   best-effort (an unresolvable collective is logged and skipped, not fatal).
2. **Observer boots degraded, not dead.** `ObserverHub.start()` keeps the
   synchronous fast path when NATS is reachable (so `observer(cid)` is live the
   moment `start()` returns), but on a connect failure it schedules a background
   reconnect task with capped backoff and returns. `/health` and the SPA serve
   regardless; the collective shows as not-yet-connected and connects when NATS
   becomes reachable.

## Success criteria

- [x] webgui container carries `ACC_NATS_URL=nats://<corpus>-nats:4222`.
- [x] `ACC_COLLECTIVE_IDS` is the comma-joined CollectiveID of the corpus's
      collectives; omitted (app default kept) when the corpus has none.
- [x] `ObserverHub.start()` does not raise when NATS is unavailable at boot.
- [x] A background retry connects the observer once NATS recovers.
- [x] Existing behavior preserved when NATS is up: `observer(cid)` available
      synchronously after `start()` (action layer + existing tests unaffected).
- [x] `pytest tests/test_webgui.py` green (incl. 2 new resilience tests);
      `go test ./test/unit/ -run WebGUI` green (incl. NATS-url + collective-ids
      assertions).

## Scope

In: `webgui.go` env wiring + `observedCollectiveIDs` helper; `observers.py`
non-fatal start + background reconnect; operator + python tests.

Out: readiness/liveness probes on the webgui pod (separate follow-up — see
tasks); a UI affordance that surfaces "NATS disconnected" beyond the existing
empty-snapshot state; any change to the NATS subject protocol.

## Assumptions

- NATS service name is `<corpus>-nats` in the corpus namespace (the operator's
  own convention — `acc_config.go`, `tui.go`, `kafka_bridge.go`).
- `ACC_COLLECTIVE_IDS` is comma-separated (per `acc/webgui/app.py
  _collective_ids`).
- Once connected, the underlying NATS client handles transient reconnects; the
  new retry loop only covers the *initial* boot-time connect.
