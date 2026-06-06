"""Phase F — A2A federation discovery cache tests."""

from __future__ import annotations

import asyncio
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from acc.a2a.federation import (
    FederationCache,
    PeerCardEntry,
    discover_peer_cards,
)


def _card(skill_ids: list[str]) -> dict[str, Any]:
    return {
        "name": "peer",
        "version": "1.0.0",
        "skills": [{"id": sid, "name": sid} for sid in skill_ids],
    }


# ---------------------------------------------------------------------------
# PeerCardEntry / FederationCache
# ---------------------------------------------------------------------------


def test_peer_entry_skill_ids_when_reachable():
    e = PeerCardEntry(
        collective_id="lyra",
        a2a_url="http://lyra:9000",
        card=_card(["data_engineer", "analyst"]),
        fetched_at_s=0.0,
    )
    assert e.is_reachable
    assert sorted(e.skill_ids()) == ["analyst", "data_engineer"]


def test_peer_entry_unreachable_has_empty_skills():
    e = PeerCardEntry(
        collective_id="lyra",
        a2a_url="http://lyra:9000",
        card=None,
        fetched_at_s=0.0,
        error="timed out",
    )
    assert not e.is_reachable
    assert e.skill_ids() == []


def test_cache_find_skill_returns_only_reachable_advertisers():
    cache = FederationCache(
        entries={
            "lyra": PeerCardEntry("lyra", "u1", _card(["data_engineer"]), 0.0),
            "vega": PeerCardEntry("vega", "u2", _card(["analyst"]), 0.0),
            "down": PeerCardEntry("down", "u3", None, 0.0, error="x"),
        }
    )
    de = cache.find_skill("data_engineer")
    assert [e.collective_id for e in de] == ["lyra"]
    assert cache.find_skill("missing") == []
    # Unreachable peer is NEVER returned
    assert all(e.is_reachable for e in cache.reachable_peers())


def test_cache_is_stale_when_empty_or_old():
    empty = FederationCache(entries={})
    assert empty.is_stale()
    fresh = FederationCache(
        entries={"x": PeerCardEntry("x", "u", _card([]), time.monotonic())},
        ttl_s=300.0,
    )
    assert not fresh.is_stale()
    old = FederationCache(
        entries={"x": PeerCardEntry("x", "u", _card([]), 0.0)},
        ttl_s=300.0,
    )
    assert old.is_stale(now_s=10_000.0)


# ---------------------------------------------------------------------------
# discover_peer_cards
# ---------------------------------------------------------------------------


def test_discover_empty_returns_empty_cache():
    cache = asyncio.run(discover_peer_cards({}))
    assert cache.entries == {}


class _FakeResponse:
    def __init__(self, status: int, payload: dict[str, Any]) -> None:
        self.status = status
        self._payload = payload

    async def json(self) -> dict[str, Any]:
        return self._payload

    async def __aenter__(self) -> "_FakeResponse":
        return self

    async def __aexit__(self, *_: Any) -> None:
        return None


class _FakeSession:
    def __init__(self, route: dict[str, _FakeResponse]) -> None:
        self._route = route
        self.calls: list[str] = []

    def get(self, url: str, **_: Any) -> _FakeResponse:
        self.calls.append(url)
        return self._route[url]

    async def close(self) -> None:
        return None


def test_discover_fans_out_and_records_status(monkeypatch):
    """Two peers; one returns a card, one returns 404 — both recorded."""
    session = _FakeSession({
        "http://lyra:9000/.well-known/agent-card.json":
            _FakeResponse(200, _card(["data_engineer"])),
        "http://down:9000/.well-known/agent-card.json":
            _FakeResponse(404, {}),
    })
    cache = asyncio.run(discover_peer_cards(
        {"lyra": "http://lyra:9000", "down": "http://down:9000"},
        session=session,
    ))
    assert set(cache.entries) == {"lyra", "down"}
    assert cache.entries["lyra"].is_reachable
    assert cache.entries["lyra"].skill_ids() == ["data_engineer"]
    assert not cache.entries["down"].is_reachable
    assert "404" in (cache.entries["down"].error or "")


def test_discover_skips_empty_urls():
    """An empty URL means 'peer not configured' — don't fetch."""
    session = _FakeSession({})
    cache = asyncio.run(discover_peer_cards(
        {"lyra": ""},
        session=session,
    ))
    assert cache.entries == {}
    assert session.calls == []
