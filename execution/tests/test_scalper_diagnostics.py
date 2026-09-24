from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

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


class DecisionScanEvidenceTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user("scan-owner", is_staff=True)
        self.owner.user_permissions.add(Permission.objects.get(codename="view_decision"))
        self.other = get_user_model().objects.create_user("other-scan-owner")
        asset = Asset.objects.get(symbol="BTCUSDm")
        self.bot = Bot.objects.create(owner=self.owner, name="My BTC bot", asset=asset)
        self.other_bot = Bot.objects.create(owner=self.other, name="Private BTC bot", asset=asset)
        self.client.force_login(self.owner)
        self.url = reverse("admin:execution_decision_changelist")

    def test_empty_decisions_show_latest_scan_per_owned_bot(self):
        ScalperRunLog.objects.create(bot=self.bot, summary={"rejection_reason": "old_reason"})
        latest = ScalperRunLog.objects.create(bot=self.bot, summary={
            "outcome": "skipped", "rejection_reason": "htf_context_conflict",
            "strategies_evaluated": [], "htf_status": "conflict",
        })
        ScalperRunLog.objects.create(bot=self.other_bot, summary={"rejection_reason": "private_reason"})
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(response.context["latest_scalper_runs"]), [latest])
        self.assertContains(response, "htf_context_conflict")
        self.assertContains(response, "No decisions found.")
        self.assertNotContains(response, "private_reason")
        self.assertNotContains(response, "old_reason")

    def test_stale_scans_do_not_claim_recent_activity(self):
        old = ScalperRunLog.objects.create(bot=self.bot)
        ScalperRunLog.objects.filter(pk=old.pk).update(created_at=timezone.now() - timedelta(hours=25))
        response = self.client.get(self.url)
        self.assertContains(response, "No scalper scans recorded for your bots in the last 24 hours.")

    def test_superuser_can_see_other_owners_scan_evidence(self):
        self.owner.is_superuser = True
        self.owner.save(update_fields=["is_superuser"])
        run = ScalperRunLog.objects.create(bot=self.other_bot)
        response = self.client.get(self.url)
        self.assertEqual(list(response.context["latest_scalper_runs"]), [run])
