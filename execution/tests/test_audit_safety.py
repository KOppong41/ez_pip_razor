from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone

from bots.models import Bot
from execution.connectors.mt5 import MT5Connector
from execution.models import BrokerPosition, Decision, ExecutionAttempt, Order, Signal
from execution.services.bot_loss_guard import latch_bot_losses
from execution.services.bot_schedule import can_automatically_resume, set_bot_status
from execution.services.exposure import entry_reservations
from execution.services.performance_identity import performance_identity
from execution.tasks import _account_entry_slots, cancel_stale_orders_task, kill_switch_monitor_task
from execution.tests import test_live_risk, test_kill_switch


@override_settings(MAX_ORDER_LOT=Decimal(10))
class DurableExposureTests(TestCase):
    setUp = test_live_risk.LiveRiskTest.setUp
    _bot = test_live_risk.LiveRiskTest._bot
    _order = test_live_risk.LiveRiskTest._order
    _position = test_live_risk.LiveRiskTest._position
    _enforce = test_live_risk.LiveRiskTest._enforce
    _assert_rejected = test_live_risk.LiveRiskTest._assert_rejected

    def partial(self, *, represented=".4", reserved_at=None):
        bot = self._bot("partial")
        order = self._order(bot, qty=Decimal(1), filled_qty=Decimal(".4"), remaining_qty=Decimal(".6"),
                             status="part_filled", broker_position_ticket=712, risk_reserved_at=reserved_at)
        position = self._position(bot, 712, volume=represented)
        position.originating_order = order
        position.save()
        self.policy.max_aggregate_open_lots = Decimal(1)
        self.policy.save()
        return order, position

    def candidate(self, qty=".1"):
        return self._order(self._bot("candidate", position_sizing_mode="fixed", default_qty=Decimal(qty),
                                    risk_max_concurrent_positions=min(5, self.policy.max_total_open_positions or 5)), qty=Decimal(qty))

    def test_partial_remainder_reserves_capacity_without_timestamp(self):
        self.partial()
        rejection = self._assert_rejected("ACCOUNT_MAX_AGGREGATE_LOTS", self.candidate())
        self.assertEqual(Decimal(rejection.context["aggregate_lots"]), Decimal(1))

    def test_unsynchronized_partial_fill_cannot_open_an_exposure_gap(self):
        self.partial(represented=".2")
        rejection = self._assert_rejected("ACCOUNT_MAX_AGGREGATE_LOTS", self.candidate())
        self.assertEqual(Decimal(rejection.context["aggregate_lots"]), Decimal(1))

    def test_partial_position_and_remainder_use_one_position_slot(self):
        self.partial()
        self.policy.max_total_open_positions = 2
        self.policy.max_aggregate_open_lots = Decimal(2)
        self.policy.save()
        self.assertEqual(_account_entry_slots(self.account), 1)
        self.assertEqual(self._enforce(self.candidate()).volume, Decimal(".1"))

    def test_old_ack_and_ambiguous_submissions_never_expire_out_of_limits(self):
        bot = self._bot("pending")
        order = self._order(bot, status="ack", qty=Decimal(1), submitted_at=timezone.now()-timedelta(days=1))
        self.policy.max_aggregate_open_lots = Decimal(1)
        self.policy.save()
        self._assert_rejected("ACCOUNT_MAX_AGGREGATE_LOTS", self.candidate())
        order.status = "error"  # Legacy local error does not prove broker rejection.
        order.save()
        ExecutionAttempt.objects.create(order=order, attempt_no=1, status="ambiguous", requested_qty=1)
        self.assertEqual(sum(r.lots for r in entry_reservations(self.account)), Decimal(1))

    def test_filled_but_unsynchronized_volume_stays_reserved(self):
        order = self._order(self._bot("filled"), qty=Decimal(".7"), filled_qty=Decimal(".7"),
                            status="filled", risk_reserved_at=timezone.now()-timedelta(hours=2))
        self.policy.max_aggregate_open_lots = Decimal(1)
        self.policy.save()
        self._assert_rejected("ACCOUNT_MAX_AGGREGATE_LOTS", self.candidate(".4"))
        position = self._position(order.bot, 713, volume=".7")
        position.originating_order = order
        position.status = "closed"
        position.save()
        self.assertEqual(entry_reservations(self.account), [])

    @patch("execution.connectors.mt5.mt5")
    def test_terminal_history_releases_only_confirmed_remainder(self, api):
        order, _ = self.partial(reserved_at=timezone.now())
        order.broker_order_ticket = 900
        order.save()
        ExecutionAttempt.objects.create(order=order, attempt_no=1, status="ambiguous", requested_qty=1)
        api.ORDER_STATE_CANCELED, api.ORDER_STATE_REJECTED, api.ORDER_STATE_EXPIRED = 2, 5, 6
        connector = MT5Connector()
        for rows in (None, (), (SimpleNamespace(ticket=901, state=2, volume_initial=1, volume_current=.6),),
                     (SimpleNamespace(ticket=900, state=2, volume_initial=1, volume_current=.2),)):
            api.history_orders_get.return_value = rows
            self.assertFalse(connector._reconcile_terminal_remainder(order))
            self.assertEqual(entry_reservations(self.account)[0].lots, Decimal(".6"))
        api.history_orders_get.return_value = (SimpleNamespace(ticket=900, state=2, volume_initial=1, volume_current=.6),)
        self.assertTrue(connector._reconcile_terminal_remainder(order))
        order.refresh_from_db()
        self.assertEqual((order.status, order.remaining_qty, order.filled_qty), ("canceled", Decimal(0), Decimal(".4")))
        self.assertEqual(entry_reservations(self.account), [])
        self.assertEqual(self._enforce(self.candidate(".5")).volume, Decimal(".5"))
        api.order_send.assert_not_called()

    @patch("execution.tasks.MT5Connector")
    def test_stale_worker_keeps_unknown_submitted_and_new_ambiguous_orders(self, connector):
        bot = self._bot("stale")
        submitted = self._order(bot, status="ack", submitted_at=timezone.now()-timedelta(hours=1), risk_reserved_at=timezone.now()-timedelta(hours=1))
        ambiguous = self._order(bot, suffix="ambiguous")
        ExecutionAttempt.objects.create(order=ambiguous, attempt_no=1, status="ambiguous", requested_qty=ambiguous.qty)
        local = self._order(bot, suffix="local")
        Order.objects.filter(pk__in=[submitted.pk, ambiguous.pk, local.pk]).update(updated_at=timezone.now()-timedelta(hours=1))
        connector.return_value.reconcile_order.return_value = False
        result = cancel_stale_orders_task.run(max_age_seconds=1)
        self.assertCountEqual(result["unresolved"], [submitted.pk, ambiguous.pk])
        self.assertEqual(result["canceled_local"], [local.pk])
        submitted.refresh_from_db()
        ambiguous.refresh_from_db()
        self.assertEqual(submitted.status, "ack")
        self.assertEqual(ambiguous.status, "new")
        self.assertIsNotNone(submitted.risk_reserved_at)


