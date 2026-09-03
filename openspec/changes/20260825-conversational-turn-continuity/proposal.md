# 20260825-conversational-turn-continuity — proposal

## Why

A Hermes trace on 2026-08-25 — the operator asks *"how can I configure access to
my email account so you can take care of spam etc."* — resolves in four moves:
match the request to capabilities not yet loaded, probe the host
(`which himalaya`), offer two integration paths, then **ask one clarifying
question and act on the answer next turn**.

The ACC Assistant can do the middle two today. It holds `shell_exec` +
`python_exec` (`roles/assistant/role.yaml`) and the control perception profile
already renders available MCPs (`acc/perception.py:649`). It cannot do the
fourth, because ACC has no conversational continuity at all:

* `PromptChannel` is `send()` → `receive()` over a single TASK_ASSIGN /
  TASK_COMPLETE pair (`acc/channels/base.py`). There is no thread id in the
  contract.
* The agent *reads* `session_id` off the task payload and falls back to
  `task_id` when it is absent (`acc/agent.py:1336`). **No channel ever sets
  it.** The only other occurrence in the tree is the TUI reading it back off a
  restored payload (`acc/tui/screens/prompt.py:2472`). Every prompt is
  therefore a session of exactly one turn.
* `sessions.context_for()` already renders a session as `operator: … /
  <role>: …` lines (`acc/sessions.py:188`) and its only callers live in
  `acc/cli/sessions_cmd.py`. The prompt-assembly path never consumes it.
* The TUI Prompt screen already holds a complete client-side thread —
  `self._session_id` at `acc/tui/screens/prompt.py:389`, `_append_history`
  plus autosave throughout — and never sends any of it.

The transcript is durable, the renderer exists, the payload field exists and is
honoured, and `sessions.resume()` already links a child session to its parent
without rewriting history. What is missing is the wire between them.

**The reason this is worth doing now is the model tier, not the ergonomics.**
With no continuity every turn must be self-contained: the model re-derives the
operator's intent from one line, reconstructs where in the task it is, holds the
remaining plan in one forward pass, and emits a correct dispatch marker — all at
once, with no memory of what it just asked. That is the hardest available turn
shape, and ACC hands it identically to a frontier model and to a 3B-class model
on an edge box. `models.yaml.example:139` is the admission: the assistant is
mapped to `claude-sonnet` and annotated *"the demo hinge"*. The harness is
currently what forces that choice.

Give the harness the turn state and each turn becomes: read what is already
known, fill or request **one** missing thing, emit **one** marker. Small models
are competitive at short structured single-decision turns and weak at
long-horizon planning; this change converts the second shape into the first. The
lighthouse edge posture — small-context models on constrained hardware, no
frontier fallback available — is where that conversion pays and where its
absence costs most.

Escalating to a larger model or an additional role stays a **policy** decision
with a declared trigger (Phase 3). It is not failover — `acc/llm_failover.py`
reacts to an unavailable model; escalation is taken while everything works — and
it is not the model's own judgement, which is the line
`20260530-role-proposal-assistant-agent-of-agents` and `agency_limiter.py` draw
and this change does not reopen.

Design context and the cross-round alignment table live in the roadmap proposal
`RP-02 — Conversational continuity and the small-model harness` (Obsidian:
`ACC Roadmap/Proposals/`).

## What changes

### Phase 1 (this ship — v0.8.x)

* **Thread id on the contract.** `PromptChannel.send()` gains an optional
  `session_id`; the TUI passes the `_session_id` it already maintains; the
  TASK_ASSIGN payload carries it. The agent's existing read at
  `acc/agent.py:1336` starts finding a real value instead of falling back to
  `task_id`.
* **Server-side replay.** `CognitiveCore` consumes `sessions.context_for()` and
  injects prior turns into the user content as a distinct, labelled block. The
  replay is read from the durable tracelog — never from client-supplied text —
  so `20260817-*`/DS-01's *model-visible means logged* invariant holds by
  construction rather than by review.
