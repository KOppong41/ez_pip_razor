from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from bots.models import Asset, Bot
from bots.services import apply_recommendations_to_bot
from brokers.models import BrokerAccount
from execution.models import Decision, ScalperRunLog
from execution.services.ai_strategy_selector import select_ai_strategies
from execution.services.brokers import BrokerSymbolConstraints
from execution.services.engine_types import EngineDecision
from execution.services.strategy_registry import SCALPER_STRATEGY_REGISTRY
from execution.tasks import trade_scalper_strategies_for_bot


@override_settings(ECONOMIC_CALENDAR_ENABLED=False)
class BtcNeutralContextTests(TestCase):
    def setUp(self):
        owner = get_user_model().objects.create_user("neutral-btc")
        self.account = BrokerAccount.objects.create(owner=owner, name="Neutral BTC", broker="mt5",
            connector="mt5_local", account_ref="neutral-btc", is_active=True, is_verified=True)
        self.bot = Bot.objects.create(owner=owner, name="Neutral BTC", broker_account=self.account,
                                      asset=Asset.objects.get(symbol="BTCUSDm"), engine_mode="scalper")
        apply_recommendations_to_bot(self.bot, save=False)
        self.bot.status, self.bot.auto_trade = "active", True
        self.bot.save()
        self.bars = [{"open": Decimal(86000), "close": Decimal(86000), "high": Decimal(86020),
                      "low": Decimal(85980), "time": timezone.now(), "tick_volume": 100} for _ in range(100)]
        self.constraints = BrokerSymbolConstraints(point=Decimal(".01"), digits=2, min_lot=Decimal(".01"),
            max_lot=Decimal(100), lot_step=Decimal(".01"), stops_level_points=Decimal(0))

    def scan(self, frames=(None, None), *, decision=None, null_runner=False, spread=2, runner_error=None):
        details = [frame if isinstance(frame, dict) else {"bias": frame, "structure": "range",
                   "ema_slope_pct": 0, "atr_ratio": 1} for frame in frames]
        runner = Mock(return_value=None if null_runner else decision or EngineDecision(action="skip", reason="no_setup"))
        runner.side_effect = runner_error
        others = {name: Mock() for name in ("momentum_ignition", "breakout_retest")}
        registry = {"trend_pullback": replace(SCALPER_STRATEGY_REGISTRY["trend_pullback"], runner=runner)}
        registry.update({name: replace(SCALPER_STRATEGY_REGISTRY[name], runner=mock) for name, mock in others.items()})
        with (
            patch("execution.tasks.get_market_status_for_bot", return_value=SimpleNamespace(is_open=True, reason="test")),
            patch("execution.tasks.bot_is_available_for_trading", return_value=True),
            patch("execution.tasks.get_broker_symbol_constraints", return_value=self.constraints),
            patch("execution.tasks.get_candles_for_account", return_value=self.bars),
            patch("execution.tasks._analyze_htf_bias", side_effect=details),
            patch("execution.tasks.select_ai_strategies", wraps=select_ai_strategies) as selector,
            patch.dict("execution.tasks.SCALPER_STRATEGY_REGISTRY", registry),
            patch("execution.tasks.MT5Connector") as connector,
            patch("execution.services.decision.get_price", return_value=Decimal(86000)),
            patch("execution.tasks._dispatch_scalper_candidate") as dispatch,
        ):
            connector.return_value.symbol_info_for_account.return_value = SimpleNamespace(visible=True, trade_mode=4)
            connector.return_value.tick_for_account.return_value = SimpleNamespace(
                bid=86000, ask=86000 + spread, last=86000, time=timezone.now().timestamp())
            result = trade_scalper_strategies_for_bot.run(self.bot.pk, timeframe="5m", defer_dispatch=True)
        dispatch.assert_not_called()
        self.assertFalse(self.bot.orders.exists())
        for mock in others.values():
            mock.assert_not_called()
        return result, ScalperRunLog.objects.filter(bot=self.bot).latest("id").summary, selector, runner

    def test_neutral_and_mixed_context_only_evaluate_confirmed_pullbacks(self):
        for frames in ((None, None), ("buy", None), (None, "sell")):
            with self.subTest(frames=frames):
                result, summary, selector, runner = self.scan(frames)
                self.assertEqual(result["status"], "ok")
                selector.assert_called_once()
                self.assertIsNone(selector.call_args.kwargs["context"]["htf_bias"])
                self.assertEqual(summary["strategies_evaluated"], ["trend_pullback"])
                self.assertEqual(summary["htf_status"], "neutral")
                self.assertEqual(set(summary["htf_bias_detail"]["frames"]), {"15m", "1h"})
                self.assertEqual(summary["htf_bias_detail"]["regime"]["bias"], frames[1])
                self.assertEqual(summary["rejection_reason"], "no_setup")
                self.assertTrue(runner.call_args.args[-1].require_confirmation)
        self.assertFalse(self.bot.signals.exists())
        self.assertFalse(Decision.objects.filter(bot=self.bot).exists())
        self.bot.refresh_from_db()
        self.assertEqual(self.bot.risk_per_trade_pct, Decimal(".25"))
        self.assertFalse(self.bot.trading_schedule_enabled)

    def test_neutral_context_with_expanding_directional_h1_still_excludes_momentum(self):
        _, summary, selector, _ = self.scan((None, {"bias": "buy", "atr_ratio": 2,
            "ema_slope_pct": .002, "structure": "higher_high"}))
        self.assertEqual(summary["strategies_evaluated"], ["trend_pullback"])
        self.assertEqual(selector.call_args.kwargs["context"]["regime"]["atr_ratio"], 2)

    def test_neutral_context_cannot_restore_strategies_outside_configured_pool(self):
        self.bot.enabled_strategies = ["momentum_ignition", "breakout_retest"]
        self.bot.save()
        result, summary, selector, runner = self.scan()
        self.assertEqual(result["reason"], "no_suitable_strategies")
        self.assertEqual(summary["htf_status"], "neutral")
        selector.assert_called_once()
        runner.assert_not_called()

    def test_missing_malformed_and_conflicting_context_still_block(self):
        for frames, reason in (
            ((None, {}), "htf_bias_unavailable"),
            (({"bias": "invalid"}, None), "htf_bias_unavailable"),
            (("buy", "sell"), "htf_context_conflict"),
            (("sell", "buy"), "htf_context_conflict"),
        ):
            with self.subTest(frames=frames):
                result, _, selector, runner = self.scan(frames)
                self.assertEqual(result, {"status": "skipped", "reason": reason})
                selector.assert_not_called()
                runner.assert_not_called()

    def test_gold_neutral_context_retains_strict_gate(self):
        self.bot.asset = Asset.objects.get(symbol="XAUUSDm")
        apply_recommendations_to_bot(self.bot, save=False)
        self.bot.trading_schedule_enabled = False
        self.bot.save()
        result, summary, selector, runner = self.scan()
        self.assertEqual(result, {"status": "skipped", "reason": "htf_bias_neutral"})
        self.assertEqual(summary["htf_status"], "neutral")
        selector.assert_not_called()
        runner.assert_not_called()

    def test_unsupported_btc_context_still_blocks_before_selection(self):
        with patch("execution.services.higher_timeframe_context.analyze_context",
                   return_value=(None, {}, "htf_timeframe_unsupported")):
            result, _, selector, runner = self.scan()
        self.assertEqual(result["reason"], "htf_timeframe_unsupported")
        selector.assert_not_called()
        runner.assert_not_called()

    def test_qualifying_neutral_pullback_reaches_persisted_decision(self):
        decision = EngineDecision(action="open", direction="buy", strategy="trend_pullback", score=.99,
            entry_price=Decimal(86000), sl=Decimal(85580), tp=Decimal(86840), target_rr=Decimal(2),
            entry_trigger=Decimal(85990), reason="confirmed_pullback")
        result, summary, _, _ = self.scan(decision=decision)
        self.assertEqual((result["signals"], result["decisions"]), (1, 1))
        saved = Decision.objects.get(bot=self.bot)
        self.assertEqual(saved.action, "open", saved.reason)
        self.assertEqual(saved.params["sl"], "85580")
        self.assertEqual(saved.signal.payload["htf_status"], "neutral")
        self.assertEqual(summary["outcome"], "candidate_pending_allocation")

    def test_mixed_context_allows_only_the_remaining_direction(self):
        for frames in (("buy", None), (None, "buy"), ("sell", None), (None, "sell")):
            bias = next(value for value in frames if value)
            for direction in ("buy", "sell"):
                with self.subTest(frames=frames, direction=direction):
                    # Keep each decision independent of previous test entries'
                    # interval and same-direction cooldown history.
                    self.bot.signals.all().delete()
                    # Each scan needs a fresh candle to avoid signal deduplication.
                    self.bars[-1]["time"] = timezone.now()
                    decision = EngineDecision(action="open", direction=direction, strategy="trend_pullback", score=.99,
                        entry_price=Decimal(86000), sl=Decimal(85580 if direction == "buy" else 86420),
                        tp=Decimal(86840 if direction == "buy" else 85160), target_rr=Decimal(2),
                        entry_trigger=Decimal(85990 if direction == "buy" else 86010), reason="confirmed_pullback")
                    _, summary, selector, _ = self.scan(frames, decision=decision)
                    self.assertIsNone(selector.call_args.kwargs["context"]["htf_bias"])
                    self.assertEqual(summary["strategies_evaluated"], ["trend_pullback"])
                    saved = Decision.objects.filter(bot=self.bot).latest("id")
                    self.assertEqual(saved.signal.direction, direction)
                    self.assertEqual(saved.signal.payload["context_bias"], bias)
                    if direction == bias:
                        self.assertEqual(saved.action, "open", saved.reason)
                    else:
                        self.assertEqual((saved.action, saved.reason), ("ignore", "scalper:context_direction_conflict"))

    def test_neutral_context_keeps_structural_stop_enforcement(self):
        decision = EngineDecision(action="open", direction="buy", strategy="trend_pullback", score=.99,
            entry_price=Decimal(86000), sl=Decimal(85900), tp=Decimal(86200), reason="too_tight")
        self.scan(decision=decision)
        saved = Decision.objects.get(bot=self.bot)
        self.assertEqual((saved.action, saved.reason), ("ignore", "scalper:sl_below_min"))

    def test_spread_diagnostic_uses_profile_limit_when_bot_limit_is_disabled(self):
        self.bot.max_spread_points = 0
        self.bot.save()
        _, summary, _, _ = self.scan(spread=100)
        self.assertEqual(summary["spread_status"], "fail")
        self.assertGreater(Decimal(summary["spread_points"]), Decimal(summary["spread_limit_points"]))

    def test_none_runner_result_is_recorded_without_crashing_cycle(self):
        result, summary, _, _ = self.scan(null_runner=True)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(summary["rejection_reason"], "strategy_no_decision")
        self.assertEqual(summary["outcome"], "strategy_errors")
        self.assertEqual(summary["strategies_evaluated"], ["trend_pullback"])
        self.assertFalse(self.bot.signals.exists())

    def test_detector_exception_is_visible_in_persisted_scan_evidence(self):
        _, summary, _, _ = self.scan(runner_error=ValueError("private exception details"))
        self.assertEqual(summary["outcome"], "strategy_errors")
        self.assertEqual(summary["rejection_reason"], "strategy_exception")
        self.assertEqual(summary["strategies_evaluated"], ["trend_pullback"])
        self.assertEqual(summary["strategies"][0]["action"], "error")
        self.assertEqual(summary["strategies"][0]["metadata"], {"error_type": "ValueError"})
        self.assertNotIn("private exception details", str(summary))
        self.assertFalse(self.bot.signals.exists())
        self.assertFalse(Decision.objects.filter(bot=self.bot).exists())
