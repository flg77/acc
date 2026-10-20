# Flagship demo — "Regulated loan origination, the ACC way" — design & separation

- **Status:** internal design draft — NOT for distribution (`.gitignore`d)
- **Date:** 2026-08-10
- **Companion to:** `docs/loan-origination-integration-analysis.md` (the capability/gap analysis)
- **Purpose:** turn the §6 flagship recommendation into a buildable plan — where it lives
  (separation), how the demo is structured, and the role profiles we must design (they do
  **not** exist yet).

---

## 1. Separation — three repos, three responsibilities

The operator's instinct is right: **most of this must NOT land in `acc-spearhead`.** The
platform repo gets only reusable primitives; the demo is domain content and ships as an
**ecosystem pack**; the reference app stays a read-only mirror.

| Layer | Repo | What lands here | Why |
|---|---|---|---|
| **Platform primitives** | `acc-spearhead` (+ `operator/`) | operator pod-env injection of the MLflow URI; RHOAI-3.4 MLflow auth resolver; trace↔audit correlation tags; `genai.evaluate` scorer *framework*; OpenShell negative-test hook; **corpus-scoped `KNOWLEDGE_SHARE` consumer + `search_episodes(collective_id=)`** (the assistant-learning primitive, §4.2) | These are generic — *every* collective benefits. They are capability, not content. |
| **The demo as a pack** | `acc-ecosystem-spearhead` (`roles/`, `mcps/`, `collectives/`, `skills/`, `evals/`) | the 4 domain roles; the `loan-origination` collective preset; the predictive-model MCP manifest + server; the demo walkthrough + TUI screenshots; the scorer *dataset*; the **catalog trigger metadata (`whenToUse`)** so the assistant can select the pack (§4.1); the E2E infusion test | Domain/demo content must version independently of the platform and is the **contribution-back artifact** (a Red Hat AI quickstart-shaped pattern). Keeps core clean. |
| **Reference (read-only)** | `integrate/multi-agent-loan-origination` | nothing — never modified | The RH app we mirror patterns from. Comparison only. |

**Rule of thumb:** if removing the loan-origination scenario would delete the code, it belongs
in the pack (ecosystem), not the platform. If the code helps an unrelated collective, it's a
platform primitive.

> Practical note: `acc-ecosystem-spearhead` already carries `roles/`, `mcps/`, `skills/`,
> `evals/` and a `sync-sources.sh`/`build-all.sh` pack pipeline — the demo drops in as a new
> family pack, published via the existing ecosystem publish flow (never a git-mirror dev push).

---

## 2. The role profiles we must design (they do not exist)

ACC's shipped roles are **functional** (`arbiter`, `assistant`, `ingester`, `observer`,
`orchestrator`, `reviewer`, `compliance_officer`) — generic agent *functions*. The loan app's
roles are **domain personas** (`borrower`, `loan_officer`, `underwriter`, `ceo`). None of the
domain personas exist in ACC. Mapping them onto ACC's model is the "make it crisp" work.

### 2.1 Six crispness principles (the design contract)

1. **Disjoint `task_types`.** No two roles share a task type → arbiter `TASK_ASSIGN` routing is
   deterministic, never ambiguous.
2. **Fail-closed capability surface.** Every role's `allowed_skills` / `allowed_mcps` is the
   *minimal* set; empty = deny-all. `max_skill_risk_level` / `max_mcp_risk_level` cap the ceiling.
3. **Data-scope = retrieval boundary + RBAC, not a bespoke class.** The reference app's
   `DataScope` (borrower=own-data, LO=assigned, CEO=pii-masked) maps onto ACC's **governed-RAG
   id-allowlist + Cat-B boundary**. The demographic/HMDA-analog store is a boundary that **only
   the compliance role holds** — that is how we express HMDA schema-isolation crisply, without a
   second database.
