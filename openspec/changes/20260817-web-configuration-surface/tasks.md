# Tasks: Configuration through the web interface

**Change ID:** 20260817-web-configuration-surface
**Branch:** `feat/web-configuration-surface`

---

## Phase 1 — Read

- [x] `[1]` Expose configuration through the schema, with owning-file and secret flags
- [x] `[2]` Distinguish ordinary configuration from posture in the model
- [x] `[3]` Authorisation split: view vs reconfigure (open question 2)
      *(the EXISTING viewer/operator split, not a new tier. Viewer reads the
      surface; operator writes ordinary configuration; NOBODY writes posture from
      a browser -- that routes through oversight. A third RBAC tier would have
      been a second authorisation model to keep in step with the first.)*

## Phase 2 — Write

- [x] `[4]` Validated writes reusing the preflight checks
- [x] `[5]` Change preview and confirmation
- [x] `[6]` Route posture changes through the oversight path
- [x] `[7]` Attribution and recording of every change

## Phase 3 — Verify

- [x] `[8]` Test: an invalid combination cannot be saved through the API
- [x] `[9]` Test: a read-only user cannot write configuration

