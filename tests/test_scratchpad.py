"""Tests for acc.scratchpad.ScratchpadClient (ACC-10)."""

from __future__ import annotations

import asyncio
import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from acc.scratchpad import ScratchpadClient, ScratchpadAccessError


def make_redis():
    """Return a minimal async mock Redis client."""
    redis = AsyncMock()
    store: dict[str, bytes] = {}

    async def _set(key, value):
        store[key] = value.encode() if isinstance(value, str) else value

    async def _get(key):
        return store.get(key)

    async def _delete(*keys):
        count = 0
        for k in keys:
            k_str = k.decode() if isinstance(k, bytes) else k
            if k_str in store:
                del store[k_str]
                count += 1
        return count

    async def _expireat(key, ts):
        pass  # no-op in tests

    async def _scan(cursor, match=None, count=100):
        import fnmatch
        all_keys = list(store.keys())
        if match:
            pattern = match.replace("*", "**")
            matched = [k.encode() for k in all_keys if fnmatch.fnmatch(k, match)]
        else:
            matched = [k.encode() for k in all_keys]
        return 0, matched

    redis.set = AsyncMock(side_effect=_set)
    redis.get = AsyncMock(side_effect=_get)
    redis.delete = AsyncMock(side_effect=_delete)
    redis.expireat = AsyncMock(side_effect=_expireat)
    redis.scan = AsyncMock(side_effect=_scan)
    redis._store = store
    return redis


@pytest.fixture
def redis():
    return make_redis()


@pytest.fixture
def sc(redis):
    return ScratchpadClient(redis, "sol-01", "analyst")


class TestScratchpadSet:
    @pytest.mark.asyncio
    async def test_set_and_get_own(self, sc):
        await sc.set("plan-1", "result", "hello")
        val = await sc.get_own("plan-1", "result")
        assert val == "hello"

    @pytest.mark.asyncio
    async def test_set_json_and_get_json(self, sc):
        data = {"tokens": [1, 2, 3], "count": 3}
        await sc.set_json("plan-1", "data", data)
        result = await sc.get_json("plan-1", "analyst", "data")
        assert result == data

    @pytest.mark.asyncio
    async def test_set_enforces_own_role(self, sc):
        with pytest.raises(ScratchpadAccessError):
            await sc.set("plan-1", "key", "val", role="synthesizer")

    @pytest.mark.asyncio
    async def test_cross_role_read(self, redis):
        """analyst can read synthesizer namespace."""
        analyst_sc = ScratchpadClient(redis, "sol-01", "analyst")
        synth_sc = ScratchpadClient(redis, "sol-01", "synthesizer")
        await synth_sc.set("plan-1", "summary", "The answer is 42")
        val = await analyst_sc.get("plan-1", "synthesizer", "summary")
        assert val == "The answer is 42"


class TestScratchpadPlanTTL:
    def test_register_plan_clamps_to_max(self):
        sc = ScratchpadClient(MagicMock(), "sol-01", "analyst", max_ttl_s=100)
        sc.register_plan("plan-1", ttl_s=9999)
        expiry = sc._plan_expiry["plan-1"]
        assert expiry <= int(time.time()) + 100 + 2  # +2 for timing slack

    def test_register_plan_within_max(self):
        sc = ScratchpadClient(MagicMock(), "sol-01", "analyst", max_ttl_s=3600)
        sc.register_plan("plan-1", ttl_s=60)
        expiry = sc._plan_expiry["plan-1"]
        assert expiry <= int(time.time()) + 62

    def test_expiry_defaults_to_max_when_not_registered(self):
        sc = ScratchpadClient(MagicMock(), "sol-01", "analyst", max_ttl_s=300)
        expiry = sc._expiry_for("unknown-plan")
        assert expiry <= int(time.time()) + 302


class TestScratchpadFlush:
    @pytest.mark.asyncio
    async def test_flush_deletes_all_plan_keys(self, redis):
        sc = ScratchpadClient(redis, "sol-01", "analyst")
        await sc.set("plan-1", "a", "v1")
        await sc.set("plan-1", "b", "v2")
        # Add a key for a different plan — should not be deleted
        sc2 = ScratchpadClient(redis, "sol-01", "analyst")
        await sc2.set("plan-2", "c", "v3")

        deleted = await sc.flush_plan("plan-1")
        assert deleted == 2
        assert await sc.get_own("plan-1", "a") is None
        assert await sc.get_own("plan-1", "b") is None
        # plan-2 key untouched
        assert await sc2.get_own("plan-2", "c") == "v3"

    @pytest.mark.asyncio
    async def test_flush_removes_plan_expiry_cache(self, redis):
        sc = ScratchpadClient(redis, "sol-01", "analyst")
        sc.register_plan("plan-1", ttl_s=60)
        assert "plan-1" in sc._plan_expiry
        await sc.flush_plan("plan-1")
        assert "plan-1" not in sc._plan_expiry

    @pytest.mark.asyncio
    async def test_flush_no_redis_returns_zero(self):
        sc = ScratchpadClient(None, "sol-01", "analyst")
        assert await sc.flush_plan("plan-1") == 0


class TestScratchpadDelete:
    @pytest.mark.asyncio
    async def test_delete_own_key(self, sc):
        await sc.set("plan-1", "temp", "data")
        await sc.delete("plan-1", "temp")
        assert await sc.get_own("plan-1", "temp") is None


class TestScratchpadGetAllForPlan:
    @pytest.mark.asyncio
    async def test_get_all_returns_all_roles(self, redis):
        a = ScratchpadClient(redis, "sol-01", "analyst")
        s = ScratchpadClient(redis, "sol-01", "synthesizer")
        await a.set("plan-1", "result", "r1")
        await s.set("plan-1", "summary", "s1")
        all_data = await a.get_all_for_plan("plan-1")
        assert "analyst/result" in all_data
        assert "synthesizer/summary" in all_data

    @pytest.mark.asyncio
    async def test_get_all_no_redis_returns_empty(self):
        sc = ScratchpadClient(None, "sol-01", "analyst")
        assert await sc.get_all_for_plan("plan-1") == {}
