# Golden Prompts capability — delta: versioning, success criteria, connected traces

## ADDED

### Prompt versioning

**REQ-GP-VER-001** A `GoldenPrompt` SHALL carry a monotonic `revision` (int, default 1),
an `updated_at` (ISO-8601 UTC) and an `author`. A prompt file authored before this change
SHALL load as `revision: 1` with empty `updated_at`/`author` and behave exactly as today.

**REQ-GP-VER-002** Saving an edited prompt SHALL increment `revision`, refresh `updated_at`
and `author`, and append the prior version to an append-only history
(`<name>.history.jsonl`) BEFORE replacing `<name>.yaml`. History SHALL never be mutated or
truncated by a save.

**REQ-GP-VER-003** The system SHALL expose, for a given prompt name, (a) its revision
history, (b) a textual diff between any two revisions, and (c) a rollback that restores a
chosen past revision AS A NEW revision (history remains append-only).

### Success criteria and definition of good

**REQ-GP-CRIT-001** A `GoldenPrompt` SHALL accept an optional `definition_of_good` (prose
rubric) and an optional list of named `success_criteria`. When `success_criteria` is empty,
evaluation SHALL fall back to the existing `expects` asserts unchanged.

**REQ-GP-CRIT-002** Each `success_criteria` entry SHALL be evaluated independently and its
pass/fail SHALL be reported per-criterion in the result; the overall verdict SHALL be the
conjunction of all evaluated criteria.

**REQ-GP-CRIT-003** A criterion of kind `judge` SHALL score the reply against its rubric
using a configured judge model (resolved via `acc.models`), recording `{score, pass,
rationale}`. When no judge model is available the criterion SHALL be reported as
not-applicable (skipped), NOT as a failure.

### Connected traces

**REQ-GP-TRACE-001** A `GoldenResult` SHALL record the `prompt_revision` it ran against and
a `trace_id` correlating the run to the agent invocation. The runner SHALL propagate an
existing correlation id when present and otherwise generate one.

**REQ-GP-TRACE-002** Persisted history rows SHALL include `prompt_revision` and `trace_id`,
and the TUI SHALL present, for the selected prompt, a run timeline (timestamp, verdict,
latency, revision, trace_id) sufficient to pivot from a failing golden to its trace.

### Provenance and export

**REQ-GP-SRC-001** The TUI SHALL display each prompt's `source` (shipped, writable, or
attached) so an operator can tell an override from a shipped baseline.

**REQ-GP-SRC-002** The system SHALL provide an action to export a writable-store prompt
(with its current revision and provenance) into an attached or source-controlled directory.
