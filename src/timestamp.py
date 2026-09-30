"""Timestamps written into commit messages, conflict markers and the pending file."""

from datetime import datetime, timedelta, timezone

TIMESTAMP_TZ = timezone(timedelta(hours=7))


def now_iso() -> str:
    return datetime.now(TIMESTAMP_TZ).isoformat(timespec="seconds")