* **Scope enforcement at assembly.** The replay is filtered on the same scope
  key `acc/memory_scope.py` gives episodes. A thread whose scope does not match
  the requester replays empty; it does not error, and it is never partially
  disclosed.
* **A hard cap, labelled as a stopgap.** `ACC_THREAD_TURNS` (default 6) and
  `ACC_THREAD_CHARS` bound the replay. ACC has no context compaction anywhere
  (`acc/cognitive_core.py` contains none), so an uncapped thread is a
  context-overflow bug aimed squarely at the smallest deployments. Compaction is
  separate work and must not block this.
* **One role, not all of them.** A `thread_continuity` field on
  `RoleDefinitionConfig`, default `False`, set `True` only on
  `roles/assistant/role.yaml`. Blast radius is one role and the default
  deployment is unchanged.
* **A two-turn golden prompt.** The suite is single-turn throughout, so
  "a 3B model can drive this" is currently unfalsifiable. Phase 1 adds one
  multi-turn entry and records a baseline on a small model.

### Phases 2–4 (deferred)

* **Phase 2 — the frame.** Harness-held slot state for a multi-turn task (what
  was asked, what is still missing, what is decided) so the model reads state
  instead of reconstructing it. This is where the small-model claim is actually
  earned; Phase 1 only makes it possible.
* **Phase 3 — escalation policy.** Declared triggers (no slot filled in N
  turns; task type outside `task_types`; two consecutive reviewer rejections; an
  existing `ROLE_GAP`), a recorded reason, and a pre-authorised bounded pool.
* **Phase 4 — the motivating scenario.** Capability search over skills and MCPs
  (`catalog_query` enumerates roles only today, while `.accpkg` already carries
  `skills`/`mcps` per `acc/pkg/manifest.py:278`); an oversight item kind that
  returns operator *input* rather than a verdict (`OversightItem` is a summary
  plus approve/reject, `acc/oversight.py:47`); and `acc/credentials/broker.py`
  wired to it — it is fully built, `start_connect` included, and its only
  callers are its own tests.

## Impact

* **Affected code:** `acc/channels/base.py` (protocol), `acc/channels/tui.py`,
  `acc/channels/slack.py`, `acc/channels/webgui.py`,
  `acc/tui/screens/prompt.py`, `acc/cognitive_core.py`, `acc/sessions.py`,
  `acc/config.py` (`RoleDefinitionConfig`), `roles/assistant/role.yaml`.
* **New env knobs:** `ACC_THREAD_TURNS` (default `6`), `ACC_THREAD_CHARS`
  (default `4000`), `ACC_THREAD_CONTINUITY` (`0` disables globally — a kill
  switch, not a feature gate).
* **Tests:** ~12. Contract (`session_id` optional and threaded through each
  channel), replay (ordering, cap, empty-thread), scope (two requesters cannot
  see each other's thread; unattributed replays empty), role gating (a role
  without the flag is byte-identical to today), and the two-turn golden prompt.
* **Backward compatibility:** `session_id` is optional. Absent → the current
  fallback to `task_id` and an empty replay, which is exactly today's behaviour.
  Roles without `thread_continuity` are unaffected. No change to the tracelog
  schema, the retention policy, or the audit record's append-only property.

## What stays open after Phase 1

* **Where a thread ends** — idle timeout, explicit close, or turn count. Too
  eager loses continuity mid-task; too lazy replays a week-old thread into a
  small context window.
* **Whether a thread crosses roles.** The assistant's seed context says to treat
  a specialist's reply as part of its own answer, which argues the specialist
  should see the thread — but that is a capability grant nobody has reviewed.
* **Which turns replay** — all of them, or only operator prompts and final
  replies. Reasoning traces and tool results are most of the tokens and much of
  the value.
* **Whether the compat endpoint gets threads.** It already accepts
  OpenAI-shaped conversation arrays, so honouring them is nearly free; it is
  also the least-authenticated prompt surface in the system, so it should not be
  free by accident.
* **Compaction.** The Phase 1 cap is a stopgap. Real compaction plus an
  agent-facing lookup for what fell out is separate, and its priority should
  rise on the strength of this change rather than wait behind it.
