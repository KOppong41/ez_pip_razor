from __future__ import annotations

import re
from functools import lru_cache

_SANITIZE_PATTERN = re.compile(r"[^A-Z0-9]")

# Map broker-specific aliases to canonical asset symbols.
_CANONICAL_OVERRIDES: dict[str, str] = {
    "XAUUSD": "XAUUSD",
    "XAUUSDM": "XAUUSD",
    "GOLD": "XAUUSD",
    "GOLDM": "XAUUSD",
    "XAUUSDMICRO": "XAUUSD",
    "BTCUSD": "BTCUSD",
    "BTCUSDM": "BTCUSD",
    "BTCUSDTP": "BTCUSD",
    "ETHUSD": "ETHUSD",
    "ETHUSDM": "ETHUSD",
    "EURUSD": "EURUSD",
    "EURUSDM": "EURUSD",
    "GBPUSD": "GBPUSD",
    "GBPUSDM": "GBPUSD",
}

_SUFFIXES = ("MICRO", "MINI", "PRO")


@lru_cache(maxsize=256)
def canonical_symbol(symbol: str | None) -> str:
    """
    Normalize broker-specific symbols (e.g., BTCUSDm, XAUUSD.micro) to canonical asset keys.
    """
    if not symbol:
        return ""
    sanitized = _SANITIZE_PATTERN.sub("", symbol.strip().upper())
    if not sanitized:
        return ""

    if sanitized in _CANONICAL_OVERRIDES:
        return _CANONICAL_OVERRIDES[sanitized]

    # Remove known suffixes such as 'micro' or final 'm'.
    for suffix in _SUFFIXES:
        if sanitized.endswith(suffix):
            sanitized = sanitized[: -len(suffix)]
            break
    if sanitized.endswith("M"):
        sanitized = sanitized[:-1]

    return _CANONICAL_OVERRIDES.get(sanitized, sanitized)


def symbols_match(left: str | None, right: str | None) -> bool:
    """
    Safe comparison helper that applies canonical normalization to both inputs.
    """
    return canonical_symbol(left) == canonical_symbol(right)


__all__ = ["canonical_symbol", "symbols_match"]
