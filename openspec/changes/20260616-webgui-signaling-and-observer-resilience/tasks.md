# Tasks

## Operator — signaling wiring (Gap 1)
- [x] `webgui.go`: add `observedCollectiveIDs(ctx, corpus)` — Get each
      `AgentCollective` in `corpus.Spec.Collectives`, collect `Spec.CollectiveID`
      (best-effort; log + skip unresolvable).
- [x] `webgui.go`: `Reconcile` computes `natsURL = nats://<corpus>-nats:4222`
      and `collectiveIDs`, passes both to `buildDeployment`.
- [x] `webgui.go`: `buildDeployment` adds `ACC_NATS_URL` (always) and
      `ACC_COLLECTIVE_IDS` (only when non-empty) to the webgui container env.
- [x] `webgui_test.go`: happy path asserts `ACC_NATS_URL` set + `ACC_COLLECTIVE_IDS`
      omitted (no collectives); new `TestWebGUI_WiresObservedCollectiveIDs` asserts
      the comma-joined ids.

## acc-webgui — observer resilience (Gap 2)
- [x] `observers.py`: `start()` keeps synchronous connect on success, schedules a
      background reconnect on failure; never raises on a down NATS.
- [x] `observers.py`: `_try_connect` + `_reconnect_loop` (capped backoff
      `_RECONNECT_MIN_S`..`_RECONNECT_MAX_S`); `stop()` cancels reconnect tasks too.
- [x] `test_webgui.py`: `test_start_is_nonfatal_when_nats_unavailable`,
      `test_observer_connects_after_nats_recovers`.

## Verification
- [x] `pytest tests/test_webgui.py` — 51 passed (acc1 `.venv-acc`).
- [x] `go build ./... && go vet ./internal/reconcilers/ui/... && go test ./test/unit/ -run WebGUI`
      — BUILD_OK / VET_OK / 5 PASS (golang:1.24 container on acc1).

## Live unblock (operated by the user; not in this change's code)
- [x] Scale operator to 0, `oc set env` the webgui deployment with the correct
      `ACC_NATS_URL`/`ACC_COLLECTIVE_IDS` to lift the crash-loop pending the new
      operator image. (Manual, reverted by reconcile — superseded by the code fix.)

## Follow-up (out of scope here)
- [ ] Add a readiness probe (oauth2-proxy → webgui `/health`) so the Service stops
      routing to a dead backend and the failure is visible in `oc get pod` sooner.
- [ ] Surface "NATS disconnected" explicitly in the SPA (today it shows an empty
      snapshot, indistinguishable from an idle collective).
