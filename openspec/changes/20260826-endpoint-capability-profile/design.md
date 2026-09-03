# 20260826-endpoint-capability-profile — design

Technical detail for Phase 1, plus the four judgment calls the existing
`acc/preflight.py` contract forces.

---

## 1. Two layers, because a profile is not a `Result`

`preflight.Result` is five flat strings — `name`, `severity`, `summary`,
`detail`, `subject` (`acc/preflight.py:60-88`). That is right for a health
report and wrong for a capability profile, which is structured data with
consumers (the context budgeter wants a boolean `tokenize_available`, not a
sentence).

So the work splits in two, which also happens to be exactly what preflight's
own docstring asks for — *"the checks are a registry of plain callables and
`run` returns data, not text"*:

```
acc/endpoint_profile.py          preflight.check_endpoint_capabilities
┌──────────────────────┐         ┌────────────────────────────────┐
│ probe_endpoint(entry)│ ──────► │ render profile → Result rows   │ ──► doctor
│   → EndpointProfile  │    │    └────────────────────────────────┘
└──────────────────────┘    │
                            └──► direct consumers (context budgeter, RP-03)
```

`Result`, `Severity`, `Context`, `run()`, `report()` and `registry()` are
**unchanged**. The new check is one more `register()`ed callable.

```python
@dataclass(frozen=True)
class Capability:
    """One probed property. `value is None` means unknown, always."""
    value: Any | None
    source: str            # "declared" | "probed" | "metrics" | "table" | "inferred"
    note: str = ""         # why unknown, or what the inference rests on

@dataclass(frozen=True)
class EndpointProfile:
    model_id: str
    base_url: str
    backend: str
    served_model: Capability          # str
    served_window: Capability         # int
    native_window: Capability         # int
    rope_extension: Capability        # {"type": "yarn", "factor": 4.0, ...}
    prefix_cache: Capability          # {"hit_rate": 0.0, "queries": 1284}
    tokenize: Capability              # bool
    embeddings: Capability            # bool
    kv_cache: Capability              # {"dtype": "fp8", "usage_perc": 0.31}
    probed_at: float
    errors: tuple[str, ...] = ()      # transport failures, never credentials
```

Every field is a `Capability`, never a bare value, so *"we asked and it said
8192"* and *"we could not ask"* are different states at the type level rather
than by convention. `None` is the only representation of unknown; there are no
sentinel integers.

---

## 2. Rung 1 — the declarative probes

No inference. Target: about a second per endpoint, one HTTP round trip each,
all failures caught.

| probe | backends | yields | on failure |
|---|---|---|---|
| `GET {base}/models` | vllm, openai_compat | `served_model`, `served_window` ← `max_model_len` | `unknown` |
| `POST {base}/api/show` | ollama | `native_window` ← `model_info["<arch>.context_length"]`; `rope_extension` ← `model_info["<arch>.rope.scaling.{type,factor,original_context_length}"]`; served `num_ctx` ← `parameters` | `unknown` |
| `POST {base}/tokenize` with a 3-token payload | vllm | `tokenize` = True/False | False on 404, `unknown` on transport error |
| `POST {base}/embeddings` with a short input | vllm, openai_compat | `embeddings` = True/False | as above |
| `GET {root}/metrics` | vllm | `prefix_cache`, `kv_cache` | `unknown` |
| static table | anthropic | `native_window`, `tokenize` = True (`count_tokens`) | n/a |

`native_window` on vLLM has no probe. It is filled from a small built-in table
keyed on a model-id substring (`Llama-3.2-3B` → 131072, `Qwen2.5-14B` → 32768,
…) with `source="table"`, and left `unknown` on a miss. `rope_extension` is then
**inferred**, not read:

```python
if served_window > native_window:
    rope_extension = Capability(
        {"active": True, "type": "unknown", "ratio": served/native},
        source="inferred",
        note="served window exceeds native length; mechanism not exposed by this backend",
    )
```

That is the honest ceiling of what vLLM's OpenAI-compatible surface supports.
It cannot distinguish YaRN from linear position interpolation, and the `note`
says so in the report rather than in a comment nobody reads.

### 2.1 Fixtures before parsers -- done, and it paid

Captured 2026-08-26 from the live lighthouse vLLM (0.11.2+rhai5) into
`tests/fixtures/endpoint/`; see its `PROVENANCE.md`. Three assumptions in the
table above were wrong, and every one would have shipped as a bug:

