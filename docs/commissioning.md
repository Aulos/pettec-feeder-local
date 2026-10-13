# Connecting a PF 05WD without the old app

This is an experimental offline commissioning tool. Its packet formats come
from the original Android APK. It has not been tested with a physical feeder.
It does not need an Xlink cloud account, Android, or the vendor's native SDK.

Use a computer with Python 3.11 or newer connected **through Wi-Fi** to the
intended **2.4 GHz** network. Use its Wi-Fi adapter's current IPv4 address and
subnet prefix below, not the router's address. The helper supports UTF-8 SSIDs
up to 32 bytes and ASCII Wi-Fi passphrases. Only one feeder should be in setup
mode. The provisioning protocol transmits Wi-Fi credentials in reversible
encodings over the local wireless network. Use a separate IoT network if
appropriate.

From this repository's directory:

```sh
python3 tools/provision_wifi.py --ssid 'Your Wi-Fi name' \
    --network 192.168.1.50/24 --initialize-key
```

The password is prompted without echoing it or putting it in command-line
history. Both SmartLink v7 multicast and the app's concurrent v3 broadcast
sender run for at most 60 seconds by default. No traffic is sent by importing
the module, running `--help`, or running its tests.

The feeder must already be in Wi-Fi setup mode. The APK describes restarting
Wi-Fi as follows: slide to unlock, press Settings, hold the manual-feeding
button for more than three seconds, choose number 100 using the arrows, then
press Settings to confirm. This is evidence of the app's instructions, not a
tested reset procedure. We have not established whether it clears access keys
or affects stored schedules. Choose to perform it only after preserving any
settings you need.

If the feeder acknowledges provisioning, the helper prints its IP and MAC.
`--initialize-key` then generates a new Xlink access key, prints it **before**
sending the assignment, and attempts assignment only if discovery advertises
an unpaired device. Save the requested key even if assignment times out: the
device may have received it. The helper never replaces an existing key and
never sends feeding commands. Without `--initialize-key`, it only configures
Wi-Fi.

If the feeder still has an existing unknown key, commissioning stops. A Wi-Fi
reset may leave that key intact. This integration does not provide a proven
way to clear it. If assignment succeeds, use the displayed IP and key in the
Home Assistant integration; reserve that IP in the router.

## Static evidence

APK identity: `cn.xlink.pettec`, version 1.1.4.
SHA-256: `4bc347b9213453c6cf73d4a751e0e29ee3a7e7b7916d11486febed3622f612f0`.

- `ConnectWifiPresenter.onActivityStart`: device type 1 uses
  `com.hiflying.smartlink.v7.MulticastSmartLinker`, with SSID and password.
- `MulticastSmartLinker.setupSendAction`: runs v7 and v3 concurrently by default.
- `MulticastSmartLinkerSendAction.generateData2Send/encodedPMK`: four length
  bytes, SSID, passphrase, PBKDF2-HMAC-SHA1 PMK with 4096 iterations and 32-byte
  output; pad to an even byte count.
- `MulticastSmartLinkerSendAction.run`: five 30-byte sync packets to
  `239.48.0.0:6000`; data pairs in `239.46.X.Y:6000`, payload length `30+index`.
- `SnifferSmartLinkerSendAction.sendContents`: subnet broadcast UDP 49999,
  payloads of byte `05`; recovered lengths and timing are covered by tests.
- `AbstractSmartLinker$4.run`: listens on UDP 49999 for `smart_config<MAC>`.
- `ConnectNewDevice3Usecase`: stock app follows Wi-Fi provisioning with account
  login, subscription-key retrieval, cloud subscription and HTTP metadata.
  Those steps are deliberately absent from the offline helper.
- APK resource `0x7f070128`: code 100 instructions for restarting Wi-Fi.

Focused decompiled classes and v7 DEX instructions are retained separately in
the original investigation's `pettec-commissioning` directory. These are static
observations; firmware acceptance and reset behavior remain unverified.