4. **One CRITICAL gate per regulated decision.** `underwriter` decision-render and `compliance`
   policy-update carry `risk_level: CRITICAL` → `needs_oversight` → `HumanOversightQueue`.
5. **Tier placement is explicit.** 2 edge roles (small local model, interactive budget) + 2 DC
   roles (large model; compliance deliberately throttled).
6. **Reuse over invent.** The reference "CEO/analytics" persona is **not a new role** — reuse the
   existing `observer` role scoped read-only to the collective. Design 4 roles, not 5.

### 2.2 The four roles (design intent — not final YAML)

Schema per `roles/TEMPLATE/role.yaml`: `purpose · persona · task_types · seed_context ·
allowed_actions · allowed_skills/mcps + max_*_risk_level · category_b_overrides`.

**① `loan-intake` — EDGE (borrower-facing)**
- **purpose:** "Guide an applicant through mortgage intake at the edge — capture the application,
  answer product/eligibility questions from borrower-facing knowledge, and hand a complete
  package upstream. Never render a decision."
- **persona:** `concise` · **tier:** edge / small local model
- **task_types:** `INTAKE_START · APPLICATION_CAPTURE · DOC_REQUEST · STATUS_QUERY · PRODUCT_EXPLAIN`
- **actions:** `read_vector_db` (borrower KB tier only) · `write_working_memory` · `write_scratchpad` · `publish_task` (delegate upstream)
- **skills/mcps:** doc-checklist skill (LOW); **no external MCP**
- **scope:** governed RAG = public/product KB tier + own-application memory. **No** underwriting KB, **no** demographic, **no** predictive model, **no** decision.
- **Cat-B:** generous `rate_limit_rpm` (interactive), modest `token_budget`.

**② `loan-officer` — EDGE (pipeline)**
- **purpose:** "Manage the branch loan pipeline — review captured applications, draft borrower
  communications, search the compliance KB, and route complete files to underwriting. Advisory
  only; no approval authority."
- **persona:** `formal` · **tier:** edge
- **task_types:** `PIPELINE_REVIEW · COMMS_DRAFT · KB_SEARCH · FILE_ROUTE · CONDITION_TRACK`
- **actions:** `read_vector_db` (assigned-files scope) · `read/write_working_memory` · `search_episodes` · `publish_task` (delegate → underwriter)
- **skills/mcps:** comms-draft, kb-search skills (MEDIUM); kb-search MCP (MEDIUM)
- **scope:** governed-RAG id-allowlist = files routed to this officer + full compliance KB read. **No** demographic, **no** decision, **no** Cat-C mutation.

**③ `underwriter` — DC (the delegated heavy role)**
- **purpose:** "Perform risk assessment and render an underwriting recommendation in the data
  center — run DTI/LTV and risk tools inside the OpenShell cage, optionally consult the
  predictive-model MCP, and produce an auditable decision with citations."
- **persona:** `analytical` · **tier:** DC / large model (KServe or MaaS)
- **task_types:** `RISK_ASSESS · DECISION_RENDER · CONDITION_SET · PREDICTIVE_CONSULT`
- **actions:** `read_vector_db` (full underwriting scope) · `read/write_working_memory` · `publish_eval_outcome` · `publish_task` (return result → edge)
- **skills/mcps:** `dti-ltv-calc` + `risk-eval` skills **executed inside OpenShell** (mode 2/3, MEDIUM); `predictive-model` + `risk-assessment` MCP (HIGH — gated)
- **CRITICAL:** `DECISION_RENDER` on a high-value loan → `needs_oversight` → oversight queue.
- **scope:** full underwriting KB; **only aggregated** fair-lending signal (never raw demographic — that stays with ④).

**④ `compliance-reviewer` — DC (oversight + governance push-down)**
- **purpose:** "Enforce fair-lending and regulatory compliance in the data center — review
  underwriting decisions against Cat-A/B policy, hold demographic (HMDA-analog) data in an
  isolated scope, decide CRITICAL actions in the oversight queue, and publish Cat-C threshold
  updates that sync down to the edge."
