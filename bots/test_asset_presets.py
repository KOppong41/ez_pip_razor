from copy import deepcopy
from decimal import Decimal
import hashlib
from importlib import import_module
import json
from types import SimpleNamespace
from unittest.mock import patch

from django.apps import apps
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase

from bots.models import Asset, Bot
from bots.services import apply_recommendations_to_bot, asset_recommendation_state, recommended_bot_defaults
from brokers.models import BrokerAccount
from core.asset_trading_presets import (
    ASSET_CATALOG,
    ASSET_PRESET_VERSION,
    ASSET_TRADING_PRESETS,
    recommended_config_for,
)
from execution.models import RiskPolicy
from execution.services.brokers import BrokerSymbolConstraints
from execution.services.scalper_config import (
    build_scalper_config,
    resolve_allowed_strategy_pool,
)
from execution.services.strategies.momentum_ignition import MomentumIgnitionConfig
from execution.services.strategy_registry import (
    SCALPER_STRATEGY_REGISTRY,
    build_strategy_config,
    build_strategy_config_for_bot,
)


class AssetPresetCatalogTests(TestCase):
    def test_reference_gold_and_btc_presets_are_unchanged(self):
        for symbol, expected in {
            "XAUUSDm": "2765bcd712eedc4c51bebaf1fe5d97b5680f259c1d3a387951e05f54908d8aa1",
            "BTCUSDm": "92e0770618463c92ff86ca232f640d95c4ca696c5cb0043277587e483001f3d3",
        }.items():
            with self.subTest(symbol=symbol):
                digest = hashlib.sha256(json.dumps(
                    ASSET_TRADING_PRESETS[symbol], sort_keys=True, separators=(",", ":"),
                ).encode()).hexdigest()
                self.assertEqual(digest, expected)

    def test_new_assets_inherit_independent_category_recommendations(self):
        for category, symbol in (
            ("forex", "SEKUSDm"), ("commodities", "PLATINUMm"),
            ("indices", "JPN225m"), ("crypto", "SOLUSDm"),
        ):
            with self.subTest(category=category):
                asset = Asset.objects.create(symbol=symbol, category=category)
                preset = asset.recommended_config
                self.assertEqual(asset.recommended_config_version, ASSET_PRESET_VERSION)
                self.assertEqual(preset["preset_origin"], "category")
                self.assertEqual(preset["engine_mode"], "scalper")
                self.assertEqual(preset["position_sizing_mode"], "risk")
                self.assertEqual(preset["strategy_overrides"]["momentum_ignition"]["min_relative_volume"], 1.0)
                self.assertEqual(preset["strategy_overrides"]["breakout_retest"]["require_retest_rejection"], True)
                self.assertEqual(preset["trading_schedule"]["enabled"], category != "crypto")
                self.assertEqual(preset, recommended_config_for(symbol, category))
                bot = Bot(name=f"{symbol} bot", asset=asset)
                apply_recommendations_to_bot(bot, save=False)
                self.assertIsNotNone(build_scalper_config(bot).resolve_symbol(symbol))
                self.assertEqual(build_strategy_config_for_bot("momentum_ignition", bot).min_relative_volume, 1)
                self.assertTrue(build_strategy_config_for_bot("breakout_retest", bot).require_retest_rejection)
        first = Asset.objects.get(symbol="SEKUSDm")
        second = Asset.objects.create(symbol="NOKUSDm", category="forex")
        first.recommended_config["strategy_overrides"]["momentum_ignition"]["min_relative_volume"] = 99
        self.assertEqual(second.recommended_config["strategy_overrides"]["momentum_ignition"]["min_relative_volume"], 1.0)

    def test_explicit_asset_recommendation_is_not_replaced(self):
        asset = Asset.objects.create(
            symbol="CUSTOMm", category="indices",
            recommended_config={"custom": True}, recommended_config_version=42,
        )
        self.assertEqual(asset.recommended_config, {"custom": True})
        self.assertEqual(asset.recommended_config_version, 42)

    def test_identical_version_bump_keeps_customized_bot_state(self):
        asset = Asset.objects.get(symbol="BTCUSDm")
        bot = Bot(
            name="Custom BTC", asset=asset,
            asset_preset_version_applied=asset.recommended_config_version - 1,
            asset_recommended_config_applied=deepcopy(asset.recommended_config),
            asset_strategy_overrides_applied=deepcopy(asset.recommended_config["strategy_overrides"]),
            **recommended_bot_defaults(asset),
        )
        bot.risk_per_trade_pct = Decimal("0.5")
        self.assertEqual(asset_recommendation_state(bot), "customized")

    def test_v5_upgrade_preserves_custom_recommendations_and_bot_snapshot(self):
        migration = import_module("bots.migrations.0059_category_quality_presets")
        self.assertEqual(set(migration.V5_HASHES), set(ASSET_TRADING_PRESETS))
        quality_keys = {
            "momentum_ignition": ("min_relative_volume", "volume_lookback", "require_confirmation"),
            "breakout_retest": ("min_relative_volume", "volume_lookback", "require_retest_rejection"),
        }
        def v5_copy(symbol):
            old = deepcopy(ASSET_TRADING_PRESETS[symbol])
            for strategy, keys in quality_keys.items():
                for key in keys:
                    old["strategy_overrides"][strategy].pop(key)
            return old

        eur = Asset.objects.get(symbol="EURUSDm")
        gbp = Asset.objects.get(symbol="GBPJPYm")
        old_eur = v5_copy("EURUSDm")
        custom_gbp = v5_copy("GBPJPYm")
        custom_gbp["risk_per_trade_pct"] = .13
        Asset.objects.filter(pk=eur.pk).update(recommended_config=old_eur, recommended_config_version=5)
        Asset.objects.filter(pk=gbp.pk).update(recommended_config=custom_gbp, recommended_config_version=5)
        bot = Bot.objects.create(name="Frozen EUR bot", asset=eur, asset_preset_version_applied=5,
                                 asset_recommended_config_applied=deepcopy(old_eur))
        migration.upgrade_unchanged_recommendations(apps, SimpleNamespace(connection=connection))
        eur.refresh_from_db()
        gbp.refresh_from_db()
        bot.refresh_from_db()
        self.assertEqual(eur.recommended_config, ASSET_TRADING_PRESETS["EURUSDm"])
        self.assertEqual(eur.recommended_config_version, 6)
        self.assertEqual(gbp.recommended_config, custom_gbp)
        self.assertEqual(gbp.recommended_config_version, 5)
        self.assertEqual(bot.asset_recommended_config_applied, old_eur)
        self.assertEqual(bot.asset_preset_version_applied, 5)

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

    def test_every_asset_recommendation_has_a_serializable_state(self):
        for asset in Asset.objects.filter(symbol__in=ASSET_CATALOG):
            bot = Bot(
                asset=asset,
                asset_preset_version_applied=asset.recommended_config_version,
                asset_recommended_config_applied=deepcopy(asset.recommended_config),
                asset_strategy_overrides_applied=deepcopy(
                    asset.recommended_config["strategy_overrides"]
                ),
                **recommended_bot_defaults(asset),
            )
            self.assertEqual(
                asset_recommendation_state(bot),
                "recommended",
                asset.symbol,
            )

    def test_linked_assets_inherit_quality_without_losing_symbol_tuning(self):
        for symbol in ("EURUSDm", "ETHUSDm", "GBPJPYm", "USOILm"):
            with self.subTest(symbol=symbol):
                preset = ASSET_TRADING_PRESETS[symbol]
                overrides = preset["strategy_overrides"]
                self.assertEqual(overrides["momentum_ignition"]["min_relative_volume"], 1.0)
                self.assertTrue(overrides["momentum_ignition"]["require_confirmation"])
                self.assertEqual(overrides["breakout_retest"]["min_relative_volume"], 1.0)
                self.assertTrue(overrides["breakout_retest"]["require_retest_rejection"])
                if symbol in {"ETHUSDm", "USOILm"}:
                    self.assertTrue(overrides["trend_pullback"]["require_confirmation"])
        self.assertEqual(
            ASSET_TRADING_PRESETS["ETHUSDm"]["strategy_overrides"]["momentum_ignition"]["min_impulse_pct"],
            .0017,
        )
        self.assertEqual(
            ASSET_TRADING_PRESETS["USOILm"]["strategy_overrides"]["momentum_ignition"]["min_impulse_pct"],
            .0012,
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

    def test_new_category_asset_flows_into_bot_without_changing_existing_bots(self):
        asset = Asset.objects.create(symbol="SOLUSDm", category="crypto")
        response = self.client.post(
            "/api/bots/",
            {"name": "New crypto", "asset": asset.pk, "broker_account": self.account.pk},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201, response.content)
        bot = Bot.objects.get(pk=response.json()["id"])
        self.assertEqual(bot.asset_preset_version_applied, ASSET_PRESET_VERSION)
        self.assertFalse(bot.trading_schedule_enabled)
        self.assertEqual(bot.asset_strategy_overrides_applied["momentum_ignition"]["min_relative_volume"], 1.0)
        self.assertTrue(bot.asset_strategy_overrides_applied["trend_pullback"]["require_confirmation"])
        self.assertEqual(response.json()["asset_preset_state"], "recommended")

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
        self.assertEqual(
            bot.asset_strategy_overrides_applied,
            btc.recommended_config["strategy_overrides"],
        )

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

    def test_existing_bot_uses_frozen_strategy_tuning_until_reapplied(self):
        btc = Asset.objects.get(symbol="BTCUSDm")
        create_response = self.client.post(
            "/api/bots/",
            data={
                "name": "Frozen BTC tuning",
                "asset": btc.id,
                "broker_account": self.account.id,
            },
            content_type="application/json",
        )
        self.assertEqual(create_response.status_code, 201, create_response.json())
        bot = Bot.objects.get(pk=create_response.json()["id"])
        self.assertEqual(
            build_strategy_config_for_bot("momentum_ignition", bot).min_impulse_pct,
            Decimal("0.0015"),
        )
        updated = deepcopy(btc.recommended_config)
        updated["strategy_overrides"]["momentum_ignition"][
            "min_impulse_pct"
        ] = 0.009
        btc.recommended_config = updated
        btc.save(update_fields=["recommended_config"])
        bot.refresh_from_db()

        self.assertEqual(
            build_strategy_config_for_bot("momentum_ignition", bot).min_impulse_pct,
            Decimal("0.0015"),
        )
        self.assertEqual(asset_recommendation_state(bot), "customized")

        apply_response = self.client.post(
            f"/api/bots/{bot.id}/apply-asset-recommendations/",
            data={},
            content_type="application/json",
        )
        self.assertEqual(apply_response.status_code, 200, apply_response.json())
        bot.refresh_from_db()
        self.assertEqual(
            build_strategy_config_for_bot("momentum_ignition", bot).min_impulse_pct,
            Decimal("0.009"),
        )

    def test_asset_change_without_restore_clears_previous_tuning_snapshot(self):
        eur = Asset.objects.get(symbol="EURUSDm")
        btc = Asset.objects.get(symbol="BTCUSDm")
        create_response = self.client.post(
            "/api/bots/",
            data={
                "name": "Market switch",
                "asset": eur.id,
                "broker_account": self.account.id,
            },
            content_type="application/json",
        )
        bot_id = create_response.json()["id"]

        response = self.client.patch(
            f"/api/bots/{bot_id}/",
            data={"asset": btc.id},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200, response.json())
        bot = Bot.objects.get(pk=bot_id)
        self.assertIsNone(bot.asset_preset_version_applied)
        self.assertEqual(bot.asset_strategy_overrides_applied, {})
        self.assertEqual(
            build_strategy_config_for_bot("momentum_ignition", bot).min_impulse_pct,
            MomentumIgnitionConfig().min_impulse_pct,
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

    def test_invalid_stored_recommendation_cannot_break_bot_list(self):
        gold = Asset.objects.get(symbol="XAUUSDm")
        bot = Bot.objects.create(
            owner=self.user,
            name="Invalid stored recommendation",
            asset=gold,
            broker_account=self.account,
            asset_preset_version_applied=gold.recommended_config_version,
            asset_recommended_config_applied=deepcopy(gold.recommended_config),
        )
        invalid_config = dict(gold.recommended_config)
        invalid_config["risk_per_trade_pct"] = "not-a-number"
        gold.recommended_config = invalid_config
        gold.save(update_fields=["recommended_config"])

        response = self.client.get(f"/api/bots/{bot.id}/")

        self.assertEqual(response.status_code, 200, response.json())
        self.assertEqual(response.json()["asset_preset_state"], "customized")

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
            asset_recommended_config_applied=deepcopy(gold.recommended_config),
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
            asset_recommended_config_applied=deepcopy(gold.recommended_config),
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
