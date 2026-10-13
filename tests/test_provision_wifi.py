"""Static SmartLink vectors and fake-network tests. No packets leave the host."""

import asyncio
import importlib.util
import io
import ipaddress
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

spec = importlib.util.spec_from_file_location(
    "provision_wifi", Path(__file__).parents[1] / "tools" / "provision_wifi.py"
)
wifi = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wifi)


class ProvisionTests(unittest.TestCase):
    def test_wpa_reference_pmk_and_pair_encoding(self):
        # Published WPA test vector: password=password, SSID=IEEE.
        expected_pmk = bytes.fromhex(
            "f42c6fc52df0ebef9ebb4b90b38a5f902e83fe1b135a70e23aed762e9710a12e"
        )
        data = wifi.credentials("IEEE", "password")
        self.assertEqual(data, b"\x04\x08\x20\x00IEEEpassword" + expected_pmk)
        packets = list(wifi.multicast_packets(data))
        self.assertEqual(packets[:5], [(("239.48.0.0", 6000), bytes(30), 0.01)] * 5)
        self.assertEqual(packets[5], (("239.46.4.8", 6000), bytes(30), 0.01))
        decoded = bytes(
            int(octet)
            for address, _, _ in packets[5:]
            for octet in address[0].split(".")[2:]
        )
        self.assertEqual(decoded, data)
        self.assertEqual(
            [len(payload) for _, payload, _ in packets[5:]], list(range(30, 54))
        )

    def test_open_network_padding_and_validation(self):
        self.assertEqual(wifi.credentials("A", ""), b"\x01\x00\x00\x00A\x00")
        for ssid, password in [
            ("", "password"),
            ("x" * 33, "password"),
            ("IEEE", "short"),
            ("IEEE", "pässword"),
        ]:
            with self.assertRaises(ValueError):
                wifi.credentials(ssid, password)

    def test_v3_password_lengths_and_timing(self):
        packets = list(wifi.broadcast_packets("password", "192.168.1.255"))
        self.assertEqual(len(packets), 200 + 5 * (3 + 8 + 3 + 3))
        self.assertEqual(
            packets[:200], [(("192.168.1.255", 49999), b"\x05" * 76, 0.01)] * 200
        )
        self.assertEqual(
            [len(p[1]) for p in packets[200:217]],
            [89] * 3 + [ord(c) + 76 for c in "password"] + [86] * 3 + [340] * 3,
        )
        self.assertEqual([p[2] for p in packets[200:203]], [0.05, 0.05, 0.1])
        self.assertEqual([p[2] for p in packets[211:217]], [0, 0, 0.1, 0.05, 0.05, 0.5])

    def test_acknowledgement(self):
        self.assertEqual(
            wifi.acknowledgement(b"smart_config aa:bb:cc:dd:ee:ff\x00"),
            "aa:bb:cc:dd:ee:ff",
        )
        for packet in [
            b"\x05" * 80,
            b"random",
            b"smart_config",
            b"smart_config not-a-mac",
            b"smart_config \xff",
        ]:
            self.assertIsNone(wifi.acknowledgement(packet))

    def test_network_lifecycle_with_fake_sockets(self):
        receiver, multicast = MagicMock(), MagicMock()

        async def scenario():
            loop = asyncio.get_running_loop()
            with (
                patch.object(wifi.socket, "socket", side_effect=[receiver, multicast]),
                patch.object(
                    loop,
                    "sock_recvfrom",
                    new=AsyncMock(
                        return_value=(
                            b"smart_configaabbccddeeff",
                            ("192.168.1.77", 49999),
                        )
                    ),
                ),
                patch.object(loop, "sock_sendto", new=AsyncMock()),
            ):
                return await wifi.provision(
                    "IEEE", "password", ipaddress.IPv4Interface("192.168.1.50/24"), 1
                )

        self.assertTrue(asyncio.run(scenario()))
        receiver.bind.assert_called_once_with(("", 49999))
        receiver.close.assert_called_once()
        multicast.close.assert_called_once()

    def test_explicit_key_assignment_and_timeout_preserve_key(self):
        for error, expected_exit in [(None, 0), (TimeoutError(), 1)]:
            transport = SimpleNamespace(
                initialize_access_key=AsyncMock(
                    return_value="aabbccddeeff", side_effect=error
                ),
                XlinkError=ValueError,
            )
            spec = SimpleNamespace(name="fake_bootstrap_xlink", loader=MagicMock())
            output = io.StringIO()
            with (
                patch.object(
                    wifi.sys,
                    "argv",
                    [
                        "provision_wifi.py",
                        "--ssid",
                        "IEEE",
                        "--network",
                        "192.168.1.50/24",
                        "--initialize-key",
                    ],
                ),
                patch.object(wifi.getpass, "getpass", return_value="password"),
                patch.object(
                    wifi,
                    "provision",
                    new=AsyncMock(return_value=("192.168.1.77", "aabbccddeeff")),
                ),
                patch.object(wifi.secrets, "randbelow", return_value=123456788),
                patch.object(
                    wifi.importlib.util, "spec_from_file_location", return_value=spec
                ),
                patch.object(
                    wifi.importlib.util, "module_from_spec", return_value=transport
                ),
                patch.object(wifi.sys, "stdout", output),
            ):
                self.assertEqual(wifi.main(), expected_exit)
            wifi.sys.modules.pop(spec.name, None)
            transport.initialize_access_key.assert_awaited_once_with(
                "192.168.1.77", 123456789
            )
            self.assertIn("Requested new access key: 123456789", output.getvalue())
            self.assertNotIn("password", output.getvalue())


if __name__ == "__main__":
    unittest.main()
