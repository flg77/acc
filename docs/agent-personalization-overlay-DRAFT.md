# Agent personalization overlay — `soul.md` / `collective.md` / `AGENTS.md`

> **Status: DRAFT for internal review.** Proposal 1 of 2 (this is the
> *foundation*; the `role_lifecycle` + routing-governor proposal builds on it).
> Author: hub (workstation), 2026-06-25. Scope: `acc-spearhead` (dev source;
> promote vetted → mirror `flg77/acc` via acc-promote).

## 0. Design revision — 2026-06-25 (role-scoped; supersedes §4 layout, §7.1 scope, §9 soul phasing, §10)

> Operator design change after the §10 resolutions landed (PR #114). It moves the
> overlay from **workspace-scoped** to **role-scoped**, makes the role a
> **self-contained directory**, and adds a **per-role capability surface**. Where
> this section conflicts with the body below, **this section wins**; the merge-core
> mechanics (§5.3 resolver, §4 merge order) are unchanged.

**1. The role is a self-contained directory.** Every role ACC ships owns its full
set of files, co-located — overlays are **role-scoped**, not workspace-scoped:

```
<roles-root>/<role-name>/
  role.yaml      # signed, immutable — editing breaks the signature (pack-owned)
  role.md        # signed authoring source → compiled seed_context (pack-owned)
  AGENTS.md      # NEW · user-editable · role-scoped operational overlay (Tier-1 toggles + Tier-2 context)
  soul.md        # NEW · user-editable · this role's persona / voice (Tier-2, voice-wins)
  skills/        # NEW · role-local skill definitions (shipped = signed; user-added = local/unsigned)
  mcp/           # NEW · role-local MCP definitions (same trust split as skills/)
```

`role.yaml` + `role.md` stay the **signed, immutable package core** (editing them
invalidates the cosign signature). `AGENTS.md` / `soul.md` / `skills/` / `mcp/`
are the **user-editable surface**, **picked up at runtime init — no repackage,
no re-sign of the core**. This supersedes §4's workspace-rooted "Default layout"
and §9's "workspace/operator-scoped" P0.

**2. `soul.md` = the role's persona, user-tunable** (not a user-global voice).
Each role carries its own `soul.md`; the user edits it to tune that role's
voice/identity. The earlier "one user-voice that follows the person across
collectives" idea (§4 "follows the user", §9 P2) is **dropped/deferred** — it
depended on AoA-P5 per-user identity anyway. The §4 merge order is unchanged in
mechanics: `soul.md` still applies last so **voice/identity wins**; only its
*scope* changes (per-role, not per-user).

**3. `AGENTS.md` = role-scoped** — exactly one per role directory. This
supersedes the §10 "keyed by `cluster_id`/`role`" resolution: there is no
per-cluster keying; all instances of a role share the role's `AGENTS.md`.

**4. `collective.md` is unchanged** — it remains **agentset-scoped** (lives with
`collective.yaml` at the collective/workspace level), *not* in the role dir. Only
`AGENTS.md` + `soul.md` move into the per-role directory.

**5. Per-role `skills/` + `mcp/` — a governed local capability surface.** Roles
may now bundle skill/MCP **definitions** in their own dir, and users may add
**agent-specific** capabilities *there* — never in the systemwide
`accroot/{skills,roles}` pool. Trust splits by origin:

- **Shipped** (in the release): part of the **signed package** → fully trusted,
  resolvable for that role like any packaged capability; listed in `role.yaml`'s
  `allowed_*`/`default_*` and signed alongside it.
- **User-added** (dropped into the role's own `skills/`|`mcp/`): resolvable for
  **that one agent only**. Granting it **widens the envelope for that local agent
  exclusively via the existing `allow_unsigned` path** — operator-explicit,
  **audit-logged**, constrained risk tier, **never silent, never prod-default**.
  It is **not** promoted into the shared pool and **not** added to any other
  agent's envelope.

This **refines** the §7.1 invariant rather than breaking it: the *overlay files*
(`soul.md`/`AGENTS.md`/`collective.md`) still **cannot** widen `allowed_*` — they
only toggle within the ceiling. The per-role `skills/`/`mcp/` dir is a
**separate, explicit, operator-gated** local-extension surface, and the "never
*silently* widen" principle holds (every local add rides `allow_unsigned` +
audit). It is the low-friction, **local/unsigned** counterpart to proposal 2's
**signed-infuse** path: a capability gap now resolves **two** operator-choosable
ways — *promote to the trusted shared pool* (signed infuse / A-BOM, proposal 2)
**or** *accept it local + unsigned for this agent* (role-dir add, `allow_unsigned`).

## 1. Problem

ACC personalizes **per role**, never **per user, per project, or per agent**. A
role's behaviour is fixed in its signed package (`roles/<id>/role.yaml` +
`role.md`). That is correct for *trust* — packs are cosign-signed and
`eval_pass`-attested — but it leaves three gaps:

