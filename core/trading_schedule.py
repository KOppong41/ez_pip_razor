"""Validation and clock handling for visible bot trading windows."""
from datetime import time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.core.exceptions import ValidationError

DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def validate_trading_windows(windows):
    if not isinstance(windows, list) or len(windows) > 8:
        raise ValidationError("Provide at most eight trading windows.")
    for window in windows:
        try:
            if not isinstance(window, dict):
                raise ValueError()
            time.fromisoformat(window["start"])
            time.fromisoformat(window["end"])
            ZoneInfo(window["timezone"])
            days = window["allowed_days"]
            if not isinstance(days, list) or not days or any(day not in DAYS for day in days):
                raise ValueError()
        except (KeyError, TypeError, ValueError, ZoneInfoNotFoundError):
            raise ValidationError("Each window needs valid start/end times, an IANA timezone and allowed weekdays.")


def window_contains(window, now):
    local = now.astimezone(ZoneInfo(window["timezone"]))
    start, end = time.fromisoformat(window["start"]), time.fromisoformat(window["end"])
    current = local.time().replace(tzinfo=None)
    # An overnight window belongs to the day on which it starts.
    day = local - timedelta(days=1) if start > end and current <= end else local
    if DAYS[day.weekday()] not in window["allowed_days"]:
        return False
    return start <= current <= end if start <= end else current >= start or current <= end
