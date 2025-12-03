
from dataclasses import dataclass
from typing import Literal, Dict, Any
from execution.models import Signal

DecisionAction = Literal["open", "close", "ignore"]

@dataclass
class StrategyDecision:
    action: DecisionAction
    reason: str = ""
    params: Dict[str, Any] = None
    score: float = 0.0

def naive_strategy(signal: Signal) -> StrategyDecision:
    """
    Simple placeholder:
    - if direction=buy -> open
    - if direction=sell -> open
    Future: add filters (session, spread, indicators).
    """
    payload_score = None
    try:
        payload_score = float(signal.payload.get("score")) if signal.payload else None
    except Exception:
        payload_score = None
    score = payload_score if payload_score is not None else 0.5

    return StrategyDecision(
        action="open",
        reason=f"naive:{signal.direction}",
        params={"symbol": signal.symbol, "timeframe": signal.timeframe, "direction": signal.direction},
        score=score,
    )
