# 20260826-context-budget — proposal

## Why

ACC assembles every prompt without knowing how large the context window is.

`grep -rn "context_window\|max_model_len\|rope_scaling"` over `acc/`,
`models.yaml.example` and `deploy/` returns **nothing**. `ModelEntry`
(`acc/models.py:44`) carries `backend`, `model`, `base_url`, `api_key_env`,
`label`, `notes`, `zone` — no capacity field of any kind. The window is not
under-used; it is not represented.

Assembly is correspondingly unbounded. `_compose_user_content`
(`acc/cognitive_core.py:2067`, joined at `:2099`) concatenates four blocks with `"\n\n".join` and
no ceiling anywhere in the path:

```
MEMORY_NOTES  →  RECENT_RELEVANT_EPISODES  →  EARLIER_TURNS  →  task
```

* `_retrieve_episodes` returns `top_k=5` nearest neighbours
  (`acc/cognitive_core.py:2265`) with **no similarity floor** — the fifth
  neighbour is included because it is fifth, not because it is relevant.
* `EARLIER_TURNS` is bounded only by `ACC_THREAD_TURNS` / `ACC_THREAD_CHARS`,
  which `acc/thread_continuity.py:34` labels a stopgap in its own module
  docstring: *"ACC has no context compaction anywhere, so an uncapped thread is
  a context-overflow bug aimed squarely at the smallest deployments — the ones
  with no frontier model to fall back on."*
* Token count is learned **after** the call:
  `token_count = response.get("usage", {}).get("total_tokens", 0)`
  (`acc/cognitive_core.py:2231`). The Cat-B `token_budget` setpoint
  (`acc/cognitive_core.py:2132`) throttles an agent that has already overspent.
  Neither shapes what goes in.

### The integrity argument

`20260825-conversational-turn-continuity` spent a release establishing
*model-visible means logged* (REQ-RPL-003). The converse is currently false:
**logged does not mean model-visible.** When an assembled prompt exceeds the
served window the outcome is backend-dependent and ungoverned by ACC —
OpenAI-compatible servers reject with a 400, Ollama silently truncates to
`num_ctx`. On the Ollama path the tracelog records a prompt the model never
fully received, with no event marking the difference.

That path is not hypothetical. `acc/backends/llm_ollama.py:59` POSTs to
`/api/chat` with **no `options` block**, so `num_ctx` is never set and Ollama
serves at its own default (~4096) irrespective of the model's real capacity.
Every Ollama deployment in ACC today runs a ~4k effective window while
`models.yaml.example:38` advertises `qwen2.5:14b`, and the truncation is
invisible on both sides.

Worse, the truncation lands on the wrong end. `_compose_user_content` places
the operator's task **last** — correct for attention and for the PR-CA1 prefix
cache, and catastrophic under a server-side head/tail trim, because the bytes
at risk are the request itself.

### Why now, and why not compaction

The pain is concentrated exactly where ACC has no fallback: the lighthouse edge
posture, small-context models on constrained hardware, no frontier model to
fail over to. Compaction is the reflex answer and it is the wrong first move —
summarising a small model's history *with that same small model* compounds its
errors and adds a second inference call to the hot path. Before compressing
anything, ACC needs to (a) know the number, (b) decide deliberately what enters
and what is dropped, and (c) record the drop. Dropping visibly beats compressing
invisibly. Compaction becomes worth building once a budgeter exists to tell it
how many tokens it must recover.

## What changes

### Phase 1 (this ship — v0.11.0)

* **`ModelEntry.context_window: int = 0`** — declared capacity in tokens; `0`
  means undeclared. Plus `ModelEntry.max_output_tokens: int = 0` for the
  completion reserve. Both optional; every existing `models.yaml` stays valid.
* **`acc/context_budget.py`** — a pure, synchronous packer. Input: the candidate
  blocks plus a resolved ceiling. Output: assembled text, tokens used, and an
  explicit record of every item dropped. No I/O, no `await`, safe on the
  dispatch hot path (same discipline as `acc/estimator.py`).
* **Self-calibrating token estimate** — a char-class heuristic corrected by an
  EMA over `usage.prompt_tokens`, already returned at
  `acc/cognitive_core.py:2231` and currently discarded. No tokenizer dependency
  on edge boxes; converges within a handful of turns per model.
* **Wiring** — `_compose_user_content` delegates to the budgeter. Display order
  is unchanged; eviction order is new and separate.
* **Ollama `num_ctx`** — the backend sends `options.num_ctx` derived from the
  resolved window, closing the silent-truncation path.
