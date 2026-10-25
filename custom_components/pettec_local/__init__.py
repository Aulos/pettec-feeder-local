"""Local control of PetTec PF 05WD feeders."""

import logging

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import CONF_HOST, EVENT_HOMEASSISTANT_STARTED, Platform
from homeassistant.core import CoreState, HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from pettec_xlink.feeder import FeederClient

from .card_actions import async_setup_actions
from .cards import async_setup_cards
from .const import CONF_ACCESS_KEY, CONF_SYNC_TIME_STARTUP
from .coordinator import FeederCoordinator

PLATFORMS = [
    Platform.SENSOR,
    Platform.NUMBER,
    Platform.BUTTON,
    Platform.TIME,
    Platform.SELECT,
    Platform.SWITCH,
]
_LOGGER = logging.getLogger(__name__)


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Serve the dashboard cards and register their actions."""
    await async_setup_cards(hass)
    await async_setup_actions(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Connect and perform a read before exposing any controls."""
    client = FeederClient(entry.data[CONF_HOST], entry.data[CONF_ACCESS_KEY])
    coordinator = FeederCoordinator(hass, entry, client)
    try:
        await coordinator.async_config_entry_first_refresh()
        entry.runtime_data = coordinator
        # Unsupported schedule reads must not disable working manual controls.
        await coordinator.schedules.async_refresh()
        await coordinator.telemetry.async_refresh()
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
        if hass.state in (CoreState.not_running, CoreState.starting):

            async def sync_clock_at_start(event):
                if entry.state is ConfigEntryState.LOADED and entry.options.get(
                    CONF_SYNC_TIME_STARTUP, False
                ):
                    try:
                        await coordinator.async_sync_clock()
                    except HomeAssistantError:
                        _LOGGER.warning(
                            "Startup clock sync failed; check the device clock. "
                            "No automatic retry will be made."
                        )

            entry.async_on_unload(
                hass.bus.async_listen_once(
                    EVENT_HOMEASSISTANT_STARTED, sync_clock_at_start
                )
            )
    except BaseException:
        await client.close()
        raise
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Release entities and the UDP socket."""
    if await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        await entry.runtime_data.client.close()
        return True
    return False
