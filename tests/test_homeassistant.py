"""Exercise the real Home Assistant classes against a controlled feeder client."""

from dataclasses import replace
from datetime import datetime, time
from types import MappingProxyType, SimpleNamespace
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

import pytest

pytest.importorskip("homeassistant")

from homeassistant import auth, data_entry_flow, loader
from homeassistant.config_entries import ConfigEntries, ConfigEntry, ConfigEntryState
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED, EntityCategory
from homeassistant.core import CoreState, HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers import device_registry, entity_registry, frame
from homeassistant.helpers.update_coordinator import UpdateFailed
from homeassistant.setup import async_setup_component

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
async def hass(tmp_path, unused_tcp_port):
    instance = HomeAssistant(str(tmp_path))
    loader.async_setup(instance)
    instance.config_entries = ConfigEntries(instance, {})
    device_registry.async_setup(instance)
    await device_registry.async_load(instance)
    await entity_registry.async_load(instance)
    instance.auth = await auth.auth_manager_from_config(instance, [], [])
    assert await async_setup_component(
        instance,
        "http",
        {"http": {"server_host": "127.0.0.1", "server_port": unused_tcp_port}},
    )
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
    mock = SimpleNamespace(
        mac="00:11:22:33:44:55",
        connect=AsyncMock(),
        close=AsyncMock(),
        get_clock=AsyncMock(return_value=CLOCK),
        set_clock=AsyncMock(return_value=CLOCK),
        dispense=AsyncMock(),
        get_schedules=AsyncMock(return_value=SCHEDULES),
        update_schedule=AsyncMock(),
    )

    async def update(slot, schedule, *, expected):
        current = list(mock.get_schedules.return_value)
        if current[slot - 1] != expected:
            raise ScheduleChangedError("changed")
        if schedule.enabled and (
            not 1 <= schedule.quantity <= 200 or not schedule.weekdays & 127
        ):
            raise ValueError("Enabled schedules need an amount and weekdays")
        current[slot - 1] = schedule
        mock.get_schedules.return_value = tuple(current)
        return tuple(current)

    mock.update_schedule.side_effect = update
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
    frame.async_setup(hass)
    with patch("custom_components.pettec_local.FeederClient", return_value=client):
        await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    numbers = list(hass.data["number"].entities)
    assert len(numbers) == 6
    number = next(number for number in numbers if number.translation_key == "quantity")
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
    assert client.get_schedules.await_count == 3
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


def schedule_entities(hass):
    return [
        entity
        for platform in ("time", "number", "select", "switch")
        for entity in hass.data[platform].entities
        if hasattr(entity, "slot_number")
    ]


async def test_all_schedule_controls_are_configuration_entities_on_same_device(
    hass, entry, client
):
    await load_feeder(hass, entry, client)
    entities = schedule_entities(hass)
    assert len(entities) == 20
    assert len({entity.unique_id for entity in entities}) == 20
    registry = entity_registry.async_get(hass)
    sensor = next(iter(hass.data["sensor"].entities))
    device_id = registry.async_get(sensor.entity_id).device_id
    assert device_id is not None
    for entity in entities:
        registered = registry.async_get(entity.entity_id)
        assert registered.entity_category is EntityCategory.CONFIG
        assert registered.device_id == device_id
        assert entity.available
    client.update_schedule.assert_not_awaited()


async def test_device_page_can_configure_and_enable_an_empty_slot(hass, entry, client):
    await load_feeder(hass, entry, client)
    controls = {
        entity.translation_key: entity
        for entity in schedule_entities(hass)
        if entity.slot_number == 2
    }
    for domain, service, key, data in [
        ("time", "set_value", "schedule_time", {"time": "07:45:00"}),
        ("number", "set_value", "schedule_amount", {"value": 15}),
        ("select", "select_option", "schedule_days", {"option": "Mon, Wed, Fri"}),
        ("switch", "turn_on", "schedule_enabled", {}),
    ]:
        await hass.services.async_call(
            domain,
            service,
            {"entity_id": controls[key].entity_id, **data},
            blocking=True,
        )
    expected = ScheduleSlot(True, 7, 45, 15, 21)
    assert entry.runtime_data.schedules.data == (SCHEDULES[0], expected, *SCHEDULES[2:])
    assert hass.states.get(controls["schedule_time"].entity_id).state == "07:45:00"
    assert hass.states.get(controls["schedule_amount"].entity_id).state == "15"
    assert hass.states.get(controls["schedule_days"].entity_id).state == "Mon, Wed, Fri"
    assert hass.states.get(controls["schedule_enabled"].entity_id).state == "on"
    assert client.update_schedule.await_count == 4
    client.dispense.assert_not_awaited()
    client.set_clock.assert_not_awaited()