* **`acc.pipeline.context_budget` stage event** — ceiling, tokens used,
  per-block kept/dropped counts, and `degraded: bool`. A drop nobody can see is
  the bug this change exists to remove.
* **Overflow fails closed** — if the operator's task alone exceeds the ceiling,
  ACC raises rather than trimming the request.
* **`deploy_mode`-derived posture defaults** — `edge` / `standalone` / `rhoai`
  set block shares, safety margin and strictness. The *ceiling* still comes from
  the model, never from the mode.

### Phases 2–N (deferred)

* **Phase 2 — window discovery.** A `preflight.py` check (the `register()` /
  `probe_endpoints` machinery at `acc/preflight.py:104` already exists) that
  dials the backend and reconciles the declared window against the served one:
  vLLM / `openai_compat` via `GET /v1/models` → `max_model_len`; Ollama via
  `POST /api/show` → `model_info["<arch>.context_length"]` and `num_ctx`;
  Anthropic via a static table plus `/v1/messages/count_tokens`. Reports a
  mismatch; never silently overrides the operator's declaration.
* **Phase 3 — learned ceiling.** Parse the `maximum context length is N tokens`
  clause out of a 400, cache it per `(base_url, model)`, retry once at the
  discovered ceiling.
* **Phase 4 — retrieval quality.** Similarity floor, near-duplicate suppression
  and chunk-level episode retrieval. Independent of the budgeter and higher
  value per line on small models; split out only to keep Phase 1 shippable.
* **Phase 5 — structured compaction.** Extraction into fixed slots (goal,
  decisions taken, open questions, artifact handles) rather than free-form
  summary. Schema-validated, bounded output, degrades to "slot empty".
* **Phase 6 — concurrency-aware ceiling.** On a shared vLLM the KV cache is a
  server-wide resource; the per-agent ceiling should reflect
  `role.max_parallel_tasks` (Cat-A A-019), not the model alone.

## Impact

* **Affected code:** `acc/models.py` (2 fields), `acc/context_budget.py` (new),
  `acc/cognitive_core.py` (`_compose_user_content`, `_call_llm` calibration
  hook, one stage event), `acc/backends/llm_ollama.py` (`options.num_ctx`),
  `acc/config.py` (posture knobs), `models.yaml.example` (annotate entries).
* **New env knobs:** `ACC_CONTEXT_WINDOW_DEFAULT` (fallback when undeclared,
  default `8192`), `ACC_CONTEXT_RESERVE_OUTPUT`, `ACC_CONTEXT_SAFETY_MARGIN`,
  `ACC_CONTEXT_BUDGET` (hard override / kill switch — `0` disables the packer
  and restores byte-identical pre-change assembly).
* **Tests:** ~26. Packer unit tests (fit, evict-in-order, divisible eviction,
  task-overflow raises, display-vs-eviction order), estimator calibration
  (convergence, clamp, reset on model change), posture defaults per
  `deploy_mode`, stage-event shape, Ollama `num_ctx` wiring, and a
  no-regression test asserting an under-budget prompt is byte-identical to
  today's output.
* **Backward compatibility:** total. An undeclared window plus a prompt that
  already fits produces the identical string. `ACC_CONTEXT_BUDGET=0` is the
  escape hatch.
* **One pre-existing bug picked up in passing:** `_entry_to_dict`
  (`acc/models.py:375`) serialises only
  `("model", "base_url", "api_key_env", "label", "notes")` — **`zone` is
  missing**, so every `upsert_model()` / `delete_model()` / TUI-driven save
  silently strips declared zones and, once none remain, `ZonePolicyGate`
  (`acc/llm_failover.py:126`) reverts to permitting any hop. The new capacity
  fields would land in the identical hole, so `zone` is fixed in the same
  change with its own regression test.

## What stays open after Phase 1

* The window is **declared, not verified**, until Phase 2. A wrong declaration
  is still a wrong ceiling — it is merely now a *visible* one.
* Anthropic's window is a static table; there is no discovery endpoint for it.
* The estimator is calibrated, not exact. The safety margin is what pays for
  that, and it costs recall on small windows.
* **YaRN is deliberately out of scope, and on edge it is arguably the wrong
  lever.** Extending a 32k model to 128k multiplies the KV cache needed to
  actually use the length by 4× on hardware chosen for its lack of headroom,
  and static YaRN degrades short-prompt accuracy — the shape most ACC turns
  have. The budgeter is the *alternative* to YaRN on edge, not its complement.
  What ACC should own is the `context_window` field; whether a site serves it
  with `rope_scaling` is a serving decision that reaches ACC as a number.
