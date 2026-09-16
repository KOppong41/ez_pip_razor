from datetime import datetime, timedelta, timezone
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase

from bots.models import Asset, Bot
from brokers.models import BrokerAccount
from execution.models import BrokerPosition, Decision, Execution, Order, PerformanceBaseline, Signal, TradeLog


class PerformanceHistoryTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="history-owner")
        self.other = get_user_model().objects.create_user(username="history-other")
        self.account = BrokerAccount.objects.create(owner=self.user, name="History", broker="mt5", connector="mt5_local", account_ref="history")
        self.bot = Bot.objects.create(owner=self.user, broker_account=self.account, name="Gold baseline", asset=Asset.objects.get(symbol="XAUUSDm"), asset_preset_version_applied=3)
        self.at = datetime(2026, 9, 14, 15, tzinfo=timezone.utc)
        self.client.force_login(self.user)
        self.sequence = 100

    def outcome(self, pnl, *, symbol="XAUUSDm", strategy="trend_pullback", opened=None, closed=None, snapshot=True, overlay=False, ticket=None, position_status="closed"):
        self.sequence += 1
        ticket = ticket or self.sequence
        signal = Signal.objects.create(bot=self.bot, source="test", symbol=symbol, timeframe="5m", direction="buy", dedupe_key=f"history-{self.sequence}", payload={"strategy": strategy})
        decision = Decision.objects.create(bot=self.bot, signal=signal, action="open", params={"is_opposite_scalp": overlay})
        entry = Order.objects.create(bot=self.bot, broker_account=self.account, decision=decision, intent="entry", symbol=symbol,
                                     side="buy", qty=Decimal(".1"), client_order_id=f"history-entry-{self.sequence}", broker_position_ticket=ticket)
        if not snapshot:
            Order.objects.filter(pk=entry.pk).update(performance_context={})
        position = BrokerPosition.objects.create(broker_account=self.account, bot=self.bot, originating_order=entry,
                    broker_position_ticket=ticket, symbol=symbol, side="buy", open_price=100, volume=0,
                    ownership="ez_trade", status=position_status, opened_at=opened or self.at, closed_at=closed or self.at + timedelta(hours=1))
        exit_order = Order.objects.create(bot=self.bot, broker_account=self.account, intent="exit", symbol=symbol,
                    side="sell", qty=Decimal(".1"), client_order_id=f"history-exit-{self.sequence}", broker_position_ticket=ticket, status="filled")
        log = TradeLog.objects.create(order=exit_order, bot=self.bot, broker_account=self.account, symbol=symbol, side="sell", qty=Decimal(".1"),
                    price=100, exit_price=101, pnl=Decimal(pnl), status="win", broker_ticket=ticket, closed_at=closed or self.at + timedelta(hours=1))
        return entry, position, log

    def history(self, **params):
        response = self.client.get('/api/personal/history/', {"broker_account_id": self.account.pk, **params})
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def test_filters_and_strategy_breakdown_exclude_crypto_from_gold(self):
        self.outcome("2")
        self.outcome("-1", strategy="momentum_ignition", overlay=True)
        self.outcome("-20", symbol="BTCUSDm")
        self.outcome("-10", symbol="ETHUSDm")
        self.outcome("3", symbol="EURUSDm")
        gold = self.history(market="gold")
        self.assertEqual(gold["summary"]["total_trades"], 2)
        self.assertEqual(Decimal(str(gold["summary"]["net_profit"])), 1)
        self.assertEqual(Decimal(str(gold["summary"]["profit_factor"])), 2)
        self.assertEqual(len(gold["strategy_breakdown"]), 5)
        self.assertEqual(gold["opposite_scalp"]["total_trades"], 1)
        self.assertEqual(self.history(market="btc")["opposite_scalp"]["total_trades"], 0)
        self.assertEqual(self.history(market="forex")["summary"]["total_trades"], 1)
        self.assertEqual(self.history(symbol="GOLD", strategy="momentum_ignition", preset_version=3, bot_id=self.bot.pk)["summary"]["total_trades"], 1)

    def test_preset_is_frozen_at_entry_and_missing_history_stays_unknown(self):
        entry, _, _ = self.outcome("2")
        self.outcome("-1", snapshot=False)
        self.bot.asset_preset_version_applied = 4
        self.bot.save()
        entry.status = "filled"
        entry.save()
        self.assertEqual(entry.performance_context["preset_version"], 3)
        self.assertEqual(self.history(preset_version=3)["summary"]["total_trades"], 1)
        unknown = self.history(preset_version="unknown")
        self.assertEqual(unknown["summary"]["total_trades"], 1)
        self.assertEqual(unknown["trades"][0]["strategy"], "trend_pullback")
        self.assertEqual(self.history(preset_version=4)["summary"]["total_trades"], 0)

    def test_baseline_excludes_preexisting_entries_and_preserves_every_record(self):
        self.outcome("-10", opened=self.at - timedelta(hours=1))
        self.outcome("2", opened=self.at)
        self.outcome("-5", symbol="BTCUSDm", opened=self.at)
        before = TradeLog.objects.count()
        response = self.client.post('/api/personal/history/baselines/', {"broker_account_id": self.account.pk,
                    "name": "Gold demo v3", "started_at": self.at.isoformat(), "filters": {"market": "gold", "bot_id": self.bot.pk}}, content_type="application/json")
        self.assertEqual(response.status_code, 201, response.content)
        result = self.history(baseline_id=response.json()["id"])
        self.assertEqual(result["summary"]["total_trades"], 1)
        self.assertEqual(Decimal(str(result["summary"]["net_profit"])), 2)
        self.assertEqual(TradeLog.objects.count(), before)
        self.assertEqual(self.history()["summary"]["total_trades"], 3)
        self.assertEqual(self.history(baseline_id=response.json()["id"], market="btc")["summary"]["total_trades"], 0)

    def test_unknown_entry_timestamp_does_not_enter_baseline(self):
        _, position, _ = self.outcome("1")
        position.opened_at = None
        position.save()
        baseline = PerformanceBaseline.objects.create(owner=self.user, broker_account=self.account, name="No inferred time", started_at=self.at)
        self.assertEqual(self.history(baseline_id=baseline.pk)["summary"]["total_trades"], 0)

    def test_partial_exits_are_combined_and_open_positions_excluded(self):
        _, position, log = self.outcome("2")
        self.sequence += 1
        order = Order.objects.create(bot=self.bot, broker_account=self.account, intent="exit", symbol=log.symbol, side="sell",
                                    qty=Decimal(".05"), client_order_id="second-partial", broker_position_ticket=position.broker_position_ticket)
        TradeLog.objects.create(order=order, bot=self.bot, broker_account=self.account, symbol=log.symbol, side="sell", qty=Decimal(".05"), pnl=-3, closed_at=self.at + timedelta(hours=2))
        self.outcome("5", position_status="open")
        result = self.history()
        self.assertEqual(result["summary"]["total_trades"], 1)
        self.assertEqual(result["summary"]["losses"], 1)
        self.assertEqual(result["trades"][0]["exit_records"], 2)
        self.assertEqual(Decimal(str(result["summary"]["net_profit"])), -1)
        self.assertEqual(result["data_quality"]["open_position_exit_records_omitted"], 1)

    def test_utc_date_range_includes_whole_end_day_and_ignores_provisional_logs(self):
        _, _, log = self.outcome("1", closed=self.at.replace(hour=23, minute=59))
        self.outcome("2", closed=self.at + timedelta(days=1))
        TradeLog.objects.create(order=log.order, bot=self.bot, broker_account=self.account, symbol=log.symbol, side="sell", qty=1, status="filled")
        result = self.history(**{"from": "2026-09-14", "to": "2026-09-14"})
        self.assertEqual(result["summary"]["total_trades"], 1)
        self.assertEqual(result["trades"][0]["id"], log.pk)

    def test_totals_cover_more_than_1000_rows_and_pages_do_not_change_totals(self):
        orders = Order.objects.bulk_create([Order(bot=self.bot, broker_account=self.account, owner=self.user, intent="exit", symbol="XAUUSDm", side="sell", qty=1,
                                                client_order_id=f"large-history-{i}") for i in range(1005)])
        TradeLog.objects.bulk_create([TradeLog(order=order, bot=self.bot, broker_account=self.account, symbol="XAUUSDm", side="sell", qty=1,
                                               pnl=1, status="win", closed_at=self.at) for order in orders])
        result = self.history(page=6, page_size=200)
        self.assertEqual(result["summary"]["total_trades"], 1005)
        self.assertEqual(len(result["trades"]), 5)
        self.assertEqual(Decimal(str(result["summary"]["net_profit"])), 1005)
        self.assertIsNone(result["trades"][0]["bot_id"])

    def test_account_and_baseline_authorization_and_input_validation(self):
        other_account = BrokerAccount.objects.create(owner=self.other, name="Other", broker="mt5", connector="mt5_local", account_ref="other")
        foreign = PerformanceBaseline.objects.create(owner=self.other, broker_account=other_account, name="Private", started_at=self.at)
        for params in ({"broker_account_id": other_account.pk}, {"baseline_id": foreign.pk}, {"page": 0}, {"page_size": 201}, {"from": "garbage"}, {"from": "2026-09-15T12:00:00"}, {"preset_version": "bad"}, {"market": "bad"}):
            response = self.client.get('/api/personal/history/', {"broker_account_id": self.account.pk, **params})
            self.assertEqual(response.status_code, 400, (params, response.content))
        response = self.client.post('/api/personal/history/baselines/', {"broker_account_id": other_account.pk, "name": "Invalid"}, content_type="application/json")
        self.assertEqual(response.status_code, 400)
        self.client.logout()
        self.assertEqual(self.client.get('/api/personal/history/').status_code, 401)

    def test_empty_sample_has_undefined_win_rate_and_profit_factor(self):
        result = self.history(market="gold")
        self.assertEqual(result["summary"]["total_trades"], 0)
        self.assertIsNone(result["summary"]["win_rate"])
        self.assertIsNone(result["summary"]["profit_factor"])
        self.assertEqual(len(result["strategy_breakdown"]), 5)
        self.assertIn("3", result["options"]["preset_versions"])

    def test_multiple_entries_are_not_attributed_to_a_single_strategy(self):
        entry, position, _ = self.outcome("2")
        Order.objects.create(bot=self.bot, broker_account=self.account, intent="entry", symbol=entry.symbol,
                             side="buy", qty=1, client_order_id="additional-netting-entry", status="filled",
                             broker_position_ticket=position.broker_position_ticket)
        row = self.history()["trades"][0]
        self.assertEqual(row["strategy"], "mixed_entries")
        self.assertIsNone(row["preset_version"])
        self.assertEqual(self.history(strategy="trend_pullback")["summary"]["total_trades"], 0)

    def test_missing_exit_result_cannot_turn_partial_profit_into_a_full_win(self):
        entry, position, _ = self.outcome("2")
        Order.objects.create(bot=self.bot, broker_account=self.account, intent="exit", symbol=entry.symbol,
                             side="sell", qty=1, client_order_id="missing-final-result", status="filled",
                             broker_position_ticket=position.broker_position_ticket)
        result = self.history()
        self.assertEqual(result["summary"]["total_trades"], 0)
        self.assertEqual(result["data_quality"]["positions_with_missing_exit_results"], 1)
