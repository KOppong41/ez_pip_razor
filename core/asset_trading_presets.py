"""Canonical, pure-data recommended trading presets for supported assets.

These values are creation-time recommendations. Runtime callers should read the
copy stored on ``Asset.recommended_config`` so administrators can adjust it.
"""

from copy import deepcopy


ASSET_PRESET_VERSION = 2
WEEKDAYS = ["mon", "tue", "wed", "thu", "fri"]
ALL_DAYS = WEEKDAYS + ["sat", "sun"]

FX_TIMEFRAMES = {
    "default_timeframe": "5m",
    "allowed_timeframes": ["5m", "15m"],
    "context_timeframes": ["15m", "1h"],
}
FAST_TIMEFRAMES = {
    "default_timeframe": "5m",
    "allowed_timeframes": ["1m", "5m", "15m"],
    "context_timeframes": ["15m", "1h"],
}

SCHEDULES = {
    "ln_overlap": ("America/New_York", WEEKDAYS, "08:00", "12:00", True),
    "tokyo": ("Asia/Tokyo", WEEKDAYS, "09:00", "16:00", True),
    "london": ("Europe/London", WEEKDAYS, "08:00", "16:00", True),
    "london_open": ("Europe/London", WEEKDAYS, "07:00", "12:00", True),
    "ny_metals": ("America/New_York", WEEKDAYS, "07:30", "14:00", True),
    "ny_energy": ("America/New_York", WEEKDAYS, "08:00", "14:30", True),
    "ny_cash": ("America/New_York", WEEKDAYS, "09:30", "16:00", True),
    "xetra": ("Europe/Berlin", WEEKDAYS, "09:00", "17:30", True),
    "lse": ("Europe/London", WEEKDAYS, "08:00", "16:30", True),
    "crypto": ("UTC", ALL_DAYS, "00:00", "23:59", False),
}

EXIT_PRESETS = {
    "fixed_tp": {
        "exit_mode": "fixed_tp",
        "be_trigger_r": 1.0,
        "be_buffer_r": 0.15,
        "trail_trigger_r": 1.5,
        "trail_mode": "ema",
        "tp1_r": None,
        "tp1_close_pct": None,
        "trail_start_r": None,
    },
    "hybrid": {
        "exit_mode": "hybrid",
        "tp1_r": 1.0,
        "tp1_close_pct": 70,
        "be_trigger_r": 0.8,
        "be_buffer_r": 0.10,
        "trail_start_r": 1.0,
        "trail_trigger_r": 1.4,
        "trail_mode": "structure",
    },
    "hybrid_crypto": {
        "exit_mode": "hybrid",
        "tp1_r": 1.2,
        "tp1_close_pct": 70,
        "be_trigger_r": 0.9,
        "be_buffer_r": 0.10,
        "trail_start_r": 1.0,
        "trail_trigger_r": 1.6,
        "trail_mode": "structure",
    },
}

COMMON_DEFAULTS = {
    "engine_mode": "scalper",
    "position_sizing_mode": "risk",
    "risk_max_concurrent_positions": 1,
    "allow_opposite_scalp": False,
    "allow_live_account_execution": False,
    "kill_switch_enabled": True,
    "kill_switch_max_unrealized_pct": 3.0,
    "loss_streak_autopause_enabled": True,
    "max_loss_streak_before_pause": 3,
    "loss_streak_cooldown_min": 120,
    "soft_drawdown_limit_pct": 1.0,
    "soft_size_multiplier": 0.50,
    "hard_drawdown_limit_pct": 2.0,
    "hard_size_multiplier": 0.25,
    # Raw MT5-point fields are optional user overrides; zero means inherit.
    "max_spread_points": 0,
    "allowed_deviation_points": 0,
}

