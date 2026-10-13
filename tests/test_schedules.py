"""Stored schedule vectors and preservation across uncertain writes."""

import asyncio

import pytest
from test_feeder import client_with_transport

from custom_components.pettec_local.feeder import ScheduleChangedError
from custom_components.pettec_local.protocol import (
    ProtocolError,
    ScheduleSlot,
    decode_frame,
    decode_schedules,
    encode_frame,
    encode_schedules,
)
from custom_components.pettec_local.xlink import XlinkError

# Literal records: Mon/Wed/Fri at 08:30, Sun at 19:00, and disabled slots.
# The final slot preserves the stock app's high weekday bit and a wide quantity.
RAW = bytes.fromhex(
    "01 08 1e 00 19 15 01 13 00 00 28 40 "
    "00 00 00 00 00 00 00 00 00 00 00 00 00 0c 00 01 2c ff"
)
SLOTS = decode_schedules(RAW)
NEW = ScheduleSlot(True, 9, 15, 30, 0x7F)


def test_schedule_vectors_and_weekday_bit_order():
    assert SLOTS[0] == ScheduleSlot(True, 8, 30, 25, 0b0010101)
    assert SLOTS[1] == ScheduleSlot(True, 19, 0, 40, 0b1000000)
    assert SLOTS[4].quantity == 300
    assert encode_schedules(SLOTS) == RAW
    assert NEW.to_bytes() == bytes.fromhex("01 09 0f 00 1e 7f")


@pytest.mark.parametrize("raw", [b"", RAW[:-1], RAW + b"\x00", b"\x02" + RAW[1:]])
def test_reject_incomplete_or_unknown_schedule_layout(raw):
    with pytest.raises(ProtocolError):
        decode_schedules(raw)


@pytest.mark.parametrize(
    "values",
    [
        (True, 24, 0, 10, 1),
        (True, 8, 60, 10, 1),
        (True, 8, 0, -1, 1),
        (True, 8, 0, 10, 256),
        (1, 8, 0, 10, 1),
    ],
)
def test_reject_invalid_schedule_fields(values):
    with pytest.raises(ProtocolError):
        ScheduleSlot(*values)


async def test_schedule_read_uses_recovered_opcode(monkeypatch):
    client, transport = client_with_transport(monkeypatch, encode_frame(0x85, RAW))
    assert await client.get_schedules() == SLOTS
    transport.send_pipe.assert_awaited_once_with(bytes.fromhex("57 ab ff ff 05 00 05"))


async def test_update_preserves_other_slots_and_verifies_all_five(monkeypatch):
    updated = (NEW, *SLOTS[1:])
    wanted = NEW.to_bytes() + RAW[6:]
    client, transport = client_with_transport(monkeypatch)
    transport.send_pipe.side_effect = [
        encode_frame(0x85, RAW),
        encode_frame(0x86, b"\x00"),
        encode_frame(0x85, wanted),
    ]
    assert await client.update_schedule(1, NEW, expected=SLOTS[0]) == updated
    packets = [
        decode_frame(call.args[0]) for call in transport.send_pipe.await_args_list
    ]
    assert [packet.command for packet in packets] == [5, 6, 5]
    assert packets[1].payload == wanted
    assert packets[1].payload[6:] == RAW[6:]


async def test_disable_slot_preserves_its_details(monkeypatch):
    old = SLOTS[0]
    disabled = ScheduleSlot(False, old.hour, old.minute, old.quantity, old.weekdays)
    wanted = b"\x00" + RAW[1:]
    client, transport = client_with_transport(monkeypatch)
    transport.send_pipe.side_effect = [
        encode_frame(0x85, RAW),
        encode_frame(0x86, b"\x00"),
        encode_frame(0x85, wanted),
    ]
    actual = await client.update_schedule(1, disabled, expected=old)
    assert not actual[0].enabled
    assert encode_schedules(actual) == wanted


@pytest.mark.parametrize("slot", [0, 6, True, 1.5])
async def test_invalid_slot_does_not_connect_or_write(monkeypatch, slot):
    client, transport = client_with_transport(monkeypatch)
    with pytest.raises(ValueError):
        await client.update_schedule(slot, NEW, expected=SLOTS[0])
    transport.connect.assert_not_awaited()
    transport.send_pipe.assert_not_awaited()


@pytest.mark.parametrize("quantity,days", [(0, 1), (201, 1), (10, 0), (10, 128)])
async def test_invalid_active_schedule_sends_nothing(monkeypatch, quantity, days):
    client, transport = client_with_transport(monkeypatch)
    with pytest.raises(ValueError):
        await client.update_schedule(
            1, ScheduleSlot(True, 8, 0, quantity, days), expected=SLOTS[0]
        )
    transport.send_pipe.assert_not_awaited()


async def test_stale_selected_slot_is_not_overwritten(monkeypatch):
    client, transport = client_with_transport(monkeypatch, encode_frame(0x85, RAW))
    with pytest.raises(ScheduleChangedError):
        await client.update_schedule(1, NEW, expected=SLOTS[1])
    assert transport.send_pipe.await_count == 1


async def test_already_applied_change_is_read_without_rewriting(monkeypatch):
    wanted = NEW.to_bytes() + RAW[6:]
    client, transport = client_with_transport(monkeypatch, encode_frame(0x85, wanted))
    assert (await client.update_schedule(1, NEW, expected=SLOTS[0]))[0] == NEW
    assert transport.send_pipe.await_count == 1


@pytest.mark.parametrize(
    "failure",
    [
        TimeoutError,
        encode_frame(0x86, b"\x01"),
        encode_frame(0x86, b""),
        encode_frame(0x86, b"\x00\x00"),
    ],
)
async def test_failed_write_is_not_retried(monkeypatch, failure):
    client, transport = client_with_transport(monkeypatch)
    transport.send_pipe.side_effect = [encode_frame(0x85, RAW), failure]
    with pytest.raises((XlinkError, TimeoutError)):
        await client.update_schedule(1, NEW, expected=SLOTS[0])
    assert transport.send_pipe.await_count == 2


async def test_readback_mismatch_is_not_reported_as_success(monkeypatch):
    client, transport = client_with_transport(monkeypatch)
    transport.send_pipe.side_effect = [
        encode_frame(0x85, RAW),
        encode_frame(0x86, b"\x00"),
        encode_frame(0x85, RAW),
    ]
    with pytest.raises(XlinkError, match="readback differs"):
        await client.update_schedule(1, NEW, expected=SLOTS[0])
    assert transport.send_pipe.await_count == 3


async def test_malformed_initial_read_prevents_any_write(monkeypatch):
    client, transport = client_with_transport(monkeypatch, encode_frame(0x85, RAW[:-1]))
    with pytest.raises(XlinkError):
        await client.update_schedule(1, NEW, expected=SLOTS[0])
    assert transport.send_pipe.await_count == 1


async def test_concurrent_edits_preserve_each_other(monkeypatch):
    client, transport = client_with_transport(monkeypatch)
    stored = RAW

    async def respond(raw):
        nonlocal stored
        await asyncio.sleep(0)
        request = decode_frame(raw)
        if request.command == 5:
            return encode_frame(0x85, stored)
        assert request.command == 6
        stored = request.payload
        return encode_frame(0x86, b"\x00")

    transport.send_pipe.side_effect = respond
    second = ScheduleSlot(True, 18, 0, 20, 1)
    await asyncio.gather(
        client.update_schedule(1, NEW, expected=SLOTS[0]),
        client.update_schedule(2, second, expected=SLOTS[1]),
    )
    assert decode_schedules(stored) == (NEW, second, *SLOTS[2:])
