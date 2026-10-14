"""Literal APK command layouts; no physical device is contacted."""

import pytest
from test_feeder import client_with_transport

from pettec_xlink import XlinkError
from pettec_xlink.protocol import decode_frame, encode_frame

CASES = [
    ("check_connection", (7,), 0x01, b"\x07"),
    ("get_device_info", (), 0x02, b""),
    ("erase_flash_block", (b"\x00\x12\x34\x56",), 0x07, b"\x00\x12\x34\x56"),
    ("write_flash_block", (b"\x12\x34", b"\xaa\xbb"), 0x08, b"\x12\x34\x02\xaa\xbb"),
    ("read_flash_block", (b"\x12\x34", 64), 0x09, b"\x12\x34\x40"),
    ("get_module_address", (), 0x0A, b""),
    ("reset_device", (), 0x0E, b""),
    ("get_feed_record", (), 0x0F, b""),
    ("get_latest_feed_data_offset", (), 0x13, b""),
    ("control_cradle_head", (4,), 0x13, b"\x04"),
    ("get_alarm_packet", (), 0x14, b""),
    ("control_camera_direction", ("left",), 0x15, b"\x01"),
    ("control_camera_direction", ("right",), 0x15, b"\x02"),
    ("control_camera_direction", ("up",), 0x15, b"\x03"),
    ("control_camera_direction", ("down",), 0x15, b"\x04"),
    ("begin_upgrade", (b"\x55" * 25,), 0x18, b"\x55" * 25),
    ("write_mcu_chunk", (0x1234, b"\xfe\x05"), 0x19, b"\x12\x34\xfe\x05"),
    (
        "write_mcu_chunk_checksummed",
        (0x1234, b"\xfe\x05"),
        0x19,
        b"\x12\x34\x03\xfe\x05",
    ),
    ("check_mcu_file", (b"\x04\x03\x02\x01",), 0x1A, b"\x04\x03\x02\x01"),
    ("complete_upgrade", (b"\x55" * 25,), 0x1B, b"\x55" * 25),
    ("set_upgrade_enabled", (True,), 0x1C, b"\x01\x01"),
    ("set_upgrade_enabled", (False,), 0x1C, b"\x01\x00"),
    ("get_firmware_version", (), 0x1D, b"\x01"),
    ("get_feeder_info", (), 0x1F, b""),
    (
        "begin_audio_upload",
        (b"\x55" * 10, 0x12345678, 0x99),
        0x20,
        bytes.fromhex("00 00 00 00 00 55 55 55 55 55 55 55 55 55 55 12 34 56 78 99"),
    ),
    ("write_audio_chunk", (0x1234, b"\xfe\x05"), 0x21, b"\x12\x34\x03\xfe\x05"),
    (
        "complete_audio_upload",
        (b"\x55" * 10, 0x12345678, 0x99),
        0x22,
        bytes.fromhex("00 00 00 00 00 55 55 55 55 55 55 55 55 55 55 12 34 56 78 99"),
    ),
    ("reset_feed_calibration", (), 0x27, b"\x00"),
    ("enter_boot", (), 0x3F, b""),
]


@pytest.mark.parametrize("method,args,command,payload", CASES)
async def test_apk_layout_and_opaque_reply(monkeypatch, method, args, command, payload):
    reply = b"\x00\x02\xff"
    client, transport = client_with_transport(
        monkeypatch, encode_frame(command | 128, reply)
    )
    assert await getattr(client, method)(*args) == reply
    transport.send_pipe.assert_awaited_once()
    frame = decode_frame(transport.send_pipe.await_args.args[0])
    assert (frame.address, frame.command, frame.payload) == (65535, command, payload)


@pytest.mark.parametrize("method,args,command,payload", CASES)
async def test_maintenance_timeout_never_retries(
    monkeypatch, method, args, command, payload
):
    client, transport = client_with_transport(monkeypatch, error=TimeoutError())
    with pytest.raises(TimeoutError):
        await getattr(client, method)(*args)
    assert transport.send_pipe.await_count == 1


@pytest.mark.parametrize("method,args,command,payload", CASES)
async def test_maintenance_requires_matching_valid_frame(
    monkeypatch, method, args, command, payload
):
    client, transport = client_with_transport(monkeypatch, encode_frame(0xFF, b"\x00"))
    with pytest.raises(XlinkError):
        await getattr(client, method)(*args)
    assert transport.send_pipe.await_count == 1


