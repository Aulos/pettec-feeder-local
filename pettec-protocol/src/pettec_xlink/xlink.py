"""Local Xlink UDP subset recovered from PetTec Android 1.1.4.

Source: SDK c.f (encoder), b.c (decoder), util.b and e.a in the APK.
Only device revisions 2 and 3 are supported; no cloud or Wi-Fi provisioning.
"""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import secrets
import socket
import struct
import time
from dataclasses import dataclass

PRODUCT_ID = "160fa6b0c66c03e9160fa6b0c66c4a01"
DISCOVERY_PORT = 5987
MAX_PACKET = 1024  # SDK h.a allocates a 1024-byte receive buffer.
SESSION_TIMEOUT = 100  # XDevice default, seconds.


class XlinkError(Exception):
    """Protocol or device error."""


class AuthenticationError(XlinkError):
    """Device refused authentication or invalidated the session."""


class UnsupportedDeviceError(XlinkError):
    """Product or protocol revision is outside the recovered subset."""


def encode_packet(kind: int, body: bytes, *, response: bool = False) -> bytes:
    """SDK c.d.a: type:4, response:1, revision:3, big-endian length."""
    if not 1 <= kind <= 15 or len(body) > MAX_PACKET - 5:
        raise ValueError("Invalid packet type or size")
    return struct.pack(">BI", kind << 4 | int(response) << 3 | 3, len(body)) + body


def decode_packet(packet: bytes) -> tuple[int, bool, int, bytes]:
    if not 5 < len(packet) <= MAX_PACKET:
        raise XlinkError("Invalid packet size")
    header, length = struct.unpack_from(">BI", packet)
    if length != len(packet) - 5 or header >> 4 == 0:
        raise XlinkError("Invalid packet header")
    revision = header & 7
    if revision not in (2, 3):
        raise UnsupportedDeviceError("Unsupported envelope revision")
    return header >> 4, bool(header & 8), revision, packet[5:]


class _Reader:
    def __init__(self, body: bytes):
        self.body = body
        self.offset = 0

    def take(self, size: int) -> bytes:
        if size < 0 or self.offset + size > len(self.body):
            raise XlinkError("Truncated packet")
        value = self.body[self.offset : self.offset + size]
        self.offset += size
        return value

    def number(self, fmt: str) -> int:
        return struct.unpack(fmt, self.take(struct.calcsize(fmt)))[0]

    def sized(self, maximum: int) -> bytes:
        size = self.number(">H")
        if size > maximum:
            raise XlinkError("Invalid field length")
        return self.take(size)


@dataclass(frozen=True)
class DiscoveredDevice:
    version: int
    mac: bytes
    product_id: str
    port: int
    access_key: int | None
    initialized: bool


def parse_discovery(body: bytes) -> DiscoveredDevice:
    """SDK b.c.x: discovery metadata and optional access key."""
    reader = _Reader(body)
    version = reader.number("B")
    if version not in (2, 3):
        raise UnsupportedDeviceError("Only Xlink device revisions 2 and 3 supported")
    mac = reader.sized(6) if version == 3 else reader.take(6)
    if len(mac) != 6:
        raise UnsupportedDeviceError("Unsupported device MAC size")
    try:
        product_id = reader.sized(64).decode("utf-8")
    except UnicodeDecodeError as err:
        raise XlinkError("Invalid product identity") from err
    if product_id != PRODUCT_ID:
        raise UnsupportedDeviceError("Device is not the PF05WD product")
    reader.take(3)  # MCU hardware:u8 and software:u16.
    port = reader.number(">H")
    reader.take(2)  # Product type.
    scan_flags = reader.number("B")
    data_flags = reader.number("B") if version == 3 else scan_flags
    if data_flags & 1:
        reader.sized(256)  # Optional device name.
    is_mac_scan = bool(scan_flags & (1 if version == 3 else 16))
    access_key = reader.number(">i") if not is_mac_scan and data_flags & 4 else None
    if not port:
        raise XlinkError("Invalid device port")
    # Data flag 4 means key field present. The app assigns a new key only
    # for -1; the SDK maps an absent key field to the same sentinel.
    initialized = is_mac_scan or access_key not in (None, -1)
    return DiscoveredDevice(version, mac, product_id, port, access_key, initialized)


