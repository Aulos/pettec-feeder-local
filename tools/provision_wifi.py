#!/usr/bin/env python3
"""Offline High-Flying SmartLink v7/v3 provisioning recovered from PetTec APK.

Requires Python 3.11+. Run on a computer connected through Wi-Fi to the
intended 2.4 GHz network.
This changes Wi-Fi credentials only when run explicitly. It does not reset the
feeder or send feeding commands. --initialize-key separately authorizes assigning
a new Xlink key only if the feeder advertises that it is unpaired.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import hashlib
import importlib.util
import ipaddress
import secrets
import socket
import sys
from pathlib import Path


def credentials(ssid: str, password: str) -> bytes:
    """MulticastSmartLinkerSendAction.generateData2Send/encodedPMK."""
    ssid_bytes = ssid.encode("utf-8")
    if not 1 <= len(ssid_bytes) <= 32:
        raise ValueError("SSID must contain 1 to 32 UTF-8 bytes")
    try:
        password_bytes = password.encode("ascii")
    except UnicodeEncodeError as err:
        raise ValueError("This helper supports ASCII Wi-Fi passwords only") from err
    if password and not 8 <= len(password_bytes) <= 63:
        raise ValueError("Wi-Fi passphrase must contain 8 to 63 ASCII characters")
    pmk = (
        hashlib.pbkdf2_hmac("sha1", password_bytes, ssid_bytes, 4096, 32)
        if password
        else b""
    )
    result = (
        bytes((len(ssid_bytes), len(password_bytes), len(pmk), 0))
        + ssid_bytes
        + password_bytes
        + pmk
    )
    return result + bytes(len(result) % 2)


def multicast_packets(data: bytes):
    """One SmartLink v7 cycle: destination, bytes, post-send delay."""
    for _ in range(5):
        yield ("239.48.0.0", 6000), bytes(30), 0.01
    for index in range(len(data) // 2):
        first, second = data[index * 2 : index * 2 + 2]
        yield (f"239.46.{first}.{second}", 6000), bytes(30 + index), 0.01


def broadcast_packets(password: str, broadcast: str):
    """One SnifferSmartLinkerSendAction.sendContents cycle."""
    address = (broadcast, 49999)
    for _ in range(200):
        yield address, bytes([5]) * 76, 0.01
    values = [89] + [ord(char) + 76 for char in password] + [86]
    for _ in range(5):
        for index, size in enumerate(values):
            last = index == len(values) - 1
            repeat = 3 if index in (0, len(values) - 1) else 1
            for count in range(repeat):
                delay = 0.0 if last else 0.05
                if count == repeat - 1:
                    delay += 0.1 if last else 0.05
                yield address, bytes([5]) * size, delay
        for count in range(3):
            yield (
                address,
                bytes([5]) * (len(password) + 332),
                0.05 if count < 2 else 0.5,
            )


def acknowledgement(data: bytes) -> str | None:
    """Validate the MAC identifier in AbstractSmartLinker$4's acknowledgement."""
    if not data.startswith(b"smart_config"):
        return None
    try:
        identifier = data[len(b"smart_config") :].decode("ascii").strip(" \t\r\n\x00")
    except UnicodeDecodeError:
        return None
    compact = identifier.replace(":", "").replace("-", "")
    if len(compact) != 12 or any(
        char not in "0123456789abcdefABCDEF" for char in compact
    ):
        return None
    return identifier


async def provision(
    ssid: str, password: str, interface: ipaddress.IPv4Interface, duration: int
) -> tuple[str, str] | None:
    data = credentials(ssid, password)
    loop = asyncio.get_running_loop()
    receiver = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    multicast = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    tasks = []
    try:
        receiver.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        receiver.bind(("", 49999))
        receiver.setblocking(False)
        multicast.setsockopt(
            socket.IPPROTO_IP, socket.IP_MULTICAST_IF, interface.ip.packed
        )
        multicast.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)
        multicast.setblocking(False)

        async def send_packets(sock, packet_cycle, cycle_delay=0.0):
            while True:
                for address, payload, delay in packet_cycle():
                    await loop.sock_sendto(sock, payload, address)
                    await asyncio.sleep(delay)
                await asyncio.sleep(cycle_delay)

        tasks = [
            asyncio.create_task(
                send_packets(multicast, lambda: multicast_packets(data), 0.1)
            ),
            asyncio.create_task(
                send_packets(
                    receiver,
                    lambda: broadcast_packets(
                        password, str(interface.network.broadcast_address)
                    ),
                )
            ),
        ]
        deadline = loop.time() + duration
        while loop.time() < deadline:
            for task in tasks:
                if task.done():
                    task.result()
            try:
                packet, address = await asyncio.wait_for(
                    loop.sock_recvfrom(receiver, 1024), min(1, deadline - loop.time())
                )
            except asyncio.TimeoutError:
                continue
            identifier = acknowledgement(packet)
            if identifier:
                print(
                    f"Wi-Fi acknowledgement from {address[0]}, device {identifier}. "
                    "Next: Xlink discovery and status read."
                )
                return address[0], identifier
        print(
            "No acknowledgement received. Check pairing mode, the Wi-Fi adapter, "
            "and 2.4 GHz. No Xlink key was assigned."
        )
        return None
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        receiver.close()
        multicast.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--ssid", required=True, help="Intended 2.4 GHz Wi-Fi network name"
    )
    parser.add_argument(
        "--network",
        required=True,
        type=ipaddress.IPv4Interface,
        help="Computer's Wi-Fi IPv4 address/CIDR, e.g. 192.168.1.50/24",
    )
    parser.add_argument(
        "--duration",
        type=int,
        default=60,
        help="Seconds to transmit, 1 to 180; default 60",
    )
    parser.add_argument(
        "--initialize-key",
        action="store_true",
        help="After Wi-Fi acknowledgement, assign a new key only to an unpaired feeder",
    )
    args = parser.parse_args()
    if not 1 <= args.duration <= 180:
        parser.error("duration must be between 1 and 180 seconds")
    password = getpass.getpass("Wi-Fi password, empty for an open network: ")
    try:
        credentials(args.ssid, password)
    except ValueError as err:
        parser.error(str(err))
    print(
        "Sending Wi-Fi credentials using the APK's SmartLink v7 and v3 formats. "
        "The feeder must already be in Wi-Fi setup mode."
    )

    async def run():
        result = await provision(args.ssid, password, args.network, args.duration)
        if result is None:
            return 1
        if args.initialize_key:
            host, _ = result
            spec = importlib.util.spec_from_file_location(
                "pettec_bootstrap_xlink",
                Path(__file__).parents[1]
                / "pettec-protocol"
                / "src"
                / "pettec_xlink"
                / "xlink.py",
            )
            xlink = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = xlink
            spec.loader.exec_module(xlink)
            key = secrets.randbelow(999_999_999) + 1
            print(
                f"Requested new access key: {key}. Save it now. "
                f"A timeout may mean it was assigned. Feeder IP: {host}."
            )
            try:
                mac = await xlink.initialize_access_key(host, key)
            except (xlink.XlinkError, TimeoutError) as err:
                print(
                    f"Key assignment not confirmed: {err}. "
                    "Existing keys are never replaced; no reset was attempted."
                )
                return 1
            print(
                f"Key assignment acknowledged by {mac}. "
                "Use the IP and saved key in Home Assistant."
            )
        return 0

    try:
        return asyncio.run(run())
    except OSError as err:
        parser.exit(2, f"Network error: {err}\n")
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
