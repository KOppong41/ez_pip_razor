from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace

from django.test import SimpleTestCase
from django.utils import timezone

from execution.services.position_management import plan_scalper_position


def _position(*, side="buy", market_sl="0.9900", age_minutes=5, ownership="ez_trade"):
    decision = SimpleNamespace(
        params={
            "entry": "1.0000",
            "sl": "0.9900" if side == "buy" else "1.0100",
            "scalper": {
                "be_trigger_r": "1.0",
                "be_buffer_r": "0.2",
                "trail_trigger_r": "1.5",
                "trail_mode": "swing",
                "time_in_trade_limit_min": 30,
            },
        },
        decided_at=timezone.now() - timedelta(minutes=age_minutes),
    )
    order = SimpleNamespace(decision=decision, sl=Decimal(market_sl), created_at=decision.decided_at)
    return SimpleNamespace(
        is_manageable=ownership == "ez_trade",
        ownership=ownership,
        originating_order=order,
        open_price=Decimal("1.0000"),
        opened_at=decision.decided_at,
        side=side,
        sl=Decimal(market_sl),
    )


class ScalperPositionPlanTests(SimpleTestCase):
    def test_break_even_and_trailing_choose_best_stop(self):
        plan = plan_scalper_position(_position(), Decimal("1.0200"))
        self.assertEqual(plan.new_sl, Decimal("1.01500"))
        self.assertFalse(plan.close)

    def test_stale_near_breakeven_requests_close(self):
        plan = plan_scalper_position(
            _position(age_minutes=31),
            Decimal("1.0010"),
        )
        self.assertTrue(plan.close)
        self.assertEqual(plan.reason, "scalper_stale_near_breakeven")

    def test_manual_position_is_never_managed(self):
        self.assertIsNone(
            plan_scalper_position(_position(ownership="manual"), Decimal("1.0200"))
        )

    def test_sell_trailing_moves_stop_down(self):
        plan = plan_scalper_position(
            _position(side="sell", market_sl="1.0100"),
            Decimal("0.9800"),
        )
        self.assertEqual(plan.new_sl, Decimal("0.98500"))
