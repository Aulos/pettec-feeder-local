"""Weekday combinations for device-page schedule controls."""

from homeassistant.components.select import SelectEntity
from homeassistant.const import EntityCategory
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from pettec_xlink.protocol import SCHEDULE_COUNT, TRAY_INTERVALS

from .coordinator import TelemetryCoordinator
from .schedule import ScheduleEntity

DAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
DAY_OPTIONS = {"Every day": 127, "Monday–Friday": 31, "Saturday–Sunday": 96}
for mask in range(1, 128):
    if mask not in DAY_OPTIONS.values():
        label = ", ".join(day for bit, day in enumerate(DAY_NAMES) if mask & (1 << bit))
        DAY_OPTIONS[label] = mask
DAY_OPTIONS["No days"] = 0


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities(
        [
            *(
                ScheduleDays(entry.runtime_data, slot)
                for slot in range(1, SCHEDULE_COUNT + 1)
            ),
            TrayRetractionInterval(entry.runtime_data),
        ]
    )


class ScheduleDays(ScheduleEntity, SelectEntity):
    _attr_translation_key = "schedule_days"
    _attr_icon = "mdi:calendar-week"
    _attr_options = list(DAY_OPTIONS)

    @property
    def current_option(self):
        slot = self.schedule
        if slot is None:
            return None
        return next(
            label for label, mask in DAY_OPTIONS.items() if mask == slot.weekdays & 127
        )

    async def async_select_option(self, option: str):
        if option not in DAY_OPTIONS:
            raise HomeAssistantError("Choose a listed weekday combination")
        slot = self.schedule
        if slot is None:
            raise HomeAssistantError("Stored schedules are unavailable")
        await self.coordinator.async_change_slot(
            self.slot_number, weekdays=(slot.weekdays & 128) | DAY_OPTIONS[option]
        )


class TrayRetractionInterval(CoordinatorEntity[TelemetryCoordinator], SelectEntity):
    _attr_has_entity_name = True
    _attr_translation_key = "tray_retraction_interval"
    _attr_icon = "mdi:timer-outline"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_options = ["disabled", *(str(value) for value in TRAY_INTERVALS)]

    def __init__(self, root):
        super().__init__(root.telemetry)
        self._attr_unique_id = f"{root.entry.entry_id}_tray_retraction_interval"
        self._attr_device_info = root.device_info

    @property
    def available(self):
        return super().available and "tray_interval" in (self.coordinator.data or {})

    @property
    def current_option(self):
        if not self.available:
            return None
        value = self.coordinator.data["tray_interval"]
        return "disabled" if value is None else str(value)

    async def async_select_option(self, option):
        if option not in self.options:
            raise HomeAssistantError("Choose a listed tray-retraction interval")
        await self.coordinator.async_set_interval(
            None if option == "disabled" else int(option)
        )
