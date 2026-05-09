# Index — ACC Autoresearcher Documentation Set

Landing page for the documents that came out of the autoresearcher
demo work (PRs #41-#46). Read in this order if you're new to the
topic.

## For implementers / reviewers

1. **[`docs/examples/acc-autoresearcher-example.md`](examples/acc-autoresearcher-example.md)**
   *(internal — gitignored)*
   Original six-PR design plan, revision 2. Why each PR exists,
   what's in scope, what's deferred.

2. **[`AUTORESEARCHER_implementation.md`](AUTORESEARCHER_implementation.md)**
   Reference for what shipped. PR-by-PR module breakdown,
   wire-protocol cheat sheet, file map, test matrix.

## For role authors / operators

3. **[`SUBAGENT_COMMUNICATION.md`](SUBAGENT_COMMUNICATION.md)**
   Patterns A-E (static decomposition, knowledge-share fan-in,
   scratchpad rendezvous, autocrine self-feedback, cancel
   propagation). The autoresearcher uses every pattern.

4. **[`../examples/acc_autoresearcher/README.md`](../examples/acc_autoresearcher/README.md)**
   Concrete six-step plan + iteration loop + citation verification.
   Runnable on `main`.

## For demos / leadership

5. **[`DEMO_TUI_autoresearcher.md`](DEMO_TUI_autoresearcher.md)**
   Phase-by-phase TUI walkthrough (~10 minutes). What to type,
   what to look at, what proves the feature works, talk track for
   each phase, recovery scripts when something goes wrong.

## For planning the next iteration

6. **[`ROADMAP_subagent_clustering.md`](ROADMAP_subagent_clustering.md)**
   Short-, medium-, long-term improvements. The autoresearcher
   work surfaced new follow-ups documented in the "Autoresearcher
   follow-ups" section.

---

## Cross-reference matrix

| If you're trying to … | Read first |
|---|---|
| Understand the iteration loop wire | AUTORESEARCHER_implementation § E1 |
| Pick a research persona to extend | AUTORESEARCHER_implementation § E4 |
| Run the showcase demo | examples/acc_autoresearcher/README.md |
| Walk a stakeholder through it | DEMO_TUI_autoresearcher |
| File a follow-up PR | ROADMAP § Autoresearcher follow-ups |
| Understand why X is the way it is | docs/examples/acc-autoresearcher-example.md (private) |

## PR cross-reference

| PR | Branch | What it does |
|---|---|---|
| [#41](https://github.com/flg77/acc/pull/41) | `feat/eval-revise-iteration-loop` | Iteration loop + cost cap + prompt patches + A-020/A-021 |
| [#42](https://github.com/flg77/acc/pull/42) | `feat/mcp-research-tools` | Three real MCP servers (browser-harness, Brave, fetch with paywall) |
| [#43](https://github.com/flg77/acc/pull/43) | `feat/research-stub-skills` | Six stub skill manifests (governance + audit anchors) |
| [#44](https://github.com/flg77/acc/pull/44) | `feat/research-personas` | Six research personas with role.md + role.yaml + prompts + rubrics |
| [#45](https://github.com/flg77/acc/pull/45) | `feat/example-acc-autoresearcher` | Runnable scenario + citation verifier |
| [#46](https://github.com/flg77/acc/pull/46) | `docs/acc-autoresearcher` | This documentation set |

Stack order: #41, #42, #43 in parallel → #44 on #43 → #45 on all
four → #46 closes.

---

## Companion to Example No. 1

The autoresearcher demo (Example No. 2) shares scaffolding
conventions with the coding-split-skills demo
([Example No. 1](INDEX_subagent_clustering.md)) but introduces
three runtime additions on top of the cluster fan-out foundation
PRs #26-#30 established:

| Capability | Example No. 1 (coding-split) | Example No. 2 (autoresearcher) |
|---|---|---|
| Cluster fan-out per PLAN step | ✓ | ✓ |
| Single-shot research | ✓ | — |
| Iteration loop (NEEDS_REVISE) | — | ✓ (E1) |
| Cost cap (max_run_tokens) | — | ✓ (E1) |
| Self-modifying personas | — | ✓ (E1, opt-in) |
| Stub skills (governance only) | ✓ | ✓ |
| Real MCP servers | echo only | browser-harness + Brave + fetch |
| Citation re-verification | — | ✓ (E5) |

Operators familiar with Example No. 1 will recognise the
`run.sh` / `verify.sh` / `clean.sh` / `.env.example` shape
verbatim. The new bits are wire-shape additions, not workflow
re-invention.

---

## See also — Podman Desktop extension

The autoresearcher demo can be driven entirely from inside
Podman Desktop via the
**[acc-podman-desktop](https://github.com/flg77/acc-podman-desktop)**
extension.  Operators living in PD get:

* **Stack panel** — bring the AUTORESEARCHER profile up with one
  checkbox; live container status mirrors `acc-deploy.sh status`.
* **Examples panel** — topic-slug input → `--topic <slug>`;
  streaming stdout/stderr in the panel; post-run reads
  `runs/<topic>-<date>/.verification.json` and renders the
  citation report inline.
* **Cluster topology panel** — the same six research personas
  that the TUI's prompt pane (PR #29) renders, surfaced as a
  webview against the same NATS subscription.
* **Compliance + Performance dashboards** — OWASP-LLM violation
  log + Cat-A trigger summary + per-MCP `capability_stats` +
  the cost-cap progress bar driven by E1's `tokens_used` /
  `max_run_tokens` fields documented in
  `AUTORESEARCHER_implementation.md § E1`.

Same wire format (msgpack-of-JSON over NATS).  See
[`docs/EXTENSION_implementation.md`](https://github.com/flg77/acc-podman-desktop/blob/main/docs/EXTENSION_implementation.md)
for the panel-side module breakdown and
[`docs/DEMO_PD_extension.md`](https://github.com/flg77/acc-podman-desktop/blob/main/docs/DEMO_PD_extension.md)
for the operator walkthrough.
