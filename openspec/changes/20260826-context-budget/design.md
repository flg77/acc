# 20260826-context-budget — design

Technical detail for Phase 1, plus the deployment-posture and
window-discovery reasoning that Phases 2–3 depend on.

---

## 1. Placement in the pipeline

The budgeter sits between retrieval and assembly, replacing the `"\n\n".join`
at `acc/cognitive_core.py:2098`:

```
_retrieve_episodes ──┐
_memory_notes ───────┤
replay_block ────────┼──► context_budget.pack(blocks, ceiling) ──► llm_user_content
task content ────────┘                    │
                                          └──► emit_stage("acc.pipeline.context_budget", …)
```

Constraints on the module, inherited from `acc/estimator.py`:

* **Pure.** No I/O, no `await`, no clock, no global state. `pack()` is a
  function of its arguments and must be callable from the dispatch hot path.
* **Total.** No input raises except the one designed to (`ContextOverflow`,
  §5). A malformed block is dropped and recorded, never propagated.
* **Order-stable.** Same inputs → byte-identical output. This is what makes the
  no-regression test possible and what keeps the PR-CA1 prefix cache honest.

The system prompt is **not** an input to the packer. PR-CA1 made it stable per
role (`acc/cognitive_core.py:1929`); the budgeter must not reintroduce
variability there or it destroys the prefix cache it depends on for headroom.
The system prompt enters the ceiling calculation only as a *measured constant*
(§3).

---

## 2. Data model

```python
@dataclass(frozen=True)
class Block:
    kind: str                      # "notes" | "episodes" | "thread" | "task"
    heading: str                   # rendered verbatim when >=1 item survives
    items: tuple[str, ...]         # ordered by keep-preference, best first
    priority: int                  # eviction order; lower survives longer
    divisible: bool                # False => all-or-nothing
    display_rank: int              # position in the assembled output

@dataclass(frozen=True)
class Drop:
    kind: str
    index: int                     # index within Block.items
    est_tokens: int
    reason: str                    # "ceiling" | "block_cap" | "empty"

@dataclass(frozen=True)
class BudgetResult:
    text: str
    est_tokens: int
    ceiling: int
    kept: dict[str, int]           # kind -> items kept
    drops: tuple[Drop, ...]
    degraded: bool                 # True iff drops is non-empty
    calibration: float             # estimator correction in force
```

### Two orders, deliberately distinct

Conflating them is the common implementation error, so they are separate
fields:

| kind | `priority` (evict last → first) | `display_rank` (top → bottom) |
|---|---|---|
| `task` | 0 — never evicted | 3 (last) |
| `thread` | 1 | 2 |
| `notes` | 2 | 0 (first) |
| `episodes` | 3 | 1 |

Rationale for the eviction order: losing the previous turn makes the current
turn *incoherent* — "yes, do that" with no antecedent is unanswerable. Losing
an episode makes the answer merely less informed. So thread outranks notes and
episodes even though it is rendered nearest the task.

Rationale for the display order: unchanged from `_compose_user_content`'s
existing docstring, which already argues it. The budgeter must not alter what
the model sees when nothing is dropped.

### Item preference within a divisible block

* `thread` — **newest first.** The most recent turns are the ones the current
  request is answering.
* `episodes` — **highest similarity first.** `_retrieve_episodes` already
  returns them in that order; the packer relies on it rather than re-sorting.
* `notes` — **as given.** They are curated, not retrieved; there is no score to
  order by and reordering would be arbitrary.

---

## 3. The ceiling

```
ceiling = window
        - reserve_output
        - measured_system_tokens
        - safety_margin(window, confidence)
```

* **`window`** — `ModelEntry.context_window`, or
  `ACC_CONTEXT_WINDOW_DEFAULT` (8192) when undeclared.
  `ACC_CONTEXT_BUDGET` overrides both when set; `0` disables the packer.
* **`reserve_output`** — `ModelEntry.max_output_tokens`, else
  `ACC_CONTEXT_RESERVE_OUTPUT`, else a posture default (§6). **A budgeter that
  fills the window leaves nothing to answer with**; this reserve is the
  difference between a working change and a subtly broken one.
