from decimal import Decimal
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from bots.models import Bot
from brokers.models import BrokerAccount
from execution.models import Signal, Position, Decision, Order
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
            account_ref="p1",
            owner=self.user,
        )
        self.bot = Bot.objects.create(
            name="Bot",
            owner=self.user,
            status="active",
            auto_trade=True,
            broker_account=self.account,
            allowed_symbols=["EURUSDm"],
            risk_max_concurrent_positions=5,
        )

    def _signal(self, direction: str, score: float, key: str):
        return Signal.objects.create(
            bot=self.bot,
            source="test",
            symbol="EURUSDm",
            timeframe="5m",
            direction=direction,
            payload={},
            dedupe_key=key,
        )

    def test_flip_creates_close_decision_and_order(self):
        Position.objects.create(
            broker_account=self.account,
            symbol="EURUSDm",
            qty=Decimal("-1.0"),
            avg_price=Decimal("1.1000"),
            status="open",
        )
        sig = self._signal("buy", score=1.0, key="flip-1")
        # inject score via payload->StrategyDecision naive uses 0.5 default; override by naive? For test set BotConfig? Instead monkey: set direction -> naive score default 0.5 enough due to threshold 0.2
        decision = make_decision_from_signal(sig)
        self.assertEqual(decision.action, "open")

        close_decisions = Decision.objects.filter(action="close", reason="flip_close")
        self.assertEqual(close_decisions.count(), 1)
        close_decision = close_decisions.first()
        self.assertEqual(close_decision.params.get("position_id"), Position.objects.first().id)

        close_orders = Order.objects.filter(symbol="EURUSDm", side="buy")
        self.assertEqual(close_orders.count(), 1)

    @override_settings(DECISION_MAX_FLIPS_PER_DAY=1)
    def test_flip_blocked_by_daily_cap(self):
        Position.objects.create(
            broker_account=self.account,
            symbol="EURUSDm",
            qty=Decimal("-1.0"),
            avg_price=Decimal("1.1000"),
            status="open",
        )
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
        self.assertEqual(decision.action, "open")
        # No new flip decisions/orders because cap hit
        self.assertEqual(Decision.objects.filter(action="close", reason="flip_close").count(), 1)
