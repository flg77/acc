# `role_lifecycle` skill + task-distribution governor

> **Status: DRAFT for internal review.** Proposal 2 of 2. Builds on the
> personalization overlay (`docs/agent-personalization-overlay-DRAFT.md`) — it
> reconciles the roster that `collective.md`/`collective.yaml` declare and acts on
> the capability gaps `AGENTS.md` surfaces. Author: hub (workstation),
> 2026-06-25. Scope: `acc-spearhead` (dev source; promote vetted → mirror
> `flg77/acc` via acc-promote).

## 1. Problem

The Assistant can *propose* one-off mutations today — `[PROPOSE_INFUSE:…]`,
`[PROPOSE_SPAWN:…]`, `[PROPOSE_ROUTE:…]` (`roles/assistant/role.yaml:191–209`,
`acc/assistant_proposal.py`). But it has **no first-class skill for the role
lifecycle as a whole**, and three things are missing:

1. **"Infuse" is overloaded and incomplete.** In code it means only *install*
   (`execute_infuse_install` → `.acc/packages/`, filesystem state). Bringing a
   role *online* is a separate `PROPOSE_SPAWN` → signed `ROLE_ASSIGN`, and
   taking one *offline* has **no first-class path at all** (today only the
   deploy-layer `./acc-deploy.sh apply --prune` reconciles down). A user who says
   "bring the legal-review role online, then stand it down when we're done" has
   no single governed verb for it.
2. **No active task-distribution governance.** Nothing watches *how* TASK_ASSIGN
   load is spread across roles and proposes rebalancing — the Assistant is
   reactive, not a steward of the running team.
3. **No reconciliation target for the overlay.** Proposal 1 makes `collective.md`
   a declarative roster descriptor and `AGENTS.md` a per-agent desired-capability
   doc. Something has to *reconcile* desired → actual. That something is this
   skill.

The operator's intent (this session): the Assistant **actively infuses and
deactivates roles on demand**, and exercises **active governance over how tasks
are distributed** — all through the compliant, observable, gated path.

## 2. Goals / non-goals

**Goals**

- One **`role_lifecycle`** skill with `action ∈ {install, activate, deactivate,
  status}` that composes the existing seams instead of being a thin wrapper over
  `execute_infuse_install`.
- A **`task_routing_governor`** that observes roster + load and proposes
  rebalancing — same `collective.yaml`-edit + reconcile mechanism as deactivate.
- **Deactivate via declarative edit + reconcile-down**, not a fire-and-forget
  signal (operator decision: fs-observable, git-diffable, log-visible, debuggable).
- Every mutation **stages a proposal into the existing Compliance oversight
  queue** — no new approval wire, no silent execution.

**Non-goals (this proposal)**

- Granting capability *beyond* a role's signed envelope — that is the signed
  infuse / A-BOM path (already exists; this skill *invokes* it).
- A bespoke drain/RPC signal for deactivate — explicitly rejected in favour of
  the declarative path (§7).
- The full proactive skill family (health digest, goal-decompose, profiler) —
  sketched in §10 as the roadmap this skill anchors.

## 3. The lifecycle verbs — what each maps to

The headline finding: "infuse + deactivate a role" is really a **lifecycle**, and
three distinct runtime operations sit under it. The skill names them explicitly.

