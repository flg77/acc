# 20260902-assistant-autonomy-prompt-pane-approvals — proposal

## Why

Lighthouse, 2026-09-02, Prompt pane in `ASK_PERMISSIONS`:

```
07:58:41  operator → assistant   let's engage the RH sre roles to investigate the
                                 integration into this localhost …
07:58:58  assistant-1            [PROPOSE_INFUSE:@acc/redhat-sre-roles:need Red Hat SRE
                                 expertise to assess localhost integration …]
08:00:44  operator → assistant   approved
```

Nothing happened in the pane. The operator switched to Compliance, found two
gates (`Install @acc/redhat-sre-roles@0.1.0` HIGH, `Spawn
product_security_advisor in default` MEDIUM), pressed `a`, confirmed the
high-consequence modal, came back — and the transcript still ended at
`approved`. Under `AUTO` the sequence ends the same way.

Two of the reasons were plain bugs and shipped as `ffb06ca`: the Prompt screen
was missing from `ACCTUIApp._apply_snapshot`'s fan-out, so the inline GATE CARD
from proposal 044 B8 (`05ec943`) never rendered and `approved` went to the LLM
as a prompt; and the arbiter discarded the `collective.reconcile` payload that
named the approved role. What the trace exposed underneath is a trust model
that no longer matches what the assistant is for.

**The operator's position (2026-09-02), which this change adopts:**

* Infusing roles from the catalog and putting specialists onto a user-defined
  task are the assistant's *central* function, not exceptions to be
  questioned. `AUTO` means exactly that: such requests are executed, not
  asked — tracked, and bound by Cat-A/B/C — as long as they clash with no
  rule in force.
* Only curated roles land in the catalog and are reachable. A pack that
  passes the signing floor at install is trusted by construction.
* What *does* warrant a human is anything beyond an agent's allowed tools and
  skills — above all system access and actions taken on the user's behalf.
  Those are verified and double-checked.
* In `ASK_PERMISSIONS` the user gets the question **inside the Prompt pane** —
  pop-up requests refitted to the pane's design, supporting multi-option
  dialogs — never by leaving for Compliance. Compliance keeps registering,
  tracking, and showing the history of every gate event; it stops being the
  place where the decision has to be made.

Where the tree disagrees with that today:

* **INFUSE never auto-executes.** `_NEVER_AUTOEXEC` (`acc/assistant_proposal.py:203`)
  pins `PROPOSAL_INFUSE` to the queue in every mode, with a single escape for
  `AUTO` + `ACC_OPERATOR_MODE=dev`. The code comment records this as the Stage
  1.4 decision "always Compliance pane over AUTO may infuse autonomously"
  (`7f49a9e`, `20260605-acc-pkg-trust-and-assistant`), and
  `docs/howto-demo-coding-finance-e2e.md:250` repeats it. That decision
  predates the signing floor being enforced at install
  (`acc/pkg/install_infuse.py:71-73`: `allow_unsigned` is passed only in dev;
  prod is strict). **This change reverses it deliberately.** The trust anchor
  moves from "a human clicked" to "the pack verified against the catalog's
  `required_signer`" — the anchor the catalog was built to provide
  (`acc/pkg/catalog.py:673`).
* **The wrong things are gated in `AUTO`, and the right things have no
  category.** `should_gate_invocation` (`acc/operating_modes.py:113`) gates
  only `CRITICAL` in `AUTO`; the assistant's own role admits `shell_exec` /
  `python_exec` at `HIGH` under `max_skill_risk_level: HIGH`
  (`roles/assistant/role.yaml:435`) and its text tells the model "HIGH-risk
  skills are oversight-gated by design" (`:155`) — they are not, in `AUTO`.
  Meanwhile a curated-pack install *is* gated. System access and
  acting-on-behalf (`telegram_send`, `slack_post`, `google_workspace` writes,
  `PROPOSE_PUBLISH`) are just risk levels on manifests.