* **`measured_system_tokens`** — counted once per role at load and memoised.
  PR-CA1's stability guarantee is what makes this a constant rather than an
  estimate, which is a real synergy: the prefix-cache discipline makes
  budgeting *exact* on its largest single term.
* **`safety_margin`** — `max(64, window * margin_pct)`. Starts at 15%,
  decays toward a 5% floor as estimator confidence rises (§4).

### Per-block soft caps

The global ceiling alone is insufficient. On a 128k DC model five 8k-char
episodes would consume ~10k tokens purely *because they fit* — and fitting is
not the goal, signal density is. Each block therefore also gets:

```
block_cap(kind) = min(ceiling * share(kind), absolute_cap(kind))
```

Shares are posture-derived (§6); absolute caps are constant. On edge the
shares bind; on DC the absolute caps bind. That single line is most of the
edge/DC difference in mechanism terms.

---

## 4. Token estimation without a tokenizer

Three options, and Phase 1 takes the third:

1. **Server-side exact** — vLLM `/tokenize`, Anthropic
   `/v1/messages/count_tokens`. Correct, but adds a network round-trip to
   every turn and does not exist on Ollama.
2. **Local tokenizer** — `transformers.AutoTokenizer` for the served model.
   Exact, but a heavy dependency to push onto an edge box, and it must track
   whatever the server actually loaded.
3. **Calibrated heuristic** — cheap, dependency-free, self-correcting.

### The heuristic

`chars / 4` is wrong for ACC's actual content, which is largely code, YAML and
command output. A class-weighted estimate:

```python
def _raw_estimate(text: str) -> int:
    # whitespace-delimited words, plus a per-class surcharge
    words = text.split()
    n = len(words)
    punct = sum(1 for c in text if c in _PUNCT_CLASS)
    non_ascii = sum(1 for c in text if ord(c) > 0x7F)
    return int(n * 1.3 + punct * 0.5 + non_ascii * 0.8) + 1
```

### The correction

`_call_llm` already receives `usage` at `acc/cognitive_core.py:2231` and keeps
only `total_tokens`. Phase 1 also reads `prompt_tokens` and folds the ratio
into a per-`(backend, model)` EMA:

```python
observed = usage["prompt_tokens"]
estimated = self._last_prompt_estimate          # what the packer computed
if estimated > 0 and observed > 0:
    ratio = observed / estimated
    factor = _clamp(_EMA_ALPHA * ratio + (1 - _EMA_ALPHA) * factor, 0.6, 2.0)
```

* `_EMA_ALPHA = 0.25` — converges in ~10 turns, stays responsive.
* The clamp is a safety rail: a single malformed `usage` block must not be able
  to drive the factor somewhere that overflows the window.
* The factor **resets on model change** — it is keyed on `(backend, model)`, so
  a role reassigned to a different model starts from 1.0 rather than inheriting
  a correction calibrated for a different tokenizer.
* Confidence = sample count. Below 5 samples the safety margin stays at 15%;
  at ≥20 it reaches the 5% floor.

This is the pleasing part of the design: the signal needed to calibrate is
already crossing the wire and being thrown away.

**Where the factor lives.** In-process on the `CognitiveCore` instance for
Phase 1 — losing it on restart costs ten turns of slightly conservative
budgeting, which does not justify a persistence dependency. Redis working
memory (`acc/config.py` `WorkingMemoryConfig`) is the natural home if it ever
does.

---

## 5. The packing algorithm

```
pack(blocks, ceiling, caps) -> BudgetResult

1.  task = blocks["task"]
    t_task = est(task)
    if t_task > ceiling:
        raise ContextOverflow(need=t_task, ceiling=ceiling)
    remaining = ceiling - t_task

2.  for block in sorted(blocks_except_task, key=lambda b: b.priority):
        budget = min(remaining, caps[block.kind])
        if not block.divisible:
            t = est(block.render())
            if t <= budget: keep whole; remaining -= t
            else:           drop whole; record Drop(reason="ceiling")
            continue
        spent = est(block.heading) if block.items else 0
        if spent > budget:
            drop the entire block including heading; continue
        for i, item in enumerate(block.items):          # already best-first
            t = est(item) + _JOIN_COST
            if spent + t <= budget:
                keep(i); spent += t
            else:
                record Drop(kind, i, t, "block_cap" if budget < remaining
                                                   else "ceiling")
        remaining -= spent

3.  assemble surviving blocks by display_rank, "\n\n".join
4.  return BudgetResult(...)
```

