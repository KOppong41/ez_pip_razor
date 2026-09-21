"""Account-scoped closed outcomes, entry attribution and performance epochs."""
from collections import defaultdict
from datetime import datetime, time, timedelta, timezone as dt_timezone
from decimal import Decimal
import re

from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime

from bots.models import Asset, Bot
from execution.models import BrokerPosition, Execution, Order, PerformanceBaseline, TradeLog
from execution.utils.symbols import canonical_symbol

ZERO = Decimal("0")
SCOPE_KEYS = ("market", "bot_id", "symbol", "strategy", "preset_version", "config_fingerprint", "build_sha")
IDENTITY_KEYS = ("config_fingerprint", "build_sha", "build_status", "execution_timeframe", "recommendation_state")
GOLD_STRATEGIES = ("trend_pullback", "breakout_retest", "momentum_ignition", "price_action_pinbar", "doji_breakout")


def timestamp(value, *, end_date=False):
    """Dates are UTC; timestamps require an explicit timezone."""
    if not value:
        return None
    value = str(value)
    try:
        if len(value) == 10:
            day = parse_date(value)
            if day is not None:
                return datetime.combine(day, time(), dt_timezone.utc) + (timedelta(days=1) if end_date else timedelta())
        result = parse_datetime(value)
        if result is not None and timezone.is_aware(result):
            return result.astimezone(dt_timezone.utc)
    except (ValueError, OverflowError):
        pass
    raise ValueError("Use YYYY-MM-DD or an ISO timestamp with timezone.")


def validate_scope(values, account):
    scope = {key: str(values[key]).strip() for key in SCOPE_KEYS if values.get(key) not in (None, "", "all")}
    if scope.get("market", "all") not in {"all", "gold", "btc", "eth", "forex"}:
        raise ValueError("Unknown market filter.")
    if "bot_id" in scope:
        try:
            bot_id = int(scope["bot_id"])
        except (ValueError, TypeError):
            raise ValueError("Invalid bot filter.") from None
        if not Bot.objects.filter(pk=bot_id, broker_account=account).exists():
            raise ValueError("Bot not found in this account.")
        scope["bot_id"] = str(bot_id)
    if "preset_version" in scope and scope["preset_version"] != "unknown":
        try:
            version = int(scope["preset_version"])
        except ValueError:
            raise ValueError("Invalid preset version.") from None
        if version < 1:
            raise ValueError("Invalid preset version.")
        scope["preset_version"] = str(version)
    if "symbol" in scope:
        scope["symbol"] = canonical_symbol(scope["symbol"])
    for key, pattern in (("config_fingerprint", r"[0-9a-f]{64}"), ("build_sha", r"[0-9a-f]{40}|[0-9a-f]{64}")):
        if key in scope and scope[key] != "unknown" and not re.fullmatch(pattern, scope[key]):
            raise ValueError(f"Invalid {key} filter.")
    if any(len(value) > 128 for value in scope.values()):
        raise ValueError("Filter value is too long.")
    return scope


def baseline_dict(baseline):
    return {"id": baseline.pk, "name": baseline.name, "started_at": baseline.started_at,
            "filters": baseline.filters, "created_at": baseline.created_at}


