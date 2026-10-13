# pettec-xlink

An async Python client for local control of PetTec PF 05WD feeders over Xlink UDP.
It supports reading and setting the device clock, dispensing a selected amount,
and reading and updating the five stored feeding schedules.

The client uses Python 3.11 or newer and the standard library. It does not require
Home Assistant or a PetTec cloud account. Only the PF 05WD product ID
`160fa6b0c66c03e9160fa6b0c66c4a01` and Xlink device revisions 2 and 3 are supported.
The protocol was recovered from PetTec Android 1.1.4 and tested with a PF 05WD.

Install the published package with `python -m pip install pettec-xlink`.
For development, run `python -m pip install -e './pettec-protocol[test]'` from
this repository's root.

```python
import asyncio

from pettec_xlink import FeederClient


async def main():
    client = FeederClient("192.168.1.50", access_key=123456789)
    try:
        await client.connect()
        print(await client.get_clock())
        for slot in await client.get_schedules():
            print(slot)
    finally:
        await client.close()


asyncio.run(main())
```

The access key must already belong to the feeder. The example only reads its
state. Wi-Fi provisioning is a separate repository tool documented in
[the integration README](https://github.com/Aulos/pettec-feeder-local#feeder-is-not-connected-to-wi-fi).

To dispense, call `await client.dispense(25)` for 25 grams. To set the clock,
pass a naive local `datetime` to `await client.set_clock(value)`. The device does
not report a time zone. Clock changes affect when stored schedules run.

For a schedule update, read the slots first, then pass the original slot as
`expected`. Slot numbers are 1 through 5. Weekday bits 0 through 6 represent
Monday through Sunday. `127` means every day.

```python
from pettec_xlink import ScheduleSlot

slots = await client.get_schedules()
meal = ScheduleSlot(enabled=True, hour=8, minute=30, quantity=25, weekdays=127)
await client.update_schedule(1, meal, expected=slots[0])
```

Updates preserve the other slots and verify all five after writing. A
`ScheduleChangedError` means the original slot no longer matches the feeder.

`AuthenticationError` and `UnsupportedDeviceError` inherit from `XlinkError`.
Malformed inner frames raise `ProtocolError`; client operations report malformed
responses as `XlinkError`. Network operations can also raise `OSError` or
`TimeoutError`. Dispensing is sent once, with no automatic retry. If it fails,
check the bowl before trying again because food may already have been dispensed.
An acknowledgement does not measure the actual food delivered.

SmartLink and Xlink do not provide modern encrypted transport. Use a trusted
local network and keep the Wi-Fi password and access key private.

Run the library tests independently of Home Assistant:

```sh
python -m pip install -e './pettec-protocol[test]'
python -m pytest -q -c pettec-protocol/pyproject.toml pettec-protocol/tests
```

Tests use protocol vectors, mocked command responses and localhost UDP.

Library releases use tags such as `protocol-v0.1.0`. GitHub Actions builds a wheel
and a source distribution, runs the library tests, and publishes to PyPI through
Trusted Publishing. These tags have no GitHub Release object, so HACS continues
to use the integration's `v*` GitHub releases.