1. **No user voice.** Every operator gets the same persona. There is no place to
   say "I'm an expert, be terse" vs "I'm new, explain first."
2. **No project context.** A role can't be told "in *this* repo, 'done' means CI
   green + a changelog entry" without forking the pack.
3. **Per-deployment reality leaks into the signed prompt.** Concrete evidence in
   this very repo: `roles/assistant/role.md:9` still says the Assistant *"holds
   no skills, no MCP servers, and no workspace access"* — while `role.yaml` now
   grants `shell_exec`, `python_exec`, `fs_read/fs_write`, git, three MCPs, and
   `workspace_access: true`. The signed doc drifted because there was nowhere
   *else* to record deployment-specific truth.

The operator wants users to **personalize and extend an agent without losing the
role's characteristics** and **without forking the role** — so the ACC team can
keep shipping *supported* role versions (`v2.3 → v2.4`) and the user's
personalization keeps working across the upgrade.

## 2. Goals / non-goals

**Goals**

- A human-authored **overlay layer** that tunes voice + context and toggles
  capabilities **within the role's signed envelope**, applied at
  prompt-assembly time.
- **Supported-version contract:** overlays bind to a stable capability
  vocabulary, never to `role.yaml` internals, so role upgrades don't clobber
  personalization and personalization never forks the role.
- **Three scopes:** user (`soul.md`), agentset (`collective.md`), individual
  agent (`AGENTS.md`).
- **Observable & governable:** the resolved "effective profile" is computable,
  logged, and inspectable; the signed safety floor is structurally unreachable
  from an overlay.

**Non-goals (this proposal)**

- Granting capabilities **beyond** the signed envelope — that is a *capability
  gap* and routes through the signed infuse / A-BOM path (proposal 2).
- True multi-user identity namespacing — depends on AoA-P5
  (`ACC_OPERATOR_ID_SOURCE=session`); until then `soul.md` is **workspace/operator
  scoped** (§4, §9).
- Editing `role.yaml` / `role.md` / the compiled `seed_context` — those stay
  pack-owned and signed.

## 3. The layering model — package vs config

**Framing:** `role.(yaml,md)` is the *signed package* — the constitution, the
competence, and the capability **envelope**. The overlay files are *config
within that envelope* — `/etc` to a package, `values.yaml` to a chart. You gain
capability by switching on what the envelope already permits (instant), or by
declaring a desire it doesn't cover (→ governed infuse, proposal 2). You never
edit, fork, or lose the role.

`role.yaml` **already** stratifies into three tiers; this proposal formalizes the
boundary that is implicit today:

| Tier | Real `role.yaml` fields | Signed? | Overlay may… |
|---|---|---|---|
| **0 — Envelope / invariant** | `purpose`, `persona`, `task_types`, `domain_id`, **`allowed_skills`**, **`allowed_mcps`**, **`allowed_actions`**, `max_skill_risk_level`, `category_b_overrides` (token/rate ceilings), policy bounds, the compiled `seed_context` core | ✅ pack-owned | **never touch** — the ceiling + identity + safety floor |
| **1 — Activation within envelope** | `default_skills` (⊆ `allowed_skills`), `default_mcps` (⊆ `allowed_mcps`), `default_operating_mode`, `proactive_wakeup`/`wakeup_interval_s`, model pick, `reasoning_trace`, `memory_retrieval`, `perception_profile` | ✅ pack ships defaults | **toggle within the ceiling** — enable an allowed-but-default-off capability, or narrow |
| **2 — Context / voice** | *(no home today)* | ❌ | **append freely** — profile, tone, project goals, conventions |

