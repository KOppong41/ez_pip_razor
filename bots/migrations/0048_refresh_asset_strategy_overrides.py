from copy import deepcopy

from django.db import migrations


# Keep migration data frozen: later preset versions must not alter this migration.
CATEGORY_OVERRIDES = {
    "forex": {
        "trend_pullback": {
            "min_trend_slope_pct": 0.00004,
            "min_atr_pct": 0.00004,
            "pullback_atr_multiple": 0.80,
        },
        "breakout_retest": {
            "min_range_pct": 0.0006,
            "retest_tolerance": 0.0008,
            "min_breakout_body_pct": 0.0002,
            "breakout_extension_pct": 0.00015,
            "min_breakout_volume": 60,
        },
        "range_reversion": {
            "min_range_pct": 0.0006,
            "max_directional_efficiency": 0.45,
        },
        "momentum_ignition": {
            "min_impulse_pct": 0.0005,
            "pullback_ratio": 0.70,
            "min_tick_volume": 60,
        },
        "doji_breakout": {
            "min_atr_pct": 0.00004,
            "wick_level_tolerance_atr": 0.45,
            "breakout_buffer_atr": 0.08,
        },
        "price_action_pinbar": {
            "min_atr_pct": 0.00004,
            "wick_level_tolerance_atr": 0.45,
        },
    },
    "commodities": {
        "trend_pullback": {
            "min_trend_slope_pct": 0.00008,
            "min_atr_pct": 0.00010,
            "pullback_atr_multiple": 1.0,
        },
        "breakout_retest": {
            "min_range_pct": 0.0012,
            "retest_tolerance": 0.0015,
            "min_breakout_body_pct": 0.0005,
            "breakout_extension_pct": 0.00035,
            "min_breakout_volume": 80,
        },
        "range_reversion": {
            "min_range_pct": 0.0012,
            "max_directional_efficiency": 0.40,
        },
        "momentum_ignition": {
            "min_impulse_pct": 0.0009,
            "pullback_ratio": 0.65,
            "min_tick_volume": 80,
        },
        "doji_breakout": {
            "min_atr_pct": 0.00010,
            "wick_level_tolerance_atr": 0.60,
            "breakout_buffer_atr": 0.12,
        },
        "price_action_pinbar": {
            "min_atr_pct": 0.00010,
            "wick_level_tolerance_atr": 0.60,
        },
    },
    "indices": {
        "trend_pullback": {
            "min_trend_slope_pct": 0.00008,
            "min_atr_pct": 0.00008,
            "pullback_atr_multiple": 0.95,
        },
        "breakout_retest": {
            "min_range_pct": 0.0010,
            "retest_tolerance": 0.0012,
            "min_breakout_body_pct": 0.0004,
            "breakout_extension_pct": 0.0003,
            "min_breakout_volume": 80,
        },
        "range_reversion": {
            "min_range_pct": 0.0010,
            "max_directional_efficiency": 0.40,
        },
        "momentum_ignition": {
            "min_impulse_pct": 0.0008,
            "pullback_ratio": 0.65,
            "min_tick_volume": 80,
        },
        "doji_breakout": {"min_atr_pct": 0.00008},
        "price_action_pinbar": {"min_atr_pct": 0.00008},
    },
    "crypto": {
        "trend_pullback": {
            "min_trend_slope_pct": 0.00012,
            "min_atr_pct": 0.00015,
            "pullback_atr_multiple": 1.10,
        },
        "breakout_retest": {
            "min_range_pct": 0.0018,
            "retest_tolerance": 0.0020,
            "min_breakout_body_pct": 0.0008,
            "breakout_extension_pct": 0.0005,
            "min_breakout_volume": 80,
        },
        "range_reversion": {
            "min_range_pct": 0.0018,
            "max_directional_efficiency": 0.35,
        },
        "momentum_ignition": {
            "min_impulse_pct": 0.0012,
            "pullback_ratio": 0.60,
            "min_tick_volume": 80,
        },
        "doji_breakout": {
            "min_atr_pct": 0.00015,
            "wick_level_tolerance_atr": 0.65,
            "breakout_buffer_atr": 0.15,
        },
        "price_action_pinbar": {
            "min_atr_pct": 0.00015,
            "wick_level_tolerance_atr": 0.65,
        },
    },
}

ASSET_OVERRIDES = {
    "XAUUSDm": {
        "momentum_ignition": {
            "min_impulse_pct": 0.0007,
            "min_tick_volume": 70,
        },
        "breakout_retest": {"min_range_pct": 0.0008},
    },
    "XAGUSDm": {"momentum_ignition": {"min_impulse_pct": 0.0011}},
    "USOILm": {"momentum_ignition": {"min_impulse_pct": 0.0012}},
    "UKOILm": {"momentum_ignition": {"min_impulse_pct": 0.0012}},
    "BTCUSDm": {"momentum_ignition": {"min_impulse_pct": 0.0015}},
    "ETHUSDm": {"momentum_ignition": {"min_impulse_pct": 0.0017}},
}


def refresh_asset_presets(apps, schema_editor):
    Asset = apps.get_model("bots", "Asset")
    for asset in Asset.objects.all().iterator():
        overrides = deepcopy(CATEGORY_OVERRIDES.get(asset.category, {}))
        for strategy, values in ASSET_OVERRIDES.get(asset.symbol, {}).items():
            overrides.setdefault(strategy, {}).update(deepcopy(values))
        config = deepcopy(asset.recommended_config or {})
        config["strategy_overrides"] = overrides
        asset.recommended_config = config
        asset.recommended_config_version = 2
        asset.save(
            update_fields=["recommended_config", "recommended_config_version"]
        )


class Migration(migrations.Migration):
    dependencies = [("bots", "0047_asset_recommended_config_and_bot_preset_tracking")]

    operations = [migrations.RunPython(refresh_asset_presets, migrations.RunPython.noop)]