* **A proposal is not a conversation turn.** The reply *is* the marker;
  `agent.py:987-1059` queues it, caches it, and publishes the full payload
  (`summary`, `rationale`, `goal_text`, `task_id`) on
  `acc.<cid>.assistant_proposal`. The TUI has no handler for that subject
  (`acc/tui/client.py` `_HANDLERS`). The pane learns of a gate as a stripped
  row on the arbiter's next HEARTBEAT (`agent.py:1515-1523`); the rationale
  never reaches the user.
* **Approval resumes nothing.** `_handle_assistant_proposals` submits and
  returns — no `wait_for_decision`, unlike `capability_dispatch._gate_on_oversight`
  (`acc/capability_dispatch.py:493`) which blocks a `[SKILL:]`/`[MCP:]` call
  until the verdict. The infuse continuation (`assistant_proposal.py:820`)
  re-uses the original `task_id`, and `TUIPromptChannel.receive` has already
  popped that future (`acc/channels/tui.py:200-219`). The follow-up reply has
  nowhere to land; an `unmet` spawn is a log line in the arbiter container.
* **"Track" has no record type.** `OversightItem.status` is
  `PENDING | APPROVED | REJECTED | EXPIRED` (`acc/oversight.py:58`). An
  auto-executed proposal leaves a log line and a bus notice, not a row in
  the history the operator reads.

## What changes

### Phase 1 (this ship — v0.11.0)

**A. Trust model — `decide_dispatch` and the gate categories**

* `PROPOSAL_INFUSE` leaves `_NEVER_AUTOEXEC`. In `AUTO` and `ACCEPT_EDITS`
  an infuse **executes**; the install path stays strict (`allow_unsigned`
  only in dev). A pack that fails the signing floor is not queued for a human
  to override — it is refused and reported: a human approval cannot make an
  unsigned pack signed. `ASK_PERMISSIONS` still asks, in the pane.
* `PROPOSAL_SPAWN` and `PROPOSAL_ROUTE` execute in `AUTO` and `ACCEPT_EDITS`
  (putting a specialist onto the task is the feature). `PROPOSAL_ROLE_UPDATE`
  stays structural — it changes what a role *may do* — so it queues below
  `AUTO`. `PROPOSAL_PUBLISH` and `PROPOSAL_ROLE_GAP` are unchanged: a
  publication crosses contexts and a gap finding is a question, not an action.
* A new **gate category** on skill/MCP manifests, orthogonal to `risk_level`:
  `acts_on_behalf: bool` (sends, posts, writes to an operator's external
  account, publishes) and `system_access: bool` (`shell_exec`, `python_exec`,
  `fs_write`, `git_*` writes). `should_gate_invocation` gates both categories
  in `AUTO` and `ACCEPT_EDITS`, `CRITICAL` as before, and everything in
  `ASK_PERMISSIONS`. Manifests that do not declare the flags inherit them
  from a name-based default table (`_WRITE_MARKERS` already exists,
  `operating_modes.py:77`) so no third-party skill silently escapes the gate.
* An invocation of a skill or MCP **not in the role's `allowed_skills` /
  `allowed_mcps`** is today a Cat-A A-017/A-018 refusal
  (`acc/skills/skill_runtime.py:76`). It becomes an *escalation request*: the
  gate item carries the missing grant, the pane offers "allow for this task",
  and refusal stays the default. The membrane does not move; the user can
  open it, per task, from where they are.
* Cat-A/B/C remain in front of every mutation. Nothing in A touches the
  regulatory layer; it changes who is asked *before* the layer runs.

**B. Tracked, not asked**

* `OversightItem` gains status `AUTO_APPROVED` with
  `approver_id = "policy:<mode>"`. Every proposal the mode executes is still
  `submit()`-ed and immediately resolved, so the Compliance history, the
  heartbeat projection, and the tracelog show the same row a human decision
  would — with the policy named as the approver.
