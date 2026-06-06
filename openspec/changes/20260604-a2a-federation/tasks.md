# Tasks — A2A federation (Phase F)

## Phase F.1 — Discovery cache (this PR)

- [x] `acc/a2a/federation.py` — PeerCardEntry, FederationCache,
      discover_peer_cards
- [x] Export from `acc.a2a` package
- [x] Tests — 7 hermetic cases (no aiohttp dep when fake session)

## Phase F.2 — Agent wire-up (next PR)

- [ ] `Agent._on_startup` calls `discover_peer_cards` when
      `peer_a2a_urls` is non-empty
- [ ] Background refresh task tied to `cache.ttl_s`
- [ ] Atomically swap `self._federation_cache` on each refresh

## Phase F.3 — Orchestrator integration (next PR)

- [ ] Orchestrator's `[DELEGATE:skill:reason]` consults
      `cache.find_skill()` when LLM emits a skill name instead of CID
- [ ] Failover to NATS bridge when peer is unreachable
- [ ] Cat-A/B/C governance still enforced per-peer
