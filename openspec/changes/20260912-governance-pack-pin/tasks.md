# 20260912-governance-pack-pin — tasks

## Phase 1 — the governance pack, pinned in the AgentBOM (AS-09)

### 1.1 The field
- [x] `AgentBOMSpec.governance: list[str]`, default `[]`
- [x] exact pins only, with their own validation message
- [x] `AgentBOM.unresolved_governance(available)`
- [x] `AgentBOMVerdict.unresolved_governance`, counted against `ok`

### 1.2 The `spec.policy` decision
- [x] it stays the **install-time EC policy ref** (not a pack) — the field
      description says so
- [x] a `policy` written as a pack pin must be listed in `spec.governance`,
      so the two cannot drift

### 1.3 Tests + docs
- [x] `tests/test_agent_bom.py` (7 added)
- [x] `docs/HOWTO-asago-policy.md` §8: the governance pack, pinned in the BOM
- [x] CHANGELOG

### Verification
- [x] the BOM suite green (15)
- [x] the wider package suite green: 1657 passed across 102 files touching `acc.pkg`,
      the BOM, the catalog and the slash commands (the 4 reds are the workstation's
      known cosign-environment ones in `tests/catalog/`, untouched by this change)

## Phase 2 (deferred)
- [ ] the pins recorded beside every eval result — with AS-05 (EvalHub client /
      local garak-midojo fallback), which writes the artefact they belong in
- [ ] AS-02: the governance pack kind itself; `instance` carries one per posture
- [ ] AS-03: `risk_profile` on the BOM (scenarios run, verdicts, artefact digest)
- [ ] AS-07: the hub's review date tied to the pinned governance version
- [ ] the operator's `AgentBOM` CRD wrapper gains the field