class BotLossGuardTests(TestCase):
    setUp = test_kill_switch.KillSwitchRiskDayTests.setUp

    def position(self, ticket=800, *, bot=None, ownership="ez_trade", stored_profit=0):
        return BrokerPosition.objects.create(broker_account=self.account, bot=bot or self.bot,
            broker_position_ticket=ticket, ownership=ownership, symbol="KILLRISKUSD", side="buy",
            volume=".1", open_price=100, profit=stored_profit)

    def raw(self, ticket=800, profit=-500, swap=0):
        return SimpleNamespace(ticket=ticket, profit=profit, swap=swap, volume=Decimal(".1"))

    def guard(self, *rows):
        return latch_bot_losses(self.account, SimpleNamespace(balance=10000), rows)

    def test_boundary_triggers_only_owned_bot_and_cancels_schedule_resume(self):
        self.position()
        self.position(801, ownership="manual", stored_profit=-9999)
        self.bot.status, self.bot.schedule_paused = "paused", True
        self.bot.save()
        result = self.guard(self.raw(), self.raw(801, -9999))
        self.assertEqual([p.broker_position_ticket for p in result[0][1]], [800])
        self.bot.refresh_from_db()
        self.assertEqual(self.bot.status, "stopped")
        self.assertFalse(self.bot.schedule_paused)
        self.assertIsNotNone(self.bot.kill_switch_triggered_at)
        self.assertFalse(can_automatically_resume(self.bot, timezone.now()))
        self.policy.refresh_from_db()
        self.assertTrue(self.policy.entries_enabled)
        self.assertFalse(self.policy.emergency_stop)

    def test_disabled_switch_and_below_threshold_do_not_trigger(self):
        self.position()
        self.assertEqual(self.guard(self.raw(profit=-499)), [])
        self.bot.kill_switch_enabled = False
        self.bot.save()
        self.assertEqual(self.guard(self.raw(profit=-9999)), [])

    def test_allocation_and_swap_contribute_to_bot_limit(self):
        self.position()
        self.bot.allocation_amount = Decimal(1000)
        self.bot.save()
        self.assertTrue(self.guard(self.raw(profit=-49, swap=-1)))

    def test_only_fresh_broker_pnl_can_trigger(self):
        self.position(stored_profit=-9999)
        self.assertEqual(self.guard(self.raw(profit=0)), [])
        self.assertEqual(self.guard(), [])
        with self.assertRaises(ValueError):
            latch_bot_losses(self.account, SimpleNamespace(balance=10000), None)

    def test_latch_retries_after_price_recovery_until_explicit_start(self):
        self.position()
        self.assertTrue(self.guard(self.raw()))
        self.assertTrue(self.guard(self.raw(profit=10)))
        self.bot.trading_schedule_enabled = False
        self.bot.save(update_fields=["trading_schedule_enabled"])
        set_bot_status(self.bot, "active")
        self.assertIsNone(self.bot.kill_switch_triggered_at)
        self.assertEqual(self.guard(self.raw(profit=10)), [])

    def test_monitor_cancels_only_triggered_bot_entries_and_flattens_without_account_opt_in(self):
        self.position()
        other = Bot.objects.create(name="Unaffected", broker_account=self.account, asset=self.bot.asset, status="active")
        own = Order.objects.create(bot=self.bot, broker_account=self.account, client_order_id="kill-own", symbol="KILLRISKUSD", side="buy", qty=".1")
        unrelated = Order.objects.create(bot=other, broker_account=self.account, client_order_id="kill-other", symbol="KILLRISKUSD", side="buy", qty=".1")
        self.policy.max_daily_loss_pct = self.policy.max_account_drawdown_pct = 0
        self.policy.save()
        self.account_info.balance = self.account_info.equity = 10000
        with (patch("execution.tasks.MT5Connector") as connector,
              patch("execution.services.orchestrator.create_close_order_for_position", return_value=(object(), True)) as close,
              patch("execution.tasks._queue_or_dispatch_order") as dispatch):
            connector.return_value.account_info_for_account.return_value = self.account_info
            connector.return_value.history_deals_for_account.return_value = ()
            connector.return_value.positions_for_account.return_value = (self.raw(),)
            result = kill_switch_monitor_task.run()
        self.assertEqual(result["closed_owned_tickets"], [800])
        self.assertEqual(result["triggered"], [])
        own.refresh_from_db()
        unrelated.refresh_from_db()
        other.refresh_from_db()
        self.assertEqual((own.status, unrelated.status, other.status), ("canceled", "new", "active"))
        close.assert_called_once()
        self.assertTrue(dispatch.call_args.kwargs["emergency"])


