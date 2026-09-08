from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase

from bots.models import Asset, Bot
from brokers.models import BrokerAccount
from execution.models import HistoricalBacktest, Order, Signal
from execution.services.engine_types import EngineDecision
from execution.services.historical_backtest import parse_csv, run_simulation, validate_config


def payload(**overrides):
    data = {
        "strategy": "trend_pullback", "timeframe": "1m", "quantity": "1",
        "contract_size": "1", "point_size": "0.01", "initial_balance": "10000",
        "spread_points": "10", "slippage_points": "1", "commission_per_lot": "0.2",
        "warmup": 30,
    }
    return {**data, **overrides}


def csv_data(count=35):
    at = datetime(2025, 1, 6, 10, tzinfo=timezone.utc)
    return "time,open,high,low,close,tick_volume\n" + "\n".join(
        f"{(at + timedelta(minutes=i)).isoformat()},100,102,98,100,100"
        for i in range(count)
    )


class HistoricalReplayTests(SimpleTestCase):
    @patch("execution.connectors.mt5.mt5")
    def test_specification_read_never_initializes_or_changes_terminal(self, api):
        from execution.connectors.mt5 import MT5Connector
        from execution.connectors.base import ConnectorError
        account = SimpleNamespace(mt5_login="123", mt5_server="Demo")
        api.account_info.return_value = SimpleNamespace(login=123, server="Demo")
        connector = MT5Connector()
        self.assertIs(connector.current_symbol_info_for_account(account, "BTCUSDm"), api.symbol_info.return_value)
        api.symbol_info.assert_called_once_with("BTCUSDm")
        api.account_info.return_value = SimpleNamespace(login=456, server="Demo")
        with self.assertRaises(ConnectorError):
            connector.current_symbol_info_for_account(account, "BTCUSDm")
        api.account_info.side_effect = [SimpleNamespace(login=123, server="Demo"), SimpleNamespace(login=456, server="Demo")]
        with self.assertRaises(ConnectorError):
            connector.current_symbol_info_for_account(account, "BTCUSDm")
        for method in (api.initialize, api.login, api.shutdown, api.symbol_select, api.order_send):
            method.assert_not_called()

    def simulate(self, config=None, change=None, direction="buy"):
        cfg = validate_config(config or payload())
        bars, dataset = parse_csv(csv_data(), cfg)
        if change:
            change(bars)
        calls = []

        def runner(window):
            calls.append(window)
            if len(calls) > 1:
                return EngineDecision(action="skip", reason="no_pattern")
            return EngineDecision(action="open", direction=direction, score=1,
                                  sl=Decimal("99") if direction == "buy" else Decimal("101"),
                                  tp=Decimal("101") if direction == "buy" else Decimal("99"))

        result = run_simulation(bars, cfg, dataset, "EURUSD", runner=runner)
        return result, calls, bars

    def test_next_open_and_net_costs_are_reproducible(self):
        result, calls, bars = self.simulate()
        trade = result["trades"][0]
        self.assertEqual(calls[0][-1]["time"], bars[29]["time"])
        self.assertEqual(trade["entry_time"], bars[30]["time"].isoformat())
        self.assertEqual(Decimal(trade["entry_price"]), Decimal("100.11"))
        self.assertEqual(Decimal(trade["pnl"]), Decimal("-1.32"))
        self.assertEqual(Decimal(trade["spread_cost"]), Decimal("0.10"))
        self.assertEqual(Decimal(trade["slippage_cost"]), Decimal("0.02"))
        self.assertEqual(result["summary"]["trades"], 1)
        self.assertEqual(Decimal(result["summary"]["ending_balance"]), Decimal("9998.68"))
        self.assertEqual(Decimal(result["summary"]["max_drawdown_pct"]), Decimal("0.0132"))
        self.assertEqual(result["skip_reasons"], [{"reason": "no_pattern", "count": 4}])

    def test_same_bar_policy_changes_the_ambiguous_exit(self):
        stop, _, _ = self.simulate()
        target, _, _ = self.simulate(payload(same_bar_policy="target_first"))
        self.assertEqual(stop["trades"][0]["reason"], "stop_loss")
        self.assertEqual(target["trades"][0]["reason"], "take_profit")

    def test_real_momentum_strategy_generates_and_closes_a_trade(self):
        cfg = validate_config(payload(strategy="momentum_ignition", spread_points="0", slippage_points="0"))
        bars, dataset = parse_csv(csv_data(), cfg)
        for bar in bars:
            bar.update(open=Decimal("100"), high=Decimal("100.02"), low=Decimal("99.98"), close=Decimal("100"))
        for i in range(25, 29):
            price = Decimal("100") + Decimal(i - 24) * Decimal("0.05")
            bars[i].update(open=price, high=price + Decimal("0.02"), low=price - Decimal("0.02"), close=price)
        bars[29].update(open=Decimal("100.17"), high=Decimal("100.20"), low=Decimal("100.14"), close=Decimal("100.17"))
        bars[30].update(open=Decimal("100.18"), high=Decimal("100.40"), low=Decimal("100.17"), close=Decimal("100.35"))
        result = run_simulation(bars, cfg, dataset, "BTCUSDm")
        self.assertGreaterEqual(result["summary"]["trades"], 1)
        self.assertEqual(result["trades"][0]["reason"], "take_profit")
        self.assertEqual(result["trades"][0]["strategy"], "momentum_ignition")

    def test_short_stops_use_ask_prices(self):
        def change(bars):
            bars[30]["high"] = Decimal("100.95")
            bars[30]["low"] = Decimal("99.9")
        result, _, _ = self.simulate(change=change, direction="sell")
        trade = result["trades"][0]
        self.assertEqual(trade["reason"], "stop_loss")
        self.assertEqual(Decimal(trade["exit_price"]), Decimal("101.01"))

    def test_stop_gap_fills_at_worse_open(self):
        def change(bars):
            bars[30].update(high=Decimal("100.5"), low=Decimal("99.5"))
            bars[31].update(open=Decimal("95"), high=Decimal("96"), low=Decimal("94"))
        result, _, _ = self.simulate(change=change)
        self.assertEqual(result["trades"][0]["reason"], "stop_gap")
        self.assertEqual(Decimal(result["trades"][0]["exit_price"]), Decimal("94.99"))

    def test_invalid_stops_after_entry_gap_are_rejected(self):
        def change(bars):
            bars[30]["open"] = Decimal("105")
        result, _, _ = self.simulate(change=change)
        self.assertEqual(result["trades"], [])
        self.assertIn({"reason": "invalid_stops_at_next_open", "count": 1}, result["skip_reasons"])
        self.assertIsNone(result["summary"]["profit_factor"])

    def test_open_position_is_closed_at_end_of_data(self):
        def change(bars):
            for bar in bars[30:]:
                bar.update(high=Decimal("100.5"), low=Decimal("99.5"))
        result, _, bars = self.simulate(change=change)
        self.assertEqual(result["trades"][0]["reason"], "end_of_data")
        self.assertEqual(result["trades"][0]["exit_time"], bars[-1]["time"].isoformat())
        self.assertEqual(result["equity"][-1]["balance"], result["equity"][-1]["equity"])

    def test_rejects_nonfinite_costs_and_bad_dates(self):
        for overrides in ({"point_size": "NaN"}, {"quantity": "Infinity"}, {"spread_points": "-1"},
                          {"start_date": "2025-02-01", "end_date": "2025-01-01"}):
            with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                validate_config(payload(**overrides))

    def test_rejects_duplicate_and_inconsistent_candles(self):
        cfg = validate_config(payload())
        rows = csv_data().splitlines()
        for source in ("\n".join([*rows, rows[-1]]), csv_data().replace(",100,102,98,100,", ",100,97,98,100,", 1)):
            with self.assertRaises(ValueError):
                parse_csv(source, cfg)

    def test_mt5_tab_export_converts_broker_offset_to_utc(self):
        rows = ["<DATE>\t<TIME>\t<OPEN>\t<HIGH>\t<LOW>\t<CLOSE>\t<TICKVOL>"]
        at = datetime(2025, 1, 6, 12)
        for i in range(35):
            stamp = at + timedelta(minutes=i)
            rows.append(f"{stamp:%Y.%m.%d}\t{stamp:%H:%M:%S}\t100\t102\t98\t100\t50")
        bars, _ = parse_csv("\n".join(rows), validate_config(payload(csv_utc_offset_minutes=120)))
        self.assertEqual(bars[0]["time"].hour, 10)
        self.assertEqual(bars[0]["tick_volume"], 50)

    def test_missing_volume_and_wrong_timeframe_are_rejected(self):
        no_volume = "\n".join(",".join(row.split(",")[:-1]) for row in csv_data().splitlines())
        with self.assertRaisesRegex(ValueError, "needs tick_volume"):
            parse_csv(no_volume, validate_config(payload(strategy="momentum_ignition")))
        with self.assertRaisesRegex(ValueError, "interval"):
            parse_csv(csv_data(), validate_config(payload(timeframe="5m")))

    def test_date_range_and_future_candles_are_checked(self):
        with self.assertRaisesRegex(ValueError, "no tradable"):
            parse_csv(csv_data(), validate_config(payload(start_date="2025-02-01")))
        with self.assertRaisesRegex(ValueError, "future or has not completed"):
            parse_csv(csv_data(), validate_config(payload()), now=datetime(2025, 1, 6, 10, 30, tzinfo=timezone.utc))


class HistoricalBacktestApiTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("backtest-owner")
        self.other = get_user_model().objects.create_user("backtest-other")
        asset, _ = Asset.objects.get_or_create(symbol="EURUSD")
        self.bot = Bot.objects.create(owner=self.user, name="Replay bot", asset=asset)
        self.client.force_login(self.user)

    def test_preview_suggests_csv_dates_after_warmup_without_saving(self):
        source = csv_data(1450)
        response = self.client.post("/api/personal/backtests/preview/", {
            "csv": source, "warmup": 100, "timeframe": "1m",
        }, content_type="application/json")
        self.assertEqual(response.status_code, 200, response.content)
        data = response.json()
        self.assertEqual(data["start_date"], "2025-01-06")
        self.assertEqual(data["end_date"], "2025-01-07")
        self.assertEqual(data["first_tradable_at"], "2025-01-06T11:40:00+00:00")
        self.assertEqual(data["bars"], 1450)
        self.assertEqual(HistoricalBacktest.objects.count(), 0)

    def test_preview_honors_timezone_and_warmup_crossing_midnight(self):
        at = datetime(2025, 1, 6, 23, 45)
        source = "time,open,high,low,close,tick_volume\n" + "\n".join(
            f"{(at + timedelta(minutes=i)).isoformat()},100,102,98,100,100" for i in range(150)
        )
        response = self.client.post("/api/personal/backtests/preview/", {
            "csv": source, "warmup": 30, "csv_utc_offset_minutes": 0,
        }, content_type="application/json")
        self.assertEqual(response.json()["start_date"], "2025-01-07")
        response = self.client.post("/api/personal/backtests/preview/", {
            "csv": source, "warmup": 30, "csv_utc_offset_minutes": 120,
        }, content_type="application/json")
        self.assertEqual(response.json()["start_date"], "2025-01-06")

    def test_preview_rejects_invalid_csv_and_requires_authentication(self):
        response = self.client.post("/api/personal/backtests/preview/", {
            "csv": csv_data(), "warmup": 100,
        }, content_type="application/json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("102", response.json()["detail"])
        self.client.logout()
        response = self.client.post("/api/personal/backtests/preview/", {}, content_type="application/json")
        self.assertIn(response.status_code, (401, 403))

    def attach_account(self):
        self.bot.broker_account = BrokerAccount.objects.create(
            owner=self.user, name="Backtest demo", broker="mt5", connector="mt5_local",
            account_ref="backtest-demo", mt5_login="123", mt5_server="Demo",
        )
        self.bot.save(update_fields=["broker_account"])

    @patch("execution.connectors.mt5.MT5Connector.current_symbol_info_for_account")
    def test_defaults_use_broker_specs_with_source_and_do_not_guess_costs(self, lookup):
        self.attach_account()
        lookup.return_value = SimpleNamespace(
            trade_contract_size=100000, point=0.00001, currency_profit="USD", spread=12,
        )
        response = self.client.get(f"/api/personal/backtests/defaults/{self.bot.id}/")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["source"], "broker_snapshot")
        self.assertEqual(data["values"], {
            "contract_size": "100000", "point_size": "0.00001", "currency": "USD", "spread_points": "12",
        })
        lookup.assert_called_once_with(self.bot.broker_account, "EURUSD")
        self.assertIn("not a historical average", data["message"])
        self.assertEqual(HistoricalBacktest.objects.count(), 0)

    @patch("execution.connectors.mt5.MT5Connector.current_symbol_info_for_account")
    def test_defaults_fail_safely_and_do_not_read_another_users_account(self, lookup):
        self.attach_account()
        lookup.side_effect = RuntimeError("private connection error")
        response = self.client.get(f"/api/personal/backtests/defaults/{self.bot.id}/")
        self.assertEqual(response.json()["values"], {})
        self.assertNotIn("private connection error", response.content.decode())
        lookup.reset_mock()
        self.bot.broker_account.owner = self.other
        self.bot.broker_account.save(update_fields=["owner"])
        self.client.get(f"/api/personal/backtests/defaults/{self.bot.id}/")
        lookup.assert_not_called()
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(f"/api/personal/backtests/defaults/{self.bot.id}/").status_code, 404)

    @patch("execution.connectors.mt5.MT5Connector.current_symbol_info_for_account")
    def test_defaults_reuse_only_own_completed_same_symbol_run(self, lookup):
        self.attach_account()
        HistoricalBacktest.objects.create(
            owner=self.other, bot=self.bot, symbol="EURUSD", status="completed",
            config={"contract_size": "999"},
        )
        HistoricalBacktest.objects.create(
            owner=self.user, bot=self.bot, symbol="EURUSD", status="completed",
            config={"contract_size": "100000", "point_size": "0.00001", "commission_per_lot": "7"},
        )
        HistoricalBacktest.objects.create(
            owner=self.user, bot=self.bot, symbol="BTCUSDm", status="completed",
            config={"contract_size": "1"},
        )
        data = self.client.get(f"/api/personal/backtests/defaults/{self.bot.id}/").json()
        self.assertEqual(data["source"], "saved_backtest")
        self.assertEqual(data["values"]["commission_per_lot"], "7")
        self.assertEqual(data["values"]["contract_size"], "100000")
        lookup.assert_not_called()

    def test_run_persists_results_without_creating_trading_records(self):
        before = (Signal.objects.count(), Order.objects.count())
        response = self.client.post("/api/personal/backtests/", {
            **payload(), "bot_id": self.bot.id, "csv": csv_data(), "source_name": "history.csv",
        }, content_type="application/json")
        self.assertEqual(response.status_code, 201, response.content)
        record = response.json()
        self.assertEqual(record["status"], "completed")
        self.assertIn("equity", record["result"])
        self.assertNotIn("source_csv", record)
        self.assertEqual((Signal.objects.count(), Order.objects.count()), before)
        self.assertEqual(HistoricalBacktest.objects.get(id=record["id"]).source_csv, csv_data())
        history = self.client.get("/api/personal/backtests/").json()
        self.assertEqual(history["count"], 1)
        self.assertNotIn("result", history["results"][0])
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(f"/api/personal/backtests/{record['id']}/").status_code, 404)
        self.assertEqual(self.client.get("/api/personal/backtests/").json()["count"], 0)

    def test_options_and_submission_are_owner_scoped(self):
        self.client.force_login(self.other)
        self.assertEqual(self.client.get("/api/personal/backtests/options/").json()["bots"], [])
        response = self.client.post("/api/personal/backtests/", {
            **payload(), "bot_id": self.bot.id, "csv": csv_data(),
        }, content_type="application/json")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(HistoricalBacktest.objects.count(), 0)

    def test_validation_errors_are_actionable(self):
        response = self.client.post("/api/personal/backtests/", {
            **payload(point_size="NaN"), "bot_id": self.bot.id, "csv": csv_data(),
        }, content_type="application/json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("point_size", response.json()["detail"])
        self.assertEqual(HistoricalBacktest.objects.count(), 0)

    @patch("execution.backtesting_api.run_simulation", side_effect=RuntimeError("internal secret"))
    def test_failure_is_saved_without_exposing_internal_error(self, _run):
        response = self.client.post("/api/personal/backtests/", {
            **payload(), "bot_id": self.bot.id, "csv": csv_data(),
        }, content_type="application/json")
        self.assertEqual(response.status_code, 500)
        self.assertNotIn("internal secret", response.content.decode())
        self.assertEqual(HistoricalBacktest.objects.get().status, "failed")
