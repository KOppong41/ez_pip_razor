from django.contrib import admin
from .models import Tenant


@admin.register(Tenant)
class TenantAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "slug", "is_active", "created_at")
    search_fields = ("name", "slug")
    readonly_fields = ("slug", "created_at", "updated_at")
    list_filter = ("is_active",)

