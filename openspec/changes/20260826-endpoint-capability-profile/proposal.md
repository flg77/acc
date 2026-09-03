# 20260826-endpoint-capability-profile — proposal

## Why

ACC has never asked an inference endpoint what it can do.

`check_endpoints` (`acc/preflight.py:448`) dials the **gateway root** with a bare
`GET` and reports the HTTP status. Its docstring is right about why that
matters — *"a gateway-wide outage and one bad model id look identical from a
single model probe"* — and it is the entirety of ACC's endpoint knowledge.
`grep -rn "v1/models\|/metrics\|/tokenize\|api/show"` over `acc/` returns **one**
hit, a comment in `acc/tui/screens/configuration.py:146` describing what a
healthy gateway serves. Nothing reads any of them.

The endpoint is an unexamined dependency: ACC knows it is up, and nothing else.

### Three shipped optimisations, none verified

| Optimisation | Assumes about the endpoint | Checked |
|---|---|---|
| **PR-CA1** — the RAG block was moved out of the system prompt onto the user message *specifically* so the per-role prefix stays contiguous and cacheable (`acc/cognitive_core.py:1929-1938`) | the server prefix-caches, and ACC is hitting it | ❌ neither half |
| **PR-CA2** — `enable_prompt_cache` attaches Anthropic `cache_control` (`acc/config.py:720`, `acc/backends/llm_anthropic.py:64-84`); vLLM/Ollama ignore the hint because *"they already benefit from the stable prefix"* | that those servers auto-cache at all | ❌ assumed |
| **Local embedding fallback** — `acc/backends/llm_vllm.py:104-129` loads SentenceTransformer client-side *because* the served model may be chat-only with no `/v1/embeddings` | that the server has no embeddings endpoint | ❌ never tested |

Each of these degrades **silently**. A prefix cache that never hits, a CPU
embedder running beside an idle server-side one, a window a quarter the size
declared — all three present as *"the model is a bit slow and a bit
forgetful"*, which is model weakness misattributed, the same failure shape
`20260825-conversational-turn-continuity` identified for missing continuity.

PR-CA1's prefix invariant, by contrast, **is** protected, and the claim
inherited from HG-12 that *"nothing currently asserts that ordering"* is stale.
`tests/test_prompt_prefix_cache.py` shipped with PR-CA1 itself (`2a20b0a`,
2026-05-23) and `tests/test_prompt_cache_ordering.py` was added by `5c0b629` on
2026-08-20 — **three days after HG-12 was written**, and HG-12 was never
updated. Between them they cover the invariant, its determinism, the
role-change exemption, and the vacuous-hold failure mode.

What those thirteen tests do not cover is narrower and real:

* They assert against a **minimal synthetic role** — purpose, persona, version
  — and none of the optional blocks. Every optional block is where variability
  could leak, and both are live in `roles/`: six roles set `reasoning_trace`
  (`acc/cognitive_core.py:1927`) and `_base` carries `default_skills`, so all
  eleven inherit the advertised-skills branch (`:1946`), whose list is filtered
  against a **set** (`advertised_skill_ceiling`, `:1835`).
* They compare two calls **in one process**. A set- or dict-ordering leak is
  stable within an interpreter and differs between them — it breaks the cache
  on every restart while every in-process comparison agrees.

### The question that opened this

*"Can ACC ensure YaRN has been enabled on the endpoint?"*

**No, and the framing needs correcting.** ACC cannot enable YaRN; RoPE
rescaling is a serving decision. ACC owns exactly one endpoint knob and it is
currently broken — Ollama's `num_ctx` is a per-request option
`acc/backends/llm_ollama.py:59` never sends (that is
`openspec/changes/20260826-context-budget` Phase 1).

What ACC can do is **verify, record and refuse**, and the property worth
verifying is the *effective usable window*, not the presence of a config field:
config fields are backend-specific and mostly unexposed, a configured extension
can be present and still degrade, and the effective number is what the context
budgeter consumes anyway. Detecting YaRN says extension is configured; it says
nothing about whether the model retrieves at length.

## What changes

### Phase 1 (this ship — v0.11.0)

* **`tests/test_prompt_prefix_stability_real_roles.py`** — extends the existing
  coverage in the two directions it does not reach: parametrised over the real
  `roles/` tree rather than a synthetic role, and a cross-process check under
  differing `PYTHONHASHSEED` for the set-ordering leak an in-process comparison
  structurally cannot see. Verified by mutation: reversing `:1947` to iterate
  the set fails the new test naming all eleven roles, while all thirteen
  existing prefix tests still pass.
