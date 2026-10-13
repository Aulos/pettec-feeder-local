"""Explicitly dispense the selected quantity once."""

from homeassistant.components.button import ButtonEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .coordinator import FeederCoordinator


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    async_add_entities([FeedButton(entry.runtime_data)])


class FeedButton(CoordinatorEntity[FeederCoordinator], ButtonEntity):
    _attr_has_entity_name = True
    _attr_translation_key = "feed"
    _attr_icon = "mdi:food-drumstick"

    def __init__(self, coordinator: FeederCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_feed"
        self._attr_device_info = coordinator.device_info

    async def async_press(self) -> None:
        await self.coordinator.async_dispense()