def closed_outcomes(account):
    # Each live TradeLog is an accumulated exit-order result. Ignore provisional
    # entry logs and use the latest result if a historical repair duplicated one.
    logs = list(TradeLog.objects.filter(broker_account=account, order__broker_account=account,
                order__intent="exit", pnl__isnull=False, closed_at__isnull=False)
                .select_related("order", "bot").order_by("created_at", "id"))
    latest = {log.order_id: log for log in logs}
    missing_results = {
        (ticket, canonical_symbol(symbol))
        for order_id, ticket, symbol in Order.objects.filter(
            broker_account=account, intent="exit", status__in=["filled", "part_filled"],
            broker_position_ticket__isnull=False,
        ).values_list("id", "broker_position_ticket", "symbol")
        if order_id not in latest
    }
    entries = {order.pk: order for order in Order.objects.filter(broker_account=account, intent="entry")
               .select_related("decision__signal", "bot").order_by("created_at", "id")}
    by_ticket, opened = {}, {}
    entry_ids = defaultdict(set)
    for fill in (Execution.objects.filter(order__broker_account=account, order__intent="entry")
                 .order_by("exec_time", "id")):
        if fill.broker_position_ticket and fill.order_id in entries:
            key = (fill.broker_position_ticket, canonical_symbol(entries[fill.order_id].symbol))
            by_ticket.setdefault(key, entries[fill.order_id])
            opened.setdefault(key, fill.exec_time)
            entry_ids[key].add(fill.order_id)
    for order in entries.values():
        if order.broker_position_ticket:
            key = (order.broker_position_ticket, canonical_symbol(order.symbol))
            by_ticket.setdefault(key, order)
            entry_ids[key].add(order.pk)
    positions = {(pos.broker_position_ticket, canonical_symbol(pos.symbol)): pos for pos in
                 BrokerPosition.objects.filter(broker_account=account).select_related("bot")}
    categories = {canonical_symbol(asset.symbol): asset.category for asset in Asset.objects.all()}
    groups = defaultdict(list)
    omitted_open = 0
    for log in latest.values():
        ticket = log.order.broker_position_ticket or log.broker_ticket
        key = (ticket, canonical_symbol(log.symbol))
        position = positions.get(key)
        if position and position.status != "closed":
            omitted_open += 1
            continue
        groups[(ticket or f"order:{log.order_id}", key[1])].append(log)
    rows = []
    for (ticket, symbol), parts in groups.items():
        parts.sort(key=lambda log: (log.closed_at, log.id))
        last = parts[-1]
        key = (ticket, symbol)
        if key in missing_results:
            continue
        position = positions.get(key)
        entry = entries.get(position.originating_order_id) if position else None
        entry = entry or by_ticket.get(key)
        mixed = len(entry_ids[key]) > 1
        snapshot = (entry.performance_context or {}) if entry else {}
        decision = entry.decision if entry and entry.decision_id else None
        payload = (decision.signal.payload or {}) if decision and decision.signal_id else {}
        strategy = snapshot.get("strategy") or payload.get("strategy") or (position.strategy_name if position else "") or "unknown"
        bot = entry.bot if entry else position.bot if position else None
        pnl = sum((part.pnl for part in parts), ZERO)
        entry_time = (position.opened_at if position else None) or opened.get(key)
        rows.append({
            "id": last.id, "created_at": last.created_at, "closed_at": last.closed_at,
            "opened_at": entry_time, "symbol": symbol, "category": categories.get(symbol, "unknown"),
            "side": entry.side if entry else position.side if position else ("sell" if last.side == "buy" else "buy"),
            "qty": sum((part.qty for part in parts), ZERO), "price": last.price, "exit_price": last.exit_price,
            "pnl": pnl, "status": "win" if pnl > 0 else "loss" if pnl < 0 else "breakeven",
            "broker_ticket": ticket if isinstance(ticket, int) else None,
            "bot_id": bot.pk if bot and not mixed else None, "bot_name": "Mixed entries" if mixed else bot.name if bot else "Unknown",
            "strategy": "mixed_entries" if mixed else str(strategy), "preset_version": None if mixed else snapshot.get("preset_version"),
            "attribution_source": "mixed_entries" if mixed else snapshot.get("source", "historical_entry" if entry else "unknown"),
            "is_opposite_scalp": not mixed and bool(snapshot.get("is_opposite_scalp", (decision.params or {}).get("is_opposite_scalp") if decision else False)),
            "exit_records": len(parts), "completion_verified": position is not None,
            **{key: None if mixed else snapshot.get(key) for key in IDENTITY_KEYS},
        })
    return rows, {"open_position_exit_records_omitted": omitted_open,
                  "positions_with_missing_exit_results": len(missing_results),
                  "duplicate_order_records_omitted": len(logs) - len(latest)}


def summary(rows):
    wins = sum(row["pnl"] > 0 for row in rows)
    losses = sum(row["pnl"] < 0 for row in rows)
    profit = sum((row["pnl"] for row in rows if row["pnl"] > 0), ZERO)
    loss = sum((row["pnl"] for row in rows if row["pnl"] < 0), ZERO)
    return {"total_trades": len(rows), "wins": wins, "losses": losses,
            "breakeven": len(rows) - wins - losses,
            "win_rate": Decimal(wins * 100) / len(rows) if rows else None,
            "gross_profit": profit, "gross_loss": loss, "net_profit": profit + loss,
            "profit_factor": profit / abs(loss) if loss else None,
            "expectancy": (profit + loss) / len(rows) if rows else None}


def matches(row, scope):
    market = scope.get("market", "all")
    market_symbols = {"gold": "XAUUSD", "btc": "BTCUSD", "eth": "ETHUSD"}
    if market in market_symbols and row["symbol"] != market_symbols[market]:
        return False
    if market == "forex" and row["category"] != "forex":
        return False
    for key in SCOPE_KEYS[1:]:
        if key in scope and str(row.get(key) if row.get(key) is not None else "unknown") != scope[key]:
            return False
    return True


