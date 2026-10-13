# PetTec PF 05WD for Home Assistant

Experimental local integration for the older PetTec PF 05WD feeder, using the
Xlink protocol recovered from its Android app. This is a different device family
from the PetTec Cam Buddy / Snoop Cube products.

**No physical feeder has been tested yet.** The tests cover the recovered packet
formats, a local UDP simulator, and actual Home Assistant setup and entity
lifecycle. They do not establish that a particular firmware accepts these packets.

## Features

- Local IPv4 communication; no PetTec account or cloud server.
- Configuration through Home Assistant's integration UI.
- Device clock sensor and connection availability.
- Saved feeding amount, from 1 to 200 grams, matching the stock app's manual UI.
- A button that sends one feeding request. Feeding commands are never retried
  automatically after an uncertain result.
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
The first request may already have dispensed food. Existing on-device schedules
are not changed by this integration, so account for them when creating Home
Assistant automations. Keep a reliable feeding arrangement until device behavior
has been tested.

## Scope and limitations

The supported product ID is `160fa6b0c66c03e9160fa6b0c66c4a01`, recovered from
the PF 05WD label QR. Other products and Xlink device revisions outside 2 and 3
are rejected. The network implementation uses the configured address for unicast
discovery; the original SDK broadcasts, and unicast acceptance is not hardware
verified. Replies must pass source, MAC, length, checksum and command checks.

There is no cloud fallback, firmware flashing, schedule editing, automatic food
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

The [Xlink evidence ledger](docs/xlink-evidence.md) records the APK digest,
source classes and DEX offsets. Decompiled Java is investigation material and
is not distributed in this repository. This project is independent of PetTec,
Homerun and Xlink.
