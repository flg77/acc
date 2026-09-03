# 20260826-endpoint-capability-profile — tasks

## Phase 1 (v0.11.0) — assert the prefix, profile the endpoint

### 1.1 Fixtures first -- DONE
- [x] Captured from **live lighthouse** (10.199.12.9, container
      `vllm-llama-32-3b-fp8`, `0.0.0.0:8022->8000`) into
      `tests/fixtures/endpoint/`: `vllm_v1_models.json`, `vllm_tokenize.json`,
      `vllm_embeddings_unsupported.json`, `vllm_metrics.txt`,
      `vllm_version.json`.
- [x] Version recorded: vLLM **0.11.2+rhai5** (RHAIIS), model
      `RedHatAI/Llama-3.2-3B-Instruct-FP8`, served `max_model_len=8192`.
      `tests/fixtures/endpoint/PROVENANCE.md` carries the capture method.
- [x] Secret-scanned: no credential-shaped strings (the local vLLM is
      unauthenticated).
- [ ] Ollama `api_show.json` -- **NOT captured**: no ACC host runs Ollama.
      The Ollama parser ships against an authored fixture and says so in its
      docstring.

> **Three findings that changed the design.** This is what fixtures-first was
> for; all three would have shipped as bugs.
>
> 1. **HTTP status lies.** `POST /v1/embeddings` on a chat-only model returns
>    **HTTP 200** with `{"error":{...,"code":400}}`. A status-only probe reports
>    embeddings as *available* and would have declared the local
>    SentenceTransformer redundant -- the opposite of the truth. Detection now
>    parses the body (`_error_of`). An unrouted path does still 404 honestly.
> 2. **Prefix caching is declarative.** `vllm:cache_config_info` carries
>    `enable_prefix_caching="True"` as a label, so *is it on* needs no
>    behavioural A/B -- that was scheduled for Phase 3 and is not needed for
>    this question. It also yields `cache_dtype`, `gpu_memory_utilization`,
>    `block_size` and `num_gpu_blocks`.
> 3. **The real direction is reduction, not extension.** lighthouse serves 8192
>    of a 131072-token model -- 6%, to fit KV in VRAM. The design only handled
>    `served > native`; `_window_scaling` now reports both directions.
>
> **Two numbers that did not exist before:**
> * **Prefix-cache hit rate 29040/31185 = 93.1%** -- HG-12's item 1, answered
>   with a measurement on the deployment that matters. PR-CA1 works.
> * **KV pool = 6032 blocks x 16 = 96,512 tokens** = ~11.8 concurrent
>   full-window sequences at 8192. That is the shared-KV concurrency ceiling
>   RP-04 Phase 6 needed and could not name.
>
> Trap avoided: `vllm:external_prefix_cache_*` is a *different* family
> (cross-instance KV-connector sharing, 0.0 here). Reading it would report 0%
> on a cache hitting 93%. Pinned by a test.

### 1.2 Prefix stability (independent -- DONE)
> Scope corrected on implementation. The invariant was **already** covered by
> `tests/test_prompt_prefix_cache.py` (shipped with PR-CA1, `2a20b0a`) and
> `tests/test_prompt_cache_ordering.py` (`5c0b629`, 2026-08-20 -- three days
> after HG-12 claimed nothing asserted it). What was missing was reach and
> method, not the assertion itself.

- [x] `tests/test_prompt_prefix_stability_real_roles.py` -- parametrised over
      the real `roles/` tree via `list_roles()` rather than a synthetic
      `RoleDefinitionConfig`, so the optional prompt branches are actually
      rendered: `_base/role.yaml` carries `default_skills` (all 11 roles inherit
      the advertised-skills branch, `:1946`) and six set `reasoning_trace`
      (`:1927`).
- [x] `test_prefix_survives_a_different_hash_seed` -- renders every role's
      prompt in a fresh interpreter under `PYTHONHASHSEED=0` and `=1` and
      compares SHA-256 digests. This is the check an in-process comparison
      **structurally cannot** make: a set-ordered list is stable within one
      process and differs between them, so the cache dies on restart while
      every existing test agrees.
