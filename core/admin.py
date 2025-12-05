
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin, GroupAdmin as BaseGroupAdmin
from django.contrib.auth.models import User, Group
from django.core.exceptions import PermissionDenied

from django.contrib import admin


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
