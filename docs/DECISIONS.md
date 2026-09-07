# ACC Planning Decision Log

Running record of design decisions for the post-PR-D operator-triage
feature work. ADR-lite format — each decision captures the question,
the options, the chosen option, the reasoning, and (where applicable)
the implementation plan.

Companion to `docs/WORKFLOW_infusion_to_prompt.md` (the operator
workflow paper) and `docs/IMPLEMENTATION_SPEC_v0.2.0.md` (the formal
spec). New decisions are appended in chronological order; superseded
decisions are marked but not deleted.

## Status legend

* **PROPOSED** — captured here, not yet started.
* **IN PROGRESS** — actively being implemented.
* **LANDED** — shipped to `main`, deployable.
* **SUPERSEDED** — replaced by a later decision; reference noted.

---

## D-001 — Spawn coding_agent via worker pool, not apply-watcher

**Status:** LANDED — PR-J (agent side; commit `d2c6842`; 19 tests)
+ **PR-M / J-2** (arbiter reconcile; commit on `main` 2026-05-22;
16 new tests).  PR-J shipped the agent-side primitive (dormant
boot mode, signed ROLE_ASSIGN verifier, ``_promote_from_dormant``,
universal ``_subscribe_role_assign``).  PR-M closes the loop:
``acc.worker_reconcile.compute_assignments`` (pure greedy
idempotent matcher) + ``build_role_assign_payloads`` (signs via
PR-J's ``sign_role_assign``).  Arbiter glue in ``acc.agent``:
HEARTBEAT-fed ``_worker_roster``, a ``subject_collective_reconcile``
trigger subscription, and ``_run_worker_reconcile`` that loads
``collective.yaml``, diffs, and publishes.  New config field
``security.arbiter_signing_key`` (env ``ACC_ARBITER_SIGNING_KEY``)
holds the arbiter private key; empty → loop warns + emits nothing
(no unsigned payloads).

**Q follow-up — LANDED (PR-Q, commit on `main` 2026-05-22; 8 new
tests).**  The dormant pool is now declared in the agentset itself,
matching the operator's mental model (agentset → Role → subrole).
``CollectiveSpec.worker_pool: int`` declares how many dormant
workers to pre-spawn; ``recommended_pool_size(spec)`` = sum of
replicas (size the pool to the desired subrole slots).
``roles_to_compose`` gains a worker-pool mode: when
``worker_pool > 0`` it synthesizes ``acc-worker-<n>`` dormant
services (``ACC_AGENT_ROLE=dormant``) instead of concrete
``acc-cell-*`` containers — the arbiter reconcile fills the
desired ``agents`` (commonly coding_agent subroles) onto the pool
at runtime.  Shipped ``collective.worker-pool.yaml`` exemplar
(2 implementer + 1 reviewer + 1 tester → ``worker_pool: 4``).
Operator runbook (keypair provisioning, the down→up→apply→reconcile
order, the network-name gotcha, troubleshooting table) lives in
``docs/worker_pool_setup.md``.
**Date:** 2026-05-21
**Context:** PR-D (commit `83883fd`) wired "Nucleus Apply" to write
the requested agent into `./collective.yaml` and touch
`./.acc-apply.request`. The intent was a host-side watcher
(systemd path-unit or `inotifywait`) consumes the marker and runs
`./acc-deploy.sh apply <spec>`. The standalone installer never grew
that watcher; in practice operators see `Awaiting reconcile…` forever
and the arbiter ends up answering everything because no `coding_agent`
container ever starts.

**Options considered:**

1. **Install an apply-watcher.** Ship `scripts/acc-apply-watcher.sh`
   plus a `./acc-deploy.sh setup --install-watcher` flag (drops a
   systemd unit, or a tmux/`nohup` background process on hosts
   without systemd). ~50 LoC of shell + a few `acc-deploy.sh`
   branches.
   * Pros: Tiny. Matches what the original plan assumed.
   * Cons: Adds host-side state (the watcher process). Requires
     filesystem permissions to install a systemd unit. Foot-gun
     when the watcher dies silently — operator never finds out.
     Doesn't help K8s mode (the operator there is the K8s operator,
     not a host script).

2. **PR-G — worker pool with runtime role-assign.** Pre-spawn N
   dormant agent containers at `up` time. Each dormant agent boots
   without a CognitiveCore and parks waiting for a signed
   `SIG_ROLE_ASSIGN` from the arbiter. When the operator hits
   Apply, the arbiter assigns the requested role to the
   lowest-numbered dormant worker; the worker promotes itself
   (loads the role definition, builds CognitiveCore, registers).
   * Pros: No host privilege, no podman churn, no per-Apply
     container restart, sub-second infusion. Symmetric across
     standalone and K8s. Already sketched in the PR-G section of
     the original plan.
   * Cons: More work (~3-5 days). Requires SIG_ROLE_ASSIGN signal
     + signed-message validation + dormant-agent boot mode.

**Decision:** Option 2 (PR-G worker pool). Done right once, then
forever.

**Implementation outline:**

* `acc/agent.py` — allow `ACC_AGENT_ROLE in {"", "dormant"}` →
  boot a slim event loop that only subscribes to
  `subject_role_assign(cid)` and HEARTBEATs an `IDLE` state.
* `acc/signals.py` — `SIG_ROLE_ASSIGN`, `subject_role_assign(cid)`,
  Ed25519-signed envelope (reuse `acc.role_store.apply_update`
  validation).
* `acc/scheduler/` — arbiter-side reconcile loop reads
  `collective.yaml`, diffs against active roles, picks dormant
  workers from a free-list, emits SIG_ROLE_ASSIGN.
* `acc/tui/screens/infuse.py` — `_apply_started_ts` heartbeat
  watcher still fires when the assigned worker comes online (now
  matches a dormant-worker promotion, not a new container).
* `container/production/podman-compose.yml` — bump default replica
  count to `4 × dormant` per agent base image.
* Tests: `test_worker_pool_assign.py`, `test_dormant_boot.py`,
  `test_arbiter_reconcile_role_assign.py`.

---

