"""Set up an already provisioned feeder through its local connection."""

from ipaddress import IPv4Address
from typing import Any

import probatio
from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_HOST
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import selector, translation
from pettec_xlink.feeder import FeederClient
from pettec_xlink.xlink import AuthenticationError, UnsupportedDeviceError, XlinkError

from .const import CONF_ACCESS_KEY, CONF_SYNC_TIME_STARTUP, DOMAIN
from .coordinator import ScheduleConflict, ScheduleInvalid
from .schedule_rules import (
    WEEKDAY_KEYS,
    ScheduleChanges,
    minute_of_day,
    weekday_mask,
)

KEY_SELECTOR = selector.TextSelector(
    selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
)
USER_SCHEMA = probatio.Schema(
    {
        probatio.Required(CONF_HOST): str,
        probatio.Required(CONF_ACCESS_KEY): KEY_SELECTOR,
    }
)


class PetTecConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Validate local authentication with a harmless clock read."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return PetTecOptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                host = str(IPv4Address(user_input[CONF_HOST].strip()))
            except AttributeError, ValueError:
                return self.async_show_form(
                    step_id="user",
                    data_schema=USER_SCHEMA,
                    errors={"host": "invalid_host"},
                )
            try:
                key = int(user_input[CONF_ACCESS_KEY])
                if not 1 <= key <= 999999999:
                    raise ValueError
            except TypeError, ValueError:
                errors["base"] = "invalid_auth"
            else:
                client = FeederClient(host, key)
                try:
                    await client.connect()
                    await client.get_clock()
                except AuthenticationError:
                    errors["base"] = "invalid_auth"
                except UnsupportedDeviceError:
                    errors["base"] = "unsupported_device"
                except XlinkError, OSError, TimeoutError:
                    errors["base"] = "cannot_connect"
                except Exception:
                    errors["base"] = "unknown"
                finally:
                    await client.close()
                if not errors:
                    if client.mac:
                        await self.async_set_unique_id(client.mac)
                        self._abort_if_unique_id_configured(updates={CONF_HOST: host})
                    else:
                        self._async_abort_entries_match({CONF_HOST: host})
                    return self.async_create_entry(
                        title="PetTec PF 05WD",
                        data={CONF_HOST: host, CONF_ACCESS_KEY: key},
                    )
        return self.async_show_form(
            step_id="user", data_schema=USER_SCHEMA, errors=errors
        )

    async def async_step_reauth(
        self, entry_data: dict[str, Any]
    ) -> config_entries.ConfigFlowResult:
        """Let the user correct the stored access key."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Validate a replacement key without creating a duplicate entry."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                key = int(user_input[CONF_ACCESS_KEY])
                if not 1 <= key <= 999999999:
                    raise ValueError
            except TypeError, ValueError:
                errors["base"] = "invalid_auth"
            else:
                client = FeederClient(entry.data[CONF_HOST], key)
                try:
                    await client.connect()
                    await client.get_clock()
                except AuthenticationError:
                    errors["base"] = "invalid_auth"
                except XlinkError, OSError, TimeoutError:
                    errors["base"] = "cannot_connect"
                except Exception:
                    errors["base"] = "unknown"
                finally:
                    await client.close()
                if not errors:
                    if client.mac:
                        await self.async_set_unique_id(client.mac)
                        self._abort_if_unique_id_mismatch()
                    return self.async_update_reload_and_abort(
                        entry, data_updates={CONF_ACCESS_KEY: key}
                    )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=probatio.Schema(
                {probatio.Required(CONF_ACCESS_KEY): KEY_SELECTOR}
            ),
            errors=errors,
        )


class PetTecOptionsFlow(config_entries.OptionsFlow):
    """Configure stored schedules and opt-in startup clock synchronization."""

    async def async_step_init(self, user_input=None):
        return self.async_show_menu(step_id="init", menu_options=["schedules", "clock"])

    async def async_step_clock(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(
                data={**self.config_entry.options, **user_input}
            )
        return self.async_show_form(
            step_id="clock",
            data_schema=probatio.Schema(
                {
                    probatio.Required(
                        CONF_SYNC_TIME_STARTUP,
                        default=self.config_entry.options.get(
                            CONF_SYNC_TIME_STARTUP, False
                        ),
                    ): bool,
                }
            ),
        )

    async def async_step_schedules(self, user_input=None):
        if self.config_entry.state is not ConfigEntryState.LOADED:
            return self.async_abort(reason="not_loaded")
        schedules = self.config_entry.runtime_data.schedules
        try:
            slots = await schedules.async_read_slots()
        except HomeAssistantError:
            return self.async_abort(reason="cannot_read_schedules")
        if user_input is not None:
            try:
                self._slot = int(user_input["slot"])
                if not 1 <= self._slot <= 5:
                    raise ValueError
            except KeyError, TypeError, ValueError:
                return self.async_abort(reason="invalid_schedule")
            self._original = slots[self._slot - 1]
            return await self.async_step_schedule()
        translations = await translation.async_get_translations(
            self.hass,
            self.context.get("language", self.hass.config.language),
            "selector",
            {DOMAIN},
        )
        prefix = f"component.{DOMAIN}.selector."
        choices = []
        for index, slot in enumerate(slots, 1):
            days = ", ".join(
                translations.get(f"{prefix}weekdays.options.{day}", day.title())
                for bit, day in enumerate(WEEKDAY_KEYS)
                if slot.weekdays & (1 << bit)
            ) or translations.get(f"{prefix}schedule_status.options.no_days", "No days")
            state_key = "enabled" if slot.enabled else "disabled"
            state = translations.get(
                f"{prefix}schedule_status.options.{state_key}", state_key
            )
            choices.append(
                {
                    "value": str(index),
                    "label": f"{index}: {slot.hour:02}:{slot.minute:02}, "
                    f"{slot.quantity} g, {days}, {state}",
                }
            )
        return self.async_show_form(
            step_id="schedules",
            data_schema=probatio.Schema(
                {
                    probatio.Required("slot"): selector.SelectSelector(
                        selector.SelectSelectorConfig(options=choices)
                    )
                }
            ),
        )

    async def async_step_schedule(self, user_input=None):
        if self.config_entry.state is not ConfigEntryState.LOADED:
            return self.async_abort(reason="not_loaded")
        errors = {}
        schedules = self.config_entry.runtime_data.schedules
        if user_input is not None:
            try:
                hour, minute = minute_of_day(user_input["time"])
                changes: ScheduleChanges = {
                    "enabled": user_input["enabled"],
                    "hour": hour,
                    "minute": minute,
                    "quantity": user_input["quantity"],
                    "weekdays": weekday_mask(user_input["weekdays"]),
                }
            except KeyError, TypeError, ValueError:
                errors["base"] = "invalid_schedule"
            else:
                try:
                    await schedules.async_change_slot(
                        self._slot, changes, expected_slot=self._original
                    )
                except ScheduleConflict:
                    errors["base"] = "schedule_changed"
                except ScheduleInvalid:
                    errors["base"] = "invalid_schedule"
                except HomeAssistantError:
                    errors["base"] = "cannot_save_schedule"
                else:
                    return self.async_create_entry(data=dict(self.config_entry.options))
        slot = self._original
        schema = probatio.Schema(
            {
                probatio.Required("enabled"): bool,
                probatio.Required("time"): selector.TimeSelector(),
                probatio.Required("quantity"): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=1, max=200, step=1, unit_of_measurement="g"
                    )
                ),
                probatio.Required("weekdays"): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=list(WEEKDAY_KEYS),
                        translation_key="weekdays",
                        multiple=True,
                    )
                ),
            }
        )
        values = (
            user_input
            if user_input is not None
            else {
                "enabled": slot.enabled,
                "time": f"{slot.hour:02}:{slot.minute:02}:00",
                "quantity": min(200, max(1, slot.quantity)),
                "weekdays": [
                    day
                    for bit, day in enumerate(WEEKDAY_KEYS)
                    if slot.weekdays & (1 << bit)
                ],
            }
        )
        clock = self.config_entry.runtime_data.data
        return self.async_show_form(
            step_id="schedule",
            data_schema=self.add_suggested_values_to_schema(schema, values),
            errors=errors,
            description_placeholders={
                "slot": str(self._slot),
                "clock": clock.isoformat() if clock else "unknown",
            },
        )
