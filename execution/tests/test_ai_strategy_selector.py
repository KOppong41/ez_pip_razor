from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase

from execution.services.ai_strategy_selector import effective_spread_allowance, select_ai_strategies


GOLD_POOL = [
    "trend_pullback",
    "breakout_retest",
    "momentum_ignition",
    "price_action_pinbar",
    "doji_breakout",
]


class GoldStrategySelectorTests(SimpleTestCase):
    def select(self, **context):
        return select_ai_strategies(
            engine_mode="scalper",
            available=GOLD_POOL,
            symbol="XAUUSDm",
            context=context,
        )

    def test_high_volatility_favors_momentum_breakout_and_trend(self):
        self.assertEqual(
            self.select(atr_price="12", last_close="2300"),
            ["momentum_ignition", "breakout_retest", "trend_pullback"],
        )

    def test_strong_h1_trend_uses_high_energy_pool(self):
        self.assertEqual(
            self.select(
                atr_price="1",
                last_close="2300",
                htf_bias="buy",
                regime={"ema_slope_pct": "0.0002", "structure": "higher_high"},
            ),
            ["momentum_ignition", "breakout_retest", "trend_pullback"],
        )

    def test_medium_regime_favors_trend_breakout_and_pinbar(self):
        self.assertEqual(
            self.select(
                atr_price="5",
                last_close="2300",
                htf_bias="buy",
                regime={"ema_slope_pct": "0.0001", "structure": "range"},
            ),
            ["trend_pullback", "breakout_retest", "price_action_pinbar"],
        )

    def test_quiet_rejection_regime_includes_doji(self):
        self.assertEqual(
            self.select(
                atr_price="1",
                last_close="2300",
                regime={"ema_slope_pct": "0.00001", "structure": "range"},
            ),
            ["price_action_pinbar", "doji_breakout", "trend_pullback"],
        )

    def test_quiet_regime_with_required_directional_bias_still_includes_doji(self):
        for bias in ("buy", "sell"):
            with self.subTest(bias=bias):
                self.assertEqual(
                    self.select(
                        atr_price="1", last_close="2300", htf_bias=bias,
                        regime={"ema_slope_pct": "0.0001", "structure": "range"},
                    ),
                    ["price_action_pinbar", "doji_breakout", "trend_pullback"],
                )

    def test_atr_expansion_selects_momentum_even_with_small_entry_atr(self):
        self.assertEqual(
            self.select(atr_price="1", last_close="2300", regime={"atr_ratio": "1.25"}),
            ["momentum_ignition", "breakout_retest", "trend_pullback"],
        )

    def test_wide_spread_preserves_precise_setup_preference_in_strong_regime(self):
        self.assertEqual(
            self.select(atr_price="12", last_close="2300", spread_price="0.3", allowed_spread_price="0.345"),
            ["price_action_pinbar"],
        )

    def test_gold_selection_tracks_allowance_instead_of_price(self):
        for price in ("2300", "4600"):
            with self.subTest(price=price):
                self.assertEqual(self.select(last_close=price, regime={"atr_ratio": "1.5"},
                                             spread_price=".3", allowed_spread_price=".375"),
                                 ["price_action_pinbar"])
                self.assertEqual(self.select(last_close=price, regime={"atr_ratio": "1.5"},
                                             spread_price=".3", allowed_spread_price=".6"),
                                 ["momentum_ignition", "breakout_retest", "trend_pullback"])

    def test_missing_or_invalid_gold_allowance_does_not_guess_a_price_cap(self):
        for allowance in (None, 0, "NaN", "Infinity", -1):
            self.assertEqual(self.select(atr_price="12", last_close="2300", spread_price="3",
                                         allowed_spread_price=allowance),
                             ["momentum_ignition", "breakout_retest", "trend_pullback"])

    def test_effective_allowance_uses_stricter_bot_or_profile_cap(self):
        for unit, value, expected in (("percent", ".015", ".345"), ("points", "40", ".4"),
                                       ("price", ".25", ".25"), ("pips", "4", ".04")):
            profile = SimpleNamespace(max_spread_points=Decimal(value), max_spread_unit=unit)
            for bot_points in (0, 10, 100):
                allowance = effective_spread_allowance(SimpleNamespace(max_spread_points=bot_points), profile,
                                                       point=Decimal(".01"), digits=2, market_price=Decimal("2300"))
                expected_limit = min(Decimal(expected), Decimal(bot_points) * Decimal(".01")) if bot_points else Decimal(expected)
                self.assertEqual(allowance, expected_limit)
        self.assertIsNone(effective_spread_allowance(SimpleNamespace(max_spread_points=10),
                                                    SimpleNamespace(max_spread_points=40, max_spread_unit="points"),
                                                    point=None, market_price=Decimal("2300")))

    def test_selection_respects_configured_pool_and_limit(self):
        self.assertEqual(
            select_ai_strategies(
                engine_mode="scalper", symbol="XAUUSDm", max_strategies=1,
                available=["doji_breakout", "breakout_retest"],
                context={"atr_price": "12", "last_close": "2300"},
            ),
            ["breakout_retest"],
        )