The crux — *"gain capability without losing characteristics"* — lives entirely in
**Tier 1**, and the machinery already exists:

- `roles/assistant/role.yaml` declares
  `allowed_skills: [shell_exec, python_exec, test_execution, echo, git_status, git_log_recent, catalog_query]`
  but `default_skills: [echo, catalog_query]`.
- Everything in `allowed` but not in `default` is a **pre-vetted, already-eval'd
  capability sitting dormant**.
- `acc/cognitive_core.py:1560` advertises exactly `default_skills ∩ allowed_skills`;
  `capability_validator.py` enforces `default_skills ⊆ allowed_skills`.

So an overlay that enables `git_status` for a project **gains capability** with
no re-sign, no re-eval, no pack edit — because it was already inside the signed
ceiling. The role's character (Tier 0) is **structurally unreachable**: the
overlay schema has no key for `purpose` / `persona` / `max_skill_risk_level`. You
cannot overlay your way out of who the role is or past its safety floor.

## 4. The three overlay files

| | `soul.md` | `collective.md` | `AGENTS.md` |
|---|---|---|---|
| **Scope** | the **user** (follows the person) | the **agentset** (the whole team) | the **individual agent** |
| **Companion to** | — (operator identity) | `collective.yaml` | one agent's role-in-context |
| **Tier 2 content** | who you are, voice/tone, comms prefs, **`user_profile: novice→operator`**, relationship facts | what this team is + why; references each agent's `AGENTS.md` | this agent's project context, conventions, "definition of done" |
| **Tier 1 toggles** | *personal* prefs: verbosity, proactivity level, cost/model lean | team-wide operational defaults | *this agent's* allowed-skill enablement, operating mode |
| **Wins conflicts on…** | **voice & identity** (how it talks to *me*) | team-wide baseline | **operational capability** (most specific) |

`collective.md` is the prose descriptor of the complete agentset — the human
companion to `collective.yaml` — and it **references** each agent's `AGENTS.md`.
Naming follows the emerging open `AGENTS.md` convention so ACC interoperates with
tools that already read it; note the ACC-specific reading: `AGENTS.md` is
**per-agent within a collective**, with `collective.md` as the umbrella (vs the
single repo-root file the convention often assumes).

**Default layout:**

```
<workspace>/
  soul.md                         # operator identity & voice (workspace-scoped in P0; §9)
  collective.yaml                 # machine desired-agentset (existing)
  collective.md                   # NEW prose descriptor; references each agent's AGENTS.md
  agents/
    <cluster_or_role>/AGENTS.md    # NEW per-agent operational overlay
```

**Merge order** for one agent's effective profile, under the Tier-0 ceiling:

```
role.(yaml,md) signed defaults  →  collective.md (team-wide)  →  AGENTS.md (this agent)  →  soul.md (user)
```

Precedence is **two axes**, both *below* the signed envelope:

- **Operational / capability toggles:** `AGENTS.md` (most specific) > `collective.md`
  (team) > role defaults. The project decides *what work happens*.
- **Voice / identity:** `soul.md` (user) wins. How the agent talks to *me* is my
  call.

## 5. Architecture — where it injects (grounded in real seams)

1. **Prompt assembly.** `CognitiveCore.build_system_prompt(role, …)`
   (`acc/cognitive_core.py:1453–1620`) concatenates, in order:
   `purpose → persona → seed_context → sub_collectives → perception ("## Currently
   available") → reasoning block → skills → MCPs → delegation`.
   The overlay injects as an additional fenced block **after `seed_context`
   (~line 1486) and before the sub-collectives block** — i.e. *after* the signed
   identity, *before* the dynamic per-task perception. Fenced with an explicit
   "subordinate user/project preference" banner (§7).

