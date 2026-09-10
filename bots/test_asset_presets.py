from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from bots.models import Asset, Bot
from brokers.models import BrokerAccount
from core.asset_trading_presets import (
    ASSET_CATALOG,
    ASSET_PRESET_VERSION,
    ASSET_TRADING_PRESETS,
)
from execution.models import RiskPolicy
from execution.services.brokers import BrokerSymbolConstraints
from execution.services.scalper_config import (
    build_scalper_config,
    resolve_allowed_strategy_pool,
)
from execution.services.strategy_registry import (
    SCALPER_STRATEGY_REGISTRY,
    build_strategy_config,
)


class AssetPresetCatalogTests(TestCase):
    def test_all_assets_have_valid_categories_strategies_and_m5(self):
        self.assertEqual(len(ASSET_CATALOG), 34)
        self.assertEqual(set(ASSET_CATALOG), set(ASSET_TRADING_PRESETS))
        for symbol, metadata in ASSET_CATALOG.items():
            preset = ASSET_TRADING_PRESETS[symbol]
            self.assertIn(
                metadata["category"],
                {"forex", "commodities", "indices", "crypto"},
            )
            self.assertIn("5m", preset["allowed_timeframes"])
            self.assertTrue(preset["enabled_strategies"])
            for strategy in preset["enabled_strategies"]:
                self.assertIn(strategy, SCALPER_STRATEGY_REGISTRY)

    def test_migration_seeded_all_database_recommendations(self):
        assets = Asset.objects.filter(symbol__in=ASSET_CATALOG)
        self.assertEqual(assets.count(), 34)
        for asset in assets:
            self.assertEqual(asset.category, ASSET_CATALOG[asset.symbol]["category"])
            self.assertEqual(
                asset.recommended_config,
                ASSET_TRADING_PRESETS[asset.symbol],
            )
            self.assertEqual(
                asset.recommended_config_version,
                ASSET_PRESET_VERSION,
            )


class AssetPresetApiTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("preset-user", password="pass")
        self.account = BrokerAccount.objects.create(
            owner=self.user,
            name="Preset MT5",
            broker="mt5",
            account_ref="preset-account",
            mt5_login="1234",
            is_active=True,
        )
        self.policy = RiskPolicy.objects.create(
            broker_account=self.account,
            max_total_open_positions=4,
            max_order_lot_size="0.50",
        )
        self.client.force_login(self.user)

    def test_options_include_database_recommendation(self):
        response = self.client.get("/api/bots/options/")
        self.assertEqual(response.status_code, 200)
        gold = next(row for row in response.json()["assets"] if row["symbol"] == "XAUUSDm")
        self.assertEqual(gold["category"], "commodities")
        self.assertEqual(gold["recommended_config_version"], ASSET_PRESET_VERSION)
        self.assertEqual(gold["recommended_config"]["default_timeframe"], "5m")
        self.assertEqual(
            {row["value"] for row in response.json()["scalper_strategies"]},
            set(SCALPER_STRATEGY_REGISTRY),
        )

    @patch("bots.views.get_broker_symbol_constraints")
    def test_fixed_lot_hint_uses_connected_broker_constraints(self, constraints):
        constraints.return_value = BrokerSymbolConstraints(
            min_lot=Decimal("0.05"),
            max_lot=Decimal("25"),
            lot_step=Decimal("0.05"),
        )
        gold = Asset.objects.get(symbol="XAUUSDm")

        response = self.client.get(
            "/api/bots/symbol-constraints/",
            {"asset": gold.id, "broker_account": self.account.id},
        )

        self.assertEqual(response.status_code, 200, response.json())
        self.assertTrue(response.json()["available"])
        self.assertEqual(Decimal(response.json()["suggested_quantity"]), Decimal("0.05"))
        self.assertEqual(Decimal(response.json()["volume_step"]), Decimal("0.05"))

    def test_new_bot_gets_preset_but_explicit_value_wins(self):
        btc = Asset.objects.get(symbol="BTCUSDm")
        response = self.client.post(
            "/api/bots/",
            data={
                "name": "BTC preset",
                "asset": btc.id,
                "broker_account": self.account.id,
                "risk_per_trade_pct": "0.20",
            },
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201, response.json())
        bot = Bot.objects.get(name="BTC preset")
        self.assertEqual(bot.engine_mode, "scalper")
        self.assertEqual(bot.default_timeframe, "5m")
        self.assertEqual(bot.risk_per_trade_pct, Decimal("0.20"))
        self.assertEqual(bot.enabled_strategies, ASSET_TRADING_PRESETS["BTCUSDm"]["enabled_strategies"])
        self.assertFalse(bot.trading_schedule_enabled)
        self.assertEqual(bot.trading_timezone, "UTC")
        self.assertEqual(bot.asset_preset_version_applied, ASSET_PRESET_VERSION)

    def test_scalper_rejects_strategy_without_registered_runner(self):
        gold = Asset.objects.get(symbol="XAUUSDm")
        response = self.client.post(
            "/api/bots/",
            data={
                "name": "Unsupported scalper",
                "asset": gold.id,
                "broker_account": self.account.id,
                "engine_mode": "scalper",
                "enabled_strategies": ["engulfing"],
            },
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400, response.json())
        self.assertIn("enabled_strategies", response.json())

    def test_strategy_config_merges_category_asset_and_stored_overrides(self):
        gold = Asset.objects.get(symbol="XAUUSDm")
        silver = Asset.objects.get(symbol="XAGUSDm")
        euro = Asset.objects.get(symbol="EURUSDm")

        self.assertEqual(
            build_strategy_config("momentum_ignition", euro).min_impulse_pct,
            Decimal("0.0005"),
        )
        self.assertEqual(
            build_strategy_config("momentum_ignition", gold).min_impulse_pct,
            Decimal("0.0007"),
        )
        self.assertEqual(
            build_strategy_config("momentum_ignition", silver).min_impulse_pct,
            Decimal("0.0011"),
        )

        stored = dict(gold.recommended_config)
        stored["strategy_overrides"] = dict(stored["strategy_overrides"])
        stored["strategy_overrides"]["momentum_ignition"] = {
            "min_impulse_pct": 0.0025,
        }
        gold.recommended_config = stored
        gold.save(update_fields=["recommended_config"])
        self.assertEqual(
            build_strategy_config("momentum_ignition", gold).min_impulse_pct,
            Decimal("0.0025"),
        )

    def test_recommendation_state_tracks_match_customization_and_update(self):
        gold = Asset.objects.get(symbol="XAUUSDm")
        create_response = self.client.post(
            "/api/bots/",
            data={
                "name": "Recommendation state",
                "asset": gold.id,
                "broker_account": self.account.id,
            },
            content_type="application/json",
        )
        self.assertEqual(create_response.status_code, 201, create_response.json())
        self.assertEqual(create_response.json()["asset_preset_state"], "recommended")
        bot_id = create_response.json()["id"]

        customized_response = self.client.patch(
            f"/api/bots/{bot_id}/",
            data={"risk_per_trade_pct": "0.19"},
            content_type="application/json",
        )
        self.assertEqual(customized_response.status_code, 200, customized_response.json())
        self.assertEqual(customized_response.json()["asset_preset_state"], "customized")

        apply_response = self.client.post(
            f"/api/bots/{bot_id}/apply-asset-recommendations/",
            data={},
            content_type="application/json",
        )
        self.assertEqual(apply_response.status_code, 200, apply_response.json())
        self.assertEqual(apply_response.json()["asset_preset_state"], "recommended")

        updated_config = dict(gold.recommended_config)
        updated_config["risk_per_trade_pct"] = 0.31
        gold.recommended_config = updated_config
        gold.recommended_config_version += 1
        gold.save(update_fields=["recommended_config", "recommended_config_version"])
        refreshed_response = self.client.get(f"/api/bots/{bot_id}/")
        self.assertEqual(refreshed_response.status_code, 200, refreshed_response.json())
        self.assertEqual(
            refreshed_response.json()["asset_preset_state"],
            "update_available",
        )

    def test_existing_bot_changes_only_after_explicit_apply(self):
        eur = Asset.objects.get(symbol="EURUSDm")
        btc = Asset.objects.get(symbol="BTCUSDm")
        bot = Bot.objects.create(
            owner=self.user,
            name="Existing custom",
            asset=eur,
            broker_account=self.account,
            engine_mode="scalper",
            risk_per_trade_pct="0.77",
            enabled_strategies=["range_reversion"],
        )
        policy_before = {
            "positions": self.policy.max_total_open_positions,
            "lot": self.policy.max_order_lot_size,
        }

        response = self.client.patch(
            f"/api/bots/{bot.id}/",
            data={"asset": btc.id},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.json())
        bot.refresh_from_db()
        self.assertEqual(bot.risk_per_trade_pct, Decimal("0.77"))
        self.assertEqual(bot.enabled_strategies, ["range_reversion"])

        response = self.client.post(
            f"/api/bots/{bot.id}/apply-asset-recommendations/",
            data={},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.json())
        bot.refresh_from_db()
        self.policy.refresh_from_db()
        self.assertEqual(bot.risk_per_trade_pct, Decimal("0.25"))
        self.assertEqual(bot.enabled_strategies, ASSET_TRADING_PRESETS["BTCUSDm"]["enabled_strategies"])
        self.assertEqual(self.policy.max_total_open_positions, policy_before["positions"])
        self.assertEqual(
            self.policy.max_order_lot_size,
            Decimal(str(policy_before["lot"])),
        )

    def test_runtime_merges_asset_then_user_symbol_override(self):
        gold = Asset.objects.get(symbol="XAUUSDm")
        bot = Bot.objects.create(
            owner=self.user,
            name="Gold override",
            asset=gold,
            broker_account=self.account,
            engine_mode="scalper",
            asset_preset_version_applied=gold.recommended_config_version,
            scalper_params={
                "symbols": {"XAUUSD": {"tp_r_multiple": 2.1}},
            },
        )
        symbol = build_scalper_config(bot).resolve_symbol("XAUUSDm")
        self.assertEqual(symbol.sl_points_unit, "percent")
        self.assertEqual(symbol.tp_r_multiple, Decimal("2.1"))

    def test_unapplied_existing_bot_keeps_legacy_runtime_profile(self):
        gold = Asset.objects.get(symbol="XAUUSDm")
        bot = Bot.objects.create(
            owner=self.user,
            name="Legacy gold",
            asset=gold,
            broker_account=self.account,
            engine_mode="scalper",
            asset_preset_version_applied=None,
        )

        before_apply = build_scalper_config(bot).resolve_symbol("XAUUSDm")
        self.assertEqual(before_apply.sl_points_unit, "points")

        apply_response = self.client.post(
            f"/api/bots/{bot.id}/apply-asset-recommendations/",
            data={},
            content_type="application/json",
        )
        self.assertEqual(apply_response.status_code, 200, apply_response.json())
        bot.refresh_from_db()
        after_apply = build_scalper_config(bot).resolve_symbol("XAUUSDm")
        self.assertEqual(after_apply.sl_points_unit, "percent")

    def test_strategy_pool_customization_and_empty_inheritance(self):
        gold = Asset.objects.get(symbol="XAUUSDm")
        bot = Bot(
            asset=gold,
            enabled_strategies=["range_reversion"],
            asset_preset_version_applied=gold.recommended_config_version,
        )
        self.assertEqual(
            resolve_allowed_strategy_pool(bot),
            (["range_reversion"], "bot"),
        )
        bot.enabled_strategies = []
        self.assertEqual(
            resolve_allowed_strategy_pool(bot),
            (ASSET_TRADING_PRESETS["XAUUSDm"]["enabled_strategies"], "asset"),
        )
