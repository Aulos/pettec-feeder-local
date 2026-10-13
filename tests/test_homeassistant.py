"""Exercise the real Home Assistant classes against a controlled feeder client."""

from datetime import datetime
from types import MappingProxyType
from unittest.mock import AsyncMock, patch

import pytest

pytest.importorskip("homeassistant")

from homeassistant import data_entry_flow, loader
from homeassistant.config_entries import ConfigEntries, ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers import device_registry, entity_registry, frame
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.pettec_local import async_setup_entry
from custom_components.pettec_local.config_flow import PetTecConfigFlow
from custom_components.pettec_local.const import DOMAIN, MAX_QUANTITY
from custom_components.pettec_local.coordinator import FeederCoordinator
from custom_components.pettec_local.number import FeedQuantity
from custom_components.pettec_local.xlink import AuthenticationError, XlinkError

CLOCK = datetime(2026, 10, 13, 12, 0, 0)


@pytest.fixture
async def hass(tmp_path):
    instance = HomeAssistant(str(tmp_path))
    loader.async_setup(instance)
    instance.config_entries = ConfigEntries(instance, {})
    yield instance
    await instance.async_stop(force=True)


@pytest.fixture
def entry():
    return ConfigEntry(
        version=1,
        minor_version=1,
        domain=DOMAIN,
        title="PetTec PF 05WD",
        data={"host": "192.0.2.5", "access_key": 123456},
        options={},
        source="user",
        unique_id="00:11:22:33:44:55",
        discovery_keys=MappingProxyType({}),
        subentries_data=(),
    )


@pytest.fixture
def client():
    mock = AsyncMock()
    mock.mac = "00:11:22:33:44:55"
    mock.get_clock.return_value = CLOCK
    return mock


async def test_config_flow_reads_clock_and_closes_without_feeding(hass, client):
    flow = PetTecConfigFlow()
    flow.hass = hass
    flow.context = {"source": "user"}
    with patch(
        "custom_components.pettec_local.config_flow.FeederClient", return_value=client
    ):
        result = await flow.async_step_user(
            {"host": "192.0.2.5", "access_key": "123456"}
        )
    assert result["type"] == "create_entry"
    assert result["data"] == {"host": "192.0.2.5", "access_key": 123456}
    assert flow.unique_id == client.mac
    client.get_clock.assert_awaited_once()
    client.close.assert_awaited_once()
    client.dispense.assert_not_called()


@pytest.mark.parametrize(
    ("failure", "error"),
    [
        (AuthenticationError("rejected"), "invalid_auth"),
        (TimeoutError(), "cannot_connect"),
    ],
)
async def test_config_flow_connection_error_cleanup(hass, client, failure, error):
    client.connect.side_effect = failure
    flow = PetTecConfigFlow()
    flow.hass = hass
    flow.context = {"source": "user"}
    with patch(
        "custom_components.pettec_local.config_flow.FeederClient", return_value=client
    ):
        result = await flow.async_step_user(
            {"host": "192.0.2.5", "access_key": "123456"}
        )
    assert result["errors"] == {"base": error}
    client.close.assert_awaited_once()
    client.dispense.assert_not_called()


@pytest.mark.parametrize("key", ["", "NaN", "0", "1000000000"])
async def test_config_flow_rejects_invalid_key_without_network(hass, key):
    flow = PetTecConfigFlow()
    flow.hass = hass
    flow.context = {"source": "user"}
    with patch("custom_components.pettec_local.config_flow.FeederClient") as factory:
        result = await flow.async_step_user({"host": "192.0.2.5", "access_key": key})
    assert result["errors"] == {"base": "invalid_auth"}
    factory.assert_not_called()


@pytest.mark.parametrize("host", ["", "feeder.local", "https://192.0.2.5", "::1"])
async def test_config_flow_rejects_invalid_host_without_network(hass, host):
    flow = PetTecConfigFlow()
    flow.hass = hass
    flow.context = {"source": "user"}
    with patch("custom_components.pettec_local.config_flow.FeederClient") as factory:
        result = await flow.async_step_user({"host": host, "access_key": "123456"})
    assert result["errors"] == {"host": "invalid_host"}
    factory.assert_not_called()


