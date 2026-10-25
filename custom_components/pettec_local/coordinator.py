"""Read-only polling and explicit dispensing."""

import asyncio
import logging
from datetime import datetime, timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util
from pettec_xlink.feeder import FeederClient, ScheduleChangedError
from pettec_xlink.protocol import SCHEDULE_COUNT, ScheduleSlot
from pettec_xlink.xlink import AuthenticationError, XlinkError

from .const import CONF_QUANTITY, DEFAULT_QUANTITY, DOMAIN
from .schedule_rules import ScheduleChanges, apply_changes, matches_expected

_LOGGER = logging.getLogger(__name__)


class ScheduleConflict(HomeAssistantError):
    """The stored schedule changed while an editor was open."""


class ScheduleInvalid(HomeAssistantError):
    """The requested schedule fields cannot be stored."""


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
        self.telemetry = TelemetryCoordinator(hass, entry, client)

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

    async def async_dispense(self, quantity: int | None = None) -> None:
        """Send one request; a lost acknowledgement must not trigger a retry."""
        try:
            await self.client.dispense(self.quantity if quantity is None else quantity)
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

    async def async_control_tray(self):
        try:
            await self.client.connect()
            await self.client.control_tray()
        except (XlinkError, OSError, TimeoutError, ValueError) as err:
            await self.client.close()
            raise HomeAssistantError(
                "Tray control failed. Inspect the tray before trying again; "
                "it may already be moving."
            ) from err

    async def async_reset_feeding_weight(self):
        try:
            await self.client.connect()
            actual = await self.client.reset_feeding_weight()
        except (XlinkError, OSError, TimeoutError, ValueError) as err:
            await self.client.close()
            await self.telemetry.async_refresh()
            raise HomeAssistantError(
                "Weight reset could not be verified. Check the remaining food "
                "reading before trying again; the reset may already have happened."
            ) from err
        self.telemetry.async_set_updated_data(
            {**(self.telemetry.data or {}), "food": actual}
        )

    async def async_read_feeder_details(self, selector):
        try:
            await self.client.connect()
            return await self.client.get_feeder_details(selector)
        except (XlinkError, OSError, TimeoutError, ValueError) as err:
            await self.client.close()
            raise HomeAssistantError("Unable to read selected feeding history") from err


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

    async def async_read_slots(self) -> tuple[ScheduleSlot, ...]:
        """Read all five slots under the edit lock and refresh the cache."""
        async with self._edit_lock:
            try:
                await self.client.connect()
                slots = await self.client.get_schedules()
            except (XlinkError, OSError, TimeoutError, ValueError) as err:
                await self.client.close()
                raise HomeAssistantError(
                    "Unable to read stored feeding schedules"
                ) from err
            self.async_set_updated_data(slots)
            return slots

    async def async_change_slot(
        self,
        slot: int,
        changes: ScheduleChanges,
        *,
        expected_slot: ScheduleSlot | None = None,
    ) -> None:
        """Apply one field set to a freshly verified slot, preserving the rest."""
        if type(slot) is not int or not 1 <= slot <= SCHEDULE_COUNT:
            raise ScheduleInvalid("Schedule slot must be from 1 to 5")
        try:
            async with self._edit_lock:
                if self.data is None or not self.last_update_success:
                    raise ScheduleInvalid("Stored schedules are unavailable")
                expected = self.data[slot - 1]
                if not matches_expected(expected, expected_slot):
                    raise ScheduleConflict(
                        "This meal changed elsewhere. Reload it before saving."
                    )
                updated = apply_changes(expected, changes)
                actual = await self.client.update_schedule(
                    slot, updated, expected=expected
                )
                self.async_set_updated_data(actual)
        except ScheduleChangedError as err:
            await self.async_refresh()
            raise ScheduleConflict(
                "This meal changed elsewhere. Reload it before saving."
            ) from err
        except ValueError as err:
            raise ScheduleInvalid(str(err)) from err
        except (XlinkError, OSError, TimeoutError) as err:
            await self.client.close()
            # Only read after an uncertain write; never repeat the write itself.
            await self.async_refresh()
            raise HomeAssistantError(
                "Unable to verify schedule change. Check the refreshed schedule "
                "before trying again; the change may already have been applied."
            ) from err


class TelemetryCoordinator(DataUpdateCoordinator[dict]):
    """Optional reads must not disable the working clock or feeding controls."""

    def __init__(self, hass, entry, client):
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name="PetTec telemetry",
            update_interval=timedelta(minutes=1),
        )
        self.client = client
        self.entry = entry

    async def _async_update_data(self):
        data = {}
        for key, method in (
            ("food", self.client.get_remaining_food),
            ("battery", self.client.get_remaining_battery),
            ("tray_interval", self.client.get_tray_retraction_interval),
        ):
            try:
                await self.client.connect()
                data[key] = await method()
            except XlinkError, OSError, TimeoutError, ValueError:
                _LOGGER.debug("Unable to read optional PetTec %s", key)
        return data

    async def async_set_interval(self, minutes):
        try:
            await self.client.connect()
            actual = await self.client.set_tray_retraction_interval(minutes)
        except (XlinkError, OSError, TimeoutError, ValueError) as err:
            await self.client.close()
            await self.async_refresh()
            raise HomeAssistantError(
                "Unable to verify tray interval. Check its refreshed value before "
                "trying again; the change may already have been applied."
            ) from err
        self.async_set_updated_data({**(self.data or {}), "tray_interval": actual})
