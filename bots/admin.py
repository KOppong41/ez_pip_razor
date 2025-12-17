from copy import copy

from datetime import time, timedelta
from decimal import Decimal

from django import forms
from django.conf import settings
from django.contrib import admin
from django.contrib.admin.views.main import ChangeList
from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils import timezone
from django.utils.html import format_html
from django.utils.safestring import mark_safe

from .models import Asset, Bot, STRATEGY_CHOICES, STANDARD_TIMEFRAMES, DEFAULT_TRADING_DAYS, CATEGORY_CHOICES, STRATEGY_GUIDES
from brokers.models import Broker
from execution.models import TradeLog
from execution.services.psychology import get_size_multiplier, reset_allocation_cycle
from execution.services.scalper_config import build_scalper_config
from execution.models import default_scalper_profile_config, Position, ScalperRunLog, TradeLog
from execution.utils.symbols import canonical_symbol
from django.db.models import Sum
from django.utils import timezone


# (Strategy model kept for legacy but managed via Bot.enabled_strategies)


@admin.register(Asset)
class AssetAdmin(admin.ModelAdmin):
    change_list_template = "admin/bots/assets.html"
    change_form_template = "admin/bots/asset_change_form.html"

    list_display = (
        "symbol",
        "display_name",
        "category",
        "min_qty",
        "recommended_qty",
        "max_spread",
        "min_notional",
        "is_active",
    )
    list_filter = ("is_active", "category")
    search_fields = ("symbol", "display_name")
    actions = ["activate_assets", "deactivate_assets"]

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path(
                "<path:object_id>/activate/",
                self.admin_site.admin_view(self.activate_asset_view),
                name="bots_asset_activate",
            ),
            path(
                "<path:object_id>/deactivate/",
                self.admin_site.admin_view(self.deactivate_asset_view),
                name="bots_asset_deactivate",
            ),
        ]
        return custom + urls

    def activate_assets(self, request, queryset):
        updated = queryset.update(is_active=True)
        self.message_user(request, f"Activated {updated} asset(s).")
    activate_assets.short_description = "Activate selected assets"

    def deactivate_assets(self, request, queryset):
        updated = queryset.update(is_active=False)
        self.message_user(request, f"Deactivated {updated} asset(s).")
    deactivate_assets.short_description = "Deactivate selected assets"

    def changelist_view(self, request, extra_context=None):
        qs = self.model._default_manager.all()
        active_assets_count = qs.filter(is_active=True).count()
        total_assets_count = qs.count()
        category_counts = {
            cat: qs.filter(category=cat).count() for cat, _ in CATEGORY_CHOICES
        }
        broker_qs = Broker.objects.all()
        recent_cutoff = timezone.now() - timedelta(days=30)
        extra_context = extra_context or {}
        extra_context.update({
            "assets": list(qs),
            "active_assets_count": active_assets_count,
            "total_assets_count": total_assets_count,
            "forex_assets_count": category_counts.get("forex", 0),
            "crypto_assets_count": category_counts.get("crypto", 0),
            "active_brokers_count": broker_qs.filter(is_active=True).count(),
            "connected_brokers_count": broker_qs.filter(is_active=True).count(),
            "total_brokers_count": broker_qs.count(),
            "recent_brokers_count": broker_qs.filter(created_at__gte=recent_cutoff).count(),
        })
        return super().changelist_view(request, extra_context=extra_context)

    def _toggle_asset_status(self, request, object_id, *, active):
        try:
            asset = self.get_object(request, object_id)
        except TypeError:
            asset = self.model._default_manager.filter(pk=object_id).first()
        if asset is None:
            raise Http404("Asset not found")
        if not self.has_change_permission(request, asset):
            raise PermissionDenied
        asset.is_active = active
        asset.save(update_fields=["is_active"])
        action = "activated" if active else "deactivated"
        self.message_user(request, f"Asset '{asset.symbol}' {action}.")
        next_url = request.POST.get("next") or request.GET.get("next") or reverse("admin:bots_asset_changelist")
        return HttpResponseRedirect(next_url)

    def activate_asset_view(self, request, object_id, *args, **kwargs):
        if request.method != "POST":
            return HttpResponseRedirect(reverse("admin:bots_asset_changelist"))
        return self._toggle_asset_status(request, object_id, active=True)

    def deactivate_asset_view(self, request, object_id, *args, **kwargs):
        if request.method != "POST":
            return HttpResponseRedirect(reverse("admin:bots_asset_changelist"))
        return self._toggle_asset_status(request, object_id, active=False)