class EngineStrategySelectorIntegrationTests(TestCase):
    def test_gold_scalper_passes_effective_allowance_from_active_profile(self):
        from django.utils import timezone
        from bots.models import Asset, Bot
        from bots.services import apply_recommendations_to_bot
        from brokers.models import BrokerAccount
        from execution.services.brokers import BrokerSymbolConstraints
        from execution.tasks import trade_scalper_strategies_for_bot

        account = BrokerAccount.objects.create(name="Gold selector", broker="mt5", connector="mt5_local",
                                               account_ref="gold-selector", is_active=True, is_verified=True)
        bot = Bot.objects.create(name="Gold selector", broker_account=account, asset=Asset.objects.get(symbol="XAUUSDm"))
        apply_recommendations_to_bot(bot, save=False)
        bot.auto_trade, bot.status, bot.trading_schedule_enabled = True, "active", False
        bot.max_spread_points = 20
        bot.save()
        bars = [{"open": Decimal(2300), "close": Decimal(2300), "high": Decimal(2301), "low": Decimal(2299),
                 "tick_volume": 100, "time": timezone.now()} for _ in range(50)]
        constraints = BrokerSymbolConstraints(point=Decimal(".01"), digits=2, min_lot=Decimal(".01"),
                                               max_lot=Decimal("100"), lot_step=Decimal(".01"), stops_level_points=Decimal("0"))
        with (
            patch("execution.tasks.get_market_status_for_bot", return_value=SimpleNamespace(is_open=True, reason="test")),
            patch("execution.tasks.bot_is_available_for_trading", return_value=True),
            patch("execution.tasks.get_broker_symbol_constraints", return_value=constraints),
            patch("execution.tasks.get_candles_for_account", return_value=bars),
            patch("execution.tasks.MT5Connector") as connector,
            patch("execution.services.higher_timeframe_context.analyze_context", return_value=("buy", {"regime": {"atr_ratio": "1.5"}}, None)),
            patch("execution.tasks.select_ai_strategies", wraps=select_ai_strategies) as selector,
        ):
            connector.return_value.symbol_info_for_account.return_value = SimpleNamespace(visible=True, trade_mode=4)
            connector.return_value.tick_for_account.return_value = SimpleNamespace(
                bid=2300, ask=2300.17, last=2300, time=timezone.now().timestamp())
            result = trade_scalper_strategies_for_bot.run(bot.id, timeframe="5m")
        self.assertTrue(selector.called, result)
        selector.assert_called_once()
        context = selector.call_args.kwargs["context"]
        self.assertEqual(context["allowed_spread_price"], Decimal(".20"))
        self.assertEqual(context["spread_price"], Decimal(".17"))
        self.assertEqual(select_ai_strategies(**selector.call_args.kwargs), ["price_action_pinbar"])
        self.assertFalse(bot.orders.exists())

    def test_engine_passes_analyzed_htf_regime_to_selector(self):
        from bots.models import Asset, Bot
        from brokers.models import BrokerAccount
        from execution.services.engine import EngineDecision
        from execution.tasks import trade_harami_for_bot

        account = BrokerAccount.objects.create(
            name="Selector", broker="paper", connector="paper", account_ref="selector",
        )
        bot = Bot.objects.create(
            name="Selector", broker_account=account, asset=Asset.objects.get(symbol="XAUUSDm"),
            auto_trade=True, status="active", trading_schedule_enabled=False,
            allowed_symbols=["XAUUSDm"], allowed_timeframes=["5m"],
        )
        candles = [
            {"open": Decimal(2300 + i), "close": Decimal(2301 + i),
             "high": Decimal(2302 + i), "low": Decimal(2299 + i)}
            for i in range(40)
        ]
        with (
            patch("execution.tasks.get_market_status_for_bot", return_value=None),
            patch("execution.tasks.get_candles_for_account", return_value=candles),
            patch("execution.tasks.select_ai_strategies", wraps=select_ai_strategies) as selector,
            patch("execution.services.engine.run_engine", return_value=EngineDecision(action="skip")) as engine,
        ):
            result = trade_harami_for_bot.run(bot.id, timeframe="5m")

        self.assertEqual(result["action"], "skip")
        engine.assert_called_once()
        context = selector.call_args.kwargs["context"]
        self.assertEqual(context["htf_bias"], "buy")
        self.assertEqual(context["regime"]["bias"], "buy")
        self.assertGreater(context["regime"]["ema_slope_pct"], 0)
