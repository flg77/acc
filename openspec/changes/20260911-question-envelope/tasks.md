# 20260911-question-envelope — tasks

## Phase 1 — the question envelope; destructive calls ask

### 1.1 The question
- [x] `acc/question.py`: `Question` / `QuestionOption` (text, options with
      `proceeds` and `detail`, `destructive`, `evidence`), `to_dict` /
      `from_dict`, `destructive_confirm()` naming what will be destroyed

### 1.2 The row
- [x] `OversightItem.question` / `.answer` (defaults, so old rows load)
- [x] `submit(question=)`; `approve(answer=)` / `reject(answer=)` refuse an
      answer that is not an option, or whose `proceeds` contradicts the decision;
      no answer still decides (Compliance pane, acc-cli)
- [x] `expire(oversight_id)` — one row, PENDING only

### 1.3 Destructive calls ask
- [x] `destructive_evidence()` in `acc/operating_modes.py`: exec-skill command
      text (always), declared `destructive` / `destructive_tools`, the name
- [x] skill manifest `destructive: bool | None`; MCP manifest `destructive_tools`
- [x] `_dispatch_one`: a destructive call is gated in every mode but PLAN,
      with the question on the row, at least HIGH; refused when there is no queue
- [x] an escalation of a destructive call asks the question once, not twice
- [x] a call its own sandbox refuses (`denied_tools`) is not asked about —
      found by the sweep (`test_manifest_sandbox_is_never_escalated`)
- [x] summary tag `DESTRUCTIVE`

### 1.4 Critical functions, the journal, expiry
- [x] `InvocationOutcome.critical` + `.question` (how the question ended)
- [x] TASK_COMPLETE invocations carry `critical`; the transcript trace row is marked
- [x] `_tracelog_turn`: `tool_call` carries `critical`; an answered question is
      an `oversight` record (`proposal_kind=question`, answer, approver, evidence)
- [x] the gate expires a row it stopped waiting on; a decision that landed
      meanwhile is honoured

### 1.5 The wire
- [x] HEARTBEAT pending items carry `question`, decided items `answer`
- [x] OVERSIGHT_DECISION carries `answer`; `_handle_decision` passes it on
- [x] no new signal, no NATS permission change

### 1.6 The Prompt pane
- [x] `GateCard.question`; `is_destructive()`; `DESTRUCTIVE` card copy
- [x] request options come from the question's options — none is a grant
- [x] the panel asks the agent's question, shows each option's detail, marks it
      destructive, takes the key twice at any risk, posts the answer
- [x] a destructive request goes to the panel alone, whatever `ACC_PROMPT_PANEL`
      says; other requests wait behind it
- [x] never auto-resolved by a task grant; "yes" and `/allow` refuse it;
      `/disallow` still refuses the call
- [x] `_OversightAction.answer` → the app's OVERSIGHT_DECISION payload

### Verification
- [x] `tests/test_question_envelope.py` — classifier, question, row, dispatcher,
      agent handler + trace, pane core + pilots
- [x] existing oversight / dispatch / panel suites green
- [x] full sweep — 5691 passed; the 8 reds are the workstation's known
      pre-existing ones (4 catalog cosign, 1 model registry), the tour-state
      order flake in `test_tui_profiles` (green alone, green on origin/main), and
      one real regression, fixed (a sandboxed destructive call was asked about)
- [ ] lighthouse smoke: an AUTO task that runs `rm` asks in the panel; "run it"
      runs it and the trace row is marked critical; "don't run it" refuses it

## Phase 2 (deferred)
- [ ] a question the model authors (a reply block), not tied to one invocation
- [ ] a question that gates nothing — its own journal kind, off the approval queue
- [ ] input, multi-choice and masked answers
- [ ] `fs_write` overwrite detection (does the target exist at dispatch time)
- [ ] not re-asking agent-set infusions in a continued AUTO workflow — its own
      proposal under SIP-P2 rail 6
- [ ] a heartbeat sweep expiring rows no dispatcher waits on
- [ ] the Compliance pane shows the question and asks for the typed answer