* The reward harness ignores `policy:*` approvers
  (`acc/policy_layer.py:323` records every decision as
  `REWARD_OPERATOR_APPROVAL`, weight 1.5). A mode approving its own proposals
  must not read as operator praise; SIP rail 6 already freezes the bandit in
  `AUTO`, and this makes the input side match.

**C. The question, in the pane**

* A `PermissionRequest` region in the Prompt pane, in the place and style of
  the slash palette (above the input, transcript stays visible, focus
  captured, `Esc` = leave pending) rather than a centred modal. It renders
  from the joined proposal payload: a new `@handles("ASSISTANT_PROPOSAL")`
  route keeps `rationale` / `goal_text` on the snapshot, joined to the
  oversight row by `item.task_id == proposal_id` (`agent.py:992`).
* **Multi-option by kind**, numbered for the keyboard:
  * proposal batch (`ASK_PERMISSIONS`): one request per *reply*, each step a
    row with the reasoning summary (`_reasoning_summary`, `prompt.py:1155`);
    *1 approve all · 2 pick steps · 3 reject all · r reason*.
  * capability gate (system access / on-behalf / `CRITICAL`): *1 allow once ·
    2 allow for this task · 3 deny · r reason*. "For this task" is a
    pane-held allowlist keyed `(task_id, kind, target)`; later gates matching
    it are approved by the pane with reason `allowed-for-task`, so every
    grant is still a row.
  * escalation (off-role skill): *1 allow for this task · 2 deny*.
  * `PROPOSAL_PUBLISH` / `ROLE_GAP`: *1 approve · 2 reject · r reason*. The
    high-consequence confirm (`_is_high_consequence`, `compliance.py:904`)
    is an inline second keystroke, not a second dialog.
* Every option posts the existing `_OversightAction`; the app's
  `on__oversight_action` publish (`acc/tui/app.py:600-643`) is unchanged.
  Compliance and the CLI keep working on the same rows. The 044 B14
  affirmation ("yes" / "approved" resolves a single gate) stays.
* `/oversight approve|reject <id>` in the pane stops being a stub
  (`prompt.py:1628`).

**D. Outcomes in the thread**

* `TUIPromptChannel` keeps the correlation for a `task_id` open until the pane
  releases it (next operator send or `/done`); a TASK_COMPLETE tagged
  `_continuation_of` / `trigger=infuse_continuation` appends under the
  originating operator line.
* The pane renders the notices already published on
  `acc.<cid>.assistant_proposal` (`infuse_completed`,
  `proposal_dispatch_failed`) and a new arbiter
  `reconcile_result {assigned, unmet}` as `system` lines — *"✓ installed
  @acc/redhat-sre-roles@0.1.0 (policy:AUTO)"*, *"✗ spawn
  product_security_advisor: no dormant worker — raise `worker_pool` or run
  `./acc-deploy.sh apply worker-pool`"*.

**E. Copy that now lies**