STRATEGY_LABELS = {
    "harami": "Harami",
    "engulfing": "Engulfing",
    "hammer": "Hammer",
    "marubozu": "Marubozu",
    "shooting_star": "Shooting Star",
    "three_soldiers": "Three Soldiers",
    "sanpe_tonkachi_fvg": "Sanpe Tonkachi FVG",
    "sansen_sutsumi_liquidity": "Sansen Sutsumi Liquidity",
    "price_action_pinbar": "Price Action Pin Bar",
    "doji_breakout": "Doji Breakout",
    "trend_pullback": "Trend Pullback",
    "breakout_retest": "Breakout Retest",
    "range_reversion": "Range Reversion",
    "momentum_ignition": "Momentum Ignition",
}


def _strategy_help_text():
    """
    Build HTML list of per-strategy guidance so admins don't have to read a long paragraph.
    """
    items = []
    for code in STRATEGY_CHOICES:
        guide = STRATEGY_GUIDES.get(code, {})
        label = STRATEGY_LABELS.get(code, guide.get("label", code.title()))
        best_for = guide.get("best_for")
        notes = guide.get("notes")
        line = f"<strong>{label}</strong>"
        if best_for:
            line += f" — best for {best_for}"
        if notes:
            line += f"; {notes}"
        items.append(f"<li>{line}</li>")
    note = "<p>Manual strategy selection only applies when auto-trade is disabled.</p>"
    return mark_safe(note + "<div>Recommendations:<ul>" + "".join(items) + "</ul></div>")


WEEKDAY_CHOICES = [
    ("mon", "Monday"),
    ("tue", "Tuesday"),
    ("wed", "Wednesday"),
    ("thu", "Thursday"),
    ("fri", "Friday"),
    ("sat", "Saturday"),
    ("sun", "Sunday"),
]


class BotChangeList(ChangeList):
    """
    Custom ChangeList for Bot so that clicking the row link opens
    the pretty details page instead of the native /change/ page.
    """
    def url_for_result(self, result):
        # result is a Bot instance; self.pk_attname is usually "pk" / "id"
        pk = getattr(result, self.pk_attname)
        return reverse("admin:bots_bot_details", args=[pk])

