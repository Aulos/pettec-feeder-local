"""Local PF05WD commands, including verified schedule updates."""

import asyncio
from datetime import datetime, timedelta
from time import monotonic

from .protocol import (
    FEED,
    READ_CLOCK,
    READ_SCHEDULES,
    SCHEDULE_COUNT,
    SET_CLOCK,
    WRITE_SCHEDULES,
    ProtocolError,
    ScheduleSlot,
    clock_payload,
    decode_clock,
    decode_frame,
    decode_schedules,
    encode_frame,
    encode_schedules,
    feed_payload,
)
from .xlink import XlinkClient, XlinkError


class ScheduleChangedError(XlinkError):
    """The selected slot changed while its editor was open."""


class FeederClient:
    """Wrap the recovered inner protocol in the local Xlink transport."""

    def __init__(self, host: str, access_key: int) -> None:
        self._transport = XlinkClient(host, access_key)
        self._schedule_lock = asyncio.Lock()

    @property
    def mac(self) -> str:
        """The identity received from Xlink discovery."""
        return self._transport.mac

    async def connect(self) -> None:
        await self._transport.connect()

    async def close(self) -> None:
        await self._transport.close()

    async def _request(self, command: int, payload: bytes = b"") -> bytes:
        raw = await self._transport.send_pipe(encode_frame(command, payload))
        try:
            response = decode_frame(raw)
            if response.command != command | 0x80:
                raise ProtocolError("Unexpected feeder response command")
        except ProtocolError as err:
            raise XlinkError(str(err)) from err
        return response.payload

    async def get_clock(self) -> datetime:
        """Read-only connection probe used for setup and availability."""
        payload = await self._request(READ_CLOCK)
        try:
            return decode_clock(payload)
        except ProtocolError as err:
            raise XlinkError(str(err)) from err

    async def dispense(self, quantity: int) -> None:
        """Send exactly one manual feeding command and require a success reply."""
        payload = await self._request(FEED, feed_payload(quantity))
        if len(payload) != 1:
            raise XlinkError("Malformed feeding acknowledgement; outcome is uncertain")
        if payload[0] != 0:
            raise XlinkError(
                f"Feeder returned status 0x{payload[0]:02x}; no automatic retry"
            )

    async def set_clock(self, value: datetime) -> datetime:
        """Write once and verify the returned local clock, allowing transit time."""
        payload = clock_payload(value)
        started = monotonic()
        status = await self._request(SET_CLOCK, payload)
        if status != b"\x00":
            raise XlinkError("Clock acknowledgement failed; outcome is uncertain")
        actual = await self.get_clock()
        tolerance = timedelta(seconds=monotonic() - started + 2)
        if abs(actual - value.replace(microsecond=0)) > tolerance:
            raise XlinkError(
                "Clock readback differs; inspect the clock before retrying"
            )
        return actual

    async def get_schedules(self) -> tuple[ScheduleSlot, ...]:
        """Read all stored slots without changing the device."""
        payload = await self._request(READ_SCHEDULES)
        try:
            return decode_schedules(payload)
        except ProtocolError as err:
            raise XlinkError(str(err)) from err

    async def update_schedule(
        self, slot: int, schedule: ScheduleSlot, *, expected: ScheduleSlot
    ) -> tuple[ScheduleSlot, ...]:
        """Preserve other slots, write once, and verify the entire list."""
        if type(slot) is not int or not 1 <= slot <= SCHEDULE_COUNT:
            raise ValueError("Schedule slot must be from 1 to 5")
        if not isinstance(schedule, ScheduleSlot) or not isinstance(
            expected, ScheduleSlot
        ):
            raise ValueError("Expected schedule slots")
        if not 0 <= schedule.quantity <= 200 or (
            schedule.enabled
            and (schedule.quantity == 0 or not schedule.weekdays & 0x7F)
        ):
            raise ValueError("Enabled schedules need 1–200 grams and at least one day")
        async with self._schedule_lock:
            await self.connect()
            current = await self.get_schedules()
            if current[slot - 1] == schedule:
                return current
            if current[slot - 1] != expected:
                raise ScheduleChangedError("Schedule changed; reopen its editor")
            updated = list(current)
            updated[slot - 1] = schedule
            wanted = tuple(updated)
            status = await self._request(WRITE_SCHEDULES, encode_schedules(wanted))
            if status != b"\x00":
                raise XlinkError(
                    "Schedule acknowledgement failed; outcome is uncertain"
                )
            actual = await self.get_schedules()
            if actual != wanted:
                raise XlinkError(
                    "Schedule readback differs; inspect schedules before retrying"
                )
            return actual
