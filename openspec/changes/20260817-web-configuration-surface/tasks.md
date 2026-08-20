# Tasks: Configuration through the web interface

**Change ID:** 20260817-web-configuration-surface
**Branch:** `feat/web-configuration-surface`

---

## Phase 1 — Read

- [ ] `[1]` Expose configuration through the schema, with owning-file and secret flags
- [ ] `[2]` Distinguish ordinary configuration from posture in the model
- [ ] `[3]` Authorisation split: view vs reconfigure (open question 2)

## Phase 2 — Write

- [ ] `[4]` Validated writes reusing the preflight checks
- [ ] `[5]` Change preview and confirmation
- [ ] `[6]` Route posture changes through the oversight path
- [ ] `[7]` Attribution and recording of every change

## Phase 3 — Verify

- [ ] `[8]` Test: an invalid combination cannot be saved through the API
- [ ] `[9]` Test: a read-only user cannot write configuration