# Strategy detector tuning follows a deliberately shallow hierarchy:
# generic dataclass defaults -> category overrides below -> exceptional assets.
# These values are persisted into Asset.recommended_config so runtime behavior
# remains administratively inspectable and adjustable.
CATEGORY_STRATEGY_OVERRIDES = {
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

ASSET_STRATEGY_OVERRIDES = {
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


def _merged_strategy_overrides(symbol, category):
    result = deepcopy(CATEGORY_STRATEGY_OVERRIDES.get(category, {}))
    for strategy, values in ASSET_STRATEGY_OVERRIDES.get(symbol, {}).items():
        result.setdefault(strategy, {}).update(deepcopy(values))
    return result


def _schedule(name):
    timezone_name, days, start, end, enabled = SCHEDULES[name]
    return {
        "name": name,
        "enabled": enabled,
        "timezone": timezone_name,
        "allowed_days": list(days),
        "start": start,
        "end": end,
    }


def _preset(
    *,
    category,
    strategies,
    risk,
    score,
    sl,
    tp_r,
    exit_name,
    spread,
    slippage,
    interval,
    max_trades,
    schedule,
    news_currencies=(),
    energy_news=False,
):
    timeframes = FX_TIMEFRAMES if category == "forex" else FAST_TIMEFRAMES
    config = deepcopy(COMMON_DEFAULTS)
    config.update(deepcopy(timeframes))
    config.update(
        {
            "enabled_strategies": list(strategies),
            "risk_per_trade_pct": risk,
            "decision_min_score": score,
            "trade_interval_minutes": interval,
            "max_trades_per_day": max_trades,
            "trading_schedule": _schedule(schedule),
            "symbol_config": {
                "sl_points": {"min": sl[0], "max": sl[1], "unit": sl[2]},
                "tp_r_multiple": tp_r,
                **deepcopy(EXIT_PRESETS[exit_name]),
                "max_spread_points": spread[0],
                "max_spread_unit": spread[1],
                "max_slippage_points": slippage[0],
                "max_slippage_unit": slippage[1],
            },
            "news": {
                "currencies": list(news_currencies),
                "energy_events": bool(energy_news),
            },
        }
    )
    return config


# symbol: display name, category, strategies, risk, score, SL min/max/unit,
# TP R, exit preset, spread value/unit, slippage value/unit, interval, cap,
# schedule, news currencies, energy-news flag, compatibility min/recommended qty.
_SPECS = {
    "EURUSDm": ("EUR/USD", "forex", ["trend_pullback", "breakout_retest", "range_reversion"], .40, .60, (5, 10, "pips"), 1.50, "fixed_tp", (1.5, "pips"), (.5, "pips"), 10, 6, "ln_overlap", ["EUR", "USD"], False, ".10", ".10"),
    "GBPUSDm": ("GBP/USD", "forex", ["trend_pullback", "breakout_retest", "price_action_pinbar"], .35, .62, (7, 14, "pips"), 1.60, "fixed_tp", (2.0, "pips"), (.7, "pips"), 12, 5, "ln_overlap", ["GBP", "USD"], False, ".10", ".10"),
    "USDJPYm": ("USD/JPY", "forex", ["trend_pullback", "breakout_retest", "range_reversion"], .40, .60, (6, 12, "pips"), 1.50, "fixed_tp", (1.5, "pips"), (.5, "pips"), 10, 6, "tokyo", ["USD", "JPY"], False, ".10", ".10"),
    "USDCHFm": ("USD/CHF", "forex", ["trend_pullback", "range_reversion", "price_action_pinbar"], .35, .62, (6, 12, "pips"), 1.50, "fixed_tp", (1.8, "pips"), (.6, "pips"), 12, 5, "ln_overlap", ["USD", "CHF"], False, ".10", ".10"),
    "USDCADm": ("USD/CAD", "forex", ["trend_pullback", "breakout_retest", "range_reversion"], .35, .62, (7, 14, "pips"), 1.50, "fixed_tp", (2.0, "pips"), (.7, "pips"), 12, 5, "ln_overlap", ["USD", "CAD"], False, ".10", ".10"),
    "AUDUSDm": ("AUD/USD", "forex", ["trend_pullback", "range_reversion", "breakout_retest"], .35, .60, (6, 12, "pips"), 1.50, "fixed_tp", (1.8, "pips"), (.6, "pips"), 10, 6, "tokyo", ["AUD", "USD"], False, ".10", ".10"),
    "NZDUSDm": ("NZD/USD", "forex", ["trend_pullback", "range_reversion", "breakout_retest"], .35, .62, (7, 14, "pips"), 1.50, "fixed_tp", (2.2, "pips"), (.8, "pips"), 12, 5, "tokyo", ["NZD", "USD"], False, ".10", ".10"),
    "EURJPYm": ("EUR/JPY", "forex", ["trend_pullback", "breakout_retest", "price_action_pinbar"], .30, .64, (8, 16, "pips"), 1.60, "fixed_tp", (2.5, "pips"), (1.0, "pips"), 15, 5, "london_open", ["EUR", "JPY"], False, ".10", ".10"),
    "EURGBPm": ("EUR/GBP", "forex", ["range_reversion", "price_action_pinbar", "trend_pullback"], .35, .64, (5, 10, "pips"), 1.35, "fixed_tp", (2.0, "pips"), (.7, "pips"), 15, 5, "london", ["EUR", "GBP"], False, ".10", ".10"),
    "EURAUDm": ("EUR/AUD", "forex", ["trend_pullback", "breakout_retest", "price_action_pinbar"], .30, .66, (10, 20, "pips"), 1.60, "fixed_tp", (3.0, "pips"), (1.2, "pips"), 15, 4, "london_open", ["EUR", "AUD"], False, ".10", ".10"),
    "EURNZDm": ("EUR/NZD", "forex", ["trend_pullback", "breakout_retest", "price_action_pinbar"], .25, .68, (12, 24, "pips"), 1.60, "fixed_tp", (3.5, "pips"), (1.5, "pips"), 20, 4, "london_open", ["EUR", "NZD"], False, ".10", ".10"),
    "EURCADm": ("EUR/CAD", "forex", ["trend_pullback", "breakout_retest", "price_action_pinbar"], .30, .66, (9, 18, "pips"), 1.60, "fixed_tp", (3.0, "pips"), (1.2, "pips"), 15, 4, "ln_overlap", ["EUR", "CAD"], False, ".10", ".10"),
    "GBPJPYm": ("GBP/JPY", "forex", ["momentum_ignition", "breakout_retest", "trend_pullback"], .25, .68, (12, 25, "pips"), 1.80, "hybrid", (3.5, "pips"), (1.5, "pips"), 20, 4, "london_open", ["GBP", "JPY"], False, ".10", ".10"),
    "GBPCHFm": ("GBP/CHF", "forex", ["trend_pullback", "breakout_retest", "price_action_pinbar"], .30, .66, (10, 20, "pips"), 1.60, "fixed_tp", (3.0, "pips"), (1.2, "pips"), 15, 4, "london", ["GBP", "CHF"], False, ".10", ".10"),
    "AUDJPYm": ("AUD/JPY", "forex", ["trend_pullback", "breakout_retest", "range_reversion"], .30, .64, (8, 16, "pips"), 1.60, "fixed_tp", (2.5, "pips"), (1.0, "pips"), 15, 5, "tokyo", ["AUD", "JPY"], False, ".10", ".10"),
    "AUDCADm": ("AUD/CAD", "forex", ["range_reversion", "trend_pullback", "breakout_retest"], .30, .66, (8, 16, "pips"), 1.50, "fixed_tp", (3.0, "pips"), (1.2, "pips"), 15, 4, "ln_overlap", ["AUD", "CAD"], False, ".10", ".10"),
    "AUDNZDm": ("AUD/NZD", "forex", ["range_reversion", "price_action_pinbar", "trend_pullback"], .30, .66, (7, 14, "pips"), 1.35, "fixed_tp", (3.0, "pips"), (1.0, "pips"), 20, 4, "tokyo", ["AUD", "NZD"], False, ".10", ".10"),
    "NZDJPYm": ("NZD/JPY", "forex", ["trend_pullback", "breakout_retest", "range_reversion"], .30, .66, (9, 18, "pips"), 1.60, "fixed_tp", (3.0, "pips"), (1.2, "pips"), 15, 4, "tokyo", ["NZD", "JPY"], False, ".10", ".10"),
    "NZDCADm": ("NZD/CAD", "forex", ["range_reversion", "trend_pullback", "breakout_retest"], .25, .68, (9, 18, "pips"), 1.50, "fixed_tp", (3.5, "pips"), (1.5, "pips"), 20, 4, "ln_overlap", ["NZD", "CAD"], False, ".10", ".10"),
    "NZDCHFm": ("NZD/CHF", "forex", ["range_reversion", "trend_pullback", "price_action_pinbar"], .25, .68, (9, 18, "pips"), 1.50, "fixed_tp", (3.5, "pips"), (1.5, "pips"), 20, 4, "london", ["NZD", "CHF"], False, ".10", ".10"),
    "CADJPYm": ("CAD/JPY", "forex", ["trend_pullback", "breakout_retest", "momentum_ignition"], .30, .66, (10, 20, "pips"), 1.70, "hybrid", (3.0, "pips"), (1.2, "pips"), 15, 4, "ln_overlap", ["CAD", "JPY"], False, ".10", ".10"),
    "CADCHFm": ("CAD/CHF", "forex", ["range_reversion", "trend_pullback", "price_action_pinbar"], .30, .66, (8, 16, "pips"), 1.50, "fixed_tp", (3.0, "pips"), (1.2, "pips"), 15, 4, "ln_overlap", ["CAD", "CHF"], False, ".10", ".10"),
    "EURCHFm": ("EUR/CHF", "forex", ["range_reversion", "price_action_pinbar", "trend_pullback"], .35, .64, (6, 12, "pips"), 1.40, "fixed_tp", (2.2, "pips"), (.8, "pips"), 15, 5, "london", ["EUR", "CHF"], False, ".10", ".10"),
    "XAUUSDm": ("Gold", "commodities", ["price_action_pinbar", "breakout_retest", "momentum_ignition"], .30, .68, (.10, .30, "percent"), 1.70, "hybrid", (.015, "percent"), (.008, "percent"), 12, 5, "ny_metals", ["USD"], False, ".01", ".01"),
    "XAGUSDm": ("Silver", "commodities", ["breakout_retest", "momentum_ignition", "trend_pullback"], .25, .70, (.25, .70, "percent"), 1.80, "hybrid", (.08, "percent"), (.04, "percent"), 15, 4, "ny_metals", ["USD"], False, ".01", ".01"),
    "USOILm": ("WTI Oil", "commodities", ["breakout_retest", "momentum_ignition", "trend_pullback"], .25, .68, (.30, .80, "percent"), 1.80, "hybrid", (.10, "percent"), (.05, "percent"), 15, 4, "ny_energy", ["USD"], True, ".10", ".10"),
    "UKOILm": ("Brent Oil", "commodities", ["breakout_retest", "momentum_ignition", "trend_pullback"], .25, .68, (.30, .80, "percent"), 1.80, "hybrid", (.10, "percent"), (.05, "percent"), 15, 4, "ny_energy", ["USD"], True, ".10", ".10"),
    "US30m": ("Dow Jones 30", "indices", ["momentum_ignition", "breakout_retest", "trend_pullback"], .25, .68, (.10, .30, "percent"), 1.80, "hybrid", (.03, "percent"), (.015, "percent"), 15, 4, "ny_cash", ["USD"], False, ".10", ".10"),
    "US500m": ("S&P 500", "indices", ["trend_pullback", "breakout_retest", "range_reversion"], .30, .64, (.10, .30, "percent"), 1.60, "hybrid", (.025, "percent"), (.012, "percent"), 12, 5, "ny_cash", ["USD"], False, ".10", ".10"),
    "NAS100m": ("Nasdaq 100", "indices", ["momentum_ignition", "breakout_retest", "trend_pullback"], .25, .68, (.12, .35, "percent"), 1.80, "hybrid", (.035, "percent"), (.018, "percent"), 15, 4, "ny_cash", ["USD"], False, ".10", ".10"),
    "GER40m": ("DAX 40", "indices", ["breakout_retest", "trend_pullback", "momentum_ignition"], .25, .68, (.12, .35, "percent"), 1.70, "hybrid", (.035, "percent"), (.018, "percent"), 15, 4, "xetra", ["EUR"], False, ".10", ".10"),
    "UK100m": ("FTSE 100", "indices", ["trend_pullback", "breakout_retest", "range_reversion"], .30, .66, (.12, .35, "percent"), 1.60, "fixed_tp", (.035, "percent"), (.018, "percent"), 15, 4, "lse", ["GBP"], False, ".10", ".10"),
    "BTCUSDm": ("Bitcoin/USD", "crypto", ["momentum_ignition", "breakout_retest", "trend_pullback"], .25, .68, (.35, .90, "percent"), 1.80, "hybrid_crypto", (.06, "percent"), (.03, "percent"), 15, 6, "crypto", ["USD"], False, ".01", ".01"),
    "ETHUSDm": ("Ethereum/USD", "crypto", ["momentum_ignition", "breakout_retest", "trend_pullback"], .25, .68, (.40, 1.00, "percent"), 1.80, "hybrid_crypto", (.15, "percent"), (.08, "percent"), 15, 6, "crypto", ["USD"], False, ".01", ".01"),
}


ASSET_CATALOG = {}
ASSET_TRADING_PRESETS = {}
for _symbol, _spec in _SPECS.items():
    (
        _display_name, _category, _strategies, _risk, _score, _sl, _tp_r,
        _exit_name, _spread, _slippage, _interval, _max_trades, _schedule_name,
        _news_currencies, _energy_news, _min_qty, _recommended_qty,
    ) = _spec
    ASSET_CATALOG[_symbol] = {
        "symbol": _symbol,
        "display_name": _display_name,
        "category": _category,
        "min_qty": _min_qty,
        "recommended_qty": _recommended_qty,
    }
    ASSET_TRADING_PRESETS[_symbol] = _preset(
        category=_category,
        strategies=_strategies,
        risk=_risk,
        score=_score,
        sl=_sl,
        tp_r=_tp_r,
        exit_name=_exit_name,
        spread=_spread,
        slippage=_slippage,
        interval=_interval,
        max_trades=_max_trades,
        schedule=_schedule_name,
        news_currencies=_news_currencies,
        energy_news=_energy_news,
    )
    ASSET_TRADING_PRESETS[_symbol]["strategy_overrides"] = (
        _merged_strategy_overrides(_symbol, _category)
    )


def recommended_config_for(symbol):
    """Return a caller-safe copy of the canonical preset for ``symbol``."""
    return deepcopy(ASSET_TRADING_PRESETS.get(symbol, {}))
