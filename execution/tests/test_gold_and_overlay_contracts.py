from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase, override_settings

from bots.models import Asset
from execution.models import Decision, Signal
from execution.services.entry_contract import gold_stop_reason, target_at_entry
from execution.services.higher_timeframe_context import analyze_context
from execution.services.live_risk import RiskRejected, enforce_pretrade_risk
from execution.services.opposite_scalp import validate_primary
from execution.services.overlay_analytics import overlay_report
from execution.services.position_management import plan_scalper_position
from execution.services.strategies.momentum_ignition import run_momentum_ignition
from execution.services.strategies.price_action_pinbar import run_price_action_pinbar
from execution.services.trading_type import is_within_trading_window
from execution.tests import test_live_risk
from execution.tests.test_strategy_price_normalization import _scaled_pinbar_candles


class GoldEntryContracts(SimpleTestCase):
    def test_pin_requires_next_completed_confirmation_and_keeps_wick_stop(self):
        candles = _scaled_pinbar_candles(Decimal("2300"))
        self.assertEqual(run_price_action_pinbar("XAUUSD", candles[:-1]).action, "skip")
        result = run_price_action_pinbar("XAUUSD", candles)
        self.assertEqual(result.action, "open")
        self.assertGreaterEqual(result.entry_price, result.entry_trigger)
        self.assertLess(result.sl, candles[-2]["low"])
        self.assertEqual((result.tp - result.entry_price) / (result.entry_price - result.sl), result.target_rr)
        candles[-1]["close"] = candles[-2]["close"]
        self.assertEqual(run_price_action_pinbar("XAUUSD", candles).reason, "pinbar_confirmation_failed")

    def test_momentum_rr_uses_pullback_for_both_directions(self):
        for sign in (1, -1):
            prices = [Decimal("2300") + sign * Decimal(i) for i in range(6)]
            prices.append(prices[-1] - sign * Decimal("1"))
            candles = [{"open": price, "high": price + Decimal("0.2"), "low": price - Decimal("0.2"),
                        "close": price, "tick_volume": 200} for price in prices]
            result = run_momentum_ignition(candles)
            self.assertEqual(result.action, "open", result)
            self.assertEqual(result.entry_price, prices[-1])
            self.assertEqual(abs(result.tp - result.entry_price) / abs(result.entry_price - result.sl), Decimal("2.2"))

    def test_submission_target_uses_current_quote_and_rejects_lost_trigger(self):
        self.assertEqual(target_at_entry("buy", Decimal("2305"), Decimal("2300"), "2"), Decimal("2315"))
        with self.assertRaisesRegex(ValueError, "entry_trigger_lost"):
            target_at_entry("buy", Decimal("2301"), Decimal("2300"), "2", "2302")

    def test_gold_stop_envelope_rejects_instead_of_moving_structure(self):
        config = SimpleNamespace(key="XAUUSD", sl_points_min=Decimal("0.1"), sl_points_max=Decimal("0.3"), sl_points_unit="percent")
        for stop, reason in (("2295.4", None), ("2288.5", "scalper:sl_above_max"), ("2299", "scalper:sl_below_min")):
            self.assertEqual(gold_stop_reason(config, Decimal("2300"), Decimal(stop), point=Decimal("0.01")), reason)

    def test_context_uses_h1_regime_and_m15_structure_and_blocks_disagreement(self):
        calls = []
        frames = {"15m": {"bias": "buy", "structure": "higher_high"}, "1h": {"bias": "buy", "structure": "higher_high"}}
        def fetch(frame):
            calls.append(frame)
            return frames[frame]
        bias, details, reason = analyze_context(["M15", "H1"], fetch, lambda value: value)
        self.assertEqual(calls, ["15m", "1h"])
        self.assertEqual((bias, reason, details["dominant_timeframe"]), ("buy", None, "1h"))
        frames["1h"]["bias"] = "sell"
        self.assertEqual(analyze_context(["M15", "H1"], fetch, lambda value: value)[2], "htf_context_conflict")

    def test_multiple_windows_use_local_timezones_and_overnight_start_day(self):
        bot = SimpleNamespace(trading_schedule_enabled=True, trading_windows=[
            {"timezone": "Europe/London", "start": "08:00", "end": "10:00", "allowed_days": ["mon"]},
            {"timezone": "America/New_York", "start": "08:00", "end": "10:00", "allowed_days": ["mon"]},
            {"timezone": "UTC", "start": "22:00", "end": "02:00", "allowed_days": ["mon"]},
        ])
        for hour, allowed in ((7, True), (10, False), (13, True), (17, False)):
            self.assertEqual(is_within_trading_window(bot, datetime(2025, 7, 7, hour, tzinfo=timezone.utc)), allowed)
        self.assertTrue(is_within_trading_window(bot, datetime(2025, 7, 8, 1, tzinfo=timezone.utc)))
        self.assertFalse(is_within_trading_window(bot, datetime(2025, 7, 7, 1, tzinfo=timezone.utc)))

    def test_overlay_analytics_groups_partial_fills_and_attributes_drawdown(self):
        rows = [dict(position_id=1, is_opposite_scalp=False, pnl=10, costs=0, exit_time=1),
                dict(position_id=2, is_opposite_scalp=True, pnl=3, costs=1, exit_time=2),
                dict(position_id=2, is_opposite_scalp=True, pnl=-5, costs=1, exit_time=3)]
        report = overlay_report(rows)
        self.assertEqual((report["trades"], report["losses"], report["net_pnl"], report["costs"]), (1, 1, -2, 2))
        self.assertEqual(report["expectancy"], -2)
        self.assertEqual(report["realized_drawdown_delta"], 5)


