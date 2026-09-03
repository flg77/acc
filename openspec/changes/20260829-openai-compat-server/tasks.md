# Tasks — serving the OpenAI-compatible endpoint (HG-24)

Status legend: `[ ]` open · `[x]` done · `[~]` in progress

## 0. Already in the tree (do not redo)

- [x] `acc/compat_endpoint.py` — auth, parse, gate, dispatch ordering,
      completion/pending/models response shapes (`ccf2a18`).
- [x] The 202-with-a-handle decision, and its rationale, recorded in the module
      docstring rather than left to be rediscovered.
- [x] `model` → role mapping.
- [x] `tests/test_compat_endpoint.py`.
- [x] `acc/compat_endpoint.handle_async()` — the async ordering path, needed
      because the real gate and dispatch are coroutines.
- [x] Memory scoping: `compat_endpoint` is `ISOLATED` in `acc/memory_scope.py`,
      so a compat caller's episodes do not leak into another principal's recall.

## 1. The router

- [x] `acc/webgui/routes_compat.py` — `POST /v1/chat/completions`,
      `GET /v1/models`, `GET /v1/tasks/{task_id}`.
- [x] Mount in `acc/webgui/app.py`, gating its own auth (option A, chosen).
- [x] **Off unless configured** (`ACC_COMPAT_API_KEYS`). No keys → not mounted.
- [x] Reject `stream: true` — already in `parse_request`; now covered by a test.

## 2. The poll route — the contract currently lies without it

- [ ] `GET /v1/tasks/{task_id}` returns: still awaiting approval · approved and
      running · completed (the full chat-completion body) · rejected.
- [ ] Same auth as the completions route, and **the same principal**: a caller
      may poll only its own task. Task ids are guessable enough that this
      matters.
- [ ] Decide and implement expiry for a task nobody ever actions (proposal's
      open question). Whatever is chosen, the poll route must say so rather than
      return `awaiting_approval` forever.

## 3. `dispatch` — submit to the collective

- [ ] Submit the parsed request as a task, wait for the reply, return
      `(reply, usage)`.
- [ ] `usage` must be real token counts, not estimates — a client billing
      against this will trust them. Reuse whatever the prompt path already
      records.
- [ ] Budget: a compat request is charged like any other. Verify it flows
      through the context budget (RP-04) rather than around it.

## 4. `gate` — consult oversight

- [ ] Return an oversight id when the work needs approval, empty otherwise.
- [ ] Reuse the existing compliance/oversight path; do not reimplement risk
      classification here.
- [ ] Test that a HIGH-risk request never reaches `dispatch` — the ordering is
      the whole point of `handle()`, and a regression here is a governance
      bypass, not a bug.

## 5. Keys and principals

- [ ] Document the env format for the key→principal map.
- [ ] Keys are hashed for comparison and **never logged** — verify no log line
      can emit a presented key, including on the error paths.
- [ ] `acc-cli doctor` reports whether the endpoint is enabled and how many
      principals are configured. Not the keys.

## 6. Tests

- [ ] Route-level: 200 completion, 202 pending, 401 unauthenticated,
      400 malformed, 503 no dispatcher, explicit error on `stream: true`.
- [ ] Poll route: each state, plus a caller polling another principal's task
      gets 404 (not 403 — 403 confirms the task exists).
- [ ] Governance: a gated request produces no dispatch call. Assert on the
      mock, not on the response.
- [ ] An unmodified OpenAI client library completes a round trip against a test
      server. This is the actual claim of HG-24 — "anything that can talk to
      OpenAI could talk to a governed ACC collective" — and it is not proven by
      shape-level unit tests.

## 7. Docs

- [ ] `docs/howto-openai-compat.md`: point an existing client at ACC, what 202
      means, how to poll, why `model` is a role.
- [ ] Note in the docs that this is the surface most likely to be pointed at by
      something the operator did not write, and that it is off by default.

## Deferred, deliberately

- [ ] Streaming. Needs its own design: a response that may become a 202
      mid-flight does not map onto SSE chunks.
- [ ] An OAuth-upstream proxy (Hermes' `hermes proxy`).
- [ ] Embeddings / other OpenAI routes. Only add on real demand.


## APPLIED — 2026-08-29

Option A (mount on `acc-webgui`) chosen by the operator.

Shipped: the three routes, `handle_async`, the pending store, and
`tests/test_compat_routes.py` (14 tests; 38 with the existing endpoint suite).
232 passed across the webgui/auth/compat suites.

### The finding that changed the shape

**ACC has no pre-execution oversight gate on an ordinary completion.** It gates
*actions* — `capability_dispatch.py:493` submits to the oversight queue before
invoking a capability, and `agent.py:989` does the same for assistant proposals
— but `cognitive_core.py:1347` classifies a prompt's EU AI Act risk *after* the
work, to fill the audit record with `outcome="PROCESSED"`. Nothing holds
execution.

So the 202-with-a-handle contract had nothing real to call. HG-24 offered two
acceptable answers — "the endpoint supports only non-gated work, or returns a
pending handle" — and this ships the **first**, because the second would return
a handle pointing at an oversight item nobody created: a lie the client cannot
detect.

A HIGH-or-above role is therefore refused with 403 and an explanation, rather
than dispatched. The 202 machinery stays intact; when a real pre-flight gate
lands, `_make_gate` returns its oversight id and the rest already works.

### Still open

- [ ] A real pre-flight oversight gate for completions — the prerequisite for
      HIGH-risk roles being usable here at all. This is a governance capability,
      not endpoint work, and is bigger than HG-24.
- [ ] `PendingStore` is in-process: a restart loses handles and a second replica
      does not see the first's. Fine for a single deployment that is off by
      default; move to Redis before any fleet use.
- [ ] `usage` token counts are read off the reply if present and default to 0.
      Verify the prompt path actually records them before a client bills on it.
- [ ] The round-trip test with an unmodified OpenAI client library. This is
      HG-24's actual claim and shape-level unit tests do not prove it.


## APPLIED — compat sessions, 2026-08-30

`X-ACC-Session` names the thread a completion continues. One history either
way, never two:

| Header | What is sent | History from |
|---|---|---|
| present | the **latest** user message only | the durable tracelog |
| absent | the whole array joined (unchanged) | the client, one-turn session |

`acc.thread_continuity` is why: "a channel can name a thread; it cannot supply
its content", because a client-supplied transcript is model-visible text with no
durable origin — the invariant DS-01 exists to protect. An OpenAI client resends
everything each turn, so honouring both would put the same turns in front of the
model twice and charge them twice against the RP-04 budget.

A header rather than a body field: the OpenAI schema has no session concept, and
adding one would make the request non-standard for every other client.

**Session ids are not a secret and do not need to be.** A caller may name
another principal's thread; replay is filtered on the same `acc.memory_scope`
key episodes use, and `compat_endpoint` is ISOLATED there, so a foreign thread
replays **empty** — never partially, and never as an error that would confirm it
exists.

6 new tests (44 in the compat suites, 216 across compat/webgui/continuity).

### Corrects the earlier note

This was recorded as "blocked on a design decision". It was not blocked — it was
unanswered, and `thread_continuity`'s module docstring had already answered it.
The tracelog is the single source of truth; compat was simply using the wrong
one.
