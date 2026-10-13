"""Set up an already provisioned feeder through its local connection."""

from datetime import time
from ipaddress import IPv4Address
from typing import Any

import probatio
from homeassistant import config_entries
from homeassistant.const import CONF_HOST
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import CONF_ACCESS_KEY, DOMAIN
from .feeder import FeederClient, ScheduleChangedError
from .protocol import ScheduleSlot
from .xlink import AuthenticationError, UnsupportedDeviceError, XlinkError

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
        return PetTecScheduleOptionsFlow()

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


WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


class PetTecScheduleOptionsFlow(config_entries.OptionsFlow):
    """Edit one stored timer, with no writes until the user submits it."""

    async def async_step_init(self, user_input=None):
        if not hasattr(self.config_entry, "runtime_data"):
            return self.async_abort(reason="not_loaded")
        client = self.config_entry.runtime_data.client
        try:
            await client.connect()
            slots = await client.get_schedules()
        except XlinkError, OSError, TimeoutError, ValueError:
            await client.close()
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
        choices = []
        for index, slot in enumerate(slots, 1):
            days = (
                ", ".join(
                    day.title()
                    for bit, day in enumerate(WEEKDAYS)
                    if slot.weekdays & (1 << bit)
                )
                or "no days"
            )
            state = "enabled" if slot.enabled else "disabled"
            choices.append(
                {
                    "value": str(index),
                    "label": f"{index}: {slot.hour:02}:{slot.minute:02}, "
                    f"{slot.quantity} g, {days}, {state}",
                }
            )
        return self.async_show_form(
            step_id="init",
            data_schema=probatio.Schema(
                {
                    probatio.Required("slot"): selector.SelectSelector(
                        selector.SelectSelectorConfig(options=choices)
                    )
                }
            ),
        )

    async def async_step_schedule(self, user_input=None):
        errors = {}
        client = self.config_entry.runtime_data.client
        if user_input is not None:
            try:
                clock_time = time.fromisoformat(user_input["time"])
                if clock_time.second or clock_time.microsecond or clock_time.tzinfo:
                    raise ValueError("Schedules have minute precision")
                quantity = user_input["quantity"]
                if isinstance(quantity, bool) or not float(quantity).is_integer():
                    raise ValueError("Quantity must be a whole number")
                if not 1 <= quantity <= 200:
                    raise ValueError("Quantity out of range")
                # Preserve the high weekday bit used by some stock-app defaults.
                days = self._original.weekdays & 0x80
                for day in user_input["weekdays"]:
                    days |= 1 << WEEKDAYS.index(day)
                schedule = ScheduleSlot(
                    user_input["enabled"],
                    clock_time.hour,
                    clock_time.minute,
                    int(quantity),
                    days,
                )
                if schedule.enabled and not days & 0x7F:
                    raise ValueError("Choose at least one weekday")
            except KeyError, TypeError, ValueError:
                errors["base"] = "invalid_schedule"
            else:
                try:
                    await client.update_schedule(
                        self._slot, schedule, expected=self._original
                    )
                except ScheduleChangedError:
                    errors["base"] = "schedule_changed"
                except XlinkError, OSError, TimeoutError, ValueError:
                    await client.close()
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
                        options=[
                            {"value": day, "label": day.title()} for day in WEEKDAYS
                        ],
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
                    for bit, day in enumerate(WEEKDAYS)
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
