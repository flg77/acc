# 20260923-lessons-that-travel — proposal

## Why

ACC keeps a learning two ways: a reflected memory note (`acc/memory_reflection.py`,
per role, tiered, quorum-published, `20260823-attributed-memory`) and a signed
role / skill / rule change (`acc/role_store.py`, `acc/self_author/`,
`acc/rule_proposals.py`). It has no way to hand a learning from one agent to
another **as a message**. `SIG_KNOWLEDGE_SHARE` is defined (`acc/signals.py:96`),
permitted for workers (`acc/nats_permissions.yaml:41`), rendered by the TUI and
drawn as "Pattern B — knowledge-share fan-in" in `docs/SUBAGENT_COMMUNICATION.md`;
`grep -rln knowledge_share acc/` finds no producer and no consumer. Inside one
collective, in one afternoon, what the analyst learned never reaches the reviewer:
notes are read per role from the hot cache, and the only cross-role path is the hub
tier, after two people approve.

The Prime Agent comparison (vault `20-backlog/prime-agent-gaps`, PA-03, the round's
P0) put it plainly: the storage is fine; the missing piece is a message. Prime
Agent's harness moves a lesson between agents as an `agent_message`; what it lacks
and ACC already has is the provenance on the note — `scope`, `ceiling`,
`source_requesters`, `dissent`. Putting the note on the wire with those fields intact
changes where a lesson can be *read*, not who may read it.

## What changes

### Phase 1 (this ship — v0.22.0)

* **`acc/lessons.py`** — the typed envelope. `Lesson` (pydantic, `extra="ignore"`):
  `kind: note|role_patch|skill_patch|rule`, `scope`, `ceiling`,
  `source_requesters`, `trigger`, `summary`, `evidence{source_episode_ids,
  dissent, tracelog_refs}`, `expected_outcome`, `patch`, `target_agent_id`,
  `schema_rev`. `parse_lesson()` validates or drops (never partially reads).
  `PeerLessonRing`: bounded, TTL'd, **consumed on read**, filtered by the
  information rule (`visible_to`: same scope, not above the reader's ceiling; an
  unlabelled ceiling reads as CRITICAL — the note rule).
