"""PF05WD inner frames, recovered from PetTec Android APK 1.1.4."""

import struct
from dataclasses import dataclass
from datetime import datetime

HEADER = b"\x57\xab"
READ_CLOCK = 0x03
SET_CLOCK = 0x04
READ_SCHEDULES = 0x05
WRITE_SCHEDULES = 0x06
FEED = 0x0C
MAX_FEED_GRAMS = 200
SCHEDULE_COUNT = 5


class ProtocolError(ValueError):
    """An invalid or unsupported feeder response."""


@dataclass(frozen=True)
class ScheduleSlot:
    """One six-byte timer; weekday bits 0 through 6 mean Monday to Sunday."""

    enabled: bool
    hour: int
    minute: int
    quantity: int
    weekdays: int

    def __post_init__(self) -> None:
        if type(self.enabled) is not bool:
            raise ProtocolError("Schedule enabled must be a boolean")
        for value, maximum in (
            (self.hour, 23),
            (self.minute, 59),
            (self.quantity, 65535),
            (self.weekdays, 255),
        ):
            if type(value) is not int or not 0 <= value <= maximum:
                raise ProtocolError("Invalid schedule field")

    def to_bytes(self) -> bytes:
        return struct.pack(
            ">BBBHB", self.enabled, self.hour, self.minute, self.quantity, self.weekdays
        )


def decode_schedules(payload: bytes) -> tuple[ScheduleSlot, ...]:
    """Require all five slots before permitting a read/modify/write operation."""
    if len(payload) != SCHEDULE_COUNT * 6:
        raise ProtocolError("Expected five six-byte schedule slots")
    slots = []
    for offset in range(0, len(payload), 6):
        enabled, hour, minute, quantity, weekdays = struct.unpack(
            ">BBBHB", payload[offset : offset + 6]
        )
        if enabled not in (0, 1):
            raise ProtocolError("Unknown schedule enabled flag")
        slots.append(ScheduleSlot(bool(enabled), hour, minute, quantity, weekdays))
    return tuple(slots)


def encode_schedules(slots: tuple[ScheduleSlot, ...]) -> bytes:
    if len(slots) != SCHEDULE_COUNT or any(
        not isinstance(slot, ScheduleSlot) for slot in slots
    ):
        raise ProtocolError("Expected five schedule slots")
    return b"".join(slot.to_bytes() for slot in slots)


@dataclass(frozen=True)
class Frame:
    """Decoded inner packet. This is not an Xlink UDP datagram."""

    address: int
    command: int
    payload: bytes


def encode_frame(command: int, payload: bytes = b"", address: int = 0xFFFF) -> bytes:
    """Encode the big-endian header and additive eight-bit checksum."""
    if type(command) is not int or not 0 <= command <= 0xFF:
        raise ProtocolError("Command must be one unsigned byte")
    if type(address) is not int or not 0 <= address <= 0xFFFF:
        raise ProtocolError("Address must be an unsigned short")
    if not isinstance(payload, bytes) or len(payload) > 0xFF:
        raise ProtocolError("Payload must be at most 255 bytes")
    body = HEADER + struct.pack(">HBB", address, command, len(payload)) + payload
    return body + bytes([sum(body) & 0xFF])


def decode_frame(data: bytes) -> Frame:
    """Validate packet length, magic and checksum before reading fields."""
    if len(data) < 7 or data[:2] != HEADER:
        raise ProtocolError("Invalid feeder packet header")
    if len(data) != 7 + data[5]:
        raise ProtocolError("Invalid feeder packet length")
    if sum(data[:-1]) & 0xFF != data[-1]:
        raise ProtocolError("Invalid feeder packet checksum")
    return Frame(int.from_bytes(data[2:4], "big"), data[4], data[6:-1])


def decode_clock(payload: bytes) -> datetime:
    """Decode the device's local wall clock, without inventing a timezone."""
    if len(payload) != 7 or payload[0] > 99 or payload[6] > 6:
        raise ProtocolError("Invalid device clock payload")
    try:
        return datetime(2000 + payload[0], *payload[1:6])
    except ValueError as err:
        raise ProtocolError("Invalid device clock values") from err


def clock_payload(value: datetime) -> bytes:
    """Encode a local wall clock; RTC weekdays use Sunday=0, unlike timers."""
    if not isinstance(value, datetime) or value.tzinfo is not None:
        raise ValueError("Clock must be a local datetime without a timezone")
    if not 2000 <= value.year <= 2099:
        raise ValueError("The feeder supports clock years 2000–2099")
    return bytes(
        [
            value.year - 2000,
            value.month,
            value.day,
            value.hour,
            value.minute,
            value.second,
            (value.weekday() + 1) % 7,
        ]
    )


def feed_payload(grams: int) -> bytes:
    """Match the stock app's manual feeding range and units."""
    if type(grams) is not int or not 1 <= grams <= MAX_FEED_GRAMS:
        raise ValueError(f"Feeding amount must be 1–{MAX_FEED_GRAMS} whole grams")
    return struct.pack(">H", grams)
