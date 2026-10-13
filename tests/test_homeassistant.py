"""Exercise the real Home Assistant classes against a controlled feeder client."""

from datetime import datetime
from types import MappingProxyType, SimpleNamespace
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

import pytest

pytest.importorskip("homeassistant")

from homeassistant import data_entry_flow, loader
from homeassistant.config_entries import ConfigEntries, ConfigEntry, ConfigEntryState
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import CoreState, HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers import device_registry, entity_registry, frame
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.pettec_local import async_setup_entry
from custom_components.pettec_local.config_flow import PetTecConfigFlow
from custom_components.pettec_local.const import (
    CONF_SYNC_TIME_STARTUP,
    DOMAIN,
    MAX_QUANTITY,
)
from custom_components.pettec_local.coordinator import FeederCoordinator
from custom_components.pettec_local.feeder import ScheduleChangedError
from custom_components.pettec_local.number import FeedQuantity
from custom_components.pettec_local.protocol import ScheduleSlot
from custom_components.pettec_local.xlink import AuthenticationError, XlinkError

CLOCK = datetime(2026, 10, 13, 12, 0, 0)


@pytest.fixture
async def hass(tmp_path):
    instance = HomeAssistant(str(tmp_path))
    loader.async_setup(instance)
    instance.config_entries = ConfigEntries(instance, {})
    yield instance
    for flow in instance.config_entries.options.async_progress():
        instance.config_entries.options.async_abort(flow["flow_id"])
    for config_entry in instance.config_entries.async_entries():
        if config_entry.state == ConfigEntryState.LOADED:
            await instance.config_entries.async_unload(config_entry.entry_id)
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
    return SimpleNamespace(
        mac="00:11:22:33:44:55",
        connect=AsyncMock(),
        close=AsyncMock(),
        get_clock=AsyncMock(return_value=CLOCK),
        set_clock=AsyncMock(return_value=CLOCK),
        dispense=AsyncMock(),
        get_schedules=AsyncMock(),
        update_schedule=AsyncMock(),
    )


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
        (AuthenticationError, "invalid_auth"),
        (TimeoutError, "cannot_connect"),
    ],
)
async def test_config_flow_connection_error_cleanup(hass, client, failure, error):
    client.connect.side_effect = failure("test failure")
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
        (AuthenticationError, ConfigEntryAuthFailed),
        (TimeoutError, UpdateFailed),
    ],
)
async def test_coordinator_reports_failures_and_closes(
    hass, entry, client, failure, expected
):
    client.get_clock.side_effect = failure("test failure")
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
    assert len(sensors) == 1
    assert len(buttons) == 2
    assert hass.states.get(sensors[0].entity_id).state == CLOCK.isoformat()
    await hass.services.async_call(
        "button", "press", {"entity_id": buttons[0].entity_id}, blocking=True
    )
    client.dispense.assert_awaited_once_with(25)
    await hass.config_entries.async_unload(entry.entry_id)
    assert not list(hass.data["number"].entities)


SCHEDULES = (
    ScheduleSlot(True, 8, 30, 25, 0xFF),
    *(ScheduleSlot(False, 0, 0, 0, 0) for _ in range(4)),
)
SCHEDULE_INPUT = {
    "enabled": True,
    "time": "09:15:00",
    "quantity": 30,
    "weekdays": ["mon", "sun"],
}


async def load_feeder(hass, entry, client):
    """Load actual platforms with a controlled client."""
    hass.config_entries._entries[entry.entry_id] = entry
    device_registry.async_setup(hass)
    await device_registry.async_load(hass)
    await entity_registry.async_load(hass)
    frame.async_setup(hass)
    client.get_schedules.return_value = SCHEDULES
    with patch("custom_components.pettec_local.FeederClient", return_value=client):
        assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def start_schedule_editor(hass, entry, client):
    """Use the real options-flow manager and integration lifecycle."""
    await load_feeder(hass, entry, client)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["step_id"] == "init"
    assert result["type"] == "menu"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "schedules"}
    )
    assert result["step_id"] == "schedules"
    client.update_schedule.assert_not_awaited()
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"slot": "1"}
    )
    assert result["step_id"] == "schedule"
    assert result["description_placeholders"]["clock"] == CLOCK.isoformat()
    return result


async def test_schedule_options_read_then_save_without_feeding(hass, entry, client):
    result = await start_schedule_editor(hass, entry, client)
    assert client.get_schedules.await_count == 2
    # A number change made while the editor is open must also be preserved.
    hass.config_entries.async_update_entry(entry, options={"quantity": 67})
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], SCHEDULE_INPUT
    )
    assert result["type"] == "create_entry"
    assert entry.options == {"quantity": 67}
    client.update_schedule.assert_awaited_once_with(
        1, ScheduleSlot(True, 9, 15, 30, 0xC1), expected=SCHEDULES[0]
    )
    client.dispense.assert_not_awaited()


async def test_schedule_options_disable_slot(hass, entry, client):
    result = await start_schedule_editor(hass, entry, client)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {**SCHEDULE_INPUT, "enabled": False, "weekdays": []}
    )
    assert result["type"] == "create_entry"
    assert not client.update_schedule.await_args.args[1].enabled
    client.dispense.assert_not_awaited()


@pytest.mark.parametrize(
    "changes", [{"weekdays": []}, {"quantity": 1.5}, {"time": "09:15:30"}]
)
async def test_schedule_options_reject_invalid_edits(hass, entry, client, changes):
    result = await start_schedule_editor(hass, entry, client)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {**SCHEDULE_INPUT, **changes}
    )
    assert result["errors"] == {"base": "invalid_schedule"}
    client.update_schedule.assert_not_awaited()
    client.dispense.assert_not_awaited()


