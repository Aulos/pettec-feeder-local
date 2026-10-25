"""Schedule enable switches and the saved startup clock-sync preference."""

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory
from pettec_xlink.protocol import SCHEDULE_COUNT

from .const import CONF_SYNC_TIME_STARTUP
from .schedule import ScheduleEntity


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities(
        [
            StartupClockSync(entry.runtime_data),
            *(
                ScheduleEnabled(entry.runtime_data, slot)
                for slot in range(1, SCHEDULE_COUNT + 1)
            ),
        ]
    )


class ScheduleEnabled(ScheduleEntity, SwitchEntity):
    _attr_translation_key = "schedule_enabled"
    _attr_icon = "mdi:calendar-check"

    @property
    def is_on(self):
        slot = self.schedule
        return slot.enabled if slot else None

    async def async_turn_on(self, **kwargs):
        await self.coordinator.async_change_slot(self.slot_number, {"enabled": True})

    async def async_turn_off(self, **kwargs):
        await self.coordinator.async_change_slot(self.slot_number, {"enabled": False})


class StartupClockSync(SwitchEntity):
    _attr_has_entity_name = True
    _attr_translation_key = "startup_clock_sync"
    _attr_icon = "mdi:clock-sync"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_should_poll = False

    def __init__(self, coordinator):
        self.entry = coordinator.entry
        self._attr_device_info = coordinator.device_info
        self._attr_unique_id = f"{self.entry.entry_id}_startup_clock_sync"

    @property
    def is_on(self):
        return self.entry.options.get(CONF_SYNC_TIME_STARTUP, False)

    async def async_added_to_hass(self):
        self.async_on_remove(self.entry.add_update_listener(self._options_updated))

    async def _options_updated(self, hass, entry):
        self.async_write_ha_state()

    async def async_turn_on(self, **kwargs):
        self._set_enabled(True)

    async def async_turn_off(self, **kwargs):
        self._set_enabled(False)

    def _set_enabled(self, enabled):
        self.hass.config_entries.async_update_entry(
            self.entry, options={**self.entry.options, CONF_SYNC_TIME_STARTUP: enabled}
        )
        self.async_write_ha_state()
