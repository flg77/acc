# Spec: Endpoint capability profiling

**Capability:** preflight · inference-substrate introspection · prefix-cache integrity
**Change ID:** 20260826-endpoint-capability-profile
**Version:** 0.1.0

---

## Requirements — ADDED

### Prefix stability

> Context: the base invariant is **already** specified and tested — retrieved
> memory stays out of the system prompt, the prompt is deterministic within a
> process, a role edit may change it. These requirements ADD the two dimensions
> that coverage did not reach.

**REQ-PFX-001** The prefix invariant SHALL be asserted against the roles in the
shipped `roles/` tree, enumerated at test time, and NOT only against a
synthetic `RoleDefinitionConfig`.

**REQ-PFX-002** The assertion SHALL exercise the optional prompt branches —
`reasoning_trace` (`acc/cognitive_core.py:1927`) and the advertised-skills
branch (`:1946`) — and a test SHALL fail if no shipped role sets either, so the
parametrised coverage cannot become vacuous through a `role.yaml` edit.

**REQ-PFX-003** The prefix SHALL be byte-identical across interpreters started
with differing `PYTHONHASHSEED`. An in-process comparison SHALL NOT be
considered sufficient: a set- or dict-ordered prefix is stable within a process
and invalidates the cache on every restart.

**REQ-PFX-004** A guard test SHALL assert that `PYTHONHASHSEED` varies string
hashing in the running environment, so REQ-PFX-003 cannot pass vacuously.

**REQ-PFX-005** These requirements SHALL hold independently of any endpoint
probe. They are properties of ACC's own assembly, not of the server.

**REQ-PFX-006** Assertion messages SHALL be ASCII. Failure text is rendered on
cp1252 consoles, where a non-ASCII glyph degrades or raises while reporting the
fault.

### The profile

**REQ-PRF-001** `acc.endpoint_profile.probe_endpoint()` SHALL return an
`EndpointProfile` for one `ModelEntry`, and SHALL NOT raise for any transport
failure, timeout, non-JSON body, or unexpected status.

**REQ-PRF-002** Every probed property SHALL be represented as a `Capability`
carrying `value`, `source` and `note`. `value is None` SHALL be the sole
representation of *unknown*. Sentinel values SHALL NOT be used.

**REQ-PRF-003** `source` SHALL record how the value was obtained — one of
`declared`, `probed`, `metrics`, `table`, `inferred` — so a reader can tell a
measurement from a lookup.

**REQ-PRF-004** A probe that cannot locate its expected field SHALL return
`unknown` naming the missing field in `note`. It SHALL NOT infer, default, or
substitute a plausible value.

**REQ-PRF-005** `EndpointProfile` SHALL be importable without importing
`acc.preflight`, so consumers can obtain a profile without the check registry.

**REQ-PRF-007** Capability detection SHALL NOT rely on the HTTP status code
alone. vLLM 0.11.2 answers an unsupported-but-routed endpoint with **HTTP 200**
and an OpenAI error envelope; a probe SHALL parse the body and treat an `error`
key as absence of the capability. An unrouted path returning 404 remains a valid
negative.

**REQ-PRF-008** Where a server declares a capability, the declaration SHALL be
preferred over inferring it from behaviour. Specifically,
`vllm:cache_config_info{enable_prefix_caching}` SHALL be read directly rather
than inferred from a hit rate.

**REQ-PRF-009** *Configured on* and *observed hitting* SHALL be reported as two
distinct properties. A cache with zero queries SHALL be reported as **cold**
with no rate, never as a 0% hit rate, which would read as a misconfiguration.

**REQ-PRF-010** The prefix-cache hit rate SHALL be read from
`vllm:prefix_cache_{queries,hits}_total`. It SHALL NOT be read from
`vllm:external_prefix_cache_*`, which counts cross-instance KV-connector sharing
and reads zero on a single server.

**REQ-PRF-006** Phase 1 SHALL NOT cache profiles. A served model may be
swapped underneath a stable `base_url`, and a cache without an invalidation
story would report a substrate that is no longer running.

### Rope extension

**REQ-ROP-001** On the Ollama backend, rope scaling SHALL be read from the GGUF
metadata returned by `/api/show` — `<arch>.rope.scaling.type`, `.factor`,
`.original_context_length` — and reported with `source="probed"`.

**REQ-ROP-002** On backends that do not expose rope configuration, extension
SHALL be reported as `inferred` when the served window exceeds a known native
length, and the `note` SHALL state that the mechanism is not exposed.

**REQ-ROP-003** An inferred extension SHALL NOT be labelled YaRN. The available
evidence does not distinguish YaRN from linear position interpolation.

**REQ-ROP-004** Detection of a configured extension SHALL NOT be reported as
evidence that the model retrieves well at length. Only a behavioural probe
speaks to that, and only for the depth it tested.

**REQ-ROP-005** The served-to-native comparison SHALL report **reduction** as
well as extension. A served window materially below the native length is the
common edge posture — lighthouse serves 8192 of a 131072-token model to fit the
KV cache in VRAM — and a profile that reported only extension would call that
unremarkable.

