"""Explicitly dispense the selected quantity once."""

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .coordinator import FeederCoordinator


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    async_add_entities(
        [
            FeedButton(entry.runtime_data),
            SyncClockButton(entry.runtime_data),
            ControlTrayButton(entry.runtime_data),
            ResetWeightButton(entry.runtime_data),
        ]
    )


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


class SyncClockButton(CoordinatorEntity[FeederCoordinator], ButtonEntity):
    _attr_has_entity_name = True
    _attr_translation_key = "sync_clock"
    _attr_icon = "mdi:clock-sync"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: FeederCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_sync_clock"
        self._attr_device_info = coordinator.device_info

    async def async_press(self) -> None:
        await self.coordinator.async_sync_clock()


class ControlTrayButton(CoordinatorEntity[FeederCoordinator], ButtonEntity):
    _attr_has_entity_name = True
    _attr_translation_key = "control_tray"
    _attr_icon = "mdi:tray-arrow-up"

    def __init__(self, coordinator):
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_control_tray"
        self._attr_device_info = coordinator.device_info

    async def async_press(self):
        await self.coordinator.async_control_tray()


class ResetWeightButton(CoordinatorEntity[FeederCoordinator], ButtonEntity):
    _attr_has_entity_name = True
    _attr_translation_key = "reset_feeding_weight"
    _attr_icon = "mdi:scale-balance"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator):
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_reset_feeding_weight"
        self._attr_device_info = coordinator.device_info

    async def async_press(self):
        await self.coordinator.async_reset_feeding_weight()
