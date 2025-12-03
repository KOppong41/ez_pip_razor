from decimal import Decimal
import csv

from django.contrib import admin
from django.db.models import Count, Sum, Q
from django.db.models.functions import Coalesce
from django.http import HttpResponse
from django.utils import timezone

from bots.models import Bot
from .models import Signal, Decision, Order, Execution, Position, TradeLog, ExecutionSetting, TradingProfile

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

    def get_urls(self):
        from django.urls import path

        urls = super().get_urls()
        custom = [
            path(
                "backtest/",
                self.admin_site.admin_view(self.backtest_view),
                name="execution_tradelog_backtest",
            ),
            path(
                "backtest/export/",
                self.admin_site.admin_view(self.backtest_export_view),
                name="execution_tradelog_backtest_export",
            ),
        ]
        return custom + urls

    def _backtest_queryset(self, request, symbol: str | None = None, period: str | None = None):
        """
        Base queryset for backtest logs:
        - Only trades that actually reached MT5 (orders filled/part-filled)
        - Include all outcomes for those orders; PnL may be empty for older rows.
        - Optional symbol filter and period filter.

        We build this queryset directly from TradeLog so that it mirrors the
        CSV export and is independent from any changelist filters, while still
        respecting per-owner scoping for non-superusers.
        """
        from .models import TradeLog

        qs = TradeLog.objects.select_related("order", "bot", "broker_account")

        # Respect owner scoping for normal staff users
        if not request.user.is_superuser:
            qs = qs.filter(owner=request.user)

        # Only keep orders that actually made it to MT5 (filled/part-filled)
        qs = qs.filter(order__status__in=["filled", "part_filled"]).order_by("-created_at")

        if symbol:
            qs = qs.filter(symbol__iexact=symbol)
        if period == "today":
            qs = qs.filter(created_at__date=timezone.localdate())
        return qs

    def backtest_view(self, request):
        symbol = (request.GET.get("symbol") or "").strip()
        period = (request.GET.get("period") or "all").lower()
        qs = self._backtest_queryset(request, symbol or None, period)
        logs = list(qs[:1000])

        metrics = qs.aggregate(
            total_count=Count("id"),
            total_pnl=Coalesce(Sum("pnl"), Decimal("0")),
            win_count=Count("id", filter=Q(status="win")),
            loss_count=Count("id", filter=Q(status="loss")),
        )
        total_trades = (metrics["win_count"] or 0) + (metrics["loss_count"] or 0)
        if total_trades > 0:
            metrics["win_rate"] = (metrics["win_count"] / total_trades) * 100
        else:
            metrics["win_rate"] = Decimal("0")

        context = {
            **self.admin_site.each_context(request),
            "opts": self.model._meta,
            "logs": logs,
            "title": "Backtest Data",
            "metrics": metrics,
            "current_symbol": symbol,
            "current_period": period,
        }
        from django.template.response import TemplateResponse

        return TemplateResponse(request, "admin/executions/backtest_logs.html", context)

    def backtest_export_view(self, request):
        symbol = (request.GET.get("symbol") or "").strip()
        period = (request.GET.get("period") or "all").lower()
        logs = self._backtest_queryset(request, symbol or None, period)
        now = timezone.now().strftime("%Y%m%d_%H%M%S")
        filename = f"backtest_logs_{now}.csv"

        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = f'attachment; filename="{filename}"'

        writer = csv.writer(response)
        headers = [
            "time",
            "bot",
            "account",
            "symbol",
            "side",
            "qty",
            "price",
            "pnl",
        ]
        writer.writerow(headers)

        for log in logs.iterator():
            order = log.order
            bot = log.bot
            acct = log.broker_account
            writer.writerow(
                [
                    log.created_at.isoformat(),
                    bot.name if bot else "",
                    acct.name if acct else "",
                    log.symbol,
                    log.side,
                    str(log.qty),
                    str(log.price or ""),
                    str(log.pnl or ""),
                ]
            )

        return response

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


@admin.register(TradingProfile)
class TradingProfileAdmin(admin.ModelAdmin):
    
    change_list_template = "admin/executions/trading_types.html"
    change_form_template = "admin/executions/trading_type_change_form.html"
    
    list_display = (
        "slug",
        "name",
        "risk_per_trade_pct",
        "max_trades_per_day",
        "max_concurrent_positions",
        "max_drawdown_pct",
        "is_default",
        "updated_at",
    )
    readonly_fields = ("updated_at",)
    list_editable = ("is_default",)
