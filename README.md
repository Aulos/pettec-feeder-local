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
- Manual clock sync and optional sync when Home Assistant starts.
- Saved feeding amount, from 1 to 200 grams, matching the stock app's manual UI.
- A button that sends one feeding request. Feeding commands are never retried
  automatically after an uncertain result.
- Device-page controls for five stored schedules: time, amount, weekdays and
  an enabled switch, plus an editor for changing a whole slot at once.
- Separate Wi-Fi provisioning helper and explicit initial access-key assignment.

Requires Home Assistant **2026.10.0 or later**. Framework tests currently use
Home Assistant 2026.10.1. The integration has no extra runtime dependencies.
The standalone provisioning helper requires Python 3.11 or later.

## Install through HACS

1. In HACS, open **Custom repositories**.
2. Add `https://github.com/Aulos/pettec-feeder-local` with category **Integration**.
3. Download **PetTec PF 05WD** and restart Home Assistant.
4. Open **Settings → Devices & services → Add integration**, and search for PetTec.
5. Enter the feeder's IPv4 address and its numeric Xlink access key.

Setup performs authentication and read-only clock and schedule requests before
it creates controls. If schedule reads fail, schedule controls are unavailable;
manual feeding and clock controls can still work. Clock sync on startup is off by default and must be enabled explicitly.
Setup does not configure Wi-Fi, feed, reset the device, or update firmware.
Reserve the feeder's IPv4 address in your router so it remains stable.

For manual installation, copy `custom_components/pettec_local` into your Home
Assistant configuration's `custom_components` directory and restart.

## Stored feeding schedules

Update the integration in HACS and restart Home Assistant. Open the feeder's
**device page → Configuration**. Each of the five slots has **Enabled**, **Time**,
**Amount** and **Weekdays** controls. The weekday dropdown includes every day,
Monday–Friday, Saturday–Sunday and all individual day combinations. No separate
frontend card is required.

Each field saves immediately and verifies the stored result. Disable a slot
before changing several fields, check its time, amount and weekdays, then enable
it again. An unused slot may show zero grams and no days; choose 1–200 grams
and at least one weekday before enabling it. Schedule controls refresh once a
minute and after successful writes. Failed writes trigger a read, not a retry.

For a single Save covering all fields, the existing **Configure → Stored feeding
schedules** editor remains available, including weekday multiselect. It reads
all five slots before editing and updates the device-page controls after saving.

The time follows the **feeder's own clock**. The form displays the most recent
clock reading; check that its local date and time are correct before enabling
any schedule. Use **Sync device clock** if it needs correcting. Schedules are
intended to run on the feeder without Home Assistant;
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

## Clock synchronization

Press the feeder's **Sync device clock** button to set its clock to Home
Assistant's current local date and time. Home Assistant's configured time zone
is used, including its current daylight saving offset. One clock write is sent,
then the clock is read back to verify it and update the device clock sensor.

For automatic synchronization, turn on **Sync device clock on Home Assistant
startup** in the feeder device page's **Configuration** section. This defaults
to off. The switch saves the preference immediately and does not contact the
feeder; use the clock button to sync immediately. The existing Configure dialog
continues to use the same saved preference. On
the next Home Assistant restart, one attempt is made after startup completes.
Integration reloads and reconnects do not trigger synchronization. The integration
must finish loading before the startup event; if it misses startup or sync fails,
use the button. Failures are logged, and clock writes are not retried automatically.

This is not continuous synchronization. If daylight saving time changes while
Home Assistant keeps running, press the button or use a separate automation
for it. Synchronization does not rewrite schedule records or send manual feeding
requests, but changing the clock affects when existing schedules run. Test the
first clock correction with the feeder attended. Clock writes still need hardware
verification; failed readback can mean the clock changed without being confirmed.

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

## Food level, battery, tray and feeding history

The device page shows **Remaining food** in grams and **Remaining battery** in
percent. The battery becomes unknown with a `charging: true` attribute when the
feeder reports the app's charging code instead of a percentage. Optional reads
that fail only make their corresponding new entities unavailable.

**Control food tray** sends the original app's tray-button command once. The
response describes extending or retracting, but the integration does not infer
a settled tray position. Inspect the tray before repeating a failed request.

Under **Configuration**, **Automatic tray retraction** offers Disabled or 10–60
minutes in five-minute steps. Startup only reads this preference. Changes are
written once and read back; a lost acknowledgement does not trigger a retry.

**Tare food weight** resets the hopper scale's tare offset. Follow the feeder's
physical tare procedure before pressing it. It does not perform dispensing
calibration. The integration sends the command once and refreshes the food
reading; do not repeat an uncertain reset without inspecting that reading.

