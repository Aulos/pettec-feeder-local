"""Golden packets and malformed inputs from the recovered inner protocol."""

from datetime import datetime

import pytest

from pettec_xlink.protocol import (
    ProtocolError,
    decode_clock,
    decode_frame,
    encode_frame,
    feed_payload,
)


def test_read_clock_golden_packet():
    raw = bytes.fromhex("57 ab ff ff 03 00 03")
    assert encode_frame(0x03) == raw
    assert decode_frame(raw).address == 65535
    assert decode_frame(raw).payload == b""


def test_feed_golden_packet_and_network_order():
    # Independent expected sum: 87 + 171 + 255 + 255 + 12 + 2 + 0 + 25 = 807.
    raw = bytes.fromhex("57 ab ff ff 0c 02 00 19 27")
    assert encode_frame(0x0C, feed_payload(25)) == raw
    assert decode_frame(raw).payload == b"\x00\x19"


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        bytes.fromhex("57 ab ff ff 03 00"),
        bytes.fromhex("56 ab ff ff 03 00 02"),
        bytes.fromhex("57 ab ff ff 03 01 03"),
        bytes.fromhex("57 ab ff ff 03 00 04"),
        bytes.fromhex("57 ab ff ff 03 00 03 00"),
    ],
)
def test_reject_malformed_packets(raw):
    with pytest.raises(ProtocolError):
        decode_frame(raw)


def test_clock_values_and_no_timezone_assumption():
    actual = decode_clock(bytes([26, 10, 13, 8, 30, 4, 2]))
    assert actual == datetime(2026, 10, 13, 8, 30, 4)
    assert actual.tzinfo is None


@pytest.mark.parametrize(
    "payload",
    [
        bytes([26, 2, 30, 8, 0, 0, 1]),
        bytes([26, 13, 1, 8, 0, 0, 1]),
        bytes([26, 1, 1, 24, 0, 0, 1]),
        bytes([26, 1, 1, 0, 0, 0, 7]),
        bytes([100, 1, 1, 0, 0, 0, 1]),
        b"\x00",
    ],
)
def test_reject_invalid_clock(payload):
    with pytest.raises(ProtocolError):
        decode_clock(payload)


@pytest.mark.parametrize("grams", [0, -1, 201, True, 1.5, "10"])
def test_invalid_dispense_amount_never_encodes(grams):
    with pytest.raises(ValueError):
        feed_payload(grams)


@pytest.mark.parametrize(
    "command,payload,address",
    [
        (-1, b"", 0),
        (256, b"", 0),
        (True, b"", 0),
        (3, b"", -1),
        (3, b"", 65536),
        (3, b"x" * 256, 0),
        (3, "x", 0),
    ],
)
def test_reject_out_of_range_frame_fields(command, payload, address):
    with pytest.raises(ProtocolError):
        encode_frame(command, payload, address)
