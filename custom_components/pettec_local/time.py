"""A native time picker for each stored feeding schedule."""

from datetime import time

from homeassistant.components.time import TimeEntity
from homeassistant.exceptions import HomeAssistantError
from pettec_xlink.protocol import SCHEDULE_COUNT

from .schedule import ScheduleEntity
from .schedule_rules import minute_of_day


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities(
        [
            ScheduleTime(entry.runtime_data, slot)
            for slot in range(1, SCHEDULE_COUNT + 1)
        ]
    )


class ScheduleTime(ScheduleEntity, TimeEntity):
    _attr_translation_key = "schedule_time"
    _attr_icon = "mdi:clock-outline"

    @property
    def native_value(self):
        slot = self.schedule
        return time(slot.hour, slot.minute) if slot else None

    async def async_set_value(self, value: time):
        try:
            hour, minute = minute_of_day(value)
        except ValueError as err:
            raise HomeAssistantError(str(err)) from err
        await self.coordinator.async_change_slot(
            self.slot_number, {"hour": hour, "minute": minute}
        )
