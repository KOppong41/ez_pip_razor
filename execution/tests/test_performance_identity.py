import json
import subprocess
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from bots.models import Asset, Bot
from bots.services import apply_recommendations_to_bot
from core.build_info import capture_build_identity
from execution.services.performance_identity import canonical_settings, performance_identity


SHA = "a" * 40


class BuildIdentityTests(SimpleTestCase):
    def test_source_build_requires_clean_root_checkout(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            for status, expected in (("", SHA), (" M execution/models.py", None), ("?? new.py", None)):
                with self.subTest(status=status), patch("core.build_info.subprocess.run") as run:
                    run.side_effect = [SimpleNamespace(stdout=value) for value in (str(root), SHA, status)]
                    result = capture_build_identity(root, frozen=False)
                    self.assertEqual(result["sha"], expected)
                    self.assertEqual(result["status"], "clean" if expected else "dirty")
                    self.assertEqual(result["source"], "source_checkout")

    def test_git_failure_or_parent_repository_is_not_a_verified_build(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            cases = ([SimpleNamespace(stdout=str(root.parent))],
                     [SimpleNamespace(stdout=str(root)), SimpleNamespace(stdout="invalid")],
                     FileNotFoundError(), subprocess.TimeoutExpired("git", 5))
            for effect in cases:
                with self.subTest(effect=effect), patch("core.build_info.subprocess.run", side_effect=effect):
                    result = capture_build_identity(root, frozen=False)
                    self.assertIsNone(result["sha"])
                    self.assertEqual(result["status"], "unavailable")

    def test_packaged_build_uses_manifest_and_never_local_git(self):
        with TemporaryDirectory() as directory, patch("core.build_info.subprocess.run") as run:
            root = Path(directory)
            (root / "core").mkdir()
            manifest = root / "core" / "build-info.json"
            cases = [({"sha": SHA, "status": "clean"}, SHA, "clean"),
                     ({"sha": SHA, "status": "dirty"}, None, "dirty"),
                     ({"sha": "invalid", "status": "clean"}, None, "unavailable"),
                     ([], None, "unavailable")]
            for data, sha, status in cases:
                with self.subTest(data=data):
                    manifest.write_text(json.dumps(data), encoding="utf-8")
                    self.assertEqual(capture_build_identity(root, frozen=True),
                                     {"sha": sha, "status": status, "source": "build_manifest"})
            manifest.write_text("broken JSON", encoding="utf-8")
            self.assertIsNone(capture_build_identity(root, frozen=True)["sha"])
            manifest.unlink()
            self.assertIsNone(capture_build_identity(root, frozen=True)["sha"])
            run.assert_not_called()


@override_settings(EXECUTION_BUILD_IDENTITY={"sha": SHA, "status": "clean"})
class PerformanceIdentityTests(SimpleTestCase):
    def setUp(self):
        self.bot = Bot(asset=Asset(pk=1, symbol="XAUUSDm", category="commodities"), engine_mode="scalper")

    def identity(self):
        return performance_identity(self.bot, "XAUUSDm", "5m")

    def fingerprint(self):
        result = self.identity()
        self.assertIsNotNone(result["config_fingerprint"], result)
        return result["config_fingerprint"]

    def test_numeric_canonicalization_preserves_precision_and_rejects_nonfinite(self):
        self.assertEqual(canonical_settings({"b": Decimal("1.00"), "a": "0.5000"}), {"a": "0.5", "b": "1"})
        self.assertEqual(canonical_settings(Decimal("123456789012345678901234567890.1")),
                         "123456789012345678901234567890.1")
        self.assertEqual(canonical_settings(Decimal("-0.0")), "0")
        for value in (float("nan"), Decimal("Infinity")):
            with self.assertRaises(ValueError):
                canonical_settings(value)

    def test_operational_state_and_display_names_do_not_change_fingerprint(self):
        before = self.fingerprint()
        self.bot.name = "Renamed bot"
        self.bot.status = "active"
        self.bot.allocation_start_pnl = Decimal("123")
        self.bot.asset.name = "Renamed asset"
        self.bot.asset_preset_applied_at = "2026-09-21"
        self.assertEqual(self.fingerprint(), before)

    def test_equivalent_numeric_types_and_allowlist_order_are_stable(self):
        self.bot.enabled_strategies = ["trend_pullback", "momentum_ignition"]
        self.bot.risk_per_trade_pct = Decimal("0.5000")
        before = self.fingerprint()
        self.bot.enabled_strategies.reverse()
        self.bot.risk_per_trade_pct = "0.5"
        self.assertEqual(self.fingerprint(), before)
        self.assertEqual(performance_identity(self.bot, "GOLD", "M5")["config_fingerprint"], before)

    def test_behavior_changes_have_distinct_fingerprints(self):
        before = self.fingerprint()
        changes = {"risk_per_trade_pct": Decimal("0.7"), "allow_opposite_scalp": True,
                   "auto_trade": False, "decision_min_score": Decimal("1.1"),
                   "trading_window_start": self.bot.trading_window_start.replace(hour=3),
                   "scalper_params": {"symbols": {"XAUUSD": {"tp_r_multiple": 2.5}}}}
        for field, value in changes.items():
            with self.subTest(field=field):
                original = getattr(self.bot, field)
                setattr(self.bot, field, value)
                self.assertNotEqual(self.fingerprint(), before)
                setattr(self.bot, field, original)
        self.assertNotEqual(performance_identity(self.bot, "XAUUSD", "1m")["config_fingerprint"], before)

    def test_frozen_detector_and_inherited_pool_are_part_of_identity(self):
        self.bot.asset_preset_version_applied = 4
        before = self.fingerprint()
        self.bot.asset_strategy_overrides_applied = {"momentum_ignition": {"min_relative_volume": 1}}
        detector_changed = self.fingerprint()
        self.assertNotEqual(detector_changed, before)
        self.bot.asset_recommended_config_applied = {"enabled_strategies": ["momentum_ignition"]}
        self.assertNotEqual(self.fingerprint(), detector_changed)

    def test_catalog_update_does_not_change_frozen_configuration(self):
        self.bot.asset.recommended_config = {"risk_per_trade_pct": 0.5}
        apply_recommendations_to_bot(self.bot, save=False)
        before = self.fingerprint()
        self.assertEqual(self.identity()["recommendation_state"], "recommended")
        self.bot.asset.recommended_config = {"risk_per_trade_pct": 0.7}
        self.bot.asset.recommended_config_version += 1
        self.assertEqual(self.fingerprint(), before)
        self.assertEqual(self.identity()["recommendation_state"], "update_available")

    def test_identity_uses_startup_build_without_rereading_git(self):
        with patch("core.build_info.subprocess.run") as run:
            first = self.identity()
            second = self.identity()
        run.assert_not_called()
        self.assertEqual(first, second)
        self.assertEqual(first["build_sha"], SHA)
        self.assertEqual(first["execution_timeframe"], "5m")

    def test_malformed_configuration_has_no_comparable_fingerprint(self):
        self.bot.scalper_params = {"risk": "bad"}
        result = self.identity()
        self.assertIsNone(result["config_fingerprint"])
        self.assertEqual(result["config_status"], "unavailable")
        self.assertNotIn("config_snapshot", result)
        self.assertEqual(result["build_sha"], SHA)
