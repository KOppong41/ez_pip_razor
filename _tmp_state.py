import os, django, traceback
from datetime import timedelta
from django.utils import timezone
os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings')
try:
    django.setup()
    from execution.models import Execution, Order, Position
    now = timezone.now()
    since = now - timedelta(hours=1)
    print('Now', now)
    print('Executions last 1h:', Execution.objects.filter(exec_time__gte=since).count())
    for e in Execution.objects.filter(exec_time__gte=since).order_by('-exec_time'):
        print(f"exec {e.id} order={e.order_id} side={e.order.side} qty={e.qty} price={e.price} time={e.exec_time}")
    print('\nOrders last 1h:')
    for o in Order.objects.filter(created_at__gte=since).order_by('-created_at'):
        print(f"order {o.id} side={o.side} qty={o.qty} status={o.status} price={o.price} last_error={o.last_error}")
    print('\nCurrent positions:')
    for p in Position.objects.filter(status='open'):
        print(f"pos {p.id} symbol={p.symbol} qty={p.qty} avg={p.avg_price}")
except Exception:
    traceback.print_exc()
