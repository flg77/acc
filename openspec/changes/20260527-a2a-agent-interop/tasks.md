# Tasks — A2A interop (ACC ⇄ Kagenti mesh)

Pair: [`20260527-agentcard-discovery/`](../20260527-agentcard-discovery/).
Docs: [`docs/a2a-interop.md`](../../../docs/a2a-interop.md).

## Phase 1 — Agent Card generator (LANDED)
- [x] `acc/a2a/__init__.py` exposes `build_agent_card`.
- [x] `acc/a2a/card.py`: pure function `build_agent_card(role, *, role_label,
      collective_id, agent_id, base_url) -> dict`.  A2A v1 shape + `acc`
      vendor extension + `A2A_CARD_SCHEMA_VERSION` pinned constant.
- [x] Tests (`tests/test_a2a_card.py`, 20 tests).

## Phase 1b — `/.well-known/agent-card.json` endpoint (LANDED)
- [x] `acc/a2a/server.py`: `aiohttp` app, GET handler.
- [x] Lifecycle helpers `start_server` + env helpers `env_port`, `env_host`,
      `env_base_url`.
- [x] `acc.agent.Agent._maybe_start_a2a_server` env-gated via `ACC_A2A_PORT`;
      lazy import (no aiohttp at import time); clean runner cleanup in
      `run()` finally.  Skips for dormant workers / no role / no
      CognitiveCore / missing `acc[a2a]` extra.
- [x] `pyproject.toml` new optional `a2a` extra (aiohttp).
- [x] Tests for GET card cover content + ACC extension visibility.

## Phase 2 — JSON-RPC inbound, governance preserved (LANDED)
- [x] `acc/a2a/jsonrpc.py`: minimal JSON-RPC 2.0 helpers + standard error
      codes + custom `GOVERNANCE_BLOCKED (-32001)`.
- [x] `POST /` JSON-RPC `message/send` handler in `server.py`:
      - Translates to `task = {task_id, content, target_role=<this role>,
        source: "a2a"}`.
      - Calls `CognitiveCore.process_task(task, role)` — the SAME pipeline
        as NATS TASK_ASSIGN; Cat-A/B + oversight enforce structurally.
      - Blocked result → JSON-RPC `GOVERNANCE_BLOCKED` (-32001) + 403 +
        structured `error.data.blockReason`; never a silent bypass.
      - Standard JSON-RPC error codes for parse / invalid request / method
        not found / invalid params / internal error.
- [x] Tests (`tests/test_a2a_server.py`, 14 aiohttp-gated tests).

## Phase 3 — Outbound A2A client + transport resolver (LANDED)
- [x] `acc/a2a/client.py`:
      - `A2AClientError` with `.code` + `.data` + `.is_governance_blocked`.
      - `async call_peer(base_url, content, *, task_id=None, timeout=30.0,
        session=None) -> dict` — JSON-RPC `message/send`; raises on any
        failure (HTTP, JSON-RPC, timeout, connection refused).
      - `select_transport(*, deploy_mode, target_cid, peer_urls,
        prefer_a2a=True) -> "a2a"|"nats"` — pure decision matrix.
- [x] Tests: 7 pure `select_transport` tests (always run); 6 aiohttp-gated
      `call_peer` tests against in-process aiohttp app (success, taskId
      synth, JSON-RPC error code/data, `is_governance_blocked`, HTTP error,
      connection-refused).

## Phase 4 — Hub-as-gateway wiring (LANDED)
- [x] `AgentConfig.peer_a2a_urls: dict[str, str]` with env-friendly parser
      (`ACC_PEER_A2A_URLS='cid1=url1,cid2=url2'`).
- [x] `acc.a2a.client.try_a2a_delegation(target_cid, content, task_id,
      deploy_mode, peer_urls, ...)` — composition helper returning
      bridge-result dict OR `None` (fall back to NATS).
- [x] `acc.agent.Agent._maybe_delegate_via_a2a` (reads deploy_mode +
      peer_a2a_urls; gated by aiohttp availability).
- [x] `acc.agent.Agent._forward_bridge_result` extracted so the A2A and
      NATS paths converge on the same TASK_COMPLETE shape.
- [x] `_delegate_task` short-circuits via A2A first; falls back to NATS on
      transport failure; surfaces governance denials as blocked (no NATS
      fallback).
- [x] Tests: 7 `try_a2a_delegation` cases (incl. mode-aware fallthrough,
      success shape, reachability fallback, governance denial sticky).

## Phase 5 — SPIRE JWT-SVID card signing (LANDED)
- [x] `acc/a2a/signing.py`:
      - `sign_card(card, jwt_svid)` → envelope dict.
      - `verify_signed_card(envelope, *, issuer_key,
        expected_trust_domain, expected_audience=None, algorithms=None,
        leeway_s=30) -> card` — enforces signature, expiry, required claims,
        trust-domain prefix on `sub`, optional audience.
      - `CardVerificationError` distinct exception (sticky denial).
      - `spire_x5c_scheme(trust_domain)` for `authentication.schemes`.
      - `read_jwt_svid_file(path)` + `spiffe_id_trust_domain(spiffe_id)`
        helpers.
- [x] Server opt-in via `ACC_A2A_JWT_SVID_PATH` + `ACC_A2A_TRUST_DOMAIN`;
      SVID re-read on every GET so rotated creds always serve; unreadable
      file → log + serve unsigned (graceful degrade).
- [x] Card's `authentication.schemes` advertises `spire-jwt-svid` when
      signing is configured.
- [x] Tests (`tests/test_a2a_signing.py`, 14 tests using `cryptography` +
      `pyjwt`, both already core deps).

## Phase 6 — deferred follow-ups (out of scope here)
- [ ] Operator-side per-role Kubernetes `Service` to expose the in-pod A2A
      port externally (in-pod server is functional today).
- [ ] AgentCard CRD discovery on the *outbound* side (peer URLs from Kagenti
      CRD instead of `peer_a2a_urls`).
- [ ] Peer-side card verification wired into `call_peer` (the
      `verify_signed_card` helper is shipped + tested; the *call site* still
      trusts the configured URL — a few lines to wire).
- [ ] TLS at the HTTP layer (SPIRE-issued certs on the agent's listener).
- [ ] ACC-9 bridge deprecation gate (rhoai-only; edge/standalone retain it
      indefinitely — see vault scope note).

## Gate before promote (LANDED)
- [x] All A2A test suites pass (42 passed, 10 aiohttp-gated skipped where
      the `a2a` extra is absent; CI runs them).
- [x] `acc.agent` imports cleanly without aiohttp (lazy import gate).
- [x] Docs published (`docs/a2a-interop.md`).
- [x] Cross-linked with the paired `agentcard-discovery` change.
