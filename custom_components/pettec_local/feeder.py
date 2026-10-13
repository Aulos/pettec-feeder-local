"""Local PF05WD commands. No cloud, resets, scheduling or firmware writes."""

from datetime import datetime

from .protocol import (
    FEED,
    READ_CLOCK,
    ProtocolError,
    decode_clock,
    decode_frame,
    encode_frame,
    feed_payload,
)
from .xlink import XlinkClient, XlinkError


class FeederClient:
    """Wrap the recovered inner protocol in the local Xlink transport."""

    def __init__(self, host: str, access_key: int) -> None:
        self._transport = XlinkClient(host, access_key)

    @property
    def mac(self) -> str:
        """The identity received from Xlink discovery."""
        return self._transport.mac

    async def connect(self) -> None:
        await self._transport.connect()

    async def close(self) -> None:
        await self._transport.close()

    async def _request(self, command: int, payload: bytes = b"") -> bytes:
        raw = await self._transport.send_pipe(encode_frame(command, payload))
        try:
            response = decode_frame(raw)
            if response.command != command | 0x80:
                raise ProtocolError("Unexpected feeder response command")
        except ProtocolError as err:
            raise XlinkError(str(err)) from err
        return response.payload

    async def get_clock(self) -> datetime:
        """Read-only connection probe used for setup and availability."""
        payload = await self._request(READ_CLOCK)
        try:
            return decode_clock(payload)
        except ProtocolError as err:
            raise XlinkError(str(err)) from err

    async def dispense(self, quantity: int) -> None:
        """Send exactly one manual feeding command and require a success reply."""
        payload = await self._request(FEED, feed_payload(quantity))
        if len(payload) != 1:
            raise XlinkError("Malformed feeding acknowledgement; outcome is uncertain")
        if payload[0] != 0:
            raise XlinkError(
                f"Feeder returned status 0x{payload[0]:02x}; no automatic retry"
            )
