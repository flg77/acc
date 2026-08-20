# Tasks: Configuration schema and programmatic access

**Change ID:** 20260817-config-schema-and-crud
**Branch:** `feat/config-schema-and-crud`

---

## Phase 1 — Schema

- [x] `[1]` Enumerate every key currently read from the five files, with type, default and owning file
- [x] `[2]` Express it as a schema (see options); export JSON Schema for reuse
- [x] `[3]` Mark secret-bearing keys explicitly so no surface can print them by accident

## Phase 2 — Access layer

- [x] `[4]` `acc/configstore.py` — resolve, merge, report owning file, validate
- [x] `[5]` Comment-preserving writer with an atomic write path
- [x] `[6]` Duplicate-top-level-key detection (the failure that made marker fencing necessary)
- [x] `[7]` Unit tests incl. round-trip preservation of comments and ordering

## Phase 3 — CLI

- [x] `[8]` `acc-cli config show|get|set|unset|path`
- [x] `[9]` `acc-cli config check` — missing, unknown, deprecated
- [x] `[10]` `acc-cli config migrate` — add new options with defaults, never overwrite
- [x] `[11]` Refuse a `set` that would create an unresolvable reference
- [ ] `[12]` Wire preflight checks onto the schema rather than ad-hoc parsing
      *(blocked: no preflight command exists yet — that is 20260817-operator-preflight-doctor.
      The schema is exported via `configschema.json_schema()` and `check()` already
      implements the fault detection preflight needs, so this is a consumer change.)*

