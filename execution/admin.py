from decimal import Decimal

from django import forms
from django.contrib import admin, messages
from django.http import HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils.translation import gettext_lazy as _
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
    ScalperProfile,
    AccountSnapshot,
    BrokerPosition,
    BrokerSymbolMapping,
    ExecutionAttempt,
    MT5ConnectionState,
    RiskPolicy,
)
from django.utils import timezone

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


class SymbolProfileForm(forms.Form):
    aliases = forms.CharField(
        label=_("Aliases"),
        required=False,
        help_text=_("Comma separated MT5 symbols that map to this asset."),
    )
    execution_timeframes = forms.CharField(
        label=_("Execution TFs"),
        help_text=_("Comma separated execution timeframes (e.g. M1,M5)."),
    )
    context_timeframes = forms.CharField(
        label=_("Context TFs"),
        help_text=_("Comma separated context timeframes (e.g. M15,H1)."),
    )
    sl_min = forms.DecimalField(label=_("SL min"), max_digits=10, decimal_places=2)
    sl_max = forms.DecimalField(label=_("SL max"), max_digits=10, decimal_places=2)
    sl_unit = forms.ChoiceField(
        label=_("SL unit"),
        choices=[("points", "points"), ("pips", "pips"), ("price", "price")],
        initial="points",
    )
    tp_r_multiple = forms.DecimalField(label=_("TP R multiple"), max_digits=5, decimal_places=2, required=False)
    be_trigger_r = forms.DecimalField(label=_("BE trigger R"), max_digits=5, decimal_places=2)
    be_buffer_r = forms.DecimalField(label=_("BE buffer R"), max_digits=5, decimal_places=2)
    trail_trigger_r = forms.DecimalField(label=_("Trail trigger R"), max_digits=5, decimal_places=2)
    trail_mode = forms.CharField(label=_("Trail mode"))
    exit_mode = forms.ChoiceField(
        label=_("Exit mode"),
        choices=[("fixed_tp", "fixed_tp"), ("trail_only", "trail_only"), ("hybrid", "hybrid")],
        initial="fixed_tp",
    )
    trail_start_r = forms.DecimalField(label=_("Trail start R"), max_digits=5, decimal_places=2, required=False)
    tp1_r = forms.DecimalField(label=_("TP1 R (hybrid)"), max_digits=5, decimal_places=2, required=False)
    tp1_close_pct = forms.IntegerField(label=_("TP1 close % (hybrid)"), required=False, min_value=1, max_value=100)
    max_spread_points = forms.DecimalField(label=_("Max spread"), max_digits=10, decimal_places=2)
    max_spread_unit = forms.ChoiceField(
        label=_("Spread unit"),
        choices=[("points", "points"), ("pips", "pips"), ("price", "price")],
        initial="points",
    )
    max_slippage_points = forms.DecimalField(label=_("Max slippage"), max_digits=10, decimal_places=2)
    max_slippage_unit = forms.ChoiceField(
        label=_("Slippage unit"),
        choices=[("points", "points"), ("pips", "pips"), ("price", "price")],
        initial="points",
    )
    allow_countertrend = forms.BooleanField(label=_("Allow countertrend"), required=False)
    risk_pct = forms.DecimalField(label=_("Risk %"), max_digits=6, decimal_places=3)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            css = field.widget.attrs.get("class", "")
            field.widget.attrs["class"] = f"{css} form-control".strip()

    def clean(self):
        cleaned = super().clean()
        mode = cleaned.get("exit_mode") or "fixed_tp"
        tp_r = cleaned.get("tp_r_multiple")
        trail_start = cleaned.get("trail_start_r")
        tp1_r = cleaned.get("tp1_r")
        tp1_pct = cleaned.get("tp1_close_pct")
        if mode == "fixed_tp" and tp_r is None:
            self.add_error("tp_r_multiple", _("Required when exit_mode = fixed_tp"))
        if mode == "trail_only":
            if trail_start is None:
                self.add_error("trail_start_r", _("Required when exit_mode = trail_only"))
            cleaned["tp_r_multiple"] = None
        if mode == "hybrid":
            if tp1_r is None:
                self.add_error("tp1_r", _("Required when exit_mode = hybrid"))
            if tp1_pct is None:
                self.add_error("tp1_close_pct", _("Required when exit_mode = hybrid"))
            if trail_start is None:
                self.add_error("trail_start_r", _("Required when exit_mode = hybrid"))
            if tp1_r is not None and trail_start is not None and trail_start >= tp1_r:
                self.add_error("trail_start_r", _("trail_start_r must be less than tp1_r"))
        return cleaned