class _Datagrams(asyncio.DatagramProtocol):
    def __init__(self, host: str):
        self.host = host
        self.queue: asyncio.Queue[tuple[bytes, int] | Exception] = asyncio.Queue(64)

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        if addr[0] == self.host and len(data) <= MAX_PACKET:
            try:
                self.queue.put_nowait((data, addr[1]))
            except asyncio.QueueFull:
                pass

    def error_received(self, exc: Exception) -> None:
        if not self.queue.full():
            self.queue.put_nowait(exc)


class XlinkClient:
    """One authenticated LAN session; each physical command is sent once.

    Only numeric IPv4 hosts are accepted. The caller supplies a known access
    key; key assignment/reset and provisioning are deliberately absent.
    """

    def __init__(self, host: str, access_key: int, *, timeout: float = 5.0):
        self.host = str(ipaddress.IPv4Address(host))
        if type(access_key) is not int or not 1 <= access_key <= 999999999:
            raise ValueError("Access key must be between 1 and 999999999")
        if timeout <= 0:
            raise ValueError("Timeout must be positive")
        self.access_key = access_key
        self.timeout = timeout
        self.mac: str | None = None
        self.version: int | None = None
        self.port = DISCOVERY_PORT
        self._mac = b""
        self._session: int | None = None
        self._connected_at = 0.0
        self._transport: asyncio.DatagramTransport | None = None
        self._protocol: _Datagrams | None = None
        self._lock = asyncio.Lock()
        self._message_id = secrets.randbelow(30000) + 1

    def _next_id(self) -> int:
        self._message_id = self._message_id % 32000 + 1
        return self._message_id

    def _send(self, kind: int, body: bytes, *, response: bool = False) -> None:
        if self._transport is None:
            raise XlinkError("Connection is closed")
        self._transport.sendto(
            encode_packet(kind, body, response=response), (self.host, self.port)
        )

    async def _receive(self) -> tuple[int, bool, int, bytes]:
        assert self._protocol is not None
        while True:
            result = await self._protocol.queue.get()
            if isinstance(result, Exception):
                raise result
            packet, port = result
            if port != self.port:
                continue
            try:
                return decode_packet(packet)
            except XlinkError:
                continue

    async def connect(self) -> None:
        async with self._lock:
            if self._session is not None and time.monotonic() - self._connected_at < 90:
                return
            await self._connect()

    async def _connect(self) -> None:
        try:
            async with asyncio.timeout(self.timeout):
                local_port = await self._discover()
                message_id = self._next_id()
                # SDK util.b.a(int): MD5 of four LITTLE-endian bytes.
                digest = hashlib.md5(struct.pack("<I", self.access_key)).digest()
                body = bytes([self.version])
                if self.version == 3:
                    body += struct.pack(">H", message_id)
                body += digest + struct.pack(">HBH", local_port, 0, SESSION_TIMEOUT)
                self._send(2, body)
                while True:
                    kind, _, _, body = await self._receive()
                    if kind != 2:
                        continue
                    reader = _Reader(body)
                    status = reader.number("B")
                    version = reader.number("B")
                    if version != self.version:
                        continue
                    if version == 3 and reader.number(">H") != message_id:
                        continue
                    mac = reader.sized(6) if version == 3 else reader.take(6)
                    if mac != self._mac:
                        continue
                    if status:
                        raise AuthenticationError(f"Handshake rejected ({status})")
                    reader.take(6)  # Device ID:u32 and MCU software:u16.
                    self._session = reader.number(">H")
                    if not self._session:
                        raise AuthenticationError("Device returned an invalid session")
                    reader.take(1)  # Flags.
                    self._connected_at = time.monotonic()
                    return
        except BaseException:
            await self.close()
            raise

    async def _discover(self) -> int:
        await self.close()
        self.port = DISCOVERY_PORT
        loop = asyncio.get_running_loop()
        self._transport, self._protocol = await loop.create_datagram_endpoint(
            lambda: _Datagrams(self.host),
            local_addr=("0.0.0.0", 0),
            family=socket.AF_INET,
        )
        local_port = self._transport.get_extra_info("sockname")[1]
        # SDK c.f.a(int,String), mode=2 means product ID scan.
        self._send(1, struct.pack(">BHB", 3, local_port, 2) + PRODUCT_ID.encode())
        while True:
            kind, _, _, body = await self._receive()
            if kind == 1:
                discovered = parse_discovery(body)
                break
        self.version = discovered.version
        self._mac = discovered.mac
        self.mac = discovered.mac.hex().upper()
        self.port = discovered.port
        self._initialized = discovered.initialized
        return local_port

    async def send_pipe(self, payload: bytes) -> bytes:
        """Wait for the transport ACK and the inner command response.

        SDK b.c.q and the app dispatcher correlate pushes by MAC and inner
        opcode, not by the independently allocated push message ID.
        """
        if not _valid_frame(payload) or payload[4] & 128:
            raise ValueError("Expected a valid feeder request frame")
        async with self._lock:
            if self._session is None or time.monotonic() - self._connected_at >= 90:
                await self._connect()
            assert self._session is not None
            assert self._protocol is not None
            # Discard pushes received before this command starts.
            while not self._protocol.queue.empty():
                self._protocol.queue.get_nowait()
            message_id = self._next_id()
            accepted = False
            result: bytes | None = None
            try:
                async with asyncio.timeout(self.timeout):
                    # SDK c.f.a(XDevice,byte,byte[]): pipe type is zero.
                    self._send(
                        8, struct.pack(">HHB", self._session, message_id, 0) + payload
                    )
                    while not accepted or result is None:
                        kind, response, revision, body = await self._receive()
                        if kind != 8:
                            continue
                        reader = _Reader(body)
                        try:
                            if response:
                                if reader.number(">H") != message_id:
                                    continue
                                status = reader.number("B")
                                if status == 1:
                                    raise AuthenticationError("Device session expired")
                                if status:
                                    raise XlinkError(f"Command rejected ({status})")
                                accepted = True
                            else:
                                mac = (
                                    reader.sized(6) if revision == 3 else reader.take(6)
                                )
                                if mac != self._mac:
                                    continue
                                push_id = reader.number(">H")
                                pipe_type = reader.number("B")
                                frame = body[reader.offset :]
                                self._send(
                                    8, struct.pack(">HB", push_id, 0), response=True
                                )
                                if (
                                    pipe_type == 0
                                    and _valid_frame(frame)
                                    and frame[4] == payload[4] | 128
                                ):
                                    result = frame
                        except (struct.error, IndexError):
                            continue
                    return result
            except BaseException:
                # A timed-out command may have executed. Never resend it.
                await self.close()
                raise

    async def close(self) -> None:
        if self._transport is not None:
            self._transport.close()
        self._transport = None
        self._protocol = None
        self._session = None