async def test_weekday_control_preserves_reserved_bit_and_allows_all_combinations(
    hass, entry, client
):
    from custom_components.pettec_local.select import DAY_OPTIONS

    assert set(DAY_OPTIONS.values()) == set(range(128))
    await load_feeder(hass, entry, client)
    control = next(
        entity for entity in hass.data["select"].entities if entity.slot_number == 1
    )
    assert control.current_option == "Every day"
    await control.async_select_option("Mon, Wed, Fri")
    assert entry.runtime_data.schedules.data[0].weekdays == 0x95
    assert control.current_option == "Mon, Wed, Fri"


@pytest.mark.parametrize(
    "platform,key,value",
    [
        ("time", "schedule_time", time(8, 0, 1)),
        ("number", "schedule_amount", 1.5),
        ("select", "schedule_days", "Not a weekday"),
    ],
)
async def test_invalid_device_page_fields_do_not_write(
    hass, entry, client, platform, key, value
):
    await load_feeder(hass, entry, client)
    control = next(
        entity
        for entity in hass.data[platform].entities
        if entity.translation_key == key and entity.slot_number == 1
    )
    method = {
        "time": "async_set_value",
        "number": "async_set_native_value",
        "select": "async_select_option",
    }[platform]
    with pytest.raises(HomeAssistantError):
        await getattr(control, method)(value)
    client.update_schedule.assert_not_awaited()


async def test_empty_slot_cannot_be_enabled_until_configured(hass, entry, client):
    await load_feeder(hass, entry, client)
    control = next(
        entity
        for entity in hass.data["switch"].entities
        if getattr(entity, "slot_number", None) == 2
    )
    with pytest.raises(HomeAssistantError, match="amount and weekdays"):
        await control.async_turn_on()
    assert control.is_on is False
    assert entry.runtime_data.schedules.data == SCHEDULES


async def test_failed_device_page_write_reads_actual_state_without_retry(
    hass, entry, client
):
    await load_feeder(hass, entry, client)
    control = next(
        entity
        for entity in hass.data["number"].entities
        if getattr(entity, "slot_number", None) == 1
    )

    async def applied_without_ack(slot, schedule, **kwargs):
        current = list(client.get_schedules.return_value)
        current[slot - 1] = schedule
        client.get_schedules.return_value = tuple(current)
        raise TimeoutError("acknowledgement lost")

    client.update_schedule.side_effect = applied_without_ack
    with pytest.raises(HomeAssistantError, match="may already have been applied"):
        await control.async_set_native_value(33)
    client.update_schedule.assert_awaited_once()
    assert client.get_schedules.await_count == 2
    assert control.native_value == 33
    assert entry.runtime_data.schedules.data[1:] == SCHEDULES[1:]
    client.dispense.assert_not_awaited()


async def test_schedule_read_failure_keeps_manual_controls_and_preferences_available(
    hass, entry, client
):
    client.get_schedules.side_effect = XlinkError("unsupported schedule response")
    await load_feeder(hass, entry, client)
    assert not any(entity.available for entity in schedule_entities(hass))
    buttons = list(hass.data["button"].entities)
    assert all(button.available for button in buttons)
    preference = next(
        entity
        for entity in hass.data["switch"].entities
        if entity.translation_key == "startup_clock_sync"
    )
    assert preference.available
    await preference.async_turn_on()
    assert entry.options[CONF_SYNC_TIME_STARTUP] is True
    client.update_schedule.assert_not_awaited()
    client.close.assert_not_awaited()


async def test_startup_preference_on_device_page_is_saved_without_network_writes(
    hass, entry, client
):
    await load_feeder(hass, entry, client)
    hass.config_entries.async_update_entry(entry, options={"quantity": 67})
    preference = next(
        entity
        for entity in hass.data["switch"].entities
        if entity.translation_key == "startup_clock_sync"
    )
    assert preference.is_on is False
    registry = entity_registry.async_get(hass)
    assert (
        registry.async_get(preference.entity_id).entity_category
        is EntityCategory.CONFIG
    )
    for service, enabled in [("turn_on", True), ("turn_off", False)]:
        await hass.services.async_call(
            "switch", service, {"entity_id": preference.entity_id}, blocking=True
        )
        await hass.async_block_till_done()
        assert entry.options == {"quantity": 67, CONF_SYNC_TIME_STARTUP: enabled}
        assert preference.is_on is enabled
        assert hass.states.get(preference.entity_id).state == (
            "on" if enabled else "off"
        )
    client.update_schedule.assert_not_awaited()
    client.set_clock.assert_not_awaited()
    client.dispense.assert_not_awaited()


