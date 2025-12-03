from django import template
from django.apps import apps

register = template.Library()


@register.simple_tag
def metric(model_path: str):
    """Return count for the given model path 'app_label.ModelName'."""
    try:
        app_label, model_name = model_path.split(".", 1)
        model = apps.get_model(app_label, model_name)
        return model.objects.count()
    except Exception:
        return "-"


@register.simple_tag
def recent_signals(limit: int = 10):
    """Fetch the most recent signals for display on custom admin pages."""
    try:
        Signal = apps.get_model("execution", "Signal")
        return Signal.objects.select_related("bot").order_by("-received_at")[:limit]
    except Exception:
        return []


@register.simple_tag
def recent_bots(limit: int = 20):
    """Fetch the most recent bots for custom admin pages."""
    try:
        Bot = apps.get_model("bots", "Bot")
        return Bot.objects.select_related("asset", "broker_account", "owner").order_by("-created_at")[:limit]
    except Exception:
        return []


@register.simple_tag
def recent_broker_accounts(limit: int = 20):
    """Fetch recent broker accounts for custom admin pages."""
    try:
        BrokerAccount = apps.get_model("brokers", "BrokerAccount")
        return BrokerAccount.objects.select_related("owner").order_by("-created_at")[:limit]
    except Exception:
        return []


@register.simple_tag
def recent_decisions(limit: int = 25):
    """Fetch recent decisions for custom admin pages."""
    try:
        Decision = apps.get_model("execution", "Decision")
        return Decision.objects.select_related("bot", "signal").order_by("-decided_at")[:limit]
    except Exception:
        return []


@register.simple_tag
def positions_count(status: str = None):
    """Return the number of positions, optionally filtered by status (e.g., 'open' or 'closed')."""
    try:
        Position = apps.get_model("execution", "Position")
        qs = Position.objects.all()
        if status:
            qs = qs.filter(status=status)
        return qs.count()
    except Exception:
        return 0


@register.simple_tag
def positions_long_short():
    """Return a dict with counts of long vs short positions (qty > 0 vs qty < 0)."""
    try:
        Position = apps.get_model("execution", "Position")
        longs = Position.objects.filter(qty__gt=0).count()
        shorts = Position.objects.filter(qty__lt=0).count()
        return {"longs": longs, "shorts": shorts}
    except Exception:
        return {"longs": 0, "shorts": 0}


@register.simple_tag
def recent_orders(limit: int = 25):
    """Fetch recent orders with related bot/broker for custom admin pages."""
    try:
        Order = apps.get_model("execution", "Order")
        return Order.objects.select_related("bot", "broker_account").order_by("-created_at")[:limit]
    except Exception:
        return []


@register.simple_tag
def recent_trade_logs(limit: int = 100):
    """Fetch recent trade logs with related order/bot/account for custom admin pages."""
    try:
        TradeLog = apps.get_model("execution", "TradeLog")
        return TradeLog.objects.select_related("order", "bot", "broker_account").order_by("-created_at")[:limit]
    except Exception:
        return []


@register.simple_tag
def recent_positions(limit: int = 25):
    """Fetch recent positions with related broker account for custom admin pages."""
    try:
        Position = apps.get_model("execution", "Position")
        return Position.objects.select_related("broker_account").order_by("-updated_at")[:limit]
    except Exception:
        return []
