"""Serve the bundled dashboard cards and keep their Lovelace resource current."""

import logging
from pathlib import Path
from urllib.parse import urlsplit

from homeassistant.components.http import StaticPathConfig
from homeassistant.components.lovelace.const import LOVELACE_DATA
from homeassistant.exceptions import HomeAssistantError
from homeassistant.loader import async_get_integration

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)
CARD_PATH = "/pettec_local/pettec-cards.js"
CARDS_SETUP = f"{DOMAIN}_cards_setup"


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


async def async_setup_cards(hass) -> None:
    """Register the card resource and static path once per Home Assistant process."""
    if hass.data.get(CARDS_SETUP):
        return
    try:
        await async_register_card_resource(hass)
    except OSError, HomeAssistantError, ValueError:
        _LOGGER.exception(
            "Unable to register the PetTec card resource; add it manually "
            "in Dashboard Resources. Feeder controls remain available."
        )
    await hass.http.async_register_static_paths(
        [
            StaticPathConfig(
                CARD_PATH,
                str(Path(__file__).parent / "frontend" / "pettec-cards.js"),
                False,
            )
        ]
    )
    hass.data[CARDS_SETUP] = True
