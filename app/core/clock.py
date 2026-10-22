"""UTC clock helper; database timestamps remain naive UTC for M1 compatibility."""
from datetime import datetime, timezone


def now_utc():
    return datetime.now(timezone.utc).replace(tzinfo=None)
