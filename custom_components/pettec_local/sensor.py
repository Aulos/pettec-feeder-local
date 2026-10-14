"""The feeder's local clock, without guessing its time zone."""

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfMass
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .coordinator import FeederCoordinator, TelemetryCoordinator


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    async_add_entities(
        [
            FeederClock(entry.runtime_data),
            RemainingFood(entry.runtime_data),
            RemainingBattery(entry.runtime_data),
        ]
    )


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


class TelemetrySensor(CoordinatorEntity[TelemetryCoordinator], SensorEntity):
    _attr_has_entity_name = True

    def __init__(self, root):
        super().__init__(root.telemetry)
        self._attr_unique_id = f"{root.entry.entry_id}_{self._attr_translation_key}"
        self._attr_device_info = root.device_info

    @property
    def available(self):
        return super().available and self.field in (self.coordinator.data or {})

    @property
    def native_value(self):
        return (self.coordinator.data or {}).get(self.field)


class RemainingFood(TelemetrySensor):
    field = "food"
    _attr_translation_key = "remaining_food"
    _attr_icon = "mdi:food"
    _attr_device_class = SensorDeviceClass.WEIGHT
    _attr_native_unit_of_measurement = UnitOfMass.GRAMS
    _attr_state_class = SensorStateClass.MEASUREMENT


class RemainingBattery(TelemetrySensor):
    field = "battery"
    _attr_translation_key = "remaining_battery"
    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def extra_state_attributes(self):
        return {"charging": self.native_value is None} if self.available else {}
