# 20260911-question-envelope — proposal

Backlog: vault `20-backlog/usability/UX-00` item **UX-02** (the question
envelope) and §5b (the operator's answer of 2026-09-11 that sets its first
consumer). Follows `20260902-assistant-autonomy-prompt-pane-approvals` (the
in-pane gate) and the acc-prompt decision panel (#381).

## Why

ACC has one decision envelope — the oversight item, answered APPROVE or REJECT
— so every question it asks is an approval wearing a label. The acc-prompt panel
(#381) made those approvals readable; it could not make an agent *ask* anything.

The operator set the first thing it must ask (UX-00 §5b, answering Q8 on
2026-09-11): in AUTO, **super-high critical functions — file deletion, data
modification — are double-checked as a question in the options panel**, and the
reasoning marks every critical function it runs so they are tracked.

What the tree does today, measured:

* AUTO gates a call only when it is CRITICAL or carries a gate category
  (`should_gate_invocation`, `acc/operating_modes.py:169-206`). There is **no
  notion of destructive** anywhere: skill and MCP manifests declare
  `risk_level`, `system_access`, `acts_on_behalf` and nothing about deleting
  (`acc/skills/manifest.py:99-103`, `acc/mcp/manifest.py:122-129`). The only
  write classifier, `is_write_action` (`operating_modes.py:88-107`), is consulted
  in ACCEPT_EDITS alone.
* `shell_exec` is gated in AUTO (it declares `system_access`), but the question
  is the same generic *"allow shell_exec?"* for `ls` as for `rm -rf` — the
  command is a truncated `args=` line in the summary
  (`capability_dispatch.py:737-751`).
* **An "allow for this task" grant then covers every later `shell_exec` in the
  task.** The TUI keys the grant on `(task_id, kind, target)` and resolves
  matching gates itself (`PromptScreen._apply_task_grants`); the target is the
  skill id, never the command. A grant given for a directory listing approves a
  deletion in the same task without asking.
* A gate that times out is refused by the dispatcher
  (`capability_dispatch.py:704-710`) but its row stays PENDING and approvable:
  `expire_timed_out` has no caller outside tests (`acc/oversight.py:415-438`),
  and `wait_for_decision` returns the still-PENDING row at its deadline
  (`oversight.py:491-496`). An operator can approve a call that already failed,
  and the record says it was approved.

## What changes

### Phase 1 (this ship)

1. **A typed question, carried on the oversight row** (`acc/question.py`).
   `Question` — the text, options each with a key, a label, what taking it
   means, and `proceeds` (does the gated action run), plus `destructive` and
   the `evidence` that made it so. The options are the shape: a confirm is two
   of them, a choice more; there is no separate kind to keep in step.
   `OversightItem.question` and `OversightItem.answer`. Wire:
   the HEARTBEAT's pending items carry `question`, OVERSIGHT_DECISION carries the
   chosen option as `answer`, decided rows carry `answer`. The chosen option maps
   onto APPROVE / REJECT through `proceeds`, so finality (D-013), the
   two-approver rule, persistence and the way a decision reaches a worker are
   exactly what they were.

   **Why the row and not a new signal pair** (UX-00 sketched
   `PROMPT_REQUEST` / `PROMPT_REPLY`): the first consumer *is* a gate, and
   everything a gate needs already exists on the row — the wait, finality, the
   shared store that is how a decision reaches a worker agent (workers may not
   subscribe to decision subjects, `acc/nats_permissions.yaml:49-60`), and the
   TUI's permission to publish decisions (`nats_permissions.yaml:111-115`). A new
   pair would need NKey grants in two places (`nats_permissions.yaml` and the
   operator's template) for nothing the row cannot carry. A question that gates
   nothing is Phase 2, and may need its own envelope (UX-00 §5 Q2).

2. **A destructive function asks, in every mode but PLAN.**
   `destructive_evidence(kind, target, args, manifest)` in
   `acc/operating_modes.py`, beside `gate_categories`, strongest evidence first:
   * for the exec skills (`shell_exec`, `ssh_exec`, `python_exec`), the
     **command text**, checked whatever the manifest declares, because it is
     what will actually run: `rm`, `rmdir`, `unlink`, `shred`, `find … -delete`,
     `dd of=`, `mkfs`, `wipefs`, `truncate`, `git push --force`,
     `git reset --hard`, `git clean -f`, `git branch -D`,
     `kubectl|oc delete`, `podman|docker rm|rmi|volume rm|… prune`,
     `DROP|TRUNCATE TABLE|DATABASE|SCHEMA`, `DELETE FROM`, `UPDATE … SET`,
     `os.remove`, `os.unlink`, `shutil.rmtree`, `.unlink()`;
   * a declared flag — `destructive: true` on a skill manifest, a tool listed
     in `destructive_tools` on an MCP manifest;
   * otherwise the target's name (`delete`, `remove`, `drop`, `destroy`, `purge`,
     `wipe`, `truncate`, `rmdir`, `unlink`), the same "declared wins, name
     decides otherwise" rule as `gate_categories`.

   A match gates the call with a **confirm question that names what will be
   destroyed**, with the matched command as evidence, and the row is at least
   HIGH whatever the manifest says. In AUTO this is new — a tightening, never a
   thaw. Where the call was already gated, the question stops being generic.
   With no oversight queue to ask, a destructive call is **refused**, not run.
   A call the capability's own sandbox refuses (`denied_tools`) is not asked
   about: no answer could make it run.

3. **A destructive question is answered on its own, in the panel.** It is never
   batched with other requests, never covered by an "allow for this task" grant,
   never resolved by a bare "yes" or a `/allow` (`/disallow` still refuses it),
   and always takes the key twice. The panel shows the agent's question, the
   evidence and the options, whatever `ACC_PROMPT_PANEL` says; other requests
   wait behind it.

4. **Critical functions are marked.** A call that ran through a destructive or
   CRITICAL gate carries `critical` into TASK_COMPLETE's invocation list and the
   session trace log; its row in the transcript is marked.

5. **An answered question is journalled** as a trace-log oversight record
   carrying the question, the chosen option and who chose it.

6. **A gate the dispatcher gave up on is expired**, so a late answer is refused
   instead of recorded as the approval of a call that never ran.

### Phases 2–N (deferred)

* A question the **model** authors — "which of these three?" — as a reply block
  the cognitive core parses, not tied to one invocation.
* A question that **gates nothing** (UX-00 §5 Q2): its own journal kind, off the
  approval queue, possibly its own envelope.
* Input, multi-choice and masked answers (UX-00 §2 B.8–12).
* `fs_write` overwrite detection — it needs to know whether the target exists at
  dispatch time.
* **Not re-asking agent-set infusions inside a continued AUTO workflow** (the
  operator's Q8 point 2). That relaxes AUTO, so it is its own proposal under
  SIP-P2 rail 6, not a rider on this one.
* A heartbeat sweep that expires rows no dispatcher is waiting on.

## Impact

* **Affected code:** `acc/question.py` (new), `acc/operating_modes.py`,
  `acc/oversight.py`, `acc/capability_dispatch.py`, `acc/skills/manifest.py`,
  `acc/mcp/manifest.py`,
  `acc/agent.py`, `acc/tui/gate_cards.py`, `acc/tui/acc_prompt.py`,
  `acc/tui/widgets/acc_prompt_panel.py`, `acc/tui/screens/prompt.py`,
  `acc/tui/screens/compliance.py`, `acc/tui/app.py`.
* **New env knobs:** none. A switch that turned the destructive question off
  would be an AUTO thaw.
* **Tests:** `tests/test_question_envelope.py` — the classifier (positive and
  negative commands, declared flags, name fallback), the question round-trip, the
  row fields, the dispatcher gating a destructive call in AUTO and refusing it
  with no queue, the answer recorded, the timed-out row expired; plus Prompt-pane
  pilots for the panel, the no-grant rule, and the affirmation and `/allow`
  exclusions.
* **Backward compatibility:** a row without a question behaves exactly as
  before; old rows load; an OVERSIGHT_DECISION without `answer` still decides;
  no NATS permission changes. An agent older than this cannot load a row that
  carries a question and treats it as lost — which refuses the call, so a mixed
  collective fails closed.

## What stays open after Phase 1

* The command patterns are a list. A destructive command it does not recognise
  is gated only as it was before (in AUTO, `shell_exec` is gated anyway).
* The Compliance pane still approves a destructive row with its own
  confirmation modal and no typed answer — the full record surface keeps the
  ability to decide.
* Signed evidence of *which* command ran after an approval is the trace log's,
  not the row's.
