"""Unique identifier conventions shared by entities and card actions."""

FEED = "feed"
SCHEDULE_TIME = "schedule_time"


def unique_id(entry_id: str, suffix: str) -> str:
    return f"{entry_id}_{suffix}"


def schedule_suffix(slot: int, field: str) -> str:
    return f"schedule_{slot}_{field}"