Four properties this shape guarantees:

* **The task is never truncated.** Silently trimming the operator's request is
  the one failure this change exists to prevent, so it is the one input the
  packer will not touch. `ContextOverflow` propagates as a visible task
  failure naming both numbers.
* **A block with a surviving item always carries its heading**, and a block
  with none carries neither. A dangling `RECENT_RELEVANT_EPISODES:` with
  nothing under it invites a small model to hallucinate the contents.
* **Eviction is greedy within a block, not across blocks.** A single very long
  episode is dropped and the next one considered, rather than the block being
  abandoned at the first miss — otherwise one 6k-char episode at rank 1 silently
  costs you four relevant ones.
* **No compression anywhere.** Phase 1 drops. It does not summarise, elide or
  ellipsise. Every dropped item is enumerated in `drops`.

### The stage event

```python
emit_stage("acc.pipeline.context_budget", {
    "ceiling": r.ceiling,
    "est_tokens": r.est_tokens,
    "window": window,
    "window_source": src,          # "declared" | "default" | "override"
    "kept": r.kept,                # {"episodes": 3, "thread": 4, "notes": 2}
    "dropped": len(r.drops),
    "dropped_by_kind": {...},
    "degraded": r.degraded,
    "calibration": r.calibration,
})
```

`degraded: true` is the operator-visible signal that this deployment is
running out of window — the thing that is completely invisible today.

---

## 6. Edge vs DC

The two postures differ in what is scarce, and therefore in what the budgeter
should optimise for.

| | **edge** | **rhoai / standalone (DC)** |
|---|---|---|
| Typical window | 4k–32k; often reduced further by `--max-model-len` to fit KV in VRAM | 128k–200k |
| Binding constraint | **KV cache VRAM, shared across concurrent agents** | model cost / quality per token |
| Overflow behaviour today | Ollama truncates silently; vLLM 400s | rarely reached |
| Distraction cost of a marginal chunk | **High** — a 3B model degrades measurably on irrelevant context | Low |
| Fallback on failure | none (lighthouse has no frontier model) | `acc/llm_failover.py` has somewhere to go |
| Prefix-cache value | very high — prefill FLOPs dominate on a weak GPU | high, but as cost not latency |
| Correct posture | **budget hard, drop early, keep the turn structured** | **cap for signal density, not for capacity** |

Concretely, Phase 1 posture defaults keyed on `deploy_mode`
(`acc/config.py:31`, `Literal["standalone", "rhoai", "edge"]`):

| knob | `edge` | `standalone` | `rhoai` |
|---|---|---|---|
| `reserve_output` | 512 | 1024 | 2048 |
| `safety_margin` start | 20% | 15% | 10% |
| share: thread | 30% | 35% | 35% |
| share: episodes | 15% | 25% | 25% |
| share: notes | 10% | 10% | 10% |
| absolute cap: episodes | 1 200 tok | 3 000 tok | 6 000 tok |
| overflow strictness | raise | raise | raise |

Three points worth stating explicitly because they are the ones easy to get
wrong:

1. **`deploy_mode` sets the posture; the model sets the ceiling. Never infer
   the window from the mode.** An edge box can serve a 128k model and a DC can
   serve a 4k one. These are independent axes and collapsing them produces a
   budgeter that is confidently wrong in exactly the deployments that matter.
2. **`ModelEntry.zone` is not this.** It is a trust / data-residency label for
   `ZonePolicyGate` (`acc/models.py:54`, `acc/llm_failover.py:106`).
   Overloading it with capacity would break the failover gate.
