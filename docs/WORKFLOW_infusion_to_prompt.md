# ACC Workflow — Role Infusion → Prompt → Response

**Audience:** Operators driving the ACC TUI on a podman-stack
deployment (standalone mode) or against a K8s operator (rhoai mode).
This document tracks the end-to-end happy-path from selecting a role
in the Ecosystem screen to seeing the agent's reply in the Prompt
transcript. Each step names the responsible component, the wire
signal it emits/consumes, and the failure modes that surface
operator-visible symptoms.

The workflow lands after commits `48b0414…088661e` and the agent-side
payload-decode fix in `58d09c3`. Earlier deployments may not exhibit
the behaviour described here verbatim — pin to ≥ `58d09c3` for fidelity.

---

## 1. Pick a role (Ecosystem)

```
1 Soma   2 Nucleus   3 Compliance   4 Comms   5 Performance   6 Ecosystem   7 Prompt   8 Configuration
                                                              ────────────
ROLE LIBRARY                                                  ROLE DETAIL
  Filter roles […]                                              role.md (narrative)
  ●  coding_agent     software_engineering  analytical  8
     coding_agent_architect …                                   role.yaml (read-only · press Edit to modify)
```

| Component | Behaviour |
|-----------|-----------|
| `EcosystemScreen.on_mount` | Force-renders detail for the first role and arms buttons (post-Commit-1; no click required). |
| `RowHighlighted` (cursor key) | **Preview**: loads `role.md` + `role.yaml` into the right pane without touching `_selected_role` (post-Commit-4). |
| Space key | Same as `RowHighlighted` — for filter-input-focused state where arrows move the filter caret instead of the table cursor. |
| Enter / mouse click | **Commit**: pins `_selected_role`, arms `Schedule infusion`, paints the `●` selection marker in column 0, acquires the role's advisory file-lock. |
| Filter Input.Submitted | Refocuses the table on the top-filtered row and auto-previews it. |
| `e` / `s` keys | Toggle role.yaml read-only/edit; save (atomic write through `acc.tui.role_writeback`). |

**Operator chord summary** (also rendered above the Footer):
`↑/↓ navigate · Space preview · Enter commit · e edit role.yaml · s save · i schedule infusion · / filter · Tab switch to Agentset`

**Failure modes:**

* Editor empty after picking a role → first-row render skipped because
  `_load_roles` found nothing under `ACC_ROLES_ROOT`. Check the env
  var matches the container's bind-mount (`/app/roles` by default).
* `Schedule infusion` stays disabled → `_selected_role == ""`. Check
  `_extract_role_name(event.row_key)` got a non-empty key.

---

## 2. Schedule infusion → Nucleus

Clicking `Schedule infusion → Nucleus` (or pressing `i`) posts a
`RolePreloadMessage(role_name)` to the App. The App resolves the
`InfuseScreen` instance, calls `preload_from_role(role_name)`, and
switches the screen.

`InfuseScreen.preload_from_role` loads the role definition via
`acc.role_loader.RoleLoader` and populates every editable field
(Select, Inputs, TextAreas) — Persona, Version, Task types, Allowed
actions, Domain ID, Domain receptors, Seed context, Cat-B overrides.

Post-Commit-5: when the operator scrolls in Ecosystem but doesn't
press Enter, the cursor-row is auto-committed at Schedule-time so the
role that's *visually* selected always gets forwarded.

---

## 3. Apply (Nucleus → arbiter + collective.yaml)

```
ACC Role Infusion — Nucleus
Collective: sol-01   Role: coding_agent
Cluster id: backend
Purpose:    Implement a Python webscraper for IBM stock prices
[Apply ↵]   [Clear]   [History ▼]
Awaiting reconcile… (role applied; agent spawn requested)
```

`InfuseScreen.action_apply` does two things in order (PR-D, commit
`83883fd`):

1. **Publishes `ROLE_UPDATE`** on `acc.<cid>.role_update`. Active
   agents matching the role hot-reload their `RoleDefinitionConfig`
   via `RoleStore.apply_update` (Ed25519-signed in production).
2. **Calls `_spawn_via_collective(role, cluster_id, purpose)`** which:
   * `acc.collective.upsert_agent_entry(path, role, …)` — adds or
     bumps the matching entry in `./collective.yaml`. Idempotent.
   * Touches `./.acc-apply.request` next to the spec.
   * Sets `_apply_started_ts = time.time()` and
     `_pending_apply = (role, cluster_id)`.

