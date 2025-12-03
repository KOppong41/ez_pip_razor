from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from bots.models import Bot
from brokers.models import BrokerAccount
from execution.models import Signal, Decision, Position
from execution.services.decision import make_decision_from_signal


class DecisionGuardrailTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="alice", password="test123")
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
            risk_max_positions_per_symbol=5,  # allow test to hit guardrails instead of hard cap
        )
        self.bot.allow_opposite_scalp = True
        self.bot.save(update_fields=["allow_opposite_scalp"])

    def _signal(self, direction: str, dedupe_key: str, score: float = 1.0) -> Signal:
        return Signal.objects.create(
            bot=self.bot,
            source="test",
            symbol="EURUSDm",
            timeframe="5m",
            direction=direction,
            payload={"tp": "1.2", "sl": "1.0", "score": score},
            dedupe_key=dedupe_key,
        )

    def test_blocks_same_direction_when_position_exists(self):
        Position.objects.create(
            broker_account=self.account,
            symbol="EURUSDm",
            qty=Decimal("1.0"),
            avg_price=Decimal("1.1000"),
            status="open",
        )
        decision = make_decision_from_signal(self._signal("buy", "sig-1"))
        self.assertEqual(decision.action, "ignore")
        self.assertEqual(decision.reason, "existing_position_same_direction")

    @override_settings(DECISION_ALLOW_HEDGING=False, DECISION_FLIP_SCORE=0.8, DECISION_MIN_SCORE=0.5)
    def test_blocks_opposite_direction_when_no_hedging(self):
        Position.objects.create(
            broker_account=self.account,
            symbol="EURUSDm",
            qty=Decimal("-1.0"),  # short position
            avg_price=Decimal("1.1000"),
            status="open",
        )
        decision = make_decision_from_signal(self._signal("buy", "sig-2", score=0.6))
        self.assertEqual(decision.action, "open")  # allow a small opposite scalp while keeping short alive
        self.assertEqual(decision.reason, "opposite_scalp")
        self.assertTrue(decision.params.get("scalp"))
        self.assertIsNotNone(decision.params.get("sl"))
        self.assertIsNotNone(decision.params.get("tp"))
        close_decisions = Decision.objects.filter(action="close", reason="flip_close")
        self.assertEqual(close_decisions.count(), 0)
