from datetime import time

from django.db import models
from django.core.validators import MaxValueValidator, MinValueValidator
from decimal import Decimal
import secrets
import string

from django.apps import apps
from django.conf import settings
from django.core.exceptions import ValidationError

from brokers.models import BrokerAccount
from subscription.utils import get_bot_limit

DEFAULT_TRADING_DAYS = ["mon", "tue", "wed", "thu", "fri"]


def default_trading_days():
    return DEFAULT_TRADING_DAYS.copy()


TRADING_PROFILE_CHOICES = [
    ("very_safe", "Very Safe (Beginner)"),
    ("balanced", "Balanced"),
    ("day_trader", "Aggressive Day Trader"),
    ("scalper", "Scalper"),
    ("long_term", "Long-Term Investor"),
    ("custom", "Custom / Advanced"),
]


ENGINE_MODES = [
    ("external", "External signals (TradingView/Telegram)"),
    ("harami", "Internal engine (candlestick/SMC)"),
    ("scalper", "Internal scalper (M1-M5 high frequency)"),
]

# Strategy registry (update as you add engines/strategies)
# Each entry represents a standalone pattern/engine the bot can run.
STRATEGY_CHOICES = [
    "harami",
    "engulfing",
    "hammer",
    "marubozu",
    "shooting_star",
    "three_soldiers",
    "sanpe_tonkachi_fvg",
    "sansen_sutsumi_liquidity",
    "price_action_pinbar",
    "doji_breakout",
    "trend_pullback",
    "breakout_retest",
    "range_reversion",
    "momentum_ignition",
]

# Guidance for each strategy (description + recommended assets) to help new traders.
STRATEGY_GUIDES = {
    "harami": {
        "label": "Harami (inside candle reversal)",
        "best_for": "Major FX pairs in ranges (EURUSD, GBPUSD, AUDUSD)",
        "notes": "Look for inside bars at clear support/resistance; avoid during high-impact news.",
    },
    "engulfing": {
        "label": "Engulfing (momentum reversal)",
        "best_for": "Majors and indices after sharp moves (EURUSD, NAS100)",
        "notes": "Works best when aligned with higher-timeframe bias and fresh impulse moves.",
    },
    "hammer": {
        "label": "Hammer (reversal off support)",
        "best_for": "Metals and majors after selloffs",
        "notes": "Higher win rate when wick rejects a prior demand zone; avoid mid-range entries.",
    },
    "marubozu": {
        "label": "Marubozu (full-body momentum)",
        "best_for": "Trending indices/crypto (NAS100, BTCUSD)",
        "notes": "Use as breakout/continuation; confirm with volume or higher-timeframe trend.",
    },
    "shooting_star": {
        "label": "Shooting Star (reversal off resistance)",
        "best_for": "Metals and volatile crosses",
        "notes": "Stronger when rejecting a prior supply zone or round number.",
    },
    "three_soldiers": {
        "label": "Three Soldiers (bullish continuation)",
        "best_for": "Trending majors/indices (EURUSD, US500)",
        "notes": "Use after a pullback in trend; avoid chasing extended legs.",
    },
    "sanpe_tonkachi_fvg": {
        "label": "Sanpe Tonkachi FVG (liquidity sweep + imbalance)",
        "best_for": "FX majors and metals around session opens",
        "notes": "Look for liquidity grab into a fair value gap before entry.",
    },
    "sansen_sutsumi_liquidity": {
        "label": "Sansen Sutsumi Liquidity (three-candle SMC)",
        "best_for": "FX majors and metals",
        "notes": "Combine with session timing and nearby equal highs/lows.",
    },
    "price_action_pinbar": {
        "label": "Price Action Pin Bar (wick rejection)",
        "best_for": "Majors and commodities at swing highs/lows",
        "notes": "Confluence with key levels and trend direction improves reliability.",
    },
    "doji_breakout": {
        "label": "Doji Breakout (volatility expansion)",
        "best_for": "Majors and crypto into sessions (EURUSD, BTCUSD)",
        "notes": "Wait for break + retest of the doji range; avoid chopping markets.",
    },
    "trend_pullback": {
        "label": "Trend Pullback (EMA + structure)",
        "best_for": "Trending majors/indices on intraday (EURUSD, NAS100)",
        "notes": "Buy/sell pullbacks to a fast EMA within a confirmed trend; skip during chop/news.",
    },
    "breakout_retest": {
        "label": "Breakout + Retest",
        "best_for": "Liquid FX and metals on 5m–1h",
        "notes": "Trade range break then retest; require clean base and avoid high spread conditions.",
    },
    "range_reversion": {
        "label": "Range Reversion (mean reversion)",
        "best_for": "Tight ranges on majors (EURUSD, AUDUSD)",
        "notes": "Fade extremes of a defined range with volatility filter; disable around red news.",
    },
    "momentum_ignition": {
        "label": "Momentum Ignition (impulse continuation)",
        "best_for": "Indices/crypto after strong push (NAS100, BTCUSD)",
        "notes": "Enter on strong impulse + shallow pullback; use time-based stop if momentum dies.",
    },
}

