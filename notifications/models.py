from django.db import models
from django.conf import settings
from django.contrib.auth import get_user_model
from django.utils import timezone
from decimal import Decimal

User = get_user_model()


class NotificationPreference(models.Model):
    FREQUENCY_CHOICES = [
        ("15m", "Every 15 minutes"),
        ("30m", "Every 30 minutes"),
        ("1h", "Every hour"),
        ("4h", "Every 4 hours"),
        ("1d", "Daily"),
    ]

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="notification_pref")
    enabled = models.BooleanField(default=True)
    email = models.EmailField(blank=True, default="")
    extra_emails = models.JSONField(default=list, blank=True)

    # Toggles
    notify_order_filled = models.BooleanField(default=True)
    notify_order_error = models.BooleanField(default=True)
    notify_order_canceled = models.BooleanField(default=False)
    notify_daily_digest = models.BooleanField(default=False)
    digest_frequency = models.CharField(max_length=3, choices=FREQUENCY_CHOICES, default="1d")

    # Optional quiet hours (UTC)
    quiet_hours_start = models.TimeField(null=True, blank=True)
    quiet_hours_end = models.TimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"NotificationPreference({self.user})"


class NotificationLog(models.Model):
    STATUS_CHOICES = [
        ("sent", "sent"),
        ("skipped", "skipped"),
        ("failed", "failed"),
    ]
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="notification_logs")
    event_type = models.CharField(max_length=64)
    payload = models.JSONField(default=dict)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default="sent")
    error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["user", "event_type", "created_at"])]

    def __str__(self):
        return f"{self.event_type} -> {self.user}"