**`/v1/embeddings` returns HTTP 200 with an error envelope.** On a chat-only
model vLLM answers `{"error":{"message":"The model does not support Embeddings
API","code":400}}` under a **200**. The `on failure` column above said "as
above" -- i.e. status-driven -- which would have reported embeddings as
*available* and declared the local SentenceTransformer redundant. Detection now
runs through `_error_of()` on the body. An unrouted path (`/v1/nonexistent`)
does return a real 404, so the two cases stay distinguishable.

**Prefix caching is a label, not a behaviour.** `vllm:cache_config_info` carries
`enable_prefix_caching="True"`, so *configured on* is Rung 1 and certain. §3's
report line assumed a hit rate was the only evidence available. It also yields
`cache_dtype`, `gpu_memory_utilization`, `block_size` and `num_gpu_blocks`, from
which the KV pool follows: 6032 x 16 = 96,512 tokens, ~11.8 concurrent
full-window sequences at 8192.

**`/tokenize` also returns `max_model_len`**, giving the window a second,
independent source -- now the fallback when a gateway's ModelCard omits the
vLLM extension.

The example report in §3 is therefore wrong in its most quotable line: the real
hit rate is **93.1%** (29,040 / 31,185), not 0.00. PR-CA1 works, measured.

**Two families, one name.** `vllm:external_prefix_cache_*` counts cross-instance
KV-connector sharing and reads 0.0 here. Reading it instead of the plain family
reports 0% on a cache hitting 93% -- the precise false alarm this check exists
to avoid raising. Pinned by a test.

### 2.2 Scaling is bidirectional, and reduction is the common case

lighthouse serves **8192 of a 131072-token model** -- 6%, a KV-memory decision.
The `rope_extension` sketch above only fired on `served > native`, which would
have called that unremarkable. `_window_scaling` now reports `extended` /
`reduced` / `native`, and still never names YaRN in the extended case
(REQ-ROP-003): served-exceeds-native proves *some* rescaling is configured and
nothing distinguishes YaRN from linear interpolation from here.

---

## 3. Rendering — and why there is no fifth severity

`Severity` is `BROKEN | DEGRADED | DRIFTED | OK`, and only `BROKEN` sets the
exit code (`acc/preflight.py:117`). Adding `UNKNOWN` would touch the exit-code
rule, `worst()`, `report()` and three renderers (CLI, TUI, web GUI) for a
condition that is **not a fault**: an endpoint without `/tokenize` is normal,
and a capability nobody probed is not a health problem.

So `unknown` lives in the data and renders at `Severity.OK`:

```
endpoint-capability  http://lighthouse:8022/v1                        vllm
  served model     RedHatAI/Llama-3.2-3B-Instruct-FP8         ok       probed
  window           served 8192  ·  declared 8192               ok       probed
  rope extension   not exposed by this backend                 unknown
  prefix cache     hit-rate 0.00 over 1,284 queries            DEGRADED cold, disabled, or unstable prefix
  tokenize         /tokenize present                           ok       exact counting available
  embeddings       /v1/embeddings absent                       ok       local fallback warranted
```

Three rendering rules, all deliberate:

* **`unknown` reads as clearly as `ok`.** It is a column value, not an absence.
* **A WARN names the ambiguity, never a cause.** A zero hit-rate on a cold
  server is not a misconfiguration; the line lists all three candidates because
  Phase 1 genuinely cannot choose between them.
* **Every line ends in an implication for ACC**, not a raw fact. *"local
  fallback warranted"* is what the operator does with `/v1/embeddings absent`.

The one row that can exceed `OK` is a **declared-vs-served window mismatch**,
which is `DRIFTED` — declared state and running state disagree, which is
precisely what `DRIFTED` was defined for (`acc/preflight.py` module docstring).
It is never `BROKEN`: an operator may deliberately declare a window below what
is served, to leave KV headroom for co-tenant agents. The probe reconciles and
reports; it never overrides.

---

## 4. Authenticated probes — a deliberate widening

`acc/preflight.py`'s module docstring states: *"no check ever reads a secret
**value** — only whether a name is present."* Rung 1 needs to widen that,
because a keyed gateway (`backend: openai_compat` with `api_key_env`) answers
401 to an unauthenticated `GET /v1/models`, and MaaS is exactly that shape.

The widening and its bounds:

* A probe sends `Authorization: Bearer <key>` when `api_key_env` names a
  variable that is present, constructed the same way as
  `acc/backends/llm_openai_compat.py:302-308`.
