# Xlink transport evidence

Static artifact: `cn.xlink.pettec` Android 1.1.4 APK SHA-256
`4bc347b9213453c6cf73d4a751e0e29ee3a7e7b7916d11486febed3622f612f0`.
Androguard 4.1.4 decompiled the packaged SDK and its bytecode was inspected.
REA's Android provider was unavailable because JADX was not installed.
No physical feeder was discovered, configured, or controlled.

Offsets below are **code_item offsets within classes2.dex**, not APK offsets.
The original opcode disassembly is retained in the investigation workspace as
`pettec-protocol/xlink-sdk-bytecode.txt`.

| Behavior | Source in io.xlink.wifi.sdk | DEX offset |
|---|---|---|
| Header type/response/revision bit packing | `util.b.a(int,int)` | `0x4ffac` |
| UDP destination 5987 | `e.a.<clinit>()` | `0x4d8d4` |
| Product discovery request | `c.f.a(int,String)` | `0x4c308` |
| Discovery response parser | `b.c.x(b.a)` | `0x48e28` |
| Integer-key handshake builder | `c.f.a(XDevice,int)` | `0x4c4d0` |
| MD5 key derivation | `util.b.a(int)` | `0x501b8` |
| Four-byte little-endian key input | `util.b.c(int)` | `0x50520` |
| Handshake response parser | `b.c.w(b.a)` | `0x48bac` |
| LAN pipe request builder | `c.f.a(XDevice,byte,byte[])` | `0x4c400` |
| LAN pipe ACK parser | `b.c.r(b.a)` | `0x487c8` |
| Incoming pipe push parser | `b.c.q(b.a)` | `0x486d8` |
| ACK outgoing device push | `c.f.a(int,int,InetAddress)` | `0x4c2c8` |
| Initial access-key assignment builder | `c.f.b(XDevice,int)` | `0x4cbb0` |
| Initial access-key assignment response | `b.c.n(b.a)` | `0x4839c` |

## Recovered wire format

Envelope: one byte `(type << 4) | (response << 3) | revision`, then
big-endian 32-bit payload length. The packaged SDK always writes envelope
revision 3. Device revisions 2 and 3 have different body layouts. Revisions
outside 2 and 3 are rejected by this implementation.

Product scan (type 1) body: `03`, local UDP port:u16, flags `02`, UTF-8 product
ID. Device replies contain the device revision, MAC (revision 3 prefixes u16
length), u16-length UTF-8 product ID, MCU hardware:u8/software:u16,
device UDP port:u16, product type:u16, flags. Revision 3 separates scan and
data flags. Optional device name and access key are controlled by data flags.
The selected product ID comes from the user's feeder label QR code.

Handshake (type 2): device revision:u8, message ID:u16 **only for device
revision 3**, MD5(little-endian u32 access key):16, local UDP port:u16,
flags:u8=`00`, session lifetime:u16=`100`. Revision 2 has no message ID.
Replies contain status:u8, device revision:u8, revision-3 message ID,
MAC, device ID:u32, MCU software:u16, session ID:u16, flags:u8.
Revision-3 MAC is length-prefixed; revision-2 MAC is six bytes.

Pipe request (type 8): session ID:u16, request ID:u16, pipe type:u8=`00`,
inner feeder frame. Pipe ACK (type 8 with response flag): request ID:u16,
status:u8. **ACK does not contain a feeder command response.**
The application response arrives as an unsolicited type-8 push containing
MAC, independent push ID:u16, pipe type:u8, inner feeder frame. Envelope
revision 3 prefixes the MAC with a u16 length. The host acknowledges the push
ID with status `00`.

The SDK correlates ACKs by request ID and the app dispatches inner responses
by opcode. Push IDs are acknowledged independently; their equality to a
request ID is not established. The implementation serializes commands,
checks source IP/port and MAC, and checks the inner prefix, length, checksum,
and response opcode. Failed/uncertain commands close the socket and session;
they are never automatically retransmitted.

Initial key assignment (type 11) is a separate, explicit commissioning action:
message ID:u16, flags:u8=`00`, local UDP port:u16, requested key:u32 big-endian.
Its reply contains message ID:u16 and status:u8. This implementation sends it
only after product discovery reports an uninitialized device. A device with
an existing or unknown key is refused. Only an absent key or the explicit -1
sentinel reported by the stock SDK is accepted; MAC-scan protected states
are always refused.
The function does not reset or overwrite an existing key. Retain the requested
key after a timeout, since assignment might have occurred.

