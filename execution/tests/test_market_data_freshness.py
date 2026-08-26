from datetime import timedelta

from django.test import SimpleTestCase, override_settings
from django.utils import timezone

from execution.tasks import _tick_timestamp_is_stale


@override_settings(
    MT5_TICK_MAX_AGE_SECONDS=120,
    MT5_TICK_FUTURE_TOLERANCE_SECONDS=7200,
)
class TickFreshnessTest(SimpleTestCase):
    def test_accepts_bounded_broker_clock_offset(self):
        now = timezone.now()
        self.assertFalse(_tick_timestamp_is_stale(now + timedelta(hours=1), now=now))

    def test_rejects_old_or_implausibly_future_ticks(self):
        now = timezone.now()
        self.assertTrue(_tick_timestamp_is_stale(now - timedelta(seconds=121), now=now))
        self.assertTrue(_tick_timestamp_is_stale(now + timedelta(hours=3), now=now))
