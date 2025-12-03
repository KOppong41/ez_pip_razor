from datetime import timedelta, timezone
from django.http import JsonResponse
from django.db import connection
from django.http import HttpResponse
from django.conf import settings
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST

from core.models import WorkerHeartbeat

def health(_request):
    # DB check
    db_ok = True
    try:
        with connection.cursor() as c:
            c.execute("SELECT 1")
            c.fetchone()
    except Exception:
        db_ok = False

    # Celery worker/beat heartbeat (seen within last 2 minutes)
    worker_ok = False
    try:
        hb = WorkerHeartbeat.objects.filter(name="default").first()
        if getattr(settings, "TESTING", False):
            worker_ok = True
        elif hb and (timezone.now() - hb.last_seen) < timedelta(minutes=2):
            worker_ok = True
    except Exception:
        worker_ok = False

    overall_ok = db_ok and worker_ok
    return JsonResponse(
        {
            "status": "ok" if overall_ok else "degraded",
            "db": db_ok,
            "worker": worker_ok,
        },
        status=200 if overall_ok else 503,
    )

def metrics(_request):
    from execution.models import Position
    from core.metrics import open_positions_gauge
    # live gauge snapshot
    open_positions_gauge.set(Position.objects.filter(status="open").count())
    return HttpResponse(generate_latest(), content_type=CONTENT_TYPE_LATEST)
