"""Shared identity and stored-state access for device-page schedule controls."""

from homeassistant.const import EntityCategory
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .coordinator import FeederCoordinator, ScheduleCoordinator


class ScheduleEntity(CoordinatorEntity[ScheduleCoordinator]):
    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, root: FeederCoordinator, slot: int):
        super().__init__(root.schedules)
        self.slot_number = slot
        self._attr_unique_id = (
            f"{root.entry.entry_id}_schedule_{slot}_{self._attr_translation_key}"
        )
        self._attr_translation_placeholders = {"slot": str(slot)}
        self._attr_device_info = root.device_info

    @property
    def available(self):
        return super().available and self.coordinator.data is not None

    @property
    def schedule(self):
        if self.coordinator.data is None:
            return None
        return self.coordinator.data[self.slot_number - 1]