def _csv_list(value: str, upper: bool = False) -> list[str]:
    parts = [item.strip() for item in (value or "").split(",") if item.strip()]
    if upper:
        return [item.upper() for item in parts]
    return parts


def _csv_display(values) -> str:
    return ", ".join(values or [])


def _to_float(value):
    if value is None:
        return None
    try:
        return float(value)
    except Exception:
        return value


def _diagnostics_for_symbols(symbols_cfg: dict) -> list[dict]:
    """
    Return per-symbol diagnostics with last few decisions to help operators see why trades were skipped/placed.
    """
    data = []
    for sym, meta in symbols_cfg.items():
        variants = {sym.upper()}
        for alias in meta.get("aliases", []) or []:
            if alias:
                variants.add(str(alias).upper())
        decisions_data = []
        for decision in (
            Decision.objects.filter(signal__symbol__in=variants)
            .select_related("signal", "bot")
            .order_by("-id")[:3]
        ):
            payload = (decision.signal.payload or {}) if decision.signal else {}
            strategy_name = payload.get("strategy") or payload.get("engine")
            params = decision.params or {}
            entry = params.get("entry")
            sl = params.get("sl")
            tp = params.get("tp")
            risk = None
            rr = None
            try:
                if entry is not None and sl is not None:
                    risk_val = abs(Decimal(str(entry)) - Decimal(str(sl)))
                    risk = str(risk_val)
                    if tp is not None and risk_val > 0:
                        rr = str(abs(Decimal(str(tp)) - Decimal(str(entry))) / risk_val)
            except Exception:
                risk = None
                rr = None
            decisions_data.append(
                {
                    "decided_at": decision.decided_at,
                    "action": decision.action,
                    "reason": decision.reason,
                    "score": decision.score,
                    "strategy": strategy_name,
                    "signal_source": getattr(decision.signal, "source", None) if decision.signal else None,
                    "entry": entry,
                    "sl": sl,
                    "tp": tp,
                    "risk": risk,
                    "rr": rr,
                }
            )
        run_logs = []
        try:
            from bots.models import Bot
            from execution.models import ScalperRunLog

            bots = (
                Bot.objects.select_related("asset")
                .filter(engine_mode="scalper", asset__symbol__in=variants)
                .order_by("id")
            )
            for bot in bots:
                last_run = (
                    ScalperRunLog.objects.filter(bot=bot).order_by("-created_at").first()
                )
                if not last_run:
                    continue
                summary = last_run.summary or {}
                strategies = summary.get("strategies") or []
                run_logs.append(
                    {
                        "bot_id": bot.id,
                        "bot_name": bot.name,
                        "asset_symbol": getattr(bot.asset, "symbol", None) if bot.asset else None,
                        "created_at": last_run.created_at,
                        "session": last_run.session,
                        "timeframe": last_run.timeframe,
                        "strategies": strategies[:6],
                        "htf_bias": summary.get("htf_bias"),
                    }
                )
        except Exception:
            run_logs = []
        data.append(
            {
                "symbol": sym,
                "variants": sorted(variants),
                "exit_mode": meta.get("exit_mode", "fixed_tp"),
                "sl_unit": (meta.get("sl_points") or {}).get("unit") or meta.get("sl_points_unit") or "points",
                "spread_unit": meta.get("max_spread_unit") or "points",
                "slip_unit": meta.get("max_slippage_unit") or "points",
                "decisions": decisions_data,
                "run_logs": run_logs,
            }
        )
    return data

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


@admin.register(ExecutionAttempt)
class ExecutionAttemptAdmin(admin.ModelAdmin):
    list_display = ("order", "attempt_no", "status", "mt5_retcode", "filled_qty", "remaining_qty", "started_at")
    list_filter = ("status",)
    readonly_fields = tuple(field.name for field in ExecutionAttempt._meta.fields)


@admin.register(BrokerPosition)
class BrokerPositionAdmin(OwnedAdmin):
    list_display = ("broker_position_ticket", "broker_account", "ownership", "symbol", "side", "volume", "profit", "status")
    list_filter = ("ownership", "status", "symbol")
    search_fields = ("broker_position_ticket", "symbol", "comment")


@admin.register(AccountSnapshot)
class AccountSnapshotAdmin(admin.ModelAdmin):
    list_display = ("broker_account", "balance", "equity", "free_margin", "currency", "captured_at")
    readonly_fields = tuple(field.name for field in AccountSnapshot._meta.fields)


