from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
from importlib import import_module
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase, override_settings
from django.apps import apps
from django.db import connection

from bots.models import Asset
from bots.services import apply_recommendations_to_bot
from core.asset_trading_presets import ASSET_TRADING_PRESETS
from execution.models import Decision, Signal
from execution.services.ai_strategy_selector import select_ai_strategies
from execution.services.decision import make_decision_from_signal
from execution.services.engine_types import EngineDecision
from execution.services.entry_contract import structural_stop_reason
from execution.services.live_risk import RiskRejected
from execution.services.scalper_config import build_scalper_config
from execution.services.strategies.breakout_retest import BreakoutRetestConfig, run_breakout_retest
from execution.services.strategies.confirmation import confirm_pullback
from execution.services.strategies.momentum_ignition import MomentumIgnitionConfig, run_momentum_ignition
from execution.services.strategies.trend_pullback import run_trend_pullback
from execution.services.strategy_registry import build_strategy_config_for_bot
from execution.tests import test_live_risk, test_scalper_pipeline_contracts, test_trend_pullback


def candle(opening, high, low, close, volume=20):
    return dict(zip(("open", "high", "low", "close"), map(Decimal, (opening, high, low, close))), tick_volume=volume)


def mirror(bars):
    return [{**bar, "open": 200 - bar["open"], "close": 200 - bar["close"],
             "high": 200 - bar["low"], "low": 200 - bar["high"]} for bar in bars]


