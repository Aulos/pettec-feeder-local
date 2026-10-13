"""Feeder command checks, especially avoiding duplicate food dispensing."""

import asyncio
from datetime import datetime
from unittest.mock import AsyncMock

import pytest

from custom_components.pettec_local.feeder import FeederClient
from custom_components.pettec_local.protocol import encode_frame
from custom_components.pettec_local.xlink import XlinkError


def client_with_transport(monkeypatch, response=None, error=None):
    transport = AsyncMock()
    transport.mac = "aabbccddeeff"
    transport.send_pipe.return_value = response
    transport.send_pipe.side_effect = error
    monkeypatch.setattr(
        "custom_components.pettec_local.feeder.XlinkClient", lambda *args: transport
    )
    return FeederClient("192.0.2.1", 123456789), transport


async def test_clock_probe(monkeypatch):
    client, transport = client_with_transport(
        monkeypatch, encode_frame(0x83, bytes([26, 10, 13, 8, 0, 0, 2]))
    )
    assert await client.get_clock() == datetime(2026, 10, 13, 8)
    transport.send_pipe.assert_awaited_once_with(bytes.fromhex("57 ab ff ff 03 00 03"))


async def test_feed_success(monkeypatch):
    client, transport = client_with_transport(monkeypatch, encode_frame(0x8C, b"\x00"))
    await client.dispense(25)
    transport.send_pipe.assert_awaited_once_with(
        bytes.fromhex("57 ab ff ff 0c 02 00 19 27")
    )


async def test_feed_timeout_is_never_retried(monkeypatch):
    client, transport = client_with_transport(monkeypatch, error=asyncio.TimeoutError())
    with pytest.raises(asyncio.TimeoutError):
        await client.dispense(25)
    assert transport.send_pipe.await_count == 1


@pytest.mark.parametrize(
    "response",
    [
        encode_frame(0x8C, b"\xe6"),
        encode_frame(0x8C, b""),
        encode_frame(0x8C, b"\x01"),
        encode_frame(0x83, b"\x00"),
        bytes.fromhex("57 ab ff ff 8c 01 00 00"),
    ],
)
async def test_bad_feed_response_is_not_success_or_retried(monkeypatch, response):
    client, transport = client_with_transport(monkeypatch, response)
    with pytest.raises(XlinkError):
        await client.dispense(10)
    assert transport.send_pipe.await_count == 1


async def test_invalid_quantity_sends_nothing(monkeypatch):
    client, transport = client_with_transport(monkeypatch)
    with pytest.raises(ValueError):
        await client.dispense(201)
    transport.send_pipe.assert_not_awaited()