@pytest.mark.parametrize(
    "failure,error",
    [
        (TimeoutError, "cannot_save_schedule"),
        (ScheduleChangedError, "schedule_changed"),
        (XlinkError, "cannot_save_schedule"),
    ],
)
async def test_schedule_options_save_failure_is_not_success(
    hass, entry, client, failure, error
):
    result = await start_schedule_editor(hass, entry, client)

    async def fail(*args, **kwargs):
        raise failure("test failure")

    client.update_schedule.side_effect = fail
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], SCHEDULE_INPUT
    )
    assert result["errors"] == {"base": error}
    client.update_schedule.assert_awaited_once()
    client.dispense.assert_not_awaited()


async def test_schedule_options_read_failure_aborts_without_writing(
    hass, entry, client
):
    result = await start_schedule_editor(hass, entry, client)
    hass.config_entries.options.async_abort(result["flow_id"])
    client.get_schedules.side_effect = XlinkError("wrong length")
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "schedules"}
    )
    assert result["type"] == "abort"
    assert result["reason"] == "cannot_read_schedules"
    client.update_schedule.assert_not_awaited()
    client.close.assert_awaited_once()


async def test_schedule_options_require_loaded_integration(hass, entry):
    hass.config_entries._entries[entry.entry_id] = entry
    frame.async_setup(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "schedules"}
    )
    assert result["type"] == "abort"
    assert result["reason"] == "not_loaded"


async def test_schedule_editor_stops_if_integration_is_unloaded(hass, entry, client):
    result = await start_schedule_editor(hass, entry, client)
    await hass.config_entries.async_unload(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], SCHEDULE_INPUT
    )
    assert result["type"] == "abort"
    assert result["reason"] == "not_loaded"
    client.update_schedule.assert_not_awaited()


async def test_clock_options_are_off_by_default_and_preserve_quantity(hass, entry):
    hass.config_entries._entries[entry.entry_id] = entry
    frame.async_setup(hass)
    hass.config_entries.async_update_entry(entry, options={"quantity": 67})
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "clock"}
    )
    assert result["step_id"] == "clock"
    marker = next(iter(result["data_schema"].schema))
    assert marker.default() is False
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_SYNC_TIME_STARTUP: True}
    )
    assert result["type"] == "create_entry"
    assert entry.options == {"quantity": 67, CONF_SYNC_TIME_STARTUP: True}


@pytest.mark.parametrize("month,hour", [(1, 13), (7, 14)])
async def test_manual_clock_button_uses_ha_local_time_and_updates_sensor(
    hass, entry, client, month, hour
):
    await load_feeder(hass, entry, client)
    local = datetime(2026, month, 13, hour, tzinfo=ZoneInfo("Europe/Warsaw"))
    expected = local.replace(tzinfo=None)
    client.set_clock.return_value = expected
    buttons = list(hass.data["button"].entities)
    button = next(
        button for button in buttons if button.translation_key == "sync_clock"
    )
    with patch(
        "custom_components.pettec_local.coordinator.dt_util.now", return_value=local
    ):
        await hass.services.async_call(
            "button", "press", {"entity_id": button.entity_id}, blocking=True
        )
    client.set_clock.assert_awaited_once_with(expected)
    assert entry.runtime_data.data == expected
    sensor = next(iter(hass.data["sensor"].entities))
    assert hass.states.get(sensor.entity_id).state == expected.isoformat()
    client.dispense.assert_not_awaited()
    client.update_schedule.assert_not_awaited()


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("state", [CoreState.not_running, CoreState.starting])
async def test_startup_clock_sync_honors_option_and_runs_once(
    hass, entry, client, enabled, state
):
    hass.set_state(state)
    await load_feeder(hass, entry, client)
    hass.config_entries.async_update_entry(
        entry, options={CONF_SYNC_TIME_STARTUP: enabled}
    )
    client.set_clock.assert_not_awaited()
    for _ in range(2):
        hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
        await hass.async_block_till_done()
    assert client.set_clock.await_count == int(enabled)
    client.dispense.assert_not_awaited()
    client.update_schedule.assert_not_awaited()


async def test_unload_removes_startup_clock_listener(hass, entry, client):
    await load_feeder(hass, entry, client)
    hass.config_entries.async_update_entry(
        entry, options={CONF_SYNC_TIME_STARTUP: True}
    )
    await hass.config_entries.async_unload(entry.entry_id)
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
    await hass.async_block_till_done()
    client.set_clock.assert_not_awaited()


async def test_loading_while_ha_is_running_does_not_sync_clock(hass, entry, client):
    hass.set_state(CoreState.running)
    await load_feeder(hass, entry, client)
    hass.config_entries.async_update_entry(
        entry, options={CONF_SYNC_TIME_STARTUP: True}
    )
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
    await hass.async_block_till_done()
    client.set_clock.assert_not_awaited()


async def test_startup_clock_failure_is_logged_and_not_retried(
    hass, entry, client, caplog
):
    await load_feeder(hass, entry, client)
    hass.config_entries.async_update_entry(
        entry, options={CONF_SYNC_TIME_STARTUP: True}
    )
    client.set_clock.side_effect = TimeoutError("lost acknowledgement")
    for _ in range(2):
        hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
        await hass.async_block_till_done()
    client.set_clock.assert_awaited_once()
    client.close.assert_awaited_once()
    assert "Startup clock sync failed" in caplog.text
    client.dispense.assert_not_awaited()