class BtcDetectorTests(SimpleTestCase):
    def test_quality_flags_require_explicit_snapshot_adoption(self):
        for symbol in ("BTCUSDm", "XAUUSDm"):
            bot = SimpleNamespace(asset=SimpleNamespace(symbol=symbol), asset_preset_version_applied=4,
                                  asset_strategy_overrides_applied={})
            self.assertFalse(build_strategy_config_for_bot("momentum_ignition", bot).require_confirmation)
            self.assertFalse(build_strategy_config_for_bot("trend_pullback", bot).require_confirmation)
            self.assertFalse(build_strategy_config_for_bot("breakout_retest", bot).require_retest_rejection)
        bot.asset_strategy_overrides_applied = ASSET_TRADING_PRESETS["BTCUSDm"]["strategy_overrides"]
        bot.asset_preset_version_applied = 5
        self.assertTrue(build_strategy_config_for_bot("momentum_ignition", bot).require_confirmation)
        self.assertTrue(build_strategy_config_for_bot("trend_pullback", bot).require_confirmation)
        self.assertTrue(build_strategy_config_for_bot("breakout_retest", bot).require_retest_rejection)

    def test_momentum_waits_for_confirmation_and_volume_is_feed_scale_invariant(self):
        bars = [candle("100", "101", "99", "100") for _ in range(20)] + [
            candle("100", "100.4", "99.9", "100.3"), candle("100.3", "100.8", "100.2", "100.7"),
            candle("100.7", "101.2", "100.6", "101", 30), candle("101", "101.1", "100.7", "100.9"),
        ]
        config = MomentumIgnitionConfig(impulse_lookback=3, min_relative_volume=Decimal(1), require_confirmation=True)
        for direction, transform in (("buy", lambda x: x), ("sell", mirror)):
            setup_bars = transform(bars)
            setup = run_momentum_ignition(setup_bars, replace(config, require_confirmation=False))
            self.assertEqual(setup.action, "open", setup)
            self.assertEqual(run_momentum_ignition(setup_bars, config).action, "skip")
            confirmed_bars = transform(bars + [candle("100.9", "101.5", "100.8", "101.3", 1)])
            result = run_momentum_ignition(confirmed_bars, config)
            self.assertEqual((result.action, result.direction, result.sl), ("open", direction, setup.sl))
            self.assertEqual(result.score, setup.score)
            self.assertEqual(abs(result.tp - result.entry_price) / abs(result.entry_price - result.sl), result.target_rr)
            for scale in (1, 100):
                scaled = deepcopy(confirmed_bars)
                for bar in scaled:
                    bar["tick_volume"] *= scale
                other = run_momentum_ignition(scaled, config)
                self.assertEqual((other.action, other.score), (result.action, result.score))
                scaled[-3]["tick_volume"] = 10 * scale
                self.assertEqual(run_momentum_ignition(scaled, config).reason, "momentum_ignition_low_volume")

    def test_confirmation_rejects_stop_breach_even_if_candle_recovers(self):
        for side in ("buy", "sell"):
            buy = side == "buy"
            setup = EngineDecision(action="open", strategy="trend_pullback", direction=side,
                                   entry_price=Decimal(100), sl=Decimal(98 if buy else 102), target_rr=Decimal(2))
            setup_bar = candle("100", "101", "99", "100")
            confirmed = candle("100", "103", "99", "102") if buy else candle("100", "101", "97", "98")
            self.assertEqual(confirm_pullback(setup, setup_bar, confirmed).action, "open")
            confirmed["low" if buy else "high"] = setup.sl
            self.assertEqual(confirm_pullback(setup, setup_bar, confirmed).reason, "trend_pullback_setup_invalidated")

    def test_confirmation_requires_directional_close_beyond_setup_extreme(self):
        setup = EngineDecision(action="open", strategy="momentum_ignition", direction="buy",
                               entry_price=Decimal(100), sl=Decimal(98), target_rr=Decimal(2))
        for bar in (candle("100", "102", "99", "101"), candle("103", "104", "99", "102")):
            self.assertEqual(confirm_pullback(setup, candle("100", "101", "99", "100"), bar).reason,
                             "momentum_ignition_confirmation_failed")

    def test_trend_runner_confirms_without_recalculating_setup_indicators_on_future_bar(self):
        config = test_trend_pullback.TrendPullbackFractalTests()._config()
        config.require_confirmation = True
        markers = [{"up": False, "down": i == 3} for i in range(6)]
        emas = list(map(Decimal, ("9.5", "9.6", "9.7", "9.8", "9.9", "10.0")))
        setup_bars = test_trend_pullback._candles()
        with (patch("execution.services.strategies.trend_pullback._atr", return_value=Decimal(1)),
              patch("execution.services.strategies.trend_pullback._ema", return_value=emas),
              patch("execution.services.strategies.trend_pullback.fractals", return_value=markers) as fractals):
            setup = run_trend_pullback(setup_bars, replace(config, require_confirmation=False))
            result = run_trend_pullback(setup_bars + [candle("10.1", "10.6", "10", "10.5")], config)
        self.assertEqual((result.action, result.sl, result.score), ("open", setup.sl, setup.score))
        self.assertEqual(result.entry_trigger, Decimal("10.3"))
        self.assertEqual(fractals.call_args.args[0], setup_bars)

    def test_breakout_requires_directional_rejection_and_protects_retest_wick(self):
        bars = [candle("100", "101", "99", "100") for _ in range(40)] + [
            candle("101.03", "101.4", "101", "101.2", 30), candle("101.05", "101.3", "100.95", "101.15"),
        ]
        config = BreakoutRetestConfig(min_relative_volume=Decimal(1), require_retest_rejection=True)
        for side, transform in (("buy", lambda x: x), ("sell", mirror)):
            rows = transform(bars)
            result = run_breakout_retest(rows, config)
            self.assertEqual((result.action, result.direction), ("open", side), result)
            self.assertEqual(result.sl, rows[-1]["low" if side == "buy" else "high"])
            self.assertEqual(result.entry_trigger, Decimal(101 if side == "buy" else 99))
            for index in (-2, -1):
                red = deepcopy(bars)
                red[index]["open"] = Decimal("101.3" if index == -2 else "101.2")
                self.assertEqual(run_breakout_retest(transform(red), config).reason, "breakout_retest_unconfirmed_rejection")

    def test_stop_envelope_accepts_boundaries_and_rejects_invalid_prices(self):
        config = SimpleNamespace(key="BTCUSD", sl_points_min=Decimal(".35"), sl_points_max=Decimal(".9"), sl_points_unit="percent")
        for stop, expected in (("99650", None), ("99100", None), ("99900", "scalper:sl_below_min"),
                               ("99000", "scalper:sl_above_max"), ("NaN", "scalper:invalid_sl")):
            self.assertEqual(structural_stop_reason(config, Decimal(100000), Decimal(stop), point=Decimal(".01")), expected)


class BtcSelectorTests(SimpleTestCase):
    pool = ["breakout_retest", "momentum_ignition", "trend_pullback"]

    def select(self, available=None, **context):
        return select_ai_strategies(engine_mode="scalper", available=self.pool if available is None else available,
                                    symbol="BTCUSDm", context={"last_close": 86000, **context})

    def test_quiet_market_does_not_force_momentum_from_symbol_or_session(self):
        self.assertEqual(self.select(htf_bias="buy", session="london", regime={"structure": "range"}), ["trend_pullback"])
        self.assertEqual(self.select(available=["momentum_ignition"]), [])

    def test_directional_structure_or_expansion_enables_momentum(self):
        for bias, regime in (("buy", {"structure": "higher_high"}), ("sell", {"ema_slope_pct": "-.0002"}),
                             ("buy", {"atr_ratio": "1.25"})):
            self.assertEqual(self.select(htf_bias=bias, regime=regime), ["momentum_ignition", "breakout_retest", "trend_pullback"])
        self.assertEqual(self.select(htf_bias="buy", regime={"structure": "lower_low", "ema_slope_pct": "-.0002"}), ["trend_pullback"])

    def test_spread_filter_uses_price_allowance_and_never_restores_filtered_strategies(self):
        context = {"htf_bias": "buy", "regime": {"atr_ratio": "1.5"}, "allowed_spread_price": 10}
        self.assertEqual(self.select(spread_price=8, **context), ["trend_pullback"])
        self.assertEqual(self.select(available=["momentum_ignition", "breakout_retest"], spread_price=8, **context), [])
        self.assertEqual(self.select(spread_price=1, spread_points=1000, **context), ["momentum_ignition", "breakout_retest", "trend_pullback"])