* **Producer** — `Agent._run_reflection_once` lifts every persisted note onto
  `acc.{cid}.knowledge.{tag}` (`tag` = the role's `domain_id`, else `general`)
  via `_publish_lessons`, keeps a 7-day Redis copy
  (`acc:{cid}:lesson:{id}`, index `acc:{cid}:lessons`) and journals
  `direction=published` in the agent's `lessons-<agent_id>` tracelog session
  (`KIND_LESSON`).
* **Consumer** — `Agent._subscribe_knowledge_share` on `acc.{cid}.knowledge.*`;
  `_accept_lesson` drops invalid / own / addressed-elsewhere / receptor-mismatched
  (`_receptor_allows`, PARACRINE — the existing membrane model) and offers the rest
  to `CognitiveCore.receive_lesson`. Journalled `received`.
* **Prompt** — `CognitiveCore._process_task_body` drains the ring under the task's
  `scope_key` + `ceiling_of` with the role's `memory_note_bandwidth`, renders a
  `PEER_LESSONS` block **after** `MEMORY_NOTES` and before the episodes (role label,
  never agent id; dissent attached), through `context_budget.standard_blocks`
  as a fifth block with the **lowest** keep priority (hearsay is evicted before the
  reader's own notes). Empty ring ⇒ byte-identical prompt.
* **Outcome join** — `CognitiveResult.lessons_used`; `TASK_COMPLETE` carries
  `lessons_used` when non-empty; `acc:{cid}:lesson:{id}:used` indexes the task ids.
* **`acc-cli lessons list|show|trace|send`** — `trace` = the lesson and every task
  whose prompt rendered it; `send --to <agent>` publishes one operator-authored
  lesson addressed to a single agent on the shared subject (the S2 relay smoke
  test). No new bus subjects.
* **Kill switch** `ACC_PEER_LESSONS=0` (publish and render both off; registered in
  `acc/configschema.py`).

### Phases 2–6 (same PR, same day — the operator asked to continue)

* **2 — the ledger** (PA-04) — `acc/refinements.py`: one append-only row per
  durable change (`note`, `adopt`, `publish`, `hub_promote`, `role_patch`, `rule`,
  `forget`, `outcome`, `rollback`) in `refinements.jsonl` under the tracelog dir
  (`ACC_REFINEMENTS_PATH`), mirrored 30 days in Redis `acc:{cid}:refinements`.
  Writers: `Agent._record_notes` (reflection), `RoleStore.apply_update` (**old and
  new values of the fields that moved**, approver, the `lesson_id` / `proposal_id`
  that asked), `rule_proposals.approve_proposal`, `memory_forget.forget_person`
  (`--apply` only), `assistant_proposal._dispatch_publish`. `acc-cli refine
  list|show|trace|rollback [--apply]`; `memory_reflection.revoke_note` pulls a note
  from every cache and the table; a `role_patch` rollback queues a `role_update`
  proposal restoring the old values through oversight. Rules, erasures,
  publications are refused with the manual path named.
* **3 — `role_patch` → proposal** — `Agent._classify_lesson` returns `role_patch`
  without touching the ring; `_propose_from_lesson` queues ONE `role_update`
  proposal (`params.lesson_id`, rationale = the lesson) via
  `_queue_assistant_proposal`; `_dispatch_role_update` forwards `lesson_id` /
  `rollback_of` on the `ROLE_UPDATE`. `acc-cli lessons send --kind role_patch
  --patch-role R --set FIELD=VALUE`.
* **4 — durable adoption** — `RoleDefinitionConfig.accept_peer_lessons`,
  **on by default since D-028** (operator decision 2026-09-24; it shipped as
  `False` on this branch first). When on, an accepted note-kind lesson also goes
  through `memory_reflection.publish_note` into the reader role's shared tier for
  the lesson's scope (probation applies) and writes an `adopt` ledger row. A
  role opts out with `accept_peer_lessons: false` in its signed definition. The
  default lives once, `acc.config.ACCEPT_PEER_LESSONS_DEFAULT`, which the agent's
  fallback also reads. `publish_note`'s docstring names its second caller and
  why it is still a person's decision (the operator's default, or a signed role
  change), not the agent's. What durable-by-default does **not** change: the
  adopted note carries the lesson's ceiling and scope, and the read path filters
  on both — it decides *whether* an accepted lesson is kept, not who may read it.
* **5 — the outcome** — `Agent._record_lesson_outcomes` on every `TASK_COMPLETE`
  that carries `lessons_used`: the used-index (Phase 1), one observation per lesson
  in `acc:{cid}:lesson:{id}:outcomes`, a confidence move on the lesson's Redis copy,
  an `outcome` ledger row. `lesson_outcome_delta` is **objective only**: blocked
  −0.10, BAD / NEEDS_REVISE −0.10, GOOD +0.10, PARTIAL / unreviewed 0. `lessons
  trace` shows them.
* **6 — the inbox** (PA-02) — `acc/agent_messages.py`: `AgentMessage` on
  `acc.{cid}.agent.{agent_id}.inbox` (`SIG_AGENT_MESSAGE`, SYNAPTIC).
  `Agent._deliver_message`: `steer` → `CognitiveCore.receive_steer` →
  `OPERATOR_STEERING` block right before the task (`context_budget.KIND_STEER`,
  priority 1, evicted last); `follow_up` → `AgentMessage.follow_up_task` handed to
  the agent's own `_handle_task` (`_local_task_handler`; no bus round-trip) with
  the sender's attribution copied verbatim; `auto` picks by `_tasks_in_flight`.
  Receipts in Redis (`acc:{cid}:message:{id}`, `acc:{cid}:agent:{id}:messages`) and
  the `messages-<agent_id>` journal (`KIND_AGENT_MESSAGE`).
  `CognitiveResult.steer_used` → `TASK_COMPLETE.steer_used`. NKey matrix: workers
  subscribe `acc.*.agent.*.inbox`; arbiter and `tui` publish; `comms_provisioning`
  base subscribe. `acc-cli msg send|tail|show`.

### Phase 7 (same PR) — the arbiter countersigns

The gap found wiring Phase 3, closed: `Agent._countersign_role_update`. An
unsigned `ROLE_UPDATE` — the approved proposal's `{trigger: assistant_proposal,
role, fields, lesson_id}` or `acc-cli role infuse`'s full `role_definition` with
`signature: ""` — is nobody's to apply and the arbiter's to sign. The arbiter
resolves the target role (Redis role key of a roster member of that role, else
the roles directory through the same `RoleLoader` every agent boots from), applies
the fields, bumps the version (`0.3.0 → 0.3.1`, `7 → 8`; no fields, no bump),
validates the merged `RoleDefinitionConfig`, signs the canonical
`{approver_id, role_definition}` message with `security.arbiter_signing_key`
(the key every RoleStore already verifies, the same one `ROLE_ASSIGN` uses) and
re-publishes with `approver_id = <arbiter>`, `countersigned: true`,
`countersigned_for = <who decided>` and the `trigger` / `proposal_id` /
`lesson_id` / `rollback_of` carried through. `_handle_role_update` gained two
rules: an unsigned update is countersigned (arbiter) or dropped (everyone else),
never applied; a signed update that names a `role` is applied only by agents of
that role — before, a role-scoped update reached every agent.

Also in this slice: **the critic join** (Phase 5's open half) —
`PlanExecutor._notify_critic_verdict` hands a reviewer step's verdict to the
lessons its `depends_on` steps used (`_Step.lessons_used` from their
`TASK_COMPLETE`), through `on_critic_verdict`, which the arbiter binds to
`Agent._record_critic_verdict` (`source: critic`, the one outcome an agent
cannot give itself). And **the harness fingerprint** (PA-04) —
`CognitiveCore.harness_fingerprint`: sha256 over role name + version, the
system prompt, the notes, the lessons and steering rendered; on the episode's
`payload_json`, on `TASK_COMPLETE`, on every outcome observation.

### Phase 8 (same PR) — who may dispatch an approved proposal

Dispatching a proposal is a control-plane publish, and the identity that
claims the approval is not always the identity the NKey matrix lets make it.
Nobody asked: the publish went out and the server refused it, silently, on the
one deployment shape (NKeys enforced) where governance is strictest. Measured
against `nats_permissions.load_permission_matrix()`, **every** kind was
affected — `role_update` / `route` refused for workers, and `collective.reconcile`
(spawn) plus `assistant.proposal` (infuse / role_gap / publish) granted to no
identity at all, arbiter included.

* **The matrix was wrong, not just narrow.** `acc.*.assistant.*` is now in the
  worker baseline (static matrix and `comms_provisioning`): a proposal
  *announcement* carries no mutation — it is what the Compliance screen renders
  a pending row from — so it belongs with `alert` / `knowledge` / `eval`, and
  without it an NKey deployment could not announce a pending proposal at all.
  `acc.*.collective.reconcile` and `acc.*.assistant.*` are now on the arbiter,
  which is what makes the rule below safe: **the arbiter may dispatch every
  kind**, so an approval a worker declines always has a taker.
* **The question is now askable.** `assistant_proposal._DISPATCH_SUBJECTS_OF`
  maps each kind to **every** subject its dispatch may publish, and
  `dispatch_subjects()` renders them. All of them count: `infuse` announces the
  outcome *and* publishes the continuation `TASK_ASSIGN`, and an identity
  allowed only the first installs the pack and drops the continuation — half a
  dispatch. `nats_permissions.may_publish(identity, subject)` answers it; an
  identity the matrix does not name (a packaged role, an instance) reads as a
  **worker**, the conservative choice.
* **Enforced at the claim, not after it.** `Agent._may_dispatch_proposal(kind)`
  gates both dispatch paths. On the approval path an agent that may not publish
  the mutation does **not** take the exactly-once claim — claiming and then
  failing would consume the approval and drop the mutation, which is worse than
  the server refusal it replaces. It leaves it unclaimed; the arbiter receives
  the same `OVERSIGHT_DECISION` and takes it. On the AUTO path there is no
  approval to leave, so an auto-execute this identity cannot publish is
  **queued** instead of dropped: the mutation stays possible, a human is asked,
  and whoever claims the approval will be an identity that may publish it.
* **Only where the server enforces it.** The guard is inert unless
  `security.nkey.enabled` is true. With the matrix off the publish would
  succeed, and refusing it would be ACC inventing a restriction the deployment
  never asked for — so standalone (lighthouse included) behaves exactly as
  before. The guard mirrors the server; it is not a second gate, and a guard
  that cannot answer lets the publish through.
* **The contract test that would have caught it.**
  `tests/test_proposal_dispatch_authority.py` (34) asserts per-kind *publish*
  authority: every kind declares its subjects, the arbiter may publish all of
  them, workers still may not publish a control subject, every worker may
  announce. It also pins why this hid: `subject_covered()` passes when any
  identity matches on publish **or subscribe**, and the arbiter subscribes
  `acc.>` — it answered "can anything reach this subject", never "may anyone
  send it".

### Phase 9 (same PR) — a `can_route` role asks, the arbiter routes

The last identity-versus-subject mismatch, and the one Phase 8's guard could
not see because it is not a proposal: an orchestrator re-dispatches by
publishing a directed `TASK_ASSIGN` itself, and `acc.*.task.assign` is
arbiter-only. On an enforced deployment **every orchestrator hand-off was
refused by the server**.

The two ways out were making `can_route` a proposal, or moving the publish.
Making it a proposal is wrong: routing is a work hand-off, not a mutation of
the collective, `[PROPOSE_ROUTE]` already exists for the case that wants a
human, and putting an approval in front of every hand-off would make the
orchestrator useless. So the **decision stays the orchestrator's and only the
privileged publish moves**:

* `SIG_ROUTE_REQUEST` on `acc.{cid}.route.request` (SYNAPTIC), which workers
  may publish and the arbiter subscribes — a worker still may not publish
  `task.assign`.
* `Agent._route_task()` publishes the `TASK_ASSIGN` itself where the identity
  may, and asks otherwise. Inert unless `security.nkey.enabled`, so the
  standalone path is byte-for-byte what it was.
* `build_routed_task()` is now the one place that knows what a routed task
  looks like — same `task_id` (the operator's reply correlation resolves on
  it), the chosen role, no pinned agent, and the `routed_by` stamp — so the
  orchestrator and the arbiter cannot build it differently.
* The arbiter re-checks everything the worker was trusted with
  (`_route_request_refusal`): the requesting role's **signed** definition
  carries `can_route` (the authorisation the matrix cannot read, and the
  reason relaying is not a widening), the roster agrees the asking agent holds
  that role, and the task is routable and not already routed — the hop cap of
  one, re-applied rather than trusted from the ask.

**What this does not prove is who sent the message.** The payload names its own
sender and nothing but the matrix ties that to a connection, so a collective
member could claim a role it does not hold. The bound is narrow — a request can
only re-dispatch a task already in flight, once, and to a role — and closing it
properly needs an authenticated sender on the envelope rather than a field in
it (the typed-envelope item, vault PA-09). Stated here rather than implied,
because a relay whose verification is "the sender said so" is the failure mode
worth naming.

### Phase 10 (same PR) — an authenticated sender (PA-09 Phase 1)

The open item Phase 9 left, closed for the path that needed it. Phases 8 and 9
both moved a worker-initiated control publish behind the arbiter — a worker
asks, the arbiter acts — and both then believed the `from_agent` / `from_role`
fields *in the payload*. Nothing tied either to the connection that carried the
message, so a collective member could ask in another role's name and the matrix
restriction the relay exists to respect would mean nothing.

* **`acc/wire.py`** — the sender signs a canonical form of the payload (compact,
  key-sorted JSON of everything except the proof field) with **the very key it
  authenticates its NATS connection with**, and the receiver verifies against
  the public half from the key set `scripts/acc-nkeys generate` already writes.
  No new key material, no new provisioning, no new dependency:
  `acc/nkeys.py` has implemented the NKey format on `cryptography` since
  proposal 013 — it only ever encoded, so this adds `decode_seed`,
  `decode_public` and `public_key_of_seed`.
* **Applied to `ROUTE_REQUEST`** — `Agent._sign_ask()` on the way out,
  `_sender_refusal()` first in the arbiter's refusal ladder, before anything
  the payload says about itself is weighed.
* **The key identifies the signer, not a name it writes down.** The proof
  carries the signer's **public key**; the verifier finds that key in the key
  set to learn *which* identity signed, and verifies with the **key set's**
  copy. The `identity` field is a log label and nothing authorises on it.
  (Proof schema **rev 2**. Rev 1 asked the signer to name itself and looked
  that name up — see the smoke below for what that cost.)
* **The binding, stated exactly.** A signature proves the message came from a
  holder of that bus identity and that nothing in it changed in transit. Where
  the claimed role **is itself an NKey identity** the signer must be it, which
  is what stops an `analyst` asking in the `arbiter`'s name. A packaged role
  the matrix does not name (`orchestrator`, `assistant`) presents a worker
  identity, so its identity cannot prove its role — for those the signed role
  definition and the roster remain the bound, and the code says so rather than
  implying the gap is shut.
* **What the lighthouse smoke returned, and why rev 2 exists.** With the key
  set distributed, the first signed ask was refused:
  `route: refused a request from smoke-orch (orchestrator) → 'analyst':
  sender not proven: no public key for identity 'orchestrator'`. The
  orchestrator is a packaged role: `ACC_NKEY_ROLE` is unset, so it signs with
  `seed-coding_agent` while `_nkey_identity()` labels it by its **agent role**
  — a name the key set, indexed by the eight NKey identities, does not
  contain. Rev 1 therefore refused every legitimate ask from precisely the
  roles Phase 9 exists for, the moment a deployment turned verification on.
  Every unit test had passed because each one handed its worker an explicit
  `nkey_role`; the smoke was the first thing to run the real shape. The
  regression test now reproduces that refusal verbatim.
* **Rollout, not a flag day.** Signing ships first; a receiver with no key set
  readable accepts an unverified ask and logs that it did, which is exactly
  today's behaviour. Distributing `public_keys.json` turns verification on, and
  from then on an ask that cannot be verified is refused. Inert unless
  `security.nkey.enabled`, like Phases 8 and 9.

## Impact

* **Affected code:** `acc/lessons.py` (new), `acc/cli/lessons_cmd.py` (new),
  `acc/agent.py` (`_publish_lessons`, `_subscribe_knowledge_share`,
  `_accept_lesson`, `_store_lesson`, `_index_lessons_used`; `TASK_COMPLETE` body;
  `run()` gather), `acc/cognitive_core.py` (ring, `receive_lesson`,
  `_compose_user_content` / `_packed_user_content` `peer_lessons`,
  `CognitiveResult.lessons_used`), `acc/context_budget.py` (`KIND_PEER_LESSONS`,
  fifth block), `acc/signals.py` (three Redis keys), `acc/tracelog.py`
  (`KIND_LESSON`, `log_lesson`), `acc/configschema.py`, `acc/cli/__init__.py`.
* **Phases 2–6 add:** `acc/refinements.py`, `acc/agent_messages.py`, `acc/cli/refine_cmd.py`,
  `acc/cli/msg_cmd.py` (new); `acc/agent.py` (`_classify_lesson`, `_propose_from_lesson`,
  `_adopt_lesson`, `_record_notes`, `_record_lesson_outcomes`, `lesson_outcome_delta`,
  `_deliver_message`, `_subscribe_agent_inbox`, `_write_receipt`, `_tasks_in_flight`,
  `_local_task_handler`), `acc/cognitive_core.py` (`SteerRing`, `receive_steer`, `steer_used`),
  `acc/context_budget.py` (`KIND_STEER`, sixth block), `acc/role_store.py`, `acc/rule_proposals.py`,
  `acc/memory_forget.py`, `acc/memory_reflection.py` (`revoke_note`), `acc/assistant_proposal.py`,
  `acc/config.py` (`accept_peer_lessons`), `acc/signals.py`, `acc/tracelog.py`,
  `acc/nats_permissions.yaml`, `acc/comms_provisioning.py`.
* **Phase 8 adds:** `acc/nats_permissions.py` (`may_publish`), `acc/nats_permissions.yaml`
  (worker `acc.*.assistant.*`; arbiter `acc.*.collective.reconcile` + `acc.*.assistant.*`),
  `acc/comms_provisioning.py` (same announce grant for derived roles),
  `acc/assistant_proposal.py` (`_DISPATCH_SUBJECTS_OF`, `dispatch_subjects`),
  `acc/agent.py` (`_nkey_identity`, `_may_dispatch_proposal`, the claim guard, the AUTO
  partition), `tests/test_proposal_dispatch_authority.py` (34).
* **Phase 9 adds:** `acc/signals.py` (`SIG_ROUTE_REQUEST`, `subject_route_request`),
  `acc/nats_permissions.yaml` + `acc/comms_provisioning.py` (worker publish/subscribe,
  arbiter publish), `acc/agent.py` (`build_routed_task`, `_route_task`,
  `_may_publish_task_assign`, `_route_request_refusal`, `_handle_route_request`,
  `_subscribe_route_requests`), `tests/test_route_through_arbiter.py` (22).
* **Phase 10 adds:** `acc/wire.py` (new — `SenderProof`, `canonical_bytes`,
  `sign_payload`, `verify_payload`, `identity_of_key`, `load_public_keys`),
  `acc/nkeys.py` (the decoders), `acc/config.py` + `acc/configschema.py`
  (`security.nkey.public_keys_path`), `acc/agent.py` (`_sign_ask`,
  `_sender_refusal`, `_sender_seed`, `_sender_public_keys`),
  `tests/test_wire.py` (40) and 10 more in
  `tests/test_route_through_arbiter.py`.
* **Wire compatibility, Phase 10:** the proof is one added field on
  `ROUTE_REQUEST`, a subject introduced in this same PR, so nothing released
  reads it. `PROOF_SCHEMA_REV` is 2 and a receiver refuses a revision it does
  not implement rather than guessing — rev 1 never left this branch.
* **New env knobs:** `ACC_PEER_LESSONS` (default `1`); `ACC_REFINEMENTS_PATH` (default
  `<tracelog dir>/refinements.jsonl`); `ACC_NKEY_PUBLIC_KEYS_PATH` (default: beside the seed).
* **Tests:** `tests/test_lessons.py` — 23 tests: envelope, ring (once, ceiling,
  scope, TTL, maxlen, kinds), block order + eviction + byte-identity, core render /
  consume / kill switch / `memory_retrieval` off, agent accept filters + journal,
  publish path + Redis + journal, used-index, and **S2** (agent A reflects → agent
  B's next prompt carries it, once, role not id).
* **Tests, phases 2–6:** `tests/test_refinements.py` (10), `tests/test_agent_messages.py` (13),
  11 more in `tests/test_lessons.py` (role_patch proposal, adoption, outcome deltas).
* **Backward compatibility, phases 2–6:** ONE new subject (`acc.{cid}.agent.{agent_id}.inbox`)
  and a matching NKey matrix change (workers subscribe; arbiter + tui publish) — the operator's
  `nats.conf` renderer reads the same YAML; `TASK_COMPLETE` gains optional `steer_used`;
  `ROLE_UPDATE` gains optional `lesson_id` / `rollback_of`; the context budget gains a block
  that renders nothing when empty. Phase 1: no new subjects, no NKey change. `TASK_COMPLETE`
  gains an optional key. Prompts are byte-identical when no lesson was heard or
  `ACC_PEER_LESSONS=0`. Older agents ignore the new payload on a subject they
  never consumed.

## What stays open after Phases 1–10

* **PA-09's other halves are untouched and deliberately so.** The typed
  envelope covers the *proof*, not the wire generally: `TASK_ASSIGN` and
  friends are still an untyped dict validated by `.get()`, there is still no
  `NATSBackend.request()`, no durable `task.*` stream (which PA-06 needs), and
  no generated `docs/WIRE.md`. Those are a refactor of every publish path in
  `acc/agent.py` and belong in their own change; this one bought the property
  the priority list asked for first.
* **`AGENT_MESSAGE` is the next thing to sign.** Its follow-up task runs at
  the *sender's* attribution and ceiling, so the sender claim is load-bearing
  there too. The matrix already limits inbox publishers to the arbiter and the
  operator surface, which is why it is second rather than first.
* Only `kind=note` renders; `role_patch` proposes; `skill_patch` and `rule` are
  accepted on the wire and held (`kind-not-rendered`), never acted on.
* The critic join covers `depends_on` edges only. A reviewer step that does not
  declare its dependency reviews nothing, by construction.
* A countersigned update for a role with no roster member and no roles-dir
  entry is refused (logged); the proposal's approval row stays, the change does
  not happen. `acc-cli refine trace` shows the proposal and no `role_patch`.
* The reader's receptor filter is on `domain_tag`; a role with no `domain_id`
  publishes universal ligands, so an untagged collective hears everything a peer
  reflects, capped by bandwidth and the ring. Whether that is too chatty is a
  soak-run question (PA-08 S1).
* `lessons trace` sees only tasks that completed after the lesson; nothing joins a
  lesson to a *reply's quality*.
* `acc-cli lessons send` needs NATS + Redis reachable from the CLI host.
