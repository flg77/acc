# Changelog

All notable changes to the **`flg77/acc`** runtime are recorded here.

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versioning per [SemVer](https://semver.org/spec/v2.0.0.html).

Tracked since proposal 003 (ACC TUI usability hardening,
2026-05-13) — earlier changes are reconstructable from
`git log` but not back-filled into this file.

## [Unreleased] — proposal 003 development cycle

In-flight work for the **0.2.0** release.  Proposal 003 closes
when PRs 1–6 of the TUI hardening series have all landed; that
tag is the v0.2.0 cut.

### Added

- **TUI Ecosystem: `role.md` narrative surface.**  The role detail
  panel now reads `roles/<name>/role.md` alongside `role.yaml` and
  renders it in a `Markdown` widget at the top of a two-section
  collapsible.  The raw yaml is preserved under a second
  collapsible (closed by default).  Roles without a `role.md`
  show a friendly placeholder pointing operators at the
  forthcoming authoring guideline (slot 006).  (PR-2 of proposal
  003 — PR #55.)
- **TUI Ecosystem: role search filter.**  An `Input` widget above
  the ROLE LIBRARY DataTable narrows the visible rows by
  case-insensitive substring match against name / domain /
  persona.  Clearing the input restores the full list.  Backed by
  an in-memory cache (`_all_role_rows`) so the filter doesn't
  re-read disk per keystroke.  (PR-2.)

### Fixed

- **TUI Prompt: cancel-on-timeout.**  The Prompt screen now
  publishes `TASK_CANCEL` on `acc.{cid}.task.cancel` when the
  receive loop times out, instead of silently abandoning the
  in-flight task.  Without this fix, vLLM / llama.cpp backends
  kept generating against the dropped task; the operator's work
  was discarded and the late `TASK_COMPLETE` landed on a screen
  the operator had moved past.  (PR-1 of proposal 003 — PR #54.)

### Changed

- **TUI Prompt timeout default** raised from 60 s to 180 s for
  slow local LLM backends.  Configurable via the
  `ACC_PROMPT_TIMEOUT_S` environment variable.  (PR-1.)
- **TUI Prompt transcript message** when receive times out now
  reads "(cancelled after Ns — no reply; TASK_CANCEL published)"
  instead of "(timeout after 60s — no reply)".  Reflects the
  fact that the system actually cancelled rather than gave up.
  (PR-1.)

### Added

- `CHANGELOG.md` (this file) — Keep-a-Changelog format,
  introduced alongside the proposal 003 development cycle.
- `acc/tui/screens/prompt.py:_resolve_timeout()` helper —
  reads `ACC_PROMPT_TIMEOUT_S` with safe fallback to the
  default; warns on malformed / non-positive values.  (PR-1.)
- `acc/tui/screens/prompt.py:_mark_cancelled()` /
  `_is_cancelled()` — 256-entry FIFO of task_ids cancelled by
  the timeout path, for late-TASK_COMPLETE suppression.  Public
  API; not yet wired into the agent-entry append path (the
  channel layer already returns on first signal, so no
  late-reply hazard exists today).  (PR-1.)

## [0.1.0] — pre-proposal-003 baseline

Reconstructable from `git log`; not back-filled here.  Notable
landmarks for context:

- Sub-agent clustering (PRs #26–#30).
- Autoresearcher demo + iteration loop (PRs #41–#46).
- Operator (Kubernetes/OpenShift) scaffold (PRs #47–#51).
- Podman Desktop extension sibling repo
  (`flg77/acc-podman-desktop`) shipped to v0.3.0 in parallel.
