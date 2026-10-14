# Additional library commands

These methods are available on `FeederClient` starting with 0.3.0. They do not add
Home Assistant entities or services. They send one command per call, without
retries, and validate the response frame and opcode. Except for the calibration
status and existing tray movement response, results are raw `bytes`. A returned
packet is not proof that an operation completed successfully; the caller must
interpret its status using evidence for their device.

| Method | Opcode | Request payload |
| --- | --- | --- |
| `check_connection(value, address=0xFFFF)` | `01` | One unsigned byte; explicit inner address |
| `get_device_info(address=0xFFFF)` | `02` | Empty; explicit inner address |
| `erase_flash_block(address)` | `07` | Opaque address bytes |
| `write_flash_block(address, data)` | `08` | Address bytes, one-byte length, up to 64 data bytes |
| `read_flash_block(address, length)` | `09` | Address bytes, one-byte requested length |
| `get_module_address()` | `0A` | Empty |
| `reset_device()` | `0E` | Empty |
| `get_feed_record()` | `0F` | Empty |
| `control_tray(request_code=2)` | `12` | `01` or `02`; default preserves the app button behavior |
| `get_latest_feed_data_offset()` | `13` | Empty; the APK also calls this `probeExceptionList` |
| `control_cradle_head(code)` | `13` | One caller-supplied byte |
| `get_alarm_packet()` | `14` | Empty |
| `control_camera_direction(direction)` | `15` | left=`01`, right=`02`, up=`03`, down=`04` |
| `begin_upgrade(metadata)` | `18` | Opaque caller-supplied metadata |
| `write_mcu_chunk(index, data)` | `19` | Two-byte big-endian index, up to 64 data bytes |
| `write_mcu_chunk_checksummed(index, data)` | `19` | Two-byte index, additive byte checksum, 1–128 data bytes |
| `check_mcu_file(payload)` | `1A` | Opaque caller-supplied payload |
| `complete_upgrade(metadata)` | `1B` | Opaque caller-supplied metadata |
| `set_upgrade_enabled(enabled)` | `1C` | `01`, then `00` or `01`; requires a boolean |
| `get_firmware_version()` | `1D` | `01` |
| `get_feeder_info()` | `1F` | Empty |
| `begin_audio_upload(date, file_size, checksum)` | `20` | Five zeros, ten date bytes, four-byte file size, checksum byte |
| `write_audio_chunk(index, data)` | `21` | Two-byte index, additive byte checksum, 1–128 data bytes |
| `complete_audio_upload(date, file_size, checksum)` | `22` | Same metadata as audio start |
| `get_feed_calibration_status()` | `27` | `01`; returns an integer code from 0 through 3 |
| `reset_feed_calibration()` | `27` | `00`; starts the app's calibration procedure |
| `enter_boot()` | `3F` | Empty |

Hexadecimal opcodes are shown without the `0x` prefix. Inner frames default to
address `FFFF`. Numeric fields reject booleans, fractions, strings and overflow.
Binary fields require `bytes`; the frame payload cannot exceed 255 bytes.

## Example

```python
from pettec_xlink import FeederClient

client = FeederClient("192.168.1.80", access_key=123456789)
try:
    await client.connect()
    firmware = await client.get_firmware_version()
    print(firmware.hex())  # Keep raw data until your reply layout is verified.
finally:
    await client.close()
```

## Maintenance and upload limits

Reset, calibration, bootloader entry, and flash/firmware writes can change device
configuration, erase data or leave the feeder unable to operate. None was sent
to a real feeder during development. Camera commands come from the app's shared
command manager and may be unsupported on the camera-less PF 05WD.

The calibration UI recognizes codes 0–3, but this library retains the numeric
codes rather than assigning unverified physical states. Starting calibration is
different from the existing food-scale tare command `26` and requires the app's
physical calibration procedure. Neither is run automatically.

Flash geometry, address width, firmware compatibility, upload acknowledgements,
and upgrade completion criteria are unresolved. Upgrade metadata is opaque so
both APK layouts can be reproduced. The original builder writes one type byte,
five version bytes, ten date bytes, four size bytes, one checksum byte, and four
zeros. The secondary builder concatenates product, hardware, software, date and
size byte arrays, followed by a checksum byte. Callers must supply the correct
layout and bytes for their firmware. This is packet support, not a complete
firmware updater or a way to flash arbitrary replacement firmware.

Audio start/end metadata retains the ten raw date bytes used by the app; file
size is an unsigned big-endian 32-bit integer and checksum is a caller-supplied
byte. Chunk indexes are unsigned 16-bit wire values. Checksummed chunks use the
sum of their data bytes modulo 256. The two MCU chunk methods deliberately keep
the 64-byte legacy and 128-byte checksummed formats separate.

Each upload call sends a single inner frame. The app also groups multiple frames
into one Xlink message. Grouped uploads, audio conversion, firmware selection,
progress, retries and a complete upload sequence are not implemented. Do not
assume the device accepts single frames throughout an upload without testing.

An unexpected reply or timeout does not establish that a write failed. Inspect
the device before manually retrying a reset, calibration, erase, or upload.

## Evidence

Static source: PetTec Android 1.1.4, package `cn.xlink.pettec`, APK SHA-256
`4bc347b9213453c6cf73d4a751e0e29ee3a7e7b7916d11486febed3622f612f0`.
REA's Android provider remains unavailable because its JADX MCP JAR is not
configured. The existing Androguard installation supplied decompiled source and
Dalvik instructions; no runtime behavior was observed.

The opcode and payload table follows `cn.xlink.homerun.protocol.CmdManager`,
including its duplicate command `13` names and its original and secondary upload
builders. The camera mapping follows `CmdManager$2`, `Direct`, and
`controlCameraDirection`. `WriteAudioUsecase.generateBlockGroupData` and
`WriteMcuUsecase.generateBlockGroupData` establish the checksummed chunk layouts;
`UploadDataCmd.toBytes` establishes grouped-frame concatenation.
`WifiDeviceInfoActivity$7.onNext` establishes the four calibration reply codes.
Convenience aliases for methods with identical packets are documented rather
than duplicated in the Python API. Existing clock, schedule, feeding, remaining
food/battery, history, tray interval and tare commands remain unchanged.
