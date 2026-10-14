"""Local PetTec PF 05WD feeder communication without Home Assistant."""

from .feeder import FeederClient, ScheduleChangedError
from .protocol import FeedingDetails, FeedingRecord, ProtocolError, ScheduleSlot
from .xlink import (
    AuthenticationError,
    UnsupportedDeviceError,
    XlinkError,
    initialize_access_key,
)

__all__ = [
    "AuthenticationError",
    "FeederClient",
    "FeedingDetails",
    "FeedingRecord",
    "ProtocolError",
    "ScheduleChangedError",
    "ScheduleSlot",
    "UnsupportedDeviceError",
    "XlinkError",
    "initialize_access_key",
]
