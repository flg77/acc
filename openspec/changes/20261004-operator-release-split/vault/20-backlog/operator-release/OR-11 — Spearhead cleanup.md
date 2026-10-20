---
type: backlog-item
area: operator-release
id: OR-11
status: done
size: S
depends: [OR-02, OR-10]
created: 2026-10-04
index: "[[OR-00 — Operator release and repo split]]"
tags: [backlog, operator, operatorhub]
---

# OR-11 — Spearhead cleanup

**What.** Remove `operator/`, `console-plugin/` and the two parity tests from `acc-spearhead`; fix `pyproject.toml`/test references; README badges and tree; CHANGELOG upgrade notes name the operator version from the operator's own changelog.

**Done when.** `acc-spearhead` tests are green without the operator tree; the mirror promote of the runtime no longer carries it.

**2026-10-05 — done** (acc-spearhead, branch `claude/modest-bell-35a6sk`, PR #534). `operator/` and `console-plugin/` (222 tracked files) and the two parity tests are gone; the suite collects 5655 tests with the same 67 pre-existing collection errors as before (missing `textual`/`fastapi`/`rich` extras here). README: one operator badge pointing at `flg77/acc-operator` instead of the Go and OLM badges; the edge and RHOAI quick starts install the operator from the public bundle (`operator-sdk run bundle quay.io/flg77/acc-operator-bundle:0.3.0`); tree listing and related-repositories table updated. `docs/howto-edge.md` installs through OLM (the old text claimed a CRD-only mode without webhooks, which the operator does not have); `howto-rhoai.md`'s CatalogSource names a community publisher and an index image; TUI/WebGUI sample Deployments are fetched from the operator repository at v0.3.0; `operator/internal/...` code pointers in the design docs now name the repository. `pyproject.toml` had no operator references. CHANGELOG: "Removed" entry under Unreleased; the upgrade notes already pair the runtime with operator 0.3.0. Still open from the "done when": the runtime's `acc-promote` to its mirror no longer carries the tree once this merges (nothing to do; the tree is gone), and the second promote pair for the operator repositories is OR-02's leftover.
