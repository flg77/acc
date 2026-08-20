# Tasks: Named deployment profiles as a product capability

**Change ID:** 20260817-named-deployment-profiles
**Branch:** `feat/named-deployment-profiles`

---

## Phase 1 — Local profiles

- [x] `[1]` Profile representation per the existing design
- [x] `[2]` `validate` reusing the preflight checks
- [x] `[3]` `apply` with recorded, reversible switching
- [x] `[4]` Active-profile reporting from the deployment

## Phase 2 — Governed application

- [x] `[5]` Treat posture changes as consequential mutations, per the design
- [x] `[6]` `diff` between active and candidate

## Phase 3 — Distribution

- [x] `[7]` `export` / `import` with an explicit report of what is not carried
- [ ] `[8]` Decide the signing question (open question 2)
      *(NOT decided. An exported profile is currently plain JSON with a version field
      and no signature. It carries no credentials, so the risk is a profile that
      LOWERS a posture arriving unverified -- which is exactly the case signing would
      address. Left for the operator; the document has a version field to hang it on.)*
- [x] `[9]` Decide the future of the external tooling (open question 3)
      *(the ansible role becomes a THIN CALLER. It already knows how to reach hosts
      and sequence a restart, which this does not; what it should stop doing is
      fencing its own edits into config files now that `profile apply` validates,
      records and reverses. Not yet rewritten -- that is a lab-gitops change.)*

