from dataclasses import dataclass
from decimal import Decimal
from typing import Literal, Optional


Action = Literal["open", "skip", "close"]
Direction = Literal["buy", "sell"]
Trend = Literal["up", "down", "flat"]


@dataclass
class EngineDecision:
    """Result returned by an internal strategy without importing the engine router."""

    action: Action
    direction: Optional[Direction] = None
    sl: Optional[Decimal] = None
    tp: Optional[Decimal] = None
    reason: str = ""
    strategy: str = ""
    trend: Trend = "flat"
    score: float = 0.0
    metadata: Optional[dict] = None
