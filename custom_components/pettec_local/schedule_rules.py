"""Pure rules for stored feeding schedules.

The device page, the options flow and the card action all edit the same five
slots. Keep the editable field set, the reserved weekday bit and the enabled
rules here so every editor agrees.
"""

from __future__ import annotations

from datetime import time
from typing import Iterable, NotRequired, TypedDict

from pettec_xlink.protocol import MAX_FEED_GRAMS, ScheduleSlot

WEEKDAY_KEYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
WEEKDAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
EDITABLE_WEEKDAYS = 0x7F
RESERVED_WEEKDAY = 0x80
_EDITABLE_FIELDS = frozenset({"enabled", "hour", "minute", "quantity", "weekdays"})


class ScheduleChanges(TypedDict):
    """One partial edit to a stored slot; omitted fields keep their value."""

    enabled: NotRequired[bool]
    hour: NotRequired[int]
    minute: NotRequired[int]
    quantity: NotRequired[int | float]
    weekdays: NotRequired[int]


def _weekday_options() -> dict[str, int]:
    options: dict[str, int] = {
        "Every day": 0x7F,
        "Monday–Friday": 0x1F,
        "Saturday–Sunday": 0x60,
    }
    for mask in range(1, 0x80):
        if mask in options.values():
            continue
        label = ", ".join(
            name for bit, name in enumerate(WEEKDAY_NAMES) if mask & (1 << bit)
        )
        options[label] = mask
    options["No days"] = 0
    return options


DAY_OPTIONS = _weekday_options()


def weekday_mask(days: Iterable[str]) -> int:
    """Map selector keys such as ``mon`` to weekday bits."""
    if isinstance(days, str):
        raise ValueError("Choose weekday names as a list")
    mask = 0
    for day in days:
        if day not in WEEKDAY_KEYS:
            raise ValueError(f"Unknown weekday: {day}")
        mask |= 1 << WEEKDAY_KEYS.index(day)
    return mask


def minute_of_day(value: str | time) -> tuple[int, int]:
    """Accept only a minute-precision local wall clock."""
    parsed = time.fromisoformat(value) if isinstance(value, str) else value
    if (
        not isinstance(parsed, time)
        or parsed.second
        or parsed.microsecond
        or parsed.tzinfo
    ):
        raise ValueError("Feeding schedules have minute precision")
    return parsed.hour, parsed.minute


def editable_signature(slot: ScheduleSlot) -> tuple[bool, int, int, int, int]:
    """The fields an editor may change; the reserved weekday bit is hidden."""
    if not isinstance(slot, ScheduleSlot):
        raise ValueError("Expected a schedule slot")
    return (
        slot.enabled,
        slot.hour,
        slot.minute,
        slot.quantity,
        slot.weekdays & EDITABLE_WEEKDAYS,
    )


def matches_expected(actual: ScheduleSlot, expected: ScheduleSlot | None) -> bool:
    """Compare only editable fields, so bit 7 never causes a stale edit."""
    if expected is None:
        return True
    return editable_signature(actual) == editable_signature(expected)


def apply_changes(slot: ScheduleSlot, changes: ScheduleChanges) -> ScheduleSlot:
    """Return one updated slot, preserving bit 7 and every omitted field."""
    if not isinstance(slot, ScheduleSlot):
        raise ValueError("Expected a schedule slot")
    unknown = set(changes) - _EDITABLE_FIELDS
    if unknown:
        raise ValueError(f"Unknown schedule field: {sorted(unknown)[0]}")

    values = {
        "enabled": slot.enabled,
        "hour": slot.hour,
        "minute": slot.minute,
        "quantity": slot.quantity,
        "weekdays": slot.weekdays,
    }

    if "enabled" in changes:
        enabled = changes["enabled"]
        if type(enabled) is not bool:
            raise ValueError("Schedule enabled must be a boolean")
        values["enabled"] = enabled

    for field, maximum in (("hour", 23), ("minute", 59)):
        if field in changes:
            value = changes[field]
            if type(value) is not int or not 0 <= value <= maximum:
                raise ValueError(f"Schedule {field} must be from 0 to {maximum}")
            values[field] = value

    if "quantity" in changes:
        quantity = changes["quantity"]
        if (
            isinstance(quantity, bool)
            or not isinstance(quantity, (int, float))
            or not float(quantity).is_integer()
        ):
            raise ValueError("Schedule amount must be a whole number")
        quantity = int(quantity)
        if not 0 <= quantity <= MAX_FEED_GRAMS:
            raise ValueError(
                f"Schedule amount must be from 0 to {MAX_FEED_GRAMS} grams"
            )
        values["quantity"] = quantity

    if "weekdays" in changes:
        weekdays = changes["weekdays"]
        if type(weekdays) is not int or not 0 <= weekdays <= EDITABLE_WEEKDAYS:
            raise ValueError("Schedule weekdays must be from 0 to 127")
        values["weekdays"] = (slot.weekdays & RESERVED_WEEKDAY) | weekdays

    if values["enabled"] and (
        not 1 <= values["quantity"] <= MAX_FEED_GRAMS
        or not values["weekdays"] & EDITABLE_WEEKDAYS
    ):
        raise ValueError("Enabled schedules need an amount and weekdays")

    return ScheduleSlot(**values)