def get_strategy_guides():
    """Return strategy guidance (label, best_for, notes) for UI/API helpers."""
    return STRATEGY_GUIDES

# Standard timeframes for selection
STANDARD_TIMEFRAMES = [
    "1m",
    "5m",
    "15m",
    "30m",
    "1h",
    "4h",
    "1d",
    "1w",
    "1mo",
]

CATEGORY_CHOICES = [
    ("forex", "Forex"),
    ("crypto", "Crypto"),
    ("indices", "Indices"),
    ("commodities", "Commodities"),
]

class Asset(models.Model):
    symbol = models.CharField(max_length=32, unique=True)
    display_name = models.CharField(max_length=64, blank=True, default="")
    min_qty = models.DecimalField(
        max_digits=20,
        decimal_places=8,
        default=Decimal("0.01"),
        help_text="Broker minimum lot size for this symbol (e.g., 0.01 for XAUUSDm, 0.10 for EURUSDm).",
    )
    recommended_qty = models.DecimalField(
        max_digits=20,
        decimal_places=8,
        default=Decimal("0.10"),
        help_text="Suggested default lot size for bots using this asset.",
    )
    max_spread = models.DecimalField(
        max_digits=20,
        decimal_places=8,
        default=Decimal("0"),
        help_text="Optional max spread allowed for this asset. 0 = no limit.",
    )
    min_notional = models.DecimalField(
        max_digits=20,
        decimal_places=8,
        default=Decimal("0"),
        help_text="Optional minimum notional (price*qty) for this asset. 0 = no limit.",
    )
    is_active = models.BooleanField(
        default=True,
        help_text="If False, hide this asset from selection for new bots.",
    )
    category = models.CharField(
        max_length=32,
        choices=CATEGORY_CHOICES,
        default="forex",
        help_text="Category used for filters and dashboard summaries.",
    )

    class Meta:
        ordering = ["symbol"]

    def __str__(self):
        return self.display_name or self.symbol

    @property
    def get_category(self):
        return self.category

    # Dashboard helpers used by the admin templates
    def get_bots_count(self):
        """
        Number of bots currently configured to use this asset.
        """
        return getattr(self, "bots", None).count() if hasattr(self, "bots") else 0

    def get_signals_count(self):
        """
        Number of execution signals seen for this asset's symbol.
        """
        try:
            from execution.models import Signal
        except Exception:
            return 0
        return Signal.objects.filter(symbol=self.symbol).count()

    def get_orders_count(self):
        """
        Number of orders created for this asset's symbol.
        """
        try:
            from execution.models import Order
        except Exception:
            return 0
        return Order.objects.filter(symbol=self.symbol).count()


def generate_bot_id(length=10):
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