* **No probe output may contain a credential** — not `summary`, not `detail`,
  not `errors`, not a log line. Enforced by a test that plants a sentinel value
  in the env and asserts it appears nowhere in the rendered report or the JSON.
* An **absent** env var yields `unknown` with `note="api key env <NAME> not
  set"` — never an error, and never an unauthenticated retry that would produce
  a misleading 401.
* A 401 with a key present is `DEGRADED` and reported as a credential problem,
  which is genuinely useful: it is the same class of finding `check_key_names`
  already makes, one layer later.

The distinction being preserved is *reading a secret to report it* (still
forbidden) versus *using a configured credential to make the call the operator
asked for with `--probe`* (permitted, and already what every backend does).

---

## 5. The prefix-stability test

Independent of every probe, and the smallest thing here.

```python
@pytest.mark.parametrize("role_name", all_role_names())
def test_system_prompt_is_stable_per_role(role_name):
    core = make_core(role_name)
    a = core.build_system_prompt(role, task="add a health endpoint")
    b = core.build_system_prompt(role, task="summarise yesterday's incidents")
    assert a == b, "system prompt varies with task input — PR-CA1 prefix cache is dead"
```

**Correction to the earlier framing.** This was specified as though the
invariant were unprotected. It is not. `tests/test_prompt_prefix_cache.py`
shipped with PR-CA1 (`2a20b0a`) and `tests/test_prompt_cache_ordering.py`
(`5c0b629`, 2026-08-20) covers the invariant, determinism, the role-change
exemption and the vacuous-hold failure mode. HG-12's *"nothing currently
asserts that ordering"* was true on 2026-08-17 and false three days later.

The residual gap is two-dimensional and both dimensions matter:

**Reach.** The existing tests use a synthetic `RoleDefinitionConfig` with none
of the optional blocks set. `roles/_base/role.yaml` carries `default_skills`,
so every shipped role inherits the advertised-skills branch (`:1946`); six set
`reasoning_trace` (`:1927`). Those are the branches with ordering risk, and
none of them was being rendered under test.

**Method.** `test_the_stable_prompt_is_deterministic` compares two calls in one
interpreter. A `set`-ordered skill list is *stable within a process* — the two
calls agree, the test passes, and the prefix differs on every restart. Only a
fresh interpreter under a different `PYTHONHASHSEED` can see it.

Verified by mutation rather than asserted: reversing `:1947` from *iterate the
list, membership-test the set* to *iterate the set* fails
`test_prefix_survives_a_different_hash_seed` naming all eleven roles, while all
thirteen pre-existing prefix tests still pass. That is the gap, demonstrated.

A second guard test (`test_the_hash_seed_probe_would_notice_a_leak`) asserts
that `PYTHONHASHSEED` really does vary string hashing in this environment —
without it the probe could pass unconditionally and prove nothing.

---

## 6. Failure discipline

`preflight.run()` reports a raising check as `BROKEN` rather than propagating.
That is the right behaviour for the framework and the wrong outcome for this
check: a DNS blip must not make a deployment look broken. So
`check_endpoint_capabilities` catches everything itself and emits `DEGRADED`
with the transport error in `detail`. **Nothing in this change can produce
`BROKEN`**, and therefore nothing in it can change an exit code.

Timeouts come from the existing `Context.timeout_s` (default 5.0s). Probes run
sequentially per endpoint and endpoints are not parallelised in Phase 1 —
a doctor command that opens a connection fan-out is a worse diagnostic than a
slow one, and the profile is bounded by the number of distinct `base_url`s in
`models.yaml`, which is small.

---

## 7. Consumption (Phase 2, designed here so Phase 1 does not paint it out)

`EndpointProfile` is deliberately a plain dataclass with no preflight import,
so the budgeter can call `probe_endpoint()` directly at agent boot without
dragging the check registry in. Two consumers:

* `tokenize.value is True` ⇒ the context budgeter's estimator switches from the
  calibrated heuristic to an exact `/tokenize` count, and its safety margin
  goes to zero. This is the single largest quality win available to the
  budgeter and it costs one HTTP call per prompt — which is why it is opt-in
  per endpoint rather than universal.
* `embeddings.value is True` ⇒ `acc/backends/llm_vllm.py`'s local
  SentenceTransformer is a pessimisation on that deployment, and the docstring's
  stated justification (*"vLLM servers that haven't loaded an embeddings
  model"*) no longer applies there.

Neither consumer is wired in Phase 1. The profile is produced and reported
first, so there is something to look at before anything starts depending on it.
