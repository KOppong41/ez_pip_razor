"""Bounded, broker-independent replay of candle strategies using bid OHLC data.

One fixed-size position at a time; signals use completed candles, entries use
the next available open. This intentionally does not replay the live risk,
portfolio, news, automatic strategy selection, trailing, or partial-exit layers.
"""
import csv
import hashlib
import io
import re
from collections import Counter
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

from execution.services.strategy_registry import SCALPER_STRATEGY_REGISTRY

MAX_CSV_BYTES = 25_000_000
MAX_BARS = 150_000
MAX_REPLAY_WORK = 15_000_000
MAX_EQUITY_POINTS = 5_000
TIMEFRAMES = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "4h": 240}
ZERO = Decimal("0")


def json_safe(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    return value


def decimal_field(data, name, default=None, *, minimum=ZERO, positive=False):
    try:
        result = Decimal(str(data.get(name, default)))
        if not result.is_finite() or result.copy_abs() > Decimal("1e12"):
            raise ValueError
        if result and result.copy_abs() < Decimal("1e-12"):
            raise ValueError
        if result < minimum or (positive and result == 0):
            raise ValueError
        return result
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError(f"{name}: enter a finite {'positive' if positive else 'non-negative'} number.") from None


def int_field(data, name, default, low, high):
    value = str(data.get(name, default))
    try:
        result = int(value)
    except ValueError:
        raise ValueError(f"{name}: enter a whole number.") from None
    if not low <= result <= high:
        raise ValueError(f"{name}: must be between {low} and {high}.")
    return result


def date_boundary(value, *, end=False):
    if not value:
        return None
    try:
        parsed = datetime.strptime(str(value), "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        raise ValueError("Dates must use YYYY-MM-DD (UTC).") from None
    return parsed + timedelta(days=1) if end else parsed


def validate_config(data):
    strategy = str(data.get("strategy", ""))
    if strategy not in SCALPER_STRATEGY_REGISTRY:
        raise ValueError("Select a supported candle strategy.")
    timeframe = str(data.get("timeframe", ""))
    if timeframe not in TIMEFRAMES:
        raise ValueError("Select a supported timeframe.")
    config = {
        "strategy": strategy,
        "timeframe": timeframe,
        "quantity": decimal_field(data, "quantity", positive=True),
        "contract_size": decimal_field(data, "contract_size", positive=True),
        "point_size": decimal_field(data, "point_size", positive=True),
        "initial_balance": decimal_field(data, "initial_balance", "10000", positive=True),
        "spread_points": decimal_field(data, "spread_points", "0"),
        "slippage_points": decimal_field(data, "slippage_points", "0"),
        "commission_per_lot": decimal_field(data, "commission_per_lot", "0"),
        "min_score": decimal_field(data, "min_score", "0"),
        "warmup": int_field(data, "warmup", 100, 30, 1000),
        "csv_utc_offset_minutes": int_field(data, "csv_utc_offset_minutes", 0, -840, 840),
        "same_bar_policy": str(data.get("same_bar_policy", "stop_first")),
        "start_date": str(data.get("start_date", "") or ""),
        "end_date": str(data.get("end_date", "") or ""),
        "currency": str(data.get("currency", "USD")).strip().upper(),
    }
    if config["same_bar_policy"] not in {"stop_first", "target_first"}:
        raise ValueError("Select stop_first or target_first for ambiguous candles.")
    if not config["currency"].isascii() or not config["currency"].isalpha() or len(config["currency"]) != 3:
        raise ValueError("Currency must be a three-letter symbol profit currency, e.g. USD.")
    start, end = date_boundary(config["start_date"]), date_boundary(config["end_date"], end=True)
    if start and end and start >= end:
        raise ValueError("End date must be on or after start date.")
    config["strategy_config"] = json_safe(asdict(SCALPER_STRATEGY_REGISTRY[strategy].config_factory()))
    config["model_version"] = 1
    return config


def parse_csv(text, config, *, now=None):
    if not isinstance(text, str) or not text.strip():
        raise ValueError("Choose a CSV file containing historical bid candles.")
    if len(text.encode("utf-8")) > MAX_CSV_BYTES:
        raise ValueError(f"CSV exceeds the {MAX_CSV_BYTES // 1_000_000} MB limit. Select a smaller date range.")
    # Also tolerate a BOM-less UTF-16 file decoded by an older client as UTF-8;
    # its ASCII content arrives with a NUL between every character.
    source = text.replace("\x00", "").lstrip("\ufeff\r\n ")

    # Excel can open an MT5 tab export as one cell per row and, when saved,
    # surround that entire cell with quotes. Recover the still-present tabs;
    # never try to infer separators if Excel has actually deleted them.
    source_lines = source.splitlines()
    if source_lines and source_lines[0].startswith('"') and source_lines[0].endswith('"'):
        unwrapped_header = source_lines[0][1:-1].replace('""', '"')
        if any(separator in unwrapped_header for separator in ("\t", ";", "|")):
            source_lines = [
                line[1:-1].replace('""', '"')
                if line.startswith('"') and line.endswith('"')
                else line
                for line in source_lines
            ]
            source = "\n".join(source_lines)
    first_line = source.splitlines()[0]

    def normalize(key):
        if not isinstance(key, str):
            return ""
        value = key.replace("\x00", "").strip().strip("<>").strip().lower()
        value = re.sub(r"[\s-]+", "_", value)
        aliases = {
            "datetime": "time", "date_time": "time", "timestamp": "time",
            "tickvolume": "tick_volume", "tick_vol": "tick_volume",
            "tickvol": "tick_volume",
        }
        return aliases.get(value, value)

    required = {"time", "open", "high", "low", "close"}
    candidates = []
    for candidate in ("\t", ",", ";", "|"):
        parsed_header = next(csv.reader([first_line], delimiter=candidate))
        normalized = [normalize(key) for key in parsed_header]
        candidates.append((len(required.intersection(normalized)), len(normalized), candidate))
    _score, _columns, delimiter = max(candidates)
    reader = csv.DictReader(io.StringIO(source), delimiter=delimiter)
    if not reader.fieldnames:
        raise ValueError("CSV header is missing.")
    fields = [normalize(key) for key in reader.fieldnames]
    if len(fields) != len(set(fields)):
        raise ValueError("CSV has duplicate column names.")
    if not required.issubset(fields):
        compact_header = re.sub(r"\s+", "", first_line.upper())
        if len(fields) == 1 and all(f"<{name}>" in compact_header for name in ("TIME", "OPEN", "HIGH", "LOW", "CLOSE")):
            raise ValueError(
                "The MT5 headers are present, but every field was collapsed into one column and no usable delimiter remains. "
                "Export Bars from MT5 again and upload the original file without opening and resaving it in Excel."
            )
        visible = ", ".join(field or "(blank)" for field in fields[:12])
        raise ValueError(
            "CSV requires time,open,high,low,close and optional tick_volume. "
            f"Detected columns: {visible}. Export MT5 Bars, not Ticks."
        )
    now = now or datetime.now(timezone.utc)
    step = timedelta(minutes=TIMEFRAMES[config["timeframe"]])
    csv_zone = timezone(timedelta(minutes=config["csv_utc_offset_minutes"]))
    bars, gaps, missing_volume = [], 0, 0
    for line, original in enumerate(reader, start=2):
        if len(bars) >= MAX_BARS:
            raise ValueError(f"CSV exceeds the {MAX_BARS:,} candle limit.")
        if None in original or any(value is None for value in original.values()):
            raise ValueError(f"CSV row {line}: column count does not match the header.")
        row = {normalize(key): value.strip() for key, value in original.items()}
        stamp = f"{row['date']} {row['time']}" if row.get("date") else row["time"]
        try:
            at = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        except ValueError:
            try:
                at = datetime.strptime(stamp, "%Y.%m.%d %H:%M:%S")
            except ValueError:
                raise ValueError(f"CSV row {line}: invalid timestamp.") from None
        if at.tzinfo is None:
            at = at.replace(tzinfo=csv_zone)
        at = at.astimezone(timezone.utc)
        if at + step > now:
            raise ValueError(f"CSV row {line}: candle is in the future or has not completed.")
        values = {key: decimal_field(row, key, positive=True) for key in ("open", "high", "low", "close")}
        if not values["low"] <= min(values["open"], values["close"]) <= max(values["open"], values["close"]) <= values["high"]:
            raise ValueError(f"CSV row {line}: inconsistent OHLC prices.")
        volume = row.get("tick_volume", row.get("tickvol", ""))
        if not volume:
            missing_volume += 1
            volume = "0"
        values["tick_volume"] = int_field({"tick_volume": volume}, "tick_volume", 0, 0, 10**12)
        if bars:
            elapsed = at - bars[-1]["time"]
            if elapsed <= timedelta(0):
                raise ValueError(f"CSV row {line}: timestamps must be unique and increasing.")
            if elapsed % step:
                raise ValueError(f"CSV row {line}: interval does not match {config['timeframe']}.")
            if elapsed > step:
                gaps += 1
        bars.append({"time": at, **values})
    if len(bars) < config["warmup"] + 2:
        raise ValueError(f"Provide at least {config['warmup'] + 2} candles, including warmup.")
    if missing_volume and config["strategy"] in {"momentum_ignition", "breakout_retest"}:
        raise ValueError("This strategy needs tick_volume on every candle. Export volume with the CSV.")
    start, end = date_boundary(config["start_date"]), date_boundary(config["end_date"], end=True)
    indexes = [i for i, bar in enumerate(bars) if (not start or bar["time"] >= start) and (not end or bar["time"] < end)]
    if not indexes or indexes[-1] < max(indexes[0], config["warmup"]):
        raise ValueError("Selected dates contain no tradable candles after warmup.")
    return bars, {
        "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "bars": len(bars), "first_at": bars[0]["time"], "last_at": bars[-1]["time"],
        "gap_count": gaps, "missing_volume_bars": missing_volume,
        "first_index": max(indexes[0], config["warmup"]), "last_index": indexes[-1],
    }


def run_simulation(bars, config, dataset, symbol, *, runner=None):
    """Return immutable JSON results; no account, signal, order or broker writes."""
    entry = SCALPER_STRATEGY_REGISTRY[config["strategy"]]
    cfg = entry.config_factory()
    runner = runner or (lambda window: entry.runner(symbol, window, cfg) if entry.requires_symbol else entry.runner(window, cfg))
    spread = config["spread_points"] * config["point_size"]
    slippage = config["slippage_points"] * config["point_size"]
    units = config["quantity"] * config["contract_size"]
    commission = config["commission_per_lot"] * config["quantity"]
    balance = config["initial_balance"]
    peak = balance
    max_dd, max_dd_pct = ZERO, ZERO
    first, last = dataset["first_index"], dataset["last_index"]
    trades, equity, reasons = [], [], Counter()
    position = None
    evaluated, opens = 0, 0
    equity_sample_every = max(1, ((last - first + 1) + MAX_EQUITY_POINTS - 1) // MAX_EQUITY_POINTS)

    def close_position(bar, exit_price, reason):
        nonlocal balance, position
        direction = position["direction"]
        sign = Decimal("1") if direction == "buy" else Decimal("-1")
        fill = exit_price - sign * slippage
        pnl = (fill - position["entry_price"]) * sign * units - commission
        costs = spread * units + 2 * slippage * units + commission
        balance += pnl
        trades.append({
            **position, "exit_time": bar["time"], "exit_price": fill,
            "reason": reason, "pnl": pnl, "gross_pnl": pnl + costs,
            "spread_cost": spread * units, "slippage_cost": 2 * slippage * units,
            "commission": commission, "balance": balance,
        })
        position = None

    for i in range(first, last + 1):
        bar = bars[i]
        # A signal only sees the preceding, fully completed history window.
        if position is None and balance > 0:
            window = bars[max(0, i - config["warmup"]):i]
            decision = runner(window)
            evaluated += 1
            if decision.action != "open" or decision.direction not in {"buy", "sell"}:
                reasons[decision.reason or "no_signal"] += 1
            elif Decimal(str(decision.score or 0)) < config["min_score"]:
                reasons["below_min_score"] += 1
            else:
                opens += 1
                sign = Decimal("1") if decision.direction == "buy" else Decimal("-1")
                fill = bar["open"] + (spread if decision.direction == "buy" else ZERO) + sign * slippage
                sl, tp = decision.sl, decision.tp
                if sl is None or tp is None or not (sl.is_finite() and tp.is_finite()) or not (sign * (fill - sl) > 0 and sign * (tp - fill) > 0):
                    reasons["invalid_stops_at_next_open"] += 1
                else:
                    position = {
                        "direction": decision.direction, "signal_time": window[-1]["time"],
                        "entry_time": bar["time"], "entry_price": fill, "sl": sl, "tp": tp,
                        "quantity": config["quantity"], "score": decision.score,
                        "strategy": config["strategy"], "entry_reason": decision.reason,
                    }
        if position:
            buy = position["direction"] == "buy"
            offset = ZERO if buy else spread
            open_price, high, low = (bar[key] + offset for key in ("open", "high", "low"))
            sl, tp = position["sl"], position["tp"]
            stop_gap = open_price <= sl if buy else open_price >= sl
            target_gap = open_price >= tp if buy else open_price <= tp
            stop_hit = low <= sl if buy else high >= sl
            target_hit = high >= tp if buy else low <= tp
            if stop_gap:
                close_position(bar, open_price, "stop_gap")
            elif target_gap:
                close_position(bar, tp, "take_profit")
            elif stop_hit and (not target_hit or config["same_bar_policy"] == "stop_first"):
                close_position(bar, sl, "stop_loss")
            elif target_hit:
                close_position(bar, tp, "take_profit")
        if i == last and position:
            close_position(bar, bar["close"] + (spread if position["direction"] == "sell" else ZERO), "end_of_data")
        mark = balance
        if position:
            sign = Decimal("1") if position["direction"] == "buy" else Decimal("-1")
            exit_quote = bar["close"] + (spread if sign < 0 else ZERO) - sign * slippage
            mark += (exit_quote - position["entry_price"]) * sign * units - commission
        peak = max(peak, mark)
        dd = peak - mark
        dd_pct = dd / peak * 100 if peak > 0 else ZERO
        max_dd, max_dd_pct = max(max_dd, dd), max(max_dd_pct, dd_pct)
        if (i - first) % equity_sample_every == 0 or i == last:
            equity.append({"time": bar["time"], "balance": balance, "equity": mark, "drawdown_pct": dd_pct})

    wins = [t["pnl"] for t in trades if t["pnl"] > 0]
    losses = [t["pnl"] for t in trades if t["pnl"] < 0]
    total = len(trades)
    net = balance - config["initial_balance"]
    gross_profit, gross_loss = sum(wins, ZERO), -sum(losses, ZERO)
    summary = {
        "trades": total, "wins": len(wins), "losses": len(losses), "breakeven": total - len(wins) - len(losses),
        "win_rate_pct": Decimal(len(wins)) / total * 100 if total else ZERO,
        "net_pnl": net, "return_pct": net / config["initial_balance"] * 100,
        "initial_balance": config["initial_balance"], "ending_balance": balance,
        "gross_profit": gross_profit, "gross_loss": gross_loss,
        "profit_factor": gross_profit / gross_loss if gross_loss else None,
        "max_drawdown": max_dd, "max_drawdown_pct": max_dd_pct,
        "avg_trade": net / total if total else ZERO,
        "best_trade": max((t["pnl"] for t in trades), default=ZERO),
        "worst_trade": min((t["pnl"] for t in trades), default=ZERO),
        "spread_cost": sum((t["spread_cost"] for t in trades), ZERO),
        "slippage_cost": sum((t["slippage_cost"] for t in trades), ZERO),
        "commission": sum((t["commission"] for t in trades), ZERO),
        "evaluated_bars": evaluated, "open_signals": opens,
        "first_at": bars[first]["time"], "last_at": bars[last]["time"],
    }
    return json_safe({
        "summary": summary, "trades": trades, "equity": equity,
        "skip_reasons": [{"reason": k, "count": v} for k, v in reasons.most_common()],
        "assumptions": [
            "Bid OHLC candles; fixed spread and adverse slippage on entry and exit.",
            "Signals use completed candles; entries fill at the next available open.",
            "One fixed-size position; strategy SL/TP; round-trip commission per lot; no swap, margin liquidation or currency conversion.",
            "Drawdown uses candle-close liquidation equity, not intrabar tick equity.",
            f"The displayed equity curve is sampled to at most {MAX_EQUITY_POINTS:,} candle-close points; summary drawdown still evaluates every candle.",
            "Standalone strategy replay: live HTF gate, news, portfolio risk, automatic selection, trailing and partial exits are not simulated.",
            "Timestamp offsets are converted to UTC. Gaps are preserved; no candles are invented.",
        ],
    })
