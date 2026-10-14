"""Local PF05WD commands, including verified schedule updates."""

import asyncio
from datetime import datetime, timedelta
from time import monotonic

from .protocol import (
    CONTROL_TRAY,
    FEED,
    READ_CLOCK,
    READ_FEEDER_DETAILS,
    READ_REMAINING_BATTERY,
    READ_REMAINING_FOOD,
    READ_SCHEDULES,
    RESET_FEEDING_WEIGHT,
    SCHEDULE_COUNT,
    SET_CLOCK,
    TRAY_INTERVALS,
    TRAY_RETRACTION,
    WRITE_SCHEDULES,
    FeedingDetails,
    ProtocolError,
    ScheduleSlot,
    clock_payload,
    decode_clock,
    decode_feeder_details,
    decode_frame,
    decode_remaining_battery,
    decode_remaining_food,
    decode_schedules,
    decode_tray_interval,
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
        self._interval_lock = asyncio.Lock()

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

    async def get_remaining_food(self) -> int:
        payload = await self._request(READ_REMAINING_FOOD)
        try:
            return decode_remaining_food(payload)
        except ProtocolError as err:
            raise XlinkError(str(err)) from err

    async def get_remaining_battery(self) -> int | None:
        payload = await self._request(READ_REMAINING_BATTERY)
        try:
            return decode_remaining_battery(payload)
        except ProtocolError as err:
            raise XlinkError(str(err)) from err

    async def control_tray(self) -> str:
        """Send the app's tray-button command once; report movement, not position."""
        payload = await self._request(CONTROL_TRAY, b"\x02")
        if payload not in (b"\x01", b"\x02"):
            raise XlinkError("Tray acknowledgement failed; outcome is uncertain")
        return "retracting" if payload == b"\x01" else "extending"

    async def get_feeder_details(self, selector: int) -> FeedingDetails:
        if type(selector) is not int or not 0 <= selector <= 255:
            raise ValueError("History selector must be a whole number from 0 to 255")
        payload = await self._request(READ_FEEDER_DETAILS, bytes([selector]))
        try:
            return decode_feeder_details(payload)
        except ProtocolError as err:
            raise XlinkError(str(err)) from err

    async def get_tray_retraction_interval(self) -> int | None:
        """The APK sends FF to query this setting without changing it."""
        payload = await self._request(TRAY_RETRACTION, b"\xff")
        try:
            return decode_tray_interval(payload)
        except ProtocolError as err:
            raise XlinkError(str(err)) from err

    async def set_tray_retraction_interval(self, minutes: int | None) -> int | None:
        if minutes is not None and (
            type(minutes) is not int or minutes not in TRAY_INTERVALS
        ):
            raise ValueError("Choose 10–60 minutes in five-minute steps or None")
        value = 100 if minutes is None else minutes
        async with self._interval_lock:
            status = await self._request(TRAY_RETRACTION, bytes([value]))
            if status != b"\x00":
                raise XlinkError(
                    "Tray interval acknowledgement failed; outcome is uncertain"
                )
            actual = await self.get_tray_retraction_interval()
            if actual != minutes:
                raise XlinkError(
                    "Tray interval readback differs; inspect before retrying"
                )
            return actual

    async def reset_feeding_weight(self) -> int:
        """Tare once, then read the weight. This is not dispensing calibration."""
        status = await self._request(RESET_FEEDING_WEIGHT, b"\x00\x00")
        if status != b"\x00":
            raise XlinkError(
                "Weight reset acknowledgement failed; outcome is uncertain"
            )
        return await self.get_remaining_food()
