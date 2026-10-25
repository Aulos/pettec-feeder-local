"""Bundled dashboard resource and atomic card actions."""

import logging
from pathlib import Path
from urllib.parse import urlsplit

import voluptuous as vol
from homeassistant.auth.permissions.const import POLICY_CONTROL, POLICY_READ
from homeassistant.components.http import StaticPathConfig
from homeassistant.components.lovelace.const import LOVELACE_DATA
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import SupportsResponse
from homeassistant.exceptions import HomeAssistantError, Unauthorized
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er
from homeassistant.loader import async_get_integration
from pettec_xlink.protocol import ScheduleSlot

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)
CARD_PATH = "/pettec_local/pettec-cards.js"


async def async_register_card_resource(hass):
    """Create or update only our local resource in Lovelace storage."""
    lovelace = hass.data[LOVELACE_DATA]
    if lovelace.resource_mode != "storage":
        _LOGGER.info("PetTec cards: YAML resources need a manual module entry")
        return
    resources = lovelace.resources
    await resources.async_get_info()
    version = (await async_get_integration(hass, DOMAIN)).version
    wanted = {"url": f"{CARD_PATH}?v={version}", "res_type": "module"}
    matches = []
    for item in resources.async_items():
        url = urlsplit(item["url"])
        if not url.scheme and not url.netloc and url.path == CARD_PATH:
            matches.append(item)
    if not matches:
        await resources.async_create_item(wanted)
        return
    first = matches[0]
    if first["url"] != wanted["url"] or first["type"] != "module":
        await resources.async_update_item(first["id"], wanted)
    for duplicate in matches[1:]:
        await resources.async_delete_item(duplicate["id"])


async def async_setup_dashboard(hass):
    """Register once per Home Assistant process."""
    try:
        await async_register_card_resource(hass)
    except OSError, HomeAssistantError, ValueError:
        _LOGGER.exception(
            "Unable to register the PetTec card resource; add it manually "
            "in Dashboard Resources. Feeder controls remain available."
        )
    if hass.services.has_service(DOMAIN, "dispense"):
        return
    await hass.http.async_register_static_paths(
        [
            StaticPathConfig(
                CARD_PATH,
                str(Path(__file__).parent / "frontend" / "pettec-cards.js"),
                False,
            )
        ]
    )

    async def resolve(call, suffix, permission=POLICY_CONTROL):
        entity_id = call.data["entity_id"]
        if call.context.user_id:
            user = await hass.auth.async_get_user(call.context.user_id)
            if user is None or not user.permissions.check_entity(entity_id, permission):
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
        await root.schedules.async_change_slot(
            slot,
            {
                "enabled": data["enabled"],
                "hour": data["hour"],
                "minute": data["minute"],
                "quantity": data["quantity"],
                "weekdays": data["weekdays"],
            },
            expected_slot=ScheduleSlot(**data["expected"]),
        )

    async def read_feeder_details(call):
        root = await resolve(call, "feed", POLICY_READ)
        details = await root.async_read_feeder_details(call.data["selector"])
        return {
            "selector": call.data["selector"],
            "header": details.header,
            "records": [
                {
                    "time": f"{record.hour:02}:{record.minute:02}",
                    "quantity": record.quantity,
                    "feeding_type": "automatic" if record.type_code == 0 else "manual",
                    "type_code": record.type_code,
                }
                for record in details.records
            ],
        }

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

    hass.services.async_register(
        DOMAIN,
        "read_feeder_details",
        read_feeder_details,
        supports_response=SupportsResponse.ONLY,
        schema=vol.Schema(
            {
                vol.Required("entity_id"): cv.entity_id,
                vol.Required("selector"): whole(0, 255),
            }
        ),
    )
