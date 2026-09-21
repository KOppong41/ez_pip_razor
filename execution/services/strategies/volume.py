"""Feed-relative volume from completed candles, without candidate lookahead."""
from decimal import Decimal, InvalidOperation
from statistics import median


def relative_tick_volume(candles, lookback: int):
    # -2 is the impulse/breakout; -1 is its pullback/retest. Neither belongs
    # in the baseline. Require a complete window rather than a noisy warmup.
    if lookback < 3 or len(candles) < lookback + 2:
        raise ValueError("insufficient_volume_history")
    try:
        volumes = [Decimal(str(candle.get("tick_volume"))) for candle in candles[-lookback - 2:-1]]
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError("invalid_volume_data") from None
    if any(not value.is_finite() or value < 0 for value in volumes):
        raise ValueError("invalid_volume_data")
    baseline = median(volumes[:-1])
    if baseline <= 0:
        raise ValueError("zero_volume_baseline")
    ratio = volumes[-1] / baseline
    return ratio, {
        "relative_volume": float(ratio),
        "volume_baseline_median": float(baseline),
        "volume_lookback": lookback,
    }
