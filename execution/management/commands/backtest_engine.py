import csv
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Iterable, List, Optional

from django.core.management.base import BaseCommand, CommandError, CommandParser

from execution.services.engine import EngineContext, EngineDecision, run_engine
from execution.services.marketdata import Candle
from execution.services.runtime_config import get_runtime_config


@dataclass(frozen=True)
class BacktestConfig:
    quantity: Decimal = Decimal("0.01")
    contract_size: Decimal = Decimal("100000")
    point_size: Decimal = Decimal("0.00001")
    spread_points: Decimal = Decimal("10")
    slippage_points: Decimal = Decimal("1")
    commission_per_lot: Decimal = Decimal("7")
    swap_per_lot_day: Decimal = Decimal("0")
    same_bar_policy: str = "stop_first"

    def validate(self) -> None:
        if self.quantity <= 0 or self.contract_size <= 0 or self.point_size <= 0:
            raise ValueError("quantity, contract_size and point_size must be positive")
        if any(
            value < 0
            for value in (
                self.spread_points,
                self.slippage_points,
                self.commission_per_lot,
            )
        ):
            raise ValueError("spread, slippage and commission cannot be negative")
        if self.same_bar_policy not in {"stop_first", "target_first", "nearest_open"}:
            raise ValueError("unsupported same_bar_policy")


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
    gross_pnl: Decimal = Decimal("0")
    spread_cost: Decimal = Decimal("0")
    slippage_cost: Decimal = Decimal("0")
    commission: Decimal = Decimal("0")
    swap: Decimal = Decimal("0")


def _parse_candle_row(row: dict) -> Candle:
    """Parse time, OHLC and optional tick_volume from one CSV row."""
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
        candles = [_parse_candle_row(row) for row in csv.DictReader(fh)]
    return candles[-limit:] if limit else candles


def resample_candles(candles: List[Candle], multiple: int) -> List[Candle]:
    """Aggregate complete sequential entry bars without introducing look-ahead."""
    if multiple < 2:
        raise ValueError("HTF multiple must be at least 2")
    result: List[Candle] = []
    for start in range(0, len(candles) - multiple + 1, multiple):
        group = candles[start : start + multiple]
        result.append(
            {
                # Timestamp the HTF candle at its final constituent bar so a
                # walk-forward filter cannot expose that candle early.
                "time": group[-1]["time"],
                "open": group[0]["open"],
                "high": max(candle["high"] for candle in group),
                "low": min(candle["low"] for candle in group),
                "close": group[-1]["close"],
                "tick_volume": sum(int(candle.get("tick_volume", 0) or 0) for candle in group),
            }
        )
    return result


def _timeframe_minutes(timeframe: str) -> Optional[int]:
    value = (timeframe or "").strip().lower()
    try:
        if value.endswith("m"):
            return int(value[:-1])
        if value.endswith("h"):
            return int(value[:-1]) * 60
    except ValueError:
        return None
    return None


def _default_htf_multiple(timeframe: str) -> int:
    minutes = _timeframe_minutes(timeframe)
    if minutes and minutes < 15 and 15 % minutes == 0:
        return 15 // minutes
    return 4


def _execution_price(raw_price: Decimal, direction: str, opening: bool, cfg: BacktestConfig) -> Decimal:
    half_spread = cfg.spread_points * cfg.point_size / Decimal("2")
    slippage = cfg.slippage_points * cfg.point_size
    pays_ask = (direction == "buy" and opening) or (direction == "sell" and not opening)
    return raw_price + half_spread + slippage if pays_ask else raw_price - half_spread - slippage


