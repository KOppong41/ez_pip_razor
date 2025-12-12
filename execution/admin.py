from decimal import Decimal

from django.contrib import admin
from django.db.models import Count, Q

from bots.models import Bot
from .models import (
    Signal,
    Decision,
    Order,
    Execution,
    Position,
    TradeLog,
    ExecutionSetting,
    JournalEntry,
)

class OwnedAdmin(admin.ModelAdmin):
    """
    Restrict queryset to the current user unless superuser.
    Also assigns owner on admin-created objects if missing.
    """
    owner_field = "owner"

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if request.user.is_superuser:
            return qs
        return qs.filter(**{self.owner_field: request.user})

    def save_model(self, request, obj, form, change):
        if hasattr(obj, "owner") and not obj.owner:
            obj.owner = request.user
        super().save_model(request, obj, form, change)

@admin.register(Order)
class OrderAdmin(OwnedAdmin):
    
    change_list_template = "admin/executions/orders.html"
    change_form_template = "admin/executions/orders_change_form.html"
    
    list_display = ("id","client_order_id","bot","broker_account","symbol","side","status","created_at")
    list_filter = ("status","symbol","side")

    def changelist_view(self, request, extra_context=None):
        from django.core.paginator import Paginator, InvalidPage

        qs = (
            self.get_queryset(request)
            .select_related("bot", "broker_account")
            .order_by("-created_at")
        )
        paginator = Paginator(qs, 50)
        page_number = request.GET.get("page") or 1
        try:
            page_obj = paginator.page(page_number)
        except InvalidPage:
            page_obj = paginator.page(1)

        orders = list(page_obj.object_list)

        filled_statuses = {"filled", "part_filled"}
        error_statuses = {"error"}
        filled_orders = sum(1 for o in orders if o.status in filled_statuses)
        error_orders = sum(1 for o in orders if o.status in error_statuses)

        extra_context = extra_context or {}
        extra_context.update(
            {
                "orders": orders,
                "filled_orders": filled_orders,
                "error_orders": error_orders,
                "page_obj": page_obj,
                "paginator": paginator,
            }
        )
        return super().changelist_view(request, extra_context=extra_context)

@admin.register(Execution)
class ExecutionAdmin(OwnedAdmin):
    change_list_template = "admin/executions/executions.html"
    change_form_template = "admin/executions/executions_change_form.html"

    list_display = ("id", "order", "bot", "symbol", "side", "qty", "price", "account_balance", "exec_time")
    list_filter = ("order__bot", "order__symbol")
    date_hierarchy = "exec_time"

    @staticmethod
    def bot(obj):
        return obj.order.bot

    @staticmethod
    def symbol(obj):
        return obj.order.symbol

    @staticmethod
    def side(obj):
        return obj.order.side

    def changelist_view(self, request, extra_context=None):
        qs = self.get_queryset(request).select_related("order", "order__bot").order_by("-exec_time")
        executions = list(qs[:200])
        bots = {exec.order.bot for exec in executions if exec.order and exec.order.bot}
        symbols = sorted({exec.order.symbol for exec in executions if exec.order and exec.order.symbol})
        total_volume = sum((exec.qty or Decimal("0")) for exec in executions)
        extra_context = extra_context or {}
        extra_context.update({
            "executions": executions,
            "bots": sorted(bots, key=lambda bot: bot.name if bot else ""),
            "symbols": symbols,
            "total_volume": total_volume,
        })
        return super().changelist_view(request, extra_context=extra_context)


@admin.register(Position)
class PositionAdmin(OwnedAdmin):
    
    change_list_template = "admin/executions/positions.html"
    change_form_template = "admin/executions/positions_change_form.html"
    
    list_display = ("id", "broker_account", "symbol", "qty", "avg_price", "sl", "tp", "status", "updated_at")
    list_filter = ("status", "broker_account__broker", "symbol")
    search_fields = ("symbol", "broker_account__name")
    ordering = ("-updated_at",)

@admin.register(TradeLog)
class TradeLogAdmin(OwnedAdmin):
    
    change_list_template = "admin/executions/tradeslog.html"
    change_form_template = "admin/executions/tradeslog_change_form.html"
    
    list_display = ("id", "order", "bot", "symbol", "side", "qty", "price", "status", "pnl", "created_at")
    list_filter = ("status", "symbol", "side")
    search_fields = ("symbol", "order__client_order_id", "bot__name")
    date_hierarchy = "created_at"

    def changelist_view(self, request, extra_context=None):
        """
        Provide paginated logs to the custom tradeslog.html template instead
        of a fixed slice. The native admin list (Open in Admin) still uses
        standard pagination separately.
        """
        from django.core.paginator import Paginator, InvalidPage

        base_qs = (
            self.get_queryset(request)
            .select_related("order", "bot", "broker_account")
            .order_by("-created_at")
        )
        base_qs = base_qs.filter(status__in=["filled", "win", "loss", "breakeven"])
        paginator = Paginator(base_qs, 50)
        page_number = request.GET.get("page") or 1
        try:
            page_obj = paginator.page(page_number)
        except InvalidPage:
            page_obj = paginator.page(1)

        logs = list(page_obj.object_list)

        extra_context = extra_context or {}
        extra_context.update(
            {
                "logs": logs,
                "page_obj": page_obj,
                "paginator": paginator,
            }
        )
        return super().changelist_view(request, extra_context=extra_context)


