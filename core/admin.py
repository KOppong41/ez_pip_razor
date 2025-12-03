
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin, GroupAdmin as BaseGroupAdmin
from django.contrib.auth.models import User, Group
from django.core.exceptions import PermissionDenied
from .models import CeleryActivity, Audit
from datetime import timedelta
from decimal import Decimal

from django.contrib import admin, messages
from django.shortcuts import redirect
from django.urls import path
from django.utils import timezone



def _is_admin(user):
    return user.is_superuser or user.groups.filter(name="Admin").exists()


# Replace the default User admin to enforce Admin-only changes.
try:
    admin.site.unregister(User)
except admin.sites.NotRegistered:
    pass
# Unregister default Group admin to swap in our themed form.
try:
    admin.site.unregister(Group)
except admin.sites.NotRegistered:
    pass

# Branding: update admin titles/headers.
admin.site.site_title = "EzTrade | Admin"
admin.site.site_header = "Dashboard"
admin.site.index_title = "Dashboard"


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    
    change_list_template = "admin/core/users.html"
    change_form_template = "admin/core/users_change_form.html"
    
    def has_add_permission(self, request):
        return _is_admin(request.user)

    def save_model(self, request, obj, form, change):
        if not _is_admin(request.user):
            raise PermissionDenied("Only Admins may create or modify users.")
        super().save_model(request, obj, form, change)

    def changelist_view(self, request, extra_context=None):
        qs = self.get_queryset(request)
        extra_context = extra_context or {}
        extra_context.update(
            users=qs.select_related(),
            total_users=qs.count(),
            superusers_count=qs.filter(is_superuser=True).count(),
            staff_count=qs.filter(is_staff=True).count(),
        )
        return super().changelist_view(request, extra_context=extra_context)


@admin.register(Group)
class GroupAdmin(BaseGroupAdmin):
    change_list_template = "admin/core/groups.html"
    change_form_template = "admin/core/user_group_change.html"

    def has_add_permission(self, request):
        return _is_admin(request.user)

    def has_change_permission(self, request, obj=None):
        return _is_admin(request.user)

    def has_delete_permission(self, request, obj=None):
        return _is_admin(request.user)

    def get_queryset(self, request):
        # Explicitly avoid any owner-based filtering; show all groups.
        return self.model._default_manager.all()

    def changelist_view(self, request, extra_context=None):
        qs = self.get_queryset(request)
        extra_context = extra_context or {}
        extra_context.update(
            groups=qs,
            total_groups=qs.count(),
        )
        return super().changelist_view(request, extra_context=extra_context)


@admin.register(CeleryActivity)
class CeleryActivityAdmin(admin.ModelAdmin):
    change_list_template = "admin/core/celery_activities.html"
    change_form_template = "admin/core/celery_activities_change_form.html"

    list_display = ("ts", "level", "task_name", "component", "message_short")
    list_filter = ("level", "component")
    search_fields = ("task_name", "task_id", "message")

    
    def message_short(self, obj):
        return (obj.message[:80] + "…") if obj.message and len(obj.message) > 80 else obj.message
    message_short.short_description = "Message"

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path(
                "prune-30-days/",
                self.admin_site.admin_view(self.prune_30_days),
                name="core_celeryactivity_prune_30_days",
            ),
            path(
                "prune-7-days/",
                self.admin_site.admin_view(self.prune_7_days),
                name="core_celeryactivity_prune_7_days",
            ),
        ]
        
        return custom_urls + urls


    def prune_30_days(self, request):
        if not self.has_delete_permission(request):
            messages.error(request, "You do not have permission to prune activities.")
            return redirect("admin:core_celeryactivity_changelist")

        cutoff = timezone.now() - timedelta(days=30)
        deleted, _ = CeleryActivity.objects.filter(ts__lt=cutoff).delete()
        messages.success(request, f"Pruned {deleted} activities older than 30 days.")
        return redirect("admin:core_celeryactivity_changelist")

    def prune_7_days(self, request):
        if not self.has_delete_permission(request):
            messages.error(request, "You do not have permission to prune activities.")
            return redirect("admin:core_celeryactivity_changelist")

        cutoff = timezone.now() - timedelta(days=7)
        deleted, _ = CeleryActivity.objects.filter(ts__lt=cutoff).delete()
        messages.success(request, f"Pruned {deleted} activities older than 7 days.")
        return redirect("admin:core_celeryactivity_changelist")

    
    def changelist_view(self, request, extra_context=None):
        qs = self.get_queryset(request)
        extra_context = extra_context or {}
        extra_context.update(
            {
                "total_activities": qs.count(),
                "info_count": qs.filter(level="INFO").count(),
                "warning_count": qs.filter(level="WARNING").count(),
                "error_count": qs.filter(level="ERROR").count(),
            }
        )
        return super().changelist_view(request, extra_context=extra_context)

@admin.register(Audit)
class AuditAdmin(admin.ModelAdmin):
    list_display = ("ts", "action", "entity", "entity_id")
    list_filter = ("action", "entity")
    search_fields = ("action", "entity", "entity_id")
    ordering = ("-ts",)
    readonly_fields = ("ts",)