class PerformanceControlIdentityTests(TestCase):
    setUp = test_live_risk.LiveRiskTest.setUp
    _bot = test_live_risk.LiveRiskTest._bot
    _order = test_live_risk.LiveRiskTest._order
    _enforce = test_live_risk.LiveRiskTest._enforce

    def identity(self, bot):
        return performance_identity(bot, self.asset.symbol, "5m")["config_fingerprint"]

    def test_account_limits_change_identity_but_equity_and_latch_do_not(self):
        bot = self._bot()
        original = self.identity(bot)
        self.assertIsNotNone(original)
        self.policy.max_aggregate_open_lots = Decimal(".9")
        self.policy.save()
        changed = self.identity(bot)
        self.assertNotEqual(original, changed)
        self.policy.equity_high_water = 20000
        self.policy.save()
        bot.kill_switch_triggered_at = timezone.now()
        self.assertEqual(self.identity(bot), changed)

    def test_runtime_execution_changes_are_attributed(self):
        bot = self._bot()
        before = self.identity(bot)
        with override_settings(MAX_ORDER_LOT=Decimal(".123")):
            self.assertNotEqual(self.identity(bot), before)
        with override_settings(DECISION_FLIP_COOLDOWN_MIN=31):
            self.assertNotEqual(self.identity(bot), before)

    def test_disabled_and_dead_controls_do_not_fragment_performance(self):
        bot = self._bot(kill_switch_enabled=False)
        before = self.identity(bot)
        bot.kill_switch_max_unrealized_pct = Decimal(20)
        self.assertEqual(self.identity(bot), before)
        with override_settings(EARLY_EXIT_MAX_UNREALIZED_PCT=Decimal(".75")):
            self.assertEqual(self.identity(bot), before)
        bot.kill_switch_enabled = True
        self.assertNotEqual(self.identity(bot), before)

    @override_settings(MAX_ORDER_LOT=Decimal(10))
    def test_final_admission_captures_policy_changes_while_entry_was_queued(self):
        bot = self._bot(position_sizing_mode="fixed", default_qty=Decimal(".1"))
        signal = Signal.objects.create(bot=bot, symbol=self.asset.symbol, source="engine_v1", direction="buy", timeframe="5m", dedupe_key="audit-identity")
        decision = Decision.objects.create(bot=bot, signal=signal, action="open", score=1, params={})
        order = self._order(bot, decision=decision)
        original = order.performance_context["config_fingerprint"]
        self.policy.max_aggregate_open_lots = Decimal(".8")
        self.policy.save()
        self._enforce(order)
        order.refresh_from_db()
        self.assertEqual(order.performance_context["decision_config_fingerprint"], original)
        self.assertEqual(order.performance_context["config_fingerprint"], self.identity(bot))
        self.assertEqual(order.performance_context["config_snapshot"]["account"]["risk_policy"]["max_aggregate_open_lots"], "0.8")
