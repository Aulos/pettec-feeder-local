"""One-shot feeder commands and the shared session contract."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import TypeVar

from homeassistant.exceptions import HomeAssistantError
from pettec_xlink.feeder import FeederClient
from pettec_xlink.protocol import FeedingDetails
from pettec_xlink.xlink import AuthenticationError, XlinkError

_T = TypeVar("_T")


class CommandFailed(HomeAssistantError):
    """The request did not reach the feeder, so retrying is safe."""


class UncertainWrite(HomeAssistantError):
    """The request may have reached the feeder; never resend it blindly."""


class FeederCommands:
    """Connect, send one request, and report whether the outcome is certain."""

    def __init__(self, client: FeederClient) -> None:
        self._client = client

    async def _run(
        self,
        action: Callable[[], Awaitable[_T]],
        *,
        uncertain: str | None,
    ) -> _T:
        try:
            await self._client.connect()
        except AuthenticationError as err:
            await self._client.close()
            raise CommandFailed(
                "The feeder rejected its access key; nothing was sent."
            ) from err
        except (XlinkError, OSError, TimeoutError) as err:
            await self._client.close()
            raise CommandFailed(
                "Unable to reach the feeder; nothing was sent."
            ) from err

        try:
            return await action()
        except ValueError as err:
            # Every wrapped client method validates its input before sending.
            await self._client.close()
            raise CommandFailed(str(err)) from err
        except AuthenticationError as err:
            await self._client.close()
            raise CommandFailed(
                "The feeder rejected the request; nothing was changed."
            ) from err
        except (XlinkError, OSError, TimeoutError) as err:
            await self._client.close()
            if uncertain is None:
                raise CommandFailed("Unable to read from the feeder.") from err
            raise UncertainWrite(uncertain) from err

    async def dispense(self, grams: int) -> None:
        await self._run(
            lambda: self._client.dispense(grams),
            uncertain=(
                "The feeding request failed. Check the bowl before trying again; "
                "the feeder may have received the request."
            ),
        )

    async def sync_clock(self, value: datetime) -> datetime:
        return await self._run(
            lambda: self._client.set_clock(value),
            uncertain=(
                "Unable to verify clock sync. The clock may already have changed; "
                "check the device clock before trying again."
            ),
        )

    async def control_tray(self) -> str:
        return await self._run(
            lambda: self._client.control_tray(),
            uncertain=(
                "Tray control failed. Inspect the tray before trying again; "
                "it may already be moving."
            ),
        )

    async def reset_feeding_weight(self) -> int:
        return await self._run(
            lambda: self._client.reset_feeding_weight(),
            uncertain=(
                "Weight reset could not be verified. Check the remaining food "
                "reading before trying again; the reset may already have happened."
            ),
        )

    async def set_tray_interval(self, minutes: int | None) -> int | None:
        return await self._run(
            lambda: self._client.set_tray_retraction_interval(minutes),
            uncertain=(
                "Unable to verify tray interval. Check its refreshed value before "
                "trying again; the change may already have been applied."
            ),
        )

    async def read_feeder_details(self, selector: int) -> FeedingDetails:
        return await self._run(
            lambda: self._client.get_feeder_details(selector),
            uncertain=None,
        )
