from types import SimpleNamespace
from datetime import date, datetime, timezone as dt_timezone

from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings

from brokers.models import BrokerAccount
from execution.models import AccountRiskDay
from execution.services.daily_risk import (
    create_account_snapshot,
    risk_day_window,
    update_account_risk_day,
)


@override_settings(ACCOUNT_RISK_OPENING_SNAPSHOT_GRACE_SECONDS=-1)
class AccountRiskDayTests(TestCase):
    def setUp(self):
        self.account = BrokerAccount.objects.create(
            name="Risk-day account",
            broker="mt5",
            connector="mt5_local",
            account_ref="risk-day-account",
            is_verified=True,
            timezone="Europe/London",
        )

    @staticmethod
    def _account_info(*, balance, equity, trade_mode=2):
        return SimpleNamespace(
            balance=balance,
            equity=equity,
            margin=0,
            margin_free=equity,
            margin_level=0,
            currency="USD",
            trade_mode=trade_mode,
        )

    def test_late_live_start_without_trustworthy_history_stays_unlocked(self):
        snapshot = create_account_snapshot(
            self.account,
            self._account_info(balance=9850, equity=9850),
        )

        risk_day = update_account_risk_day(
            self.account,
            snapshot,
            connector=SimpleNamespace(),
            trade_mode=2,
        )

        self.assertFalse(risk_day.baseline_locked)
        self.assertEqual(risk_day.baseline_source, "unavailable")
        self.assertIsNone(risk_day.starting_equity)

    def test_risk_day_window_uses_broker_timezone(self):
        self.account.timezone = "Pacific/Auckland"
        observed_at = datetime(2026, 1, 1, 11, 30, tzinfo=dt_timezone.utc)

        window = risk_day_window(self.account, observed_at)

        self.assertEqual(window.risk_date, date(2026, 1, 2))
        self.assertEqual(
            window.start,
            datetime(2026, 1, 1, 11, 0, tzinfo=dt_timezone.utc),
        )

    def test_live_start_reconstructs_starting_balance_from_mt5_history(self):
        snapshot = create_account_snapshot(
            self.account,
            self._account_info(balance=9850, equity=9850),
        )
        loss = SimpleNamespace(
            profit=-150,
            commission=0,
            swap=0,
            fee=0,
            entry=1,
            position_id=42,
        )
        # A same-day entry proves this was not an overnight position.
        entry = SimpleNamespace(
            profit=0,
            commission=0,
            swap=0,
            fee=0,
            entry=0,
            position_id=42,
        )
        connector = SimpleNamespace(
            history_deals_for_account=lambda *args, **kwargs: (entry, loss),
        )

        risk_day = update_account_risk_day(
            self.account,
            snapshot,
            connector=connector,
            trade_mode=2,
            broker_positions=(),
        )

        self.assertTrue(risk_day.baseline_locked)
        self.assertEqual(risk_day.baseline_source, "mt5_history")
        self.assertEqual(risk_day.starting_balance, 10000)
        self.assertEqual(risk_day.starting_equity, 10000)
        self.assertEqual(risk_day.realized_pnl, -150)

    def test_overnight_exposure_makes_history_reconstruction_untrustworthy(self):
        snapshot = create_account_snapshot(
            self.account,
            self._account_info(balance=9850, equity=9800),
        )
        window = risk_day_window(self.account, snapshot.captured_at)
        overnight = SimpleNamespace(time=window.start.timestamp() - 60)
        connector = SimpleNamespace(
            history_deals_for_account=lambda *args, **kwargs: (),
        )

        risk_day = update_account_risk_day(
            self.account,
            snapshot,
            connector=connector,
            trade_mode=2,
            broker_positions=(overnight,),
        )

        self.assertFalse(risk_day.baseline_locked)
        self.assertEqual(risk_day.baseline_source, "unavailable")

    def test_position_without_open_time_makes_live_baseline_untrustworthy(self):
        snapshot = create_account_snapshot(
            self.account,
            self._account_info(balance=10000, equity=10000),
        )
        connector = SimpleNamespace(
            history_deals_for_account=lambda *args, **kwargs: (),
        )

        risk_day = update_account_risk_day(
            self.account,
            snapshot,
            connector=connector,
            trade_mode=2,
            broker_positions=(SimpleNamespace(time=0),),
        )

        self.assertFalse(risk_day.baseline_locked)

    def test_locked_baseline_never_moves_and_high_equity_only_increases(self):
        first = create_account_snapshot(
            self.account,
            self._account_info(balance=10000, equity=10000, trade_mode=0),
        )
        risk_day = update_account_risk_day(
            self.account,
            first,
            trade_mode=0,
        )
        second = create_account_snapshot(
            self.account,
            self._account_info(balance=9000, equity=9000, trade_mode=0),
        )
        update_account_risk_day(
            self.account,
            second,
            trade_mode=0,
        )

        risk_day = AccountRiskDay.objects.get(pk=risk_day.pk)
        self.assertEqual(risk_day.starting_equity, 10000)
        self.assertEqual(risk_day.high_equity, 10000)

        risk_day.starting_equity = 8000
        with self.assertRaisesRegex(ValidationError, "baseline cannot be changed"):
            risk_day.save()