- [x] `test_the_hash_seed_probe_would_notice_a_leak` -- guards the guard;
      asserts `PYTHONHASHSEED` really does vary string hashing here, so the
      probe cannot pass unconditionally.
- [x] `test_the_optional_prompt_branches_are_actually_exercised` -- fails if no
      shipped role sets `reasoning_trace` or `default_skills`, so the
      parametrised tests cannot go vacuous after a `role.yaml` edit.
- [x] `test_the_role_tree_is_not_empty` -- guards the parametrisation itself.
- [x] Assertion messages kept ASCII: the failure text prints to cp1252 Windows
      consoles, the same hazard `acc/cli/doctor_cmd.py:_make_stdout_lossy`
      documents.
- [x] **Mutation-verified.** Reversing `:1947` from *iterate the list,
      membership-test the set* to *iterate the set* fails
      `test_prefix_survives_a_different_hash_seed` naming all 11 roles, while
      all 13 pre-existing prefix tests still pass. `cognitive_core.py` restored;
      `git diff` clean.
- [x] 26 new tests pass; 39 pass together with the two existing prefix files.

### 1.3 The profile module -- DONE
- [x] `acc/endpoint_profile.py`: `Capability`, `EndpointProfile`, `unknown()`,
      `probe_endpoint()`, `NATIVE_WINDOWS`.
- [x] `Capability.value is None` is the sole unknown; callers test `.known`, so
      a legitimate `False` or `0` cannot be misread as absent (tested).
- [x] Does not import `acc.preflight`.
- [x] `unknown()` requires a reason -- a bare unknown is indistinguishable from
      a probe nobody wrote.
- [x] `urllib`, not `httpx`: a diagnostic must not fail on an optional dep.
- [x] Module docstring in house style, carrying the three live findings.

### 1.4 Rung 1 probes -- DONE
- [x] vllm / openai_compat `GET /v1/models` -> `served_model`, `served_window`
      via `max_model_len`.
- [x] `POST /tokenize` -> `tokenize` bool **and a second `max_model_len`**,
      used as the window fallback when a gateway's ModelCard omits it.
- [x] `POST /v1/embeddings` -> body-parsed, not status-parsed (finding 1).
- [x] `GET /metrics` -> `prefix_cache_enabled` (declarative label),
      `prefix_cache_hits` (counters), `kv_cache` (dtype, utilisation, derived
      pool size). Plain family only, never `external_*`.
- [x] Zero queries reports **cold**, with `rate: None` -- never 0%, which would
      read as a misconfiguration.
- [x] ollama `POST /api/show` -> native length, GGUF `rope.scaling.*` (the one
      backend where the mechanism is *read*), `num_ctx`. Lower confidence,
      declared as such.
- [x] anthropic static table; no call made.
- [x] `NATIVE_WINDOWS` substring table, `source="table"`, unknown on a miss.
- [x] `_window_scaling` reports `extended` / `reduced` / `native` and **never
      names YaRN** (REQ-ROP-003, tested).
- [x] **Live end-to-end probe against lighthouse: 0 errors, all 9 fields
      resolved.**

### 1.5 The check -- DONE
- [x] `check_endpoint_capabilities` in `acc/preflight.py`, via `register()`,
      gated on `Context.probe_endpoints`.
- [x] `Result`, `Severity`, `Context`, `run()`, `registry()` and `report()`
      unchanged (REQ-CHK-001).
- [x] `probe_endpoints` false -> one OK row, **zero** network calls, asserted.
- [x] Cannot emit BROKEN, so cannot move an exit code (REQ-CHK-003, asserted
      via `preflight.exit_code`).
- [x] Declared-vs-served mismatch -> **DRIFTED**, never BROKEN, and the detail
      says declaring *less* than is served is legitimate (KV headroom).
      Written against `getattr(entry, "context_window", 0)` so it is inert
      until `20260826-context-budget` adds the field, and tested both ways.
- [x] Zero-query cache -> **cold**, never "0% hit" (which reads as a
      misconfiguration). A genuinely low rate is DEGRADED and names both
      candidate causes.