**Container spawn — current gap.** The `.acc-apply.request` marker is
intended for a host-side watcher (systemd path-unit or
`inotifywait` loop) that runs `./acc-deploy.sh apply <spec>` on
change. The default standalone install does NOT install the watcher
yet — Apply succeeds, the marker file appears, but no new container
materialises. PR-G (worker pool, deferred) replaces this entirely by
pre-spawning dormant workers that accept a `ROLE_ASSIGN` signal at
runtime, eliminating both the marker-file dance and the per-Apply
container churn.

`InfuseScreen.apply_snapshot` watches incoming HEARTBEATs for a NEW
agent matching `(role, cluster_id)` whose `registered_ts >
_apply_started_ts`. When found, status flips to
`✓ Agent <id> registered`. Today this never fires for `coding_agent`
because no spawn actually happens — the arbiter ends up handling the
operator's prompt.

---

## 4. Send a prompt (Prompt screen)

```
7 Prompt
Target role: coding_agent   Agent id (optional): […]
Clusters: 0
[Transcript pane — chronological list of operator / agent / trace entries]
[Prompt textarea]   [Send ▶]
```

`PromptScreen.action_send` validates the prompt, creates a
`TUIPromptChannel(observer, collective_id)`, and calls
`channel.send(prompt, target_role, target_agent_id, on_progress)`.

`TUIPromptChannel.send` (in `acc/channels/tui.py`):

1. `task_id = uuid.uuid4().hex` (a 32-char hex string).
2. `future = asyncio.Future()`; `observer.register_task_listener(task_id, future)`.
3. If `on_progress` is provided:
   `observer.register_task_progress_listener(task_id, callback)`.
4. Build payload (`SIG_TASK_ASSIGN`, `task_id`, `task_description`,
   `content`, `target_role`, optional `target_agent_id`, …).
5. `observer.publish(subject_task_assign(cid), payload)`.

The `subject_task_assign("sol-01") = "acc.sol-01.task.assign"`.

The publish path encodes the dict as UTF-8 JSON bytes, wraps with
`msgpack.packb(use_bin_type=True)`, and forwards to `nats.publish`.

The prompt screen then `await channel.receive(task_id, timeout=180.0)`
— a blocking await on the registered future with a 3-minute timeout.

---

## 5. Agent receives, processes, replies

Every agent of the `target_role` (and matching `target_agent_id` if
set) receives the message on its `acc.<cid>.task.assign` subscription.
The signaling backend (`acc.backends.signaling_nats.NATSBackend`)
calls `_dispatch`, which `msgpack.unpackb`s the wire bytes and hands
the inner JSON-bytes payload to `_handle_task`.

`_handle_task` (in `acc/agent.py`):

