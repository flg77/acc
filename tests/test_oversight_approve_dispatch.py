"""Approving a queued Assistant proposal must actually dispatch it.

Regression guard for a silent prod-only dead end found live on lighthouse.

In prod (``ACC_OPERATOR_MODE`` unset or ``prod``) an INFUSE proposal is never
auto-executed — ``decide_dispatch`` routes it to the oversight queue so a human
approves installing code. The agent caches the proposal in Redis under the
``oversight_id`` and, on OVERSIGHT_DECISION APPROVE, looks it up and dispatches.

Both the cache write and the lookup read ``self.backends.working_memory`` — an
attribute that does not exist on the backends bundle (``llm`` / ``metrics`` /
``signaling`` / ``vector``) and that nothing ever assigns. ``getattr(..., None)``
made it ``None`` every time, so:

* the proposal was never cached, and
* approval found nothing and no-opped.

The operator could approve an infusion and **nothing happened** — no install,
no error. Dev mode hid it entirely (INFUSE auto-executes there and never
reaches the queue branch), so it only bit in prod, where the gate matters.

These tests pin the contract at the seam that broke: the attribute the agent
reads must be the client that actually exists.
"""

from __future__ import annotations

import inspect

import acc.agent as agent_mod
from acc.assistant_proposal import (
    DISPATCH_EXECUTE,
    DISPATCH_QUEUE,
    PROPOSAL_INFUSE,
    decide_dispatch,
)


class TestBackendsHasNoWorkingMemory:
    """The root cause: the attribute the old code read never existed."""

    def test_backends_bundle_lacks_working_memory(self):
        from acc.config import build_backends, load_config
        import os
        from pathlib import Path

        root = Path(__file__).resolve().parents[1]
        cfg = root / "acc-config.yaml"
        tmpl = root / "acc-config.yaml.example"
        created = False
        if not cfg.is_file() and tmpl.is_file():
            cfg.write_text(tmpl.read_text(encoding="utf-8"), encoding="utf-8")
            created = True
        try:
            backends = build_backends(load_config())
        finally:
            if created:
                os.unlink(cfg)
        assert not hasattr(backends, "working_memory"), (
            "backends grew a working_memory attribute — if it is now a REAL "
            "async client, revisit acc/agent.py: those sites use the sync "
            "client without await"
        )

    def test_agent_never_reads_working_memory_off_backends(self):
        """The exact expression that silently yielded None."""
        src = inspect.getsource(agent_mod)
        assert 'self.backends, "working_memory"' not in src, (
            "acc/agent.py reads backends.working_memory again — it does not "
            "exist, so getattr() returns None and the oversight approve path "
            "silently no-ops. Use self._redis."
        )

    def test_proposal_cache_sites_use_the_real_client(self):
        src = inspect.getsource(agent_mod)
        # the cache key is the seam; every site touching it must be reachable
        assert 'assistant_proposal:{oversight_id}' in src
        assert src.count("redis = self._redis") >= 3, (
            "expected the proposal cache write/read/delete to use self._redis"
        )


class TestSyncClientNotAwaited:
    """`_build_redis_client` returns a SYNC client.

    RoleStore and CognitiveCore already call it without ``await``; awaiting it
    would raise ``TypeError: object bytes can't be used in 'await'``.
    """

    def test_no_awaited_redis_calls_in_agent(self):
        src = inspect.getsource(agent_mod)
        for bad in ("await redis.get(", "await redis.set(",
                    "await redis.setex(", "await redis.delete("):
            assert bad not in src, (
                f"{bad!r} awaits the SYNC redis client from "
                "_build_redis_client — that raises at runtime"
            )

    def test_builder_returns_the_sync_client(self):
        src = inspect.getsource(agent_mod._build_redis_client)
        assert "redis_lib.from_url" in src
        assert "asyncio" not in src, (
            "builder switched to redis.asyncio — the call sites must regain "
            "their awaits"
        )


class TestInfuseStillRequiresApproval:
    """The fix must not turn the governance gate into auto-execute."""

    def test_infuse_queues_in_prod_even_in_auto(self):
        assert decide_dispatch("AUTO", PROPOSAL_INFUSE, operator_mode="prod") == DISPATCH_QUEUE

    def test_infuse_queues_when_operator_mode_unset(self):
        # _operator_mode_env() defaults to 'prod' — unset must NOT mean dev.
        assert decide_dispatch("AUTO", PROPOSAL_INFUSE, operator_mode=None) == DISPATCH_QUEUE

    def test_dev_mode_auto_executes_infuse_in_auto(self):
        assert decide_dispatch("AUTO", PROPOSAL_INFUSE, operator_mode="dev") == DISPATCH_EXECUTE

    def test_dev_mode_still_queues_outside_auto(self):
        assert decide_dispatch("ASK_PERMISSIONS", PROPOSAL_INFUSE, operator_mode="dev") == DISPATCH_QUEUE
