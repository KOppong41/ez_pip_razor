from decimal import Decimal

from django import template
from django.db.models import Count, Sum
from django.utils import timezone

from bots.models import Asset, Bot
from brokers.models import Broker, BrokerAccount
from execution.models import ExecutionSetting, Position, TradeLog

register = template.Library()


@register.simple_tag
def get_dashboard_metrics():
    total_profit = TradeLog.objects.aggregate(total=Sum("pnl"))["total"] or Decimal("0")
    wins = TradeLog.objects.filter(status="win").count()
    losses = TradeLog.objects.filter(status="loss").count()
    decisions_today = TradeLog.objects.filter(created_at__date=timezone.localdate()).count()
    win_loss_total = wins + losses
    success_rate = Decimal(wins / win_loss_total * 100) if win_loss_total else Decimal("0")
    active_bots_qs = Bot.objects.filter(status="active").select_related("asset", "broker_account")
    active_bots = active_bots_qs.count()
    open_positions = Position.objects.filter(status="open").count()
    active_assets = Asset.objects.filter(is_active=True).count()
    total_assets = Asset.objects.count()
    featured_assets = Asset.objects.values_list("symbol", flat=True).order_by("symbol")[:3]
    asset_preview = ", ".join(featured_assets) if featured_assets else "No assets configured"
    broker_accounts = BrokerAccount.objects.filter(is_active=True).count()
    execution_settings = ExecutionSetting.objects.count()

    return {
        "total_profit": total_profit,
        "success_rate": success_rate,
        "wins": wins,
        "losses": losses,
        "decisions_today": decisions_today,
        "active_bots": active_bots,
        "open_positions": open_positions,
        "active_assets": active_assets,
        "total_assets": total_assets,
        "asset_preview": asset_preview,
        "broker_accounts": broker_accounts,
        "execution_settings": execution_settings,
        "broker_types": Broker.objects.count(),
    }


@register.simple_tag
def get_active_bots(limit: int = 8):
    """
    Return a small list of active bots for dashboard cards.
    """
    qs = Bot.objects.filter(status="active").select_related("asset", "broker_account").order_by("name")
    return list(qs[: limit])
