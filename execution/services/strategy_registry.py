"""Pure candle strategies shared by the live scalper and historical simulations."""
from dataclasses import dataclass
from typing import Callable

from execution.services.strategies.breakout_retest import BreakoutRetestConfig, run_breakout_retest
from execution.services.strategies.doji_breakout import DojiBreakoutConfig, run_doji_breakout
from execution.services.strategies.momentum_ignition import MomentumIgnitionConfig, run_momentum_ignition
from execution.services.strategies.price_action_pinbar import PinBarConfig, run_price_action_pinbar
from execution.services.strategies.range_reversion import RangeReversionConfig, run_range_reversion
from execution.services.strategies.trend_pullback import TrendPullbackConfig, run_trend_pullback


@dataclass(frozen=True)
class ScalperStrategyEntry:
    runner: Callable
    config_factory: Callable[[], object]
    requires_symbol: bool = False


SCALPER_STRATEGY_REGISTRY = {
    "price_action_pinbar": ScalperStrategyEntry(run_price_action_pinbar, PinBarConfig, True),
    "trend_pullback": ScalperStrategyEntry(run_trend_pullback, TrendPullbackConfig),
    "doji_breakout": ScalperStrategyEntry(run_doji_breakout, DojiBreakoutConfig, True),
    "range_reversion": ScalperStrategyEntry(run_range_reversion, RangeReversionConfig),
    "breakout_retest": ScalperStrategyEntry(run_breakout_retest, BreakoutRetestConfig),
    "momentum_ignition": ScalperStrategyEntry(run_momentum_ignition, MomentumIgnitionConfig),
}
