#!/usr/bin/env python
import os
import django


os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from execution.models import Order  # noqa: E402
from execution.services.brokers import validate_order_conditions  # noqa: E402


order = Order.objects.order_by("-created_at").first()
print(f"Latest Order: {order.id}")
print(f"  Status: {order.status}")
print(f"  Symbol: {order.symbol} {order.side}")
print(f"  SL={order.sl}, TP={order.tp}")

valid, reason = validate_order_conditions(order)
print(f"\nValidation: valid={valid}, reason={reason}")

if order.status == "new":
    print("\nOrder still 'new' - dispatch hasn't run yet or failed silently")