| Verb | What it does | Underlying seam | Reverse |
|---|---|---|---|
| **`install`** (today's "infuse") | Resolve `@scope/name@constraint`, cosign-verify, land in `.acc/packages/` | `acc/pkg/install_infuse.py:execute_infuse_install` → `PROPOSAL_INFUSE` | `deactivate` then `acc-pkg -e` |
| **`activate`** | Promote an installed role into a *running* agent (arbiter signs `ROLE_ASSIGN`; a DORMANT worker loads the `RoleDefinitionConfig` → ACTIVE) | `acc/role_assign.py`, `PROPOSAL_SPAWN`, `worker_reconcile.compute_assignments` | `deactivate` |
| **`deactivate`** | Remove/scale-down the role in `collective.yaml`, then **reconcile-down**: drain ACTIVE agents → DORMANT (back to the worker pool) | edit `CollectiveSpec.agents` + reconcile (§7) | `activate` |
| **`status`** | Read-only: installed? active? roster state? pool headroom? — the safe default + every mutation's pre-flight | `acc/perception.py` roster snapshot, `catalog_query`, `worker_reconcile` | — |

The common "on-demand" path is `install → activate` (acquire + bring online) and
`deactivate` (stand down); the skill sequences the *minimal* correct steps and
is **idempotent** (re-install of a satisfied pkg is a no-op, matching
`execute_infuse_install`; re-activate of an ACTIVE role is a no-op, matching
`compute_assignments`).

### "Flawlessly" = pre-flight + gating discipline

The hard part isn't calling the seams — it's never producing a bad or silent
action:

1. **Resolve & disambiguate** the plain-English ask → a concrete
   `@scope/name@constraint` via `catalog_query` / `marketplace.render_rows`;
   refuse ambiguous matches, surface candidates.
2. **State-aware planning** — `status` first; compute the minimal step set
   (already installed? already ACTIVE? DORMANT worker free, or pool exhausted →
   `unmet`?).
3. **Trust pre-check** — tier + signer + `eval_pass` before staging; never stage
   an unsigned install except behind operator-explicit `allow_unsigned`
   (audit-logged).
4. **Stage, never execute silently** — output is the staged proposal, not a fait
   accompli (except the existing `operator_mode=dev + AUTO` escape).
5. **Dry-run + inverse** — `dry_run` returns the plan without staging; every
   mutation reports its inverse (`uninstall` / `deactivate` / `activate`).
6. **Deactivate safely** — drain (let in-flight tasks finish), warn on dependents
   (`rdeps`), and **refuse to deactivate a CONTROL role** (arbiter, assistant,
   compliance_officer, ingester, observer, orchestrator, reviewer).

## 4. Task-distribution governance — the `task_routing_governor`

This is the "active governance over how tasks are distributed" made concrete, and
it is the **same mechanism as deactivate** applied to different `AgentSpec`
fields.

- **Observe:** roster (`perception.py` `subject_roster_snapshot`), per-role queue
  depth + token-budget utilization (arbiter HEARTBEAT, `Performance` screen
  signals), cluster balance.
- **Diagnose:** overload (a role's queue/utilization sustained high — cf. the
  Cat-B 1.10 ALERT_ESCALATE the Assistant itself hit, `role.yaml:210–219`),
  starvation (a role idle while work queues elsewhere), stuck tasks.
- **Propose (never auto-apply in prod):** adjust `replicas`, `cluster_id`, or
  per-agent `model` on the relevant `AgentSpec` → edit `collective.yaml` →
  reconcile. A `PROPOSE_ROUTE` for one-off redirection already exists; this adds
  *standing* rebalancing of the desired agentset.

So `role_lifecycle.deactivate` and `task_routing_governor` are **one compliant
pattern** — *edit desired agentset → reconcile* — over `replicas=0` vs
`replicas=N`/`model=…`. That symmetry is why they ship together as P0.

## 5. The skill contract

```yaml
# skills/role_lifecycle/skill.yaml
version:          "0.1.0"
adapter_class:    "RoleLifecycleSkill"
risk_level:       "HIGH"            # mutates running agents; oversight-gated
requires_actions:                  # new allowed_actions on the assistant role (§6)
  - propose_infuse                 # install   (exists)
  - propose_activate               # activate  (new)
  - propose_deactivate             # deactivate (new)
domain_id:        "general"
input_schema:
  action:        {enum: [install, activate, deactivate, status]}
  query:         {type: string}     # plain-English OR @scope/name@constraint
  constraint:    {type: string}     # optional semver pin
  cluster_id:    {type: string}     # activate/deactivate targeting
  dry_run:       {type: boolean, default: false}
  allow_unsigned:{type: boolean, default: false}   # operator-only; audit-logged
output_schema:
  state_before:  {type: object}     # installed? active? pool headroom?
  plan:          {type: array}      # ordered steps the skill would stage
  proposals:     {type: array}      # PROPOSE_INFUSE / PROPOSE_SPAWN / collective edit markers
  inverse:       {type: object}     # how to undo
  ok:            {type: boolean}
  error:         {type: string}
```

Base class `Skill.invoke(args) -> dict` (`acc/skills/skill_runtime.py`); manifest
validated by `acc/skills/manifest.py:SkillManifest`. The adapter *composes* the
existing modules — it adds no new install/sign/verify logic.

## 6. Dispatch wiring (`acc/assistant_proposal.py`)

- **New proposal kinds:** `PROPOSAL_ACTIVATE`, `PROPOSAL_DEACTIVATE` alongside the
  existing `PROPOSAL_INFUSE` / `PROPOSAL_SPAWN`. (Activate maps closely to the
  existing `spawn`; deactivate is genuinely new.)
- **`decide_dispatch`** keeps both in `_NEVER_AUTOEXEC` semantics for prod:
  `DISPATCH_QUEUE` by default; `DISPATCH_EXECUTE` only under
  `operator_mode=dev + AUTO`. Deactivate of a **CONTROL role is hard-refused**
  regardless of mode.
- **Execution on approval:** `dispatch_approved_proposal` →
  - `install` → existing `_dispatch_infuse` → `execute_infuse_install`.
  - `activate` → arbiter-signed `ROLE_ASSIGN` (`acc/role_assign.py`) to a DORMANT
    worker, or `unmet` if the pool is exhausted (surface to operator).
  - `deactivate` → the reconcile-down path (§7).
- **New `allowed_actions`** on `roles/assistant/role.yaml`: add `propose_activate`,
  `propose_deactivate` (it already has `propose_infuse`, `propose_spawn`).

## 7. Deactivate mechanism — declarative edit + reconcile-down (the gap we fill)

Today `worker_reconcile.compute_assignments(spec, roster)` is **add-only**: it
fills *unmet* desired slots from DORMANT workers and is idempotent, but it does
**not** stand down agents that are ACTIVE yet no longer in the desired spec. The
only reconcile-down today is the deploy-script `./acc-deploy.sh apply --prune`
(`roles/assistant/role.yaml:160–164`).

Per the operator decision, deactivate is **a declarative edit + reconcile**, not
a new RPC:

1. The skill edits `CollectiveSpec.agents` (drop the role, or `replicas → N-1`)
   and writes `collective.yaml` — a **git-diffable** change, the audit record.
2. A symmetric **`compute_releases(spec, roster)`** (new, mirrors
   `compute_assignments`) diffs ACTIVE agents against the reduced desired set and
   emits **release actions**: mark excess agents `DRAINING` → finish in-flight
   tasks → return to the worker pool as `DORMANT`. (This is the programmatic
   surface behind today's `--prune`.)
3. Drain, don't kill: an agent in `DRAINING` accepts no new TASK_ASSIGN, finishes
   current work, then releases its role binding.

Why this over a drain signal: the desired state lives in a versioned file (fs
observation, logs, `git diff` — the debuggability the operator asked for), and
activate/deactivate/rebalance all become *the same* "edit desired → converge"
loop rather than three bespoke signals.

## 8. Governance & invariants (must not regress)

1. **No silent mutation** — every action stages a proposal into the existing
   Compliance oversight queue (`subject_oversight_decision`,
   `redis_oversight_pending_key`); `stage_install`'s `PROPOSE_INFUSE` stays the
   only install path. Prod never auto-executes (dev+AUTO is the sole escape).
2. **Signed or it doesn't install** — trust pre-check (tier/signer/`eval_pass`)
   before staging; `allow_unsigned` is operator-only and audit-logged.
3. **CONTROL roles are undeactivatable** — the 7 substrate roles are hard-refused.
4. **Drain, don't kill** — in-flight tasks complete before release.
5. **Reversible + observable** — every mutation reports its inverse; the
   `collective.yaml` diff is the audit trail; the effective roster is dumpable.
6. **Envelope respected** — the skill never widens a role's `allowed_*`; an
   out-of-envelope capability ask becomes a signed infuse (proposal 1 §6 bridge).

## 9. Reconcile bridge to proposal 1

- **`collective.md` ↔ `collective.yaml`** is the *roster* desired-state this skill
  reconciles: `install`/`activate`/`deactivate` converge actual → desired.
- **`AGENTS.md`** out-of-envelope desires surface as capability gaps → this
  skill's `install` verb stages the signed infuse.
- The **`task_routing_governor`** keeps the *running* roster matched to load,
  closing the loop the overlay opens.

Same edit-desired-state-then-reconcile pattern, two scopes — which is why
proposal 1 ships first and this reconciles against it.

## 10. Phasing & the proactive skill family

- **P0 (this proposal) — `role_lifecycle` (install/activate/deactivate/status) +
  `task_routing_governor`.** New proposal kinds + `allowed_actions`;
  `compute_releases` reconcile-down; staged-only (dev+AUTO escape); CONTROL-role
  refusal; effective-roster dump.
- **P1 — `capability_gap`** — given a goal/unroutable queue, detect "no active
  role can do this" → `PROPOSAL_ROLE_GAP` (kind exists) → propose
  infuse/extend/author.
- **P2 — sense + concierge family** (anchored by this skill, metered by the
  overlay's `user_profile`):
  - `collective_health` — roster/queue/error/stuck snapshot, feeds the existing
    `proactive_wakeup` (`role.yaml:259–265`).
  - `workload_digest` — periodic proactive summary, profile-metered.
  - `goal_decompose` (extends `plan_outline`) — fuzzy goal → staged plan → the
    exact lifecycle proposals to staff it (one approval, or one signed A-BOM).
  - `personalization` / `onboarding_profiler` — read/apply/seed the proposal-1
    overlays.

The proactive loop these compose: **`collective_health` (sense) →
`task_routing_governor` / `capability_gap` (diagnose) → `goal_decompose` (plan) →
`role_lifecycle` (act)** — all staged into the Compliance queue, depth metered by
`soul.md`'s `user_profile`. Personalization is the dial on every skill's
behaviour, not a sidecar.

## 11. Open questions — RESOLVED (operator review 2026-06-25)

All four resolved at the leaning; these are now P0 build directives.

- **`compute_releases` home → extend `worker_reconcile.py`.** Add
  `compute_releases` alongside the add-only `compute_assignments` — one symmetric
  module, not a new `worker_release.py`.
- **Activate when pool exhausted → report-then-offer.** Report `unmet`
  (no DORMANT headroom) and *offer* a `worker_pool` bump proposal for operator
  approval; never auto-grow the pool.
- **Routing-governor cadence → reuse `proactive_wakeup` (300s).** Ride the
  Assistant's existing proactive loop (`role.yaml:259–265`); no second scheduler.
- **Multi-role asks → one signed A-BOM for ≥2 roles** (`acc/pkg/agent_bom.py`,
  ties to `/new-agent`); a single role stays one `PROPOSE_INFUSE` marker.

## 12. References

- Proposal kinds + dispatch: `acc/assistant_proposal.py`
  (`PROPOSAL_INFUSE`/`SPAWN`, `_NEVER_AUTOEXEC`, `decide_dispatch`,
  `dispatch_approved_proposal`, `_dispatch_infuse`).
- Install: `acc/pkg/install_infuse.py:execute_infuse_install`.
- Activate: `acc/role_assign.py` (`sign_role_assign`/`verify_role_assign`).
- Reconcile: `acc/worker_reconcile.py` (`compute_assignments`, `RosterEntry`,
  `ReconcileResult`) — `compute_releases` is the new symmetric half.
- Desired agentset: `acc/collective.py` (`CollectiveSpec`, `AgentSpec`,
  `load_collective`).
- Roster/perception: `acc/perception.py` (`subject_roster_snapshot`).
- Oversight gate: `acc/tui/screens/compliance.py` (Package Proposals tab);
  `acc/signals.py` (`subject_oversight_decision`, `redis_oversight_pending_key`).
- Skill scaffold: `skills/_base/skill.yaml`, `acc/skills/manifest.py`,
  `acc/skills/skill_runtime.py`; example `skills/catalog_query/adapter.py`.
- Assistant role: `roles/assistant/role.yaml` (`allowed_actions`,
  `allowed_skills`, `proactive_wakeup`).
- A-BOM (multi-role unit): `acc/pkg/agent_bom.py`; `docs/agent-bom-and-new-agent.md`.
- Foundation: `docs/agent-personalization-overlay-DRAFT.md` (proposal 1).
- Prune precedent: `./acc-deploy.sh apply --prune` (reconcile-down today).
