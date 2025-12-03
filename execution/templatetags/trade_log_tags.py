

from decimal import Decimal

from django import template
from django.db.models import Count, Sum, Q
from django.db.models.functions import Coalesce

from execution.models import TradeLog

register = template.Library()


@register.simple_tag
def get_trade_logs_count():
    """Optimized count query for trade logs"""
    return TradeLog.objects.count()


@register.simple_tag
def get_recent_trade_logs(limit=50):
    """Optimized query with prefetching for recent trade logs"""
    return (
        TradeLog.objects.select_related(
            "order", "bot", "broker_account"
        )
        .only(
            "created_at",
            "symbol",
            "side",
            "qty",
            "price",
            "pnl",
            "status",
            "order__client_order_id",
            "order__id",
            "bot__name",
            "broker_account__name",
            
        )
        .order_by("-created_at")[:limit]
    )



@register.simple_tag
def get_trade_logs_metrics():
    """Get comprehensive metrics for trade logs"""
    metrics = TradeLog.objects.aggregate(
        total_count=Count("id"),
        win_count=Count("id", filter=Q(status="win")),
        loss_count=Count("id", filter=Q(status="loss")),
        breakeven_count=Count("id", filter=Q(status="breakeven")),
        error_count=Count("id", filter=Q(status="error")),
        total_pnl=Coalesce(Sum("pnl"), Decimal("0")),
    )

    # Normalize None -> 0
    win = metrics.get("win_count") or 0
    raw_loss = metrics.get("loss_count") or 0
    breakeven = metrics.get("breakeven_count") or 0
    errors = metrics.get("error_count") or 0

    # Treat error outcomes as losses for high-level stats so
    # the win-rate isn't misleadingly based on 0 trades.
    loss = raw_loss + errors
    metrics["loss_count"] = loss

    total_trades = win + loss + breakeven
    if total_trades > 0:
        metrics["win_rate"] = (win / total_trades) * 100
    else:
        metrics["win_rate"] = 0

    return metrics


@register.filter
def format_currency(value):
    """Format currency values"""
    if value is None:
        return "0.00"
    try:
        return f"{float(value):,.2f}"
    except (ValueError, TypeError):
        return "0.00"


@register.filter
def format_percentage(value):
    """Format percentage values"""
    if value is None:
        return "0%"
    try:
        return f"{float(value):.1f}%"
    except (ValueError, TypeError):
        return "0%"
