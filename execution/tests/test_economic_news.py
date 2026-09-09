from datetime import timedelta
from types import SimpleNamespace

from django.test import TestCase, override_settings
from django.utils import timezone

from execution.models import EconomicCalendarEvent, EconomicCalendarRefreshState
from execution.services.economic_news import (
    is_economic_news_blackout,
    refresh_economic_calendar,
    symbol_currencies,
)


@override_settings(
    ECONOMIC_CALENDAR_ENABLED=True,
    TRADING_ECONOMICS_API_KEY="test-key",
    ECONOMIC_CALENDAR_COUNTRIES=["united states", "euro area"],
    ECONOMIC_CALENDAR_MIN_IMPORTANCE=3,
    ECONOMIC_CALENDAR_MAX_STALE_MINUTES=30,
    ECONOMIC_NEWS_BLACKOUT_BEFORE_MINUTES=30,
    ECONOMIC_NEWS_BLACKOUT_AFTER_MINUTES=30,
)
class EconomicNewsTests(TestCase):
    def test_symbol_currency_mapping_covers_pairs_and_non_fx_assets(self):
        self.assertEqual(symbol_currencies("AUDCADm"), {"AUD", "CAD"})
        self.assertEqual(symbol_currencies("XAUUSDm"), {"USD"})
        self.assertEqual(symbol_currencies("GER40m"), {"EUR"})
        self.assertEqual(symbol_currencies("UK100m"), {"GBP"})
        self.assertEqual(symbol_currencies("BTCUSDm"), {"USD"})

    def test_oil_symbols_include_energy_specific_events(self):
        now = timezone.now()
        EconomicCalendarRefreshState.objects.create(
            provider="tradingeconomics",
            last_attempt_at=now,
            last_success_at=now,
        )
        EconomicCalendarEvent.objects.create(
            external_id="eia-inventory",
            starts_at=now,
            country="",
            currency="",
            title="EIA crude oil inventories",
            category="Petroleum status",
            importance=3,
        )

        self.assertTrue(is_economic_news_blackout("USOILm", at=now))
        self.assertTrue(is_economic_news_blackout("UKOILm", at=now))
        self.assertFalse(is_economic_news_blackout("EURUSDm", at=now))

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
        EconomicCalendarRefreshState.objects.create(
            provider="tradingeconomics",
            last_attempt_at=now,
            last_success_at=now,
        )
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

    def test_missing_or_stale_refresh_state_fails_closed(self):
        now = timezone.now()
        self.assertTrue(is_economic_news_blackout("EURUSD", at=now))

        EconomicCalendarRefreshState.objects.create(
            provider="tradingeconomics",
            last_attempt_at=now - timedelta(minutes=31),
            last_success_at=now - timedelta(minutes=31),
        )
        self.assertTrue(is_economic_news_blackout("EURUSD", at=now))

    def test_failed_refresh_preserves_last_success_and_records_error(self):
        now = timezone.now()
        previous_success = now - timedelta(minutes=10)
        EconomicCalendarRefreshState.objects.create(
            provider="tradingeconomics",
            last_success_at=previous_success,
        )
        response = SimpleNamespace(
            raise_for_status=lambda: (_ for _ in ()).throw(RuntimeError("provider down")),
            json=lambda: [],
        )
        session = SimpleNamespace(get=lambda *args, **kwargs: response)

        with self.assertRaisesRegex(RuntimeError, "provider down"):
            refresh_economic_calendar(now=now, session=session)

        state = EconomicCalendarRefreshState.objects.get(provider="tradingeconomics")
        self.assertEqual(state.last_success_at, previous_success)
        self.assertEqual(state.last_attempt_at, now)
        self.assertIn("provider down", state.last_error)
