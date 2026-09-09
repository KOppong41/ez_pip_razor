from __future__ import annotations

from datetime import timedelta, timezone as dt_timezone
import logging
from urllib.parse import quote

import requests
from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from execution.models import EconomicCalendarEvent, EconomicCalendarRefreshState
from execution.utils.symbols import canonical_symbol


PROVIDER = "tradingeconomics"
logger = logging.getLogger(__name__)
CURRENCY_COUNTRIES = {
    "USD": "united states",
    "EUR": "euro area",
    "GBP": "united kingdom",
    "JPY": "japan",
    "CHF": "switzerland",
    "CAD": "canada",
    "AUD": "australia",
    "NZD": "new zealand",
}
SYMBOL_NEWS_CURRENCIES = {
    "XAUUSD": {"USD"},
    "XAGUSD": {"USD"},
    "USOIL": {"USD"},
    "UKOIL": {"USD"},
    "US30": {"USD"},
    "US500": {"USD"},
    "NAS100": {"USD"},
    "GER40": {"EUR"},
    "UK100": {"GBP"},
    "BTCUSD": {"USD"},
    "ETHUSD": {"USD"},
}
ENERGY_SYMBOLS = {"USOIL", "UKOIL"}
ENERGY_KEYWORDS = ("eia", "crude oil inventories", "petroleum status", "opec")


def symbol_currencies(symbol: str) -> set[str]:
    mapped = SYMBOL_NEWS_CURRENCIES.get(canonical_symbol(symbol))
    if mapped:
        return set(mapped)
    normalized = "".join(char for char in (symbol or "").upper() if char.isalpha())
    return {code for code in CURRENCY_COUNTRIES if code in normalized}


def _event_time(value):
    parsed = parse_datetime(str(value or ""))
    if parsed is None:
        raise ValueError("Economic calendar event has an invalid Date")
    if timezone.is_naive(parsed):
        parsed = parsed.replace(tzinfo=dt_timezone.utc)
    return parsed


def refresh_economic_calendar(*, now=None, session=requests) -> int:
    """Fetch and persist the configured high-impact Trading Economics window."""
    if not getattr(settings, "ECONOMIC_CALENDAR_ENABLED", False):
        return 0
    current = now or timezone.now()
    EconomicCalendarRefreshState.objects.update_or_create(
        provider=PROVIDER,
        defaults={"last_attempt_at": current},
    )
    api_key = str(getattr(settings, "TRADING_ECONOMICS_API_KEY", "") or "").strip()
    if not api_key:
        EconomicCalendarRefreshState.objects.filter(provider=PROVIDER).update(
            last_error="TRADING_ECONOMICS_API_KEY is required when the calendar is enabled",
        )
        raise RuntimeError("TRADING_ECONOMICS_API_KEY is required when the calendar is enabled")

    countries = list(getattr(settings, "ECONOMIC_CALENDAR_COUNTRIES", CURRENCY_COUNTRIES.values()))
    country_path = quote(",".join(countries), safe=",")
    start = (current - timedelta(days=1)).date().isoformat()
    end = (current + timedelta(days=2)).date().isoformat()
    url = f"https://api.tradingeconomics.com/calendar/country/{country_path}/{start}/{end}"
    try:
        response = session.get(
            url,
            params={
                "c": api_key,
                "importance": int(
                    getattr(settings, "ECONOMIC_CALENDAR_MIN_IMPORTANCE", 3)
                ),
                "f": "json",
            },
            timeout=int(
                getattr(settings, "ECONOMIC_CALENDAR_TIMEOUT_SECONDS", 10)
            ),
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, list):
            raise ValueError("Economic calendar provider returned a non-list payload")
    except Exception as exc:
        EconomicCalendarRefreshState.objects.filter(provider=PROVIDER).update(
            last_attempt_at=current,
            last_error=str(exc)[:2000],
        )
        raise

    imported = 0
    with transaction.atomic():
        for item in payload:
            if not isinstance(item, dict):
                continue
            external_id = str(item.get("CalendarId") or item.get("calendarId") or "").strip()
            if not external_id:
                continue
            starts_at = _event_time(item.get("Date") or item.get("date"))
            country = str(item.get("Country") or item.get("country") or "").strip().lower()
            currency = str(item.get("Currency") or item.get("currency") or "").strip().upper()
            EconomicCalendarEvent.objects.update_or_create(
                provider=PROVIDER,
                external_id=external_id,
                defaults={
                    "starts_at": starts_at,
                    "country": country,
                    "currency": currency,
                    "title": str(item.get("Event") or item.get("event") or item.get("Category") or "Economic event")[:255],
                    "category": str(item.get("Category") or item.get("category") or "")[:128],
                    "importance": int(item.get("Importance") or item.get("importance") or 0),
                    "raw_payload": item,
                },
            )
            imported += 1
        EconomicCalendarEvent.objects.filter(
            provider=PROVIDER,
            starts_at__lt=current - timedelta(days=7),
        ).delete()
        EconomicCalendarRefreshState.objects.select_for_update().filter(
            provider=PROVIDER
        ).update(
            last_attempt_at=current,
            last_success_at=current,
            last_error="",
            event_count=imported,
        )
    return imported


def is_economic_news_blackout(symbol: str, *, at=None) -> bool:
    if not getattr(settings, "ECONOMIC_CALENDAR_ENABLED", False):
        return False
    currencies = symbol_currencies(symbol)
    if not currencies:
        return False
    countries = {CURRENCY_COUNTRIES[code] for code in currencies}
    current = at or timezone.now()
    max_stale = timedelta(
        minutes=max(
            1,
            int(getattr(settings, "ECONOMIC_CALENDAR_MAX_STALE_MINUTES", 30)),
        )
    )
    refresh_state = EconomicCalendarRefreshState.objects.filter(
        provider=PROVIDER
    ).first()
    if (
        refresh_state is None
        or refresh_state.last_success_at is None
        or refresh_state.last_success_at < current - max_stale
    ):
        logger.warning(
            "Economic calendar is stale; blocking entries for %s (last_success=%s)",
            symbol,
            refresh_state.last_success_at if refresh_state else None,
        )
        return True
    before = timedelta(minutes=int(getattr(settings, "ECONOMIC_NEWS_BLACKOUT_BEFORE_MINUTES", 30)))
    after = timedelta(minutes=int(getattr(settings, "ECONOMIC_NEWS_BLACKOUT_AFTER_MINUTES", 30)))
    minimum = int(getattr(settings, "ECONOMIC_CALENDAR_MIN_IMPORTANCE", 3))
    event_scope = Q(currency__in=currencies) | Q(country__in=countries)
    if canonical_symbol(symbol) in ENERGY_SYMBOLS:
        energy_scope = Q()
        for keyword in ENERGY_KEYWORDS:
            energy_scope |= Q(title__icontains=keyword) | Q(category__icontains=keyword)
        event_scope |= energy_scope
    return EconomicCalendarEvent.objects.filter(
        event_scope,
        importance__gte=minimum,
        starts_at__gte=current - after,
        starts_at__lte=current + before,
    ).exists()
