# Tasks: Deployment backup and restore

**Change ID:** 20260817-deployment-backup-restore
**Branch:** `feat/deployment-backup-restore`

---

## Phase 1 — Capture

- [ ] `[1]` Define the manifest: ACC version, host, collectives, contents, required secret names
- [ ] `[2]` Config set collector (excluding secret values)
- [ ] `[3]` Package registry + installed trees collector
- [ ] `[4]` Tracelog collector
- [ ] `[5]` `--include` tiers

## Phase 2 — Restore

- [ ] `[6]` `--dry-run` reporting exactly what would be replaced
- [ ] `[7]` Refuse to clobber a running deployment without acknowledgement
- [ ] `[8]` Report missing secret names and stop, rather than restoring unusable state
- [ ] `[9]` Version-compatibility check with a clear refusal message

## Phase 3 — Assurance

- [ ] `[10]` Test asserting no secret values appear anywhere in an archive
- [ ] `[11]` Round-trip test: change config, restore, compare resolved state
- [ ] `[12]` Decide and document the vector-store position

