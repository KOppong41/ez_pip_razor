from datetime import datetime, time, timedelta, timezone as dt_timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase

from bots.models import Asset, Bot
from brokers.models import BrokerAccount
from execution.models import MT5ConnectionState, RiskPolicy, ScalperRunLog
from execution.services.bot_schedule import reconcile_trading_schedules, set_bot_status
from execution.services.market_hours import MarketStatus
from execution.services.psychology import update_bot_after_realized_pnl
from execution.services.runtime_control import stop_user_automation
from execution.services.trading_type import is_within_trading_window
from execution.tasks_market_guard import _apply_market_status


def utc(day=22, hour=12, minute=0, *, month=9):
    return datetime(2026, month, day, hour, minute, tzinfo=dt_timezone.utc)


class BotScheduleTests(TestCase):
    def setUp(self):
        self.enterContext(patch("bots.models.get_bot_limit", return_value=10))
        self.user = get_user_model().objects.create_user("schedule-user")
        self.asset = Asset.objects.create(symbol="SCHEDULEUSD", category="forex")
        self.account = BrokerAccount.objects.create(
            owner=self.user, name="Schedule account", broker="mt5",
            connector="mt5_local", account_ref="schedule-test",
        )
        self.policy = RiskPolicy.objects.create(broker_account=self.account, entries_enabled=True)
        self.bot = Bot.objects.create(
            owner=self.user, name="Scheduled bot", asset=self.asset,
            broker_account=self.account, status="active", auto_trade=True,
            default_qty="0.01", trading_timezone="UTC",
            trading_window_start=time(9), trading_window_end=time(17),
            allowed_trading_days=["mon", "tue", "wed", "thu", "fri"],
            risk_per_trade_pct=Decimal("3.0"),
        )
        self.client.force_login(self.user)

    def state(self):
        self.bot.refresh_from_db()
        return self.bot.status, self.bot.schedule_paused

    def pause_by_schedule(self):
        self.assertEqual(reconcile_trading_schedules(now=utc(hour=18))["paused"], 1)
        self.assertEqual(self.state(), ("paused", True))

    def control(self, action):
        return self.client.post(
            f"/api/bots/{self.bot.pk}/control/", {"action": action},
            content_type="application/json",
        )

    def test_pause_and_resume_are_idempotent_and_preserve_configuration(self):
        original = Bot.objects.filter(pk=self.bot.pk).values().get()
        self.pause_by_schedule()
        self.assertEqual(reconcile_trading_schedules(now=utc(hour=19)), {"paused": 0, "resumed": 0, "errors": 0})
        self.assertEqual(reconcile_trading_schedules(now=utc(day=23))["resumed"], 1)
        self.assertEqual(self.state(), ("active", False))
        self.assertEqual(Bot.objects.filter(pk=self.bot.pk).values().get(), original)
        self.policy.refresh_from_db()
        self.assertTrue(self.policy.entries_enabled)

    def test_manual_pause_cancels_resume_even_when_already_schedule_paused(self):
        self.pause_by_schedule()
        response = self.control("pause")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertFalse(response.json()["schedule_paused"])
        self.policy.entries_enabled = True
        self.policy.save()
        reconcile_trading_schedules(now=utc(day=23))
        self.assertEqual(self.state(), ("paused", False))

    def test_stopped_bot_is_not_resumed(self):
        self.pause_by_schedule()
        self.assertEqual(self.control("stop").status_code, 200)
        reconcile_trading_schedules(now=utc(day=23))
        self.assertEqual(self.state(), ("stopped", False))

    def test_start_outside_window_waits_for_next_session(self):
        MT5ConnectionState.objects.create(broker_account=self.account, connected=True, account_mode="demo")
        with patch("execution.services.bot_schedule.timezone.now", return_value=utc(hour=18)):
            response = self.control("start")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["status"], "paused")
        self.assertTrue(response.json()["schedule_paused"])
        reconcile_trading_schedules(now=utc(day=23))
        self.assertEqual(self.state(), ("active", False))

    def test_pausing_sibling_keeps_account_enabled_for_scheduled_bot(self):
        self.pause_by_schedule()
        sibling = Bot.objects.create(owner=self.user, name="Sibling", asset=Asset.objects.create(symbol="SIBLINGUSD"),
                                     broker_account=self.account, status="active", default_qty="0.01")
        response = self.client.post(f"/api/bots/{sibling.pk}/control/", {"action": "pause"}, content_type="application/json")
        self.assertEqual(response.status_code, 200, response.content)
        self.policy.refresh_from_db()
        self.assertTrue(self.policy.entries_enabled)
        reconcile_trading_schedules(now=utc(day=23))
        self.assertEqual(self.state(), ("active", False))

    def test_disabled_account_policy_and_emergency_stop_block_resume(self):
        self.pause_by_schedule()
        for fields in ({"entries_enabled": False}, {"entries_enabled": True, "emergency_stop": True}):
            RiskPolicy.objects.filter(pk=self.policy.pk).update(**fields)
            self.assertEqual(reconcile_trading_schedules(now=utc(day=23))["resumed"], 0)
            self.assertEqual(self.state(), ("paused", True))

    def test_cooldown_is_preserved_and_must_expire_before_resume(self):
        self.pause_by_schedule()
        until = utc(day=23, hour=13)
        Bot.objects.filter(pk=self.bot.pk).update(paused_until=until)
        reconcile_trading_schedules(now=utc(day=23))
        self.assertEqual(self.state(), ("paused", True))
        self.assertEqual(self.bot.paused_until, until)
        reconcile_trading_schedules(now=until + timedelta(minutes=1))
        self.assertEqual(self.state(), ("active", False))

    def test_loss_streak_pause_takes_ownership_from_schedule(self):
        self.pause_by_schedule()
        self.bot.loss_streak_autopause_enabled = True
        self.bot.max_loss_streak_before_pause = 1
        self.bot.loss_streak_cooldown_min = 60
        with patch("execution.services.psychology._get_settings", return_value=None), patch(
            "execution.services.psychology.timezone.now", return_value=utc(hour=18)
        ):
            update_bot_after_realized_pnl(SimpleNamespace(bot=self.bot), Decimal("-1"))
        self.assertEqual(self.state(), ("paused", False))
        self.assertEqual(self.bot.pause_reason, "loss_cooldown")
        reconcile_trading_schedules(now=utc(day=23))
        self.assertEqual(self.state(), ("active", False))
        self.assertEqual(self.bot.pause_reason, "")
        self.assertIsNone(self.bot.paused_until)
        self.assertEqual(reconcile_trading_schedules(now=utc(day=23))["resumed"], 0)

    def test_timed_loss_pause_resumes_only_after_expiry(self):
        self.bot.loss_streak_autopause_enabled = True
        self.bot.max_loss_streak_before_pause = 1
        self.bot.loss_streak_cooldown_min = 60
        self.bot.save(update_fields=[
            "loss_streak_autopause_enabled", "max_loss_streak_before_pause",
            "loss_streak_cooldown_min",
        ])
        with patch("execution.services.psychology._get_settings", return_value=None), patch(
            "execution.services.psychology.timezone.now", return_value=utc(hour=10)
        ):
            update_bot_after_realized_pnl(SimpleNamespace(bot=self.bot), Decimal("-1"))
        self.assertEqual(self.state(), ("paused", False))
        self.assertEqual(self.bot.pause_reason, "loss_cooldown")
        self.assertEqual(self.bot.paused_until, utc(hour=11))
        self.assertEqual(reconcile_trading_schedules(now=utc(hour=10, minute=59))["resumed"], 0)
        self.assertEqual(self.state(), ("paused", False))
        self.assertEqual(reconcile_trading_schedules(now=utc(hour=11))["resumed"], 1)
        self.assertEqual(self.state(), ("active", False))
        self.assertIsNone(self.bot.paused_until)

    def test_zero_minute_loss_pause_requires_explicit_start(self):
        self.bot.loss_streak_autopause_enabled = True
        self.bot.max_loss_streak_before_pause = 2
        self.bot.loss_streak_cooldown_min = 0
        self.bot.save(update_fields=[
            "loss_streak_autopause_enabled", "max_loss_streak_before_pause",
            "loss_streak_cooldown_min",
        ])
        with patch("execution.services.psychology._get_settings", return_value=None):
            update_bot_after_realized_pnl(SimpleNamespace(bot=self.bot), Decimal("-1"))
            self.assertEqual(self.state(), ("active", False))
            update_bot_after_realized_pnl(SimpleNamespace(bot=self.bot), Decimal("-1"))
        self.assertEqual(self.state(), ("paused", False))
        self.assertEqual(self.bot.current_loss_streak, 2)
        self.assertEqual(self.bot.pause_reason, "manual")
        self.assertIsNone(self.bot.paused_until)
        self.assertEqual(reconcile_trading_schedules(now=utc(day=23))["resumed"], 0)
        self.assertEqual(self.state(), ("paused", False))
        with patch("execution.services.bot_schedule.timezone.now", return_value=utc(hour=12)):
            set_bot_status(self.bot, "active")
        self.assertEqual(self.state(), ("active", False))
        self.assertEqual(self.bot.pause_reason, "")

    def test_manual_pause_cannot_resume_with_stale_schedule_flag(self):
        Bot.objects.filter(pk=self.bot.pk).update(
            status="paused", pause_reason="manual", schedule_paused=True,
        )
        self.assertEqual(reconcile_trading_schedules(now=utc(day=23))["resumed"], 0)
        self.assertEqual(self.state(), ("paused", True))
        self.assertEqual(self.bot.pause_reason, "manual")

    def test_loss_cooldown_waits_for_expiry_and_preserves_manual_pause(self):
        until = utc(day=22, hour=13)
        Bot.objects.filter(pk=self.bot.pk).update(
            status="paused", pause_reason="loss_cooldown", paused_until=until,
        )
        self.assertEqual(reconcile_trading_schedules(now=utc(hour=12))["resumed"], 0)
        self.assertEqual(self.state(), ("paused", False))
        set_bot_status(self.bot, "paused")
        self.assertEqual(self.bot.pause_reason, "manual")
        self.assertIsNone(self.bot.paused_until)
        self.assertEqual(reconcile_trading_schedules(now=utc(hour=14))["resumed"], 0)
        self.assertEqual(self.state(), ("paused", False))

    def test_late_loss_update_cannot_take_over_manual_pause(self):
        stale_bot = Bot.objects.get(pk=self.bot.pk)
        stale_bot.loss_streak_autopause_enabled = True
        stale_bot.max_loss_streak_before_pause = 1
        stale_bot.loss_streak_cooldown_min = 60
        stale_bot.save(update_fields=[
            "loss_streak_autopause_enabled", "max_loss_streak_before_pause",
            "loss_streak_cooldown_min",
        ])
        set_bot_status(self.bot, "paused")
        with patch("execution.services.psychology._get_settings", return_value=None):
            update_bot_after_realized_pnl(SimpleNamespace(bot=stale_bot), Decimal("-1"))
        self.assertEqual(self.state(), ("paused", False))
        self.assertEqual(self.bot.pause_reason, "manual")
        self.assertIsNone(self.bot.paused_until)
        self.assertEqual(self.bot.current_loss_streak, 1)

    def test_loss_cooldown_transfers_to_schedule_outside_window(self):
        Bot.objects.filter(pk=self.bot.pk).update(
            status="paused", pause_reason="loss_cooldown", paused_until=utc(hour=18),
        )
        self.assertEqual(reconcile_trading_schedules(now=utc(hour=19))["paused"], 1)
        self.assertEqual(self.state(), ("paused", True))
        self.assertEqual(self.bot.pause_reason, "schedule")
        self.assertEqual(reconcile_trading_schedules(now=utc(day=23))["resumed"], 1)
        self.assertEqual(self.state(), ("active", False))

    def test_loss_cooldown_cannot_resume_through_disabled_account_or_kill_switch(self):
        Bot.objects.filter(pk=self.bot.pk).update(
            status="paused", pause_reason="loss_cooldown", paused_until=utc(hour=11),
        )
        self.policy.entries_enabled = False
        self.policy.save(update_fields=["entries_enabled"])
        self.assertEqual(reconcile_trading_schedules(now=utc(hour=12))["resumed"], 0)
        self.policy.entries_enabled = True
        self.policy.save(update_fields=["entries_enabled"])
        Bot.objects.filter(pk=self.bot.pk).update(kill_switch_triggered_at=utc(hour=11))
        self.assertEqual(reconcile_trading_schedules(now=utc(hour=12))["resumed"], 0)
        self.assertEqual(self.state(), ("paused", False))

    def test_kill_switch_latch_cannot_be_replaced_by_loss_pause_or_schedule_resume(self):
        self.bot.loss_streak_autopause_enabled = True
        self.bot.max_loss_streak_before_pause = 1
        self.bot.loss_streak_cooldown_min = 0
        self.bot.save(update_fields=[
            "loss_streak_autopause_enabled", "max_loss_streak_before_pause",
            "loss_streak_cooldown_min",
        ])
        Bot.objects.filter(pk=self.bot.pk).update(
            status="stopped", kill_switch_triggered_at=utc(hour=10),
        )
        with patch("execution.services.psychology._get_settings", return_value=None):
            update_bot_after_realized_pnl(SimpleNamespace(bot=self.bot), Decimal("-1"))
        self.assertEqual(self.state(), ("stopped", False))
        self.assertEqual(self.bot.pause_reason, "")
        self.assertEqual(self.bot.current_loss_streak, 1)
        Bot.objects.filter(pk=self.bot.pk).update(
            status="paused", schedule_paused=True, pause_reason="schedule",
        )
        self.assertEqual(reconcile_trading_schedules(now=utc(day=23))["resumed"], 0)
        self.assertEqual(self.state(), ("paused", True))
        self.assertEqual(self.bot.kill_switch_triggered_at, utc(hour=10))

    def test_app_exit_cancels_scheduled_and_legacy_market_resume(self):
        self.pause_by_schedule()
        legacy = Bot.objects.create(owner=self.user, name="Legacy", asset=Asset.objects.create(symbol="LEGACYUSD"),
                                   broker_account=self.account, default_qty="0.01",
                                   scalper_params={"_market_guard": {"was": "active"}})
        stop_user_automation(self.user)
        self.assertEqual(self.state(), ("stopped", False))
        legacy.refresh_from_db()
        self.assertNotIn("_market_guard", legacy.scalper_params)
        reconcile_trading_schedules(now=utc(day=23))
        self.assertEqual(self.state(), ("stopped", False))

    def test_account_stop_cancels_schedule_resume(self):
        self.pause_by_schedule()
        response = self.client.post("/api/personal/control/", {
            "action": "stop", "broker_account_id": self.account.pk,
        }, content_type="application/json")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(self.state(), ("stopped", False))

    def test_market_reopen_outside_window_hands_off_to_schedule(self):
        closed = MarketStatus(is_open=False, reason="closed", checked_at=utc())
        _apply_market_status(self.bot.pk, closed, utc())
        self.assertEqual(self.state(), ("stopped", False))
        opened = MarketStatus(is_open=True, reason="open", checked_at=utc(hour=18))
        _apply_market_status(self.bot.pk, opened, utc(hour=18))
        self.assertEqual(self.state(), ("paused", True))
        reconcile_trading_schedules(now=utc(day=23))
        self.assertEqual(self.state(), ("active", False))

    def test_manual_stop_clears_market_guard_and_cannot_be_undone(self):
        Bot.objects.filter(pk=self.bot.pk).update(status="stopped", scalper_params={"_market_guard": {"was": "active"}})
        set_bot_status(self.bot, "stopped")
        opened = MarketStatus(is_open=True, reason="open", checked_at=utc())
        _apply_market_status(self.bot.pk, opened, utc())
        self.assertEqual(self.state(), ("stopped", False))

    def test_market_close_outside_session_preserves_schedule_ownership(self):
        closed = MarketStatus(is_open=False, reason="closed", checked_at=utc(hour=18))
        _apply_market_status(self.bot.pk, closed, utc(hour=18))
        self.assertEqual(self.state(), ("paused", True))
        reconcile_trading_schedules(now=utc(day=23))
        self.assertEqual(self.state(), ("active", False))

    def test_crypto_without_schedule_stays_active_on_weekends(self):
        self.asset.symbol = "BTCUSDtest"
        self.asset.category = "crypto"
        self.asset.save()
        Bot.objects.filter(pk=self.bot.pk).update(trading_schedule_enabled=False)
        reconcile_trading_schedules(now=utc(day=26, hour=23))
        self.assertEqual(self.state(), ("active", False))

    def test_weekend_and_inactive_asset_do_not_resume(self):
        self.pause_by_schedule()
        reconcile_trading_schedules(now=utc(day=26))
        self.assertEqual(self.state(), ("paused", True))
        self.asset.is_active = False
        self.asset.save()
        reconcile_trading_schedules(now=utc(day=23))
        self.assertEqual(self.state(), ("paused", True))

    def test_disabling_schedule_releases_only_schedule_owned_pause(self):
        self.pause_by_schedule()
        Bot.objects.filter(pk=self.bot.pk).update(trading_schedule_enabled=False)
        reconcile_trading_schedules(now=utc(hour=19))
        self.assertEqual(self.state(), ("active", False))
        set_bot_status(self.bot, "paused")
        reconcile_trading_schedules(now=utc(hour=20))
        self.assertEqual(self.state(), ("paused", False))

    def test_multiple_windows_gap_and_timezone_dst(self):
        self.bot.trading_windows = [
            {"start": "09:00", "end": "10:00", "timezone": "Europe/London", "allowed_days": ["tue"]},
            {"start": "13:00", "end": "14:00", "timezone": "America/New_York", "allowed_days": ["tue"]},
        ]
        self.bot.save(update_fields=["trading_windows"])
        self.assertTrue(is_within_trading_window(self.bot, utc(hour=8, minute=30)))
        self.assertFalse(is_within_trading_window(self.bot, utc(hour=12)))
        self.assertTrue(is_within_trading_window(self.bot, utc(hour=17, minute=30)))
        self.assertFalse(is_within_trading_window(self.bot, utc(day=24, month=11, hour=17, minute=30)))
        self.assertTrue(is_within_trading_window(self.bot, utc(day=24, month=11, hour=18, minute=30)))

    def test_single_and_multi_overnight_windows_use_start_day(self):
        self.bot.allowed_trading_days = ["fri"]
        self.bot.trading_window_start = time(22)
        self.bot.trading_window_end = time(2)
        for windows in ([], [{"start": "22:00", "end": "02:00", "timezone": "UTC", "allowed_days": ["fri"]}]):
            self.bot.trading_windows = windows
            self.assertFalse(is_within_trading_window(self.bot, utc(day=25, hour=1)))
            self.assertTrue(is_within_trading_window(self.bot, utc(day=25, hour=23)))
            self.assertTrue(is_within_trading_window(self.bot, utc(day=26, hour=1)))
            self.assertTrue(is_within_trading_window(self.bot, utc(day=26, hour=2)))
            self.assertFalse(is_within_trading_window(self.bot, utc(day=26, hour=2, minute=1)))

    def test_guard_is_scheduled_every_30_seconds(self):
        from execution.tasks import trading_schedule_guard_task
        entry = settings.CELERY_BEAT_SCHEDULE["trading-schedule-guard-30s"]
        self.assertEqual(entry["task"], trading_schedule_guard_task.name)
        self.assertEqual(entry["schedule"], 30.0)

    def test_schedule_flag_is_read_only_in_api(self):
        self.pause_by_schedule()
        response = self.client.patch(f"/api/bots/{self.bot.pk}/", {"schedule_paused": False}, content_type="application/json")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(response.json()["schedule_paused"])

    def test_bot_api_exposes_cooldown_and_dominant_recent_rejection(self):
        Bot.objects.filter(pk=self.bot.pk).update(
            status="paused", pause_reason="loss_cooldown",
            paused_until=utc(day=23), current_loss_streak=3,
        )
        for reason in ("htf_bias_neutral", "htf_bias_neutral", "low_volume"):
            ScalperRunLog.objects.create(bot=self.bot, summary={"rejection_reason": reason})
        old = ScalperRunLog.objects.create(bot=self.bot, summary={"rejection_reason": "old_reason"})
        from django.utils import timezone
        ScalperRunLog.objects.filter(pk=old.pk).update(created_at=timezone.now() - timedelta(hours=13))
        response = self.client.get(f"/api/bots/{self.bot.pk}/")
        self.assertEqual(response.status_code, 200, response.content)
        payload = response.json()
        self.assertEqual(payload["pause_reason"], "loss_cooldown")
        self.assertEqual(payload["current_loss_streak"], 3)
        self.assertIsNotNone(payload["paused_until"])
        self.assertEqual(payload["diagnostic_12h"], {
            "scans": 3, "dominant_rejection_reason": "htf_bias_neutral",
            "dominant_rejection_count": 2,
        })
        response = self.client.patch(
            f"/api/bots/{self.bot.pk}/",
            {"pause_reason": "manual", "current_loss_streak": 0, "paused_until": None},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["pause_reason"], "loss_cooldown")
        self.assertEqual(response.json()["current_loss_streak"], 3)

    def test_settings_save_cannot_overwrite_a_concurrent_schedule_pause(self):
        from bots.serializers import BotSerializer

        serializer = BotSerializer(
            self.bot, data={"name": "Renamed bot"}, partial=True,
            context={"request": SimpleNamespace(user=self.user)},
        )
        serializer.is_valid(raise_exception=True)
        reconcile_trading_schedules(now=utc(hour=18))
        serializer.save()
        self.assertEqual(self.state(), ("paused", True))
        self.assertEqual(self.bot.name, "Renamed bot")
