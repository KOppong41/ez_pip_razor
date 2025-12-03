from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from functools import lru_cache
from typing import Any

from django.conf import settings

from execution.models import ExecutionSetting


@dataclass
class RuntimeConfig:
    decision_min_score: float
    decision_flip_score: float
    decision_allow_hedging: bool
    decision_flip_cooldown_min: int
    decision_max_flips_per_day: int
    decision_order_cooldown_sec: int
    decision_scalp_sl_offset: Decimal
    decision_scalp_tp_offset: Decimal
    decision_scalp_qty_multiplier: Decimal
    order_ack_timeout_seconds: int
    early_exit_max_unrealized_pct: Decimal
    trailing_trigger: Decimal
    trailing_distance: Decimal
    paper_start_balance: Decimal
    mt5_default_contract_size: int
    max_order_lot: Decimal
    max_order_notional: Decimal


def _decimal_or(default: Decimal, value: Any) -> Decimal:
    try:
        return Decimal(str(value))
    except Exception:
        return Decimal(str(default))


def _build_runtime_config(defaults: dict[str, Any], cfg: ExecutionSetting | None = None) -> RuntimeConfig:
    def _pick(attr: str):
        if cfg is None:
            return defaults.get(attr)
        val = getattr(cfg, attr, None)
        return val if val not in (None, "") else defaults.get(attr)

    return RuntimeConfig(
        decision_min_score=float(_pick("decision_min_score")),
        decision_flip_score=float(_pick("decision_flip_score")),
        decision_allow_hedging=bool(_pick("decision_allow_hedging")),
        decision_flip_cooldown_min=int(_pick("decision_flip_cooldown_min")),
        decision_max_flips_per_day=int(_pick("decision_max_flips_per_day")),
        decision_order_cooldown_sec=int(_pick("decision_order_cooldown_sec")),
        decision_scalp_sl_offset=_decimal_or(Decimal("0.0003"), _pick("decision_scalp_sl_offset")),
        decision_scalp_tp_offset=_decimal_or(Decimal("0.0005"), _pick("decision_scalp_tp_offset")),
        decision_scalp_qty_multiplier=_decimal_or(Decimal("0.3"), _pick("decision_scalp_qty_multiplier")),
        order_ack_timeout_seconds=int(_pick("order_ack_timeout_seconds")),
        early_exit_max_unrealized_pct=_decimal_or(Decimal("0.02"), _pick("early_exit_max_unrealized_pct")),
        trailing_trigger=_decimal_or(Decimal("0.0005"), _pick("trailing_trigger")),
        trailing_distance=_decimal_or(Decimal("0.0003"), _pick("trailing_distance")),
        paper_start_balance=_decimal_or(Decimal("100000"), _pick("paper_start_balance")),
        mt5_default_contract_size=int(_pick("mt5_default_contract_size")),
        max_order_lot=_decimal_or(Decimal("0.05"), _pick("max_order_lot")),
        max_order_notional=_decimal_or(Decimal("5000"), _pick("max_order_notional")),
    )


@lru_cache(maxsize=1)
def _get_cached_runtime_config() -> RuntimeConfig:
    defaults = ExecutionSetting.defaults_from_settings()
    cfg, _ = ExecutionSetting.objects.get_or_create(key="default", defaults=defaults)
    return _build_runtime_config(defaults, cfg)


def clear_runtime_config_cache():
    _get_cached_runtime_config.cache_clear()


def get_runtime_config() -> RuntimeConfig:
    """
    Load runtime settings from the singleton ExecutionSetting row.
    Falls back to django settings defaults when fields are blank/missing.
    Cached until explicitly cleared (see ExecutionSetting.save()).
    """
    defaults = ExecutionSetting.defaults_from_settings()

    # In tests, honor override_settings without persisting to DB and avoid caching.
    if getattr(settings, "TESTING", False):
        return _build_runtime_config(defaults, cfg=None)

    return _get_cached_runtime_config()
