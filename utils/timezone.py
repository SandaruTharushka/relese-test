from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

SL_TIMEZONE = ZoneInfo("Asia/Colombo")


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def now_sl() -> datetime:
    return now_utc().astimezone(SL_TIMEZONE)


def to_sl(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(SL_TIMEZONE)


def format_sl_datetime(dt: datetime | None) -> str:
    converted = to_sl(dt)
    return converted.strftime('%Y-%m-%d %H:%M') if converted else ''


def format_sl_date(dt: datetime | None) -> str:
    converted = to_sl(dt)
    return converted.strftime('%Y-%m-%d') if converted else ''
