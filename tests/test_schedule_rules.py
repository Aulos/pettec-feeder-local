"""Rules shared by every stored-schedule editor."""

from dataclasses import replace
from datetime import time

import pytest

pytest.importorskip("homeassistant")

from pettec_xlink.protocol import ScheduleSlot

from custom_components.pettec_local.schedule_rules import (
    DAY_OPTIONS,
    apply_changes,
    matches_expected,
    minute_of_day,
    weekday_mask,
)

SLOT = ScheduleSlot(True, 8, 30, 25, 0xFF)


def test_partial_change_preserves_omitted_fields_and_bit_7():
    updated = apply_changes(SLOT, {"quantity": 33})

    assert updated == ScheduleSlot(True, 8, 30, 33, 0xFF)


def test_weekday_change_preserves_reserved_bit():
    updated = apply_changes(SLOT, {"weekdays": 0x15})

    assert updated.weekdays == 0x95


def test_disabled_slot_accepts_zero_grams():
    empty = ScheduleSlot(False, 0, 0, 0, 0)

    assert apply_changes(empty, {"quantity": 0}).quantity == 0


@pytest.mark.parametrize(
    "slot,changes",
    [
        (ScheduleSlot(False, 0, 0, 0, 0), {"enabled": True}),
        (SLOT, {"quantity": 0}),
        (SLOT, {"weekdays": 0}),
    ],
)
def test_enabled_slots_need_an_amount_and_weekdays(slot, changes):
    with pytest.raises(ValueError, match="amount and weekdays"):
        apply_changes(slot, changes)


@pytest.mark.parametrize(
    "changes",
    [
        {"hour": 24},
        {"minute": 60},
        {"quantity": 201},
        {"quantity": 1.5},
        {"quantity": True},
        {"weekdays": 128},
        {"enabled": 1},
        {"unknown": 1},
    ],
)
def test_invalid_changes_are_rejected(changes):
    with pytest.raises(ValueError):
        apply_changes(SLOT, changes)


def test_expected_slot_ignores_only_the_reserved_bit():
    changed_bit = replace(SLOT, weekdays=SLOT.weekdays ^ 0x80)
    changed_amount = replace(SLOT, quantity=40)

    assert matches_expected(SLOT, None)
    assert matches_expected(SLOT, changed_bit)
    assert matches_expected(replace(SLOT, weekdays=0x7F), SLOT)
    assert not matches_expected(SLOT, changed_amount)


def test_weekday_mask_maps_selector_keys():
    assert weekday_mask(["mon", "sun"]) == 0x41
    with pytest.raises(ValueError):
        weekday_mask(["Mon"])
    with pytest.raises(ValueError):
        weekday_mask("mon")


def test_day_options_cover_every_editable_mask():
    assert set(DAY_OPTIONS.values()) == set(range(128))
    assert DAY_OPTIONS["Every day"] == 0x7F
    assert DAY_OPTIONS["Monday–Friday"] == 0x1F
    assert DAY_OPTIONS["Saturday–Sunday"] == 0x60
    assert DAY_OPTIONS["Mon, Wed, Fri"] == 0x15
    assert DAY_OPTIONS["No days"] == 0


def test_minute_of_day_rejects_sub_minute_values():
    assert minute_of_day("09:15:00") == (9, 15)
    assert minute_of_day(time(9, 15)) == (9, 15)
    with pytest.raises(ValueError):
        minute_of_day("09:15:30")
    with pytest.raises(ValueError):
        minute_of_day(time(9, 15, 1))