- [x] Caching *configured off* -> DEGRADED, with the reason ACC cares:
      PR-CA1's prompt restructuring buys nothing there.
- [x] Dedup key carries the **model**, unlike `check_endpoints` which dedups by
      root -- two models on one vLLM have different windows.
- [x] **Probing dedups; reporting does not.** Caught by a live run, not by
      reasoning: two entries naming the same served model with *different*
      declared windows collapsed to one row, so the second declaration was
      never validated -- exactly the drift the check exists to surface. Now one
      probe per `(backend, base_url, model)`, one row per **entry**.
- [x] Rendered output kept ASCII: `doctor_cmd` degrades un-encodable glyphs to
      a replacement char, which is graceful and still unreadable. Pinned by a
      test over the renderer's source.
- [x] `tests/test_preflight_capabilities.py` -- 20 tests.

> **The renderer decided the row shape.** `acc/cli/doctor_cmd.py:78` prints
> `detail` only when a row is **not** OK, and an `unknown` is an OK row (it is
> not a fault). A reason parked in the detail would therefore never be read, so
> the reason goes in the **summary** and the check emits one dense line per
> endpoint rather than a nine-row block per model. The model id is repeated into
> the summary for the same reason: `[subject]` is rendered only on non-OK rows,
> so two healthy vLLM models would otherwise be indistinguishable.

> **One perf fix the check forced.** Probing a dead endpoint tried all four
> requests, so at the default 5s timeout each unreachable entry cost 20s -- with
> five such entries in `models.yaml`, 100 seconds of certain failure. Unreachable
> is now an *endpoint* condition that short-circuits after the first request,
> the same way authentication already did. Measured on a real dead port: one
> timeout, one error, all fields unknown.

### 1.7 Rendering -- DONE, no code required
- [x] The existing generic renderer in `acc/cli/doctor_cmd.py` already produces
      the capability block: it prints `severity`, `name`, `[subject]` and
      `summary` for every result and the `detail` beneath faults. Because the
      check was shaped to that contract (above), **no renderer change was
      needed** -- which is the outcome preflight's own "one implementation,
      three surfaces" docstring is asking for, and it means the TUI and web GUI
      inherit the block for free.
- [x] `unknown` reads as a value in the line, not as an absence.
- [x] Every line ends in an implication for ACC, not a raw fact.
- [x] Verified live (see Verification).

### 1.6 Credential discipline -- DONE for the profile layer
- [x] Sends `Authorization: Bearer` when `api_key_env` is set and present
      (REQ-CRD-001).
- [x] Absent env var -> unknown naming the **variable**, and **no HTTP call at
      all** (REQ-CRD-003). Firing unauthenticated probes would be the retry the
      requirement forbids.
- [x] 401 with a key present -> endpoint-level unknown identifying it as a
      credential problem (REQ-CRD-004).
- [x] `_redact()` scrubs the key from every error string at the profile
      boundary (REQ-CRD-002), enforced by a sentinel test.
- [x] Amended the `acc/preflight.py` module docstring: the standing rule
      narrows from *"no check ever reads a secret value"* to *"no check ever
      **reports** a secret value"*, stating inline why (a keyed gateway 401s an
      unauthenticated probe, so the question is unanswerable without the
      credential the operator already configured) and where the enforcing half
      lives.

> **Two real bugs the tests caught, both in code I had just written.** Worth
> recording because both were *reasoning* failures, not typos.
>
> 1. **Credential leak (REQ-CRD-002 violated).** Redaction was placed inside
>    `_request`, justified by the comment *"urllib does not echo the header into
>    exceptions"*. True, and insufficient -- the key can reach an error string
>    by other routes, and the stubbed-transport test bypassed the placement
>    entirely and proved the leak. Fixed by redacting at the **boundary where a
>    string enters the profile** (`_redacting_caller`), which is where the
>    invariant is actually stated. Enforce an invariant where it is asserted,
>    not where you believe it is threatened.
> 2. **Auth condition masked by a fallback.** The `/tokenize` window fallback
>    happily filled in `served_window=8192` on an endpoint whose `/v1/models`
>    had returned 401 -- a confident answer for an endpoint ACC could not
>    authenticate to. Fixed by treating authentication as an **endpoint-level**
>    condition that short-circuits to `_all_unknown()` before any other probe
>    runs. Cheaper too: one call instead of four against a gateway that will
>    reject all of them.