async def test_editor_updates_device_page_schedule_controls(hass, entry, client):
    result = await start_schedule_editor(hass, entry, client)
    await hass.config_entries.options.async_configure(result["flow_id"], SCHEDULE_INPUT)
    controls = {
        entity.translation_key: entity
        for entity in schedule_entities(hass)
        if entity.slot_number == 1
    }
    assert controls["schedule_time"].native_value == time(9, 15)
    assert controls["schedule_amount"].native_value == 30
    assert controls["schedule_days"].current_option == "Mon, Sun"
    assert controls["schedule_enabled"].is_on is True


async def test_failed_read_does_not_allow_schedule_writes(hass, entry, client):
    await load_feeder(hass, entry, client)
    client.get_schedules.side_effect = TimeoutError()
    await entry.runtime_data.schedules.async_refresh()
    with pytest.raises(HomeAssistantError, match="unavailable"):
        await entry.runtime_data.schedules.async_change_slot(1, quantity=33)
    client.update_schedule.assert_not_awaited()


async def test_stale_device_page_edit_refreshes_without_overwriting(
    hass, entry, client
):
    await load_feeder(hass, entry, client)
    changed = replace(SCHEDULES[0], quantity=40)
    client.get_schedules.return_value = (changed, *SCHEDULES[1:])
    with pytest.raises(HomeAssistantError):
        await entry.runtime_data.schedules.async_change_slot(1, quantity=33)
    assert entry.runtime_data.schedules.data[0] == changed
    client.update_schedule.assert_awaited_once()


async def test_card_actions_are_atomic_and_reject_stale_edits(hass, entry, client):
    await load_feeder(hass, entry, client)
    registry = entity_registry.async_get(hass)
    feed = registry.async_get_entity_id("button", DOMAIN, f"{entry.entry_id}_feed")
    time_entity = registry.async_get_entity_id(
        "time", DOMAIN, f"{entry.entry_id}_schedule_1_schedule_time"
    )
    await hass.services.async_call(
        DOMAIN, "dispense", {"entity_id": feed, "quantity": 42}, blocking=True
    )
    client.dispense.assert_awaited_once_with(42)
    assert entry.runtime_data.quantity == 10
    data = {
        "entity_id": time_entity,
        "slot": 1,
        "enabled": True,
        "hour": 19,
        "minute": 15,
        "quantity": 35,
        "weekdays": 65,
        "expected": {
            "enabled": True,
            "hour": 8,
            "minute": 30,
            "quantity": 25,
            "weekdays": 127,
        },
    }
    await hass.services.async_call(DOMAIN, "save_schedule", data, blocking=True)
    client.update_schedule.assert_awaited_once_with(
        1, ScheduleSlot(True, 19, 15, 35, 193), expected=SCHEDULES[0]
    )
    assert entry.runtime_data.schedules.data[1:] == SCHEDULES[1:]
    with pytest.raises(HomeAssistantError, match="changed elsewhere"):
        await hass.services.async_call(DOMAIN, "save_schedule", data, blocking=True)
    assert client.update_schedule.await_count == 1
    with pytest.raises(HomeAssistantError, match="Incorrect PetTec"):
        await hass.services.async_call(
            DOMAIN, "dispense", {"entity_id": time_entity, "quantity": 5}, blocking=True
        )
    assert client.dispense.await_count == 1


@pytest.mark.parametrize("quantity", [0, 201, 1.5, True, "20"])
async def test_card_rejects_invalid_amount_without_feeding(
    hass, entry, client, quantity
):
    import voluptuous as vol

    await load_feeder(hass, entry, client)
    feed = entity_registry.async_get(hass).async_get_entity_id(
        "button", DOMAIN, f"{entry.entry_id}_feed"
    )
    with pytest.raises(vol.Invalid):
        await hass.services.async_call(
            DOMAIN, "dispense", {"entity_id": feed, "quantity": quantity}, blocking=True
        )
    client.dispense.assert_not_called()


async def test_card_resource_is_served_and_registration_is_idempotent(
    hass, entry, client
):
    from aiohttp.test_utils import TestClient, TestServer

    from custom_components.pettec_local.dashboard import async_setup_dashboard

    await load_feeder(hass, entry, client)
    await async_setup_dashboard(hass)
    async with TestClient(TestServer(hass.http.app)) as http:
        response = await http.get("/pettec_local/pettec-cards.js?v=0.5.3")
        assert response.status == 200
        assert "pettec-schedule-card" in await response.text()


