

from decimal import Decimal

from django import template
from django.db.models import Count, Sum, Q
from django.db.models.functions import Coalesce

from execution.models import TradeLog

register = template.Library()


@register.simple_tag
def get_trade_logs_count():
    """Count only completed trades."""
    return TradeLog.objects.filter(status__in=["filled", "win", "loss", "breakeven"]).count()


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
        .filter(status__in=["filled", "win", "loss", "breakeven"])
        .order_by("-created_at")[:limit]
    )



@register.simple_tag
def get_trade_logs_metrics():
    """Get comprehensive metrics for trade logs"""
    queryset = TradeLog.objects.filter(status__in=["filled", "win", "loss", "breakeven"])
    metrics = queryset.aggregate(
        total_count=Count("id"),
        win_count=Count("id", filter=Q(status="win")),
        loss_count=Count("id", filter=Q(status="loss")),
        breakeven_count=Count("id", filter=Q(status="breakeven")),
        total_pnl=Coalesce(Sum("pnl"), Decimal("0")),
    )

    # Normalize None -> 0
    win = metrics.get("win_count") or 0
    raw_loss = metrics.get("loss_count") or 0
    breakeven = metrics.get("breakeven_count") or 0
    loss = raw_loss
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