* **`acc/endpoint_profile.py`** — a `EndpointProfile` dataclass plus per-backend
  declarative probes (Rung 1): `GET /v1/models`, `POST /api/show`, `/tokenize`
  presence, `/v1/embeddings` presence, `GET /metrics` scrape, and a static table
  for Anthropic. Structured data, no rendering.
* **`preflight.check_endpoint_capabilities`** — registered through the existing
  `register()` decorator (`acc/preflight.py:113`), gated on the existing
  `Context.probe_endpoints`, rendering the profile into `Result` rows. No change
  to `Result`, `Severity`, `run()` or `report()`.
* **`unknown` as a first-class outcome** — a probe that cannot answer says so.
  It maps to `Severity.OK`, because an unprobed capability is not a fault (§
  design.md for why no fifth severity).
* **`acc-cli doctor --probe`** gains the capability block in its rendering.

### Phases 2–N (deferred)

* **Phase 2 — consumption.** `/tokenize` present ⇒
  `20260826-context-budget`'s estimator switches from calibrated to exact and
  its safety margin goes to zero. `/v1/embeddings` present ⇒ surface the local
  SentenceTransformer as a pessimisation for that deployment. Requires the
  context-budget change merged.
* **Phase 3 — behavioural probes (Rung 2), behind a new `--deep`.** Needle
  probe for effective context (binary-searched depth), prefix-cache A/B (same
  long prefix twice, compare TTFT and reported cache tokens), guided-decoding
  acceptance. These spend real inference and must never run implicitly.
* **Phase 4 — measured write-back.** Offer to write a measured effective window
  into `models.yaml` as `context_window`, only on explicit operator
  confirmation, never automatically.

Phase mapping to the roadmap proposal: this change's Phase 1 combines **RP-05
Phases 1–2**; Phase 2 is RP-05 Phase 3; Phase 3 is RP-05 Phase 4; Phase 4 is
RP-05 Phase 5.

## Impact

* **Affected code:** `acc/endpoint_profile.py` (new), `acc/preflight.py` (one
  registered check, no API change), `acc/cli/doctor_cmd.py` (rendering only),
  `tests/` (new).
* **New env knobs:** none in Phase 1. `--deep` is a Phase 3 CLI flag plus a
  `Context.deep_probe` field.
* **Authenticated probes:** Rung 1 sends the configured `Authorization: Bearer`
  header when `api_key_env` names a variable that is present, reusing the same
  construction as `acc/backends/llm_openai_compat.py:302-308`. This is a
  deliberate widening of what `doctor --probe` does — see design.md §4 — and is
  bounded by a hard rule: **no probe output may contain a credential**, and an
  absent env var yields `unknown`, never an error.
* **Tests:** ~22. Prefix stability across all roles; per-backend probe parsing
  against captured fixtures; every probe degrades to `unknown` on a missing
  field, a timeout, a non-JSON body and a 401; the check never raises; no
  `Result` field ever contains a credential; `--probe` off ⇒ no network call.
* **Backward compatibility:** total. Without `--probe` the new check emits one
  OK row saying it was not requested, exactly as `check_endpoints` does today.
  Exit codes are unchanged because nothing here can emit `BROKEN`.

## What stays open after Phase 1

* **The vLLM field names are version-dependent and unverified.** The exact
  `/v1/models` ModelCard fields and `/metrics` gauge names have **not** been
  checked against the build running on lighthouse. Task 1.1 is to capture real
  responses as fixtures before any parser is written; every parser degrades to
  `unknown` rather than asserting.
* **YaRN is directly detectable only on Ollama** (the GGUF
  `<arch>.rope.scaling.*` keys). On vLLM the strongest available claim is
  *"served `max_model_len` exceeds this model's native length, so some rope
  extension is active"* — which does not distinguish YaRN from linear position
  interpolation. Resolving that needs the Phase 3 needle probe.
* **A zero prefix-cache hit rate has three causes** — cold server, caching
  disabled, or ACC's prefix genuinely unstable. Phase 1 can only report the
  ambiguity; the new stability test plus the Phase 3 A/B are what separate them.
* **Profile staleness.** A served model can be swapped underneath a stable
  `base_url` at any time. Phase 1 does not cache the profile at all, which is
  correct-but-slow; any caching needs an invalidation story first.
* **Whether the substrate belongs in the signed AgentBOM.** An agentset
  attested as running `lighthouse-llama32-3b` may have run at a quarter of the
  declared context with no cache and no guided decoding. This change produces
  the artifact that would make that answerable; it does not answer it.
