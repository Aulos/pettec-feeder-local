"""Local PetTec PF 05WD feeder communication without Home Assistant."""

from .feeder import FeederClient, ScheduleChangedError
from .protocol import ProtocolError, ScheduleSlot
from .xlink import (
    AuthenticationError,
    UnsupportedDeviceError,
    XlinkError,
    initialize_access_key,
)

__all__ = [
    "AuthenticationError",
    "FeederClient",
    "ProtocolError",
    "ScheduleChangedError",
    "ScheduleSlot",
    "UnsupportedDeviceError",
    "XlinkError",
    "initialize_access_key",
]