@admin.register(JournalEntry)
class JournalEntryAdmin(OwnedAdmin):
    change_list_template = "admin/executions/journal.html"
    list_display = ("created_at", "event_type", "severity", "bot", "broker_account", "symbol")
    list_filter = ("severity", "event_type", "bot", "broker_account")
    search_fields = ("message", "event_type", "symbol", "order__client_order_id")
    readonly_fields = (
        "event_type",
        "severity",
        "message",
        "context",
        "symbol",
        "created_at",
        "bot",
        "broker_account",
        "order",
        "position",
        "signal",
        "decision",
    )
    date_hierarchy = "created_at"

    def has_add_permission(self, request):
        return False

    def changelist_view(self, request, extra_context=None):
        from django.core.paginator import Paginator, InvalidPage

        base_qs = (
            self.get_queryset(request)
            .select_related("bot", "broker_account", "order")
            .order_by("-created_at")
        )

        paginator = Paginator(base_qs, 75)
        page_number = request.GET.get("page") or 1
        try:
            page_obj = paginator.page(page_number)
        except InvalidPage:
            page_obj = paginator.page(1)

        entries = list(page_obj.object_list)
        stats = base_qs.aggregate(
            total=Count("id"),
            info=Count("id", filter=Q(severity="info")),
            warning=Count("id", filter=Q(severity="warning")),
            error=Count("id", filter=Q(severity="error")),
        )
        event_types = sorted(
            base_qs.order_by().values_list("event_type", flat=True).distinct()
        )

        extra_context = extra_context or {}
        extra_context.update(
            {
                "entries": entries,
                "page_obj": page_obj,
                "paginator": paginator,
                "severity_stats": stats,
                "event_types": event_types,
            }
        )
        return super().changelist_view(request, extra_context=extra_context)

@admin.register(Signal)
class SignalAdmin(OwnedAdmin):
    change_list_template = "admin/executions/signals.html"
    change_form_template = "admin/executions/signals_change_form.html"
    list_display = ("id","bot","source","symbol","timeframe","direction","received_at")
    list_filter = ("source","symbol","timeframe","direction")
    search_fields = ("symbol","dedupe_key")
    readonly_fields = ("received_at","dedupe_key","payload")

    def changelist_view(self, request, extra_context=None):
        qs = self.get_queryset(request).select_related("bot").order_by("-received_at")
        extra_context = extra_context or {}
        extra_context["signals"] = qs[:200]
        return super().changelist_view(request, extra_context=extra_context)


@admin.register(Decision)
class DecisionAdmin(OwnedAdmin):
    
    change_list_template = "admin/executions/decisions.html"
    change_form_template = "admin/executions/decisions_change_form.html"
    
    list_display = ("id","bot","signal","action","reason","score","decided_at")
    list_filter = ("action",)
    readonly_fields = ("decided_at",)

    def changelist_view(self, request, extra_context=None):
        qs = self.get_queryset(request).select_related("bot", "signal").order_by("-decided_at")
        extra_context = extra_context or {}
        extra_context["decisions"] = qs[:200]
        return super().changelist_view(request, extra_context=extra_context)


@admin.register(ExecutionSetting)
class ExecutionSettingAdmin(admin.ModelAdmin):
    
    change_list_template = "admin/executions/execution_settings.html"
    change_form_template = "admin/executions/execution_settings_change_form.html"
    
    list_display = (
        "key",
        "decision_min_score",
        "decision_flip_score",
        "decision_allow_hedging",
        "order_ack_timeout_seconds",
    )
    readonly_fields = ("key",)
    fieldsets = (
        ("Decision guardrails", {
            "fields": (
                "decision_min_score",
                "decision_flip_score",
                "decision_allow_hedging",
                "decision_flip_cooldown_min",
                "decision_max_flips_per_day",
                "decision_order_cooldown_sec",
            ),
        }),
        ("Opposite scalp tuning", {
            "fields": (
                "decision_scalp_sl_offset",
                "decision_scalp_tp_offset",
                "decision_scalp_qty_multiplier",
            ),
        }),
        ("Risk & monitoring", {
            "fields": (
                "early_exit_max_unrealized_pct",
                "trailing_trigger",
                "trailing_distance",
                "order_ack_timeout_seconds",
            ),
        }),
        ("Psychology & behavior", {
            "fields": (
                "max_loss_streak_before_pause",
                "loss_streak_cooldown_min",
                "drawdown_soft_limit_pct",
                "soft_size_multiplier",
                "drawdown_hard_limit_pct",
                "hard_size_multiplier",
            ),
        }),
        ("Accounts & sizing", {
            "fields": (
                "paper_start_balance",
                "mt5_default_contract_size",
                "bot_min_default_qty",
                "max_order_lot",
                "max_order_notional",
            ),
        }),
    )

    def has_add_permission(self, request):
        # Restrict to a single row; edits happen on the existing object.
        return not ExecutionSetting.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False
    
    
    def changelist_view(self, request, extra_context=None):
        qs = self.get_queryset(request).order_by("key")
        settings_list = list(qs)

        total = len(settings_list)
        decision = sum(1 for s in settings_list if s.get_category() == "decision")
        risk = sum(1 for s in settings_list if s.get_category() == "risk")
        account = sum(1 for s in settings_list if s.get_category() == "account")

        extra_context = extra_context or {}
        extra_context.update({
            "settings": settings_list,
            "total_settings_count": total,
            "decision_settings_count": decision,
            "risk_settings_count": risk,
            "account_settings_count": account,
        })
        return super().changelist_view(request, extra_context=extra_context)

