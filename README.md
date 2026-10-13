# PetTec PF 05WD for Home Assistant

Experimental local integration for the older PetTec PF 05WD feeder, using the
Xlink protocol recovered from its Android app. This is a different device family
from the PetTec Cam Buddy / Snoop Cube products.

The feeder owner reports successful Wi-Fi provisioning and manual dispensing.
**Stored schedules have not been tested on hardware yet.** Automated tests cover
the recovered packet formats, a local UDP simulator, and actual Home Assistant
setup, schedule forms and entity lifecycle. Firmware behavior can still differ.

## Features

- Local IPv4 communication; no PetTec account or cloud server.
- Configuration through Home Assistant's integration UI.
- Device clock sensor and connection availability.
- Saved feeding amount, from 1 to 200 grams, matching the stock app's manual UI.
- A button that sends one feeding request. Feeding commands are never retried
  automatically after an uncertain result.
- An editor for five schedules stored on the feeder, with time, quantity,
  weekdays and an enabled flag for each slot.
- Separate Wi-Fi provisioning helper and explicit initial access-key assignment.

Requires Home Assistant **2026.10.0 or later**. Framework tests currently use
Home Assistant 2026.10.1. The integration has no extra runtime dependencies.
The standalone provisioning helper requires Python 3.11 or later.

## Install through HACS

1. In HACS, open **Custom repositories**.
2. Add `https://github.com/Aulos/pettec-feeder-local` with category **Integration**.
3. Download **PetTec PF 05WD (experimental)** and restart Home Assistant.
4. Open **Settings → Devices & services → Add integration**, and search for PetTec.
5. Enter the feeder's IPv4 address and its numeric Xlink access key.

Setup performs authentication and a read-only clock request before it creates
controls. It does not configure Wi-Fi, feed, reset the device, or update firmware.
Reserve the feeder's IPv4 address in your router so it remains stable.

For manual installation, copy `custom_components/pettec_local` into your Home
Assistant configuration's `custom_components` directory and restart.

## Stored feeding schedules

Update the integration in HACS and restart Home Assistant. Open **Settings →
Devices & services → PetTec PF 05WD → Configure**. The editor reads all five
slots and shows their current settings. Select a slot, choose the time, amount
in whole grams, weekdays and whether it is enabled, then submit. Open Configure
again to inspect the stored result or edit another slot.

The time follows the **feeder's own clock**. The form displays the most recent
clock reading; check that its local date and time are correct before enabling
any schedule. This version does not set the clock or adjust it for daylight
saving time. Schedules are intended to run on the feeder without Home Assistant;
that behavior still needs a hardware test.

Saving reads the current list again, replaces only the selected slot, sends
one write and reads all five slots back. The other four slots are preserved.
If the selected slot changed while you were editing, saving is refused. If a
write or verification fails, the change may already have been applied. Cancel
and reopen Configure to inspect the current settings before trying again.
Writes are never automatically retried. Opening or cancelling the editor
does not write schedules, and saving does not send a manual feeding command.

To disable a slot, clear **Enabled** and submit. Verify the first scheduled
meal with the feeder attended. Account for every enabled slot and any Home
Assistant feeding automations to avoid overlapping meals.

## Feeder is not connected to Wi-Fi

The `tools/provision_wifi.py` helper reproduces the APK's High-Flying SmartLink
v7 multicast and v3 broadcast setup packets. It asks for the Wi-Fi password
locally, without putting it into command history. It transmits only when you run
the command, and cannot place the feeder into pairing mode by itself.

Run it from this repository on a computer connected to the intended **2.4 GHz
Wi-Fi** network:

```sh
python3 tools/provision_wifi.py --ssid "YOUR_WIFI_NAME" --network 192.168.1.50/24 --initialize-key
```

Replace `192.168.1.50/24` with the **computer's Wi-Fi IPv4 address and network
prefix**, not the feeder's address. Put the feeder into its documented Wi-Fi
setup mode before starting. The helper waits up to 60 seconds for an
acknowledgement, then prints the feeder IP and the requested access key.

`--initialize-key` is optional and explicitly authorizes assigning a key only
when the feeder advertises an unpaired state. It refuses to overwrite an existing
key. Save the requested key even if assignment times out, because the feeder
may have accepted it before the acknowledgement was lost.

If the feeder retains an old key, this helper cannot recover it. Whether the
device's Wi-Fi reset also clears authorization or schedules is **not known**.
Do not repeatedly reset or assign keys to work around an error. See
[commissioning evidence and limitations](docs/commissioning.md).

## First hardware test

Start with Home Assistant setup and the device clock sensor. A failed setup is
an error to investigate, not a reason to erase firmware. Check the IP address,
network isolation, product identity, protocol revision and access key.

Once the read-only probe works, test a small feeding amount with the feeder
attended. Verify the actual food weight; existing feeder calibration still
determines delivery accuracy. A transport or command acknowledgement does not
measure the food that physically reached the bowl.

If a feeding request times out, check the bowl before pressing the button again.
The first request may already have dispensed food. Manual feeding leaves stored
schedules unchanged; the Configure editor explicitly changes the selected
slot. Keep a reliable feeding arrangement until scheduled behavior has been tested.

## Scope and limitations

The supported product ID is `160fa6b0c66c03e9160fa6b0c66c4a01`, recovered from
the PF 05WD label QR. Other products and Xlink device revisions outside 2 and 3
are rejected. The network implementation uses the configured address for unicast
discovery; the original SDK broadcasts, and unicast acceptance is not hardware
verified. Replies must pass source, MAC, length, checksum and command checks.

There is no cloud fallback, firmware flashing, automatic food
dispensing on reconnect, or key brute-force/recovery. The clock is shown as a
local wall-clock string because the device does not report its time zone.

SmartLink and local Xlink do not provide modern encrypted transport. Use the
provisioning helper on a trusted local network; keep Wi-Fi credentials and the
access key private. Home Assistant stores the configured key in its usual
configuration storage.

## Development

```sh
python3.14 -m venv .venv
.venv/bin/python -m pip install '.[homeassistant-test]' ruff
.venv/bin/python -m pytest -q
.venv/bin/ruff check .
```

Tests make no connections to PetTec cloud services or a physical feeder. UDP
tests use localhost, and provisioning tests replace socket operations with a
simulation.

GitHub's Linux validation completes normally. The macOS investigation environment
showed a Python shutdown crash after the test assertions passed; its cause remains
unresolved. A summary of passed assertions alone is not a successful test run.

The [Xlink evidence ledger](docs/xlink-evidence.md) records the APK digest,
source classes and DEX offsets. Decompiled Java is investigation material and
is not distributed in this repository. This project is independent of PetTec,
Homerun and Xlink.