## D-002 — RAG (memory retrieval) is default-on per role

**Status:** LANDED (PR-I, commit on `main` 2026-05-22; 17 new tests)
**Date:** 2026-05-21
**Context:** Agents have `read_vector_db`, `read_scratchpad`,
`write_working_memory` in their `allowed_actions` but the LLM never
invokes them because nothing in the system prompt tells it they
exist. When the operator asks "do you remember the first task,"
the agent honestly says no — meanwhile Soma reports
`ICL episodes = 3` (the past tasks ARE in LanceDB, just unused).

**Options considered:**

1. **Default-on (everyone gets RAG).** Before each LLM call,
   query LanceDB for top-K episodes by embedding cosine similarity,
   render them into the system prompt as
   `RECENT_RELEVANT_EPISODES: …`.
   * Pros: Operator-visible improvement immediately. Closes the
     "do you remember?" gap. Works for every role with no
     per-role configuration.
   * Cons: +150-300ms latency per call (one extra LanceDB read +
     embedding). Increases system-prompt token usage by ~500-2000
     tokens depending on K and episode length.

2. **Opt-in per role.** `role.yaml` carries
   `memory_retrieval: false` by default; roles that want it
   set `true`.
   * Pros: No latency cost for ephemeral roles.
   * Cons: Footgun — operators don't know to set the flag,
     stays off forever, "do you remember?" still fails by default.

3. **Skill-based (operator-invoked).** The role's
   `allowed_actions` list `retrieve_episodes`; the agent calls it
   via `[SKILL:retrieve_episodes]` markers when it judges
   relevance.
   * Pros: Only fires when needed.
   * Cons: The LLM rarely judges correctly without explicit
     prompting; effectively the same as default-off.

**Decision:** Option 1 (default-on). The role can opt OUT via
`role.yaml: memory_retrieval: false` for ephemeral roles where the
latency matters more than the recall.

**Implementation outline:**

* `acc/cognitive_core.py:_run_pipeline` — between step 1 (Cat-A
  pre-LLM) and step 2 (compose system_prompt), call
  `_retrieve_episodes(task_payload, role, k=5)`.
* `_retrieve_episodes` embeds the task content, queries LanceDB's
  `episodes` table for top-K nearest, filters by role + freshness,
  returns a `list[dict]` of `(ts, task_type, output_snippet, …)`.
* `_build_system_prompt(role, retrieved_episodes)` — appends
  `RECENT_RELEVANT_EPISODES:` section with one line per episode.
* `RoleDefinitionConfig` grows `memory_retrieval: bool = True`
  (Pydantic field with sane default).
* Tests: `test_rag_default_on.py`, `test_rag_opt_out.py`,
  `test_rag_latency_budget.py` (assert < 500ms median).

---

## D-003 — Operating modes: PLAN, ACCEPT_EDITS, ASK_PERMISSIONS, AUTO

**Status:** LANDED (PR-L, commit on `main` 2026-05-22; 43 new
tests).  ``acc.operating_modes`` shipped (mode constants,
normaliser, write-action classifier, ``should_gate_invocation``);
``capability_dispatch.dispatch_invocations`` is now mode-aware;
``RoleDefinitionConfig.default_operating_mode`` defaults to
``AUTO``; Prompt-screen Select dropdown wires the per-session
choice through ``TUIPromptChannel.send`` → ``task_payload`` →
``_handle_task`` → ``dispatch_invocations``.  Constitutional
Cat-A invariant pinned via
``test_cat_a_block_propagates_through_all_modes``.

**L-2 follow-up — LANDED (PR-P, commit on `main` 2026-05-22; 3 new
tests).**  The Prompt screen's Mode dropdown now auto-prefills from
the selected target role's ``default_operating_mode`` via an
``on_select_changed`` handler (the operator can still override
per-task).  The handler ignores the Mode select's own Changed
events (no feedback loop) and tolerates a missing / unloadable
role (leaves the selector untouched).  The role's default flows
from ``role.yaml`` → ``RoleDefinitionConfig.default_operating_mode``
→ the dropdown, so a role infused via Nucleus carries its preferred
mode into the Prompt screen.
**Date:** 2026-05-21
**Context:** Today every operator prompt runs the agent in
"unrestricted within constitutional rules" mode — Cat-A blocks
hard violations, Cat-B drifts the compliance score, everything
else proceeds. The operator wants finer-grained control over how
autonomous the agent is per session, mirroring the permission
modes that other agentic systems (e.g. Claude Code) expose.

**Options considered:** N/A — the user named the four modes
directly. Question is purely how to implement them.

**Decision:** Four modes:

| Mode | Semantic |
|------|----------|
| `PLAN` | Agent emits a PLAN signal (DAG of intended sub-tasks) but does NOT execute anything. Operator reviews the plan in the Comms ACTIVE PLAN pane and either approves or rejects via the Compliance pane. |
| `ACCEPT_EDITS` | Agent executes read-only and pure-compute actions automatically; any write/edit/delegate invocation pops an oversight item for approval. |
| `ASK_PERMISSIONS` | Every capability invocation (`[SKILL:…]` / `[MCP:…]`) pops an oversight item. Maximum operator control; slowest. |
| `AUTO` | Today's behaviour — agent acts within constitutional rules (Cat-A blocks always, Cat-B observes), no per-action approval. |

**All four respect Cat-A constitutional rules unconditionally.**
The modes adjust what fires the oversight queue, NOT what fires
the Cat-A guardrails.

**Implementation outline:**

* `acc/config.py` — new `OperatingMode` enum + per-task field
  `task_payload["operating_mode"]: str`. Defaults to `AUTO` for
  backward compatibility.
* `acc/cognitive_core.py` — wrap step 7 (capability dispatch)
  with a mode-aware gate. In `PLAN` mode, dispatch is replaced by
  PLAN-signal emission. In `ACCEPT_EDITS` mode, a per-invocation
  `risk_classify` decides queue-or-execute. In `ASK_PERMISSIONS`
  mode, every invocation queues. In `AUTO`, today's path.