1. `data = json.loads(_payload_bytes(msg))` — the
   `_payload_bytes` helper accepts both raw-bytes (the backend's
   contract) and legacy NATS-msg-object shapes (post-`58d09c3` fix —
   pre-fix, every payload silently decoded to `{}` and the
   operator's task_id never round-tripped).
2. `target_aid` filter — drop if `target_agent_id` is set and doesn't
   match `self.agent_id`.
3. Build `_publish_progress` callback that emits `TASK_PROGRESS` on
   `acc.<cid>.task.progress` at each step boundary (CognitiveCore
   step + each capability dispatch outcome).
4. `result = await self._cognitive_core.process_task(task_payload=data, role=self._active_role, progress_callback=…)`.
5. Parse `[SKILL:…]` / `[MCP:…]` markers from `result.output` and
   dispatch via `acc.capability_dispatch.dispatch_invocations`.
6. Publish `TASK_COMPLETE` on `acc.<cid>.task.complete` with the
   *same* `task_id`, `episode_id`, `blocked`, `block_reason`,
   `latency_ms`, `output` (truncated to 500 chars for the bus), and
   the `invocations` summary list.

### CognitiveCore pipeline (`acc/cognitive_core.py:process_task`)

```
0. Pre-reasoning gate (Cat-B setpoints)         (blocked-path early-exit)
1. Cat-A pre-LLM guardrails                     (prompt injection, OWASP)
2. Compose system_prompt + user_content
3. await self._call_llm(system_prompt, user_content)
   → response_dict, latency_ms, token_count
   output_text = response.get("content") or response.get("text") or …
     (commit-5 shape tolerance — vLLM/anthropic/llama_stack/ollama
      return `{"text": …}` not `{"content": …}`)
4. Cat-B post-reasoning governance (deviation scoring)
5. Cat-A post-LLM guardrails (HIPAA redaction etc.)
6. Drift scoring (role centroid + domain centroid)
7. Delegation parse (ACC-9 — `[DELEGATE:cid:reason]` markers)
8. Persist episode to LanceDB (with embedding for future retrieval)
9. Return ProcessResult(output, blocked, latency_ms, episode_id, stress, …)
```

Each step calls the operator-supplied `_emit(step, label, confidence)`
which the progress_callback wraps into a `TASK_PROGRESS` signal. The
operator sees these in the Prompt's middle-pane task-progress line
and the invocation waterfall (PR-F).

---

## 6. TUI receives TASK_COMPLETE → renders reply

`NATSObserver._route_task_complete` (in `acc/tui/client.py`):

1. Increment `snapshot.icl_episode_count` if not blocked.
2. Clear per-agent task-progress fields on the snapshot.
3. **Resolve the channel future**: `future = self._task_listeners.pop(task_id, None); if future and not future.done(): future.set_result(data)`.
4. Drop the matching progress-listener.
5. Fan out cluster-listeners for the Prompt cluster panel.
6. Fold every `data["invocations"]` entry into
   `snapshot.capability_stats` (Performance screen, Capability
   Invocations table).
7. Diagnostic log: `task_complete: agent=X task_id=Y blocked=Z; registered_listeners=[…]` — added in commit `58d09c3` so future correlation bugs are obvious from `/app/logs/acc-tui.log`.

Channel-side, `channel.receive(task_id, timeout=180.0)` returns the
resolved payload as a `PromptResponse`. The Prompt screen then:

1. Appends the operator's prompt to the transcript (`role="operator"`).
2. For each `invocations[]` entry, appends a `role="trace"` line
   with the skill/MCP name + ok/✗ marker.
3. Appends the agent's reply (`role="agent"`, `text=reply.output`).
4. Updates status to `Reply received ok — agent=<id> latency=<ms>`.

---

## 7. End-to-end signal cookbook

```
┌─ Operator → TUI                                                       ┐
│  PromptScreen.action_send                                              │
│    → TUIPromptChannel.send                                             │
│      → register_task_listener(task_id, future)                         │
│      → publish acc.<cid>.task.assign  {task_id, content, role…}        │
│                                                                        │
│                                                  Agent A (matching role)
│                                                    NATSBackend._dispatch
│                                                      → _handle_task    │
│                                                        → cognitive_core.process_task
│                                                          ├─ TASK_PROGRESS …
│                                                          ├─ Cat-A/B gates
│                                                          ├─ LLM call (vLLM / openai_compat / …)
│                                                          ├─ post-LLM gates
│                                                          ├─ drift scoring
│                                                          └─ persist episode
│                                                        → dispatch_invocations
│                                                        → publish TASK_COMPLETE  {task_id, output, invocations, …}
│                                                                        │
│  NATSObserver._route_task_complete                                     │
│    → _task_listeners.pop(task_id).set_result(payload)                  │
│  TUIPromptChannel.receive returns                                      │
│    → PromptScreen.render: transcript += operator | trace[] | agent     │
└────────────────────────────────────────────────────────────────────────┘
```

---

## 8. Where to look when something is wrong

| Symptom | First check |
|---------|-------------|
| Prompt window stays empty for 180s, then "cancelled" | `grep "task_complete:" /app/logs/acc-tui.log` — if it shows `task_id=''`, the agent's payload-decode is broken (pre-Commit-7). If it shows `registered_listeners=[…]` with no match, the channel/agent task_ids diverged. |
| Comm pane empty | `grep "routed counts" /app/logs/acc-tui.log` — if all signal types are 0, the NATS subscription is dead. If HEARTBEAT > 0 but TASK_COMPLETE = 0, no task ever completed. |
| Agent answers but reply is nonsense / generic | The agent received `data = {}` (legacy bug). After Commit-7 you should see real `content` in the agent's `task_payload`. |
| Active task / Active plan panes never populate | TASK_PROGRESS signals not propagating; check `routed counts` for the TASK_PROGRESS counter. |
| `Save to /app/.env: Permission denied` | The acc-tui container needs `userns_mode: keep-id:uid=1001,gid=0` in compose; restart the stack. |
| Schedule infusion forwards the wrong role | Pre-Commit-5 bug; ensure deployed code includes the cursor-row fallback in `_handle_schedule_infusion`. |

---

## 9. Related documents

* `docs/DECISIONS.md` — running log of planning decisions for the
  upcoming feature work (PR-G worker pool, RAG default-on, operating
  modes, golden-prompt suite, compliance-pane redesign).
* `docs/ACCv3.md` — the architectural overview (Soma / Nucleus / …
  bio-metaphor + collective design).
* `docs/IMPLEMENTATION_SPEC_v0.2.0.md` — formal spec, signal subjects,
  Cat-A/B/C governance contracts.
