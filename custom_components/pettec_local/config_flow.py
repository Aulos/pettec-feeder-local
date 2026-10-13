"""Set up an already provisioned feeder through its local connection."""

from ipaddress import IPv4Address
from typing import Any

import probatio
from homeassistant import config_entries
from homeassistant.const import CONF_HOST
from homeassistant.helpers import selector

from .const import CONF_ACCESS_KEY, DOMAIN
from .feeder import FeederClient
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