* `acc/tui/screens/prompt.py` — new `OperatingMode` Select dropdown
  next to the target-role field. Defaults to `AUTO` for
  backward-compat; per-session override.
* `acc/tui/screens/infuse.py` — same Select on the Nucleus form so
  the role can be infused with a default mode.
* `acc/tui/screens/compliance.py` — pending-item card shows
  `mode=…` context so the operator knows whether they're approving
  a `PLAN`-gated review or an `ASK_PERMISSIONS`-gated invocation.
* Tests: `test_operating_mode_plan.py`,
  `test_operating_mode_accept_edits.py`,
  `test_operating_mode_ask_permissions.py`,
  `test_operating_mode_constitutional_invariant.py` (Cat-A blocks
  in EVERY mode).

---

## D-004 — Compliance pane redesign comes FIRST (before D-001/D-002/D-003)

**Status:** LANDED (PR-H, commit on `main` 2026-05-22; 15 new tests + 1 fixture refresh)
**Date:** 2026-05-21
**Context:** The operator-reported "not clear what the user is
approving" — today the pending-item card shows `ID · Agent · Risk ·
Submitted · Status` but no payload preview, no reason-for-queueing,
no preview of what will happen if approved. The operator is forced
to either Approve-all or Reject-all blind. This blocks the eval
loop for D-001 / D-002 / D-003 because each of those decisions
expects the Compliance pane to be the operator's interrogation
surface.

**Options considered:**

1. **Inline expansion of the existing table.** Add columns for
   `task_id`, `payload_preview`, `gate_reason`.
2. **Per-row detail panel (master/detail).** The table on top, a
   detail Static below that renders the selected row's full
   context.
3. **Modal on row-select** — like the InvocationDetailModal in
   PR-F.

**Decision:** Option 2 (master/detail). Keeps the
table's at-a-glance density while making approval-context
unmistakable when needed.

**Implementation outline:**

* `acc/tui/screens/compliance.py` — split the right pane: top half
  keeps the existing OWASP grading table; bottom half becomes a
  `DataTable#oversight-pending-table` + `Static#oversight-detail`.
* The detail panel renders, for the highlighted row:
  * `Agent: <id>  Task: <task_id>  Risk: <level>`
  * `Gate reason: <e.g. CRITICAL invocation: A-017 outside allow-list>`
  * `Payload preview` — first 400 chars of the relevant signal
    payload (TASK_ASSIGN or capability-invocation argv).
  * `Approve previews:` and `Reject previews:` — a one-line
    summary of what each action will publish on NATS.
* The existing `a`/`r` keybindings (Approve/Reject) act on the
  highlighted row; require a confirmation modal when the gate
  reason is in a set of `HIGH_CONSEQUENCE` reasons.
* Tests: `test_compliance_detail_renders.py`,
  `test_compliance_approve_publishes_decision.py`,
  `test_compliance_high_consequence_requires_confirm.py`.

---

## D-005 — Golden-prompt suite in three runner modes (CLI / TUI / scheduled)

**Status:** LANDED — Phase 1 (schema + CLI + 6 seed prompts; PR-K
`9c79463`; 28 tests) + Phase 2 / PR-N (TUI Diagnostics pane #9;
`0172bf3`; 6 pilot tests) + **Phase 3 / PR-O** (scheduled runner;
commit on `main` 2026-05-22; 5 new tests).  PR-O adds
``persist_results`` (JSONL history), the ``acc-cli e2e run
--history PATH --loop SECONDS`` flags, and
``docs/golden_prompts_scheduling.md`` (systemd-timer + k8s CronJob
+ CI-gate recipes).  A dedicated maintenance-agent that also writes
to LanceDB + posts to Comms is a future enhancement; the
timer/CronJob recipes are the supported scheduling paths today.
All three modes share the same ``acc.golden_prompts`` engine.
**Phase 4 / PR-Y** (2026-05-23) — usability after operator testing
found the pane unusable: the TUI image now COPYs the shipped suite
(`9d3dfce`); `load_merged` reads `*.yaml` + `*.md` across shipped <
writable store < attached dirs; the Diagnostics pane gained an in-pane
YAML editor (New/Save), a "+ Add" watch-dir attach, and a 2 s
live-reload poll; markdown import (`parse_markdown_prompt`, front
matter + body); and the Prompt screen now captures each successful
execution as a deduped golden candidate in the writable store
(named volume `acc-golden-data` mounted `:U` at `/app/.acc-golden`).
**Date:** 2026-05-21
**Context:** Every operator session today is a manual smoke test.
Regressions like the agent-side payload-decode bug (Commit-7) went
undetected for releases because no automated suite exercises the
operator → TUI → agent → LLM → reply loop end-to-end. We need a
canonical set of prompts whose expected agent behaviour is
committed alongside them.

**Decision:** Three deployment-environment-dependent runner modes,
sharing the same prompt definitions:

| Mode | When | Where it runs |
|------|------|---------------|
| **CI** (`acc-cli e2e`) | Nightly + on PR | GitHub Actions / GitLab CI; spins the stack via `acc-deploy.sh up` + assertions; the canonical regression gate for **DC** deployments. |
| **TUI Diagnostics pane** | Operator on-demand | New `9 Diagnostics` pane in the TUI; click a prompt from a list, see the agent run + pass/fail. Most useful on **edge** deployments where the TUI is the operator's primary tool. |
| **CLI** (`acc-cli e2e --interactive`) | Operator on-demand without TUI | Same prompts, terminal output; useful for headless edge boxes or for running through ssh. |
| **Scheduled** (`acc-cron`) | Recurring | A dedicated maintenance agent (cron-style) fires the suite hourly/daily, posts a summary to a chosen channel. Configurable per environment. |

**Shared definitions:**

* `examples/golden_prompts/<name>.yaml` — each prompt carries:
  ```yaml
  name: "python_webscraper_basic"
  prompt: "Write a Python webscraper that fetches IBM stock prices from Yahoo Finance"
  target_role: "coding_agent"
  expects:
    reply_non_empty: true
    latency_max_ms: 5000
    invocations_contain: ["SKILL:code_generate"]
    output_matches_regex: "import\\s+(requests|urllib|httpx)"
    blocked: false
  ```
