import json
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings

from bots.models import Asset, Bot
from brokers.models import BrokerAccount
from execution.models import BrokerPosition, Decision, Order, RiskPolicy, Signal
from execution.services.bot_replay import configure_bot_replay, run_bot_replay
from execution.services.bot_replay_worker import completed_context, replay
from execution.services.historical_backtest import validate_config
from execution.tests.test_strategy_price_normalization import _scaled_pinbar_candles


class ReplayIsolationTests(SimpleTestCase):
    def test_packaged_replay_does_not_load_or_start_live_services(self):
        from pathlib import Path
        from desktop.backend_launcher import main
        with patch("execution.services.bot_replay_worker.main") as worker, patch("desktop.backend_launcher.load_config") as load:
            self.assertEqual(main(["--service", "bot-replay", "--replay-input", "input.json", "--replay-output", "result.json"]), 0)
        worker.assert_called_once_with(input_path=Path("input.json"), output_path=Path("result.json"))
        load.assert_not_called()

    def test_context_excludes_unfinished_and_gapped_candles(self):
        start = datetime(2025, 1, 6, tzinfo=timezone.utc)
        bars = [{"time": start + timedelta(minutes=i * 5), "open": Decimal(i + 1), "close": Decimal(i + 2),
                 "high": Decimal(i + 3), "low": Decimal(i + 1), "tick_volume": 1} for i in range(5)]
        result = completed_context(bars, "15m", start + timedelta(minutes=25), 5)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["close"], 4)
        self.assertEqual(completed_context(bars[:1] + bars[2:], "15m", start + timedelta(minutes=25), 5), [])

    def test_replay_cannot_run_in_application_process(self):
        with self.assertRaisesRegex(RuntimeError, "dedicated in-memory"):
            replay({})