def _build_result(position: dict, candle: Candle, raw_exit: Decimal, reason: str, cfg: BacktestConfig) -> TradeResult:
    direction = position["direction"]
    raw_entry = position["raw_entry_price"]
    price_delta = raw_exit - raw_entry if direction == "buy" else raw_entry - raw_exit
    gross = price_delta * cfg.quantity * cfg.contract_size
    spread_cost = cfg.spread_points * cfg.point_size * cfg.quantity * cfg.contract_size
    slippage_cost = Decimal("2") * cfg.slippage_points * cfg.point_size * cfg.quantity * cfg.contract_size
    commission = cfg.commission_per_lot * cfg.quantity
    elapsed_seconds = max(0.0, (candle["time"] - position["entry_time"]).total_seconds())
    elapsed_days = Decimal(str(elapsed_seconds)) / Decimal("86400")
    swap = cfg.swap_per_lot_day * cfg.quantity * elapsed_days
    pnl = gross - spread_cost - slippage_cost - commission + swap
    return TradeResult(
        direction=direction,
        entry_time=position["entry_time"],
        exit_time=candle["time"],
        entry_price=position["entry_price"],
        exit_price=_execution_price(raw_exit, direction, False, cfg),
        reason=reason,
        score=position["score"],
        pnl=pnl,
        gross_pnl=gross,
        spread_cost=spread_cost,
        slippage_cost=slippage_cost,
        commission=commission,
        swap=swap,
    )


def _maybe_exit(position: dict, candle: Candle, cfg: BacktestConfig) -> Optional[TradeResult]:
    """Resolve SL/TP hits using an explicit, reproducible same-bar policy."""
    direction = position["direction"]
    sl = position.get("sl")
    tp = position.get("tp")
    if direction == "buy":
        hit_sl = sl is not None and candle["low"] <= sl
        hit_tp = tp is not None and candle["high"] >= tp
    else:
        hit_sl = sl is not None and candle["high"] >= sl
        hit_tp = tp is not None and candle["low"] <= tp

    if not hit_sl and not hit_tp:
        return None
    if hit_sl and hit_tp:
        if cfg.same_bar_policy == "target_first":
            raw_exit, reason = tp, "tp"
        elif cfg.same_bar_policy == "nearest_open":
            open_price = candle["open"]
            if abs(open_price - tp) < abs(open_price - sl):
                raw_exit, reason = tp, "tp"
            else:
                raw_exit, reason = sl, "sl"
        else:
            raw_exit, reason = sl, "sl"
    elif hit_sl:
        raw_exit, reason = sl, "sl"
    else:
        raw_exit, reason = tp, "tp"
    return _build_result(position, candle, raw_exit, reason, cfg)


def backtest_engine(
    candles: List[Candle],
    symbol: str,
    timeframe: str,
    min_score: float,
    warmup: int,
    *,
    config: Optional[BacktestConfig] = None,
    htf_candles: Optional[List[Candle]] = None,
    htf_multiple: Optional[int] = None,
) -> List[TradeResult]:
    """Walk-forward, single-position backtest with costs and completed HTF bars."""
    cfg = config or BacktestConfig()
    cfg.validate()
    if len(candles) < warmup + 2:
        return []

    if htf_candles is None:
        htf_candles = resample_candles(candles, htf_multiple or _default_htf_multiple(timeframe))

    trades: List[TradeResult] = []
    position = None
    for i in range(warmup, len(candles) - 1):
        bar = candles[i]
        if position:
            closed = _maybe_exit(position, bar, cfg)
            if closed:
                trades.append(closed)
                position = None
        if position:
            continue

        completed_htf = [candle for candle in htf_candles if candle["time"] <= bar["time"]]
        ctx = EngineContext(
            symbol=symbol,
            timeframe=timeframe,
            entry_candles=candles[: i + 1],
            htf_candles=completed_htf or None,
        )
        dec: EngineDecision = run_engine(ctx)
        if dec.action != "open" or not dec.direction or float(dec.score or 0) < float(min_score):
            continue

        next_bar = candles[i + 1]
        raw_entry = next_bar["open"]
        position = {
            "direction": dec.direction,
            "raw_entry_price": raw_entry,
            "entry_price": _execution_price(raw_entry, dec.direction, True, cfg),
            "entry_time": next_bar["time"],
            "sl": dec.sl,
            "tp": dec.tp,
            "score": float(dec.score or 0),
        }

    if position:
        last = candles[-1]
        closed = _maybe_exit(position, last, cfg)
        trades.append(closed or _build_result(position, last, last["close"], "end_of_data", cfg))
    return trades


