from __future__ import annotations

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.utils import timezone


UTC = ZoneInfo("UTC")


def get_broker_timezone(broker_account) -> ZoneInfo:
    """
    Return the configured timezone for a broker account, defaulting to UTC.
    """
    tz_name = (getattr(broker_account, "timezone", None) or "UTC").strip() or "UTC"
    try:
        return ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, ValueError):
        return UTC


def to_broker_timezone(value, broker_account):
    """
    Convert a datetime to the broker's timezone, preserving awareness.
    """
    if value is None:
        return None
    tz = get_broker_timezone(broker_account)
    if timezone.is_naive(value):
        value = timezone.make_aware(value, UTC)
    return timezone.localtime(value, tz)
