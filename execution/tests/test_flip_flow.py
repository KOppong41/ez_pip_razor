from decimal import Decimal
from datetime import timedelta
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from bots.models import Asset, Bot
from brokers.models import BrokerAccount
from execution.models import BrokerPosition, Signal, Position, Decision, Order
from execution.services.decision import make_decision_from_signal


@override_settings(
    DECISION_ALLOW_HEDGING=False,
    DECISION_FLIP_SCORE=0.2,  # lower for test
    DECISION_FLIP_COOLDOWN_MIN=0,
    DECISION_MAX_FLIPS_PER_DAY=5,
)
class FlipFlowTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="bob", password="pw")
        self.account = BrokerAccount.objects.create(
            name="Paper",
            broker="paper",
            connector="paper",
            account_ref="p1",
            owner=self.user,
        )
        self.asset, _ = Asset.objects.get_or_create(symbol="EURUSDm")
        self.bot = Bot.objects.create(
            name="Bot",
            owner=self.user,
            status="active",
            auto_trade=True,
            trading_schedule_enabled=False,
            broker_account=self.account,
            asset=self.asset,
            allowed_symbols=["EURUSDm"],
            risk_max_concurrent_positions=5,
        )

    def _signal(self, direction: str, score: float, key: str):
        return Signal.objects.create(
            bot=self.bot,
            source="engine_v1",
            symbol="EURUSDm",
            timeframe="5m",
            direction=direction,
            payload={"score": score, "sl": "1.09" if direction == "buy" else "1.11",
                     "tp": "1.12" if direction == "buy" else "1.08"},
            dedupe_key=key,
        )

    def _broker_position(self):
        return BrokerPosition.objects.create(
            broker_account=self.account,
            bot=self.bot,
            broker_position_ticket=12345,
            ownership="ez_trade",
            symbol="EURUSDm",
            side="sell",
            volume=Decimal("1.0"),
            open_price=Decimal("1.1000"),
            status="open",
        )

    @patch("execution.services.brokers.dispatch_place_order")
    def test_flip_creates_close_decision_and_order(self, dispatch_place_order):
        Position.objects.create(
            broker_account=self.account,
            symbol="EURUSDm",
            qty=Decimal("-1.0"),
            avg_price=Decimal("1.1000"),
            status="open",
        )
        broker_position = self._broker_position()

        def confirm_close(order):
            order.status = "filled"
            order.save(update_fields=["status"])
            broker_position.status = "closed"
            broker_position.save(update_fields=["status"])

        dispatch_place_order.side_effect = confirm_close
        sig = self._signal("buy", score=1.0, key="flip-1")
        decision = make_decision_from_signal(sig)
        self.assertEqual(decision.action, "open")
        self.assertEqual(decision.params["flip_state"], "pending")
        dispatch_place_order.assert_not_called()
        self.assertFalse(Decision.objects.filter(action="close").exists())

        # The selected replacement is created before dispatch can close anything.
        from execution.connectors.paper import PaperConnector
        from execution.services.fanout import fanout_orders
        replacement, _ = fanout_orders(decision, None)[0]
        PaperConnector().place_order(replacement)
        decision.refresh_from_db()
        self.assertEqual(decision.params["flip_state"], "completed")

        close_decisions = Decision.objects.filter(action="close", reason="flip_close")
        self.assertEqual(close_decisions.count(), 1)
        close_decision = close_decisions.first()
        self.assertEqual(close_decision.params.get("position_id"), Position.objects.first().id)

        close_orders = Order.objects.filter(symbol="EURUSDm", side="buy")
        self.assertEqual(close_orders.filter(intent="exit").count(), 1)

    @override_settings(DECISION_MAX_FLIPS_PER_DAY=1)
    def test_flip_blocked_by_daily_cap(self):
        Position.objects.create(
            broker_account=self.account,
            symbol="EURUSDm",
            qty=Decimal("-1.0"),
            avg_price=Decimal("1.1000"),
            status="open",
        )
        self._broker_position()
        Decision.objects.create(
            bot=self.bot,
            signal=self._signal("sell", score=1.0, key="seed"),
            action="close",
            reason="flip_close",
            score=1.0,
            params={},
        )

        sig = self._signal("buy", score=1.0, key="flip-2")
        decision = make_decision_from_signal(sig)
        from execution.connectors.paper import PaperConnector
        from execution.services.fanout import fanout_orders
        PaperConnector().place_order(fanout_orders(decision, None)[0][0])
        decision.refresh_from_db()
        self.assertEqual(decision.action, "ignore")
        self.assertEqual(decision.reason, "flip_preflight_rejected")
        self.assertEqual(decision.params["flip_error"], "flip_daily_cap")
        # No new flip decisions/orders because cap hit
        self.assertEqual(Decision.objects.filter(action="close", reason="flip_close").count(), 1)

    @patch("execution.services.flip.execute_flip")
    def test_rejected_replacement_does_not_close_primary(self, prepare_flip):
        from django.utils import timezone

        self.bot.allow_opposite_scalp = True
        self.bot.max_trades_per_day = 1
        self.bot.save()
        primary = Position.objects.create(
            broker_account=self.account, symbol="EURUSDm", qty=Decimal("-1"),
            avg_price=Decimal("1.1"), status="open",
        )
        entry = Order.objects.create(
            bot=self.bot, broker_account=self.account, symbol="EURUSDm", side="sell",
            qty=Decimal("1"), filled_qty=Decimal("1"), status="filled",
            intent="entry", client_order_id="prior-entry",
        )
        decision = make_decision_from_signal(self._signal("buy", 1.0, "daily-limit"))
        self.assertEqual((decision.action, decision.reason), ("ignore", "daily_trade_limit_reached"))
        prepare_flip.assert_not_called()

        # A later interval veto must also leave the primary alone.
        entry.created_at = timezone.now() - timedelta(days=1)
        entry.save(update_fields=["created_at"])
        self.bot.trade_interval_minutes = 30
        self.bot.save()
        Decision.objects.create(
            bot=self.bot, signal=self._signal("sell", 1.0, "recent-entry"),
            action="open", reason="prior-entry", score=1,
        )
        decision = make_decision_from_signal(self._signal("buy", 1.0, "interval-limit"))
        self.assertEqual((decision.action, decision.reason), ("ignore", "min_trade_interval_not_elapsed"))
        prepare_flip.assert_not_called()
        primary.refresh_from_db()
        self.assertEqual(primary.status, "open")
        self.assertFalse(Decision.objects.filter(action="close").exists())
