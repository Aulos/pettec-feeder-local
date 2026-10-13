"""A saved quantity for the next explicit feeding request."""

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.const import EntityCategory
from homeassistant.exceptions import HomeAssistantError

from .const import CONF_QUANTITY, MAX_QUANTITY, MIN_QUANTITY
from .coordinator import FeederCoordinator
from .protocol import SCHEDULE_COUNT
from .schedule import ScheduleEntity


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    async_add_entities(
        [
            FeedQuantity(entry.runtime_data),
            *(
                ScheduleAmount(entry.runtime_data, slot)
                for slot in range(1, SCHEDULE_COUNT + 1)
            ),
        ]
    )


class FeedQuantity(NumberEntity):
    _attr_has_entity_name = True
    _attr_translation_key = "quantity"
    _attr_icon = "mdi:food"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_native_min_value = MIN_QUANTITY
    _attr_native_max_value = MAX_QUANTITY
    _attr_native_step = 1
    _attr_native_unit_of_measurement = "g"
    _attr_mode = NumberMode.BOX
    _attr_should_poll = False

    def __init__(self, coordinator: FeederCoordinator) -> None:
        self.coordinator = coordinator
        self._attr_unique_id = f"{coordinator.entry.entry_id}_quantity"
        self._attr_device_info = coordinator.device_info

    @property
    def native_value(self) -> int:
        return self.coordinator.quantity

    async def async_set_native_value(self, value: float) -> None:
        if not MIN_QUANTITY <= value <= MAX_QUANTITY or not float(value).is_integer():
            raise HomeAssistantError(
                "Choose a whole-number quantity within the allowed range"
            )
        entry = self.coordinator.entry
        self.hass.config_entries.async_update_entry(
            entry, options={**entry.options, CONF_QUANTITY: int(value)}
        )
        self.async_write_ha_state()


class ScheduleAmount(ScheduleEntity, NumberEntity):
    _attr_translation_key = "schedule_amount"
    _attr_icon = "mdi:food"
    _attr_native_min_value = MIN_QUANTITY
    _attr_native_max_value = MAX_QUANTITY
    _attr_native_step = 1
    _attr_native_unit_of_measurement = "g"
    _attr_mode = NumberMode.BOX

    @property
    def native_value(self):
        slot = self.schedule
        return slot.quantity if slot else None

    async def async_set_native_value(self, value: float):
        if not MIN_QUANTITY <= value <= MAX_QUANTITY or not float(value).is_integer():
            raise HomeAssistantError("Choose 1–200 whole grams")
        await self.coordinator.async_change_slot(self.slot_number, quantity=int(value))