class Bot(models.Model):
    STATUS_CHOICES = [
        ("active", "Active"),
        ("paused", "Paused"),
        ("stopped", "Stopped"),
    ]

    name = models.CharField(
        max_length=100,
        help_text="Human-readable name for this bot, e.g. 'EURUSD M5 Reversal Bot'.",
    )

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="bots",
    )
    bot_id = models.CharField(
        max_length=10,
        unique=True,
        null=True,
        blank=True,
        help_text="Immutable alphanumeric identifier for UI references.",
    )

    status = models.CharField(
        max_length=10,
        choices=STATUS_CHOICES,
        default="stopped",
        help_text="High-level state of the bot. Only bots with status='active' are allowed to trade.",
    )

    default_timeframe = models.CharField(
        max_length=10,
        default="5m",
        help_text="Primary timeframe used when fetching candles for this bot (e.g. '5m', '15m').",
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
        help_text="Timestamp when this bot configuration was created.",
    )

    default_qty = models.DecimalField(
        max_digits=20,
        decimal_places=8,
        default=Decimal("0.10"),
        help_text=(
            "Default lot size per trade before SL distance is considered. "
            "Adjust per symbol volatility (e.g. 0.10 for EURUSD, 0.02 for XAUUSD)."
        ),
    )

    default_tp_pips = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        default=Decimal("10"),
        help_text="Default take-profit distance for new decisions, expressed in pips.",
    )

    default_sl_pips = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        default=Decimal("5"),
        help_text="Default stop-loss distance for new decisions, expressed in pips.",
    )

    allowed_symbols = models.JSONField(
        default=list,
        blank=True,
        help_text=(
            "Optional list of MT5 symbols this bot is allowed to trade, e.g. ['EURUSDm', 'XAUUSDm']. "
            "Empty list = allow all symbols."
        ),
    )

    allowed_timeframes = models.JSONField(
        default=list,
        blank=True,
        help_text=(
            "Optional list of timeframes this bot is allowed to trade on, e.g. ['5m', '15m']. "
            "Empty list = allow all timeframes."
        ),
    )

    auto_trade = models.BooleanField(
        default=False,
        help_text=(
            "If enabled, decisions with action='open' will send live orders to MT5. "
            "If disabled, the bot only creates signals/decisions (paper mode)."
        ),
    )

    ai_trade_enabled = models.BooleanField(
        default=False,
        help_text="If enabled, ignore manual strategy selection and let the AI selector choose strategies per market conditions.",
    )

    broker_account = models.ForeignKey(
        BrokerAccount,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="bots",
        help_text="The MT5 (or other broker) account this bot trades on.",
    )

    asset = models.ForeignKey(
        Asset,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="bots",
        help_text="The primary symbol this bot trades. Required in non-test environments.",
    )

    engine_mode = models.CharField(
        max_length=32,
        choices=ENGINE_MODES,
        default="harami",
        help_text=(
            "How this bot receives trade ideas: "
            "'external' = signals from TradingView/Telegram/webhooks, "
            "'harami' = internal candlestick/SMC engine."
        ),
    )

    scalper_profile = models.ForeignKey(
        "execution.ScalperProfile",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="bots",
        help_text="Optional scalper configuration profile. Required when engine_mode='scalper'.",
    )
    scalper_params = models.JSONField(
        default=dict,
        blank=True,
        help_text=(
            "Per-bot overrides for the scalper profile (symbols, sessions, risk). "
            "Keys mirror ScalperProfile.config – leave empty to inherit profile defaults."
        ),
    )

    # Per-bot strategy allowlist
    enabled_strategies = models.JSONField(
        default=list,
        blank=True,
        help_text=(
            "Select which engine strategies this bot may run (tick one or more). "
            "Examples: Harami, Engulfing, Hammer, Marubozu, Shooting Star, Three Soldiers, "
            "Sanpe Tonkachi FVG, Sansen Sutsumi Liquidity, Doji, Price Action Pin Bar, Doji Breakout."
        ),
    )
    decision_min_score = models.DecimalField(
        max_digits=6,
        decimal_places=4,
        default=Decimal("0.5"),
        help_text=(
            "Minimum decision score required for this bot. "
            "Set to 0 to inherit the scalper profile/global default."
        ),
    )

    risk_max_positions_per_symbol = models.PositiveIntegerField(
        default=1,
        help_text="Maximum number of open positions this bot may hold per symbol (e.g. 1 = never stack multiple EURUSD trades).",
    )

    risk_max_concurrent_positions = models.PositiveIntegerField(
        default=5,
        help_text="Maximum number of open positions this bot may hold across all symbols at the same time.",
    )

    kill_switch_enabled = models.BooleanField(
        default=True,
        help_text=(
            "If enabled, background tasks monitor unrealized PnL for this bot and close its positions "
            "automatically when loss exceeds the configured percentage threshold."
        ),
    )

    kill_switch_max_unrealized_pct = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal("5.0"),
        validators=[
            MinValueValidator(Decimal("0.1")),
            MaxValueValidator(Decimal("100.0")),
        ],
        help_text=(
            "Maximum allowed unrealized loss as a percentage (e.g. 5.00 = 5%). "
            "If floating loss reaches this level and kill switch is enabled, positions are closed."
        ),
    )

    trade_interval_minutes = models.PositiveIntegerField(
        default=15,
        validators=[MinValueValidator(0), MaxValueValidator(30)],
        help_text=(
            "Minimum time gap (in minutes) between trades for this bot. "
            "If the last trade was more recent than this, new entries are ignored. "
            "Set to 0 to disable the spacing."
        ),
    )

    max_trades_per_day = models.PositiveIntegerField(
        default=10,
        validators=[MinValueValidator(0), MaxValueValidator(200000)],
        help_text=(
            "Daily cap on filled trades for this bot. Once this number of trades is filled in a day, "
            "new trade decisions are ignored. Set to 0 to disable the daily cap."
        ),
    )

    trading_profile = models.CharField(
        max_length=64,
        choices=TRADING_PROFILE_CHOICES,
        default="very_safe",
        help_text="Choose the profile/default behavior for this bot.",
    )
    trading_schedule_enabled = models.BooleanField(
        default=True,
        help_text=(
            "If enabled, the bot only opens new trades during the configured days and time window. "
            "If disabled, it may open trades at any time (24/7), subject to other risk checks."
        ),
    )
    allowed_trading_days = models.JSONField(
        default=default_trading_days,
        blank=True,
        help_text="Weekdays (mon..sun) during which the bot can open new trades.",
    )
    trading_window_start = models.TimeField(
        default=time(6, 0),
        help_text="Local time to start opening trades each day.",
    )
    trading_window_end = models.TimeField(
        default=time(18, 0),
        help_text="Local time to stop opening trades each day.",
    )

    allow_opposite_scalp = models.BooleanField(
        default=False,
        help_text="Allow opening a small opposite-direction scalp while keeping the main position open.",
    )

    # Psychology / cooling state
    current_loss_streak = models.PositiveIntegerField(
        default=0,
        help_text="Automatically incremented when trades close at a loss; resets on wins.",
    )
    paused_until = models.DateTimeField(
        null=True,
        blank=True,
        help_text="If set, the bot will not auto-trade until this time is reached (used for automatic cool-down after loss streaks).",
    )

    # Optional per-bot psychology config (can only be more conservative than global settings).
    loss_streak_autopause_enabled = models.BooleanField(
        default=False,
        help_text=(
            "If enabled, this bot will auto-pause after a configurable loss streak. "
            "Global settings still act as an upper safety bound."
        ),
    )
    max_loss_streak_before_pause = models.PositiveIntegerField(
        default=0,
        help_text="If >0 and auto-pause is enabled, pause this bot after this many consecutive losing trades.",
    )
    loss_streak_cooldown_min = models.PositiveIntegerField(
        default=0,
        help_text="Minutes to keep this bot paused after a loss streak pause trigger (0 = stay paused until manually resumed).",
    )
    soft_drawdown_limit_pct = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text=(
            "Optional per-bot soft daily drawdown limit as a percentage of starting balance. "
            "When breached, position size is reduced according to the soft multiplier."
        ),
    )
    hard_drawdown_limit_pct = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text=(
            "Optional per-bot hard daily drawdown limit as a percentage of starting balance. "
            "When breached, position size is heavily reduced according to the hard multiplier."
        ),
    )
    soft_size_multiplier = models.DecimalField(
        max_digits=8,
        decimal_places=4,
        default=Decimal("1.0000"),
        help_text="Size multiplier once the soft drawdown limit is breached (e.g. 0.5 = half size).",
    )
    hard_size_multiplier = models.DecimalField(
        max_digits=8,
        decimal_places=4,
        default=Decimal("1.0000"),
        help_text="Size multiplier once the hard drawdown limit is breached (e.g. 0.25 = quarter size).",
    )

    class Meta:
        unique_together = (("owner", "name"),)

    def __str__(self):
        return f"{self.name} ({self.status})"

    def accepts(self, symbol: str, timeframe: str) -> bool:
        asset_symbol = self.asset.symbol if self.asset else None
        legacy_symbols = self.allowed_symbols or []
        sym_ok = False
        if asset_symbol:
            sym_ok = symbol == asset_symbol
        elif legacy_symbols:
            sym_ok = symbol in legacy_symbols
        tf_ok = (not self.allowed_timeframes) or (timeframe in self.allowed_timeframes)
        return self.status == "active" and sym_ok and tf_ok

    def clean(self):
        owner = self.owner
        if not owner and not getattr(settings, "TESTING", False):
            raise ValidationError("Owner is required for bots.")

        # Validate strategies are from the known registry unless AI Trade is handling selection.
        if not self.ai_trade_enabled:
            if not self.enabled_strategies:
                raise ValidationError({"enabled_strategies": "Select at least one strategy for this bot."})

            invalid = [s for s in self.enabled_strategies if s not in STRATEGY_CHOICES]
            if invalid:
                raise ValidationError({"enabled_strategies": f"Unknown strategies: {', '.join(invalid)}"})

        # Decision score guardrail
        try:
            Decimal(str(getattr(self, "decision_min_score", Decimal("0.5"))))
        except Exception:
            raise ValidationError({"decision_min_score": "Invalid decision_min_score value."})

        # Validate allowed_timeframes against standard choices
        if self.allowed_timeframes:
            invalid_tf = [tf for tf in self.allowed_timeframes if tf not in STANDARD_TIMEFRAMES]
            if invalid_tf:
                raise ValidationError({"allowed_timeframes": f"Unsupported timeframes: {', '.join(invalid_tf)}"})

        # Validate trading days only when a schedule is in use.
        days = self.allowed_trading_days or []
        if days:
            normalized_days = [day.lower() for day in days]
            allowed_days = {"mon", "tue", "wed", "thu", "fri", "sat", "sun"}
            invalid_days = [day for day in normalized_days if day not in allowed_days]
            if invalid_days:
                raise ValidationError({"allowed_trading_days": f"Unsupported days: {', '.join(invalid_days)}"})

        # Enforce a minimum lot size based on registered assets to avoid broker rejections.
        if not getattr(settings, "TESTING", False):
            from decimal import Decimal as _D
            if not self.asset:
                raise ValidationError({"asset": "Asset is required for bots."})
            if self.asset and not getattr(self.asset, "is_active", True):
                raise ValidationError({"asset": f"Asset {self.asset.symbol} is not active."})
            min_required = _D(str(self.asset.min_qty))
            setting_model = apps.get_model("execution", "ExecutionSetting")
            setting = setting_model.objects.first()
            if setting and setting.bot_min_default_qty:
                bot_min = _D(str(setting.bot_min_default_qty))
                if bot_min > min_required:
                    min_required = bot_min
            if self.default_qty < min_required:
                raise ValidationError(
                    {"default_qty": f"Default qty {self.default_qty} is below minimum {min_required} for {self.asset.symbol}."}
                )

        # Require a verified broker account before a bot can exist.
        if not getattr(settings, "TESTING", False):
            if not self.broker_account:
                raise ValidationError("Broker account is required for bots.")
            if not getattr(self.broker_account, "is_active", False):
                raise ValidationError("Broker account must be active.")
            if self.broker_account.broker in ["mt5", "exness_mt5", "icmarket_mt5"]:
                creds = self.broker_account.get_creds()
                missing = [k for k in ("login", "password", "server", "path") if not creds.get(k)]
                if missing:
                    raise ValidationError(f"Broker account missing credentials: {', '.join(missing)}")

        limit = get_bot_limit(owner)
        if owner:
            existing = (
                self.__class__.objects.filter(owner=owner)
                .exclude(pk=self.pk if self.pk else None)
                .count()
            )
            if existing >= limit:
                raise ValidationError(f"Bot limit reached ({limit}). Upgrade subscription to add more.")

            symbols = set([self.asset.symbol] if self.asset else [])
            if not symbols:
                raise ValidationError("Asset must be set to avoid asset conflicts.")
            conflict = (
                self.__class__.objects.filter(owner=owner)
                .exclude(pk=self.pk if self.pk else None)
                .filter(status__in=["active", "paused"])
            )
            for other in conflict:
                other_syms = set([other.asset.symbol] if other.asset else [])
                if not other_syms or symbols & other_syms:
                    raise ValidationError("Another bot already targets one of these symbols for this user.")

        return super().clean()

    def save(self, *args, **kwargs):
        self.full_clean()
        if not self.bot_id:
            self.bot_id = generate_bot_id()
        super().save(*args, **kwargs)


class Strategy(models.Model):
    bot = models.ForeignKey(
        Bot,
        on_delete=models.CASCADE,
        related_name="strategies",
        help_text="Bot that owns this strategy configuration.",
    )
    name = models.CharField(
        max_length=100,
        help_text="Strategy name, e.g. 'harami', 'engulfing', or a custom label.",
    )
    version = models.CharField(
        max_length=32,
        default="v1",
        help_text="Strategy version tag, e.g. 'v1', 'v2', 'experiment_01'.",
    )
    params = models.JSONField(
        default=dict,
        help_text="JSON parameters for this strategy (risk, filters, etc.).",
    )
    enabled = models.BooleanField(
        default=True,
        help_text="If disabled, this strategy configuration is ignored for the bot.",
    )

    class Meta:
        unique_together = ("bot", "name", "version")

    def __str__(self):
        return f"{self.bot.name}:{self.name}@{self.version}"