* `acc/cli/e2e_cmd.py` — runs one or all prompts, applies the
  `expects` block, exits non-zero on failure.
* `acc/tui/screens/diagnostics.py` (new) — DataTable of prompts +
  Run-selected + Run-all buttons.
* `acc/scheduler/maintenance_agent.py` — pulls the suite on cron;
  writes results to LanceDB and Comms.

**Implementation outline:**

* Phase 1: ship the YAML schema + CLI runner + 8 canonical
  prompts (webscraper, code-review, refactor, security-scan, …).
* Phase 2: Diagnostics TUI pane.
* Phase 3: maintenance-agent scheduling.

Tests: `test_golden_prompt_schema.py`,
`test_e2e_runner_passes_on_real_stack.py` (marked `@pytest.mark.e2e`
so it's opt-in).

---

## D-006 — Implementation order

**Status:** AGREED
**Date:** 2026-05-21

```
D-004 (Compliance pane redesign)
  └─ D-002 (RAG default-on)
       └─ D-001 (PR-G worker pool)
            └─ D-005 (Golden-prompt suite)
                 └─ D-003 (Operating modes)
```

Rationale:

* D-004 first — unblocks the operator's eval loop. Without
  payload context in Compliance, approving D-002's RAG outputs or
  D-003's mode gating is approve-blind.
* D-002 second — closes the most operator-visible quality gap
  ("the agent doesn't remember anything"). Independent of D-001
  and D-003.
* D-001 third — required before coding_agent-specific testing is
  meaningful. Without it the arbiter answers everything and
  D-005's golden prompts can't differentiate role behaviour.
* D-005 fourth — once roles actually run as themselves, freeze
  the canonical regression set.
* D-003 fifth — operating modes are the cherry on top. Useful
  but not blocking.

Each decision lands as a numbered PR on `main` (PR-H, PR-I, …)
with its own test suite, following the same per-PR commit pattern
as PR-A through PR-F.

---

## D-007 — Trusted working directory (workspace sandbox)

**Status:** LANDED — **PR-U1 (foundation)**, **PR-U2a (role flag +
auto-grant)**, and **PR-U2b (TUI Select-Directory dialog + compose
mount + payload threading)** all on `main` (2026-05-22).  The operator
resolved the open questions below: coding_agent (+ subroles) gets
`workspace_access: true` by default; every other role has the
`workspace_access` flag available in its `role.yaml`, **deactivated by
default**; a single host dir is bind-mounted to `/workspace` in every
agent + the TUI, and the per-task project is selected at prompt time.
**Date:** 2026-05-22
**Context:** Lighthouse testing surfaced that agents answer coding
tasks as *text only* — they never create files, can't iterate on a
real tree, and there's no notion of "where" they work.  The operator
asked for a **trusted working directory** (like Claude Code's trust
dialog): create a new directory or open an existing one; agents get
filesystem access scoped ONLY to that folder; applies to every role.

**Decision — a sandboxed workspace, gated three ways:**

1. **Path sandbox (security core).** `acc/workspace.py:safe_resolve`
   resolves every caller-supplied path against the workspace root to
   a real (symlink-collapsed) absolute path and asserts containment.
   Rejects absolute paths, `..` traversal, and symlink escape.  This
   is the chokepoint every filesystem skill goes through.
2. **Trust flag.** Writes additionally require the operator to have
   *trusted* the directory — a `.acc-workspace-trust` sentinel at the
   root (written by the TUI dialog).  An untrusted directory blocks
   all writes even when the path is in-bounds.  Survives restarts;
   visible to every agent that mounts the workspace.
3. **Operating-mode gate (ties into D-003).** The write skill id is
   `fs_write`, which the D-003 write-action classifier flags — so
   under ACCEPT_EDITS / ASK_PERMISSIONS every file write is funnelled
   through the human-oversight queue before touching disk.

**Skills (separate ids for selective allow-listing + gating):**
* `fs_read`  (risk MEDIUM, read-only, no trust required) — read a
  file from the workspace.
* `fs_write` (risk HIGH, trust required, write-action) — write a
  file into the workspace.
(`fs_list` / `fs_mkdir` are easy follow-ons if needed.)

**PR breakdown:**
* **PR-U1 (LANDED)** — `acc/workspace.py` sandbox (resolve + trust
  helpers), `skills/fs_read` + `skills/fs_write` adapters, 21 tests
  covering escape vectors (absolute / traversal / symlink), the
  trust flag, the skill round-trip, and the D-003 write-classifier
  integration.  **Not yet wired into any role's `allowed_skills`,
  not yet mounted** — so building it grants NO live access; it's the
  safe foundation + the thing that *prevents* escape.
* **PR-U2a (LANDED)** — role wiring: `workspace_access: bool = False`
  on `RoleDefinitionConfig`; a `model_validator` auto-grants
  `fs_read` + `fs_write` (and raises `max_skill_risk_level` to HIGH)
  whenever a role sets it true.  `coding_agent` + its 5 subroles ship
  `workspace_access: true`; every other role.yaml inherits the
  default-off flag, so the option is present but inert until the
  operator opts in.
* **PR-U2b (LANDED)** — TUI Select-Directory dialog + wiring:
  `acc/tui/widgets/workspace_select_modal.py` (`WorkspaceSelectModal`)
  browses `/workspace`, creates-new / highlights a project dir, marks
  it trusted, and dismisses with the path.  The Prompt screen gets a
  **"Select Directory"** button (bottom-left of the prompt input) +
  a path-display `Static`; the chosen project rides the TASK_ASSIGN
  as a `workspace` field (relative to the mount).  Agents honour it
  per-task via `_resolve_task_workspace_dir` →
  `ACC_WORKSPACE_DIR`.  The host workspace dir
  (`${ACC_WORKSPACE_HOST_DIR:-../../workspaces}`) is bind-mounted
  `:z` (SELinux-labelled) to `/workspace` on all six agent services
  **and** acc-tui, so a directory trusted in the TUI is visible to
  every agent.

**Open questions — RESOLVED by the operator (2026-05-22):**
* Which roles get `fs_write` by default? → **coding_agent + subroles
  only.**  All other roles expose `workspace_access` in their
  `role.yaml`, deactivated by default.
* One host dir or per-cluster isolated dirs? → **a single host dir**
  bind-mounted into every agent + the TUI (shared trust sentinel);
  the per-project scoping is chosen at prompt time, not per cluster.

* **PR-X (LANDED 2026-05-23)** — **recreate-on-select** rework after
  operator testing showed the fixed-mount picker hit `mkdir: Permission
  denied` (the `:z`-only mount + container uid mismatch).  New model:
  the picker browses the host base (`ACC_WORKSPACE_BASE`, default
  `$HOME`) mounted **read-only** at `/host-home`; on Confirm the TUI
  writes an apply request (`acc/workspace_apply.py`) naming the host
  path; a host-side watcher (`scripts/acc-apply-watcher.sh`, started by
  `acc-deploy.sh setup`) runs `acc-deploy.sh apply-workspace <path>`
  which mkdir's it, writes the trust sentinel host-side (correct uid),
  re-points `ACC_WORKSPACE_HOST_DIR`, and **force-recreates only the
  agent services** — the selected dir *becomes* `/workspace`.  acc-tui
  + the LanceDB/Redis/NATS named volumes survive, so the operator's
  session and agent memory are untouched.  Concurrency: `fs_write` now
  uses `locked_atomic_write` (atomic temp+replace under a per-root
  `flock` + in-process lock).  Operator decisions (2026-05-23):
  mechanism = recreate-on-select; browse base = home directory.
  Caveat: agents restart (~seconds) per pick; `ACC_WORKSPACE_BASE`
  bounds the browsable/ mountable blast radius.  See
  `docs/workspace_setup.md`.

**Related, not yet decided** — the "no interaction / no spawning"
observation (agents don't run the implementer→reviewer→tester
micro-cycle): that's a separate **coding-workflow PLAN** decision
that combines the worker pool (D-001) + a PLAN that decomposes a
coding task across subroles.  Tracked under "Future considerations".

---

## D-008 — Compliance / governance pane enhancements

**Status:** Phase 1 + Phase 2 + Phase 3 LANDED on `main` (2026-05-23).
**Date:** 2026-05-23
**Context:** Operator testing found the Compliance pane showed outcomes
(OWASP grading, health, oversight queue, violation log) but gave no
visibility into *what governance is loaded* and no way to browse it,
measure it against enterprise frameworks, or act per-item on oversight.

**Decision (operator-locked 2026-05-23):**
* Enterprise policy = **BOTH** built-in reference frameworks + import of
  unsupported/custom ones (BSI), with gap analysis → generated
  enforceable Cat-B/C rulesets (Cat-A immutable).
* Learned rules = **operator-selectable** *propose-pending-approval*
  (default) vs *auto-activate*.
* **TUI rework first**, then the agent/learning phases.

**Phase 1 (LANDED)** — `acc/governance_inventory.py` (PR-Z1a) parses the
Cat-A/B/C Rego files into version + rule lists; the pane gains three
collapsible Cat-A/B/C sections + a read-only `PolicyViewerModal`
(PR-Z1b); the Human Oversight queue became a focusable row-cursor table
(`o`/↑↓/`a`/`r`, per-item approve, HIGH_CONSEQUENCE confirm) and
`regulatory_layer` is mounted `:ro` into acc-tui (PR-Z1c).

**Phase 2 (LANDED)** — `acc/frameworks.py` + four built-in catalogs
(NIST AI RMF, EU AI Act, ISO 42001, SOC 2) with custom import (PR-Z2a);
`acc/gap_analysis.py` deterministic coverage mapping + JSON/markdown
audit doc + LLM prompt builder (PR-Z2b); a Frameworks collapsible with
import + Run-gap-scan that writes the audit report and opens it (PR-Z2c).
Writable named volumes `acc-frameworks-data` + `acc-compliance-data`.

**Phase 3 (LANDED)** — `compliance_officer` role extended with
COMPLIANCE_GAP_SCAN / SELF_CHALLENGE / LEARNED_RULE_PROPOSE +
workspace_access (PR-Z3a); `acc/rule_proposals.py` — Cat-B/C-only
proposals with a `learned_rule_promotion` setpoint (propose|auto) +
pending-proposals overlay the arbiter consumes (PR-Z3b); 
`acc/violation_learning.py` clusters the violation log → Cat-C
proposals (PR-Z3c); a Rule Proposals review surface in the pane with
per-item Approve/Reject (PR-Z3d); `acc/self_challenge.py` red-teams
Cat-A → mitigation proposals + a button (PR-Z3e); `acc/compliance_scan.py`
runs gap-scan + self-challenge on demand / `--loop` for scheduling
(PR-Z3f).  Cat-A is never machine-edited — proposals are validated
Cat-B/C only and routed through the signed bundle overlay.  See
`docs/compliance_governance.md`.

---

## D-009 — Prompt caching · Multimodel reviewer · Self-reflective memory

**Status:** LANDED on `main` (2026-05-23). **Date:** 2026-05-23
**Context:** three cost/quality levers requested together, designed to
work in DC **and** on the edge / autonomous deployments.

**Operator decisions (locked):**
* Order: Caching → Multimodel/Reviewer → Memory.
* Caching: a **backend-independent core first** (stable prompt prefix),
  Anthropic `cache_control` only an optional DC accelerator, optional in
  all modes.
* Reviewer: **extend the existing per-step critic loop**, run on a
  powerful model — no new plan-level gate.
* Per-agent model: **central `models.yaml` registry + Agentset dropdown**
  (1:1), via `AgentSpec.model`, not hand-edited env.
* Memory notes persistence: **dual-layer** (small LanceDB `memory_notes`
  table + Redis per-role hot-cache), all writes out-of-band, O(1) read.

**Phase 1 — Caching (PR-CA1..3):** `build_system_prompt` is now a stable
per-role prefix; RAG + memory notes moved to the LLM user message so
every backend's prefix cache hits (vLLM/Ollama/Anthropic) — works on the
edge, no dependency. Optional `cache_prefix` on `complete()`; Anthropic
`cache_control` + cache-token usage behind `enable_prompt_cache`
(default off). Best-effort cache metrics in the Performance pane.
See `docs/prompt_caching.md`.

**Phase 2 — Multimodel + reviewer (PR-MM1..3):** central `models.yaml`
(`acc.models`) + `AgentSpec.model` → per-agent LLM env via
`roles_to_compose`; Agentset **Model dropdown** (1:1); generic
`roles/reviewer` emits a JSON verdict that `acc.agent._extract_eval_outcome`
surfaces as `eval_outcome` on TASK_COMPLETE so the existing
`plan._maybe_reissue_for_revise` re-issues the reviewed step on
NEEDS_REVISE. `collective.reviewer.yaml` = cheap workers + powerful
reviewer. See `docs/multimodel_reviewer.md`.

**Phase 3 — Self-reflective memory (PR-MEM1..3):**
`acc.memory_reflection.consolidate` clusters recent episodes → durable
LLM memory notes (excludes MEMORY_NOTE); dual-layer persistence
(`memory_notes` LanceDB table + Redis hot-cache,
`redis_memory_notes_key`); out-of-band `Agent._reflection_loop`
(`ACC_REFLECTION_INTERVAL_S`, default off; role flag `memory_reflection`);
O(1) hot-path read prepends notes to the user message. See
`docs/memory_reflection.md`.

---

## D-010 — RHOAI operator bring-up via GitOps (blackbox3)

**Status:** acc-side LANDED (2026-05-23); the **GitOps deploy procedure lives
in the separate `lab-gitops` project, NOT in `acc`** (operator decision). Live
test (Phase R4) + CRD parity closers deferred. **Date:** 2026-05-23
**Context:** install the ACC operator into the local operator catalog of the
**blackbox3** OpenShift+RHOAI3 cluster, GitOps-driven. Deploy/ops are kept out
of the `acc` repo entirely.

**Decisions (locked):** target `blackbox3` (88.99.192.92), **internal
registry**; install **Method C** (local CatalogSource) via **OpenShift GitOps
(ArgoCD) + OpenShift Pipelines (Tekton, in-cluster build) + Ansible AAP
(aap1)**; **all GitOps manifests + the deploy runbook live in `lab-gitops`**
(`github.com/flg77/lab-gitops`, ArgoCD app-of-apps) — coordinated with the
instance managing that repo; `acc` keeps **no** `gitops/` tree or deploy doc.

**In `acc` (kept):** `operator/` (the operator itself) + `operator/Makefile`
`catalog-build/catalog-push/CATALOG_IMG` (+ opm) — operator *packaging*
capability the lab-gitops pipeline calls. Docs: `operator-agentset-guide.md`
(instantiate agentsets via the CRD; per-agent model/memory/cache via
`AgentRoleSpec.extraEnv` today), `operator-standalone-parity.md` (dev↔DC gap
table + process rule + parity closers). The `gitops/` tree +
`rhoai-bringup-blackbox3.md` runbook that briefly lived here were **removed**
and handed off to lab-gitops.

**Key finding:** the recent standalone features (multimodel, memory reflection,
prompt cache) are **expressible on the operator today via `extraEnv` + the
embedded `role.yaml`** — drift is ergonomic, not blocking. The only genuine
delivery gap is `regulatory_layer/frameworks/` not embedded (matters only for
in-cluster `compliance_officer` gap scans).

**Deferred (tracked follow-up):** clean CRD fields (`AgentRoleSpec.model`,
`RoleDefinition.memory*`) + `acc_config.go` template rendering + `sync-manifests`
frameworks embed + Python↔Go parity test + CI; the lab-gitops manifests + the
live bring-up on blackbox3.

---

## D-011 — Infusion and specialist hand-off execute under AUTO; the human is asked for system access and acting-on-behalf

**Status:** Phase 1.1 LANDED (dispatch table, #322); Phase 1.3 LANDED
(`AUTO_APPROVED` rows + decision history, #323); Phase 1.2 gate categories
LANDED (#324); 1.2b escalation LANDED — operator decided 2026-09-02 that a
human grant for one call is the declared capability A-006 speaks of;
Phase 1.4 (the permission request in the Prompt pane, #326), 1.5 (outcomes +
continuation replies in the thread) and 1.6 (the role prompt says the real
rule) LANDED, released **v0.11.0** (2026-09-03) and **verified live on
lighthouse 2026-09-05** through the real TUI on the live bus (approve, reject,
allow-once, the batch request) — which also found that 1.4/1.5 never reached
the observer on the wire until D-012 (v0.11.2);
`openspec/changes/20260902-assistant-autonomy-prompt-pane-approvals`.
**Date:** 2026-09-02
**Context:** Stage 1.4 (`7f49a9e`, 2026-06-04) recorded the operator's choice
"PROPOSE_INFUSE always routes through the Compliance pane, whatever the
operating mode" (with a dev-mode escape). On 2026-09-02 a lighthouse trace
showed the consequence: the assistant proposed `@acc/redhat-sre-roles`, the
operator approved in Compliance, and the conversation in the Prompt pane
went nowhere (two plain bugs, fixed in #321, plus this trust model).

**Decision (operator, 2026-09-02):** infusing curated roles and putting
specialists onto a user task are the assistant's *central* function.
`AUTO` means execute-and-track, bound only by Cat-A/B/C. Only curated
roles land in the catalog; a pack that passes the signing floor at install
is trusted by construction, so a human click adds nothing a signature does
not — and cannot rescue an unsigned pack. What warrants a human is anything
beyond the role's allowed tools and skills: system access and actions on
the user's behalf. In `ASK_PERMISSIONS` the question is asked inside the
Prompt pane; Compliance keeps the record.

**Reversed:** the Stage 1.4 Q2 choice and its dev-mode escape.
`_NEVER_AUTOEXEC` is now `{ROLE_GAP, PUBLISH}`; `ACCEPT_EDITS` executes
`ROUTE`, `SPAWN`, `INFUSE`; `ROLE_UPDATE` stays queued below `AUTO`. A
signing-floor failure is refused with a `proposal_dispatch_failed` notice,
never queued.

**Still open (this change):** the full sweep and the three lighthouse smokes
in `tasks.md`; the sibling `20260902-tui-profiles`; Phases 2–3 (the proposal
waits on the verdict in-turn; signed intent on the reconcile trigger).

---

## D-012 — The wire is msgpack-of-JSON, normalised once at publish; observers tolerate a map

**Status:** LANDED, released **v0.11.2** (2026-09-05, #341); verified on
lighthouse — the production WebGUI hub went from 81 decode errors to none.
**Date:** 2026-09-05
**Context:** `NATSBackend.publish(subject, payload: bytes)` msgpack-packs
whatever it is handed. Several publishers (assistant proposal pending /
outcome notices, the infuse-continuation `TASK_ASSIGN`, every
`TASK_PROGRESS`) handed it a dict, so a msgpack *map* went on the wire.
Agents tolerate that (`_payload_bytes`), the TUI observer's
`unpackb → json.loads` did not: D-011's 1.4 rationale never joined its
pending row, 1.5's outcome notices never rendered, batch requests degraded
to one per row, and the live progress line never moved. Unit tests stub the
signaling and inspect dicts, so agent-to-agent flows and the suite never
saw it; a bus sniff during the lighthouse smoke did.

**Decision:** the canonical wire format is `msgpack(json.dumps(payload).encode())`
and it is enforced in **one place** — `NATSBackend.publish` JSON-encodes any
non-bytes payload. Consumers stay strict but tolerant: the observer accepts a
map from an agent older than the fix. No publisher is asked to remember the
encoding again.

**Consequences:** every dict-publishing path is covered without touching it;
mixed-version collectives (old agents, new TUI) still work; a future
publisher that packs a map by hand is an observer-side warning, not a silent
drop. Tests pin both halves (`tests/test_backends_signaling.py`,
`tests/test_tui_client.py`). A bus sniffer that tries the observer's decode
per subject is the fastest way to find this class of bug on a live host.

## D-013 — An oversight decision is final; one row per decision and per submit

**Status:** LANDED — decision finality and one row per decision released
**v0.11.3** (#343); one row per `OVERSIGHT_SUBMIT` released **v0.11.4**
(#345); all verified on lighthouse 2026-09-06.
**Date:** 2026-09-06
**Context:** `OVERSIGHT_DECISION` and `OVERSIGHT_SUBMIT` are ENDOCRINE —
every agent applies them (deliberately: each must learn the outcome of items
it submitted; the dispatch is claimed exactly once). Three consequences
surfaced in the v0.11.1 smokes: every agent pushed the decided id onto the
history list (six copies of one decision on a six-agent collective); a
decided row accepted a second, conflicting decision (a REJECT after an
APPROVE flipped it, and a late APPROVE on an EXPIRED gate would have
dispatched); every agent minted its own id for a synthetic submit (four rows
for one `acc-cli oversight submit`). Separately, the CLI printed ids
truncated to 18 characters that `approve`/`reject` could not find.

**Decision:** the **first decision stands**. `approve`/`reject` return a
bool and refuse a row already decided the other way or expired (logged, no
dispatch); the same decision arriving again is an idempotent no-op that
keeps the first approver. The decided list holds **one row per id**
(`_push_decided` removes an earlier copy; `recent_decisions` de-duplicates).
A synthetic submit carries **one id minted by the publisher**; an agent that
receives one without an id derives the same UUID5 from the event so N
subscribers still enqueue one row. The CLI prints full ids and resolves a
**unique prefix** against the arbiter heartbeat, refusing an ambiguous one.

**Consequences:** a late human click cannot reopen a gate the task already
gave up on; the Compliance history is one line per decision; a synthetic
submit is one row; operators can type a prefix. The trade-off is that an
operator cannot "override" an approval with a reject any more — that would
need an explicit override decision kind, deliberately not built.

## D-014 — A principal carries a category ceiling; above it the work is refused, not asked

**Status:** LANDED, released **v0.12.0** (2026-09-06, #348) and verified on
lighthouse the same evening: a HIGH skill refused for a Slack-attributed
requester with no gate row, the SYSTEM-ACCESS gate for the operator as before,
an INFUSE proposal dropped under AUTO, `access admit --ceiling` narrow-only.
**Date:** 2026-09-06
**Context:** `20260823-attributed-memory` settled requester-vs-role authority
by the floor rule and found ACC could not express one side of it: tiers say
*may you ask, may you approve*; role grants say *what may be done*; nothing
said *how far the work someone asked for may go*. `OC-04` had put it as "a
Cat-C-capable role reachable from Slack by anyone is not a defensible
configuration, and ACC currently cannot even express the constraint." The
1.2b escalation made it worse in one respect: an off-role HIGH call from an
external requester became a question the operator could answer yes to.

**Decision:** every principal carries a **category ceiling** on the scale the
runtime already enforces (`LOW < MEDIUM < HIGH < CRITICAL` — the role's
`max_*_risk_level` and every manifest's `risk_level`), so
`effective = role grants ∩ principal ceiling` is a `min()` on one axis. It
**defaults from the tier** (`none`/`viewer` LOW, `requester` **MEDIUM**,
`operator` CRITICAL) and an admission may only **narrow** it
(`access admit --ceiling`, `Grant.ceiling`); nothing widens it, including a
hand-edited `access.yaml`. It travels on the task as `requester_ceiling`
from every admitting surface (channels, the compat endpoint) and is absent
on the operator's own unattributed work. Above the ceiling a capability
invocation is **refused before any escalation or gate** and an assistant
proposal is **dropped rather than queued**: the ceiling is a floor under
human judgement, not a question for it. The 1.2 gate categories (system
access, acts on behalf) remain questions for the human and are unchanged.

**Consequences:** the indefensible configuration is now inexpressible: a
requester from Slack or an API key gets MEDIUM work at most, whatever the
role was widened to. An OpenAI-compatible caller can no longer run a HIGH
skill or queue an INFUSE on its key's word (it could before, with a human
approval as the only check). The operator's TUI / Web GUI / Kubernetes work
is unchanged. Not done here, and now expressible: the memory retrieval /
publication floor on the stamped ceiling (memory change `[2]`, Phase 4+),
and attribution propagation onto plan steps the arbiter dispatches.

## D-015 — An instance is a collective bound to an owner, a posture and its own state; the definition travels, the state never does

**Status:** LANDED, released **v0.13.0** (2026-09-06, #354) + **v0.13.1** (#356);
verified on lighthouse the same evening (`20260906-acc-instance` Phase 2).
**Date:** 2026-09-06
**Context:** HG-40 asked for Hermes-style profiles that keep their own memory,
registry and sessions. `20260817-named-deployment-profiles` shipped a
*posture* and left "does a profile isolate state?" open. The word was
overloaded three ways (posture, TUI view, what Hermes means). The runtime
already isolates by `collective_id` (NATS, Redis, LanceDB); the tracelog and
the TUI's sessions were unpartitioned env-read directories; overlays came
from the cwd; nothing bound any of it to a person; the TUI acted as
`tui:anonymous`.

**Decision:** the missing thing is a **binding**, not a mechanism. An
**instance** is a collective (the id is the collective id) owned by one
principal the substrate vouches for (`kubernetes` / `system` / `web` — never
an external identity), running under one posture, with its own directory
holding the collective definition (the installed set), the overlays and the
state roots. Cells get their roots by environment and mounts; the agent
reads its overlay dir from `ACC_COLLECTIVE_DIR`. `profile` keeps naming the
posture; `instance` is the binding. **Export carries the definition** —
collective, overlays, posture, hub binding — **never the owner and never the
state**, and says what it left behind; the signature field is reserved for
when archives travel. `archive` never deletes state; erasure is `memory
forget`. Under rootless podman the cells own `lancedb/` and `trace/` (`U`),
read `overlays/` read-only, and do not mount `sessions/` (the TUI's, on the
host). The TUI carries its owner: decisions, board actions and every prompt
are attributed to the resolved principal; the memory source stays `tui`.

**Consequences:** a personal, long-lived agent is one `instance create` and
one `instance up`, beside the base stack, with nothing shared but the bus,
Redis and the pack registry; the enterprise brain (HG-40.1b) gets a `hub`
field already stored and passed to the cells. Plan steps inside an instance
still run unattributed (the owner rides the prompt path only). T2 — an
instance is a whole collective per person — is assumed; T1 (many people in
one collective) is the team agent and needs HG-40.1b's per-requester views.

## D-016 — The enterprise brain is a hub's shared tier, filled only through people, read by every bound instance, and the information rule holds at every memory boundary

**Status:** LANDED, released **v0.14.0** (2026-09-07, #359); verified on
lighthouse with cells built from the branch (`20260906-enterprise-brain-hub-scope`).
**Date:** 2026-09-07
**Context:** `20260823-attributed-memory` built the brain's behaviour —
private notes, publication only through a proposal a person approves,
quorum of two people, dissent, probation, erasure — inside one collective.
Instances (D-015) gave each person a collective; nothing let them publish
into, or read from, a scope above their own. Three things were found while
building this: nothing at runtime ever *built* a publish proposal; notes
carried no ceiling, so the information rule the memory change settled
(a fragment is not retrieved below the ceiling of the task that produced it)
was not enforced; and `hub_collective_id` never reached memory.

**Decision:** a **hub** is a collective id; its **enterprise tier** is its
shared tier under a fixed destination. A publish proposal may name
`hub:<cid>`; on approval the note lands there, under the hub's collective
id, never in the publisher's own shared tier, with everything the memory
change requires still applying. A collective bound to a hub reads that tier
on the prompt path and **nothing else across instances** — never a peer's
shared tier, never the hub twice (hub-only; peer reads would bring back
"one person's habit becomes everyone's" without a quorum). **The
information rule is enforced**: a note carries the highest ceiling among
its source tasks (an unattributed source counts as the operator's,
CRITICAL), the cache entry and every published copy carry it with the note
id and the people behind it, and the read skips anything above the
requester's ceiling in the own scope, the shared tier and the hub alike; a
cache written before ceilings reads as CRITICAL. Proposing is a person's act
(`acc-cli memory propose`, queued the way the assistant queues, approved
where every proposal is approved); the curator's job is a deterministic
look (`memory curate`) before it is a role. Erasure reaches the hub. **θ
stays per instance; the hub never learns.**

**Consequences:** two people's approvals put a lesson in front of every
instance on the host; a requester below the lesson's ceiling never sees it,
whatever a human approved; `forget` in one instance pulls a person's
contribution from the hub. Not yet: the curator role and its schedule, an
approver-tier check on hub promotions (the decision payload carries no
tier), per-requester views, retention. Two operator confirmations pending:
hub-only reads, and no learning at the hub.

## Future considerations (not yet decided)

* **Multi-collective infusion** — today PR-D writes to a single
  `collective.yaml`; for federations the operator may want to
  infuse a role across multiple collectives in one Apply.
* **Cost tracking** — Performance pane shows token budget utilisation;
  consider a per-task USD cost estimate using model-list pricing.
* **Skill marketplace** — `acc-cli skill install <package>` like
  `pip install` for trusted skill bundles, with signature
  verification (reuse the role-sync ed25519 chain).
* **Edge ↔ DC handoff** — when an edge collective hits its
  compute ceiling, delegate the task to a DC collective. The
  bridge code exists (`[DELEGATE:cid:reason]` markers); needs
  the DC side wired as a peer collective.
* **WebGUI parity** — the current WebGUI surfaces a subset of the
  TUI. PR-G's worker-pool reconcile and D-002's RAG context
  should be exposed there too for ops staff who prefer browser
  UX.
* **Audit-log RAG** — separate from agent-side episode RAG; the
  TUI's `History ▼` button on Nucleus could surface "similar
  past infusions" for the operator to reuse.