* `roles/assistant/role.yaml:155,276` ("HIGH-risk skills are oversight-gated
  by design", "route through the human oversight queue on the Compliance
  screen") → state the real rule: system access and acting-on-behalf are
  asked in the Prompt pane; curated infusion and specialist routing are not.
* `docs/howto-demo-coding-finance-e2e.md:250`,
  `docs/WORKFLOW_infusion_to_prompt.md` §3.

### Phases 2–3 (deferred)

* **Phase 2 — the proposal waits.** Move `PROPOSE_*` in `ASK_PERMISSIONS`
  onto the blocking gate `capability_dispatch` already uses, so the
  assistant's turn suspends on the verdict and resumes with it in-thread
  (`20260825-conversational-turn-continuity` Phase 2 slot state is the
  carrier). Removes the `_continuation_of` special-casing from D.
* **Phase 3 — signed intent.** With infusion auto-executing, the signing
  floor is the trust anchor, and the reconcile trigger is a plain bus message
  anyone in the collective can publish (the `ffb06ca` fix makes a
  trigger-named role consequential). Carry the `oversight_id` and have the
  arbiter check `AUTO_APPROVED | APPROVED` against the shared queue before
  honouring a slot; decide whether an OpenShell-sandboxed spawn
  (`ACC Implementation/051`, Model 2) is its own proposal kind.
* **Sibling — `20260902-tui-profiles`.** One tab list (`nav_bar.py:29-59`)
  and four hand-maintained shadows (`NavigationBar.BINDINGS`, `App.SCREENS`,
  the help map, `_SNAPSHOT_SCREENS` — the last was the `ffb06ca` bug).
  Consolidate, then `--profile user|operator` is a filter. The pane-side
  request region must exist first; it is what the user profile is built
  around.

## Impact

* **Affected code:** `acc/assistant_proposal.py` (`decide_dispatch`,
  `_NEVER_AUTOEXEC`), `acc/operating_modes.py` (gate categories),
  `acc/skills/manifest.py` + the MCP manifest model (`acts_on_behalf`,
  `system_access`), `acc/capability_dispatch.py` (escalation item),
  `acc/oversight.py` (`AUTO_APPROVED`), `acc/agent.py` (record on execute,
  `reconcile_result`), `acc/policy_layer.py` (approver filter),
  `acc/tui/client.py` + `models.py` (proposal route),
  `acc/tui/screens/prompt.py` (request region, allow-for-task, `/oversight`,
  `/done`, outcome lines), `acc/tui/widgets/permission_request.py` (new),
  `acc/channels/tui.py`, `roles/assistant/role.yaml`, two docs.
* **New env knobs:** none. `ACC_PROMPT_PERMISSION_REGION=0` falls back to the
  inline GATE CARD (a kill switch for demos, not a gate). The dev-only
  `allow_unsigned` path is untouched.
* **Tests:** ~18. `decide_dispatch` table per (mode × kind) incl. the reversal,
  and that an unsigned pack is refused, not queued; gate categories with and
  without declared flags; off-role skill → escalation item; `AUTO_APPROVED`
  row on execute; reward harness ignores `policy:*`; proposal route joins
  its row; one reply → one request with N rows; each option posts the
  expected action(s); allow-for-task auto-resolves the next matching gate
  with a row; `Esc` leaves PENDING + card; continuation appends; `unmet`
  renders the hint; `/oversight` resolves; B14 still resolves a single gate.
* **Backward compatibility:** `ASK_PERMISSIONS` and `PLAN` behave as today
  except where the question is asked. `AUTO` and `ACCEPT_EDITS` change
  behaviour on purpose — a curated infuse and a spawn now execute — and the
  CHANGELOG entry names that as a behaviour change, not a fix. Manifests
  without the new flags keep working via the default table. Older TUIs see
  `AUTO_APPROVED` rows as decided history; older agents never send the
  proposal payload, so the pane degrades to the card.

## What stays open after Phase 1

* **What "curated" means beyond the signature.** Phase 1 trusts any pack
  whose `required_signer` verifies. A per-deployment catalog allowlist or a
  tier floor (`acc/pkg/agent_bom.py:121` `signing_floor_ok`) is the next
  refinement and belongs with the ecosystem work, not here.
* **Who approved.** `approver_id` is `tui:anonymous` on every surface
  (`app.py:631`). Making the question one keystroke closer makes attribution
  matter more; it belongs with the TUI auth work
  `20260531-role-perception-profiles` Phase 5 deferred on.
* **`ACCEPT_EDITS` and spawn.** Phase 1 lets a spawn execute there because a
  specialist is reversible by the next prompt. If a live trace shows
  worker-pool churn, move it back to queue.
* **The heartbeat as transport.** Pending rows ride the arbiter HEARTBEAT
  (≤ 50, summaries cut at 200 chars). Phase 1 adds the proposal stream for
  the *why*; the eventual shape is one stream and a heartbeat that carries
  counts.
