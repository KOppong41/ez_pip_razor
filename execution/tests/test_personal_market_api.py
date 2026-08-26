from django.contrib.auth import get_user_model
from django.test import TestCase

from bots.models import Asset
from brokers.models import BrokerAccount
from execution.models import BrokerSymbolMapping


class PersonalMarketApiTest(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("market-user", password="pass")
        self.account = BrokerAccount.objects.create(
            owner=self.user,
            name="Market MT5",
            broker="mt5",
            account_ref="market-1",
            mt5_login="30001",
            is_active=True,
        )
        self.gold, _ = Asset.objects.update_or_create(
            symbol="XAUUSDm",
            defaults={
                "display_name": "Gold",
                "category": "commodities",
                "min_qty": "0.01",
                "recommended_qty": "0.01",
                "is_active": True,
            },
        )
        Asset.objects.update_or_create(
            symbol="OLDUSDm",
            defaults={"display_name": "Disabled", "is_active": False},
        )
        self.client.force_login(self.user)

    def test_get_returns_platform_assets_with_personal_enablement(self):
        response = self.client.get("/api/personal/markets/")
        self.assertEqual(response.status_code, 200)
        row = next(
            item for item in response.json() if item["canonical_symbol"] == "XAUUSD"
        )
        self.assertEqual(row["asset_id"], self.gold.id)
        self.assertEqual(row["canonical_symbol"], "XAUUSD")
        self.assertFalse(row["enabled"])
        self.assertEqual(row["trading_status"], "not_synced")

    def test_client_can_enable_an_active_platform_asset(self):
        response = self.client.patch(
            "/api/personal/markets/",
            data={"canonical_symbol": "XAUUSD", "enabled": True},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        mapping = BrokerSymbolMapping.objects.get(
            broker_account=self.account,
            canonical_symbol="XAUUSD",
        )
        self.assertTrue(mapping.enabled)

    def test_client_cannot_enable_an_unknown_platform_asset(self):
        response = self.client.patch(
            "/api/personal/markets/",
            data={"canonical_symbol": "NOTREAL", "enabled": True},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
