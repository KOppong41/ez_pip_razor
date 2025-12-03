
import os, django, traceback
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from execution.models import Order
from execution.services.brokers import dispatch_place_order

order = Order.objects.get(id=669)
print(f"Dispatching Order 669...")
print(f"  Status before: {order.status}")

try:
    dispatch_place_order(order)
    order.refresh_from_db()
    print(f"✓ Dispatch succeeded!")
    print(f"  Status after: {order.status}")
    print(f"  Error: {order.last_error or 'None'}")
except Exception as e:
    print(f"✗ Dispatch failed: {e}")
    traceback.print_exc()
    order.refresh_from_db()
    print(f"  Status: {order.status}")
    print(f"  Error: {order.last_error}")