class BtcDecisionTests(TestCase):
    setUp = test_scalper_pipeline_contracts.ScalperPipelineContracts.setUp
    signal = test_scalper_pipeline_contracts.ScalperPipelineContracts.signal

    def test_detector_stops_are_enforced_before_order_creation_without_moving_them(self):
        self.asset = Asset.objects.get(symbol="BTCUSDm")
        self.bot.asset = self.asset
        apply_recommendations_to_bot(self.bot, save=False)
        self.bot.trade_interval_minutes = 0
        self.bot.scalper_params = {"rollover_blackout": []}
        self.bot.save()
        for stop, expected in (("85950", "scalper:sl_below_min"), ("85000", "scalper:sl_above_max"), ("85580", "detector_setup")):
            signal = self.signal(payload={"sl": stop, "tp": "87000", "entry": "86010", "close": "86010", "score": .9,
                                          "point": ".01", "digits": 2, "target_rr": "2", "reason": "detector_setup"})
            result = make_decision_from_signal(signal)
            self.assertEqual(result.reason, expected)
            self.assertEqual(result.action, "open" if expected == "detector_setup" else "ignore")
            signal.refresh_from_db()
            self.assertEqual(signal.payload["sl"], stop)

    def test_catalog_upgrade_preserves_custom_values_and_frozen_bot_settings(self):
        asset = Asset.objects.get(symbol="BTCUSDm")
        preset = deepcopy(asset.recommended_config)
        preset["strategy_overrides"] = {"momentum_ignition": {"min_relative_volume": 1.7}}
        asset.recommended_config, asset.recommended_config_version = preset, 4
        asset.save()
        self.bot.asset = asset
        self.bot.risk_per_trade_pct = Decimal(".25")
        self.bot.asset_preset_version_applied = 4
        self.bot.asset_strategy_overrides_applied = {"momentum_ignition": {"min_tick_volume": 90}}
        self.bot.save()
        migrate = import_module("bots.migrations.0055_btc_entry_quality").update_btc_recommendation
        migrate(apps, SimpleNamespace(connection=connection))
        asset.refresh_from_db()
        self.bot.refresh_from_db()
        self.assertEqual(asset.recommended_config["strategy_overrides"]["momentum_ignition"]["min_relative_volume"], 1.7)
        self.assertEqual(self.bot.asset_preset_version_applied, 4)
        self.assertEqual(self.bot.asset_strategy_overrides_applied, {"momentum_ignition": {"min_tick_volume": 90}})
        self.assertEqual(self.bot.risk_per_trade_pct, Decimal(".25"))

    def test_btc_retains_m15_h1_context_and_unrestricted_schedule(self):
        self.bot.asset = Asset.objects.get(symbol="BTCUSDm")
        apply_recommendations_to_bot(self.bot, save=False)
        config = build_scalper_config(self.bot)
        self.assertEqual(config.resolve_symbol("BTCUSDm").context_timeframes, ("15m", "1h"))
        self.assertFalse(self.bot.trading_schedule_enabled)
        self.assertEqual(config.sessions, ())

    def test_task_does_not_restore_strategies_rejected_by_spread_filter(self):
        from django.utils import timezone
        from execution.services.brokers import BrokerSymbolConstraints
        from execution.tasks import trade_scalper_strategies_for_bot

        self.account.broker, self.account.connector = "mt5", "mt5_local"
        self.account.is_verified = True
        self.account.save()
        self.bot.asset = Asset.objects.get(symbol="BTCUSDm")
        apply_recommendations_to_bot(self.bot, save=False)
        self.bot.enabled_strategies = ["momentum_ignition", "breakout_retest"]
        self.bot.max_spread_points = 1000
        self.bot.save()
        bars = [{**candle("86000", "86020", "85980", "86000", 100), "time": timezone.now()} for _ in range(100)]
        constraints = BrokerSymbolConstraints(point=Decimal(".01"), digits=2, min_lot=Decimal(".01"),
                                               max_lot=Decimal(100), lot_step=Decimal(".01"), stops_level_points=Decimal(0))
        with (
            patch("execution.tasks.get_market_status_for_bot", return_value=SimpleNamespace(is_open=True, reason="test")),
            patch("execution.tasks.bot_is_available_for_trading", return_value=True),
            patch("execution.tasks.get_broker_symbol_constraints", return_value=constraints),
            patch("execution.tasks.get_candles_for_account", return_value=bars),
            patch("execution.tasks.MT5Connector") as connector,
            patch("execution.services.higher_timeframe_context.analyze_context", return_value=("buy", {"regime": {"atr_ratio": "1.5"}}, None)),
            patch("execution.tasks.build_strategy_config_for_bot", side_effect=AssertionError("filtered detectors must not run")),
        ):
            connector.return_value.symbol_info_for_account.return_value = SimpleNamespace(visible=True, trade_mode=4)
            connector.return_value.tick_for_account.return_value = SimpleNamespace(bid=86000, ask=86009, last=86000, time=timezone.now().timestamp())
            result = trade_scalper_strategies_for_bot.run(self.bot.id, timeframe="5m")
        self.assertEqual(result["reason"], "no_suitable_strategies")
        self.assertFalse(self.bot.orders.exists())