@override_settings(MAX_ORDER_LOT=Decimal("10"), DECISION_SCALP_QTY_MULTIPLIER=Decimal("0.3"))
class OverlayRiskContracts(TestCase):
    setUp = test_live_risk.LiveRiskTest.setUp
    _bot = test_live_risk.LiveRiskTest._bot
    _order = test_live_risk.LiveRiskTest._order
    _position = test_live_risk.LiveRiskTest._position
    _risk_day = test_live_risk.LiveRiskTest._risk_day

    def scenario(self):
        self._risk_day(Decimal("10000"))
        from django.contrib.auth import get_user_model
        owner = get_user_model().objects.create_user(username="overlay-owner")
        self.account.owner = owner
        self.account.save()
        bot = self._bot(owner=owner, allow_opposite_scalp=True, risk_per_trade_pct=Decimal("0.30"))
        parent_order = self._order(bot, sl=Decimal("99"), status="filled", filled_qty=Decimal("0.1"))
        primary = self._position(bot, 712)
        primary.originating_order = parent_order
        primary.sl = Decimal("100")
        primary.opened_at = datetime.now(timezone.utc)
        primary.save()
        primary.refresh_from_db()
        signal = Signal.objects.create(bot=bot, symbol=self.asset.symbol, source="scalper_engine", direction="sell", timeframe="5m", dedupe_key="overlay")
        decision = Decision.objects.create(bot=bot, signal=signal, action="open", score=1, params={
            "is_opposite_scalp": True, "primary_position_id": primary.pk, "risk_pct": "0.30",
            "scalper": {"time_in_trade_limit_min": 10, "hard_time_limit": True},
        })
        order = self._order(bot, side="sell", sl=Decimal("101"), tp=Decimal("98.8"), decision=decision)
        self.account_info.margin_mode = 2
        raw = SimpleNamespace(ticket=712, volume=Decimal("0.1"), sl=Decimal("100"))
        return bot, primary, order, raw

    def enforce(self, order, raw):
        return enforce_pretrade_risk(order, self.connector, self.tick, self.symbol_info, self.account_info, broker_positions=[raw])

    def test_multiplier_caps_monetary_risk_and_one_child_reserves_parent(self):
        bot, primary, order, raw = self.scenario()
        result = self.enforce(order, raw)
        self.assertEqual(result.effective_risk_pct, Decimal("0.09"))
        self.assertEqual(result.risk_amount, Decimal("9"))
        self.assertEqual(result.volume, Decimal("0.09"))
        second = self._order(bot, side="sell", sl=Decimal("101"), tp=Decimal("98.8"), decision=order.decision)
        with self.assertRaises(RiskRejected) as error:
            self.enforce(second, raw)
        self.assertIn("opposite_scalp_already_used", str(error.exception))

    def test_disabled_base_risk_cannot_be_replaced_by_bot_default(self):
        from execution.services.brokers import BrokerSymbolConstraints
        from execution.services.decision import _build_scalp_params
        _, primary, order, _ = self.scenario()
        specs = BrokerSymbolConstraints(point=Decimal("0.01"), digits=2)
        with patch("execution.services.brokers.get_broker_symbol_constraints", return_value=specs), patch("execution.services.decision.get_price", return_value=Decimal("100")):
            with self.assertRaisesRegex(ValueError, "invalid_base_risk"):
                _build_scalp_params(order.decision.signal, primary=primary, base_risk_pct=0)

    def test_netting_unknown_modes_and_unprotected_primary_fail_closed(self):
        bot, primary, order, raw = self.scenario()
        for mode in (0, 1, None):
            self.account_info.margin_mode = mode
            with self.assertRaises(RiskRejected) as error:
                self.enforce(order, raw)
            self.assertIn("requires_hedging", str(error.exception))
        self.account_info.margin_mode = 2
        raw.sl = Decimal("99")
        self.assertEqual(validate_primary(bot, "sell", primary, self.account_info, self.tick, [raw], exclude_order=order.id), "opposite_scalp_primary_unprotected")
        self.tick.bid = Decimal("100.5")
        self.assertIsNone(validate_primary(bot, "sell", primary, self.account_info, self.tick, [raw], exclude_order=order.id))

    def test_fixed_sizing_is_reduced_once_and_filled_child_cannot_be_repeated(self):
        bot, primary, order, raw = self.scenario()
        bot.position_sizing_mode = "fixed"
        bot.save()
        order.qty = Decimal("0.1")
        order.save()
        self.assertEqual(self.enforce(order, raw).volume, Decimal("0.03"))
        order.status, order.filled_qty = "filled", Decimal("0.03")
        order.save()
        self.assertEqual(validate_primary(bot, "sell", primary, self.account_info, self.tick, [raw]), "opposite_scalp_already_used")

    def test_child_is_closed_when_primary_has_closed(self):
        from execution import tasks
        bot, primary, order, _ = self.scenario()
        child = self._position(bot, 713)
        child.side, child.originating_order = "sell", order
        child.save()
        primary.status = "closed"
        primary.save()
        with patch("execution.tasks.MT5Connector") as connector, patch("execution.tasks._queue_or_dispatch_order") as dispatch:
            result = tasks.trail_positions_task.run()
        self.assertIn(child.id, result["closed"])
        self.assertEqual(dispatch.call_args.args[0].intent, "exit")
        connector.return_value.tick_for_account.assert_not_called()

    def test_balanced_primary_positions_do_not_bypass_overlay_classification(self):
        from execution.services.decision import detect_position_conflict
        bot, _, _, _ = self.scenario()
        other = self._position(bot, 714)
        other.side = "sell"
        other.save()
        conflict = detect_position_conflict(bot, self.asset.symbol, "buy", 1.0)
        self.assertEqual(conflict.reason, "opposite_scalp_primary_ambiguous")

    def test_analytics_omits_missing_exit_pnl_instead_of_inventing_breakeven(self):
        from execution.models import Execution
        from execution.services.overlay_analytics import account_overlay_report
        bot, _, order, _ = self.scenario()
        child = self._position(bot, 715)
        child.side, child.status, child.originating_order = "sell", "closed", order
        child.save()
        Execution.objects.create(order=order, qty=Decimal("0.1"), price=Decimal("100"), broker_position_ticket=715)
        report = account_overlay_report(self.account)
        self.assertEqual(report["trades"], 0)
        self.assertEqual(report["positions_missing_realized_fills"], 1)
        closing = self._order(bot, intent="exit", side="buy", broker_position_ticket=715)
        Execution.objects.create(order=closing, qty=Decimal("0.1"), price=Decimal("99"),
            broker_position_ticket=715, profit=Decimal("2"), commission=Decimal("-0.1"))
        report = account_overlay_report(self.account)
        self.assertEqual(report["trades"], 1)
        self.assertEqual(report["net_pnl"], Decimal("1.9"))
        self.assertEqual(report["costs"], Decimal("0.1"))

    def test_overlay_time_limit_is_hard_even_when_profitable(self):
        _, _, order, _ = self.scenario()
        order.sl = Decimal("101")
        position = SimpleNamespace(is_manageable=True, originating_order=order, open_price=Decimal("100"),
            side="sell", sl=Decimal("101"), opened_at=datetime.now(timezone.utc) - timedelta(minutes=11))
        plan = plan_scalper_position(position, Decimal("99"))
        self.assertTrue(plan.close)
        self.assertEqual(plan.reason, "opposite_scalp_time_limit")