async def test_card_respects_entity_control_permissions(hass, entry, client):
    from unittest.mock import Mock

    from homeassistant.core import Context
    from homeassistant.exceptions import Unauthorized

    await load_feeder(hass, entry, client)
    feed = entity_registry.async_get(hass).async_get_entity_id(
        "button", DOMAIN, f"{entry.entry_id}_feed"
    )
    user = SimpleNamespace(
        permissions=SimpleNamespace(check_entity=Mock(return_value=False))
    )
    with patch.object(hass.auth, "async_get_user", return_value=user):
        with pytest.raises(Unauthorized):
            await hass.services.async_call(
                DOMAIN,
                "dispense",
                {"entity_id": feed, "quantity": 20},
                blocking=True,
                context=Context(user_id="restricted-user"),
            )
    client.dispense.assert_not_called()


async def test_card_resource_updates_lazy_storage_and_preserves_other_resources(
    hass, entry, client
):
    from homeassistant.components.lovelace.const import LOVELACE_DATA
    from homeassistant.components.lovelace.resources import ResourceStorageCollection
    from homeassistant.loader import async_get_integration

    from custom_components.pettec_local.dashboard import (
        CARD_PATH,
        async_register_card_resource,
    )

    await load_feeder(hass, entry, client)
    lovelace = hass.data[LOVELACE_DATA]
    resources = lovelace.resources
    original = resources.async_items()[0]
    await resources.async_update_item(
        original["id"], {"url": CARD_PATH + "?v=0.5.1", "res_type": "js"}
    )
    await resources.async_create_item({"url": CARD_PATH, "res_type": "module"})
    other = await resources.async_create_item(
        {"url": "/local/unrelated-card.js", "res_type": "module"}
    )
    foreign = await resources.async_create_item(
        {"url": "https://example.org" + CARD_PATH, "res_type": "module"}
    )
    await resources.store.async_save({"items": resources.async_items()})
    lovelace.resources = ResourceStorageCollection(hass, resources.ll_config)
    assert not lovelace.resources.loaded
    await async_register_card_resource(hass)
    version = (await async_get_integration(hass, DOMAIN)).version
    expected = [
        {"id": original["id"], "url": f"{CARD_PATH}?v={version}", "type": "module"},
        other,
        foreign,
    ]
    assert lovelace.resources.async_items() == expected
    await async_register_card_resource(hass)
    assert lovelace.resources.async_items() == expected
    client.dispense.assert_not_called()


async def test_card_resource_leaves_yaml_managed_resources_unchanged(
    hass, entry, client
):
    from homeassistant.components.lovelace.const import LOVELACE_DATA
    from homeassistant.components.lovelace.resources import ResourceYAMLCollection

    from custom_components.pettec_local.dashboard import async_register_card_resource

    await load_feeder(hass, entry, client)
    lovelace = hass.data[LOVELACE_DATA]
    lovelace.resource_mode = "yaml"
    items = [{"url": "/local/another-card.js", "type": "module"}]
    lovelace.resources = ResourceYAMLCollection(items.copy())
    await async_register_card_resource(hass)
    assert lovelace.resources.async_items() == items


async def test_card_resource_storage_error_does_not_disable_actions(hass, caplog):
    from homeassistant.components.lovelace.const import LOVELACE_DATA

    from custom_components.pettec_local.dashboard import async_setup_dashboard

    assert await async_setup_component(hass, "lovelace", {})
    with patch.object(
        hass.data[LOVELACE_DATA].resources,
        "async_get_info",
        side_effect=OSError("Storage unavailable"),
    ):
        await async_setup_dashboard(hass)
    assert hass.services.has_service(DOMAIN, "dispense")
    assert hass.services.has_service(DOMAIN, "save_schedule")
    assert "add it manually" in caplog.text


async def test_polish_entity_and_selector_translations_load(hass, entry, client):
    from homeassistant.helpers import translation

    await load_feeder(hass, entry, client)
    entities = await translation.async_get_translations(hass, "pl", "entity", {DOMAIN})
    assert entities[f"component.{DOMAIN}.entity.button.feed.name"] == (
        "Podaj wybraną ilość karmy"
    )
    assert entities[
        f"component.{DOMAIN}.entity.select.schedule_days.state.Mon, Sun"
    ] == ("pon., niedz.")
    selectors = await translation.async_get_translations(
        hass, "pl", "selector", {DOMAIN}
    )
    assert (
        selectors[f"component.{DOMAIN}.selector.weekdays.options.mon"] == "Poniedziałek"
    )