async def test_config_flow_prevents_duplicate_mac(hass, entry, client):
    hass.config_entries._entries[entry.entry_id] = entry
    flow = PetTecConfigFlow()
    flow.hass = hass
    flow.handler = DOMAIN
    flow.context = {"source": "user"}
    with patch(
        "custom_components.pettec_local.config_flow.FeederClient", return_value=client
    ):
        with pytest.raises(data_entry_flow.AbortFlow, match="already_configured"):
            await flow.async_step_user({"host": "192.0.2.5", "access_key": "123456"})
    client.close.assert_awaited_once()
    client.dispense.assert_not_called()


async def test_reauth_updates_existing_entry_without_feeding(hass, entry, client):
    hass.config_entries._entries[entry.entry_id] = entry
    flow = PetTecConfigFlow()
    flow.hass = hass
    flow.handler = DOMAIN
    flow.context = {"source": "reauth", "entry_id": entry.entry_id}
    with (
        patch(
            "custom_components.pettec_local.config_flow.FeederClient",
            return_value=client,
        ),
        patch.object(hass.config_entries, "async_reload", new_callable=AsyncMock),
    ):
        result = await flow.async_step_reauth_confirm({"access_key": "654321"})
        await hass.async_block_till_done()
    assert result["type"] == "abort"
    assert result["reason"] == "reauth_successful"
    assert entry.data["access_key"] == 654321
    assert len(hass.config_entries.async_entries(DOMAIN)) == 1
    client.close.assert_awaited_once()
    client.dispense.assert_not_called()


async def test_setup_failure_closes_client(hass, entry, client):
    client.connect.side_effect = TimeoutError()
    with patch("custom_components.pettec_local.FeederClient", return_value=client):
        with pytest.raises(Exception):
            await async_setup_entry(hass, entry)
    assert client.close.await_count >= 1
    client.dispense.assert_not_called()


async def test_coordinator_status_poll_never_dispenses(hass, entry, client):
    coordinator = FeederCoordinator(hass, entry, client)
    assert await coordinator._async_update_data() == CLOCK
    assert coordinator.device_info["identifiers"] == {(DOMAIN, client.mac)}
    client.dispense.assert_not_called()


@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        (AuthenticationError("rejected"), ConfigEntryAuthFailed),
        (TimeoutError(), UpdateFailed),
    ],
)
async def test_coordinator_reports_failures_and_closes(
    hass, entry, client, failure, expected
):
    client.get_clock.side_effect = failure
    coordinator = FeederCoordinator(hass, entry, client)
    with pytest.raises(expected):
        await coordinator._async_update_data()
    client.close.assert_awaited_once()


async def test_ambiguous_feed_failure_is_never_retried(hass, entry, client):
    client.dispense.side_effect = XlinkError("acknowledgement lost")
    coordinator = FeederCoordinator(hass, entry, client)
    with pytest.raises(HomeAssistantError, match="may have received"):
        await coordinator.async_dispense()
    client.dispense.assert_awaited_once_with(10)
    client.close.assert_awaited_once()


async def test_quantity_rejects_fractional_or_out_of_range(hass, entry, client):
    number = FeedQuantity(FeederCoordinator(hass, entry, client))
    number.hass = hass
    for value in (0, MAX_QUANTITY + 1, 1.5, float("nan")):
        with pytest.raises(HomeAssistantError):
            await number.async_set_native_value(value)
    client.dispense.assert_not_called()


async def test_quantity_is_saved_without_feeding(hass, entry, client):
    hass.config_entries._entries[entry.entry_id] = entry
    device_registry.async_setup(hass)
    await device_registry.async_load(hass)
    await entity_registry.async_load(hass)
    frame.async_setup(hass)
    with patch("custom_components.pettec_local.FeederClient", return_value=client):
        await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    numbers = list(hass.data["number"].entities)
    assert len(numbers) == 1
    number = numbers[0]
    await number.async_set_native_value(25)
    assert entry.options["quantity"] == 25
    assert number.native_value == 25
    assert hass.states.get(number.entity_id).state == "25"
    client.dispense.assert_not_called()
    sensors = list(hass.data["sensor"].entities)
    buttons = list(hass.data["button"].entities)
    assert len(sensors) == len(buttons) == 1
    assert hass.states.get(sensors[0].entity_id).state == CLOCK.isoformat()
    await hass.services.async_call(
        "button", "press", {"entity_id": buttons[0].entity_id}, blocking=True
    )
    client.dispense.assert_awaited_once_with(25)
    await hass.config_entries.async_unload(entry.entry_id)
    assert not list(hass.data["number"].entities)