Use **Developer tools → Actions → PetTec: Read selected feeding history** to read
`0x23` details. Choose the feeder's Feed button and a whole-number history index
from 0 to 255. The action returns records with `time`, `quantity` in grams,
`feeding_type`, and the original `type_code`. It also returns the reply's raw
`header`. The APK ignores that byte; its meaning and the index-to-date mapping
remain unverified. This action does not change the feeder.

These formats come from the Android APK and simulated replies. Support for the
new commands on each feeder firmware still needs hardware verification. See
[the command evidence](docs/extra-commands.md).

## Development

```sh
python3.14 -m venv .venv
.venv/bin/python -m pip install '.[homeassistant-test]' ruff
.venv/bin/python -m pytest -q tests
.venv/bin/ruff check .
```

The integration uses its pinned published protocol release. Develop and test
the library in a separate environment so its version can advance independently:

```sh
python3 -m venv .venv-protocol
.venv-protocol/bin/python -m pip install -e './pettec-protocol[test]'
.venv-protocol/bin/python -m pytest -q -c pettec-protocol/pyproject.toml pettec-protocol/tests
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

## Protocol library and releases

The repository contains the HACS integration in `custom_components/pettec_local`
and the independent [`pettec-xlink` Python package](pettec-protocol/README.md) in
`pettec-protocol`. The integration pins the published library version in its
manifest. Home Assistant installs that dependency from PyPI during setup.

The library contains the existing feeder commands, packet encoding, validation
and local UDP transport. Its tests run independently on Python 3.11 and 3.14.
The integration and provisioning tests also run against the extracted library.

See [library publishing setup](pettec-protocol/PUBLISHING.md) for PyPI Trusted
Publishing configuration. Publish the library before releasing an integration
version that depends on it. Library tags use `protocol-v*` without GitHub Release
objects; integration GitHub releases keep the `v*` tags used by HACS.

## Dashboard cards

The integration includes two cards in the integration download:

- **PetTec · Feeding schedule**: five meals, time and amount inputs, individual
  weekday buttons, enable switches, and a **Save meal** button for each meal.
  A save writes the whole meal together and verifies it on the feeder. Edits stay
  local until saved. Conflicting changes are rejected instead of overwritten.
- **PetTec · Feed now**: a compact card with a feeder illustration, status indicator,
  a 1–200 g input, and an icon-only **Dispense** button (bowl icon). It follows
  your Home Assistant theme and switches to a single row in wider cards. Each click sends
  the entered amount once. This does not change the device-page amount setting.
  An error is never automatically retried; check the bowl before trying again.

From v0.5.2, the integration **automatically registers and updates the shared
JavaScript resource** when Home Assistant manages resources through the UI.
Existing manual entries for `/pettec_local/pettec-cards.js` are updated in place;
duplicate entries for that local file are consolidated. Other resources are preserved.

After updating in HACS, **restart Home Assistant and refresh your browser**.
Edit a dashboard, choose **Add card**, search for **PetTec**, and select your
feeder in the card editor. Both cards use the same resource; keep this repository
registered in HACS as an **Integration**.

If resources are managed in YAML, add this entry to your `lovelace.resources`
configuration manually (the integration does not edit YAML):

```yaml
lovelace:
  resource_mode: yaml
  resources:
    - url: /pettec_local/pettec-cards.js?v=0.7.0
      type: module
```

If automatic registration fails, the integration logs the error while leaving
feeder controls available. You can add the same URL manually under
**Settings → Dashboards → ⋮ → Resources**, selecting **JavaScript module**.

The equivalent card YAML is:

```yaml
type: custom:pettec-schedule-card
device_id: YOUR_HOME_ASSISTANT_DEVICE_ID
```

```yaml
type: custom:pettec-feed-card
device_id: YOUR_HOME_ASSISTANT_DEVICE_ID
```

Both accept an optional `title`. Use the card editor to choose the device without
copying its ID. These are **dashboard cards**; the device page continues to use
Home Assistant's standard Configuration controls. The card title links directly to
the selected device page. No separate HACS frontend repository or JavaScript build
step is needed.

Card development checks (Node.js 22 or newer):

```sh
npm ci
npx playwright install chromium
npm test
```

The browser tests use simulated Home Assistant states and service calls; they do
not connect to or dispense from a real feeder. JavaScript test dependencies are
not needed by Home Assistant or included in its integration directory.

## Languages

English, Polish, German, French, and Ukrainian translations are included for setup,
options, entity labels, weekday choices, action descriptions, and dashboard cards. Cards use the
language selected in your Home Assistant profile, with English as the fallback.
Refresh the browser after changing language. Custom card titles are preserved.
Technical errors returned by the backend may still include English diagnostics.