class BotForm(forms.ModelForm):
    enabled_strategies = forms.MultipleChoiceField(
        required=False,
        choices=[(s, STRATEGY_LABELS.get(s, s.title())) for s in STRATEGY_CHOICES],
        widget=forms.CheckboxSelectMultiple,
        help_text=_strategy_help_text(),
    )
    allowed_timeframes = forms.MultipleChoiceField(
        required=False,
        choices=[(tf, tf) for tf in STANDARD_TIMEFRAMES],
        widget=forms.CheckboxSelectMultiple,
        help_text="Optional list of timeframes allowed for this bot.",
    )
    allowed_trading_days = forms.MultipleChoiceField(
        required=False,
        choices=WEEKDAY_CHOICES,
        widget=forms.CheckboxSelectMultiple,
        help_text="Weekdays when the bot may open new trades.",
    )
    scalper_risk_profile = forms.ChoiceField(
        required=False,
        choices=(),
        label="Scalper risk mode",
        help_text="Predefined TP/SL + kill-switch presets for the scalper engine.",
    )
    scalper_psychology_profile = forms.ChoiceField(
        required=False,
        choices=(),
        label="Scalper psychology mode",
        help_text="Loss-streak / drawdown behaviour profile for the scalper.",
    )

    class Meta:
        model = Bot
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        self.request = kwargs.pop("request", None)
        super().__init__(*args, **kwargs)
        # Only allow active assets to be selected
        self.fields["asset"].queryset = Asset.objects.filter(is_active=True)
        self.fields["enabled_strategies"].initial = self.instance.enabled_strategies or []
        self.fields["allowed_timeframes"].initial = self.instance.allowed_timeframes or []
        if "decision_min_score" in self.fields:
            self.fields["decision_min_score"].help_text = (
                "Minimum signal quality required before placing trades. "
                "Set to 0 to inherit the profile/global threshold."
            )
        if self.request and not self.instance.pk and "owner" in self.fields:
            self.fields["owner"].initial = self.request.user

        # Set initial readonly/disabled state for schedule fields based on the toggle.
        # Prefer POSTed value (for bound forms); fall back to instance value.
        if self.data:
            raw = self.data.get("trading_schedule_enabled")
            enabled = bool(raw)
        else:
            enabled = getattr(self.instance, "trading_schedule_enabled", True)

        # Initialise trading days depending on whether the schedule is enabled.
        days_initial = list(self.instance.allowed_trading_days or [])
        if enabled:
            self.fields["allowed_trading_days"].initial = days_initial or DEFAULT_TRADING_DAYS
        else:
            # When disabled, keep whatever is stored (often empty) without forcing defaults.
            self.fields["allowed_trading_days"].initial = days_initial

        # Configure scalper profile selectors + hide raw TP/SL settings when needed.
        self._init_scalper_profile_fields()

        schedule_fields = ("allowed_trading_days", "trading_window_start", "trading_window_end")
        if not enabled:
            for name in schedule_fields:
                field = self.fields.get(name)
                if field:
                    field.required = False
                    css = field.widget.attrs.get("class", "")
                    if "schedule-readonly" not in css:
                        css = (css + " schedule-readonly").strip()
                    field.widget.attrs["class"] = css
                    field.widget.attrs["disabled"] = "disabled"
        else:
            for name in schedule_fields:
                field = self.fields.get(name)
                if field:
                    field.required = True

    def clean_enabled_strategies(self):
        strategies = self.cleaned_data.get("enabled_strategies") or []
        auto_mode = self.cleaned_data.get("auto_trade")
        if auto_mode is None:
            auto_mode = getattr(self.instance, "auto_trade", True)
        if auto_mode:
            # Auto-trade mode ignores manual selections, but keep whatever was provided for future manual use.
            return strategies

        if not strategies:
            asset = self.cleaned_data.get("asset") or self.instance.asset
            default_set: list[str] = []
            try:
                cfg = build_scalper_config(self.instance)
                strategy_profiles = cfg.strategy_profiles or {}
                canon_symbol = canonical_symbol(getattr(asset, "symbol", ""))
                # Use explicitly chosen profile, then symbol-specific profile, then default.
                symbol_match = next(
                    (
                        prof.enabled_strategies
                        for prof in strategy_profiles.values()
                        if prof.symbol and canonical_symbol(prof.symbol) == canon_symbol
                    ),
                    None,
                )
                if symbol_match:
                    default_set = list(symbol_match)
                if not default_set:
                    profile = strategy_profiles.get(cfg.default_strategy_profile)
                    default_set = list(profile.enabled_strategies) if profile else []
            except Exception:
                default_set = []
            strategies = default_set or strategies
        if not strategies:
            raise forms.ValidationError("Select at least one strategy when auto-trade is disabled.")
        return strategies

    def clean_allowed_timeframes(self):
        return self.cleaned_data.get("allowed_timeframes") or []

    def clean_allowed_trading_days(self):
        days = self.cleaned_data.get("allowed_trading_days") or []
        enabled = self.cleaned_data.get("trading_schedule_enabled", True)
        # Always keep a sensible default set of days so that when the
        # schedule is re-enabled we already have a usable configuration.
        if not enabled:
            return days or DEFAULT_TRADING_DAYS
        return days or DEFAULT_TRADING_DAYS

    def clean_trading_window_start(self):
        value = self.cleaned_data.get("trading_window_start")
        enabled = self.cleaned_data.get("trading_schedule_enabled", True)
        if not enabled:
            # When schedule is disabled, keep existing or default start time
            if value:
                return value
            if self.instance and getattr(self.instance, "trading_window_start", None):
                return self.instance.trading_window_start
            return time(6, 0)
        return value

    def clean_trading_window_end(self):
        value = self.cleaned_data.get("trading_window_end")
        enabled = self.cleaned_data.get("trading_schedule_enabled", True)
        if not enabled:
            # When schedule is disabled, keep existing or default end time
            if value:
                return value
            if self.instance and getattr(self.instance, "trading_window_end", None):
                return self.instance.trading_window_end
            return time(18, 0)
        return value

    def clean(self):
        cleaned = super().clean()
        if not cleaned.get("owner") and self.request:
            cleaned["owner"] = self.request.user
            self.instance.owner = self.request.user
        # Require asset outside tests
        if not cleaned.get("asset") and not getattr(settings, "TESTING", False):
            raise forms.ValidationError({"asset": "Asset is required for bots."})
        return cleaned

    # --- internal helpers -------------------------------------------------

    def _is_scalper_mode(self):
        if self.data:
            mode = self.data.get("engine_mode")
        elif self.instance and getattr(self.instance, "engine_mode", None):
            mode = self.instance.engine_mode
        else:
            mode = None
        return (mode or "").lower() == "scalper"

    def _init_scalper_profile_fields(self):
        cfg = default_scalper_profile_config()
        risk_profiles = cfg.get("risk_presets", {})
        psych_profiles = cfg.get("psychology_profiles", {})
        risk_choices = [(k, v.get("name", k.replace("_", " ").title())) for k, v in risk_profiles.items()]
        psych_choices = [(k, v.get("name", k.replace("_", " ").title())) for k, v in psych_profiles.items()]
        if self._is_scalper_mode():
            scalper_params = (self.instance.scalper_params or {}) if self.instance else {}
            self.fields["scalper_risk_profile"].choices = risk_choices
            self.fields["scalper_risk_profile"].required = True
            self.fields["scalper_risk_profile"].initial = (
                scalper_params.get("risk_profile") or cfg.get("default_risk_preset")
            )
            self.fields["scalper_psychology_profile"].choices = psych_choices
            self.fields["scalper_psychology_profile"].required = True
            self.fields["scalper_psychology_profile"].initial = (
                scalper_params.get("psychology_profile") or cfg.get("default_psychology_profile")
            )
            # Hide raw pip / psychology override fields for scalper bots
            for name in (
                "default_tp_pips",
                "default_sl_pips",
                "kill_switch_max_unrealized_pct",
                "loss_streak_autopause_enabled",
                "max_loss_streak_before_pause",
                "loss_streak_cooldown_min",
                "soft_drawdown_limit_pct",
                "soft_size_multiplier",
                "hard_drawdown_limit_pct",
                "hard_size_multiplier",
            ):
                self.fields.pop(name, None)
        else:
            self.fields["scalper_risk_profile"].choices = risk_choices
            self.fields["scalper_risk_profile"].required = False
            self.fields["scalper_risk_profile"].initial = (
                cfg.get("default_risk_preset")
            )
            self.fields["scalper_psychology_profile"].choices = psych_choices
            self.fields["scalper_psychology_profile"].required = False
            self.fields["scalper_psychology_profile"].initial = (
                cfg.get("default_psychology_profile")
            )