## Implementation limits

Tests use literal protocol vectors and a localhost UDP simulator, including
authentication refusal, wrong ACK/handshake IDs, existing-key refusal and
timeouts without retransmission. They establish implementation consistency,
not physical-device compatibility.

The original SDK broadcasts discovery. The integration uses the user-supplied
numeric IPv4 address for unicast discovery; acceptance of unicast scans by the
physical PF05WD remains unverified. The SDK's cloud fallback, subscribe-key
retrieval, remote account association, and automatic reconfiguration are omitted.
Wi-Fi commissioning is separate from this transport.

Local packets are not encrypted by the APK's inspected builders. Initial key
assignment sends the key as a plain integer. Source filtering and correlation
prevent accidental mismatches but are not cryptographic peer authentication.
The feeder firmware may have additional requirements absent from the app.

## Stored schedules

Schedule observations come from `classes.dex` of the same APK, not
`classes2.dex`. Decompiled command constructors lost some register names;
the original Dalvik instructions were checked directly for both opcodes.

| Behavior | Class and method | DEX code_item offset |
|---|---|---|
| Read schedules, inner command `05`, empty payload | `cn.xlink.homerun.protocol.CmdManager.getTimerList` | `0x2d9a00` |
| Read raw schedule bytes, inner command `05` | `CmdManager.getTimerListRawByte` | `0x2d9a50` |
| Write schedules, inner command `06` | `CmdManager.setTimers` | `0x2da06c` |
| Require a one-byte reply with status zero for a successful write | `cn.xlink.homerun.mvp.usecase.AbstUpdateTimerUsecase$1.onNext(byte[])` | `0x2cb648` |

`cn.xlink.homerun.model.Timer` and `TimerFactory.timerListToBytes` encode
six bytes per slot: enabled (`00` or `01`), hour, minute, big-endian u16
quantity, then weekday bits. `Timer.sWeekDaysMap` maps bits 0 through 6 to
Monday through Sunday. A stock-app default uses weekday byte `FF`; the
implementation preserves the high bit instead of interpreting it as a day.
`cn.xlink.homerun.protocol.app.TimerResponseParser` reads five six-byte slots.
`AbstUpdateTimerUsecase.updateTimersLists` pads the uploaded list to five
slots using disabled zero-filled timers.

The integration requires exactly 30 bytes when reading schedules. It preserves
all untouched records, including quantities outside the editor's 1–200 gram
range and the high weekday bit. A fresh read detects changes to the selected
slot before writing. Writes are serialized within one client, acknowledged
once, and verified by comparing the entire returned list. An already applied
edit is read and accepted without being retransmitted. There is no protocol
compare-and-swap, so an external writer can still race the read/write sequence.

The owner reports Wi-Fi provisioning and manual feeding work on their PF05WD.
Schedule acceptance, persistence across power loss, weekday execution, and
operation with Home Assistant offline remain unverified. The schedule editor
uses the device's existing clock and does not change it or handle daylight
saving time automatically.

## Clock synchronization

`CmdManager.setRTCObject`, at `classes.dex` code_item `0x2da01c`, constructs
inner command `04`. `RTCObject.toBytes`, at `0x2dbae8`, writes seven bytes:
year minus 2000, month, day, hour, minute, second and weekday.
`RTCObject(long)` uses the local Java Calendar and maps Calendar's Sunday=1
through Saturday=7 to wire values Sunday=0 through Saturday=6. This convention
is different from schedule weekday bit positions.

`SyncRTCTimerUsecase$1.onNext(byte[])`, at `0x2d4414`, forwards raw reply bytes
to its callback. The implementation conservatively requires the same exact
one-byte zero status used for other successful writes, then independently
reads command `03` and compares the returned clock. Readback allows elapsed
transport time plus two seconds; it does not treat an acknowledgement alone
as verified clock synchronization. The clock-setting command has not been
exercised on hardware.

Home Assistant supplies its configured local wall time through `dt_util.now()`.
The device has no time-zone field. Startup synchronization is opt-in, runs once
on `EVENT_HOMEASSISTANT_STARTED`, and its listener is removed on unload. Reloading
an integration while Home Assistant is running does not write the clock. A missed
startup or failed sync requires an explicit manual button press. No periodic,
reconnect or daylight-saving clock writes are added.