def history_report(account, user, params):
    scope = validate_scope(params, account)
    start, end = timestamp(params.get("from")), timestamp(params.get("to"), end_date=True)
    if start and end and start >= end:
        raise ValueError("From date must precede the end of the selected range.")
    try:
        page = int(params.get("page", 1))
        page_size = int(params.get("page_size", 100))
    except (ValueError, TypeError):
        raise ValueError("Invalid pagination.") from None
    if page < 1 or not 1 <= page_size <= 200:
        raise ValueError("Page must be positive; page_size must be 1–200.")
    baselines = PerformanceBaseline.objects.filter(owner=user, broker_account=account)
    baseline = None
    if params.get("baseline_id"):
        try:
            baseline = baselines.get(pk=int(params["baseline_id"]))
        except (ValueError, TypeError, PerformanceBaseline.DoesNotExist):
            raise ValueError("Baseline not found in this account.") from None
    rows, quality = closed_outcomes(account)
    current_versions = {str(value) for value in Bot.objects.filter(
        broker_account=account, asset_preset_version_applied__isnull=False,
    ).values_list("asset_preset_version_applied", flat=True)}
    options = {
        "bots": list(Bot.objects.filter(broker_account=account).order_by("name", "id").values("id", "name")),
        "symbols": sorted({row["symbol"] for row in rows} | {canonical_symbol(symbol) for symbol in
                           Bot.objects.filter(broker_account=account, asset__isnull=False).values_list("asset__symbol", flat=True)}),
        "strategies": sorted({row["strategy"] for row in rows} | set(GOLD_STRATEGIES)),
        "preset_versions": sorted(current_versions | {"unknown"} | {
            str(row["preset_version"]) if row["preset_version"] is not None else "unknown" for row in rows}),
    }
    for key in ("config_fingerprint", "build_sha"):
        options[key] = sorted({"unknown"} | {row[key] for row in rows if row[key]})
    # A selected bot can define a baseline before its first closed outcome.
    # This is a preview only: historical rows always use their entry snapshot.
    current_identity = None
    selected_bot_id = scope.get("bot_id") or (baseline.filters.get("bot_id") if baseline else None)
    if selected_bot_id:
        from execution.services.performance_identity import performance_identity
        from execution.services.scalper_config import resolve_scalper_execution_timeframe
        bot = Bot.objects.filter(pk=selected_bot_id, broker_account=account).select_related("asset").first()
        if bot and bot.asset:
            try:
                frame = (resolve_scalper_execution_timeframe(bot, bot.asset.symbol)
                         if bot.engine_mode == "scalper" else bot.default_timeframe)
            except (ArithmeticError, AttributeError, LookupError, TypeError, ValueError):
                frame = None
            if frame:
                current_identity = performance_identity(bot, bot.asset.symbol, frame)
                current_identity.pop("config_snapshot", None)
                current_identity["symbol"] = canonical_symbol(bot.asset.symbol)
                for key in ("config_fingerprint", "build_sha"):
                    if current_identity.get(key):
                        options[key] = sorted(set(options[key]) | {current_identity[key]})
    selected = [row for row in rows if matches(row, scope) and (not start or row["closed_at"] >= start)
                and (not end or row["closed_at"] < end)
                and (not baseline or (matches(row, baseline.filters) and row["opened_at"] is not None
                                      and row["opened_at"] >= baseline.started_at and row["closed_at"] >= baseline.started_at))]
    selected.sort(key=lambda row: (row["closed_at"], row["id"]), reverse=True)
    grouped = defaultdict(list)
    for row in selected:
        grouped[(row["symbol"], row["strategy"])].append(row)
    if scope.get("market") == "gold" or scope.get("symbol") == "XAUUSD" or (baseline and baseline.filters.get("market") == "gold"):
        for strategy in GOLD_STRATEGIES:
            if not scope.get("strategy") or scope["strategy"] == strategy:
                grouped.setdefault(("XAUUSD", strategy), [])
    quality.update({"unknown_preset_trades": sum(row["preset_version"] is None for row in selected),
                    "unknown_configuration_trades": sum(row["config_fingerprint"] is None for row in selected),
                    "unknown_build_trades": sum(row["build_sha"] is None for row in selected),
                    "unverified_completion_trades": sum(not row["completion_verified"] for row in selected)})
    return {"summary": summary(selected), "trades": selected[(page - 1) * page_size:page * page_size],
            "page": page, "page_size": page_size, "total_pages": max(1, (len(selected) + page_size - 1) // page_size),
            "strategy_breakdown": [{"symbol": symbol, "strategy": strategy, **summary(items)}
                                   for (symbol, strategy), items in sorted(grouped.items())],
            "opposite_scalp": summary([row for row in selected if row["is_opposite_scalp"]]),
            "baseline": baseline_dict(baseline) if baseline else None,
            "baselines": [baseline_dict(item) for item in baselines], "filters": scope, "options": options,
            "current_identity": current_identity,
            "data_quality": quality,
            "basis": "Recorded realized P/L, grouped by position ticket where available. Known open positions and positions with missing recorded exit results are excluded; legacy outcomes without position records have unverified completion. Positions with multiple entries have mixed attribution. Dates filter closes in UTC; baselines require a recorded entry at or after the start. Presets, configuration and build revisions come from entry snapshots; missing historical values remain unknown. Dirty or unavailable builds have no verified revision. Costs follow recorded realized P/L; missing charges are not estimated."}