def summarise(trades: Iterable[TradeResult]) -> dict:
    trades = list(trades)
    total = len(trades)
    wins = sum(1 for trade in trades if trade.pnl > 0)
    losses = sum(1 for trade in trades if trade.pnl < 0)
    total_pnl = sum((trade.pnl for trade in trades), Decimal("0"))
    return {
        "trades": total,
        "wins": wins,
        "losses": losses,
        "flats": total - wins - losses,
        "win_rate": (wins / total) if total else 0.0,
        "total_pnl": total_pnl,
        "avg_pnl": (total_pnl / Decimal(total)) if total else Decimal("0"),
        "gross_pnl": sum((trade.gross_pnl for trade in trades), Decimal("0")),
        "costs": sum(
            (trade.spread_cost + trade.slippage_cost + trade.commission - trade.swap for trade in trades),
            Decimal("0"),
        ),
    }


class Command(BaseCommand):
    help = "Walk-forward backtest with HTF context and configurable broker economics."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("csv_path", help="CSV: time,open,high,low,close[,tick_volume]")
        parser.add_argument("--htf-csv", default=None, help="Optional pre-aggregated HTF candle CSV")
        parser.add_argument("--htf-multiple", type=int, default=None)
        parser.add_argument("--symbol", default="")
        parser.add_argument("--timeframe", default="5m")
        parser.add_argument("--min-score", type=float, dest="min_score", default=None)
        parser.add_argument("--warmup", type=int, default=200)
        parser.add_argument("--limit", type=int, default=None)
        parser.add_argument("--quantity", default="0.01")
        parser.add_argument("--contract-size", default="100000")
        parser.add_argument("--point-size", default="0.00001")
        parser.add_argument("--spread-points", default="10")
        parser.add_argument("--slippage-points", default="1")
        parser.add_argument("--commission-per-lot", default="7")
        parser.add_argument("--swap-per-lot-day", default="0")
        parser.add_argument(
            "--same-bar-policy",
            choices=["stop_first", "target_first", "nearest_open"],
            default="stop_first",
        )

    def handle(self, *args, **options):
        min_score = options["min_score"]
        if min_score is None:
            min_score = float(get_runtime_config().decision_min_score)
        try:
            cfg = BacktestConfig(
                quantity=Decimal(options["quantity"]),
                contract_size=Decimal(options["contract_size"]),
                point_size=Decimal(options["point_size"]),
                spread_points=Decimal(options["spread_points"]),
                slippage_points=Decimal(options["slippage_points"]),
                commission_per_lot=Decimal(options["commission_per_lot"]),
                swap_per_lot_day=Decimal(options["swap_per_lot_day"]),
                same_bar_policy=options["same_bar_policy"],
            )
            cfg.validate()
        except (ValueError, ArithmeticError) as exc:
            raise CommandError(str(exc)) from exc

        candles = load_candles(options["csv_path"], limit=options["limit"])
        htf_candles = load_candles(options["htf_csv"]) if options["htf_csv"] else None
        trades = backtest_engine(
            candles,
            options["symbol"],
            options["timeframe"],
            min_score,
            options["warmup"],
            config=cfg,
            htf_candles=htf_candles,
            htf_multiple=options["htf_multiple"],
        )
        summary = summarise(trades)
        self.stdout.write(self.style.SUCCESS(f"Backtest complete on {len(candles)} bars"))
        self.stdout.write(
            f"symbol={options['symbol'] or '-'} tf={options['timeframe']} min_score={min_score} "
            f"qty={cfg.quantity} spread={cfg.spread_points}pt slippage={cfg.slippage_points}pt "
            f"commission={cfg.commission_per_lot}/lot swap={cfg.swap_per_lot_day}/lot/day "
            f"same_bar={cfg.same_bar_policy}"
        )
        self.stdout.write(
            f"trades={summary['trades']} wins={summary['wins']} losses={summary['losses']} "
            f"flats={summary['flats']} win_rate={summary['win_rate']:.2%} "
            f"gross={summary['gross_pnl']} costs={summary['costs']} net={summary['total_pnl']}"
        )
        for trade in trades[:5]:
            self.stdout.write(
                f"- {trade.direction} entry={trade.entry_time} {trade.entry_price} "
                f"exit={trade.exit_time} {trade.exit_price} net={trade.pnl} "
                f"reason={trade.reason} score={trade.score}"
            )
