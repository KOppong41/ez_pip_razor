import csv
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Iterable, List, Optional

from django.core.management.base import BaseCommand, CommandParser

from execution.services.engine import EngineContext, EngineDecision, run_engine
from execution.services.marketdata import Candle
from execution.services.runtime_config import get_runtime_config


@dataclass
class TradeResult:
    direction: str
    entry_time: datetime
    exit_time: datetime
    entry_price: Decimal
    exit_price: Decimal
    reason: str
    score: float
    pnl: Decimal


def _parse_candle_row(row: dict) -> Candle:
    """
    Expect CSV columns: time, open, high, low, close, tick_volume (tick_volume optional).
    time must be ISO-8601 (e.g. 2024-01-01T12:00:00).
    """
    return {
        "time": datetime.fromisoformat(row["time"]),
        "open": Decimal(str(row["open"])),
        "high": Decimal(str(row["high"])),
        "low": Decimal(str(row["low"])),
        "close": Decimal(str(row["close"])),
        "tick_volume": int(row.get("tick_volume", 0) or 0),
    }


def load_candles(csv_path: str, limit: Optional[int] = None) -> List[Candle]:
    with open(csv_path, newline="") as fh:
        reader = csv.DictReader(fh)
        candles = [_parse_candle_row(r) for r in reader]
    return candles[-limit:] if limit else candles


def _maybe_exit(position: dict, candle: Candle) -> Optional[TradeResult]:
    """Check SL/TP hits for the open position on the current candle."""
    direction = position["direction"]
    sl = position.get("sl")
    tp = position.get("tp")
    exit_price = None
    reason = None

    if direction == "buy":
        if sl is not None and candle["low"] <= sl:
            exit_price, reason = sl, "sl"
        elif tp is not None and candle["high"] >= tp:
            exit_price, reason = tp, "tp"
    else:  # sell
        if sl is not None and candle["high"] >= sl:
            exit_price, reason = sl, "sl"
        elif tp is not None and candle["low"] <= tp:
            exit_price, reason = tp, "tp"

    if exit_price is None:
        return None

    pnl = (
        exit_price - position["entry_price"]
        if direction == "buy"
        else position["entry_price"] - exit_price
    )
    return TradeResult(
        direction=direction,
        entry_time=position["entry_time"],
        exit_time=candle["time"],
        entry_price=position["entry_price"],
        exit_price=exit_price,
        reason=reason,
        score=position["score"],
        pnl=pnl,
    )


def backtest_engine(
    candles: List[Candle],
    symbol: str,
    timeframe: str,
    min_score: float,
    warmup: int,
) -> List[TradeResult]:
    """
    Simple walk-forward backtest:
    - run engine on each bar after warmup
    - if decision.action=='open' and score >= min_score, enter next-bar open
    - manage position with SL/TP hits; exit remaining position at final close
    - single-position at a time (no stacking)
    """
    if len(candles) < warmup + 2:
        return []

    trades: List[TradeResult] = []
    position = None

    for i in range(warmup, len(candles) - 1):
        bar = candles[i]

        # Manage existing position on current bar
        if position:
            closed = _maybe_exit(position, bar)
            if closed:
                trades.append(closed)
                position = None

        # Only one position at a time for this quick pass
        if position:
            continue

        window = candles[: i + 1]
        ctx = EngineContext(
            symbol=symbol,
            timeframe=timeframe,
            entry_candles=window,
            htf_candles=None,
        )
        dec: EngineDecision = run_engine(ctx)
        if dec.action != "open" or not dec.direction:
            continue
        if float(dec.score or 0.0) < float(min_score):
            continue

        next_bar = candles[i + 1]
        position = {
            "direction": dec.direction,
            "entry_price": next_bar["open"],
            "entry_time": next_bar["time"],
            "sl": dec.sl,
            "tp": dec.tp,
            "score": float(dec.score or 0.0),
        }

    # Force-close any remaining position at final close
    if position:
        last = candles[-1]
        trades.append(
            TradeResult(
                direction=position["direction"],
                entry_time=position["entry_time"],
                exit_time=last["time"],
                entry_price=position["entry_price"],
                exit_price=last["close"],
                reason="end_of_data",
                score=position["score"],
                pnl=(
                    last["close"] - position["entry_price"]
                    if position["direction"] == "buy"
                    else position["entry_price"] - last["close"]
                ),
            )
        )

    return trades


def summarise(trades: Iterable[TradeResult]) -> dict:
    trades = list(trades)
    total = len(trades)
    wins = sum(1 for t in trades if t.pnl > 0)
    losses = sum(1 for t in trades if t.pnl < 0)
    flats = total - wins - losses
    total_pnl = sum((t.pnl for t in trades), Decimal("0"))
    avg_pnl = (total_pnl / Decimal(str(total))) if total else Decimal("0")
    win_rate = (wins / total) if total else 0.0
    return {
        "trades": total,
        "wins": wins,
        "losses": losses,
        "flats": flats,
        "win_rate": win_rate,
        "total_pnl": total_pnl,
        "avg_pnl": avg_pnl,
    }


class Command(BaseCommand):
    help = "Backtest the internal engine (engine_v2) on a CSV of candles and apply the min_score filter."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("csv_path", type=str, help="Path to CSV with columns: time,open,high,low,close[,tick_volume]")
        parser.add_argument("--symbol", type=str, default="", help="Symbol label for reporting (not used in logic).")
        parser.add_argument("--timeframe", type=str, default="", help="Timeframe label for reporting.")
        parser.add_argument("--min-score", type=float, dest="min_score", default=None, help="Minimum score filter (defaults to DECISION_MIN_SCORE).")
        parser.add_argument("--warmup", type=int, default=200, help="Bars to warm up indicators before testing.")
        parser.add_argument("--limit", type=int, default=None, help="Optional limit of most recent bars to load.")

    def handle(self, *args, **options):
        csv_path = options["csv_path"]
        min_score = options["min_score"]
        warmup = options["warmup"]
        limit = options["limit"]
        symbol = options.get("symbol") or ""
        timeframe = options.get("timeframe") or ""

        if min_score is None:
            min_score = float(get_runtime_config().decision_min_score)

        candles = load_candles(csv_path, limit=limit)
        trades = backtest_engine(
            candles=candles,
            symbol=symbol,
            timeframe=timeframe,
            min_score=min_score,
            warmup=warmup,
        )
        summary = summarise(trades)

        self.stdout.write(self.style.SUCCESS(f"Backtest complete on {len(candles)} bars"))
        self.stdout.write(f"symbol={symbol or '-'} tf={timeframe or '-'} min_score={min_score}")
        self.stdout.write(f"trades={summary['trades']} wins={summary['wins']} losses={summary['losses']} flats={summary['flats']}")
        self.stdout.write(f"win_rate={summary['win_rate']:.2%} avg_pnl={summary['avg_pnl']} total_pnl={summary['total_pnl']}")

        # Optional: dump top few trades for inspection
        for t in trades[:5]:
            self.stdout.write(
                f"- {t.direction} entry={t.entry_time} {t.entry_price} exit={t.exit_time} {t.exit_price} "
                f"pnl={t.pnl} reason={t.reason} score={t.score}"
            )
