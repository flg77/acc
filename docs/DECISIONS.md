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
