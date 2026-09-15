from decimal import Decimal
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase

from execution.services.ai_strategy_selector import select_ai_strategies


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
            self.select(atr_price="12", last_close="2300", spread_price="3"),
            ["price_action_pinbar"],
        )

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