# --- Bot list actions ---


@admin.action(description="Start selected bots")
def start_bots(modeladmin, request, queryset):
    queryset.update(status="active")


@admin.action(description="Pause selected bots")
def pause_bots(modeladmin, request, queryset):
    queryset.update(status="paused")


@admin.action(description="Stop selected bots")
def stop_bots(modeladmin, request, queryset):
    queryset.update(status="stopped")


@admin.register(Bot)
class BotAdmin(admin.ModelAdmin):
    change_list_template = "admin/bots/bots.html"  # Updated path
    change_form_template = "admin/bots/bot_change_form.html"  # For add/edit forms
    
    
    form = BotForm

    # Add a 'details' link column at the end
    list_display = (
        "id",
        "bot_id",
        "name",
        "owner",
        "asset",
        "status",
        "auto_trade",
        "engine_mode",
        "broker_account",
        "default_timeframe",
        "default_qty",
        "created_at",
        "enabled_strategies",
        "view_link",
    )
    list_filter = (
        "status",
        "auto_trade",
        "engine_mode",
        "default_timeframe",
        "broker_account__broker",
    )
    search_fields = ("name",)
    inlines = []
    actions = [start_bots, pause_bots, stop_bots]

    base_fieldsets = (
        ("Identity", {
            "fields": ("name", "owner", "status", "auto_trade", "engine_mode", "enabled_strategies"),
        }),
        ("Routing & Sizing", {
            "fields": ("asset", "default_timeframe", "allowed_timeframes", "default_qty"),
        }),
        ("Risk Limits", {
            "fields": (
                "decision_min_score",
                "risk_max_concurrent_positions",
                "max_trades_per_day",
                "trade_interval_minutes",
                "allow_opposite_scalp",
                "allocation_amount",
                "allocation_profit_pct",
                "allocation_loss_pct",
            ),
        }),
        ("Scalper Profiles", {
            "fields": ("scalper_risk_profile", "scalper_psychology_profile"),
            "description": "Select predefined risk & psychology modes (used when engine mode = scalper).",
        }),
        ("Psychology & behavior", {
            "fields": (
                "loss_streak_autopause_enabled",
                "max_loss_streak_before_pause",
                "loss_streak_cooldown_min",
                "soft_drawdown_limit_pct",
                "soft_size_multiplier",
                "hard_drawdown_limit_pct",
                "hard_size_multiplier",
                "current_loss_streak",
                "paused_until",
            ),
            "description": "Optional per-bot loss streak cooling and drawdown-based sizing (within global safety bounds).",
        }),
        ("Trading Profile", {
            "fields": (
                "trading_profile",
                "trading_schedule_enabled",
                "allowed_trading_days",
                "trading_window_start",
                "trading_window_end",
            ),
            "description": "Choose the risk profile and allowed trading window.",
        }),
        ("Pip Targets", {
            "fields": ("default_tp_pips", "default_sl_pips"),
            "description": "Default take-profit/stop-loss distances (in pips) used by execution helpers.",
        }),
        ("Kill Switch", {
            "fields": ("kill_switch_enabled", "kill_switch_max_unrealized_pct"),
        }),
        ("Broker", {
            "fields": ("broker_account",),
        }),
        ("Timestamps", {
            "fields": ("created_at",),
            "classes": ("collapse",),
        }),
        ("Identifiers", {
            "fields": ("bot_id",),
            "classes": ("collapse",),
        }),
    )
    fieldsets = base_fieldsets

    readonly_fields = ("created_at", "owner", "bot_id")

    # --------- Custom URLs for details / controls / duplicate ----------

    def get_urls(self):
        """
        Add custom admin URLs:
        - details view (pretty bot_details page)
        - start / pause / stop actions
        - duplicate bot
        """
        urls = super().get_urls()
        custom = [
            path(
                "<path:object_id>/details/",
                self.admin_site.admin_view(self.bot_details_view),
                name="bots_bot_details",
            ),
            path(
                "<path:object_id>/start/",
                self.admin_site.admin_view(self.start_bot_view),
                name="bots_bot_start",
            ),
            path(
                "<path:object_id>/pause/",
                self.admin_site.admin_view(self.pause_bot_view),
                name="bots_bot_pause",
            ),
            path(
                "<path:object_id>/stop/",
                self.admin_site.admin_view(self.stop_bot_view),
                name="bots_bot_stop",
            ),
            path(
                "<path:object_id>/duplicate/",
                self.admin_site.admin_view(self.duplicate_bot_view),
                name="bots_bot_duplicate",
            ),
        ]
        return custom + urls

    def get_changelist(self, request, **kwargs):
        """
        Use our custom ChangeList so the object link goes to /details/
        instead of /change/.
        """
        return BotChangeList

    # Small helper
    def _get_bot_or_404(self, request, object_id):
        obj = self.get_object(request, object_id)
        if obj is None:
            raise Http404("Bot not found")
        return obj

    # --------- Details view (uses templates/admin/bots/bot_details.html) -----

    def bot_details_view(self, request, object_id, *args, **kwargs):
        bot = self._get_bot_or_404(request, object_id)
        if not (self.has_view_permission(request, bot) or self.has_change_permission(request, bot)):
            raise PermissionDenied

        # Diagnostics: realized PnL today, size multiplier, and open positions DB-side.
        today = timezone.localdate()
        pnl_today = (
            TradeLog.objects.filter(bot=bot, created_at__date=today)
            .exclude(pnl__isnull=True)
            .aggregate(total=Sum("pnl"))
            .get("total")
            or 0
        )
        try:
            size_mult = get_size_multiplier(bot)
        except Exception:
            size_mult = 1

        db_positions = Position.objects.filter(
            broker_account=bot.broker_account,
            symbol=getattr(bot.asset, "symbol", None),
            status="open",
        )
        db_open_count = db_positions.count()

        recent_logs = (
            ScalperRunLog.objects.filter(bot=bot)
            .order_by("-created_at")[:10]
        )

        lifetime_pnl = (
            TradeLog.objects.filter(bot=bot)
            .exclude(pnl__isnull=True)
            .aggregate(total=Sum("pnl"))
            .get("total")
            or Decimal("0")
        )

        allocation_amount = Decimal(str(getattr(bot, "allocation_amount", Decimal("0")) or 0))
        profit_pct = Decimal(str(getattr(bot, "allocation_profit_pct", Decimal("0")) or 0))
        loss_pct = Decimal(str(getattr(bot, "allocation_loss_pct", Decimal("100")) or 100))
        profit_target = None
        loss_limit = None
        if allocation_amount > 0 and profit_pct > 0:
            profit_target = (allocation_amount * profit_pct) / Decimal("100")
        if allocation_amount > 0:
            if loss_pct > 0:
                loss_limit = (allocation_amount * loss_pct) / Decimal("100")
            else:
                loss_limit = allocation_amount

        allocation_baseline = getattr(bot, "allocation_start_pnl", Decimal("0"))
        allocation_relative = lifetime_pnl - allocation_baseline

        allocation_status = "Disabled"
        if allocation_amount > 0:
            allocation_status = "Active"
            if profit_target and allocation_relative >= profit_target:
                allocation_status = "Profit target hit"
            elif loss_limit and allocation_relative <= -loss_limit:
                allocation_status = "Loss limit hit"

        diagnostics = {
            "pnl_today": pnl_today,
            "size_multiplier": size_mult,
            "loss_streak": getattr(bot, "current_loss_streak", 0),
            "paused_until": getattr(bot, "paused_until", None),
            "db_open_positions": db_open_count,
            "allocation": {
                "amount": allocation_amount,
                "profit_pct": profit_pct,
                "loss_pct": loss_pct,
                "profit_target": profit_target,
                "loss_limit": loss_limit,
                "pnl_relative": allocation_relative,
                "lifetime_pnl": lifetime_pnl,
                "status": allocation_status,
                "started_at": getattr(bot, "allocation_started_at", None),
            },
        }

        context = {
            **self.admin_site.each_context(request),
            "opts": self.model._meta,
            "original": bot,
            "bot": bot,
            "title": f"Bot details: {bot.name}",
            "diagnostics": diagnostics,
            "scalper_run_logs": recent_logs,
        }
        return TemplateResponse(request, "admin/bots/bot_details.html", context)  # Updated path

    # --------- Start / pause / stop single bot from details page --------

    def _update_status_and_redirect(self, request, object_id, new_status, action_verb, *, reset_allocation=False):
        bot = self._get_bot_or_404(request, object_id)
        if not self.has_change_permission(request, bot):
            raise PermissionDenied

        bot.status = new_status
        bot.save()
        if reset_allocation:
            try:
                reset_allocation_cycle(bot, reason="manual_start")
            except Exception:
                pass
        self.message_user(request, f"Bot '{bot.name}' {action_verb}.")
        return HttpResponseRedirect(
            reverse("admin:bots_bot_details", args=[bot.pk])
        )

    def start_bot_view(self, request, object_id, *args, **kwargs):
        if request.method != "POST":
            return HttpResponseRedirect(
                reverse("admin:bots_bot_details", args=[object_id])
            )
        return self._update_status_and_redirect(
            request,
            object_id,
            "active",
            "started",
            reset_allocation=True,
        )

    def pause_bot_view(self, request, object_id, *args, **kwargs):
        if request.method != "POST":
            return HttpResponseRedirect(
                reverse("admin:bots_bot_details", args=[object_id])
            )
        return self._update_status_and_redirect(request, object_id, "paused", "paused")

    def stop_bot_view(self, request, object_id, *args, **kwargs):
        if request.method != "POST":
            return HttpResponseRedirect(
                reverse("admin:bots_bot_details", args=[object_id])
            )
        return self._update_status_and_redirect(request, object_id, "stopped", "stopped")

    # --------- Duplicate bot -------------------------------------------

    def duplicate_bot_view(self, request, object_id, *args, **kwargs):
        bot = self._get_bot_or_404(request, object_id)
        if not self.has_add_permission(request):
            raise PermissionDenied

        new_bot = copy(bot)
        new_bot.pk = None
        new_bot.id = None
        new_bot.name = f"{bot.name} (Copy)"
        new_bot.status = "stopped"
        new_bot.save()

        self.message_user(request, f"Bot '{bot.name}' duplicated as '{new_bot.name}'.")
        return HttpResponseRedirect(
            reverse("admin:bots_bot_change", args=[new_bot.pk])
        )

    # --------- Extra column: Details link ------------------------------

    def view_link(self, obj):
        url = reverse("admin:bots_bot_details", args=[obj.pk])
        return format_html('<a href="{}">Details</a>', url)

    view_link.short_description = "Details"

    # --------- Standard admin hooks -----------------------------------

    def get_form(self, request, obj=None, **kwargs):
        BaseForm = super().get_form(request, obj, **kwargs)

        class RequestAwareForm(BaseForm):
            def __init__(self, *args, **kw):
                kw["request"] = request
                super().__init__(*args, **kw)

        return RequestAwareForm

    def _should_show_scalper_fields(self, request, obj=None):
        if obj and obj.engine_mode == "scalper":
            return True
        mode = None
        if request.method == "POST":
            mode = request.POST.get("engine_mode")
        else:
            mode = request.GET.get("engine_mode")
        return (mode or "").lower() == "scalper"

    def get_fieldsets(self, request, obj=None):
        fieldsets = list(super().get_fieldsets(request, obj))
        show_scalper = self._should_show_scalper_fields(request, obj)
        if show_scalper:
            # Remove raw pip/psychology sections for scalper bots
            fieldsets = [fs for fs in fieldsets if fs[0] not in ("Pip Targets", "Psychology & behavior", "Kill Switch")]
        else:
            # Hide scalper profile selector and pip targets for non-scalper bots if user not admin
            fieldsets = [fs for fs in fieldsets if fs[0] != "Scalper Profiles"]
            if not request.user.is_superuser:
                fieldsets = [fs for fs in fieldsets if fs[0] != "Pip Targets"]
        return fieldsets

    def has_add_permission(self, request):
        is_admin = request.user.is_superuser or request.user.groups.filter(name="Admin").exists()
        return is_admin

    def save_model(self, request, obj, form, change):
        is_admin = request.user.is_superuser or request.user.groups.filter(name="Admin").exists()
        if not is_admin:
            raise PermissionDenied("Only Admins may create or modify bots.")
        if not obj.owner:
            obj.owner = request.user
        # If manual mode is enabled and no strategies were provided, seed sensible defaults.
        if not obj.auto_trade and not obj.enabled_strategies:
            try:
                cfg = build_scalper_config(obj)
                profile = cfg.strategy_profiles.get(cfg.default_strategy_profile)
                obj.enabled_strategies = list(profile.enabled_strategies) if profile else list(STRATEGY_CHOICES)
            except Exception:
                obj.enabled_strategies = list(STRATEGY_CHOICES)
        allocation_amount = Decimal(str(obj.allocation_amount or 0))
        allocation_changed = (not change and allocation_amount > 0) or ("allocation_amount" in form.changed_data if form else False)
        if allocation_changed:
            lifetime = (
                TradeLog.objects.filter(bot=obj)
                .exclude(pnl__isnull=True)
                .aggregate(total=Sum("pnl"))
                .get("total")
                or Decimal("0")
            )
            if allocation_amount > 0:
                obj.allocation_start_pnl = lifetime
                obj.allocation_started_at = timezone.now()
            else:
                obj.allocation_start_pnl = Decimal("0")
                obj.allocation_started_at = None

        if obj.engine_mode == "scalper":
            self._apply_scalper_presets(obj, form.cleaned_data)
        super().save_model(request, obj, form, change)

    def _apply_scalper_presets(self, bot, cleaned_data):
        cfg = default_scalper_profile_config()
        scalper_params = bot.scalper_params or {}
        risk_key = cleaned_data.get("scalper_risk_profile") or scalper_params.get("risk_profile") or cfg.get("default_risk_preset")
        psych_key = cleaned_data.get("scalper_psychology_profile") or scalper_params.get("psychology_profile") or cfg.get("default_psychology_profile")
        risk_profile = (cfg.get("risk_presets") or {}).get(risk_key, {})
        psych_profile = (cfg.get("psychology_profiles") or {}).get(psych_key, {})

        bot.default_tp_pips = Decimal(str(risk_profile.get("tp_pips", bot.default_tp_pips or 120)))
        bot.default_sl_pips = Decimal(str(risk_profile.get("sl_pips", bot.default_sl_pips or 70)))
        bot.kill_switch_enabled = True
        bot.kill_switch_max_unrealized_pct = Decimal(
            str(risk_profile.get("kill_switch_pct", bot.kill_switch_max_unrealized_pct or 5.0))
        )

        bot.loss_streak_autopause_enabled = bool(psych_profile.get("autopause", True))
        bot.max_loss_streak_before_pause = int(psych_profile.get("max_loss_streak", 3))
        bot.loss_streak_cooldown_min = int(psych_profile.get("cooldown_min", 60))
        bot.soft_drawdown_limit_pct = Decimal(str(psych_profile.get("soft_dd_pct", 3.0)))
        bot.hard_drawdown_limit_pct = Decimal(str(psych_profile.get("hard_dd_pct", 5.0)))
        bot.soft_size_multiplier = Decimal(str(psych_profile.get("soft_multiplier", 0.5)))
        bot.hard_size_multiplier = Decimal(str(psych_profile.get("hard_multiplier", 0.25)))

        scalper_params = bot.scalper_params or {}
        scalper_params["risk_profile"] = risk_key
        scalper_params["psychology_profile"] = psych_key
        bot.scalper_params = scalper_params
