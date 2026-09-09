from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace

from django.test import TestCase, override_settings
from django.utils import timezone

from bots.models import Asset, Bot
from brokers.models import BrokerAccount
from execution.models import (
    AccountRiskDay,
    BrokerPosition,
    Decision,
    Order,
    RiskPolicy,
    Signal,
)
from execution.services.daily_risk import risk_day_window
from execution.services.equity import update_equity_high_water
from execution.services.live_risk import RiskRejected, enforce_pretrade_risk


@override_settings(MAX_ORDER_LOT=Decimal("10"))
class LiveRiskTest(TestCase):
    """Contract tests for the final, authoritative pre-trade risk gate."""

    def setUp(self):
        self.account = BrokerAccount.objects.create(
            name="MT5 demo",
            broker="mt5",
            connector="mt5_local",
            account_ref="live-risk-test",
            is_active=True,
            is_verified=True,
        )
        self.asset, _ = Asset.objects.get_or_create(
            symbol="XRISKUSDm",
            defaults={"category": "indices", "min_qty": Decimal("0.01")},
        )
        self.policy = RiskPolicy.objects.create(
            broker_account=self.account,
            entries_enabled=True,
            max_daily_loss_pct=Decimal("100"),
            max_account_drawdown_pct=Decimal("100"),
            stop_after_daily_profit_pct=Decimal("0"),
            max_order_lot_size=Decimal("5"),
            max_total_open_positions=10,
            max_positions_per_symbol=10,
            max_aggregate_open_lots=Decimal("10"),
        )
        self.account_info = SimpleNamespace(
            trade_mode=0,
            equity=Decimal("10000"),
            balance=Decimal("10000"),
            margin=Decimal("0"),
            margin_free=Decimal("10000"),
            margin_level=Decimal("0"),
            currency="USD",
        )
        # A 1.00 price move at one lot costs 100 account-currency units.
        self.symbol_info = SimpleNamespace(
            point=Decimal("0.01"),
            digits=2,
            trade_stops_level=0,
            volume_min=Decimal("0.01"),
            volume_max=Decimal("10"),
            volume_step=Decimal("0.01"),
            trade_tick_size=Decimal("0.01"),
            trade_tick_value=Decimal("1"),
            trade_tick_value_loss=Decimal("1"),
            trade_contract_size=Decimal("100"),
        )
        self.tick = SimpleNamespace(bid=Decimal("100.00"), ask=Decimal("100.02"))
        self.connector = SimpleNamespace(
            calc_profit_for_account=lambda account, side, symbol, volume, entry, stop: (
                -abs(Decimal(str(entry)) - Decimal(str(stop)))
                * Decimal(str(volume))
                * Decimal("100")
            ),
            calc_margin_for_account=lambda account, side, symbol, volume, entry: (
                Decimal(str(volume)) * Decimal("10")
            ),
            history_deals_for_account=lambda *args: (),
            positions_for_account=lambda *args: (),
        )
        self._sequence = 0

    def _bot(self, suffix="one", **overrides):
        values = {
            "name": f"Risk bot {suffix}",
            "status": "active",
            "auto_trade": True,
            "engine_mode": "external",
            "broker_account": self.account,
            "asset": self.asset,
            "position_sizing_mode": "risk",
            "risk_per_trade_pct": Decimal("1"),
            "default_qty": Decimal("0.03"),
            "max_bot_lot_size": Decimal("5"),
            "risk_max_concurrent_positions": 5,
            "max_trades_per_day": 10,
            "trade_interval_minutes": 0,
            "max_spread_points": Decimal("10"),
            "allowed_deviation_points": 8,
        }
        values.update(overrides)
        return Bot.objects.create(**values)

    def _order(self, bot=None, *, suffix="entry", **overrides):
        self._sequence += 1
        bot = bot or self._bot(suffix=f"{suffix}-{self._sequence}")
        values = {
            "bot": bot,
            "broker_account": self.account,
            "client_order_id": f"risk-{suffix}-{self._sequence}",
            "intent": "entry",
            "symbol": self.asset.symbol,
            "side": "buy",
            "qty": bot.default_qty,
            "sl": Decimal("99.02"),
            "tp": Decimal("101.02"),
        }
        values.update(overrides)
        return Order.objects.create(**values)

    def _position(self, bot, ticket, *, symbol=None, volume="0.10", ownership="ez_trade"):
        return BrokerPosition.objects.create(
            broker_account=self.account,
            bot=bot if ownership == "ez_trade" else None,
            broker_position_ticket=ticket,
            ownership=ownership,
            symbol=symbol or self.asset.symbol,
            side="buy",
            volume=volume,
            open_price="100.00",
            status="open",
        )

    def _prior_entry(self, bot, suffix, *, submitted_at=None):
        return self._order(
            bot,
            suffix=suffix,
            status="filled",
            submitted_at=submitted_at or timezone.now(),
            filled_qty=Decimal("0.10"),
        )

    def _risk_day(self, starting_equity):
        window = risk_day_window(self.account)
        return AccountRiskDay.objects.create(
            broker_account=self.account,
            risk_date=window.risk_date,
            starting_balance=starting_equity,
            starting_equity=starting_equity,
            high_equity=starting_equity,
            first_snapshot_at=timezone.now(),
            baseline_source="manual",
            baseline_locked=True,
        )

    def _enforce(self, order):
        return enforce_pretrade_risk(
            order,
            self.connector,
            self.tick,
            self.symbol_info,
            self.account_info,
            broker_positions=(),
        )

    def _assert_rejected(self, code, order):
        with self.assertRaises(RiskRejected) as raised:
            self._enforce(order)
        rejection = raised.exception
        self.assertEqual(rejection.code, code)
        self.assertEqual(rejection.context["bot_id"], order.bot_id)
        self.assertEqual(rejection.context["broker_account_id"], self.account.id)
        self.assertEqual(rejection.context["symbol"], order.symbol)
        self.assertIn("timestamp", rejection.context)
        self.assertEqual(rejection.as_dict()["code"], code)
        return rejection

    # Position sizing and broker specifications.

    def test_fixed_lot_uses_requested_default_quantity(self):
        bot = self._bot(
            position_sizing_mode="fixed",
            default_qty=Decimal("0.30"),
            max_bot_lot_size=Decimal("0.50"),
        )

        result = self._enforce(self._order(bot, qty=bot.default_qty))

        self.assertEqual(result.volume, Decimal("0.30"))
        self.assertEqual(result.risk_amount, Decimal("30.0000"))

    def test_risk_based_volume_uses_equity_stop_and_broker_loss(self):
        bot = self._bot(
            position_sizing_mode="risk",
            risk_per_trade_pct=Decimal("1"),
            default_qty=Decimal("0.03"),
            max_bot_lot_size=Decimal("2"),
        )

        result = self._enforce(self._order(bot))

        self.assertEqual(result.loss_per_lot, Decimal("100.00"))
        self.assertEqual(result.risk_amount, Decimal("100"))
        self.assertEqual(result.volume, Decimal("1"))
        self.assertNotEqual(result.volume, bot.default_qty)

    def test_risk_based_volume_is_floored_to_broker_step(self):
        bot = self._bot(risk_per_trade_pct=Decimal("0.137"))

        result = self._enforce(self._order(bot))

        self.assertEqual(result.volume, Decimal("0.13"))

    def test_risk_based_volume_is_safely_capped_by_strictest_lot_limit(self):
        bot = self._bot(
            risk_per_trade_pct=Decimal("1"),
            max_bot_lot_size=Decimal("0.50"),
        )
        self.policy.max_order_lot_size = Decimal("0.75")
        self.policy.save(update_fields=["max_order_lot_size"])

        result = self._enforce(self._order(bot))

        self.assertEqual(result.volume, Decimal("0.50"))

    def test_missing_stop_rejects_risk_based_sizing(self):
        self._assert_rejected("RISK_STOP_LOSS_REQUIRED", self._order(sl=None))

    def test_missing_tick_spec_rejects_risk_based_sizing(self):
        self.symbol_info.trade_tick_value_loss = 0
        self.symbol_info.trade_tick_value = 0

        self._assert_rejected("RISK_SYMBOL_SPEC_UNAVAILABLE", self._order())

    # Bot-scoped risk and cadence.

    def test_bot_maximum_positions_counts_only_that_bot(self):
        bot = self._bot(risk_max_concurrent_positions=1)
        other = self._bot(suffix="other")
        self._position(other, 1001)

        self._enforce(self._order(bot, suffix="other-does-not-block"))
        self._position(bot, 1002)

        self._assert_rejected("BOT_MAX_POSITIONS", self._order(bot, suffix="self-blocks"))

    def test_bot_daily_trade_limit_does_not_count_another_bot(self):
        bot = self._bot(max_trades_per_day=1)
        other = self._bot(suffix="other")
        self._prior_entry(other, "other-prior")

        self._enforce(self._order(bot, suffix="first"))
        self._prior_entry(bot, "own-prior")

        self._assert_rejected("BOT_DAILY_TRADE_LIMIT", self._order(bot, suffix="second"))

    def test_bot_minimum_interval(self):
        bot = self._bot(trade_interval_minutes=15)
        self._prior_entry(bot, "recent", submitted_at=timezone.now() - timedelta(minutes=5))

        self._assert_rejected("BOT_MIN_TRADE_INTERVAL", self._order(bot))

    def test_bot_minimum_signal_score(self):
        bot = self._bot(decision_min_score=Decimal("0.80"))
        signal = Signal.objects.create(
            bot=bot,
            source="test",
            symbol=self.asset.symbol,
            direction="buy",
            dedupe_key="low-score-risk",
        )
        decision = Decision.objects.create(
            bot=bot,
            signal=signal,
            action="open",
            score=0.79,
        )

        self._assert_rejected("BOT_SIGNAL_SCORE", self._order(bot, decision=decision))

    def test_bot_max_spread(self):
        bot = self._bot(max_spread_points=Decimal("1"))

        rejection = self._assert_rejected("BOT_MAX_SPREAD", self._order(bot))

        self.assertEqual(rejection.context["current_spread"], "2")
        self.assertEqual(Decimal(rejection.context["spread_limit"]), Decimal("1"))

    def test_live_execution_requires_bot_permission(self):
        self.account_info.trade_mode = 2
        bot = self._bot(allow_live_account_execution=False)

        self._assert_rejected("BOT_LIVE_EXECUTION_DISABLED", self._order(bot))

    def test_fixed_volume_over_bot_maximum_is_rejected(self):
        bot = self._bot(
            position_sizing_mode="fixed",
            default_qty=Decimal("0.20"),
            max_bot_lot_size=Decimal("0.20"),
        )

        self._assert_rejected("BOT_MAX_LOT", self._order(bot, qty=Decimal("0.30")))

    # Account capital and aggregate exposure.

    def test_daily_loss_blocks_every_bot_on_account(self):
        self._risk_day(Decimal("10000"))
        self.account_info.equity = Decimal("9800")
        self.account_info.balance = Decimal("9800")
        self.policy.max_daily_loss_pct = Decimal("1")
        self.policy.save(update_fields=["max_daily_loss_pct"])

        for bot in (self._bot("a"), self._bot("b")):
            self._assert_rejected("ACCOUNT_DAILY_LOSS", self._order(bot))

    def test_persistent_drawdown_blocks_every_bot_on_account(self):
        self.policy.equity_high_water = Decimal("10000")
        self.policy.max_account_drawdown_pct = Decimal("5")
        self.policy.save(update_fields=["equity_high_water", "max_account_drawdown_pct"])
        self.account_info.equity = Decimal("9400")
        self.account_info.balance = Decimal("9400")

        for bot in (self._bot("a"), self._bot("b")):
            self._assert_rejected("ACCOUNT_MAX_DRAWDOWN", self._order(bot))
        self.policy.refresh_from_db()
        self.assertEqual(self.policy.equity_high_water, Decimal("10000"))

    def test_daily_profit_lock_blocks_every_bot_on_account(self):
        self._risk_day(Decimal("10000"))
        self.account_info.equity = Decimal("10200")
        self.account_info.balance = Decimal("10200")
        self.policy.stop_after_daily_profit_pct = Decimal("1")
        self.policy.save(update_fields=["stop_after_daily_profit_pct"])

        for bot in (self._bot("a"), self._bot("b")):
            self._assert_rejected("ACCOUNT_PROFIT_LOCK", self._order(bot))

    def test_zero_capital_thresholds_disable_their_policy(self):
        self._risk_day(Decimal("10000"))
        self.account_info.equity = Decimal("9000")
        self.account_info.balance = Decimal("9000")
        self.policy.equity_high_water = Decimal("10000")
        self.policy.max_daily_loss_pct = 0
        self.policy.max_account_drawdown_pct = 0
        self.policy.stop_after_daily_profit_pct = 0
        self.policy.save(
            update_fields=[
                "equity_high_water",
                "max_daily_loss_pct",
                "max_account_drawdown_pct",
                "stop_after_daily_profit_pct",
            ]
        )

        result = self._enforce(self._order())

        self.assertGreater(result.volume, 0)

    def test_account_maximum_order_lot_beats_looser_bot_limit(self):
        bot = self._bot(
            position_sizing_mode="fixed",
            default_qty=Decimal("0.30"),
            max_bot_lot_size=Decimal("0.50"),
        )
        self.policy.max_order_lot_size = Decimal("0.20")
        self.policy.save(update_fields=["max_order_lot_size"])

        self._assert_rejected("ACCOUNT_MAX_ORDER_LOT", self._order(bot, qty=Decimal("0.30")))

    def test_bot_maximum_lot_beats_looser_account_limit(self):
        bot = self._bot(
            position_sizing_mode="fixed",
            default_qty=Decimal("0.20"),
            max_bot_lot_size=Decimal("0.20"),
        )
        self.policy.max_order_lot_size = Decimal("0.50")
        self.policy.save(update_fields=["max_order_lot_size"])

        self._assert_rejected("BOT_MAX_LOT", self._order(bot, qty=Decimal("0.30")))

    def test_account_total_positions_aggregates_multiple_bots(self):
        bot_a = self._bot("a")
        bot_b = self._bot("b")
        bot_c = self._bot("c")
        self.policy.max_total_open_positions = 2
        self.policy.save(update_fields=["max_total_open_positions"])
        self._position(bot_a, 2001, symbol="AUSD")
        self._position(bot_b, 2002, symbol="BUSD")

        self._assert_rejected("ACCOUNT_MAX_POSITIONS", self._order(bot_c))

    def test_account_symbol_limit_aggregates_multiple_bots(self):
        bot_a = self._bot("a")
        bot_b = self._bot("b")
        bot_c = self._bot("c")
        self.policy.max_positions_per_symbol = 2
        self.policy.save(update_fields=["max_positions_per_symbol"])
        self._position(bot_a, 3001)
        self._position(bot_b, 3002)

        self._assert_rejected("ACCOUNT_SYMBOL_POSITION_LIMIT", self._order(bot_c))

    def test_account_aggregate_lots_includes_all_bot_owned_positions(self):
        bot_a = self._bot("a")
        bot_b = self._bot("b")
        bot_c = self._bot(
            "c",
            position_sizing_mode="fixed",
            default_qty=Decimal("0.20"),
            max_bot_lot_size=Decimal("1"),
        )
        self.policy.max_aggregate_open_lots = Decimal("1.00")
        self.policy.save(update_fields=["max_aggregate_open_lots"])
        self._position(bot_a, 4001, volume="0.40")
        self._position(bot_b, 4002, volume="0.50")

        rejection = self._assert_rejected(
            "ACCOUNT_MAX_AGGREGATE_LOTS",
            self._order(bot_c, qty=Decimal("0.20")),
        )
        self.assertEqual(Decimal(rejection.context["aggregate_lots"]), Decimal("0.90"))

    def test_manual_and_external_positions_do_not_consume_bot_owned_limits(self):
        bot = self._bot(
            position_sizing_mode="fixed",
            default_qty=Decimal("0.10"),
        )
        self.policy.max_total_open_positions = 1
        self.policy.max_positions_per_symbol = 1
        self.policy.max_aggregate_open_lots = Decimal("0.20")
        self.policy.save(
            update_fields=[
                "max_total_open_positions",
                "max_positions_per_symbol",
                "max_aggregate_open_lots",
            ]
        )
        self._position(bot, 5001, volume="5", ownership="manual")
        self._position(bot, 5002, volume="5", ownership="external")

        result = self._enforce(self._order(bot, qty=Decimal("0.10")))

        self.assertEqual(result.volume, Decimal("0.10"))

    def test_emergency_stop_has_machine_readable_rejection(self):
        self.policy.emergency_stop = True
        self.policy.save(update_fields=["emergency_stop"])

        self._assert_rejected("EMERGENCY_STOP_ACTIVE", self._order())

    # Reservation is the database-visible part of the cross-worker race guard.

    def test_recent_reservation_prevents_second_bot_bypassing_position_cap(self):
        bot_a = self._bot("a")
        bot_b = self._bot("b")
        self.policy.max_total_open_positions = 1
        self.policy.save(update_fields=["max_total_open_positions"])

        first = self._order(bot_a, suffix="first")
        self._enforce(first)
        first.refresh_from_db()
        self.assertIsNotNone(first.risk_reserved_at)

        self._assert_rejected("ACCOUNT_MAX_POSITIONS", self._order(bot_b, suffix="second"))

    def test_acknowledged_in_flight_reservation_still_consumes_position_capacity(self):
        bot_a = self._bot("a")
        bot_b = self._bot("b")
        self.policy.max_total_open_positions = 1
        self.policy.save(update_fields=["max_total_open_positions"])

        first = self._order(bot_a, suffix="first-ack")
        self._enforce(first)
        first.status = "ack"
        first.save(update_fields=["status"])

        self._assert_rejected("ACCOUNT_MAX_POSITIONS", self._order(bot_b, suffix="second-ack"))

    def test_recent_reservation_prevents_aggregate_lot_race(self):
        bot_a = self._bot(
            "a", position_sizing_mode="fixed", default_qty=Decimal("0.60")
        )
        bot_b = self._bot(
            "b", position_sizing_mode="fixed", default_qty=Decimal("0.50")
        )
        self.policy.max_aggregate_open_lots = Decimal("1.00")
        self.policy.save(update_fields=["max_aggregate_open_lots"])

        self._enforce(self._order(bot_a, suffix="first", qty=Decimal("0.60")))

        self._assert_rejected(
            "ACCOUNT_MAX_AGGREGATE_LOTS",
            self._order(bot_b, suffix="second", qty=Decimal("0.50")),
        )

    def test_stale_reservation_does_not_permanently_consume_capacity(self):
        bot_a = self._bot("a")
        bot_b = self._bot("b")
        self.policy.max_total_open_positions = 1
        self.policy.save(update_fields=["max_total_open_positions"])
        first = self._order(bot_a, suffix="stale")
        Order.objects.filter(pk=first.pk).update(
            risk_reserved_at=timezone.now() - timedelta(minutes=6)
        )

        result = self._enforce(self._order(bot_b, suffix="new"))

        self.assertGreater(result.volume, 0)

    # Legacy high-water behavior remains persistent and monotonic.

    def test_stale_writer_cannot_lower_equity_high_water(self):
        self.policy.equity_high_water = Decimal("1000")
        self.policy.save(update_fields=["equity_high_water"])
        stale_policy = RiskPolicy.objects.get(pk=self.policy.pk)

        update_equity_high_water(self.policy, Decimal("1200"))
        drawdown = update_equity_high_water(stale_policy, Decimal("1100"))

        self.policy.refresh_from_db()
        self.assertEqual(self.policy.equity_high_water, Decimal("1200"))
        self.assertEqual(drawdown.quantize(Decimal("0.01")), Decimal("8.33"))

    @override_settings(ACCOUNT_RISK_OPENING_SNAPSHOT_GRACE_SECONDS=-1)
    def test_live_entry_fails_closed_when_daily_baseline_is_unavailable(self):
        self.account_info.trade_mode = 2
        bot = self._bot(allow_live_account_execution=True)
        self.connector.history_deals_for_account = lambda *args: (_ for _ in ()).throw(
            RuntimeError("history unavailable")
        )

        self._assert_rejected(
            "ACCOUNT_DAILY_BASELINE_UNAVAILABLE",
            self._order(bot),
        )