- **persona:** `formal` · **tier:** DC · **throttled** `rate_limit_rpm: 15` (mirrors existing `compliance_officer`)
- **task_types:** `COMPLIANCE_REVIEW · FAIRNESS_CHECK · OVERSIGHT_DECIDE · POLICY_UPDATE_PROPOSE`
- **actions:** `read_vector_db` (compliance KB **+ the isolated demographic boundary — this role only**) · oversight approve/reject · `publish_knowledge_share` · Cat-C update publish (arbiter-countersigned ROLE_UPDATE / rule sync)
- **skills/mcps:** fairness-audit, evidence-gen skills (MEDIUM); **no external MCP** (keeps demographic data in-boundary)
- **CRITICAL:** `POLICY_UPDATE_PROPOSE` → oversight before the Cat-C sync fans to edge.
- **This is the role that exercises the DC→edge governance push and the human-oversight queue.**

*(Analytics/CEO view = existing `observer` role, read-only, collective-scoped, PII-masked via
guardrails — no new role.)*

---

## 3. Demo structure — how it runs

Topology (from analysis §6): 2 edge roles + 2 DC roles across a NATS-leaf bridge.

```
EDGE cell (MicroShift / branch)                DC hub (RHOAI cluster)
  loan-intake ──┐                               ┌── underwriter (OpenShell cage + predictive MCP)
  loan-officer ─┤ acc.bridge.*.delegate ───────▶│
               │◀───── acc.bridge.*.result ─────┤   compliance-reviewer (oversight + Cat-C push)
               │◀── ROLE_UPDATE / Cat-C sync ───┘
        OpenShell (edge tools)          HMAC audit chain → MLflow experiment (task_id·cid·spiffe.id tags)
```

**Seven beats — each is a named proof-point** (beats 1–5 map 1:1 to a scorer assertion in §5;
beats 0 and 6 are the assistant bookends, see §4). The assistant frames the whole run: it
*acquires* the capability (beat 0), the ensemble runs (1–5), then the assistant *learns* from
the result (beat 6) — **acquire → run → learn**.

| # | Beat | Exercises | Pass condition |
|---|---|---|---|
| 0 | **Assistant infuses** — operator asks the assistant a mortgage question; it detects the capability gap (`catalog_query`), emits `[PROPOSE_INFUSE:@acc/loan-origination…]` → Compliance approve → cosign install | assistant infusion trigger (§4.1) | pack installs from a `PROPOSE_INFUSE` marker, operator-gated |
| 1 | **Intake at edge** — `loan-intake` captures app, governed-scope RAG answers product Qs on a local model | edge autonomy · RBAC scope | intake completes with **zero** underwriting-KB reads (scope held) |
| 2 | **Delegate to DC** — `loan-officer` routes the file `acc.bridge.<edge>.<hub>.delegate`; `underwriter` picks it up | edge→DC bridge · MCP | delegate round-trips; `underwriter` calls the **predictive-model MCP** |
| 3 | **Governed execution** — DTI/LTV runs **inside OpenShell**; a deliberately out-of-policy FS/net access is **Cat-A denied** | OpenShell · Cat-A | tool result returns **and** the denial is a `SandboxBlocked` audit event + error span |
| 4 | **Human oversight** — `DECISION_RENDER` (CRITICAL) parks in the oversight queue; approve → decision returns to edge on `.result` | oversight · DC→edge | decision blocked until approved; result reaches edge |
| 5 | **Governance push-down** — `compliance-reviewer` proposes a Cat-C threshold change; after oversight it **hot-reloads at the edge** (nested-SPIFFE verified) | DC→edge governance · SPIFFE | edge `loan-intake`/`loan-officer` reflect the new threshold without redeploy |
| 6 | **Assistant learns** — `compliance-reviewer` `publish_knowledge_share`s the decision rationale / fair-lending finding; the assistant's new consumer ingests it into `memory_notes` | assistant learning loop (§4.2) | a later assistant query reflects the ensemble's conclusion without re-running it |