**REQ-ROP-006** Where the server exposes a KV pool size, the profile SHALL
derive it in tokens (`num_gpu_blocks * block_size`), so the concurrency ceiling
— pool divided by served window — is available to a later phase rather than
being re-derived from guesswork.

### The check

**REQ-CHK-001** `check_endpoint_capabilities` SHALL be registered through the
existing `preflight.register()` decorator and gated on the existing
`Context.probe_endpoints`. `Result`, `Severity`, `Context`, `run()`,
`registry()` and `report()` SHALL be unchanged by this requirement set.

**REQ-CHK-002** With `probe_endpoints` false, the check SHALL emit exactly one
`OK` result stating that probing was not requested, and SHALL make no network
call.

**REQ-CHK-003** The check SHALL NOT emit `Severity.BROKEN` under any condition,
and therefore SHALL NOT be able to change the process exit code.

**REQ-CHK-004** A transport failure SHALL be reported as `DEGRADED` with the
error type in `detail`. The check SHALL NOT propagate the exception to
`preflight.run()`.

**REQ-CHK-005** A declared `context_window` that disagrees with the served
window SHALL be reported as `DRIFTED`. It SHALL NOT be `BROKEN`, and the
declaration SHALL NOT be overridden — an operator may deliberately declare a
window below what is served.

**REQ-CHK-006** `unknown` SHALL render at `Severity.OK`. An unprobed or
unexposed capability is not a fault.

**REQ-CHK-008** The reason for an `unknown` SHALL appear in the result's
**summary**, not only its detail. The CLI renderer suppresses `detail` on OK
rows and an unknown is an OK row, so a reason confined to the detail is
unreadable.

**REQ-CHK-012** Text rendered into a `summary` or `detail` SHALL be ASCII.
`acc/cli/doctor_cmd.py` degrades un-encodable glyphs to a replacement character
on a cp1252 console, which is the right failure mode and still leaves the line
unreadable.

**REQ-CHK-009** A result SHALL identify its model in the summary as well as in
`subject`. `subject` is rendered only for rows that are not OK, so healthy rows
for two models on one backend would otherwise be indistinguishable.

**REQ-CHK-010** The check SHALL emit one result per configured **entry**, not
one per capability and not one per endpoint. Probing MAY be deduplicated on
`(backend, base_url, model)` — unlike `check_endpoints`, which keys on the root,
because two models served by one host have different capabilities — but the
**reporting SHALL NOT be**: two entries can name the same served model and
declare different windows, and deduplicating rows would validate only the first
declaration while silently skipping the second.

**REQ-CHK-011** An unreachable endpoint SHALL be detected once, not once per
probe. The probes address a single host and port, so continuing after a
transport failure multiplies the timeout with no possibility of new information.

**REQ-CHK-007** A report line whose cause is ambiguous SHALL name the candidate
causes rather than asserting one. Specifically, a zero prefix-cache hit rate
SHALL be reported as *cold, disabled, or unstable prefix*.

### Credentials

**REQ-CRD-001** A probe MAY send `Authorization: Bearer <key>` when the model
entry's `api_key_env` names a variable that is present, constructed as
`acc/backends/llm_openai_compat.py` constructs it.

**REQ-CRD-002** No credential value SHALL appear in any `Result` field, in the
`--json` report, in `EndpointProfile.errors`, or in any log line. A test SHALL
plant a sentinel credential and assert its absence from every output.

**REQ-CRD-003** An absent `api_key_env` variable SHALL yield `unknown` with the
variable **name** in `note`. It SHALL NOT produce an error, and SHALL NOT
trigger an unauthenticated retry that would report a misleading 401.

**REQ-CRD-004** A 401 with a credential present SHALL be reported as
`DEGRADED`, identified as a credential problem.

### Non-mutation

**REQ-MUT-001** No probe SHALL modify endpoint configuration, `models.yaml`, or
any file. The profile is read-only in every phase.

**REQ-MUT-002** Behavioural probes that consume inference SHALL be gated behind
a flag distinct from `--probe`, and SHALL NOT run implicitly. *(Phase 3.)*

**REQ-MUT-003** Writing a measured window back into `models.yaml` SHALL require
explicit operator confirmation and SHALL NEVER be automatic. *(Phase 4.)*

---

## Requirements — MODIFIED

**Preflight secret discipline.** `acc/preflight.py`'s standing rule — *"no
check ever reads a secret value — only whether a name is present"* — is
narrowed to its intent: **no check may report a secret value.** A check MAY use
a configured credential to make a request the operator explicitly asked for
with `--probe`. REQ-CRD-002 is the enforceable form of the original rule.

---

## Non-requirements

* This change SHALL NOT configure any endpoint. `--enable-prefix-caching`,
  `--max-model-len`, `rope_scaling` and `--kv-cache-dtype` are observed, never
  set.
* Ollama's `num_ctx` is not an exception: it is a per-request parameter owned by
  `20260826-context-budget`, not by this change.
* This change SHALL NOT become a metrics pipeline. It is a one-shot diagnostic;
  time series belong to `acc/backends/metrics_otel.py`.
* This change makes no statement about whether the endpoint profile belongs in
  the signed AgentBOM. It produces the artifact that would make that question
  answerable.
