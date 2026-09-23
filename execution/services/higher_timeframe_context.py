"""Completed context frames used by both live scans and bot replay."""
from execution.services.scalper_config import normalize_execution_timeframe

FRAME_MINUTES = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "4h": 240, "1d": 1440}


def analyze_context(timeframes, fetch, analyze):
    frames = {normalize_execution_timeframe(value) for value in timeframes or ("15m",)}
    if None in frames or not frames.issubset(FRAME_MINUTES):
        return None, {}, "htf_timeframe_unsupported"
    # Keep every frame's evidence, including when the immediate frame is
    # neutral. A valid neutral analysis is different from missing candle data.
    details = {frame: analyze(fetch(frame)) for frame in sorted(frames, key=FRAME_MINUTES.get)}
    if any(not isinstance(detail, dict) or detail.get("bias", "invalid") not in (None, "buy", "sell")
           for detail in details.values()):
        return None, details, "htf_bias_unavailable"
    immediate = min(frames, key=FRAME_MINUTES.get)
    dominant = max(frames, key=FRAME_MINUTES.get)
    context = {
        **details[immediate], "frames": details,
        "immediate_timeframe": immediate, "dominant_timeframe": dominant,
        "regime": details[dominant],
    }
    directional = {detail["bias"] for detail in details.values() if detail["bias"] is not None}
    if len(directional) > 1:
        return None, context, "htf_context_conflict"
    if any(detail["bias"] is None for detail in details.values()):
        return None, context, "htf_bias_neutral"
    return details[dominant]["bias"], context, None