**One observability pane:** every beat emits an OTel→MLflow span tagged `acc.task_id`,
`acc.cid`, `spiffe.id`, joinable to the HMAC audit chain; a `genai.evaluate` run grades the
decisions after the fact.

---

## 4. Assistant integration — acquire, then learn

The ACC `assistant` (`roles/assistant/role.yaml` v2.6.0) is the operator's proactive
agent-of-agents. Two hooks matter here — and they sit at very different maturity.

### 4.1 Knowing WHEN to infuse the pack — **mostly EXISTS**
- The assistant already runs a proactive wakeup scan every ~5 min
  (`acc/agent.py:_proactive_wakeup_loop` / `_WAKEUP_PROMPT`) explicitly told to spot "a
  capability the collective lacks for a pending need," grounded on a live `catalog_query`
  (installed roles / available packages).
- It emits `[PROPOSE_INFUSE:@scope/name@constraint:reason]` → **always** routed through the
  Compliance oversight queue (`_NEVER_AUTOEXEC`) → operator approves → real cosign-verified
  catalog fetch+install (`acc/assistant_proposal.py`, `acc/pkg/install_infuse.py`,
  `acc/pkg/fetch.py`). Validated today as the manual E2E "Test B" in `docs/TESTING-INFUSION-E2E.md`.
- **What the pack must supply for the trigger to fire:** the catalog entry needs crisp,
  matchable **trigger metadata** — a one-line `whenToUse` (mortgage / lending / underwriting /
  compliance domain + the task_types the roles serve) — so the assistant's LLM decision reliably
  maps "evaluate this mortgage application" → `@acc/loan-origination`. The catalog description
  *is* the trigger surface. **Pack-side, small.**
- **Not required for v1 (roadmap):** the governed `role_lifecycle` / `task_routing_governor`
  skills + `ACTIVATE`/`DEACTIVATE` proposal kinds (load-aware roster edits,
  `docs/role-lifecycle-skill-DRAFT.md`, brief 13) would make this first-class; the
  propose→approve→install path is sufficient without them.

### 4.2 Learning FROM the ensemble's output — **largely ABSENT, must build (platform)**
This is the real gap and a genuine "still need to implement":
- Episodes are **`agent_id`-scoped** and retrieval self-filters to the same agent
  (`acc/cognitive_core.py:2183`) — no cross-agent, no cross-collective learning by default.
- `KNOWLEDGE_SHARE` has a fully-built **emit** side (subjects `acc.{cid}.knowledge.{tag}`, Redis
  index, `publish_knowledge_share` perm — and our `compliance-reviewer` already holds it) but
  **no consumer anywhere subscribes to it**. The learn side is unbuilt even within one collective.
- Every learning subject bakes `{cid}` → cross-collective learning is structurally blocked; the
  only cross-cid transport is the **bridge** (delegate/result), which is request/response, not learning.
- `search_episodes(collective_id=)` is referenced by the webgui but **not implemented** on the
  LanceDB backend (returns 503); the eval→policy loop is log+EWMA only (bandit update SIP-P2 unbuilt).

