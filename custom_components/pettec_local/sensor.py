"""The feeder's local clock, without guessing its time zone."""

from homeassistant.components.sensor import SensorEntity
from homeassistant.const import EntityCategory
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .coordinator import FeederCoordinator


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    async_add_entities([FeederClock(entry.runtime_data)])


class FeederClock(CoordinatorEntity[FeederCoordinator], SensorEntity):
    _attr_has_entity_name = True
    _attr_translation_key = "clock"
    _attr_icon = "mdi:clock-outline"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: FeederCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_clock"
        self._attr_device_info = coordinator.device_info

    @property
    def native_value(self) -> str | None:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.isoformat()
