# Spec: Harness-side context budgeting

**Capability:** prompt assembly · model capacity · drop accounting
**Change ID:** 20260826-context-budget
**Version:** 0.1.0

---

## Requirements — ADDED

### Declared model capacity

**REQ-CAP-001** `ModelEntry` SHALL carry `context_window: int = 0` — the
model's usable input window in tokens. `0` SHALL mean *undeclared*, not *zero
capacity*.

**REQ-CAP-002** `ModelEntry` SHALL carry `max_output_tokens: int = 0` — the
completion reserve. `0` SHALL mean *undeclared* and fall through to the posture
default.

**REQ-CAP-003** Both fields SHALL be optional. A `models.yaml` written before
this change SHALL load unchanged and SHALL NOT be rewritten by
`save_registry()` merely to add them.

**REQ-CAP-004** `context_window` SHALL NOT be inferred from `deploy_mode`,
`backend`, or `zone`. It is a property of the served model.

**REQ-CAP-005** `ModelEntry.zone` SHALL retain its sole meaning as the trust /
data-residency label consumed by `ZonePolicyGate`. This change SHALL NOT
overload it.

### Ceiling resolution

**REQ-CEI-001** The input ceiling SHALL be computed as
`window - reserve_output - measured_system_tokens - safety_margin`, and SHALL
be clamped to a minimum of 1.

**REQ-CEI-002** `window` SHALL resolve in this precedence order:
`ACC_CONTEXT_BUDGET` (when set and non-zero) → `ModelEntry.context_window`
(when non-zero) → `ACC_CONTEXT_WINDOW_DEFAULT` → the built-in default `8192`.
The source SHALL be recorded on the stage event.

**REQ-CEI-003** `measured_system_tokens` SHALL be counted from the role's
rendered system prompt and memoised per role. The budgeter SHALL NOT modify the
system prompt, so the PR-CA1 per-role prefix stability is preserved.

**REQ-CEI-004** Posture defaults for `reserve_output`, `safety_margin` and
per-block shares SHALL be selected by `deploy_mode`. `deploy_mode` SHALL NOT
influence `window`.

**REQ-CEI-005** When `ACC_CONTEXT_BUDGET` is `0`, the packer SHALL be bypassed
entirely and assembly SHALL produce output byte-identical to the pre-change
`_compose_user_content`.

### Packing

**REQ-PCK-001** `acc.context_budget.pack()` SHALL be a pure synchronous
function: no I/O, no `await`, no clock read, no mutation of its arguments.

**REQ-PCK-002** `pack()` SHALL be deterministic — identical inputs SHALL
produce a byte-identical `text`.

**REQ-PCK-003** The operator's task content SHALL never be truncated, elided or
dropped. When the task alone exceeds the ceiling, `pack()` SHALL raise
`ContextOverflow` carrying both the required and available token counts.

**REQ-PCK-004** Eviction SHALL follow block `priority`, lowest surviving
longest, with the default order `task` → `thread` → `notes` → `episodes`.

**REQ-PCK-005** Assembly SHALL follow `display_rank`, which is independent of
`priority`. With nothing dropped, the assembled text SHALL be byte-identical to
the pre-change output.

**REQ-PCK-006** Within a divisible block, items SHALL be considered in
keep-preference order — thread newest-first, episodes highest-similarity-first,
notes as supplied — and eviction SHALL be greedy: an item that does not fit
SHALL be dropped and the next item still considered.

**REQ-PCK-007** A block SHALL render its heading if and only if at least one of
its items survives. A heading with no surviving items SHALL NOT be emitted.

**REQ-PCK-008** `pack()` SHALL NOT summarise, compress, elide or ellipsise any
content. Its only reduction operation is dropping whole items.

**REQ-PCK-009** Each block SHALL additionally be bounded by
`min(remaining, block_cap)` where `block_cap` derives from the posture share
and an absolute cap, so that a large window does not cause low-value content to
be admitted merely because it fits.

### Token estimation

**REQ-EST-001** The estimator SHALL require no tokenizer, no model download and
no network call.

**REQ-EST-002** The estimator SHALL maintain a correction factor per
`(backend, model)`, updated by an EMA over the ratio of `usage.prompt_tokens`
returned by the backend to the estimate the packer produced for that call.

**REQ-EST-003** The correction factor SHALL be clamped to `[0.6, 2.0]`. A
malformed, absent or zero `usage` block SHALL leave the factor unchanged.

**REQ-EST-004** The correction factor SHALL be keyed on `(backend, model)` and
SHALL NOT carry across a model change for a role.

**REQ-EST-005** The safety margin SHALL start at the posture default and SHALL
narrow toward its floor only as the observed sample count for that
`(backend, model)` rises.

### Accountability

**REQ-ACC-001** Every `pack()` call SHALL emit an `acc.pipeline.context_budget`
stage event carrying at minimum: `ceiling`, `est_tokens`, `window`,
`window_source`, `kept` per kind, `dropped` count, `dropped_by_kind`,
`degraded`, and `calibration`.

**REQ-ACC-002** `degraded` SHALL be `true` if and only if at least one item was
dropped.

**REQ-ACC-003** Dropped items SHALL be enumerated with their kind, index within
the block, estimated tokens, and reason. A drop SHALL NOT be silent.

**REQ-ACC-004** The prompt recorded to the tracelog SHALL be the **post-pack**
text — the text actually sent — so that *logged means model-visible* holds as
the converse of REQ-RPL-003 in
`20260825-conversational-turn-continuity`.

### Backend truncation

**REQ-BKD-001** The Ollama backend SHALL send `options.num_ctx` on
`/api/chat`, derived from the resolved window, so the server does not silently
truncate at its own default.

**REQ-BKD-002** When a backend reports a context-length rejection, the failure
SHALL surface as a task failure naming the limit. It SHALL NOT be retried with
silently reduced content in Phase 1.

---

## Requirements — MODIFIED

**REQ-RPL-001** (from `20260825-conversational-turn-continuity`) —
`sessions.context_for()` retains its `max_chars` bound as an outer stopgap.
Once the budgeter is wired, `ACC_THREAD_CHARS` becomes a *ceiling on what is
offered*, not the mechanism that prevents overflow; the budgeter is
authoritative for what is sent.

---

## Non-requirements

* The budgeter SHALL NOT probe, detect or verify the served window in Phase 1.
  Discovery is `20260826-context-budget` Phase 2.
* The budgeter SHALL NOT perform compaction or summarisation in any phase; that
  is a separate mechanism that consumes the budgeter's deficit figure.
* This change makes no statement about `rope_scaling` / YaRN. Length extension
  is a serving decision that reaches ACC only as `context_window`.
