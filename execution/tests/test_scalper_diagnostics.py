from django.test import TestCase

from bots.models import Asset, Bot
from brokers.models import BrokerAccount
from execution.admin import _diagnostics_for_symbols
from execution.models import ScalperRunLog


class ScalperDashboardDiagnosticsTests(TestCase):
    def setUp(self):
        self.account = BrokerAccount.objects.create(
            name="Diagnostics account",
            broker="mt5",
            connector="mt5_local",
            account_ref="diagnostics-account",
        )
        self.asset, _created = Asset.objects.get_or_create(symbol="XAUUSDm")
        self.bot = Bot.objects.create(
            name="Diagnostics bot",
            engine_mode="scalper",
            broker_account=self.account,
            asset=self.asset,
        )

    def test_aggregates_cycle_and_per_strategy_outcomes(self):
        ScalperRunLog.objects.create(
            bot=self.bot,
            summary={
                "outcome": "no_signals",
                "rejection_reason": "htf_bias_unavailable",
                "strategies": [
                    {
                        "strategy": "price_action_pinbar",
                        "action": "skip",
                        "reason": "no_pinbar",
                    }
                ],
            },
        )
        ScalperRunLog.objects.create(
            bot=self.bot,
            summary={
                "outcome": "no_signals",
                "rejection_reason": "htf_context_conflict",
                "strategies": [
                    {
                        "strategy": "price_action_pinbar",
                        "action": "skip",
                        "reason": "no_pinbar",
                    }
                ],
            },
        )

        diagnostics = _diagnostics_for_symbols(
            {
                "XAUUSD": {
                    "aliases": ["XAUUSDm"],
                    "sl_points": {"unit": "points"},
                }
            }
        )

        self.assertEqual(
            diagnostics[0]["outcome_counts"],
            [{"outcome": "no_signals", "count": 2}],
        )
        self.assertEqual(
            diagnostics[0]["strategy_counts"],
            [
                {
                    "strategy": "price_action_pinbar",
                    "action": "skip",
                    "reason": "no_pinbar",
                    "count": 2,
                }
            ],
        )
        self.assertEqual(
            diagnostics[0]["rejection_counts"],
            [
                {"reason": "htf_bias_unavailable", "count": 1},
                {"reason": "htf_context_conflict", "count": 1},
            ],
        )
