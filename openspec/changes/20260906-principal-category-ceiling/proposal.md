# 20260906-principal-category-ceiling — proposal

## Why

`20260823-attributed-memory` settled its first question by the **floor rule**
(`effective = min(role, requester)`) and found, in settling it, that ACC could
not express one side of it: tiers (`none < viewer < requester < operator`)
govern *may you ask, may you approve*; a role's grants govern *what may be
done*. Nothing said *how far the work someone asked for may go*. Its `[1b]`
recorded the missing piece — a **per-principal category ceiling**,
`effective = role grants ∩ principal ceiling`, defaulting from the tier and
narrowable per admission — as a separate change, and `OC-04` had said the
same earlier: *"a Cat-C-capable role reachable from Slack by anyone is not a
defensible configuration, and ACC currently cannot even express the
constraint."*

Concretely, before this change: a Slack requester admitted at the
`requester` tier who reaches a role whose `max_skill_risk_level` was widened
to `HIGH` can have `HIGH` skills run on their word; an escalation (1.2b) even
offers the operator "allow for this task?" for a call the admission never
contemplated; and the assistant will queue an `INFUSE` proposal (HIGH) for an
API-key caller, where a human approval is the only thing between the key and
a filesystem mutation.

## What

**One scale.** The ceiling is expressed on the scale that already exists —
`LOW < MEDIUM < HIGH < CRITICAL`, the one a role's `max_skill_risk_level` /
`max_mcp_risk_level` and every manifest `risk_level` use — so the floor rule is
a `min()` on one axis rather than a mapping between two. A separate Cat-A/B/C
vocabulary on the principal would have needed exactly that mapping and would
have been enforced nowhere.

**Tier → ceiling** (`acc/identity.py` `TIER_CEILING`):

| tier | ceiling | why |
|---|---|---|
| `none` | LOW | cannot ask at all; the value only matters for a forged payload |
| `viewer` | LOW | reads state |
| `requester` | **MEDIUM** | may ask for work; the same default a role has. HIGH/CRITICAL work on an external word is the configuration `OC-04` called indefensible |
| `operator` | CRITICAL | the substrate vouched for them; unchanged behaviour |

**Narrowable, never widenable.** `Grant.ceiling` (`access.yaml`),
`acc-cli access admit --ceiling LOW|MEDIUM|…`, `identity.admit(ceiling=)`: a
value above the tier's is refused; a hand-edited file value above the tier's
is narrowed to the tier's with a warning. The public mirror's `flg77/acc`
users cannot promote themselves by editing a YAML file any more than they
could reach `operator` through it.

**It travels on the task.** `channel_access.Admission.task_attribution()` and
`compat_endpoint.Caller.attribution()` stamp `requester_ceiling` (the compat
caller also now stamps `requester_tier`). `identity.ceiling_of(payload)` reads
it back: explicit value first, tier fallback for a publisher older than this
change, and **no ceiling at all** for an unattributed task — the operator's
own prompt from the TUI / Web GUI, the arbiter's reconciliation, a plan step —
so nothing about the operator's own work changes.

**Refused, not asked.** In `capability_dispatch._dispatch_one` the ceiling is
checked **first**, before the 1.2b escalation and before the mode / category
gate: an invocation whose manifest `risk_level` is above it returns
`ok=False` with `refused: skill 'x' is HIGH -- above the requester's ceiling
MEDIUM`, submits no oversight row and never widens the role. The ceiling is a
floor *under* human judgement, not a question for it — a human approving the
call would be approving what the admission forbade. Same for an assistant
proposal (`cognitive_core`): one above the ceiling is dropped with a line in
the reasoning trace instead of being queued (`UNACCEPTABLE` is above every
ceiling).

**The gate categories are not the ceiling.** `system_access` /
`acts_on_behalf` (1.2) remain questions for the human regardless of risk
level; the ceiling does not replace them and they do not replace it.

## What this does not do

* **Memory retrieval.** `[2]` of the memory change — a fragment is not
  retrieved into a context below the ceiling that produced it — stays with
  that change. The ceiling is now *on* every attributed episode (inside
  `payload_json`, as `requester_ceiling`); enforcing it at retrieval and under
  publication approvals is the memory change's Phase 4+ work, now
  expressible.
* **Plan steps.** A PLAN submitted by an external requester is dispatched by
  the arbiter as step tasks that carry no requester attribution today; those
  steps run under the role's grants alone. Attribution propagation through
  the executor is a separate, small change and is noted in the tasks.
* **A widening path.** Deliberately none. A deployment that wants a
  requester to trigger HIGH work admits nobody to it; it gives the operator
  the prompt. (`operator_tier`-vouched principals — TUI, Web GUI operator,
  Kubernetes — are unchanged.)

## Behaviour change to announce

An **OpenAI-compatible endpoint** caller (any `ACC_COMPAT_API_KEYS` key) is a
requester at **MEDIUM**: a role reached through the endpoint can no longer
run a HIGH skill or queue an INFUSE proposal on the key's word. Before this
change it could, subject only to the role's own ceiling and, for CRITICAL, a
human approval. That was the gap.
