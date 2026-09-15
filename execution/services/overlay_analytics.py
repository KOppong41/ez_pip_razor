"""Realized attribution; removing overlay P/L is not a causal backtest."""
from collections import defaultdict
from decimal import Decimal

ZERO = Decimal("0")


def realized_drawdown(rows):
    total = peak = drawdown = ZERO
    for row in rows:
        total += Decimal(str(row["pnl"]))
        peak = max(peak, total)
        drawdown = max(drawdown, peak - total)
    return drawdown


def overlay_report(rows):
    rows = sorted(rows, key=lambda row: row["exit_time"])
    overlays = [row for row in rows if row.get("is_opposite_scalp")]
    primary = [row for row in rows if not row.get("is_opposite_scalp")]
    pnls = defaultdict(lambda: ZERO)
    for row in overlays:
        pnls[row["position_id"]] += Decimal(str(row["pnl"]))
    wins = [pnl for pnl in pnls.values() if pnl > 0]
    losses = [pnl for pnl in pnls.values() if pnl < 0]
    gross_loss = -sum(losses, ZERO)
    net = sum(pnls.values(), ZERO)
    return {
        "trades": len(pnls), "wins": len(wins), "losses": len(losses),
        "win_rate_pct": Decimal(len(wins) * 100) / len(pnls) if pnls else ZERO,
        "profit_factor": sum(wins, ZERO) / gross_loss if gross_loss else None,
        "expectancy": net / len(pnls) if pnls else ZERO, "net_pnl": net,
        "costs": sum((Decimal(str(row.get("costs", 0))) for row in overlays), ZERO),
        "realized_drawdown": realized_drawdown(overlays),
        "account_realized_drawdown": realized_drawdown(rows),
        "realized_drawdown_without_overlay_pnl": realized_drawdown(primary),
        "realized_drawdown_delta": realized_drawdown(rows) - realized_drawdown(primary),
        "drawdown_basis": "Realized cash flow only; excludes floating P/L. Removing overlay P/L is attribution, not proof of strategy alpha.",
    }


def account_overlay_report(account):
    from execution.models import BrokerPosition, Execution
    from execution.services.opposite_scalp import is_overlay
    positions = {position.broker_position_ticket: position for position in
        BrokerPosition.objects.filter(broker_account=account, status="closed", ownership="ez_trade")
        .select_related("originating_order__decision")}
    fills_by_ticket = defaultdict(list)
    for fill in Execution.objects.filter(order__broker_account=account,
        broker_position_ticket__in=positions).select_related("order").order_by("exec_time", "id"):
        fills_by_ticket[fill.broker_position_ticket].append(fill)
    rows, missing = [], 0
    for ticket, position in positions.items():
        fills = fills_by_ticket[ticket]
        exits = [fill for fill in fills if fill.order.intent == "exit"]
        if not exits or any(fill.profit is None for fill in exits):
            missing += 1
            continue
        for fill in fills:
            costs = fill.fee - fill.commission - fill.swap
            rows.append({"position_id": position.pk, "is_opposite_scalp": is_overlay(position),
                         "exit_time": fill.exec_time, "pnl": (fill.profit or ZERO) - costs, "costs": costs})
    report = overlay_report(rows)
    report["positions_missing_realized_fills"] = missing
    report["cost_basis"] = f"Recorded commission, fees and swap on fully closed positions. {missing} positions omitted because realized exit fills are unavailable. Spread/slippage are embedded in broker P/L and not separately estimated."
    return report
