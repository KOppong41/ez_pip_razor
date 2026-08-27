from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.http import JsonResponse
from django.db import connection
from django.http import HttpResponse
from django.utils import timezone
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from core.models import WorkerHeartbeat


@api_view(["GET", "POST"])
@permission_classes([AllowAny])
def desktop_bootstrap(request):
    """Create the first local desktop user, then permanently close setup."""
    if not getattr(settings, "DESKTOP_MODE", False):
        return Response(status=status.HTTP_404_NOT_FOUND)
    if request.META.get("REMOTE_ADDR") not in {"127.0.0.1", "::1"}:
        return Response(status=status.HTTP_403_FORBIDDEN)

    user_model = get_user_model()
    needs_setup = not user_model.objects.exists()
    if request.method == "GET":
        return Response({"needs_setup": needs_setup})
    if not needs_setup:
        return Response(
            {"detail": "Desktop setup has already been completed."},
            status=status.HTTP_409_CONFLICT,
        )

    username = str(request.data.get("username", "")).strip()
    password = str(request.data.get("password", ""))
    email = str(request.data.get("email", "")).strip()
    if not username:
        return Response(
            {"detail": "Username is required."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    try:
        validate_password(password)
    except ValidationError as exc:
        return Response(
            {"detail": " ".join(exc.messages)},
            status=status.HTTP_400_BAD_REQUEST,
        )

    user_model.objects.create_superuser(
        username=username,
        password=password,
        email=email,
    )
    return Response({"created": True}, status=status.HTTP_201_CREATED)

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
