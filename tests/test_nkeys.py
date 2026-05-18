"""Unit tests for NATS NKey generation + the runtime connect path
(proposal 013 PR-5).

A full publish/subscribe e2e needs a real NATS server with a rendered
nats.conf — that lives in ``tests/integration/test_nkey_e2e.py`` and
the operator's manual acceptance step.  These tests cover the
crypto-level + connect-path behaviour CI can run unattended.
"""

from __future__ import annotations

import asyncio
import base64

import pytest

from acc import nkeys
from acc.backends import BackendConnectionError
from acc.backends.signaling_nats import NATSBackend


def _decode_nkey(nk: str) -> tuple[bytes, int]:
    pad = "=" * ((8 - len(nk) % 8) % 8)
    raw = base64.b32decode(nk + pad)
    return raw[:-2], int.from_bytes(raw[-2:], "little")


class TestNKeyGeneration:
    def test_user_nkey_prefixes(self):
        seed, public = nkeys.generate_user_nkey()
        assert seed.startswith("SU")   # Seed + User role
        assert public.startswith("U")  # User public

    def test_crc_round_trips(self):
        seed, public = nkeys.generate_user_nkey()
        for nk in (seed, public):
            body, crc = _decode_nkey(nk)
            assert nkeys._crc16(body) == crc

    def test_keys_are_unique(self):
        seeds = {nkeys.generate_user_nkey()[0] for _ in range(20)}
        assert len(seeds) == 20

    def test_identity_keys_cover_all_eight(self):
        keys = nkeys.generate_identity_keys()
        assert set(keys) == set(nkeys.NKEY_IDENTITIES)
        assert len(nkeys.NKEY_IDENTITIES) == 8
        for ident, pair in keys.items():
            assert pair["seed"].startswith("SU")
            assert pair["public"].startswith("U")


class TestAuthorizationRendering:
    def test_block_lists_every_identity(self):
        keys = nkeys.generate_identity_keys()
        pubs = {i: keys[i]["public"] for i in nkeys.NKEY_IDENTITIES}
        block = nkeys.render_authorization_block(pubs)
        assert block.startswith("authorization {")
        for identity in nkeys.NKEY_IDENTITIES:
            assert f"# {identity}" in block
            assert pubs[identity] in block
        assert block.count("nkey:") == 8

    def test_partial_key_set_renders_subset(self):
        keys = nkeys.generate_identity_keys()
        pubs = {"arbiter": keys["arbiter"]["public"]}
        block = nkeys.render_authorization_block(pubs)
        assert block.count("nkey:") == 1
        assert "# arbiter" in block

    def test_block_carries_permission_globs(self):
        keys = nkeys.generate_identity_keys()
        pubs = {i: keys[i]["public"] for i in nkeys.NKEY_IDENTITIES}
        block = nkeys.render_authorization_block(pubs)
        # arbiter is the only publisher of the control subjects.
        assert "acc.*.task.assign" in block
        assert "acc.*.plan.*" in block


class TestConnectPath:
    """NATSBackend threads (or omits) the NKey seed correctly."""

    def test_missing_seed_fails_closed(self, tmp_path):
        backend = NATSBackend(
            "nats://localhost:4222",
            nkey_seed_path=str(tmp_path / "does-not-exist"),
        )
        with pytest.raises(BackendConnectionError, match="seed file not found"):
            asyncio.run(backend.connect())

    def test_no_seed_connects_credential_less(self, monkeypatch):
        captured: dict = {}

        async def fake_connect(url, **opts):
            captured["url"] = url
            captured["opts"] = opts
            return object()

        import acc.backends.signaling_nats as mod
        monkeypatch.setattr(mod.nats, "connect", fake_connect)

        backend = NATSBackend("nats://localhost:4222")
        asyncio.run(backend.connect())
        assert captured["opts"] == {}  # no nkeys_seed → legacy path

    def test_seed_present_is_threaded(self, monkeypatch, tmp_path):
        seed_file = tmp_path / "seed-arbiter"
        seed, _ = nkeys.generate_user_nkey()
        seed_file.write_text(seed, encoding="ascii")

        captured: dict = {}

        async def fake_connect(url, **opts):
            captured["opts"] = opts
            return object()

        import acc.backends.signaling_nats as mod
        monkeypatch.setattr(mod.nats, "connect", fake_connect)

        backend = NATSBackend(
            "nats://localhost:4222", nkey_seed_path=str(seed_file),
        )
        asyncio.run(backend.connect())
        assert captured["opts"]["nkeys_seed"] == str(seed_file)