def _valid_frame(frame: bytes) -> bool:
    return (
        len(frame) >= 7
        and frame[:2] == b"\x57\xab"
        and len(frame) == frame[5] + 7
        and sum(frame[:-1]) & 255 == frame[-1]
    )


async def initialize_access_key(
    host: str, access_key: int, *, timeout: float = 5.0
) -> str:
    """Explicitly assign a key only to a device advertising unpaired state.

    SDK c.f.b(XDevice,int): type11, ID:u16, flags:u8, localport:u16,
    key:big-endian u32. No authentication, overwrite, reset or retries.
    A timeout may mean the key was assigned; retain the requested key.
    """
    client = XlinkClient(host, access_key, timeout=timeout)
    try:
        async with asyncio.timeout(timeout):
            local_port = await client._discover()
            if client._initialized:
                raise AuthenticationError("Refusing to replace an existing device key")
            message_id = client._next_id()
            client._send(
                11, struct.pack(">HBHI", message_id, 0, local_port, access_key)
            )
            while True:
                kind, _, _, body = await client._receive()
                if kind != 11:
                    continue
                reader = _Reader(body)
                if reader.number(">H") != message_id:
                    continue
                status = reader.number("B")
                if status:
                    raise AuthenticationError(f"Key assignment refused ({status})")
                assert client.mac is not None
                return client.mac
    finally:
        await client.close()
