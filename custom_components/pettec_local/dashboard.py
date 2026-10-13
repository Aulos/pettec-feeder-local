"""Bundled dashboard resource and atomic card actions."""

from pathlib import Path

import voluptuous as vol
from homeassistant.auth.permissions.const import POLICY_CONTROL
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntryState
from homeassistant.exceptions import HomeAssistantError, Unauthorized
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN
from .protocol import ScheduleSlot


async def async_setup_dashboard(hass):
    """Register once per Home Assistant process."""
    if hass.services.has_service(DOMAIN, "dispense"):
        return
    await hass.http.async_register_static_paths(
        [
            StaticPathConfig(
                "/pettec_local/pettec-cards.js",
                str(Path(__file__).parent / "frontend" / "pettec-cards.js"),
                False,
            )
        ]
    )

    async def resolve(call, suffix):
        entity_id = call.data["entity_id"]
        if call.context.user_id:
            user = await hass.auth.async_get_user(call.context.user_id)
            if user is None or not user.permissions.check_entity(
                entity_id, POLICY_CONTROL
            ):
                raise Unauthorized(entity_id=entity_id)
        entity = er.async_get(hass).async_get(entity_id)
        if not entity or entity.platform != DOMAIN:
            raise HomeAssistantError("Choose a PetTec entity")
        entry = hass.config_entries.async_get_entry(entity.config_entry_id)
        if not entry or entry.state is not ConfigEntryState.LOADED:
            raise HomeAssistantError("The feeder integration is unavailable")
        if entity.unique_id != f"{entry.entry_id}_{suffix}":
            raise HomeAssistantError("Incorrect PetTec entity for this action")
        return entry.runtime_data

    async def dispense(call):
        root = await resolve(call, "feed")
        await root.async_dispense(call.data["quantity"])

    async def save_schedule(call):
        data = call.data
        slot = data["slot"]
        root = await resolve(call, f"schedule_{slot}_schedule_time")
        current = root.schedules.data
        if current is None:
            raise HomeAssistantError("Stored schedules are unavailable")
        await root.schedules.async_change_slot(
            slot,
            expected_slot=ScheduleSlot(
                **{
                    **data["expected"],
                    "weekdays": (current[slot - 1].weekdays & 128)
                    | data["expected"]["weekdays"],
                }
            ),
            enabled=data["enabled"],
            hour=data["hour"],
            minute=data["minute"],
            quantity=data["quantity"],
            weekdays=(current[slot - 1].weekdays & 128) | data["weekdays"],
        )

    def integer(value):
        if type(value) is not int:
            raise vol.Invalid("Choose a whole number")
        return value

    def whole(low, high):
        return vol.All(integer, vol.Range(min=low, max=high))

    hass.services.async_register(
        DOMAIN,
        "dispense",
        dispense,
        schema=vol.Schema(
            {
                vol.Required("entity_id"): cv.entity_id,
                vol.Required("quantity"): whole(1, 200),
            }
        ),
    )
    hass.services.async_register(
        DOMAIN,
        "save_schedule",
        save_schedule,
        schema=vol.Schema(
            {
                vol.Required("entity_id"): cv.entity_id,
                vol.Required("slot"): whole(1, 5),
                vol.Required("expected"): {
                    vol.Required("enabled"): cv.boolean,
                    vol.Required("hour"): whole(0, 23),
                    vol.Required("minute"): whole(0, 59),
                    vol.Required("quantity"): whole(0, 200),
                    vol.Required("weekdays"): whole(0, 127),
                },
                vol.Required("enabled"): cv.boolean,
                vol.Required("hour"): whole(0, 23),
                vol.Required("minute"): whole(0, 59),
                vol.Required("quantity"): whole(1, 200),
                vol.Required("weekdays"): whole(0, 127),
            }
        ),
    )
