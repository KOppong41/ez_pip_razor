from datetime import timedelta

from django.test import SimpleTestCase, override_settings
from django.utils import timezone

from execution.tasks import _tick_timestamp_is_stale, _tick_timestamp_problem


@override_settings(
    MT5_TICK_MAX_AGE_SECONDS=120,
    MT5_TICK_FUTURE_TOLERANCE_SECONDS=120,
)
class TickFreshnessTest(SimpleTestCase):
    def test_accepts_small_transport_clock_offset(self):
        now = timezone.now()
        self.assertFalse(_tick_timestamp_is_stale(now + timedelta(seconds=30), now=now))
        self.assertIsNone(
            _tick_timestamp_problem(now + timedelta(seconds=30), now=now)
        )

    def test_distinguishes_stale_data_from_host_clock_skew(self):
        now = timezone.now()
        self.assertTrue(_tick_timestamp_is_stale(now - timedelta(seconds=121), now=now))
        self.assertTrue(_tick_timestamp_is_stale(now + timedelta(seconds=121), now=now))
        self.assertEqual(
            _tick_timestamp_problem(now - timedelta(seconds=121), now=now),
            "market_data_stale",
        )
        self.assertEqual(
            _tick_timestamp_problem(now + timedelta(seconds=121), now=now),
            "broker_clock_skew",
        )
