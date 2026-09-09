from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase

from bots.models import Asset, Bot
from brokers.models import BrokerAccount
from execution.models import Decision, RiskPolicy, ScalperRunLog, Signal
from execution.services.scalper_config import (
    build_scalper_config,
    normalize_execution_timeframe,
    resolve_scalper_execution_timeframe,
)
from execution.services.strategies.scalper import plan_scalper_trade
from execution.tasks import run_scalper_engine_for_all_bots


class ScalperTimeframeSelectionTests(TestCase):
    def setUp(self):
        self.account = BrokerAccount.objects.create(
            name="Timeframe account",
            broker="mt5",
            connector="mt5_local",
            account_ref="timeframe-account",
            is_active=True,
            is_verified=True,
        )
        self.asset, _created = Asset.objects.get_or_create(
            symbol="BTCUSDm",
            defaults={"category": "crypto"},
        )
        self.bot = Bot.objects.create(
            name="BTC scalper",
            status="active",
            auto_trade=True,
            engine_mode="scalper",
            broker_account=self.account,
            asset=self.asset,
            default_timeframe="5m",
            allowed_timeframes=["1m", "5m"],
            scalper_params={"strategy_profile": "btc_momentum"},
        )

    def test_normalizes_application_and_mt5_timeframe_spellings(self):
        self.assertEqual(normalize_execution_timeframe("1m"), "1m")
        self.assertEqual(normalize_execution_timeframe("M1"), "1m")
        self.assertEqual(normalize_execution_timeframe("H4"), "4h")
        self.assertEqual(normalize_execution_timeframe("MN1"), "1mo")

    def test_profile_restriction_overrides_incompatible_generic_bot_default(self):
        config = build_scalper_config(self.bot)

        selected = resolve_scalper_execution_timeframe(
            self.bot,
            self.asset.symbol,
            "1m",
            config=config,
        )

        self.assertEqual(selected, "1m")

    def test_conflicting_bot_and_profile_permissions_return_no_timeframe(self):
        self.bot.allowed_timeframes = ["5m"]
        self.bot.save(update_fields=["allowed_timeframes"])
        config = build_scalper_config(self.bot)

        selected = resolve_scalper_execution_timeframe(
            self.bot,
            self.asset.symbol,
            "1m",
            config=config,
        )

        self.assertIsNone(selected)

    def test_resolved_m1_signal_passes_the_timeframe_decision_gate(self):
        signal = Signal.objects.create(
            bot=self.bot,
            source="scalper_engine",
            symbol=self.asset.symbol,
            timeframe="1m",
            direction="buy",
            payload={
                "close": "80000",
                "bias_m15": "buy",
                "session": "london",
                "spread_points": "10",
                "atr_points": "200",
            },
            dedupe_key="resolved-m1-signal",
        )

        plan = plan_scalper_trade(signal, self.bot, build_scalper_config(self.bot))

        self.assertEqual(plan.action, "open")
        self.assertEqual(plan.reason, "scalper:plan")

    @patch("execution.tasks.maybe_unpause_crypto_for_open_market")
    @patch("execution.tasks.trade_scalper_strategies_for_bot.apply")
    @patch("execution.tasks.bot_is_available_for_trading", return_value=True)
    @patch("execution.tasks.get_market_status_for_bot")
    def test_runner_dispatches_profile_compatible_timeframe(
        self,
        market_status,
        _available,
        apply_scalper,
        _unpause,
    ):
        market_status.return_value = SimpleNamespace(is_open=True, reason="test")

        result = run_scalper_engine_for_all_bots.run(timeframe="1m", n_bars=100)

        self.assertEqual(result["dispatched"], 1)
        self.assertEqual(result["skipped_not_accepted"], 0)
        apply_scalper.assert_called_once_with(
            args=(self.bot.id,),
            kwargs={
                "timeframe": "1m",
                "n_bars": 100,
                "defer_dispatch": True,
            },
        )

    @patch("execution.tasks._dispatch_scalper_candidate")
    @patch("execution.tasks.maybe_unpause_crypto_for_open_market")
    @patch("execution.tasks.trade_scalper_strategies_for_bot.apply")
    @patch("execution.tasks.bot_is_available_for_trading", return_value=True)
    @patch("execution.tasks.get_market_status_for_bot")
    def test_runner_awards_slot_to_highest_scoring_bot(
        self,
        market_status,
        _available,
        apply_scalper,
        _unpause,
        dispatch_candidate,
    ):
        market_status.return_value = SimpleNamespace(is_open=True, reason="test")
        RiskPolicy.objects.update_or_create(
            broker_account=self.account,
            defaults={"max_total_open_positions": 1},
        )
        bots = [self.bot]
        for symbol in ("ETHUSDm", "XAUUSDm"):
            asset, _ = Asset.objects.get_or_create(symbol=symbol)
            bots.append(
                Bot.objects.create(
                    name=f"{symbol} scalper",
                    status="active",
                    auto_trade=True,
                    engine_mode="scalper",
                    broker_account=self.account,
                    asset=asset,
                    default_timeframe="1m",
                )
            )

        scores = (0.70, 0.95, 0.80)
        decisions = {}
        run_logs = {}
        for index, bot in enumerate(bots):
            signal = Signal.objects.create(
                bot=bot,
                source="scalper_engine",
                symbol=bot.asset.symbol,
                timeframe="1m",
                direction="buy",
                payload={},
                dedupe_key=f"rank-{bot.id}",
            )
            decisions[bot.id] = Decision.objects.create(
                bot=bot,
                signal=signal,
                action="open",
                reason="rank-test",
                score=scores[index],
                params={"sl": "1", "tp": "2"},
            )
            run_logs[bot.id] = ScalperRunLog.objects.create(
                bot=bot,
                timeframe="1m",
                session="test",
                summary={"outcome": "candidate_pending_allocation"},
            )

        def scan_result(*_args, **call_kwargs):
            bot_id = call_kwargs["args"][0]
            decision = decisions[bot_id]
            return SimpleNamespace(
                result={
                    "candidates": [
                        {
                            "bot_id": bot_id,
                            "broker_account_id": self.account.id,
                            "decision_id": decision.id,
                            "strategy": "trend_pullback",
                            "symbol": decision.signal.symbol,
                            "score": float(decision.score),
                            "run_log_id": run_logs[bot_id].id,
                        }
                    ]
                }
            )

        apply_scalper.side_effect = scan_result
        dispatch_candidate.return_value = {
            "orders": [(999, "ETHUSDm", "buy")],
            "failures": [],
            "rejection_reason": None,
        }

        result = run_scalper_engine_for_all_bots.run(timeframe="1m", n_bars=100)

        self.assertEqual(result["candidates"], 3)
        self.assertEqual(result["awarded"], 1)
        self.assertEqual(result["slot_losses"], 2)
        winning_decision = dispatch_candidate.call_args.args[0]
        self.assertEqual(winning_decision.pk, decisions[bots[1].id].pk)
        for bot in bots:
            run_logs[bot.id].refresh_from_db()
        self.assertEqual(run_logs[bots[1].id].summary["slot_status"], "won")
        self.assertEqual(run_logs[bots[1].id].summary["outcome"], "orders_sent")
        for losing_bot in (bots[0], bots[2]):
            summary = run_logs[losing_bot.id].summary
            self.assertEqual(summary["slot_status"], "lost")
            self.assertEqual(summary["slot_winner_bot_id"], bots[1].id)
