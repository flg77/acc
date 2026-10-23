# 20261018-plan-card — proposal

Vault proposal: `40-proposals/ACC Implementation/059 — The plan card`.
Builds on OpenSpec `20261015-ask-once-p1-mission-grant` (PR #551) and
`20261015-ask-once-p5-standing-grants`. This change is functional, not
role-specific: the plan card is a mechanism of the mission, and the assistant
is its first user.

## Why

The operator, 2026-10-18: "Plan cards are necessary and should be logged, they
are key for traceability and also play a role when it comes to learning
capabilities."

058 Phase 1 asks its one question at the **first gated call** of a thread
(`acc/mission.py` `mission_question`: "This call is shell_exec …; allowing the
mission also covers this thread's later system-access calls"). The operator
approves a scope without seeing a plan. The `Mission` lives in Redis
(`MissionStore`). No durable record keeps what was planned next to what ran,
and lessons never see a plan.

The chief-of-staff A/B (`20261017-role-proposal-assistant-chief-of-staff`, P2)
shows the assistant infusing a pack while `devops_engineer` sits installed and
idle. `20261018-role-proposal-assistant-reuse-before-infuse` attached that
fact to an infuse approval that AUTO and ASK_ONCE never show. The plan card is
where it belongs.

## What changes

### Phase 1 (this change)
- `[PROPOSE_PLAN:{json}]` marker and a `Plan` model (`acc/plan_card.py`).
  - The plan has steps, each with a role, a staffing choice (reuse / infuse /
    author) and capabilities, plus hosts, packs and a budget.
  - ACC validates it and clamps it to the principal's ceiling and each role's
    grants.
  - If the plan is malformed, ACC builds one from the proposals in the same
    reply.
- Reuse annotation: every infuse step lists the installed roles that are not
  running (the logic of `annotate_infuse_with_installed_roles`, moved).
- The plan card **is** the mission card. Approving it creates
  `Mission(plan=…, hosts=…)`. Proposals that match a plan step execute in
  every mode except PLAN.
- Plan amendment card: a new host, pack or capability class shows only the
  difference.
- Durable record:
  - `PLAN_APPROVED`, `PLAN_AMENDED` and `PLAN_CLOSED` go to the tracelog
    (new `plan` kind) and to a Redis index;
  - the closing report carries `planned_vs_actual`;
  - `acc-cli mission show <id>`.
- Learning: on close, write one episode per role involved. A detector proposes
  lessons from recurring planned-vs-actual differences, and never applies one.
- Assistant prompt and rubric: plan first; reuse before infuse.

### Deferred
- Plan outcomes shared to the hub as lessons (`20260923-lessons-that-travel`),
  pending operator answer 059 Q3.
- WebGUI plan editor (edit steps before approving).

## Impact
- **Affected code:**
  - new `acc/plan_card.py`;
  - `acc/assistant_proposal.py`, `acc/mission.py`, `acc/capability_dispatch.py`,
    `acc/cognitive_core.py`, `acc/tracelog.py`;
  - `acc/cli/mission_cmd.py`, the TUI decision panel, the WebGUI Work page,
    `acc ask`;
  - `roles/assistant/role.yaml` and `eval_rubric.yaml`.
- **New env knobs:** none. Mission knobs from 058 apply.
- **Tests:**
  - parse / validate / clamp;
  - the fallback plan;
  - plan-to-mission;
  - amendments;
  - the ledger round trip;
  - lesson-candidate detection;
  - the P2 fixture (dormant role on the card).
- **Backward compatibility:** a thread with no plan behaves as 058 does: the
  mission card appears at the first gated call.

## What stays open after Phase 1
- Small models may never emit `PROPOSE_PLAN`. The fallback plan keeps the card,
  but the step list is then only as good as the proposals.
- Which changes need an amendment card (059 Q1) is a policy choice. Phase 1
  uses: a new host, pack or capability class needs one; a new role does not.