3. **On edge the window is a shared resource, not a per-agent one.** A vLLM
   server with a fixed `gpu_memory_utilization` has one KV pool; N agents each
   filling 32k causes preemption and recompute, so throughput collapses well
   before any single agent overflows. The honest edge ceiling is
   `min(model_window, kv_pool / concurrent_agents)` — deferred to Phase 6,
   where it joins up with `role.max_parallel_tasks` (A-019), but named here so
   the Phase 1 numbers are not mistaken for the final answer.

### Where YaRN lands

YaRN is a serving-side RoPE rescaling — `rope_scaling: {"rope_type": "yarn",
"factor": 4.0, "original_max_position_embeddings": 32768}` in the model config,
honoured by vLLM. It interpolates the low-frequency RoPE dimensions, leaves the
high-frequency ones (which carry local token detail) to extrapolate, and scales
attention logits to compensate for the entropy growth over longer sequences.

It reaches ACC as exactly one thing: **a larger number in
`ModelEntry.context_window`.** ACC should not implement, configure or detect it
beyond that.

The reason it is not the edge answer: a 4× window needs 4× the KV cache to
actually be used, on hardware selected for having none spare, and static YaRN
costs short-prompt accuracy — which is the shape of nearly every ACC turn once
`20260825-conversational-turn-continuity` converts long-horizon turns into
short structured ones. Budgeting a native 32k window well beats serving a
degraded 128k one.

---

## 7. Dynamic detection (Phases 2–3)

Yes, the window can largely be detected. The value must come from a strict
precedence ladder, because the four sources have different authority.

**Rung 1 — declared (`ModelEntry.context_window`). Always wins.**
An operator may deliberately budget below what the server offers — to leave KV
headroom for co-tenant agents, or because quality degrades past some length.
A probe must never override that. It reconciles and reports.

**Rung 2 — probed, per backend:**

| backend | probe | field |
|---|---|---|
| `vllm`, `openai_compat` (vLLM-served) | `GET /v1/models` | `data[].max_model_len` |
| `ollama` | `POST /api/show {"model": …}` | `model_info["<arch>.context_length"]` **and** `parameters.num_ctx` |
| `anthropic` | none — static table keyed on model-id prefix | plus `/v1/messages/count_tokens` for exact input counts |
| `llama_stack` | model list metadata | `context_length` when present |

The Ollama row carries the trap that motivates this whole change: the
architecture's `context_length` is what the *model* supports, while Ollama
serves at `num_ctx`, which defaults low unless the Modelfile or the request
sets it. The effective window is `min(arch_context_length, num_ctx)` — and
since Phase 1 makes ACC send `options.num_ctx` itself, ACC becomes the party
that decides it. The probe's job is then to confirm the model can *carry* what
ACC asks for.

Home for the probe: `acc/preflight.py`, behind the existing
`Context.probe_endpoints` gate (`acc/preflight.py:104`) and registered with the
existing `register()` decorator, alongside `check_endpoints`. No new machinery.

**Rung 3 — learned from failure.** OpenAI-compatible servers reject an
over-length prompt with a 400 whose message states the maximum. Extract `N`,
cache it per `(base_url, model)` with a TTL, retry **once** at the discovered
ceiling. This is the only rung that works on a backend with no discovery
endpoint at all, and the only one that catches a server reconfigured after
boot. Bounded to one retry so a genuinely oversized task fails rather than
loops.

**Rung 4 — floor.** Nothing known → `ACC_CONTEXT_WINDOW_DEFAULT` (8192) and a
WARN naming the model.

> **The direction of the guess matters.** A too-small assumption costs recall —
> some episodes are dropped that would have fitted. A too-large assumption
> costs *integrity* — the server truncates and the tracelog records a prompt
> the model never saw. Those are not symmetric, so when the window is unknown
> ACC guesses low, deliberately.

### Reconciliation reporting

`acc-cli doctor --probe` gains one line per configured model:

```
context-window  ollama-qwen25-14b     declared 32768  served 4096   MISMATCH
context-window  lighthouse-llama32-3b declared  8192  served 8192   ok
context-window  claude-sonnet         declared     0  table 200000  undeclared
```

A `MISMATCH` is a WARN, not a failure: the operator may have meant it. An
undeclared window on a model a role is actually mapped to is also a WARN,
because it means that role is running on the 8192 floor by accident rather than
by decision.