2. **`role.md` is untouched.** It is authoring-only: `acc/role_md.py:compile_markdown()`
   parses its `## System Prompt` section into the signed `seed_context`; it is
   **never read at runtime**. Overlays are *separate runtime files* and do not
   recompile the pack. (This is also the fix for the §1 drift: per-deployment
   truth moves to `AGENTS.md`, so the signed doc stops carrying it.)

3. **Capability toggles patch `default_skills`/`default_mcps` pre-assembly.** A
   resolver computes `default' = (default ∪ overlay_enables) − overlay_disables`,
   then the **existing** validator/intersection (`default' ∩ allowed`,
   cognitive_core.py:1560/1583; `capability_validator.py`) enforces the ceiling.
   An overlay enable that isn't in `allowed_skills` is **dropped + logged** (and
   becomes a capability-gap candidate for proposal 2) — never silently granted.

4. **`collective.md` attaches via a new `description` field on `CollectiveSpec`.**
   `load_collective()` (`acc/collective.py:319`) already validates a Pydantic
   `CollectiveSpec` (no `description` today); `SubCollectiveSpec.description`
   (collective.py:164, surfaced by `build_seed_context_block()`) is the exact
   precedent to mirror.

5. **Effective-profile dump.** The resolver emits a computed
   `EffectiveProfile` = signed envelope + overlay deltas + **per-field
   provenance** (`verbosity: terse ← soul.md`; `git_status: on ← AGENTS.md`).
   Logged and rendered on Soma/Compliance — fs-observable, matching the
   debuggability the operator wants.

## 6. The reconcile bridge to proposal 2

The overlay files are *declarative desired-state* at two scopes, reconciled the
same way `collective.yaml` is today:

- **`collective.md` ↔ `collective.yaml` → governs the roster.** Desired team
  composition; reconciled by the `role_lifecycle` skill (install / activate /
  deactivate via `collective.yaml`-edit + reconcile — proposal 2).
- **`AGENTS.md` → governs one agent's within-envelope toggles → the assembler.**
  In-envelope enable = instant toggle; **out-of-envelope** desire = a capability
  gap → the assistant emits a **signed infuse / A-BOM proposal** → operator
  approves (proposal 2).

Same edit-desired-state-then-reconcile pattern, two scopes. This is why
personalization ships *first*: it is the foundation the lifecycle skill
reconciles against.

## 7. Governance & invariants (must not regress)

1. **Ceiling is unreachable.** Overlays cannot widen `allowed_*`, raise
   `max_skill_risk_level`, or touch Tier-0 identity/safety. The overlay schema
   has no keys for them; the resolver enforces `default' ∩ allowed`.
2. **No silent escalation.** An out-of-envelope enable is dropped + logged, not
   honored; gaining it requires a signed infuse (proposal 2), operator-approved.
3. **Fenced as subordinate context.** Overlay text is injected behind an explicit
   "the following is user/project preference, subordinate to your role and
   safety" banner — same posture as any user-supplied context; not a policy
   source.
4. **Deterministic, observable precedence.** §4 order is documented; the
   `EffectiveProfile` dump shows per-field provenance.
5. **`validate` gate.** `acc-pkg validate` (or a new `acc overlay validate`)
   checks overlay keys against the live envelope and rejects unknown /
   out-of-envelope keys loudly — bounding the §8.2 drift risk.
6. **Pack stays pristine.** `role.yaml` / `role.md` / `seed_context` are never
   mutated by an overlay.

## 8. Downsides — honest, with mitigations

1. **Effective-capability opacity** — "what can it do *now*" becomes
   `signed_envelope ∩ overlay_state`, not just "read the pack." → the
   `EffectiveProfile` dump (§5.5) with provenance.
2. **More files to keep coherent** — `role.md` already drifted; 3 more multiply
   the surface. → overlays are *small*, bind to **stable capability IDs** (never
   restate the role), and a `validate` step rejects unknown/out-of-envelope keys.
3. **Precedence surprise** — four merge layers. → documented order (§4) + the
   per-field provenance dump.