@override_settings(MAX_ORDER_LOT=Decimal("10"))
class BtcLiveRiskTests(TestCase):
    _bot = test_live_risk.LiveRiskTest._bot
    _order = test_live_risk.LiveRiskTest._order
    _risk_day = test_live_risk.LiveRiskTest._risk_day
    _enforce = test_live_risk.LiveRiskTest._enforce

    def setUp(self):
        test_live_risk.LiveRiskTest.setUp(self)
        self.asset = Asset.objects.get(symbol="BTCUSDm")
        self.bot = self._bot()
        apply_recommendations_to_bot(self.bot, save=False)
        self.bot.risk_per_trade_pct = Decimal(".25")
        self.bot.trade_interval_minutes = 0
        self.bot.max_spread_points = 0
        self.bot.save()
        self.tick.bid, self.tick.ask = Decimal(86000), Decimal(86010)
        self.symbol_info.trade_tick_value = self.symbol_info.trade_tick_value_loss = Decimal(".01")
        self.symbol_info.trade_contract_size = Decimal(1)
        self.connector.calc_profit_for_account = lambda account, side, symbol, volume, entry, stop: -abs(entry - stop) * Decimal(str(volume))

    def test_valid_structural_stop_kept_and_volume_rounded_down(self):
        self._risk_day(Decimal(10000))
        order = self._order(self.bot, sl=Decimal(85580), tp=Decimal(87000))
        result = self._enforce(order)
        self.assertEqual(result.volume, Decimal(".05"))
        self.assertEqual(result.effective_risk_pct, Decimal(".25"))
        order.refresh_from_db()
        self.assertEqual(order.sl, Decimal(85580))

    def test_live_quote_rechecks_envelope_after_signal_was_valid(self):
        self._risk_day(Decimal(10000))
        order = self._order(self.bot, sl=Decimal(85705), tp=Decimal(87000))
        self.tick.bid, self.tick.ask = Decimal(85970), Decimal(85980)
        with self.assertRaisesRegex(RiskRejected, "sl_below_min"):
            self._enforce(order)

    def test_minimum_lot_does_not_override_budget_or_tighten_stop(self):
        self.account_info.equity = self.account_info.balance = Decimal("475.94")
        self._risk_day(Decimal("475.94"))
        order = self._order(self.bot, sl=Decimal("85708.965"), tp=Decimal(87000))
        with self.assertRaises(RiskRejected) as error:
            self._enforce(order)
        self.assertEqual(error.exception.code, "BROKER_MIN_VOLUME")
        order.refresh_from_db()
        self.bot.refresh_from_db()
        self.assertEqual(order.sl, Decimal("85708.965"))
        self.assertEqual(self.bot.risk_per_trade_pct, Decimal(".25"))

    def test_submission_rejects_a_lost_confirmation_trigger(self):
        self._risk_day(Decimal(10000))
        signal = Signal.objects.create(bot=self.bot, symbol="BTCUSDm", source="scalper_engine", direction="buy", timeframe="5m", dedupe_key="btc-trigger")
        decision = Decision.objects.create(bot=self.bot, signal=signal, action="open", score=.9,
                                           params={"target_rr": "2", "entry_trigger": "86100"})
        order = self._order(self.bot, sl=Decimal(85580), tp=Decimal(87000), decision=decision)
        with self.assertRaisesRegex(RiskRejected, "entry_trigger_lost"):
            self._enforce(order)
