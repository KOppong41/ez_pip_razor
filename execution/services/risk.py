
from dataclasses import dataclass
from typing import Tuple

@dataclass
class RiskConfig:
    max_positions_per_symbol: int = 1
    max_concurrent_positions: int = 5
    allowed_symbols: tuple = ()

def check_risk(signal, open_positions_count_symbol: int, open_positions_total: int, cfg: RiskConfig) -> Tuple[bool, str]:
    if cfg.allowed_symbols and signal.symbol not in cfg.allowed_symbols:
        return False, "symbol_not_allowed"
    if open_positions_total >= cfg.max_concurrent_positions:
        return False, "max_concurrent_positions"
    if open_positions_count_symbol >= cfg.max_positions_per_symbol:
        return False, "max_positions_per_symbol"
    return True, "ok"
