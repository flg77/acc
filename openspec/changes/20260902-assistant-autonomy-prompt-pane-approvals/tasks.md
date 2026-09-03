# 20260902-assistant-autonomy-prompt-pane-approvals — tasks

## Phase 0 (shipped — `ffb06ca`, PR pending) — the two plain bugs
### 0.1 Prompt pane receives snapshots
- [x] `("prompt", PromptScreen)` in `ACCTUIApp._apply_snapshot` (`acc/tui/app.py`)
- [x] pilot through `_apply_snapshot` asserts a GATE CARD renders (`tests/test_tui_smoke.py`)
### 0.2 Approved spawn reaches desired state
- [x] arbiter `_on_reconcile` decodes the trigger; `_absorb_reconcile_trigger` records
      `(role, cluster_id)`; `_merge_proposed_agents` appends to the spec (or builds one
      when the container has no `collective.yaml`) (`acc/agent.py`)
- [x] 4 tests: no-yaml assign, append to on-disk slots, bare `{}` nudge is inert,
      idempotent after promotion (`tests/test_worker_reconcile.py`)
### Verification
- [x] affected suites: 129 passed
- [ ] full sweep
- [ ] lighthouse: "approved" in the Prompt pane resolves the gate; arbiter logs the slot

## Phase 1 (v0.11.0) — trust model + the question in the pane
### 1.1 decide_dispatch — infusion and specialists are the feature
- [x] `PROPOSAL_INFUSE` out of `_NEVER_AUTOEXEC`; `AUTO`/`ACCEPT_EDITS` → EXECUTE;
      `ASK_PERMISSIONS` → QUEUE (`acc/assistant_proposal.py`)
