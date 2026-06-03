# OpenSpec — Assistant as Agent of Agents (gatekeeper)

| Field | Value |
|---|---|
| Change ID | `20260530-role-proposal-assistant-agent-of-agents` |
| Status | Phases 1, 2a, 2b, 3a, 3b, 4, 5, 6 LANDED (v0.3.24 → v0.3.34) |
| Companion | `20260530-acc-self-improvement-policy-gradient` |
| Sequencing contract | `Notes/.../20260530-aoa-and-self-improve — integration & sequencing.md` |
| Notes mirror | `Notes/Development/AgenticCellCorpus/ACC Openspec/20260530-role-proposal-assistant-agent-of-agents — OpenSpec (proposed).md` |

## Problem statement

The v0.3.x ACC Assistant was a Phase-1 *concierge* — a guide pinned to
a single TUI dropdown slot. It could explain, but could not *act*.
Every operator-facing failure mode shipped in the lead-up to this
proposal (infusion-preload defer, apply-workspace glob bug, prompt-
liveness, watcher hardening) shared a root cause: the operator was
doing work the system should do.

This proposal turns the Assistant into a **gatekeeper / agent-of-
agents** that owns operator intent and translates it into the right
ACC primitives (spawn / infuse / route / open sub-collective) while
staying bound by the same Cat-A/B/C governance every other agent
obeys.

Four axis decisions set the shape:

1. **Gate model:** Mandatory gate with a Knative-style dormant-watcher
   escape valve. Heartbeat-always-alive.
2. **Autonomy ceiling:** Mode-gated via PR-L. Default propose-then-
   approve; `AUTO` acts within Cat-A/B/C envelopes.
3. **Agentset shape:** Hybrid hub + on-demand sub-collectives.
4. **User identity:** Single-operator today, multi-user-ready
   architecture.

## Phase summary (all LANDED)

| Phase | Version | Concrete output |
|---|---|---|
| 1 | v0.3.24 | Default-target Assistant; activator decision tree; `dormant`/`dormant_at_ts` on StressIndicators; heartbeat invariant; `Ctrl+Z`/`/sleep`/`/wake`; reward-harness import seam. |
| 2a | v0.3.26 | `acc/assistant_proposal.py`: `AssistantProposal`, marker parser, `decide_dispatch`, `dispatch_approved_proposal`. |
| 2b | v0.3.27 | Cognitive-core classification (queued/executed/plan lists on `CognitiveResult`); agent I/O (`_handle_assistant_proposals`, `_maybe_dispatch_assistant_proposal`); Compliance approve bridge. |
| 3a | v0.3.29 | `CollectiveSpec.managed_sub_collectives`; `SubCollectiveSpec`; `acc/sub_collective.py` registry + routing + lifecycle wire format. |
| 3b | v0.3.31 | `acc-deploy.sh resume`/`hibernate`/`lifecycle-watcher`; `scripts/acc-lifecycle-watcher.sh`; cognitive-core seed-context injection. |
| 4  | v0.3.33 | A2A AgentCard gatekeeper extension under `acc/` vendor key (proposal kinds, dormancy-aware, policyEnabled). |
| 5  | v0.3.34 | `acc/operator_identity.py` — `resolve_operator_id(override, source)`; env-driven; Phase-5b session source stubbed for safe future flip. |
| 6  | v0.3.30 | RoleDefinitionConfig policy block (`policy_enabled`, `policy_pinned`, cadence, drift cap); harness threading; Assistant role.yaml v2.1.0; "💤 policy frozen" hint in AUTO. |

## What stays open (not blocking the interleave)

- **AoA-P3c (idle scanner).** Periodic loop publishing `hibernate` for
  `stale_cids(now)`. Lands when lighthouse traces show idle-eviction
  decisions are stable.
- **AoA-P4 (full A2A handover).** Replace ACC-9 with the A2A client
  for cross-collective delegation. Depends on
  `20260527-a2a-agent-interop` Phase 3+.
- **AoA-P5 follow-up.** TUI auth proposal lands; flip
  `ACC_OPERATOR_ID_SOURCE=session`; per-user dormancy + memory namespacing.

## Spawned follow-ups (per the standing rule)

- `20260531-assistant-catchup-window` — bounded catch-up window.
- `20260531-assistant-dormancy-banner-ux` — persistent banner +
  reminder.
- `20260531-assistant-wake-triggers-tunable` — operator-tunable wake
  triggers via `role.yaml`.

## Sibling references

- Sibling proposal: `20260530-acc-self-improvement-policy-gradient`
  (per-role policy gradient).
- Integration & sequencing: see the Obsidian note for the full
  capability map and the recommended interleave
  (`AoA-P1 → SIP-P1 → AoA-P2 → SIP-P2 → AoA-P3 → AoA-P6 → P3b → SIP-P3
  → P4 → P5`).
