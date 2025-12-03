from django.contrib import admin
from .models import NotificationPreference, NotificationLog


@admin.register(NotificationPreference)
class NotificationPreferenceAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "enabled",
        "email",
        "notify_order_filled",
        "notify_order_error",
        "notify_daily_digest",
        "digest_frequency",
        "quiet_hours_start",
        "quiet_hours_end",
    )
    list_filter = ("enabled", "notify_order_filled", "notify_order_error", "notify_daily_digest", "digest_frequency")
    search_fields = ("user__username", "email")


@admin.register(NotificationLog)
class NotificationLogAdmin(admin.ModelAdmin):
    list_display = ("user", "event_type", "status", "created_at")
    list_filter = ("status", "event_type")
    search_fields = ("user__username", "event_type")
    readonly_fields = ("user", "event_type", "payload", "status", "error", "created_at")