**Minimal build to make "the assistant learns from this ensemble" real** — hub-owned,
`acc-spearhead` core; reusable across any collective→assistant, so **platform, not pack**:
1. A **corpus-scoped (ENDOCRINE) knowledge subject** — the cross-cell mode is already defined in
   `acc/signals.py` but has no subject/helper; add one (or let the assistant subscribe to the loan
   collective's `acc.{loan-cid}.knowledge.*` with widened NATS perms).
2. A **`KNOWLEDGE_SHARE` consumer** in the assistant that ingests shares into its own
   `memory_notes` store via the existing reflection machinery (`acc/memory_reflection.py`) —
   turning the loan `compliance-reviewer`'s emitted findings / decision-rationales into durable
   assistant notes.
3. *(Optional, deeper)* implement `search_episodes(collective_id=)` + add a `collective_id`
   column to `episodes`/`memory_notes` so the assistant can RAG over the loan collective's
   episodes directly; and close the eval→policy bandit loop if the assistant should improve from
   the ensemble's reviewer verdicts.

**Demo scope = (1)+(2) only** — a knowledge-share consumer is enough to show "the assistant
learned what the loan ensemble concluded" (beat 6). (3) is a follow-on. This build is a separate
hub-owned work item from the acc1 MLflow brief — candidate for its own brief.

---

## 5. Build order (platform first, then pack)

**Phase P0 — platform primitives:** (a) finish the MLflow two-way loop (operator pod-env
injection [acc1] + RHOAI-3.4 auth + trace↔audit tags [hub]) — acc1 brief
`coordination/briefs/18-mlflow-two-way-operator.md`; without it beats 1/2/4/5 can't be observed
end-to-end. (b) build the **assistant-learning consumer** (§4.2 items 1–2: corpus knowledge
subject + `KNOWLEDGE_SHARE` consumer → `memory_notes`) — hub-owned, its own brief; gates **beat 6**
only. Both are reusable platform capability, not demo content.

**Phase P1 — the pack skeleton:** author the 4 roles (§2) + the `loan-origination` collective
preset in `acc-ecosystem-spearhead`; stand up the predictive-model MCP manifest; add the
**catalog trigger metadata (`whenToUse`)** so the assistant can select the pack (§4.1) + a
pack-specific **E2E infusion test** (Test-B style). Validate with `acc-collectives` (role/model/pack
alignment). Beat 0 needs only this metadata — the infusion pipeline itself already exists.

**Phase P2 — the beats:** wire OpenShell DTI/LTV (beat 3, needs the OpenShell Model-2 spike
landed), the oversight gate (beat 4), the Cat-C push (beat 5). Add the `genai.evaluate` scorer
pack + dataset.

**Phase P3 — the artifact:** `docs/howto-demo-*` walkthrough + `acc-tui-docs` screenshots +
the MLflow experiment as the shareable proof-point. Optionally submit as an RH AI quickstart-shaped pattern.

---

## 6. Open decisions for the operator

1. **Pack home** — confirm `acc-ecosystem-spearhead` (recommended) as the demo pack's repo vs a
   fresh `integrate/` project. *(Recommendation: ecosystem pack — it's the contribution-back vehicle.)*
2. **Role count** — ratify 4 roles + reuse `observer` for analytics (vs porting all 5 personas).
3. **Demographic isolation depth** — is a Cat-B retrieval boundary + single-role RBAC sufficient
   as the HMDA-analog, or do we want a genuinely separate store to match the reference's
   schema-level isolation? *(Recommendation: boundary+RBAC first; separate store only if a
   customer proof demands schema-level separation.)*
4. **OpenShell readiness** — beat 3 depends on the Model-2 delegated-exec spike (proposal 051)
   landing on a lab node; sequence the demo after that or stub beat 3 initially.
5. **Assistant-learning scope** — ratify the minimal (1)+(2) knowledge-share consumer for beat 6
   (recommended), vs the deeper (3) cross-cid episodic RAG + eval→policy bandit loop. And decide
   whether the learning consumer is its **own hub brief** (recommended) alongside the acc1 MLflow
   brief. *(Recommendation: minimal consumer, separate brief; it's reusable platform work.)*
6. **Do the assistant bookends (beat 0/6) gate the flagship, or ship as a follow-on?** The core
   edge↔DC + OpenShell + MLflow story (beats 1–5) stands alone; beats 0/6 make it "the assistant
   acquires and learns." *(Recommendation: build 1–5 first, land 0/6 as the second increment so
   the flagship isn't blocked on the new learning primitive.)*
