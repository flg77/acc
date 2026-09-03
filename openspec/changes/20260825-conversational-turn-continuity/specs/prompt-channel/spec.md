# Spec: Conversational turn continuity

**Capability:** prompt-channel · session replay · role gating
**Change ID:** 20260825-conversational-turn-continuity
**Version:** 0.1.0

---

## Requirements — ADDED

### Thread identity on the prompt contract

**REQ-THR-001** `PromptChannel.send()` SHALL accept an optional `session_id`
argument. When supplied it SHALL be placed on the TASK_ASSIGN payload under the
key `session_id`.

**REQ-THR-002** When `session_id` is omitted or empty, the runtime SHALL behave
exactly as it does before this change: the agent falls back to `task_id`
(`acc/agent.py:1336`) and the replay block is empty.

**REQ-THR-003** The TUI Prompt screen SHALL pass the `_session_id` it already
maintains (`acc/tui/screens/prompt.py:389`) on every send, so a TUI thread and
its tracelog session are the same object.

**REQ-THR-004** A prompt surface that does not supply a `session_id` SHALL
degrade to one-turn-per-session. It SHALL NOT fall back to any shared or
implicit thread.

### Replay assembly

**REQ-RPL-001** `sessions.context_for()` SHALL accept a `max_chars` bound in
addition to the existing turn `limit`, and SHALL retain the **most recent**
turns when truncating.

**REQ-RPL-002** `CognitiveCore` SHALL prepend a replay block to
`llm_user_content` before the LLM call, under an explicit heading identifying
the content as prior turns of the current conversation.

**REQ-RPL-003** The replay block SHALL be derived **solely** from durable
tracelog records. No client-supplied transcript SHALL reach the block. This
preserves the *model-visible means logged* invariant: every line the model reads
has a corresponding durable record.

**REQ-RPL-004** Replayed turns SHALL appear in chronological order, each turn
exactly once.

**REQ-RPL-005** An absent, empty or unreadable thread SHALL produce an empty
replay block. It SHALL NOT raise, and SHALL NOT produce a partial block.

**REQ-RPL-006** The replay block SHALL be distinguishable in the assembled
prompt from the operator's current request. Prior turns are context, not
instructions.

### Scope

**REQ-SCP-001** The replay SHALL be filtered on the scope key defined by
`acc/memory_scope.py` — the same key episode retrieval uses. No second
partitioning scheme SHALL be introduced.

**REQ-SCP-002** A thread whose scope does not match the requesting principal's
scope SHALL replay empty.

**REQ-SCP-003** Sessions recorded before this change, and sessions with no
attribution, SHALL replay empty rather than being treated as belonging to the
current requester.

**REQ-SCP-004** Scope SHALL be enforced when the replay is assembled, not by
instructing the model to respect a boundary.

### Bounds

**REQ-BND-001** The replay SHALL be bounded by `ACC_THREAD_TURNS` (default `6`)
and `ACC_THREAD_CHARS` (default `4000`).

**REQ-BND-002** `ACC_THREAD_CONTINUITY=0` SHALL disable replay for every role,
restoring pre-change behaviour.

**REQ-BND-003** The bound is a stopgap pending context compaction and SHALL be
commented as such at its definition. ACC performs no context compaction, so an
unbounded thread overflows the smallest-context deployments first.

### Role gating

**REQ-ROL-001** `RoleDefinitionConfig` SHALL gain `thread_continuity: bool`,
defaulting to `False`.

**REQ-ROL-002** Only `roles/assistant/role.yaml` SHALL set it `True` in this
change.

**REQ-ROL-003** For a role with `thread_continuity` false, the assembled prompt
SHALL be byte-identical to the pre-change output.

### Measurement

**REQ-MSR-001** The golden-prompt suite SHALL gain one multi-turn entry whose
first turn cannot be answered without a clarification and whose second turn
supplies it.

**REQ-MSR-002** The entry SHALL pass only when the reply to the second turn acts
on the first rather than re-asking.

---

## Requirements — UNCHANGED (stated to prevent drift)

**REQ-UNC-001** The tracelog schema is unchanged. Continuity reads it; it does
not write new record kinds.

**REQ-UNC-002** Session retention is unchanged and remains governed by the
policy shipped with `20260817-session-resume-and-lifecycle`.

**REQ-UNC-003** The audit record remains append-only. Replay never rewrites
history, consistent with `sessions.resume()` recording a parent edge rather than
mutating its predecessor.

**REQ-UNC-004** No escalation behaviour is added. Selecting a larger model or an
additional role remains out of scope until the escalation-policy phase, and
remains distinct from failover (`acc/llm_failover.py`).

---

## Implementation deviations — 2026-08-25 (`6853fb8`)

Recorded rather than left to drift. Two, both deliberate.

### 1. REQ-UNC-001 is narrowed: continuity WRITES one field

The requirement says the tracelog is unchanged and that continuity "reads it;
it does not write new record kinds". No new *kind* was added — but
`prompt_in` now carries a `scope` field
(`acc/agent.py::_tracelog_prompt_in`).

**Why it was unavoidable.** REQ-SCP-001..003 require the replay to be filtered
on the memory scope, and at replay time the requester is no longer available:
a `prompt_in` record carries `task_id`, `role`, `agent_id`, `collective_id` and
the prompt, and nothing identifying who asked. Deriving the scope afterwards is
impossible, so it has to be stamped when the turn is recorded. REQ-UNC-001 and
REQ-SCP-* are therefore in tension, and the tension was resolved in favour of
the security requirement.

**Why it is compatible.** `tracelog.emit()` writes `{ts, session_id, kind,
**fields}` — the record shape is open by construction, so adding a key is not
a migration and old readers are unaffected. Records written before this change
have no `scope` and are never replayed (REQ-SCP-003), which is the same
conservative default.

### 2. REQ-RPL-002's "prepend" is implemented as "adjacent to the request"

The replay block is prepended to the operator's request, but it is placed
*after* the memory-notes and RAG-episode blocks rather than at the very front
of `llm_user_content`. Final order:

    MEMORY_NOTES → RECENT_RELEVANT_EPISODES → EARLIER_TURNS → current request

Prior turns are the most immediate context, and the turn the operator is
answering should sit next to the answer. Reading "prepend" as "at the very
front" would put a week-old lesson closer to the request than the question
asked ninety seconds ago.

### REQ-MSR-001 / REQ-MSR-002 are NOT met

Both measurement requirements are unimplemented. `GoldenPrompt`
(`acc/golden_prompts.py:111`) is `extra="forbid"` with a single `prompt: str`
field, so a multi-turn entry cannot be expressed at all, and four runners
execute prompts (`acc/cli/e2e_cmd.py`, `acc/tui/screens/diagnostics.py`,
`acc/pkg/evals.py`, `acc/webgui/routes_governance.py`). Adding the field
without threading `session_id` through every runner would produce a suite that
*appears* to measure continuity while running turn 1 only — worse than not
adding it.

**Consequence:** this change does not satisfy RP-02's G4. The mechanism is
covered by `tests/test_thread_continuity.py`; whether a small model uses it
well is unmeasured. The multi-turn golden prompt is its own change.

### One requirement gained a stronger form than specified

Nothing in the spec covers a turn the runtime *refused*. The first
implementation replayed a guardrail- or Cat-A-blocked prompt as history,
putting refused text back in front of the model on the next turn. Blocked
turns are now dropped whole. Filed as threat **ACC-TM-25**, because the
pattern generalises to every replay path — memory notes, retrieval, and any
future compaction — not just this one.