@pytest.mark.parametrize(
    "method,args",
    [
        ("check_connection", (True,)),
        ("check_connection", (256,)),
        ("check_connection", (1, -1)),
        ("get_device_info", (65536,)),
        ("control_cradle_head", (-1,)),
        ("control_camera_direction", ("stop",)),
        ("control_camera_direction", ([],)),
        ("control_tray", (True,)),
        ("control_tray", (0,)),
        ("erase_flash_block", (b"",)),
        ("erase_flash_block", ("1234",)),
        ("read_flash_block", (b"\x00" * 255, 1)),
        ("read_flash_block", (b"\x00", 256)),
        ("write_flash_block", (b"\x00", b"\x00" * 65)),
        ("write_flash_block", (b"\x00" * 191, b"\x00" * 64)),
        ("begin_upgrade", (b"",)),
        ("complete_upgrade", (b"\x00" * 256,)),
        ("write_mcu_chunk", (-1, b"\x00")),
        ("write_mcu_chunk", (65536, b"\x00")),
        ("write_mcu_chunk", (1, b"\x00" * 65)),
        ("write_mcu_chunk_checksummed", (1, b"\x00" * 129)),
        ("write_audio_chunk", (True, b"\x00")),
        ("write_audio_chunk", (1, b"")),
        ("write_audio_chunk", (1, bytearray(b"\x00"))),
        ("check_mcu_file", (b"\x00" * 256,)),
        ("set_upgrade_enabled", (1,)),
        ("begin_audio_upload", (b"\x00" * 9, 1, 0)),
        ("begin_audio_upload", (b"\x00" * 10, -1, 0)),
        ("complete_audio_upload", (b"\x00" * 10, 2**32, 0)),
        ("complete_audio_upload", (b"\x00" * 10, 1, 256)),
    ],
)
async def test_invalid_input_never_sends(monkeypatch, method, args):
    client, transport = client_with_transport(monkeypatch)
    with pytest.raises(ValueError):
        await getattr(client, method)(*args)
    transport.send_pipe.assert_not_awaited()


async def test_explicit_inner_address(monkeypatch):
    client, transport = client_with_transport(monkeypatch, encode_frame(0x82, b""))
    assert await client.get_device_info(0x1234) == b""
    transport.send_pipe.assert_awaited_once_with(bytes.fromhex("57 ab 12 34 02 00 4a"))


@pytest.mark.parametrize("value", range(4))
async def test_calibration_status(monkeypatch, value):
    client, transport = client_with_transport(
        monkeypatch, encode_frame(0xA7, bytes([value]))
    )
    assert await client.get_feed_calibration_status() == value
    transport.send_pipe.assert_awaited_once_with(
        bytes.fromhex("57 ab ff ff 27 01 01 29")
    )


@pytest.mark.parametrize("reply", [b"", b"\x04", b"\x00\x00"])
async def test_invalid_calibration_status(monkeypatch, reply):
    client, _ = client_with_transport(monkeypatch, encode_frame(0xA7, reply))
    with pytest.raises(XlinkError):
        await client.get_feed_calibration_status()


async def test_alternate_tray_request_does_not_assume_reply_meaning(monkeypatch):
    client, transport = client_with_transport(monkeypatch, encode_frame(0x92, b"\x02"))
    assert await client.control_tray(1) == "extending"
    transport.send_pipe.assert_awaited_once_with(
        bytes.fromhex("57 ab ff ff 12 01 01 14")
    )


@pytest.mark.parametrize(
    "method,size,prefix",
    [
        ("write_mcu_chunk", 64, b"\xff\xff"),
        ("write_mcu_chunk_checksummed", 128, b"\xff\xff\x80"),
        ("write_audio_chunk", 128, b"\xff\xff\x80"),
    ],
)
async def test_chunk_boundaries(monkeypatch, method, size, prefix):
    command = 0x21 if method == "write_audio_chunk" else 0x19
    client, transport = client_with_transport(
        monkeypatch, encode_frame(command | 128, b"")
    )
    await getattr(client, method)(65535, b"\xff" * size)
    assert (
        decode_frame(transport.send_pipe.await_args.args[0]).payload
        == prefix + b"\xff" * size
    )
