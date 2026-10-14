# Additional PF 05WD command evidence

Artifact: PetTec Android 1.1.4, package `cn.xlink.pettec`, SHA-256
`4bc347b9213453c6cf73d4a751e0e29ee3a7e7b7916d11486febed3622f612f0`.

REA's Android provider was unavailable because its caller-supplied JADX MCP JAR
was not configured. The existing Androguard 4.1.4 installation was used to inspect
Dalvik instructions, reconstruct the relevant Java methods, and resolve packaged
string resources. This was static analysis, not execution or feeder traffic.

| Command | Request and response | APK evidence |
| --- | --- | --- |
| `10` | Empty request; two-byte signed big-endian grams | `CmdManager.getRemainFoodAmount`, `PetFeedManualViewPresenter$1.onNext`, `ByteUtil.byteToShort(byte[])`, `PetFeedManualActivity.showRemainFoodAmount` |
| `11` | Empty request; one-byte percentage 0–100; signed-byte codes 101–127 show Charging | `CmdManager.getRemainBattery`, `WifiDeviceInfoActivity$13.onNext`, resource `charging` |
| `12` | App button sends `02`; boolean true builder sends `01`. Reply `01` shows retracting, `02` extending | `CmdManager.controlBowl`, `PetFeedManualActivity.onClick`, `PetFeedManualViewPresenter$3.onNext`, `PetFeedManualActivity.showControlBowlSuccess`, resources `tips_bowl_out` and `tips_bowl_in` |
| `23` | One-byte index; leading reply byte followed by five-byte records: hour, minute, big-endian signed grams, feeding-type byte | `CmdManager.getFeederDetail`, `PetFeedDetailActivity.obtainFeedDetail`, `PetFeedDetailActivity$1.onNext`, `$1$1.convert` |
| `24` | `FF` queries; 10–60 in steps of 5 sets minutes; `64` hexadecimal disables. Query returns the setting, writes expect status `00` | `BowlRecycleSettingActivity.onCreate`, `onOptionsItemSelected`, `syncView`, `$1.onNext`, resource `bowl_recycle_after_min` |
| `26` | Request `00 00`; app describes scale tare; library requires status `00` before reading weight | `CmdManager.resetFeedWeight`, `WifiDeviceInfoActivity.resetFeedWeight`, `$4.onNext`, resources `reset_feed_weight` and `tips_reset_feed_weight` |

The tray response describes movement, not a settled state, and its values do not
establish that request and reply bytes have identical meanings. Only the app's
button payload is exposed; no open/close cover state is fabricated.

The APK ignores the leading history byte. Its meaning and the history index's
date mapping are unresolved. The decoder preserves the header and type code,
validates record lengths and time bounds, and rejects negative history quantities.
Signed remaining-food readings are preserved, including negative scale readings.

The weight-reset subscriber ignores its response data. Requiring a single zero
status is a conservative implementation consistent with the other setting
commands; different hardware acknowledgements will be reported as uncertain,
not falsely marked successful. Acknowledgements do not prove physical movement
or correct tare conditions.

Tests use literal request vectors, malformed and timeout responses, concurrent
interval changes, and Home Assistant's public service interfaces. No real feeder
commands were sent during this implementation. Unsupported optional reads cannot
disable existing clock, schedule, or feeding controls. No write is retried.