### Verification
- [x] `tests/test_prompt_prefix_stability_real_roles.py` -- 26 tests.
- [x] `tests/test_endpoint_profile.py` -- 38 tests against the captured fixtures.
- [x] `tests/test_preflight_capabilities.py` -- 20 tests: inertness without
      `--probe`, exit-code immunity, row shape, drift, dedup, no credential in
      the report.
- [x] **Live `acc-cli doctor --probe`** against a registry holding lighthouse, a
      hosted Anthropic entry and a deliberately dead port:
      ```
      ok        lighthouse-llama32-3b (vllm): window 8192 (6% of native) | prefix cache 93% hit | tokenize yes | embeddings no | KV pool 96512t
      ok        claude-sonnet (anthropic): window 200000 | prefix cache unknown | tokenize yes | embeddings no
      DEGRADED  dead-endpoint (vllm): window unknown (unreachable: URLError: timed out) | prefix cache unknown
      ```
      `--json` reports `worst: degraded`, and the **exit code stays 0**.
- [x] Coverage of `acc/endpoint_profile.py`: **93%**; every line of
      `check_endpoint_capabilities` covered.
- [x] **Live end-to-end re-verified after both fixes**: 0 errors, 9/9 fields.
- [x] `tests/test_endpoint_profile_probes.py` -- 17 tests: probe orchestration,
      partial-failure containment, the root-vs-/v1 URL split, and the four
      credential rules including a planted sentinel. **Found two real bugs.**
- [x] Per-backend parsing against the 1.1 fixtures; a missing field yields
      unknown naming the field; timeout, non-JSON body, 404 and 500 each yield
      unknown or False, never a raise.
- [x] Regression: `doctor` and `doctor --json` exit codes unchanged against an
      unreachable endpoint (verified live -- DEGRADED row, exit 0).
- [x] **Full sweep classified against a clean worktree at `HEAD` (`f8ece3e`):
      8 failed / 4899 passed / 69 skipped. All 8 reproduce on the clean tree ->
      zero regressions.** The standing note said 5 pre-existing reds; it is now
      8 (`tests/catalog/*` x4, `test_collective_presets`,
      `test_config_model_registry`, `test_os_basics_role_flag` x2). Re-classify
      rather than trusting a remembered count.
      NB `tests/container/integration/test_stack_health.py` raises
      `FileNotFoundError` at **collection** without a deployed stack, so a bare
      `pytest tests/ -x` dies before running anything -- exclude
      `tests/container` and `tests/integration` for a unit sweep.
- [x] lighthouse smoke captured (see the rendered block above); paste it into
      the PR description as the evidence that the fixtures matched reality.

## Phase 2 (deferred) — consumption
- [ ] `tokenize.value is True` ⇒ `20260826-context-budget`'s estimator switches
      to exact counting; safety margin to zero. Requires that change merged.
- [ ] `embeddings.value is True` ⇒ surface the local SentenceTransformer
      (`acc/backends/llm_vllm.py:104-129`) as a pessimisation for that
      deployment, and revisit its docstring's justification.

## Phase 3 (deferred) — behavioural probes, behind `--deep`
- [ ] `Context.deep_probe` + `--deep` on `doctor`; never implicit.
- [ ] Needle probe — retrievable token at a known depth, binary-searched, to
      yield the honest effective window.
- [ ] Prefix-cache A/B — identical long prefix twice, compare TTFT and reported
      cache tokens. This is what separates *cold* from *disabled* from
      *unstable prefix*.
- [ ] Guided-decoding acceptance probe, for RP-03 and the `[SKILL: …]` grammar.
- [ ] Measure and document the cost on a 3B FP8 edge box before `--deep` is
      described as routine.

## Phase 4 (deferred) — measured write-back
- [ ] Offer to write a measured effective window into `models.yaml` as
      `context_window`, on explicit operator confirmation only.
