# Self-reflective memory

Agents accumulate episodes (every task → a LanceDB row), but raw episodes
are noisy. **Reflection** periodically distils an agent's recent episodes
into compact, durable **memory notes** ("PDFs over 10MB reliably exhaust
the ingester") that sharpen future retrieval — the system improving its
own memory over time.

Designed to be **hot-path-safe**: all the expensive work runs off the
task path, and the prompt-build read is O(1).

## Dual-layer persistence

- **Durable** — a *separate* small `memory_notes` LanceDB table
  (`id, agent_id, role_label, ts, summary, source_count, confidence,
  embedding`). Separate from `episodes` so vector search over notes stays
  fast (few curated rows), and reads never scan the large episodes table.
- **Hot read** — a Redis per-role hot-cache
  (`acc:{cid}:memory_notes:{role}`, TTL'd, top-N summaries). Read in O(1)
  on the prompt-build path; miss → skip (no LanceDB hit there).

## The reflection loop (out-of-band)

`Agent._reflection_loop` (a heartbeat-style coroutine) runs every
`ACC_REFLECTION_INTERVAL_S` seconds — **default 0 = off** (it makes extra
LLM calls), mirroring the Cat-B `reflection_interval_s` setpoint. Each
pass (`_run_reflection_once`), gated on the role's `memory_reflection`
flag + a live CognitiveCore:

1. reads the agent's recent-episode ring (`CognitiveCore.recent_episodes()`
   — fed by `_persist_episode`, no vector scan);
2. `acc.memory_reflection.consolidate(...)` clusters related episodes
   (greedy cosine; **MEMORY_NOTE episodes excluded** — no notes-of-notes)
   and LLM-summarises each cluster into a `MemoryNote`;
3. `persist_notes(...)` writes them to the `memory_notes` table;
4. `write_hot_cache(...)` pushes the top-N to Redis.

It is **best-effort** — a summary/embed/IO failure is logged and skipped,
never raised into the loop, and it never blocks the task loop.

## Hot-path read (PR-MEM3)

In `CognitiveCore.process_task`, after the episode RAG and gated by the
same `memory_retrieval` flag, `_read_memory_notes()` does an O(1) Redis
read and the notes are prepended to the **LLM user message**:

```
MEMORY_NOTES (durable lessons …)        ← high-level, from reflection
RECENT_RELEVANT_EPISODES (…)            ← recent specifics, from RAG
<the task>
```

Both blocks live in the user message, so the role **system prompt stays a
cacheable prefix** (PR-CA1). A Redis miss or no-Redis is silent.

## Enabling

1. Set `memory_reflection: true` on the role(s) you want to self-reflect
   (`roles/<role>/role.yaml`); default is off.
2. Set `ACC_REFLECTION_INTERVAL_S` (e.g. `3600`) on those agents — via
   `AgentSpec.extra_env` in `collective.yaml`, or the Cat-B
   `reflection_interval_s` setpoint mapped into the env at deploy.
3. Redis must be configured (the notes hot-cache + episode store rely on
   it); LanceDB holds the durable `memory_notes` table.

## Tests

```
pytest tests/test_memory_reflection.py \
       tests/test_reflection_loop.py \
       tests/test_memory_notes_hotpath.py -v
```
