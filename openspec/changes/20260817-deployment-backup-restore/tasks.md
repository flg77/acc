# Tasks: Deployment backup and restore

**Change ID:** 20260817-deployment-backup-restore
**Branch:** `feat/deployment-backup-restore`

---

## Phase 1 — Capture

- [x] `[1]` Define the manifest: ACC version, host, collectives, contents, required secret names
- [x] `[2]` Config set collector (excluding secret values)
- [x] `[3]` Package registry + installed trees collector
- [~] `[4]` Tracelog collector
      *(the tier exists and collects when the tracelog root is discoverable; not
      exercised against a populated tracelog.)*
- [x] `[5]` `--include` tiers

## Phase 2 — Restore

- [x] `[6]` `--dry-run` reporting exactly what would be replaced
- [x] `[7]` Refuse to clobber a running deployment without acknowledgement
- [x] `[8]` Report missing secret names and stop, rather than restoring unusable state
- [x] `[9]` Version-compatibility check with a clear refusal message

## Phase 3 — Assurance

- [x] `[10]` Test asserting no secret values appear anywhere in an archive
- [x] `[11]` Round-trip test: change config, restore, compare resolved state
- [x] `[12]` Decide and document the vector-store position
      *(NOT captured, deliberately. Embeddings are derived data -- large, rebuildable
      from the episodes, and tied to the embedding model that produced them. An
      archive carrying vectors built by a different model would restore a store whose
      contents no longer mean what the index says. Volumes remain the right tool.)*

