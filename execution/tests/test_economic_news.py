from datetime import timedelta
from types import SimpleNamespace

from django.test import TestCase, override_settings
from django.utils import timezone

from execution.models import EconomicCalendarEvent
from execution.services.economic_news import (
    is_economic_news_blackout,
    refresh_economic_calendar,
)


@override_settings(
    ECONOMIC_CALENDAR_ENABLED=True,
    TRADING_ECONOMICS_API_KEY="test-key",
    ECONOMIC_CALENDAR_COUNTRIES=["united states", "euro area"],
    ECONOMIC_CALENDAR_MIN_IMPORTANCE=3,
    ECONOMIC_NEWS_BLACKOUT_BEFORE_MINUTES=30,
    ECONOMIC_NEWS_BLACKOUT_AFTER_MINUTES=30,
)
class EconomicNewsTests(TestCase):
    def test_refresh_persists_provider_events_and_drives_symbol_blackout(self):
        now = timezone.now().replace(microsecond=0)
        response = SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: [
                {
                    "CalendarId": "nfp-1",
                    "Date": (now + timedelta(minutes=10)).isoformat(),
                    "Country": "United States",
                    "Event": "Non Farm Payrolls",
                    "Category": "Labour",
                    "Importance": 3,
                }
            ],
        )
        session = SimpleNamespace(get=lambda *args, **kwargs: response)

        self.assertEqual(refresh_economic_calendar(now=now, session=session), 1)
        self.assertTrue(is_economic_news_blackout("EURUSDm", at=now))
        self.assertTrue(is_economic_news_blackout("XAUUSD", at=now))
        self.assertFalse(is_economic_news_blackout("EURJPY", at=now))

    def test_low_impact_or_outside_window_does_not_block(self):
        now = timezone.now()
        EconomicCalendarEvent.objects.create(
            external_id="low",
            starts_at=now,
            country="united states",
            title="Minor event",
            importance=1,
        )
        EconomicCalendarEvent.objects.create(
            external_id="later",
            starts_at=now + timedelta(hours=2),
            country="united states",
            title="Major event later",
            importance=3,
        )

        self.assertFalse(is_economic_news_blackout("GBPUSD", at=now))
