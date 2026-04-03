"""Tests for NATSBackend — all infrastructure mocked."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import msgpack
import pytest

from acc.backends import BackendConnectionError
from acc.backends.signaling_nats import NATSBackend


@pytest.fixture()
def mock_nc():
    """Return a mock NATS client."""
    nc = MagicMock()
    nc.publish = AsyncMock()
    nc.subscribe = AsyncMock()
    nc.drain = AsyncMock()
    return nc


class TestNATSBackend:
    @pytest.mark.asyncio
    async def test_connect_success(self, mock_nc):
        with patch("nats.connect", AsyncMock(return_value=mock_nc)):
            backend = NATSBackend("nats://localhost:4222")
            await backend.connect()
            assert backend._nc is mock_nc

    @pytest.mark.asyncio
    async def test_connect_failure_raises_backend_connection_error(self):
        with patch("nats.connect", AsyncMock(side_effect=ConnectionRefusedError("refused"))):
            backend = NATSBackend("nats://localhost:4222")
            with pytest.raises(BackendConnectionError, match="refused"):
                await backend.connect()

    @pytest.mark.asyncio
    async def test_connect_chains_original_exception(self):
        original = ConnectionRefusedError("conn refused")
        with patch("nats.connect", AsyncMock(side_effect=original)):
            backend = NATSBackend("nats://localhost:4222")
            with pytest.raises(BackendConnectionError) as exc_info:
                await backend.connect()
            assert exc_info.value.__cause__ is original

    @pytest.mark.asyncio
    async def test_publish_serializes_as_msgpack(self, mock_nc):
        with patch("nats.connect", AsyncMock(return_value=mock_nc)):
            backend = NATSBackend("nats://localhost:4222")
            await backend.connect()
            payload = b"hello"
            await backend.publish("acc.sol.heartbeat", payload)

        mock_nc.publish.assert_called_once()
        call_args = mock_nc.publish.call_args
        subject, packed = call_args[0]
        assert subject == "acc.sol.heartbeat"
        # The payload bytes should be msgpack-encoded
        unpacked = msgpack.unpackb(packed, raw=False)
        assert unpacked == payload

    @pytest.mark.asyncio
    async def test_subscribe_deserializes_msgpack(self, mock_nc):
        received: list = []

        async def handler(data):
            received.append(data)

        with patch("nats.connect", AsyncMock(return_value=mock_nc)):
            backend = NATSBackend("nats://localhost:4222")
            await backend.connect()
            await backend.subscribe("acc.sol.>", handler)

        # Simulate inbound msgpack message
        registered_cb = mock_nc.subscribe.call_args[1]["cb"]
        raw_data = msgpack.packb(b"test-payload", use_bin_type=True)
        msg = MagicMock()
        msg.data = raw_data
        await registered_cb(msg)

        assert received == [b"test-payload"]

    @pytest.mark.asyncio
    async def test_close_drains_connection(self, mock_nc):
        with patch("nats.connect", AsyncMock(return_value=mock_nc)):
            backend = NATSBackend("nats://localhost:4222")
            await backend.connect()
            await backend.close()
        mock_nc.drain.assert_called_once()
        assert backend._nc is None

    @pytest.mark.asyncio
    async def test_publish_before_connect_raises(self):
        backend = NATSBackend("nats://localhost:4222")
        with pytest.raises(RuntimeError, match="connect"):
            await backend.publish("sub", b"data")

    @pytest.mark.asyncio
    async def test_subscribe_before_connect_raises(self):
        backend = NATSBackend("nats://localhost:4222")
        with pytest.raises(RuntimeError, match="connect"):
            await backend.subscribe("sub", lambda x: None)