- [x] `PROPOSAL_SPAWN`, `PROPOSAL_ROUTE` → EXECUTE in `ACCEPT_EDITS`; `ROLE_UPDATE` stays QUEUE
- [x] signing-floor failure at install → refused + `proposal_dispatch_failed`, never queued
      (`_dispatch_infuse` publishes the notice with the installer's reason)
- [x] retire the dev-only INFUSE escape (subsumed); keep `allow_unsigned` dev-only;
      `decide_dispatch(operator_mode=)` kept and ignored for call-site compatibility
- [x] tests: full (mode × kind) table (`tests/test_assistant_proposal.py::test_full_dispatch_table`);
      unsigned pack refused, not queued (`tests/test_infuse_dispatch_refused.py`)
- [x] D-011 in `docs/DECISIONS.md`; CHANGELOG **Changed**; demo-doc row
### 1.2 Gate categories
- [x] `acts_on_behalf` / `system_access` (`Optional[bool]`, None = undeclared) on skill +
      MCP manifest models; name table for undeclared manifests; declared `false` opts out
      (`acc/operating_modes.py` `gate_categories`)
- [x] `should_gate_invocation(categories=)`: gate both categories in `AUTO` + `ACCEPT_EDITS`;
      dispatcher passes the manifest's categories; oversight row leads with the category and
      carries the manifest risk (`acc/capability_dispatch.py`)
- [x] declared on `shell_exec`, `python_exec`, `fs_write` (system access) and `telegram_send`,
      `slack_post`, `mattermost_post` (acts on behalf); `git_status` / `git_log_recent` are
      reads (nothing to declare); `google_workspace` writes fall to the name table per tool
      (`gmail_send` → acts_on_behalf) — no server-wide flag on a read-first server
- [x] off-role skill/MCP → `ESCALATION` gate item naming the missing grant instead of a
      bare A-017/A-018 refusal; on APPROVE `_role_with_grant` widens a copy of the role for
      that one call (target + `requires_actions` + ceiling); REJECT / EXPIRED / headless /
      no queue still refuse; manifest `denied_tools` never escalated; no second category
      question on the approved call (`acc/capability_dispatch.py`; operator yes 2026-09-02)
- [x] tests: `tests/test_escalation_request.py`
- [x] tests: `tests/test_gate_categories.py` (declared / defaulted / opted-out / mock-safe;
      the six shipped manifests; AUTO + ACCEPT_EDITS gating; dispatcher gates a
      system-access skill under AUTO with a category-led row and the manifest risk, and
      still passes an uncategorised HIGH skill)
### 1.3 Tracked, not asked
- [x] `OversightItem.status = AUTO_APPROVED`, `approver_id = "policy:<mode>"`, `outcome`;
      `HumanOversightQueue.record_auto_approved` (born resolved) + `recent_decisions`
      (capped decided list, 24 h TTL); agent `_record_auto_approved` on every executed
      proposal (`acc/oversight.py`, `acc/agent.py`)
- [x] `KIND_OVERSIGHT` tracelog record (`tracelog.log_oversight`) so the row outlives
      the queue TTL — no `OVERSIGHT_DECISION` published (would re-dispatch)
- [x] heartbeat `oversight_recent_items` → snapshot → Compliance **DECISION HISTORY**
      table with `By` column (`acc/tui/client.py`, `models.py`, `screens/compliance.py`)
- [x] `policy_layer._on_oversight` skips `policy:*` approvers
- [x] tests: `tests/test_oversight_auto_approved.py` (queue in-process + Redis path,
      EXECUTE branch rows + trace, failed dispatch, no-queue trace, reward filter,
      observer route arbiter-only, Compliance render)
### 1.4 PermissionRequest region (Prompt pane)
- [x] `acc/tui/widgets/permission_request.py` — focusable region above the input (takes
      focus on arrival, `Ctrl+G` returns); numbered options; `r` prefills
      `/oversight reject <id> ` for a reason; `Esc` leaves PENDING + focus back
- [x] `@handles("ASSISTANT_PROPOSAL")` + `CollectiveSnapshot.assistant_proposals[proposal_id]`
      (`acc/tui/client.py`, `acc/tui/models.py`); `publish_proposal_pending` stamps
      `signal_type`; join by `item.task_id == proposal_id` in `gate_cards.pending_gates`
- [x] option sets (`gate_cards.request_options`): proposal batch / capability gate /
      escalation / publish+gap; HIGH/CRITICAL approve = same key twice
- [x] allow-for-task grants `(task_id, kind, target)` → pane resolves the next matching gate
      with reason `allowed-for-task`
- [x] `/oversight pending|approve|reject` → `_resolve_gate` (reason carried); `/done` → 1.5
- [x] `ACC_PROMPT_PERMISSION_REGION=0` → GATE CARD only
- [x] tests: `tests/test_permission_request.py`
### 1.5 Outcomes in the thread
- [x] observer `register_task_followup_listener` (a later TASK_COMPLETE on a held task id
      fans out instead of "NO listener"); the pane holds the thread after a reply and
      releases it on the next send or `/done`; continuation appended as `↩` under the
      exchange (`acc/tui/client.py`, `prompt.py`; the channel's one-shot Future is untouched)
- [x] arbiter `_publish_reconcile_result {assigned, unmet, already_active}` after a
      reconcile that assigned / left unmet / was triggered for a named role; outcome
      notices stamped `ASSISTANT_PROPOSAL_OUTCOME`; `acc/tui/outcomes.py` words them; the
      pane renders each once with the worker-pool hint on `unmet`
- [x] `/done` releases the thread
- [x] tests: `tests/test_outcomes_in_thread.py`
### 1.6 Copy + docs
- [x] `roles/assistant/role.yaml` — the marker contract, the skills paragraph, the routing
      discipline (infusion / role gap), the governance paragraph, and the YAML comments
      all state the D-011 rule; "give a one-line reason" added so the request region has
      something to show
- [x] `docs/howto-demo-coding-finance-e2e.md:250` (1.1), `docs/WORKFLOW_infusion_to_prompt.md`
      §3 (spawn path + where the decision is made), `acc/tui/help/prompt.md` (layout,
      keys, entry types, an "Approvals" section, `/done`)
- [x] CHANGELOG: **Changed** (1.1, 1.6) + **Added** (1.2, 1.2b, 1.3, 1.4, 1.5)
- [ ] reasoning bench on the edited role prompt (see PR)
### Verification
- [ ] targeted tests (≈18 new)
- [ ] full sweep
- [ ] lighthouse smoke, `AUTO`: the 2026-09-02 prompt → install + spawn + route with no
      question asked, three `AUTO_APPROVED` rows in Compliance, outcome lines in the pane
      (needs `./acc-deploy.sh apply worker-pool`)
- [ ] lighthouse smoke, `ASK_PERMISSIONS`: same prompt → one request region with two rows,
      `1` approves both, outcome lines follow; Compliance shows the same two rows APPROVED
- [ ] lighthouse smoke, `AUTO`: `[SKILL: shell_exec …]` from the assistant → capability
      request in the pane (system access), `2` allow-for-task, second call passes with a row

## Phase 2 (deferred) — the proposal waits
- [ ] `PROPOSE_*` in `ASK_PERMISSIONS` through the blocking gate; verdict returned into
      the assistant's turn (continuity Phase 2 slot state)
- [ ] drop `_continuation_of` special-casing from 1.5

## Phase 3 (deferred) — signed intent
- [ ] reconcile trigger carries `oversight_id`; arbiter verifies `APPROVED | AUTO_APPROVED`
- [ ] OpenShell-sandboxed spawn as its own proposal kind — decide

## Sibling
- [x] `20260902-tui-profiles` filed — consolidate the four screen lists behind one
      registry (the #321 guard), then `--profile user|operator`
