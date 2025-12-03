from decimal import Decimal, InvalidOperation

from django import template
from django.db.models import Count, Q

from execution.models import Position

register = template.Library()

@register.filter
def abs(value):
    """Return the absolute value of a number"""
    try:
        return abs(float(value)) if value else 0
    except (ValueError, TypeError):
        return 0

@register.simple_tag
def positions_count():
    """Return count of open positions"""
    return Position.objects.filter(status="open").count()

@register.simple_tag
def positions_long_short():
    """Return long vs short position counts"""
    long_count = Position.objects.filter(status="open", qty__gt=0).count()
    short_count = Position.objects.filter(status="open", qty__lt=0).count()
    return {"long": long_count, "short": short_count}

@register.simple_tag
def recent_positions(limit=25):
    """Return recent positions with related broker account data"""
    return Position.objects.select_related('broker_account').filter(
        status="open"
    ).order_by('-updated_at')[:limit]


@register.filter
def subtract(value, arg):
    """Return value - arg (supports decimals)."""
    try:
        return Decimal(value) - Decimal(arg)
    except (InvalidOperation, TypeError, ValueError):
        try:
            return float(value) - float(arg)
        except (TypeError, ValueError):
            return ""
