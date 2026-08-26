from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional


@dataclass(frozen=True)
class ExitPolicy:
    mode: str  # fixed_tp | trail_only | hybrid
    tp_r_multiple: Optional[Decimal] = None
    trail_start_r: Optional[Decimal] = None
    tp1_r: Optional[Decimal] = None
    tp1_close_pct: Optional[int] = None

    def validate(self):
        mode = (self.mode or "").lower()
        if mode not in ("fixed_tp", "trail_only", "hybrid"):
            raise ValueError("Invalid exit_mode; must be fixed_tp, trail_only, or hybrid")

        if mode == "fixed_tp":
            if self.tp_r_multiple is None:
                raise ValueError("fixed_tp requires tp_r_multiple")
            return

        if mode == "trail_only":
            if self.trail_start_r is None:
                raise ValueError("trail_only requires trail_start_r")
            return

        if mode == "hybrid":
            if self.tp1_r is None or self.tp1_close_pct is None or self.trail_start_r is None:
                raise ValueError("hybrid requires tp1_r, tp1_close_pct, and trail_start_r")
            if not (0 < self.tp1_close_pct <= 100):
                raise ValueError("tp1_close_pct must be between 1 and 100")
            if self.trail_start_r >= self.tp1_r:
                raise ValueError("trail_start_r must be less than tp1_r to avoid dead trailing")
            return
