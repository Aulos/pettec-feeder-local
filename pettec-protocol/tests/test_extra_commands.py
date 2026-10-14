"""Recovered APK vectors, bounded parsing and single-shot optional commands."""

import asyncio

import pytest
from test_feeder import client_with_transport

from pettec_xlink import FeedingDetails, FeedingRecord, XlinkError
from pettec_xlink.protocol import decode_frame, encode_frame


@pytest.mark.parametrize("raw,value", [(b"\x05\xdc", 1500), (b"\xff\xff", -1)])
async def test_remaining_food_is_signed_big_endian_grams(monkeypatch, raw, value):
    client, transport = client_with_transport(monkeypatch, encode_frame(0x90, raw))
    assert await client.get_remaining_food() == value
    transport.send_pipe.assert_awaited_once_with(bytes.fromhex("57 ab ff ff 10 00 10"))


@pytest.mark.parametrize("raw,value", [(b"\x00", 0), (b"\x64", 100), (b"\x65", None)])
async def test_battery_percentage_and_charging(monkeypatch, raw, value):
    client, transport = client_with_transport(monkeypatch, encode_frame(0x91, raw))
    assert await client.get_remaining_battery() == value
    transport.send_pipe.assert_awaited_once_with(bytes.fromhex("57 ab ff ff 11 00 11"))


@pytest.mark.parametrize(
    "reply,movement", [(b"\x01", "retracting"), (b"\x02", "extending")]
)
async def test_tray_button_uses_apk_payload_once(monkeypatch, reply, movement):
    client, transport = client_with_transport(monkeypatch, encode_frame(0x92, reply))
    assert await client.control_tray() == movement
    transport.send_pipe.assert_awaited_once_with(
        bytes.fromhex("57 ab ff ff 12 01 02 15")
    )


async def test_selected_history_preserves_header_and_decodes_records(monkeypatch):
    raw = bytes.fromhex("07 08 1e 00 19 00 13 00 00 28 01")
    client, transport = client_with_transport(monkeypatch, encode_frame(0xA3, raw))
    assert await client.get_feeder_details(3) == FeedingDetails(
        7, (FeedingRecord(8, 30, 25, 0), FeedingRecord(19, 0, 40, 1))
    )
    transport.send_pipe.assert_awaited_once_with(
        bytes.fromhex("57 ab ff ff 23 01 03 27")
    )


@pytest.mark.parametrize("selector", [-1, 256, True, 1.5, "1"])
async def test_invalid_history_selector_never_sends(monkeypatch, selector):
    client, transport = client_with_transport(monkeypatch)
    with pytest.raises(ValueError):
        await client.get_feeder_details(selector)
    transport.send_pipe.assert_not_awaited()


@pytest.mark.parametrize("value,reply", [(10, b"\x0a"), (60, b"\x3c"), (None, b"\x64")])
async def test_tray_interval_read_query_and_disabled_sentinel(
    monkeypatch, value, reply
):
    client, transport = client_with_transport(monkeypatch, encode_frame(0xA4, reply))
    assert await client.get_tray_retraction_interval() == value
    transport.send_pipe.assert_awaited_once_with(
        bytes.fromhex("57 ab ff ff 24 01 ff 24")
    )


@pytest.mark.parametrize("value,code", [(20, 20), (None, 100)])
async def test_set_interval_writes_once_then_verifies(monkeypatch, value, code):
    client, transport = client_with_transport(monkeypatch)
    transport.send_pipe.side_effect = [
        encode_frame(0xA4, b"\x00"),
        encode_frame(0xA4, bytes([code])),
    ]
    assert await client.set_tray_retraction_interval(value) == value
    frames = [decode_frame(c.args[0]) for c in transport.send_pipe.await_args_list]
    assert [(f.command, f.payload) for f in frames] == [
        (0x24, bytes([code])),
        (0x24, b"\xff"),
    ]


@pytest.mark.parametrize("value", [0, 9, 11, 65, 100, 255, True, 20.0])
async def test_invalid_interval_never_sends(monkeypatch, value):
    client, transport = client_with_transport(monkeypatch)
    with pytest.raises(ValueError):
        await client.set_tray_retraction_interval(value)
    transport.send_pipe.assert_not_awaited()


async def test_tare_uses_zero_payload_and_returns_readback(monkeypatch):
    client, transport = client_with_transport(monkeypatch)
    transport.send_pipe.side_effect = [
        encode_frame(0xA6, b"\x00"),
        encode_frame(0x90, b"\x00\x00"),
    ]
    assert await client.reset_feeding_weight() == 0
    assert transport.send_pipe.await_args_list[0].args[0] == bytes.fromhex(
        "57 ab ff ff 26 02 00 00 28"
    )
    assert decode_frame(transport.send_pipe.await_args_list[1].args[0]).command == 0x10


@pytest.mark.parametrize(
    "method,command,args",
    [
        ("control_tray", 0x92, ()),
        ("set_tray_retraction_interval", 0xA4, (20,)),
        ("reset_feeding_weight", 0xA6, ()),
    ],
)
@pytest.mark.parametrize("failure", [TimeoutError(), b"", b"\xe6", b"\x00\x00"])
async def test_uncertain_writes_never_retry(
    monkeypatch, method, command, args, failure
):
    client, transport = client_with_transport(monkeypatch)
    if isinstance(failure, Exception):
        transport.send_pipe.side_effect = failure
    else:
        transport.send_pipe.return_value = encode_frame(command, failure)
    with pytest.raises((XlinkError, TimeoutError)):
        await getattr(client, method)(*args)
    assert transport.send_pipe.await_count == 1


@pytest.mark.parametrize(
    "method,command,payload,args",
    [
        ("get_remaining_food", 0x90, b"\x00", ()),
        ("get_remaining_battery", 0x91, b"\xff", ()),
        ("get_tray_retraction_interval", 0xA4, b"\xff", ()),
        ("get_feeder_details", 0xA3, b"", (1,)),
        ("get_feeder_details", 0xA3, b"\x00\x01", (1,)),
        ("get_feeder_details", 0xA3, bytes.fromhex("00 18 00 00 01 00"), (1,)),
    ],
)
async def test_malformed_optional_reads_are_rejected(
    monkeypatch, method, command, payload, args
):
    client, transport = client_with_transport(
        monkeypatch, encode_frame(command, payload)
    )
    with pytest.raises(XlinkError):
        await getattr(client, method)(*args)
    assert transport.send_pipe.await_count == 1


async def test_interval_readback_mismatch_is_not_success(monkeypatch):
    client, transport = client_with_transport(monkeypatch)
    transport.send_pipe.side_effect = [
        encode_frame(0xA4, b"\x00"),
        encode_frame(0xA4, b"\x0f"),
    ]
    with pytest.raises(XlinkError, match="readback differs"):
        await client.set_tray_retraction_interval(20)
    assert transport.send_pipe.await_count == 2


async def test_simultaneous_interval_edits_keep_write_and_readback_together(
    monkeypatch,
):
    client, transport = client_with_transport(monkeypatch)
    current = 15
    calls = []

    async def reply(packet):
        nonlocal current
        payload = decode_frame(packet).payload
        calls.append(payload)
        await asyncio.sleep(0)
        if payload == b"\xff":
            return encode_frame(0xA4, bytes([current]))
        current = payload[0]
        return encode_frame(0xA4, b"\x00")

    transport.send_pipe.side_effect = reply
    assert await asyncio.gather(
        client.set_tray_retraction_interval(20), client.set_tray_retraction_interval(25)
    ) == [20, 25]
    assert calls == [b"\x14", b"\xff", b"\x19", b"\xff"]
