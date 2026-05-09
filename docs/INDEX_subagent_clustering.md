# Index — Sub-agent Clustering Documentation Set

A single landing page for the documents that came out of PR #26–#30
work. Read in this order if you're new to the topic.

## For implementers / reviewers

1. **`PLAN_subagent_clustering.md`** *(internal — gitignored)*
   Original five-PR design plan. Why each PR exists, what's in
   scope, what's deferred.

2. **`IMPLEMENTATION_subagent_clustering.md`**
   Reference for what shipped. PR-by-PR module breakdown, wire-protocol
   cheat sheet, file map, test matrix.

3. **`SUBAGENT_COMMUNICATION.md`**
   Patterns A–E (static decomposition, knowledge-share fan-in,
   scratchpad rendezvous, autocrine self-feedback, cancel
   propagation). Anti-patterns + failure modes.

## For role authors / operators

4. **`CODING_AGENT_SUBROLES.md`**
   Five named personas (architect, implementer, reviewer, tester,
   dependency_auditor). Skill mix, estimator config, system prompt
   sketches, migration plan from today's bare `coding_agent`.

5. **`../examples/coding_split_skills/README.md`**
   Concrete five-step plan exercising every persona + every
   communication pattern. Runnable once the personas land.

## For demos / leadership

6. **`DEMO_TUI_subagent_clustering.md`**
   Phase-by-phase TUI walkthrough (~7 minutes). What to type, what
   to look at, what proves the feature works, talk track for each
   phase, recovery scripts when something goes wrong.

## For planning the next iteration

7. **`ROADMAP_subagent_clustering.md`**
   Short-, medium-, long-term improvements. What we explicitly
   won't do. Acceptance criteria for "feature complete".

---

## Cross-reference matrix

| If you're trying to … | Read first |
|---|---|
| Understand the wire protocol | IMPLEMENTATION § Wire-protocol summary |
| Pick a communication pattern | SUBAGENT_COMMUNICATION § Patterns |
| Author a new sub-agent persona | CODING_AGENT_SUBROLES § Persona deep-dives |
| Run the showcase demo | DEMO_TUI_subagent_clustering |
| File a follow-up PR | ROADMAP § Short-term |
| Understand why X is the way it is | PLAN (private) + IMPLEMENTATION § Why clustering at all |

## PR cross-reference

| PR | Branch | What it does |
|---|---|---|
| [#26](https://github.com/flg77/acc/pull/26) | `feat/cluster-id-propagation` | Wire-protocol foundation |
| [#27](https://github.com/flg77/acc/pull/27) | `feat/estimator-and-spawn` | Estimator + arbiter fan-out + Cat-A A-019 |
| [#28](https://github.com/flg77/acc/pull/28) | `feat/markdown-role-authoring` | role.md compiler/decompiler/lint CLI |
| [#29](https://github.com/flg77/acc/pull/29) | `feat/cluster-tui-surface` | Cluster topology panel in prompt pane |
| [#30](https://github.com/flg77/acc/pull/30) | `feat/prompt-slash-commands` | `/`-prefixed operator commands incl. `/cancel` and `/cluster kill` |

Stack order: #26 → #27 → #29 → #30. #28 is independent of all
others.

---

## See also

* **[`AUTORESEARCHER_index.md`](AUTORESEARCHER_index.md)** — companion
  index for Example No. 2 (autoresearcher demo, PRs #41-#46).
  Builds on this clustering foundation with iteration loop +
  cost cap + real research MCPs + six research personas.

* **[acc-podman-desktop](https://github.com/flg77/acc-podman-desktop)**
  — sibling Podman Desktop extension that surfaces the cluster
  topology described here as a webview, mirroring the TUI
  rendering described in `IMPLEMENTATION_subagent_clustering.md
  § PR #29`.  Same wire format (msgpack-of-JSON over NATS), same
  30 s grace window for finished clusters.  Operators using PD as
  their primary container UI get the cluster panel without
  installing the TUI.  See:
  * [`docs/EXTENSION_implementation.md`](https://github.com/flg77/acc-podman-desktop/blob/main/docs/EXTENSION_implementation.md)
    — module + wire-protocol reference (mirrors the format of
    this index's `IMPLEMENTATION_subagent_clustering.md`).
  * [`docs/DEMO_PD_extension.md`](https://github.com/flg77/acc-podman-desktop/blob/main/docs/DEMO_PD_extension.md)
    — operator walkthrough.
