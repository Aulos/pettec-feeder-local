"""Protocol vectors and loopback-only simulations; no real device required."""

import asyncio
import struct
import unittest

from pettec_xlink import xlink

MAC = bytes.fromhex("AABBCCDDEEFF")
REQUEST = bytes.fromhex("57 AB FF FF 03 00 03")
RESPONSE = bytes.fromhex("57 AB FF FF 83 00 83")


def discovery(version=3, port=5987, product=xlink.PRODUCT_ID):
    return (
        bytes([version])
        + (struct.pack(">H", len(MAC)) if version == 3 else b"")
        + MAC
        + struct.pack(">H", len(product))
        + product.encode()
        + bytes.fromhex("01 00 02")
        + struct.pack(">HH", port, 1)
        + (b"\x00\x00" if version == 3 else b"\x00")
    )


class PacketTests(unittest.TestCase):
    def test_literal_envelope_vector(self):
        self.assertEqual(
            xlink.encode_packet(8, b"\x12\x34\x00", response=True),
            bytes.fromhex("8B00000003123400"),
        )
        self.assertEqual(
            xlink.decode_packet(bytes.fromhex("8B00000003123400")),
            (8, True, 3, b"\x12\x34\x00"),
        )

    def test_metadata_versions_and_product(self):
        for version in (2, 3):
            found = xlink.parse_discovery(discovery(version))
            self.assertEqual(
                (found.version, found.mac, found.port), (version, MAC, 5987)
            )
            self.assertIsNone(found.access_key)
        with self.assertRaises(xlink.UnsupportedDeviceError):
            xlink.parse_discovery(discovery(product="different-product"))
        with self.assertRaises(xlink.UnsupportedDeviceError):
            xlink.parse_discovery(b"\x01")

    def test_truncation_revision_and_frame_checksum(self):
        for packet in (
            b"",
            bytes.fromhex("830000000512"),
            bytes.fromhex("83000000011200"),
        ):
            with self.assertRaises(xlink.XlinkError):
                xlink.decode_packet(packet)
        with self.assertRaises(xlink.UnsupportedDeviceError):
            xlink.decode_packet(bytes.fromhex("810000000100"))
        with self.assertRaises(xlink.XlinkError):
            xlink.parse_discovery(discovery()[:-1])
        self.assertTrue(xlink._valid_frame(REQUEST))
        self.assertFalse(xlink._valid_frame(REQUEST[:-1] + b"\x00"))

    def test_filters_source_and_queue_bound(self):
        protocol = xlink._Datagrams("127.0.0.1")
        protocol.datagram_received(b"bad", ("127.0.0.2", 5987))
        self.assertTrue(protocol.queue.empty())
        for _ in range(100):
            protocol.datagram_received(b"ok", ("127.0.0.1", 5987))
        self.assertEqual(protocol.queue.qsize(), 64)

    def test_access_key_requires_an_integer(self):
        for value in (123456789.0, True, "123456789", None, 0, 1000000000):
            with self.assertRaises(ValueError):
                xlink.XlinkClient("127.0.0.1", value)

    def test_discovery_key_presence_is_not_initialized_state(self):
        for version in (2, 3):
            for key in (-1, 123456789, 0, -2):
                metadata = discovery(version)[:-1] + b"\x04" + struct.pack(">i", key)
                found = xlink.parse_discovery(metadata)
                self.assertEqual(found.access_key, key)
                self.assertEqual(found.initialized, key != -1)
            metadata = discovery(version)
            if version == 3:
                metadata = metadata[:-2] + b"\x01\x00"
            else:
                metadata = metadata[:-1] + b"\x10"
            self.assertTrue(xlink.parse_discovery(metadata).initialized)


class FakeFeeder(asyncio.DatagramProtocol):
    def __init__(
        self,
        *,
        version=3,
        fail_auth=False,
        drop=False,
        ack_only=False,
        paired=False,
        discovery_key=None,
    ):
        self.version = version
        self.fail_auth = fail_auth
        self.drop = drop
        self.ack_only = ack_only
        self.paired = paired
        self.discovery_key = discovery_key
        self.pipe_count = 0
        self.requests = []
        self.port = 0

    def connection_made(self, transport):
        self.transport = transport
        self.port = transport.get_extra_info("sockname")[1]

    def reply(self, kind, body, addr, response=True):
        self.transport.sendto(xlink.encode_packet(kind, body, response=response), addr)

    def datagram_received(self, packet, addr):
        kind, response, _, body = xlink.decode_packet(packet)
        self.requests.append((kind, body))
        if kind == 1:
            metadata = discovery(self.version, self.port)
            if self.paired:
                metadata = metadata[:-1] + b"\x04" + struct.pack(">I", 987654321)
            elif self.discovery_key is not None:
                metadata = (
                    metadata[:-1] + b"\x04" + struct.pack(">i", self.discovery_key)
                )
            self.reply(1, metadata, addr)
        elif kind == 2:
            if self.version == 3:
                mid = body[1:3]
                prefix = (
                    bytes([5 if self.fail_auth else 0, 3]) + mid + b"\x00\x06" + MAC
                )
                # Wrong request ID must be ignored.
                self.reply(
                    2,
                    prefix[:2]
                    + b"\xff\xff"
                    + prefix[4:]
                    + bytes.fromhex("000000010002123400"),
                    addr,
                )
            else:
                prefix = bytes([5 if self.fail_auth else 0, 2]) + MAC
            self.reply(2, prefix + bytes.fromhex("000000010002123400"), addr)
        elif kind == 8 and not response:
            self.pipe_count += 1
            if self.drop:
                return
            # Wrong ACK ID must not settle the send.
            self.reply(8, b"\xff\xff\x00", addr)
            self.reply(8, body[2:4] + b"\x00", addr)
            if not self.ack_only:
                # Push ID is independently allocated; it is not request ID.
                prefix = struct.pack(">H", len(MAC)) + MAC
                self.reply(8, prefix + b"\x56\x78\x00" + RESPONSE, addr, response=False)
        elif kind == 11 and not self.drop:
            self.reply(11, body[:2] + b"\x00", addr)


class ClientTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.original_port = xlink.DISCOVERY_PORT
        self.server = None
        self.client = None

    async def asyncTearDown(self):
        if self.client:
            await self.client.close()
        if self.server:
            self.server.close()
        xlink.DISCOVERY_PORT = self.original_port
        await asyncio.sleep(0)

    async def start(self, **kwargs):
        feeder = FakeFeeder(**kwargs)
        self.server, _ = await asyncio.get_running_loop().create_datagram_endpoint(
            lambda: feeder, local_addr=("127.0.0.1", 0)
        )
        xlink.DISCOVERY_PORT = feeder.port
        self.client = xlink.XlinkClient("127.0.0.1", 123456789, timeout=0.15)
        return feeder

    async def test_connect_send_and_little_endian_key_digest(self):
        feeder = await self.start()
        await self.client.connect()
        self.assertEqual(self.client.mac, "AABBCCDDEEFF")
        self.assertEqual(await self.client.send_pipe(REQUEST), RESPONSE)
        handshake = next(body for kind, body in feeder.requests if kind == 2)
        # Independently calculated MD5(15 cd 5b 07), key 123456789.
        self.assertEqual(handshake[3:19].hex(), "623ad5eda301ae082d466141a7f6dcaf")
        self.assertEqual(feeder.pipe_count, 1)

    async def test_connect_reuses_valid_session(self):
        feeder = await self.start()
        await self.client.connect()
        transport = self.client._transport
        session = self.client._session
        requests = len(feeder.requests)
        await self.client.connect()
        self.assertIs(self.client._transport, transport)
        self.assertEqual(self.client._session, session)
        self.assertEqual(len(feeder.requests), requests)
        self.assertEqual(sum(kind == 1 for kind, _ in feeder.requests), 1)
        self.assertEqual(sum(kind == 2 for kind, _ in feeder.requests), 1)
        self.assertEqual(await self.client.send_pipe(REQUEST), RESPONSE)

    async def test_version_two_device_with_v3_envelope(self):
        feeder = await self.start(version=2)
        await self.client.connect()
        self.assertEqual(self.client.version, 2)
        self.assertEqual(await self.client.send_pipe(REQUEST), RESPONSE)
        self.assertEqual(len(next(b for k, b in feeder.requests if k == 2)), 22)

    async def test_authentication_rejected_closes_session(self):
        await self.start(fail_auth=True)
        with self.assertRaises(xlink.AuthenticationError):
            await self.client.connect()
        self.assertIsNone(self.client._transport)

    async def test_timeout_does_not_retransmit(self):
        feeder = await self.start(drop=True)
        with self.assertRaises(TimeoutError):
            await self.client.send_pipe(REQUEST)
        self.assertEqual(feeder.pipe_count, 1)
        self.assertIsNone(self.client._session)

    async def test_ack_alone_is_not_command_success(self):
        feeder = await self.start(ack_only=True)
        with self.assertRaises(TimeoutError):
            await self.client.send_pipe(REQUEST)
        self.assertEqual(feeder.pipe_count, 1)

    async def test_key_assignment_is_explicit_and_big_endian(self):
        feeder = await self.start()
        result = await xlink.initialize_access_key("127.0.0.1", 123456789, timeout=0.15)
        self.assertEqual(result, "AABBCCDDEEFF")
        assignment = next(body for kind, body in feeder.requests if kind == 11)
        self.assertEqual(len(assignment), 9)
        self.assertEqual(assignment[2], 0)
        self.assertEqual(assignment[-4:].hex(), "075bcd15")
        self.assertFalse(any(kind == 2 for kind, _ in feeder.requests))

    async def test_existing_key_cannot_be_overwritten(self):
        feeder = await self.start(paired=True)
        with self.assertRaises(xlink.AuthenticationError):
            await xlink.initialize_access_key("127.0.0.1", 123456789, timeout=0.15)
        self.assertFalse(any(kind == 11 for kind, _ in feeder.requests))

    async def test_explicit_unpaired_key_sentinel_can_be_initialized(self):
        feeder = await self.start(discovery_key=-1)
        result = await xlink.initialize_access_key("127.0.0.1", 123456789, timeout=0.15)
        self.assertEqual(result, "AABBCCDDEEFF")
        self.assertEqual(sum(kind == 11 for kind, _ in feeder.requests), 1)

    async def test_unknown_key_state_cannot_be_overwritten(self):
        feeder = await self.start(discovery_key=0)
        with self.assertRaises(xlink.AuthenticationError):
            await xlink.initialize_access_key("127.0.0.1", 123456789, timeout=0.15)
        self.assertFalse(any(kind == 11 for kind, _ in feeder.requests))

    async def test_assignment_timeout_does_not_retry(self):
        feeder = await self.start(drop=True)
        with self.assertRaises(TimeoutError):
            await xlink.initialize_access_key("127.0.0.1", 123456789, timeout=0.05)
        self.assertEqual(sum(kind == 11 for kind, _ in feeder.requests), 1)


if __name__ == "__main__":
    unittest.main()
