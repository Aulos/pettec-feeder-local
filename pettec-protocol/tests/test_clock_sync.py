"""RTC byte order, weekday convention and single-write verification."""

from datetime import datetime, timezone

import pytest
from test_feeder import client_with_transport

from pettec_xlink.protocol import (
    clock_payload,
    decode_frame,
    encode_frame,
)
from pettec_xlink.xlink import XlinkError

NOW = datetime(2026, 10, 13, 14, 30, 45)
PAYLOAD = bytes.fromhex("1a 0a 0d 0e 1e 2d 02")


def test_clock_payload_matches_apk_layout():
    assert clock_payload(NOW) == PAYLOAD
    assert clock_payload(datetime(2026, 10, 18))[-1] == 0  # Sunday
    assert clock_payload(datetime(2026, 10, 19))[-1] == 1  # Monday


@pytest.mark.parametrize("weekday", [0, 7])
async def test_sunday_clock_read_from_real_feeder(monkeypatch, weekday):
    # Captured read-only reply on 2026-10-25: Sunday is reported as 07.
    payload = bytes.fromhex("1a 0a 19 12 32 1b") + bytes([weekday])
    client, transport = client_with_transport(monkeypatch, encode_frame(0x83, payload))
    assert await client.get_clock() == datetime(2026, 10, 25, 18, 50, 27)
    transport.send_pipe.assert_awaited_once_with(bytes.fromhex("57 ab ff ff 03 00 03"))


@pytest.mark.parametrize("weekday", [8, 255])
async def test_clock_read_rejects_invalid_weekday(monkeypatch, weekday):
    payload = bytes.fromhex("1a 0a 19 12 32 1b") + bytes([weekday])
    client, _ = client_with_transport(monkeypatch, encode_frame(0x83, payload))
    with pytest.raises(XlinkError, match="Invalid device clock payload"):
        await client.get_clock()


@pytest.mark.parametrize(
    "value",
    [
        datetime(1999, 1, 1),
        datetime(2100, 1, 1),
        NOW.replace(tzinfo=timezone.utc),
        "14:30",
    ],
)
async def test_invalid_clock_never_sends_a_command(monkeypatch, value):
    client, transport = client_with_transport(monkeypatch)
    with pytest.raises(ValueError):
        await client.set_clock(value)
    transport.send_pipe.assert_not_awaited()


async def test_set_clock_requires_ack_then_readback(monkeypatch):
    client, transport = client_with_transport(monkeypatch)
    transport.send_pipe.side_effect = [
        encode_frame(0x84, b"\x00"),
        encode_frame(0x83, PAYLOAD),
    ]
    assert await client.set_clock(NOW) == NOW
    requests = [
        decode_frame(call.args[0]) for call in transport.send_pipe.await_args_list
    ]
    assert [request.command for request in requests] == [4, 3]
    assert requests[0].payload == PAYLOAD


@pytest.mark.parametrize(
    "reply",
    [
        encode_frame(0x84, b""),
        encode_frame(0x84, b"\x01"),
        encode_frame(0x84, b"\x00\x00"),
        TimeoutError,
    ],
)
async def test_bad_clock_ack_is_not_retried(monkeypatch, reply):
    client, transport = client_with_transport(monkeypatch)
    transport.send_pipe.side_effect = [reply]
    with pytest.raises((XlinkError, TimeoutError)):
        await client.set_clock(NOW)
    assert transport.send_pipe.await_count == 1


async def test_clock_readback_mismatch_is_not_success(monkeypatch):
    client, transport = client_with_transport(monkeypatch)
    transport.send_pipe.side_effect = [
        encode_frame(0x84, b"\x00"),
        encode_frame(0x83, bytes.fromhex("1a 0a 0d 0c 1e 2d 02")),
    ]
    with pytest.raises(XlinkError, match="readback differs"):
        await client.set_clock(NOW)
    assert transport.send_pipe.await_count == 2


async def test_verification_allows_clock_ticks_during_transport(monkeypatch):
    client, transport = client_with_transport(monkeypatch)
    times = iter([100.0, 103.0])
    monkeypatch.setattr("pettec_xlink.feeder.monotonic", lambda: next(times))
    transport.send_pipe.side_effect = [
        encode_frame(0x84, b"\x00"),
        encode_frame(0x83, bytes.fromhex("1a 0a 0d 0e 1e 30 02")),
    ]
    assert await client.set_clock(NOW) == datetime(2026, 10, 13, 14, 30, 48)
