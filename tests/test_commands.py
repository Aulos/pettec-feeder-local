"""One-shot command outcomes and session handling."""

from datetime import datetime

import pytest

pytest.importorskip("homeassistant")

from pettec_xlink.xlink import AuthenticationError, XlinkError

from custom_components.pettec_local.commands import (
    CommandFailed,
    FeederCommands,
    UncertainWrite,
)

CLOCK = datetime(2026, 10, 25, 12, 0)


class FakeClient:
    def __init__(self):
        self.calls = []
        self.closed = 0
        self.connect_failure = None
        self.failures = {}
        self.results = {
            "dispense": None,
            "set_clock": CLOCK,
            "control_tray": "extending",
            "reset_feeding_weight": 0,
            "set_tray_retraction_interval": 20,
            "get_feeder_details": "details",
        }

    async def connect(self):
        self.calls.append("connect")
        if self.connect_failure:
            raise self.connect_failure

    async def close(self):
        self.closed += 1

    async def _call(self, name, *args):
        self.calls.append((name, *args))
        failure = self.failures.get(name)
        if failure:
            raise failure("test failure")
        return self.results[name]

    async def dispense(self, grams):
        return await self._call("dispense", grams)

    async def set_clock(self, value):
        return await self._call("set_clock", value)

    async def control_tray(self):
        return await self._call("control_tray")

    async def reset_feeding_weight(self):
        return await self._call("reset_feeding_weight")

    async def set_tray_retraction_interval(self, minutes):
        return await self._call("set_tray_retraction_interval", minutes)

    async def get_feeder_details(self, selector):
        return await self._call("get_feeder_details", selector)


async def test_connect_timeout_is_safe_and_closes():
    client = FakeClient()
    client.connect_failure = TimeoutError("down")

    with pytest.raises(CommandFailed, match="nothing was sent"):
        await FeederCommands(client).dispense(10)

    assert client.calls == ["connect"]
    assert client.closed == 1


async def test_connect_authentication_is_safe():
    client = FakeClient()
    client.connect_failure = AuthenticationError("rejected")

    with pytest.raises(CommandFailed, match="access key"):
        await FeederCommands(client).control_tray()

    assert client.calls == ["connect"]
    assert client.closed == 1


async def test_lost_acknowledgement_is_uncertain_and_sent_once():
    client = FakeClient()
    client.failures["dispense"] = XlinkError

    with pytest.raises(UncertainWrite, match="Check the bowl"):
        await FeederCommands(client).dispense(10)

    assert client.calls == ["connect", ("dispense", 10)]
    assert client.closed == 1


async def test_rejection_after_send_is_safe():
    client = FakeClient()
    client.failures["control_tray"] = AuthenticationError

    with pytest.raises(CommandFailed, match="nothing was changed"):
        await FeederCommands(client).control_tray()

    assert client.calls == ["connect", ("control_tray",)]
    assert client.closed == 1


async def test_validation_failure_is_safe():
    client = FakeClient()
    client.failures["dispense"] = ValueError

    with pytest.raises(CommandFailed, match="test failure"):
        await FeederCommands(client).dispense(0)

    assert client.calls == ["connect", ("dispense", 0)]
    assert client.closed == 1


async def test_read_failure_is_not_uncertain():
    client = FakeClient()
    client.failures["get_feeder_details"] = TimeoutError

    with pytest.raises(CommandFailed, match="Unable to read") as caught:
        await FeederCommands(client).read_feeder_details(3)

    assert not isinstance(caught.value, UncertainWrite)
    assert client.closed == 1


async def test_success_returns_values_without_closing():
    client = FakeClient()
    commands = FeederCommands(client)

    assert await commands.sync_clock(CLOCK) == CLOCK
    assert await commands.control_tray() == "extending"
    assert await commands.reset_feeding_weight() == 0
    assert await commands.set_tray_interval(20) == 20
    assert await commands.read_feeder_details(3) == "details"
    assert await commands.dispense(10) is None
    assert client.closed == 0
