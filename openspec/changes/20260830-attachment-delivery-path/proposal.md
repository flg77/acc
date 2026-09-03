# Delivering an attachment to the model

**Status:** proposed · **Date:** 2026-08-30

## What exists, and why that is the confusing part

`acc/attachments.py` is complete and well-tested — 21 tests. It validates an
upload, stores it content-addressed, knows which backends are multimodal
(`{"anthropic", "openai_compat"}`), builds provider content blocks, and refuses
to silently drop an image on a text-only backend:

> Silently dropping them would produce a confident answer to a question the
> model never saw — worse than an error, because nothing looks wrong.

`acc/webgui/routes_attachments.py` is mounted: `POST /api/attachments` accepts
an image and returns the reference, `GET /api/attachments/capability` reports
whether the deployment can accept one at all.

**None of it can reach a model.** Outside its own tests, `content_blocks()` has
no callers. The endpoint accepts an image, stores the bytes, and hands back a
reference nothing downstream can use.

This is not a half-finished feature. It is a finished *component* with no socket
— the same shape as HG-24 before `f9d968d`, and it went unnoticed for the same
reason: everything present is correct, so nothing looks wrong.

## The five missing links

| Layer | State |
|---|---|
| `PromptRequest` | no attachment field — a reference cannot leave the browser |
| TASK_ASSIGN payload | carries no references |
| `cognitive_core` | never reads them |
| **`LLMBackend.complete()`** | **cannot carry image blocks at all** |
| durable record | does not note what was attached |

The fourth is the one that makes this a proposal rather than an afternoon:

```python
async def complete(
    self, system: str, user: str,
    response_schema: dict | None = None,
    cache_prefix: bool = False,
) -> dict
```

`user` is a `str`. `content_blocks()` returns `list[dict]`. **There is no
parameter of the right shape**, and five backends implement this protocol —
`ollama`, `anthropic`, `vllm`, `openai_compat`, `llama_stack` — of which only
two are multimodal.

So the cost is a protocol change across every backend, not UI wiring.

## A sixth gap, found while measuring

**`attachments.prune()` is never called.** The module's own docstring says "the
store is governed by the same retention policy as sessions" and nothing runs it.
The store grows without bound, and every stored image is personal data ACC has
no expiry for. `load_bytes()` already raises a *distinguishable* error for a
blob removed by retention — the code anticipated a retention pass that was never
wired.

That is arguably more urgent than the delivery path, because it is a live
property of a deployed system rather than a missing feature.

## The interface change

Three ways to let a backend receive blocks:

**A — an additive parameter (recommended)**

```python
async def complete(self, system: str, user: str, *,
                   content: list[dict] | None = None, ...) -> dict
```

Every existing call site is unchanged by the default. Each backend opts in.

The rule that matters: **a text-only backend must raise, never ignore.** A
default-ignore implementation reintroduces exactly the silent drop
`content_blocks()` refuses, and it would be invisible — the model answers
confidently about an image it never received.

**B — widen `user` to `str | list[dict]`.** Closer to the provider APIs, but
every backend must now handle both shapes, and a backend that forgets gets a
type error at runtime rather than at the boundary.

**C — a separate `complete_multimodal()`.** Keeps the hot path untouched, at the
cost of two methods that must not drift — the same duplication risk
`handle`/`handle_async` already carries in `compat_endpoint`.

Recommend **A**.

## Where refusal happens

An attachment can be refused at three points, and the answer is "more than one,
for different reasons":

1. **Upload** — validation only (type, size, decodability). Not capability:
   the role is not known yet, and the backend can change between upload and
   send.
2. **Compose (UI)** — `GET /api/attachments/capability` already exists so the
   browser can warn *before* the user writes a prompt. A warning, not a block.
3. **Dispatch** — the enforcement point. The role is bound, the backend is
   known, and `content_blocks()` raises. This is the one that must never be
   skipped.

## Scope

**The web surface only.** A terminal cannot display an image, which
`attachments.py` says in its first line. This is the one place where surface
parity (proposal `20260830-webgui-tui-alignment`) does *not* demand both — but
note the asymmetry is in **presentation**, not capability: the TUI must still
show that an attachment exists and what its reference is, because an operator
watching a thread needs to know an image was part of it.

## Open questions

1. **Does an image count against the context budget?** RP-04's packer measures
   text. An image is tokens — a large one can be thousands. Today it would pass
   through the budget unmeasured, which is a hole in a mechanism whose whole
   purpose is that nothing reaches the model uncounted.
2. **Who runs retention, and on what schedule?** `prune()` needs a caller. The
   session retention policy is the stated model; whether that means a timer, a
   CLI command, or an agent-side sweep is undecided.
3. **Should `/v1/chat/completions` accept `image_url` blocks?** OpenAI's schema
   has them, and honouring them would make ACC genuinely drop-in for vision
   clients. It also widens the untrusted surface — the one HG-24 called "most
   likely to be pointed at by something the operator did not write" — so it
   should follow the web path rather than lead it.
4. **What does the durable record keep?** `records_for()` returns references,
   which is the right answer for the record. Whether the *episode* text says an
   image was present, so a later reader understands the answer, is not settled.
