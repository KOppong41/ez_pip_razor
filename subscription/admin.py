from django.contrib import admin
from django.utils import timezone

from .models import SubscriptionPlan, UserSubscription


@admin.register(SubscriptionPlan)
class SubscriptionPlanAdmin(admin.ModelAdmin):
    change_list_template = "admin/subscription/subscription_plans.html"
    change_form_template = "admin/subscription/subscription_plan_change_form.html"

    list_display = ("name", "broker_accounts_limit", "bots_limit", "duration_days")
    search_fields = ("name",)


@admin.register(UserSubscription)
class UserSubscriptionAdmin(admin.ModelAdmin):
    change_list_template = "admin/subscription/user_subscriptions.html"
    change_form_template = "admin/subscription/user_subscription_change_form.html"

    list_display = ("user", "plan", "is_active", "expires_at", "created_at")
    list_filter = ("is_active", "plan")
    search_fields = ("user__username", "user__email")
