# Serving the OpenAI-compatible endpoint (HG-24)

**Status:** proposed · **Date:** 2026-08-29 · **Backlog item:** HG-24 (ADOPT, P2, M)

## What HG-24 asked, and what is already answered

> Expose the agent as an OpenAI-compatible HTTP endpoint. […] Forces a decision
> about what a 'completion' means when the responder is a collective with an
> oversight queue. […] **that design question is the real content of this item.**

**That question is already settled in the tree.** `acc/compat_endpoint.py`
(351 lines, `ccf2a18`, with `tests/test_compat_endpoint.py`) decides it
explicitly: a gated request returns **202 with a handle** — not a refusal, which
would make the endpoint useless for exactly the work worth governing, and not a
block until the oversight timeout, which hangs a client on a socket it did not
expect to hold for minutes.

It also settles two things HG-24 did not ask but should have:

- **`model` maps to a role, not a model.** The caller chooses *who does the
  work*; which model that role runs on stays a deployment decision.
- **No path bypasses evaluation, budgets or recording**, and there is no
  unauthenticated access. A familiar request shape is a client convenience, not
  a different class of work.

So HG-24's stated content is done. What is missing is smaller and duller than
the item implies, and worth restating plainly.

## The actual gap: the shape exists, the socket does not

`compat_endpoint.py` is a pure module. Nothing imports it except its own tests
and `memory_scope.py`. There is no HTTP route anywhere in ACC that serves
`/v1/chat/completions`.

`handle(body, key, *, dispatch=None, gate=None)` takes both integrations as
injected callables, so the ordering can be tested without a live collective. The
remaining work is to supply them for real and expose the result.

### A defect this surfaced

`pending_response()` tells the caller:

> "Poll with the `task_id`, or an operator can approve it from the oversight
> queue."

**No poll route exists.** A 202 handle that cannot be polled is not a handle; it
is a dropped request with better prose. Any implementation of HG-24 must ship
the retrieval route in the same change, or the documented contract is a lie on
first contact.

## Why the webgui rework should not block this

The request bundles HG-24 with realigning `acc-webgui` to `acc-tui`. The
alignment is genuinely needed — measured drift over 90 days:

| | commits |
|---|---|
| `acc/tui/screens/` | 73 |
| `webgui/` + `acc/webgui/` | 12 |

Last webgui change 2026-08-20; last TUI change 2026-08-25. Nominal screen parity
exists (13 webgui screens against 12 TUI ones), so the drift is *inside*
screens: conversational continuity, `@references`, the whole Golden Prompt pane
(047 S1–S4), `models.yaml` CRUD, the day-0 built-in catalog, OKF pack counts,
the inline gate card, prompt history.

But the coupling is one line of FastAPI. HG-24 needs an app to mount a router
on; `acc/webgui/app.py` already is one. Nothing about serving
`/v1/chat/completions` depends on the Prompt screen gaining a golden-prompt
picker.

**Bundling them means HG-24 ships when the webgui finishes catching up.** That
is the wrong trade for a P2/M item whose hard part is already merged. This
proposal covers the endpoint. The webgui alignment gets its own proposal, and
the two proceed in parallel.

There is one real shared decision, below.

## The shared decision: two auth schemes on one app

`acc-webgui` authenticates humans — oauth2-proxy/Keycloak, htpasswd sessions,
mTLS. `compat_endpoint.authenticate()` authenticates *programs*: a presented key
hashed and matched against an env-configured key→principal map, default deny,
each caller admitted by an operator as an external principal.

These are different mechanisms for different callers, and both are correct. The
decision is whether they live in one process:

**A — mount on `acc-webgui` (recommended).** One deployment, one TLS
termination, one place where budgets and audit already sit. The compat router
gates its own auth, exactly as `routes_config` and `routes_roles` already do —
this app has per-router auth already, so it is not a new pattern.
Risk: a webgui auth misconfiguration becomes an endpoint exposure.

**B — a separate `acc-compat` service.** Clean blast radius; an operator can run
the endpoint without exposing the GUI at all, which matters because this is the
surface most likely to be pointed at by something the operator did not write.
Cost: a second image, second deployment, second TLS story.

Recommend **A**, with the endpoint disabled unless keys are configured — so the
default posture is off, and turning it on is an explicit act.

## Scope

In:

- `POST /v1/chat/completions` — non-streaming, backed by `handle()`.
- `GET /v1/models` — roles, via `models_response()`.
- `GET /v1/tasks/{task_id}` — **the missing poll route**, returning the pending
  handle's current state and the completion once approved and run.
- A real `dispatch` (submit to the collective, return reply + usage) and a real
  `gate` (consult the oversight/compliance path).
- Off by default; enabled by configuring keys.

Out:

- **Streaming (`stream: true`).** Deferred deliberately: streaming a response
  that might turn into a 202 mid-flight is its own design question, and many
  clients set `stream` by default — so the endpoint must reject it explicitly
  with a clear error rather than ignore the field.
- The webgui alignment (separate proposal).
- `hermes proxy`'s OAuth-upstream equivalent.

## Open question

The 202 contract says a human is deciding. It does not say what happens if
nobody decides. An oversight item that is never actioned leaves a `task_id` that
polls forever. Whether that expires, and what the poll route returns when it
does, is not yet decided — and it is the same class of question HG-24 raised
originally, just one level down.