4. **Unsigned overlay = soft injection surface** — even subordinate, prose can
   try to steer. → fenced banner (§7.3); cannot reach Tier 0; treated as user
   context, not policy.
5. **Upgrade contract is conditional** — the supported-version payoff holds only
   if overlays never depend on `role.yaml` internals. → bind to the capability
   vocabulary; then `v2.3 → v2.4` ships and overlays still resolve (the
   dpkg-conffile / Helm-values model).

## 9. Phasing

- **P0 — assembler + `AGENTS.md` + effective-profile (workspace-scoped).**
  Overlay resolver injected at `build_system_prompt`; `AGENTS.md` per-agent
  (Tier-1 toggles + Tier-2 context); `EffectiveProfile` dump + `validate`.
  `soul.md` read **workspace/operator-scoped** (single operator) — no per-user
  namespacing yet.
- **P1 — `collective.md` + reconcile bridge.** `description` on `CollectiveSpec`;
  `collective.md` referencing per-agent `AGENTS.md`; wire the §6 bridge so the
  `role_lifecycle` skill (proposal 2) reconciles roster desired-state.
- **P2 — true per-user `soul.md`.** When AoA-P5 lands
  (`ACC_OPERATOR_ID_SOURCE=session`), namespace `soul.md` per operator id +
  per-user memory; voice/profile follows the user across collectives.
- **P3 — profile-driven proactivity depth.** `user_profile` meters how much
  complexity the assistant absorbs vs surfaces (novice → explain-then-confirm;
  expert → act-first-report-after) — the lever for the "complexity solver"
  behaviour, consumed by the proactive skill suite (proposal 2).

## 10. Open questions — RESOLVED

**⚠ The first two answers below were SUPERSEDED by the §0 revision (2026-06-25).**
Kept for history; §0 is authoritative.

- ~~**`soul.md` home → private-by-default (`~/.acc/soul.md`).**~~ **SUPERSEDED →
  §0.1/§0.2:** `soul.md` is **role-scoped** (lives in the role's own dir,
  user-editable, runtime-picked-up) and now means the **role's persona**, not a
  user-global voice.
- ~~**`AGENTS.md` keying → `cluster_id`/`role`.**~~ **SUPERSEDED → §0.3:**
  `AGENTS.md` is **role-scoped** — exactly one per role directory, no per-cluster
  keying.
- **Overlay format → front-matter-in-markdown.** (Still current.) One human-first
  file per scope with a small fenced front-matter for Tier-1 toggles; no sidecar
  `overlay.yaml`.

**Resolved in the §0 revision (2026-06-25), now P0 build directives:**

- **Role is a self-contained dir** (`role.yaml`+`role.md` signed/immutable +
  `AGENTS.md`/`soul.md`/`skills/`/`mcp/` user-editable) — one location, edits in
  place, picked up at init.
- **Local (user-added) `skills/`/`mcp/`** grant only via `allow_unsigned`
  (operator-explicit, audit-logged, constrained tier, never prod-default); shipped
  role-dir capabilities stay signed/trusted.

## 11. References

- Assembly seam: `acc/cognitive_core.py:1453–1620` (`build_system_prompt`),
  `:1560`/`:1583` (`default ∩ allowed`).
- Authoring vs runtime: `acc/role_md.py:compile_markdown` (`role.md` → `seed_context`).
- Envelope enforcement: `acc/capability_validator.py`; `acc/config.py`
  (`_grant_workspace_skills` / `_grant_os_basics_skills`).
- Collective load + companion precedent: `acc/collective.py:319` (`load_collective`),
  `:164` (`SubCollectiveSpec.description`), `acc/sub_collective.py` (`build_seed_context_block`).
- Per-user roadmap: `openspec/changes/20260530-role-proposal-assistant-agent-of-agents/proposal.md` (AoA-P5).
- Drift evidence: `roles/assistant/role.md:9` vs `roles/assistant/role.yaml`.
- Proposal 2 (builds on this): `role_lifecycle` + routing-governor (forthcoming).
