"""Read-only polling and explicit dispensing."""

import logging
from datetime import datetime, timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import CONF_QUANTITY, DEFAULT_QUANTITY, DOMAIN
from .feeder import FeederClient
from .xlink import AuthenticationError, XlinkError

_LOGGER = logging.getLogger(__name__)


class FeederCoordinator(DataUpdateCoordinator[datetime]):
    """Poll the device clock without performing feeding actions."""

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, client: FeederClient
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name="PetTec local",
            update_interval=timedelta(minutes=1),
        )
        self.client = client
        self.entry = entry

    @property
    def device_info(self) -> DeviceInfo:
        """Use a discovered MAC as identity, falling back to the config entry."""
        return DeviceInfo(
            identifiers={(DOMAIN, self.client.mac or self.entry.entry_id)},
            manufacturer="PetTec",
            model="PF 05WD",
            name=self.entry.title,
        )

    @property
    def quantity(self) -> int:
        """Return the saved quantity requested by the user."""
        return self.entry.options.get(CONF_QUANTITY, DEFAULT_QUANTITY)

    async def _async_update_data(self) -> datetime:
        try:
            await self.client.connect()
            return await self.client.get_clock()
        except AuthenticationError as err:
            await self.client.close()
            raise ConfigEntryAuthFailed("The feeder rejected its access key") from err
        except (XlinkError, OSError, TimeoutError, ValueError) as err:
            await self.client.close()
            raise UpdateFailed("Unable to read the feeder clock") from err

    async def async_dispense(self) -> None:
        """Send one request; a lost acknowledgement must not trigger a retry."""
        try:
            await self.client.dispense(self.quantity)
        except (XlinkError, OSError, TimeoutError, ValueError) as err:
            await self.client.close()
            raise HomeAssistantError(
                "The feeding request failed. Check the bowl before trying again; "
                "the feeder may have received the request."
            ) from err
