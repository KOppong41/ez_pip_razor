from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase

from bots.models import Asset, Bot
from brokers.models import BrokerAccount
from execution.models import RiskPolicy


class ClientBotApiTest(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user("alice", password="pass")
        self.other = User.objects.create_user("bob", password="pass")
        self.asset, _ = Asset.objects.update_or_create(
            symbol="EURUSDm",
            defaults={
                "display_name": "EUR/USD",
                "min_qty": "0.01",
                "recommended_qty": "0.02",
                "is_active": True,
            },
        )
        self.account = BrokerAccount.objects.create(
            owner=self.user,
            name="Alice MT5",
            broker="mt5",
            account_ref="alice-1",
            mt5_login="10001",
            is_active=True,
        )
        self.other_account = BrokerAccount.objects.create(
            owner=self.other,
            name="Bob MT5",
            broker="mt5",
            account_ref="bob-1",
            mt5_login="20001",
            is_active=True,
        )
        self.other_bot = Bot.objects.create(
            owner=self.other,
            name="Bob Bot",
            asset=self.asset,
            broker_account=self.other_account,
            default_qty="0.01",
        )
        self.client.force_login(self.user)

    def test_client_can_create_bot_and_owner_is_forced(self):
        response = self.client.post(
            "/api/bots/",
            data={
                "name": "Alice Bot",
                "asset": self.asset.id,
                "broker_account": self.account.id,
                "engine_mode": "harami",
                "default_timeframe": "5m",
                "default_qty": "0.02",
                "auto_trade": True,
                "status": "active",
            },
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201, response.json())
        bot = Bot.objects.get(name="Alice Bot")
        self.assertEqual(bot.owner, self.user)
        self.assertEqual(bot.status, "stopped")
        self.assertEqual(bot.position_sizing_mode, "risk")
        self.assertEqual(bot.risk_per_trade_pct, Decimal("0.5"))

    def test_client_cannot_see_or_control_another_users_bot(self):
        response = self.client.get("/api/bots/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [])
        response = self.client.post(
            f"/api/bots/{self.other_bot.id}/control/",
            data={"action": "stop"},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 404)

    def test_client_cannot_assign_another_users_account(self):
        response = self.client.post(
            "/api/bots/",
            data={
                "name": "Cross Tenant Bot",
                "asset": self.asset.id,
                "broker_account": self.other_account.id,
                "default_qty": "0.02",
            },
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("broker_account", response.json())

    def test_options_only_include_the_clients_accounts(self):
        RiskPolicy.objects.create(
            broker_account=self.account,
            max_order_lot_size="0.04",
            max_total_open_positions=2,
            max_positions_per_symbol=1,
            max_aggregate_open_lots="0.08",
        )
        response = self.client.get("/api/bots/options/")
        self.assertEqual(response.status_code, 200)
        account_ids = {row["id"] for row in response.json()["accounts"]}
        self.assertEqual(account_ids, {self.account.id})
        limits = response.json()["accounts"][0]["risk_limits"]
        self.assertEqual(Decimal(str(limits["max_order_lot_size"])), Decimal("0.04"))
        self.assertEqual(limits["max_total_open_positions"], 2)
        asset_ids = {row["id"] for row in response.json()["assets"]}
        self.assertIn(self.asset.id, asset_ids)

    def test_client_can_round_trip_all_user_editable_risk_and_schedule_fields(self):
        response = self.client.post(
            "/api/bots/",
            data={
                "name": "Configured Bot",
                "asset": self.asset.id,
                "broker_account": self.account.id,
                "engine_mode": "harami",
                "default_timeframe": "5m",
                "allowed_timeframes": ["1m", "5m"],
                "default_qty": "0.02",
                "default_tp_pips": "12",
                "default_sl_pips": "6",
                "allocation_amount": "300",
                "allocation_profit_pct": "50",
                "allocation_loss_pct": "100",
                "trading_schedule_enabled": True,
                "allowed_trading_days": ["mon", "wed", "fri"],
                "trading_window_start": "06:30",
                "trading_window_end": "17:45",
                "allow_opposite_scalp": True,
                "kill_switch_enabled": True,
                "kill_switch_max_unrealized_pct": "4",
                "loss_streak_autopause_enabled": True,
                "max_loss_streak_before_pause": 3,
                "loss_streak_cooldown_min": 90,
                "soft_drawdown_limit_pct": "2",
                "soft_size_multiplier": "0.5",
                "hard_drawdown_limit_pct": "4",
                "hard_size_multiplier": "0.25",
            },
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 201, response.json())
        data = response.json()
        self.assertEqual(data["allowed_timeframes"], ["1m", "5m"])
        self.assertEqual(Decimal(data["allocation_amount"]), Decimal("300"))
        self.assertEqual(data["allowed_trading_days"], ["mon", "wed", "fri"])
        self.assertTrue(data["allow_opposite_scalp"])
        self.assertTrue(data["loss_streak_autopause_enabled"])
        self.assertEqual(Decimal(data["soft_size_multiplier"]), Decimal("0.5"))
        self.assertEqual(Decimal(data["hard_size_multiplier"]), Decimal("0.25"))
