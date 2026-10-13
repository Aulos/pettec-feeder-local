"""Read-only polling and explicit dispensing."""

import asyncio
import logging
from dataclasses import replace
from datetime import datetime, timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import CONF_QUANTITY, DEFAULT_QUANTITY, DOMAIN
from .feeder import FeederClient
from .protocol import ScheduleSlot
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
        self.schedules = ScheduleCoordinator(hass, entry, client)

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

    async def async_sync_clock(self) -> None:
        """Use Home Assistant's configured local time, then update the sensor."""
        try:
            await self.client.connect()
            local_time = dt_util.now().replace(tzinfo=None, microsecond=0)
            actual = await self.client.set_clock(local_time)
        except (XlinkError, OSError, TimeoutError, ValueError) as err:
            await self.client.close()
            raise HomeAssistantError(
                "Unable to verify clock sync. The clock may already have changed; "
                "check the device clock before trying again."
            ) from err
        self.async_set_updated_data(actual)


class ScheduleCoordinator(DataUpdateCoordinator[tuple[ScheduleSlot, ...]]):
    """Refresh stored schedules independently of clock and feeding availability."""

    def __init__(self, hass, entry, client):
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name="PetTec schedules",
            update_interval=timedelta(minutes=1),
        )
        self.client = client
        self._edit_lock = asyncio.Lock()

    async def _async_update_data(self):
        async with self._edit_lock:
            try:
                await self.client.connect()
                return await self.client.get_schedules()
            except (XlinkError, OSError, TimeoutError, ValueError) as err:
                raise UpdateFailed("Unable to read stored feeding schedules") from err

    async def async_change_slot(self, slot: int, **changes) -> None:
        """Apply one field to a freshly verified slot, preserving the other four."""
        try:
            async with self._edit_lock:
                if self.data is None or not self.last_update_success:
                    raise HomeAssistantError("Stored schedules are unavailable")
                expected = self.data[slot - 1]
                updated = replace(expected, **changes)
                actual = await self.client.update_schedule(
                    slot, updated, expected=expected
                )
                self.async_set_updated_data(actual)
        except ValueError as err:
            raise HomeAssistantError(str(err)) from err
        except (XlinkError, OSError, TimeoutError) as err:
            await self.client.close()
            # Only read after an uncertain write; never repeat the write itself.
            await self.async_refresh()
            raise HomeAssistantError(
                "Unable to verify schedule change. Check the refreshed schedule "
                "before trying again; the change may already have been applied."
            ) from err
