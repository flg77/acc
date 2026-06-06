# Phase F — A2A federation discovery cache

## Why

The five-phase A2A substrate (`20260527-a2a-agent-interop`) already
gives us per-peer call_peer + the rhoai/NATS transport resolver.
What's missing: a **fan-out discovery cache** the orchestrator can
consult to answer "which peer collective hosts a `data_engineer`
role?" without an extra agent-card round-trip per dispatch.

Phase F adds that cache. It's the last piece of the cross-collective
delegation story.

## What

`acc/a2a/federation.py`:

* `PeerCardEntry` — one peer's last-known A2A v1 card + fetched-at
  timestamp + optional error string.  Distinguishes
  "peer-down" from "peer-not-configured".
* `FederationCache` — in-memory dict keyed by `collective_id`.
  Exposes `get`, `find_skill`, `reachable_peers`, `is_stale`.
* `discover_peer_cards(peer_a2a_urls, ...)` — async fan-out fetch
  returning a fresh cache.

Hookup points (deferred — Phase F.2 / separate PR):

1. `Agent._on_startup` calls `discover_peer_cards(cfg.peer_a2a_urls)`
   when `peer_a2a_urls` is non-empty.
2. Periodic refresh task tied to `cache.ttl_s` (default 300 s).
3. Orchestrator's `[DELEGATE:cid:reason]` resolution consults
   `cache.find_skill()` to pick a target collective when the LLM emits
   a skill name instead of a collective ID.

## Non-goals

* Cross-collective oversight queue (governance stays per-collective).
* Push-based card change notification (poll-based for now).
* Persistent cache across restarts (in-memory is plenty at Phase F).

## Tests

7 unit tests in `tests/test_a2a_federation.py`, all hermetic (no
aiohttp dependency when a fake session is injected).