@admin.register(RiskPolicy)
class RiskPolicyAdmin(admin.ModelAdmin):
    list_display = ("broker_account", "risk_per_trade_pct", "max_daily_loss_pct", "max_account_drawdown_pct", "entries_enabled", "emergency_stop")


@admin.register(BrokerSymbolMapping)
class BrokerSymbolMappingAdmin(admin.ModelAdmin):
    list_display = ("broker_account", "canonical_symbol", "broker_symbol", "enabled", "trading_status", "last_tick_at")
    list_filter = ("enabled", "trading_status")


@admin.register(MT5ConnectionState)
class MT5ConnectionStateAdmin(admin.ModelAdmin):
    list_display = ("broker_account", "connected", "account_mode", "active_login", "active_server", "checked_at")
    readonly_fields = ("checked_at",)

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
    
    list_display = ("id", "order", "broker_ticket", "bot", "symbol", "side", "qty", "price", "status", "pnl", "created_at")
    list_filter = ("status", "symbol", "side")
    search_fields = ("symbol", "order__client_order_id", "bot__name", "broker_ticket")
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
        base_qs = base_qs.filter(status__in=["win", "loss", "breakeven"])
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
    CLEARABLE_LOGS = {
        "orders": (Order, _("Execution Orders")),
        "executions": (Execution, _("Execution Fills")),
        "trades": (TradeLog, _("Trade Logs")),
        "positions": (Position, _("Execution Positions")),
        "journal": (JournalEntry, _("Journal Entries")),
    }
    
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
                "trailing_trigger_unit",
                "trailing_distance",
                "trailing_distance_unit",
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
    
    
    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path(
                "clear-log/<str:category>/",
                self.admin_site.admin_view(self.clear_log_view),
                name="execution_clear_log",
            ),
            path(
                "scalper-symbols/",
                self.admin_site.admin_view(self.scalper_symbols_view),
                name="execution_scalper_symbols",
            ),
        ]
        return custom + urls

    def _redirect_to_changelist(self):
        return HttpResponseRedirect(reverse("admin:execution_executionsetting_changelist"))

    def clear_log_view(self, request, category: str):
        model_info = self.CLEARABLE_LOGS.get(category)
        if not model_info:
            messages.error(request, _("Unknown log category."))
            return self._redirect_to_changelist()
        model, label = model_info
        if request.method != "POST":
            messages.error(request, _("Invalid request method."))
            return self._redirect_to_changelist()
        confirm_text = (request.POST.get("confirm_input") or "").strip()
        expected = str(label)
        if confirm_text != expected:
            messages.error(
                request,
                _("Confirmation text mismatch. Type “%(text)s” to confirm.")
                % {"text": expected},
            )
            return self._redirect_to_changelist()
        count = model.objects.count()
        model.objects.all().delete()
        messages.success(
            request,
            _("Cleared %(count)s entries from %(label)s.")
            % {"count": count, "label": expected},
        )
        return self._redirect_to_changelist()

    def scalper_symbols_view(self, request):
        if not request.user.is_superuser:
            messages.error(request, _("Only superusers may modify scalper asset profiles."))
            return self._redirect_to_changelist()

        profile = ScalperProfile.get_or_create_default()
        effective_cfg = profile.get_config()
        symbols_cfg = effective_cfg.get("symbols", {})
        target_symbol = request.POST.get("symbol")
        bound_form = None

        if request.method == "POST":
            if target_symbol not in symbols_cfg:
                messages.error(request, _("Unknown symbol profile."))
            else:
                bound_form = SymbolProfileForm(request.POST, prefix=target_symbol)
                if bound_form.is_valid():
                    raw_config = profile.config or {}
                    raw_symbols = raw_config.setdefault("symbols", {})
                    base_entry = raw_symbols.get(target_symbol, {}).copy() or symbols_cfg.get(target_symbol, {}).copy()
                    cleaned = bound_form.cleaned_data

                    base_entry["aliases"] = _csv_list(cleaned.get("aliases") or "")
                    base_entry["execution_timeframes"] = _csv_list(cleaned.get("execution_timeframes"), upper=True)
                    base_entry["context_timeframes"] = _csv_list(cleaned.get("context_timeframes"), upper=True)
                    base_entry["sl_points"] = {
                        "min": _to_float(cleaned.get("sl_min")),
                        "max": _to_float(cleaned.get("sl_max")),
                        "unit": cleaned.get("sl_unit") or "points",
                    }
                    base_entry["tp_r_multiple"] = _to_float(cleaned.get("tp_r_multiple"))
                    base_entry["be_trigger_r"] = _to_float(cleaned.get("be_trigger_r"))
                    base_entry["be_buffer_r"] = _to_float(cleaned.get("be_buffer_r"))
                    base_entry["trail_trigger_r"] = _to_float(cleaned.get("trail_trigger_r"))
                    base_entry["trail_mode"] = cleaned.get("trail_mode")
                    base_entry["exit_mode"] = cleaned.get("exit_mode") or "fixed_tp"
                    base_entry["trail_start_r"] = _to_float(cleaned.get("trail_start_r"))
                    base_entry["tp1_r"] = _to_float(cleaned.get("tp1_r"))
                    base_entry["tp1_close_pct"] = cleaned.get("tp1_close_pct")
                    base_entry["max_spread_points"] = _to_float(cleaned.get("max_spread_points"))
                    base_entry["max_spread_unit"] = cleaned.get("max_spread_unit") or "points"
                    base_entry["max_slippage_points"] = _to_float(cleaned.get("max_slippage_points"))
                    base_entry["max_slippage_unit"] = cleaned.get("max_slippage_unit") or "points"
                    base_entry["allow_countertrend"] = bool(cleaned.get("allow_countertrend"))
                    base_entry["risk_pct"] = _to_float(cleaned.get("risk_pct"))

                    raw_symbols[target_symbol] = base_entry
                    profile.config = raw_config
                    profile.save(update_fields=["config"])
                    messages.success(request, _("Updated %(symbol)s profile.") % {"symbol": target_symbol})
                    return HttpResponseRedirect(request.path)
                else:
                    messages.error(request, _("Please correct the highlighted errors."))

        symbol_forms = []
        for symbol, entry in symbols_cfg.items():
            sl_points = entry.get("sl_points") or {}
            initial = {
                "aliases": _csv_display(entry.get("aliases", [])),
                "execution_timeframes": _csv_display(entry.get("execution_timeframes", [])),
                "context_timeframes": _csv_display(entry.get("context_timeframes", [])),
                "sl_min": sl_points.get("min"),
                "sl_max": sl_points.get("max"),
                "sl_unit": sl_points.get("unit") or entry.get("sl_points_unit") or "points",
                "tp_r_multiple": entry.get("tp_r_multiple"),
                "be_trigger_r": entry.get("be_trigger_r"),
                "be_buffer_r": entry.get("be_buffer_r"),
                "trail_trigger_r": entry.get("trail_trigger_r"),
                "trail_mode": entry.get("trail_mode"),
                "exit_mode": entry.get("exit_mode") or "fixed_tp",
                "trail_start_r": entry.get("trail_start_r"),
                "tp1_r": entry.get("tp1_r"),
                "tp1_close_pct": entry.get("tp1_close_pct"),
                "max_spread_points": entry.get("max_spread_points"),
                "max_spread_unit": entry.get("max_spread_unit") or "points",
                "max_slippage_points": entry.get("max_slippage_points"),
                "max_slippage_unit": entry.get("max_slippage_unit") or "points",
                "allow_countertrend": entry.get("allow_countertrend", False),
                "risk_pct": entry.get("risk_pct"),
            }
            if bound_form is not None and symbol == target_symbol:
                form = bound_form
            else:
                form = SymbolProfileForm(initial=initial, prefix=symbol)
            symbol_forms.append(
                {
                    "symbol": symbol,
                    "form": form,
                    "meta": entry,
                }
            )

        context = {
            **self.admin_site.each_context(request),
            "opts": ExecutionSetting._meta,
            "title": _("Scalper Asset Profiles"),
            "symbol_forms": symbol_forms,
            "profile": profile,
            "scalper_config": effective_cfg,
            "changelist_url": reverse("admin:execution_executionsetting_changelist"),
            "diagnostics": _diagnostics_for_symbols(symbols_cfg),
        }
        return TemplateResponse(request, "admin/executions/scalper_symbols.html", context)
    
    
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
            "scalper_symbols_url": reverse("admin:execution_scalper_symbols"),
            "clear_actions": [
                {
                    "slug": slug,
                    "label": str(label),
                    "count": model.objects.count(),
                    "url": reverse("admin:execution_clear_log", args=[slug]),
                }
                for slug, (model, label) in self.CLEARABLE_LOGS.items()
            ],
        })
        return super().changelist_view(request, extra_context=extra_context)