@override_settings(MAX_ORDER_LOT=Decimal("5"), ECONOMIC_CALENDAR_ENABLED=False)
class BotPipelineReplayTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="replay-owner")
        self.account = BrokerAccount.objects.create(owner=self.user, name="Replay original", broker="mt5", connector="mt5_local", account_ref="original")
        self.asset = Asset.objects.get(symbol="EURUSDm")
        self.bot = Bot.objects.create(
            owner=self.user, name="Replay contract", asset=self.asset, broker_account=self.account,
            engine_mode="scalper", enabled_strategies=["price_action_pinbar"],
            default_timeframe="5m", allowed_timeframes=["5m"], trading_schedule_enabled=False,
            trade_interval_minutes=0, decision_min_score=Decimal("0.1"), risk_per_trade_pct=Decimal("0.25"),
            position_sizing_mode="risk", max_bot_lot_size=Decimal("5"),
            scalper_params={"rollover_blackout": [], "symbols": {"EURUSD": {
                "execution_timeframes": ["5m"], "context_timeframes": ["15m"], "exit_mode": "hybrid", "tp1_r": 0.3,
                "tp1_close_pct": 50, "trail_start_r": 0.2, "be_trigger_r": 0.2,
                "be_buffer_r": 0, "max_spread_points": 100, "max_spread_unit": "points",
            }}},
        )
        RiskPolicy.objects.create(broker_account=self.account, max_order_lot_size=5, max_aggregate_open_lots=5)
        self.data = {
            "pipeline_mode": "bot_pipeline", "strategy": "price_action_pinbar", "timeframe": "5m",
            "quantity": "0.01", "contract_size": "100000", "point_size": "0.00001", "digits": 5,
            "volume_min": "0.01", "volume_max": "5", "volume_step": "0.01", "margin_per_lot": "100",
            "initial_balance": "10000", "spread_points": "1", "slippage_points": "0", "warmup": 100,
        }

    def scenario(self):
        pattern = _scaled_pinbar_candles(Decimal("1.1"))
        prefix = []
        for i in range(30):
            value = Decimal("1.0967") + Decimal(i) * Decimal("0.00011")
            prefix.append({"open": value, "close": value, "high": value + Decimal("0.0011"), "low": value - Decimal("0.0011")})
        bars = prefix + pattern
        entry = pattern[-1]["close"]
        for shift in ("0.001", "0.0012", "0.0014"):
            close = entry + Decimal(shift)
            opening = bars[-1]["close"]
            bars.append({"open": opening, "high": close, "low": opening, "close": close})
        start = datetime(2025, 1, 6, tzinfo=timezone.utc)
        for i, bar in enumerate(bars):
            bar.update(time=start + timedelta(minutes=5 * i), tick_volume=100)
        return bars, {"first_index": 115, "last_index": len(bars) - 1}

    def config(self):
        return configure_bot_replay(self.bot, validate_config(self.data), self.data)

    def test_snapshot_excludes_broker_credentials_and_copies_numeric_limits(self):
        snapshot = self.config()["bot_snapshot"]
        self.assertEqual(Decimal(snapshot["policy"]["max_order_lot_size"]), 5)
        self.assertNotIn("mt5_password", json.dumps(snapshot))
        original = deepcopy(snapshot["profile"])
        self.asset.recommended_config["symbol_config"]["max_spread_points"] = 999
        self.assertEqual(snapshot["profile"], original)

    @override_settings(ECONOMIC_CALENDAR_ENABLED=True)
    def test_worker_replays_live_sizing_and_partial_exits_without_touching_live_records(self):
        bars, dataset = self.scenario()
        counts = [model.objects.count() for model in (Order, Signal, Decision, BrokerPosition)]
        result = run_bot_replay(bars, self.config(), dataset, self.asset.symbol)
        self.assertGreater(result["summary"]["trades"], 0, result)
        self.assertIn("partial_tp1", {trade["reason"] for trade in result["trades"]})
        self.assertGreater(Decimal(result["trades"][0]["original_quantity"]), Decimal("0.01"))
        self.assertGreater(result["summary"]["protection_moves"], 0)
        self.assertTrue(any("News not simulated" in assumption for assumption in result["assumptions"]))
        self.assertEqual([model.objects.count() for model in (Order, Signal, Decision, BrokerPosition)], counts)
        self.bot.refresh_from_db()
        self.assertEqual(self.bot.status, "stopped")

    def test_gold_reference_replay_uses_completed_h1_and_managed_runner(self):
        from bots.services import apply_recommendations_to_bot
        self.bot.asset = Asset.objects.get(symbol="XAUUSDm")
        apply_recommendations_to_bot(self.bot, save=False)
        self.assertEqual(set(self.bot.enabled_strategies), {
            "trend_pullback", "breakout_retest", "momentum_ignition", "price_action_pinbar", "doji_breakout",
        })
        self.bot.enabled_strategies = ["price_action_pinbar"]
        self.bot.trading_schedule_enabled = False
        self.bot.save()
        self.data.update(contract_size="100", point_size="0.01", digits=2, spread_points="10")
        scale = Decimal("2300")
        pattern = _scaled_pinbar_candles(scale)
        for bar in pattern[:-3]:
            bar["high"], bar["low"] = bar["close"] + scale * Decimal("0.0004"), bar["close"] - scale * Decimal("0.0004")
        prefix = []
        for i in range(360):
            price = scale * (Decimal("1") - Decimal(360 - i) * Decimal("0.00005"))
            prefix.append({"open": price, "close": price, "high": price + 1, "low": price - 1})
        bars = prefix + pattern
        entry = pattern[-1]["close"]
        for gain in ("4", "7", "8"):
            opening, close = bars[-1]["close"], entry + Decimal(gain)
            bars.append({"open": opening, "low": opening, "high": close, "close": close})
        start = datetime(2025, 1, 6, tzinfo=timezone.utc)
        for i, bar in enumerate(bars):
            bar.update(time=start + timedelta(minutes=i * 5), tick_volume=150)
        result = run_bot_replay(bars, self.config(), {"first_index": len(prefix) + len(pattern), "last_index": len(bars) - 1}, "XAUUSDm")
        self.assertGreater(result["summary"]["trades"], 0, result)
        self.assertIn("partial_tp1", {trade["reason"] for trade in result["trades"]})
        trade = result["trades"][0]
        entry, stop, target = (Decimal(trade[key]) for key in ("entry_price", "sl", "tp"))
        self.assertLessEqual(abs(entry - stop) / entry * 100, Decimal("0.3"))
        self.assertAlmostEqual(float((target - entry) / (entry - stop)), 2.0, places=2)

    def test_api_requires_broker_volume_and_margin_inputs(self):
        self.client.force_login(self.user)
        response = self.client.post("/api/personal/backtests/", {
            **self.data, "bot_id": self.bot.id, "volume_min": "", "csv": "unused",
        }, content_type="application/json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("volume_min", response.json()["detail"])

    def test_replay_respects_allocation_budget_and_minimum_lot(self):
        bars, dataset = self.scenario()
        self.bot.allocation_amount = Decimal("5000")
        self.bot.save()
        result = run_bot_replay(bars, self.config(), dataset, self.asset.symbol)
        self.assertGreater(result["summary"]["trades"], 0, result)
        trade = result["trades"][0]
        stop_distance = abs(Decimal(trade["entry_price"]) - Decimal(trade["sl"]))
        estimated_loss = stop_distance * Decimal(self.data["contract_size"]) * Decimal(trade["original_quantity"])
        self.assertGreater(estimated_loss, 0)
        self.assertLessEqual(estimated_loss, Decimal("12.50"))
        self.bot.allocation_amount = Decimal("1")
        self.bot.save()
        result = run_bot_replay(bars, self.config(), dataset, self.asset.symbol)
        self.assertEqual(result["summary"]["trades"], 0)
        self.assertIn("BROKER_MIN_VOLUME", {row["reason"] for row in result["skip_reasons"]})

    def test_missing_completed_context_explains_zero_trade_result(self):
        bars, _ = self.scenario()
        result = run_bot_replay(bars, self.config(), {"first_index": 50, "last_index": 52}, self.asset.symbol)
        self.assertEqual(result["summary"]["trades"], 0)
        reasons = {row["reason"]: row["count"] for row in result["skip_reasons"]}
        self.assertEqual(reasons.get("htf_bias_unavailable"), 3)

    def test_quiet_btc_replay_reaches_pullback_detector_with_completed_neutral_context(self):
        from bots.services import apply_recommendations_to_bot
        self.bot.asset = Asset.objects.get(symbol="BTCUSDm")
        apply_recommendations_to_bot(self.bot, save=False)
        self.bot.save()
        self.data.update(contract_size="1", point_size=".01", digits=2, spread_points="200")
        start = datetime(2025, 1, 6, tzinfo=timezone.utc)
        bars = [{"time": start + timedelta(minutes=5 * i), "open": Decimal(86000),
                 "close": Decimal(86000), "high": Decimal(86020), "low": Decimal(85980),
                 "tick_volume": 100} for i in range(483)]
        before = [model.objects.count() for model in (Order, Signal, Decision, BrokerPosition)]
        result = run_bot_replay(bars, self.config(), {"first_index": 480, "last_index": 482}, "BTCUSDm")
        reasons = {row["reason"]: row["count"] for row in result["skip_reasons"]}
        self.assertNotIn("htf_bias_neutral", reasons)
        self.assertNotIn("htf_bias_unavailable", reasons)
        self.assertEqual(sum(count for reason, count in reasons.items() if reason.startswith("trend_pullback_")), 3, result)
        self.assertEqual(result["summary"]["trades"], 0)
        self.assertEqual([model.objects.count() for model in (Order, Signal, Decision, BrokerPosition)], before)
