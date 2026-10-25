"""PF05WD inner frames, recovered from PetTec Android APK 1.1.4."""

import struct
from dataclasses import dataclass
from datetime import datetime

HEADER = b"\x57\xab"
CHECK_CONNECTION = 0x01
READ_DEVICE_INFO = 0x02
READ_CLOCK = 0x03
SET_CLOCK = 0x04
READ_SCHEDULES = 0x05
WRITE_SCHEDULES = 0x06
ERASE_FLASH = 0x07
WRITE_FLASH = 0x08
READ_FLASH = 0x09
READ_MODULE_ADDRESS = 0x0A
FEED = 0x0C
RESET_DEVICE = 0x0E
READ_FEED_RECORD = 0x0F
FEED_DATA_OFFSET_OR_CRADLE = 0x13
READ_ALARMS = 0x14
CONTROL_CAMERA = 0x15
BEGIN_UPGRADE = 0x18
WRITE_MCU = 0x19
CHECK_MCU = 0x1A
COMPLETE_UPGRADE = 0x1B
ENABLE_UPGRADE = 0x1C
READ_FIRMWARE_VERSION = 0x1D
READ_FEEDER_INFO = 0x1F
BEGIN_AUDIO = 0x20
WRITE_AUDIO = 0x21
COMPLETE_AUDIO = 0x22
FEED_CALIBRATION = 0x27
ENTER_BOOT = 0x3F
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
    # Firmware can report Sunday as 7; the app's writer uses 0.
    if len(payload) != 7 or payload[0] > 99 or payload[6] > 7:
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


def unsigned(value: int, maximum: int = 255) -> int:
    """Reject coercion and truncation before building a maintenance packet."""
    if type(value) is not int or not 0 <= value <= maximum:
        raise ValueError(f"Expected a whole number from 0 to {maximum}")
    return value


def data_bytes(value: bytes, minimum: int = 0, maximum: int = 255) -> bytes:
    if not isinstance(value, bytes) or not minimum <= len(value) <= maximum:
        raise ValueError(f"Expected bytes with length {minimum}..{maximum}")
    return value


def upload_chunk_payload(index: int, data: bytes) -> bytes:
    """WriteAudioUsecase and secondary MCU: index, byte sum, up to 128 bytes."""
    data = data_bytes(data, 1, 128)
    return struct.pack(">HB", unsigned(index, 65535), sum(data) & 255) + data


def audio_metadata(date: bytes, file_size: int, checksum: int) -> bytes:
    """The app prefixes ten opaque date bytes with five zeros."""
    return (
        b"\x00" * 5
        + data_bytes(date, 10, 10)
        + struct.pack(">IB", unsigned(file_size, 0xFFFFFFFF), unsigned(checksum))
    )


READ_REMAINING_FOOD = 0x10
READ_REMAINING_BATTERY = 0x11
CONTROL_TRAY = 0x12
READ_FEEDER_DETAILS = 0x23
TRAY_RETRACTION = 0x24
RESET_FEEDING_WEIGHT = 0x26
TRAY_INTERVALS = tuple(range(10, 61, 5))


@dataclass(frozen=True)
class FeedingRecord:
    """One APK history record, with its original feeding-type byte."""

    hour: int
    minute: int
    quantity: int
    type_code: int


@dataclass(frozen=True)
class FeedingDetails:
    """History reply; the APK ignores the leading byte, so retain it as raw data."""

    header: int
    records: tuple[FeedingRecord, ...]


def decode_remaining_food(payload: bytes) -> int:
    """The app reads a signed big-endian short and displays grams."""
    if len(payload) != 2:
        raise ProtocolError("Expected a two-byte food weight")
    return struct.unpack(">h", payload)[0]


def decode_remaining_battery(payload: bytes) -> int | None:
    """The app labels codes 101..127 as charging, not a percentage."""
    if len(payload) != 1 or payload[0] > 127:
        raise ProtocolError("Unknown battery response")
    return payload[0] if payload[0] <= 100 else None


def decode_tray_interval(payload: bytes) -> int | None:
    """100 disables retraction; FF is the read query, never an interval."""
    if len(payload) != 1 or payload[0] not in (*TRAY_INTERVALS, 100):
        raise ProtocolError("Unknown tray retraction interval")
    return None if payload[0] == 100 else payload[0]


def decode_feeder_details(payload: bytes) -> FeedingDetails:
    if not payload or (len(payload) - 1) % 5:
        raise ProtocolError("Expected a header followed by five-byte feeding records")
    records = []
    for offset in range(1, len(payload), 5):
        hour, minute, quantity, kind = struct.unpack_from(">BBhB", payload, offset)
        if hour > 23 or minute > 59 or quantity < 0:
            raise ProtocolError("Invalid feeding history record")
        records.append(FeedingRecord(hour, minute, quantity, kind))
    return FeedingDetails(payload[0], tuple(records))
