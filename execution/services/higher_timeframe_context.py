"""Completed context frames used by both live scans and bot replay."""
from execution.services.scalper_config import normalize_execution_timeframe

FRAME_MINUTES = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "4h": 240, "1d": 1440}


def analyze_context(timeframes, fetch, analyze):
    frames = {normalize_execution_timeframe(value) for value in timeframes or ("15m",)}
    if None in frames or not frames.issubset(FRAME_MINUTES):
        return None, {}, "htf_timeframe_unsupported"
    details = {}
    for frame in sorted(frames, key=FRAME_MINUTES.get):
        detail = analyze(fetch(frame))
        details[frame] = detail
        if not detail or not detail.get("bias"):
            return None, details, "htf_bias_unavailable"
    immediate = min(frames, key=FRAME_MINUTES.get)
    dominant = max(frames, key=FRAME_MINUTES.get)
    if len({detail["bias"] for detail in details.values()}) != 1:
        return None, details, "htf_context_conflict"
    return details[dominant]["bias"], {
        **details[immediate], "frames": details,
        "immediate_timeframe": immediate, "dominant_timeframe": dominant,
        "regime": details[dominant],
    }, None
