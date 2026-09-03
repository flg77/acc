# 20260825-conversational-turn-continuity — tasks

Status legend: `[ ]` not started · `[x]` done. Nothing here is started.

## Phase 1 (v0.8.x) — the wire

### 1.1 Thread id on the contract

- [x] Add optional `session_id: str | None = None` to `PromptChannel.send()` in
      `acc/channels/base.py`; document that omitting it preserves today's
      one-turn-per-session behaviour.
- [x] Thread it through `acc/channels/tui.py` into the TASK_ASSIGN payload.
- [x] Same for `acc/channels/slack.py` and `acc/channels/webgui.py`. A surface
      that does not pass one must degrade to today's behaviour, not to a shared
      thread.
- [x] `acc/tui/screens/prompt.py` passes the `_session_id` it already holds
      (`:389`) on every send.
- [x] Confirm `acc/agent.py:1336` needs no change — it already reads
      `data["session_id"]` with a `task_id` fallback. If it does need one, that
      is a finding worth writing down, not a silent edit.

### 1.2 Server-side replay

- [x] `acc/sessions.py`: add `context_for(..., max_chars=...)` alongside the
      existing `limit`, returning the **most recent** turns when truncating.
- [x] `acc/cognitive_core.py`: build the replay block before the LLM call at
      `:1159` and prepend it to `llm_user_content` under an explicit heading
      that names it as prior turns of this conversation — not as instructions,
      and not merged into the operator's current prompt.
- [x] Replay is read from the tracelog only. Assert in review that no
      client-supplied transcript can reach the block; that is the DS-01
      invariant and it is the reason for this design.
- [x] Empty or unreadable thread → empty block, never an error and never a
      partial one.

### 1.3 Scope enforcement

- [x] Filter the replay on the scope key from `acc/memory_scope.py`, the same
      one episodes use. Do not introduce a second partitioning scheme.
- [x] A thread whose scope does not match the requester replays **empty**.
- [x] Pre-attribution / unattributed sessions replay empty rather than
      defaulting to the current requester.

### 1.4 Bounds

- [x] `ACC_THREAD_TURNS` (default `6`) and `ACC_THREAD_CHARS` (default `4000`).
- [x] `ACC_THREAD_CONTINUITY=0` disables replay globally.
- [x] Comment the cap as a stopgap pending context compaction, with the reason:
      ACC has no compaction at all, so an uncapped thread overflows the smallest
      models first.

### 1.5 Role gating

- [x] Add `thread_continuity: bool = False` to `RoleDefinitionConfig` in
      `acc/config.py`.
- [x] Set `thread_continuity: true` in `roles/assistant/role.yaml`, with a
      comment pointing at this change id.
- [x] Leave every other role untouched. Verify a non-flagged role's assembled
      prompt is byte-identical to the pre-change output.

### 1.6 Measurement — NOT DONE, and it is a bigger job than one YAML file

- [ ] Add a two-turn golden prompt: turn 1 asks a question that cannot be
      answered without a clarification; turn 2 supplies it. Pass condition is
      that the reply to turn 2 acts on turn 1 rather than re-asking.
      **BLOCKED — the schema is single-turn by construction.**
      `GoldenPrompt` (`acc/golden_prompts.py:111`) is `extra="forbid"` with one
      `prompt: str` field, so a `turns:` list cannot simply be added to a YAML
      file — it fails validation. Multi-turn also has to be honoured by every
      runner that executes a prompt, and there are four:
      `acc/cli/e2e_cmd.py`, `acc/tui/screens/diagnostics.py`,
      `acc/pkg/evals.py` and `acc/webgui/routes_governance.py`. Adding the
      field without threading `session_id` through those runners is worse than
      not adding it: the suite would appear to measure continuity while
      silently running turn 1 only. This is the "a multi-turn golden prompt
      does not exist and has to be built" of RP-02 §8 Q5, and it is its own
      change.
- [ ] Record a baseline for a small model (3B-class / MoE with ~3B active) and
      for the current `claude-sonnet` assistant mapping
      (`models.yaml.example:139`), so the gap is a number.
      **BLOCKED — needs a live model on lighthouse**, which this workstation
      session cannot reach. Depends on the item above regardless.

> **Consequence for G4.** RP-02's "a 3B-class model completes a
> self-configuration conversation end-to-end, measured rather than asserted"
> is **not** demonstrated by this ship. Phase 1 makes it *possible* — it does
> not make it *measured*. The mechanism is covered by unit tests
> (`tests/test_thread_continuity.py`): a second turn provably carries the
> first exchange, in order, once, from durable records. What is untested is
> whether a small model then uses it well. Do not cite Phase 1 as evidence
> for the small-model claim.

### Verification

- [x] `session_id` absent → behaviour identical to today (fallback to
      `task_id`, empty replay).
- [x] Two turns on one thread: the second prompt's assembled user content
      contains the first exchange, in order, once.
- [x] Cap holds: `ACC_THREAD_TURNS=2` on a six-turn thread replays the last two.
- [x] Two requesters on one agent cannot see each other's thread.
- [x] A role without `thread_continuity` produces byte-identical prompt output.
- [x] Every replayed line has a corresponding durable tracelog record.
- [x] Targeted tests green.
- [x] Full sweep — classify any red against `origin/main` before treating it as
      a regression.
- [ ] Lighthouse smoke on a small model: the two-turn golden prompt end to end.
      **NOT DONE** — no lighthouse access from this session, and it depends on
      the blocked 1.6 golden prompt.

## Phase 2 (deferred) — the frame

- [ ] Harness-held slot state for a multi-turn task; the model reads it instead
      of reconstructing it.
- [ ] Frame rendered into the prompt as structure, not prose.
- [ ] Re-measure the small-model baseline against the Phase 1 number.

## Phase 3 (deferred) — escalation policy

- [ ] Declared trigger set; a recorded reason on every escalation.
- [ ] Pre-authorised bounded pool; the model never selects its own escalation.
- [ ] Keep the separation from `acc/llm_failover.py` explicit in code and docs.

## Phase 4 (deferred) — the motivating scenario

- [ ] Capability search over skills and MCPs, not just roles.
- [ ] An oversight item kind that returns operator input rather than a verdict.
- [ ] Wire `acc/credentials/broker.py` to it — currently test-only.
