from django.core.mail import send_mail
from django.utils import timezone
from datetime import time

from notifications.models import NotificationPreference, NotificationLog


def _within_quiet_hours(pref: NotificationPreference) -> bool:
    if not pref.quiet_hours_start or not pref.quiet_hours_end:
        return False
    now = timezone.localtime().time()
    start = pref.quiet_hours_start
    end = pref.quiet_hours_end
    if start < end:
        return start <= now < end
    # Quiet hours across midnight
    return now >= start or now < end


def notify_user(user, event_type: str, subject: str, message: str, payload: dict | None = None):
    """
    Send an email notification if user prefs allow it.
    event_type examples: order_filled, order_error, order_canceled
    """
    payload = payload or {}
    pref = NotificationPreference.objects.filter(user=user).first()
    if not pref or not pref.enabled:
        NotificationLog.objects.create(user=user, event_type=event_type, payload=payload, status="skipped")
        return

    # Map event to toggle
    allowed = {
        "order_filled": pref.notify_order_filled,
        "order_error": pref.notify_order_error,
        "order_canceled": pref.notify_order_canceled,
    }.get(event_type, False)

    if not allowed or _within_quiet_hours(pref):
        NotificationLog.objects.create(user=user, event_type=event_type, payload=payload, status="skipped")
        return

    recipients = []
    if pref.email:
        recipients.append(pref.email)
    recipients.extend(pref.extra_emails or [])
    recipients = [r for r in recipients if r]
    if not recipients:
        NotificationLog.objects.create(user=user, event_type=event_type, payload=payload, status="skipped", error="No recipients")
        return

    try:
        send_mail(subject, message, None, recipients, fail_silently=False)
        NotificationLog.objects.create(user=user, event_type=event_type, payload=payload, status="sent")
    except Exception as e:
        NotificationLog.objects.create(user=user, event_type=event_type, payload=payload, status="failed", error=str(e))
