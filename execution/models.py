from datetime import time
from decimal import Decimal
from django.db import models
from django.conf import settings
from bots.models import Bot
from brokers.models import BrokerAccount




class Signal(models.Model):
    bot = models.ForeignKey(Bot, null=True, blank=True, on_delete=models.SET_NULL, related_name="signals")
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="signals_owned",
    )
    source = models.CharField(max_length=32)  # tradingview, email, test
    symbol = models.CharField(max_length=32)
    timeframe = models.CharField(max_length=10, default="5m")
    direction = models.CharField(max_length=5, choices=[("buy","buy"),("sell","sell")])
    payload = models.JSONField(default=dict)
    dedupe_key = models.CharField(max_length=128, unique=True)
    received_at = models.DateTimeField(auto_now_add=True)
    trail_trigger = models.DecimalField(max_digits=12, decimal_places=6, default=0)  # profit in price units to start trailing
    trail_distance = models.DecimalField(max_digits=12, decimal_places=6, default=0)  # distance in price units to trail

    class Meta:
        indexes = [models.Index(fields=["dedupe_key"])]

    def save(self, *args, **kwargs):
        if not self.owner and self.bot and self.bot.owner_id:
            self.owner = self.bot.owner
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        bot_name = self.bot.name if self.bot else "No bot"
        return f"{self.symbol} {self.direction} {self.timeframe} via {self.source} ({bot_name})"

class Decision(models.Model):
    bot = models.ForeignKey(Bot, null=True, blank=True, on_delete=models.SET_NULL, related_name="decisions")
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="decisions_owned",
    )
    signal = models.ForeignKey(Signal, on_delete=models.CASCADE, related_name="decisions")
    action = models.CharField(max_length=16, choices=[("open","open"),("close","close"),("ignore","ignore")])
    reason = models.CharField(max_length=255, blank=True)
    score = models.FloatField(default=0.0)
    params = models.JSONField(default=dict)
    decided_at = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        if not self.owner:
            if self.bot and self.bot.owner_id:
                self.owner = self.bot.owner
            elif self.signal and self.signal.owner_id:
                self.owner_id = self.signal.owner_id
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        sym = self.signal.symbol if self.signal else "-"
        dirn = self.signal.direction if self.signal else "-"
        return f"{self.action} {sym} {dirn} ({self.reason or 'no-reason'})"

class Order(models.Model):
    STATUS = [
        ("new", "new"),
        ("ack", "ack"),  # Order acknowledged by broker
        ("filled", "filled"),
        ("part_filled", "part_filled"),  # Partially filled
        ("canceled", "canceled"),
        ("error", "error"),
    ]
    bot = models.ForeignKey(Bot, on_delete=models.CASCADE, related_name="orders")
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="orders_owned",
    )
    broker_account = models.ForeignKey(BrokerAccount, on_delete=models.CASCADE, related_name="orders")
    client_order_id = models.CharField(max_length=64, unique=True)
    symbol = models.CharField(max_length=32)
    side = models.CharField(max_length=4, choices=[("buy","buy"),("sell","sell")])
    qty = models.DecimalField(max_digits=20, decimal_places=8)
    price = models.DecimalField(max_digits=20, decimal_places=8, null=True, blank=True)
    sl = models.DecimalField(max_digits=20, decimal_places=8, null=True, blank=True)
    tp = models.DecimalField(max_digits=20, decimal_places=8, null=True, blank=True)
    status = models.CharField(max_length=50, choices=STATUS, default="new")
    last_error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def save(self, *args, **kwargs):
        if not self.owner:
            if self.bot and self.bot.owner_id:
                self.owner = self.bot.owner
            elif self.broker_account and self.broker_account.owner_id:
                self.owner_id = self.broker_account.owner_id
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.symbol} {self.side} {self.qty} [{self.status}]"

class Execution(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="executions")
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="executions_owned",
    )
    qty = models.DecimalField(max_digits=20, decimal_places=8)
    price = models.DecimalField(max_digits=20, decimal_places=8)
    fee = models.DecimalField(max_digits=20, decimal_places=8, default=0)
    exec_time = models.DateTimeField(auto_now_add=True)
    account_balance = models.DecimalField(
        max_digits=20,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Account balance immediately after this execution.",
    )


    def __str__(self) -> str:
        return f"Exec {self.order.symbol} {self.order.side} {self.qty} @ {self.price}"

    def save(self, *args, **kwargs):
        if not self.owner and self.order:
            if self.order.owner_id:
                self.owner_id = self.order.owner_id
            elif self.order.bot and self.order.bot.owner_id:
                self.owner = self.order.bot.owner
        super().save(*args, **kwargs)

class Position(models.Model):
    broker_account = models.ForeignKey(BrokerAccount, on_delete=models.CASCADE, related_name="positions")
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="positions_owned",
    )
    symbol = models.CharField(max_length=32)
    qty = models.DecimalField(max_digits=20, decimal_places=8, default=0)
    avg_price = models.DecimalField(max_digits=20, decimal_places=8, default=0)
    sl = models.DecimalField(max_digits=20, decimal_places=8, null=True, blank=True)
    tp = models.DecimalField(max_digits=20, decimal_places=8, null=True, blank=True)
    status = models.CharField(max_length=12, choices=[("open","open"),("closed","closed")], default="open")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("broker_account", "symbol")

    def __str__(self) -> str:
        side = "long" if self.qty > 0 else ("short" if self.qty < 0 else "flat")
        return f"{self.symbol} {self.qty} @ {self.avg_price} ({side})"

    @property
    def side(self) -> str:
        """
        Derive a human-readable side for templates/dashboard.
        """
        if self.qty > 0:
            return "long"
        if self.qty < 0:
            return "short"
        return "flat"

    @property
    def unrealized_pnl(self):
        """
        Placeholder for unrealized PnL.

        The current UI expects an `unrealized_pnl` attribute but the
        project does not yet maintain mark-to-market prices required
        to calculate it. Returning zero keeps templates working without
        implying incorrect profit values.

        To implement real PnL, we would need a price feed (e.g. MT5
        ticks) and a periodic mark-to-market job that stores per-position
        or per-symbol PnL snapshots.
        """
        return Decimal("0")

    def save(self, *args, **kwargs):
        # Auto-close flat positions to prevent stale open entries.
        if self.qty == 0:
            self.status = "closed"
            self.avg_price = Decimal("0")
            self.sl = None
            self.tp = None
        else:
            self.status = "open"
        if not self.owner and self.broker_account and self.broker_account.owner_id:
            self.owner_id = self.broker_account.owner_id
        super().save(*args, **kwargs)


class PnLDaily(models.Model):
    broker_account = models.ForeignKey("brokers.BrokerAccount", on_delete=models.CASCADE, related_name="pnl_daily")
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="pnl_daily_owned",
    )
    symbol = models.CharField(max_length=32, default="", blank=True)
    date = models.DateField()
    realized = models.DecimalField(max_digits=20, decimal_places=8, default=0)
    unrealized = models.DecimalField(max_digits=20, decimal_places=8, default=0)
    fees = models.DecimalField(max_digits=20, decimal_places=8, default=0)
    balance = models.DecimalField(max_digits=20, decimal_places=8, default=0)

    class Meta:
        unique_together = ("broker_account", "symbol", "date")

    def save(self, *args, **kwargs):
        if not self.owner and self.broker_account and self.broker_account.owner_id:
            self.owner_id = self.broker_account.owner_id
        super().save(*args, **kwargs)

class TradeLog(models.Model):
    """
    Lightweight append-only log of order outcomes for monitoring/reporting.
    PnL is optional and can be populated when you have realized data.
    """
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="trade_logs")
    bot = models.ForeignKey(Bot, null=True, blank=True, on_delete=models.SET_NULL)
    broker_account = models.ForeignKey(BrokerAccount, null=True, blank=True, on_delete=models.SET_NULL)
    symbol = models.CharField(max_length=32)
    side = models.CharField(max_length=4, choices=[("buy","buy"),("sell","sell")])
    qty = models.DecimalField(max_digits=20, decimal_places=8)
    price = models.DecimalField(max_digits=20, decimal_places=8, null=True, blank=True)
    status = models.CharField(max_length=32, default="new")
    pnl = models.DecimalField(max_digits=20, decimal_places=8, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="trade_logs_owned",
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["symbol", "status", "created_at"]),
        ]

    def save(self, *args, **kwargs):
        if not self.owner:
            if self.order and getattr(self.order, "owner_id", None):
                self.owner_id = self.order.owner_id
            elif self.bot and getattr(self.bot, "owner_id", None):
                self.owner_id = self.bot.owner_id
            elif self.broker_account and getattr(self.broker_account, "owner_id", None):
                self.owner_id = self.broker_account.owner_id
        super().save(*args, **kwargs)

    def __str__(self):
        return f"TradeLog order={self.order_id} {self.symbol} {self.side} {self.qty} {self.status}"


DEFAULT_TRADING_PROFILE_SLUGS = [
    "very_safe",
    "balanced",
    "day_trader",
    "scalper",
    "long_term",
    "custom",
]


def _merge_scalper_dict(base: dict, extra: dict) -> dict:
    """
    Recursive dict merge that preserves nested defaults when overriding profile config.
    """
    merged = base.copy()
    for key, value in (extra or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge_scalper_dict(merged[key], value)
        else:
            merged[key] = value
    return merged


def default_scalper_profile_config() -> dict:
    """
    Reference scalper configuration derived from the product brief.
    Stored in JSON to make admin edits easier while surfacing sane defaults in code/tests.
    """
    return {
        "risk": {
            "default_risk_pct": 0.5,
            "conservative_risk_pct": 0.25,
            "hard_cap_pct": 1.0,
            "soft_dd_pct": 3.0,
            "hard_dd_pct": 5.0,
            "soft_multiplier": 0.5,
            "hard_multiplier": 0.0,
            "max_concurrent_trades": 5,
            "max_trades_per_symbol": 3,
            "max_scale_ins_per_symbol": 2,
            "max_symbol_risk_pct": 1.5,
            "kill_switch_exit_minutes": 10,
        },
        "sessions": [],
        "rollover_blackout": [],
        "news_blackouts": [
            {
                "label": "nfp",
                "lead_minutes": 30,
                "trail_minutes": 30,
                "enabled": True,
                "keywords": ["NFP", "Non-Farm Payrolls", "FOMC", "CPI", "Rate Decision"],
            }
        ],
        "reentry": {
            "min_minutes_after_loss": 5,
            "min_minutes_between_wins": 1,
            "max_trades_per_move": 3,
            "require_pullback_points": 20,
        },
        "time_in_trade_limit_min": 30,
        "default_strategy_profile": "xauusd_standard",
        "strategy_profiles": {
            "xauusd_standard": {
                "name": "XAUUSD Scalper",
                "symbol": "XAUUSD",
                "execution_timeframes": ["M1"],
                "description": "Trend pullback and breakout retest with pin-bar confirmations.",
                "enabled_strategies": ["trend_pullback", "breakout_retest"],
                "internal_triggers": ["price_action_pinbar"],
                "disabled_strategies": ["range_reversion", "momentum_ignition"],
            },
            "xauusd_aggressive": {
                "name": "XAUUSD Scalper – Aggressive",
                "symbol": "XAUUSD",
                "execution_timeframes": ["M1"],
                "description": "Adds momentum ignition for higher trade frequency.",
                "enabled_strategies": ["trend_pullback", "breakout_retest", "momentum_ignition"],
                "internal_triggers": ["price_action_pinbar"],
                "disabled_strategies": ["range_reversion"],
            },
        },
        "score_profiles": {
            "aggressive": {
                "threshold": 0.5,
                "label": "Aggressive (more trades)",
            },
            "default": {
                "threshold": 0.6,
                "label": "Balanced",
            },
            "conservative": {
                "threshold": 0.7,
                "label": "Conservative (selective)",
            },
        },
        "default_score_profile": "default",
        "symbols": {
            "XAUUSD": {
                "aliases": ["XAUUSDm", "GOLDm"],
                "execution_timeframes": ["M1"],
                "context_timeframes": ["M15", "H1"],
                "sl_points": {"min": 50, "max": 150},
                "tp_r_multiple": 1.2,
                "be_trigger_r": 1.0,
                "be_buffer_r": 0.2,
                "trail_trigger_r": 1.5,
                "trail_mode": "swing",
                "max_spread_points": 35,
                "max_slippage_points": 10,
                "allow_countertrend": False,
                "risk_pct": 0.5,
            },
            "EURUSD": {
                "aliases": ["EURUSDm"],
                "execution_timeframes": ["M1", "M5"],
                "context_timeframes": ["M15", "H1"],
                "sl_points": {"min": 5, "max": 8},
                "tp_r_multiple": 1.2,
                "be_trigger_r": 1.0,
                "be_buffer_r": 0.2,
                "trail_trigger_r": 1.5,
                "trail_mode": "ema",
                "max_spread_points": 15,
                "max_slippage_points": 5,
                "allow_countertrend": False,
                "risk_pct": 0.5,
            },
            "GBPUSD": {
                "aliases": ["GBPUSDm"],
                "execution_timeframes": ["M1", "M5"],
                "context_timeframes": ["M15", "H1"],
                "sl_points": {"min": 6, "max": 10},
                "tp_r_multiple": 1.2,
                "be_trigger_r": 1.0,
                "be_buffer_r": 0.2,
                "trail_trigger_r": 1.5,
                "trail_mode": "ema",
                "max_spread_points": 18,
                "max_slippage_points": 6,
                "allow_countertrend": False,
                "risk_pct": 0.5,
            },
        },
        "countertrend": {
            "enabled": False,
            "risk_multiplier": 0.5,
            "max_positions": 1,
            "notes": "Countertrend scalps only from HTF zones with half risk.",
        },
    }


def default_trading_profile_data():
    return [
        {
            "slug": "very_safe",
            "name": "Very Safe (Beginner)",
            "description": "Low risk, few trades, tight drawdowns, long timeframes.",
            "risk_per_trade_pct": Decimal("0.5"),
            "max_trades_per_day": 2,
            "max_concurrent_positions": 1,
            "max_drawdown_pct": Decimal("3.0"),
            "decision_min_score": Decimal("0.65"),
            "signal_quality_threshold": Decimal("0.70"),
            "cooldown_seconds": 600,
            "allowed_days": ["mon", "tue", "wed", "thu", "fri"],
            "trading_start": time(8, 0),
            "trading_end": time(17, 0),
        },
        {
            "slug": "balanced",
            "name": "Balanced",
            "description": "Moderate risk, balanced frequency, default guardrails.",
            "risk_per_trade_pct": Decimal("1.0"),
            "max_trades_per_day": 6,
            "max_concurrent_positions": 2,
            "max_drawdown_pct": Decimal("5.0"),
            "decision_min_score": Decimal("0.6"),
            "signal_quality_threshold": Decimal("0.65"),
            "cooldown_seconds": 300,
            "allowed_days": ["mon", "tue", "wed", "thu", "fri"],
            "trading_start": time(6, 0),
            "trading_end": time(19, 0),
        },
        {
            "slug": "day_trader",
            "name": "Aggressive Day Trader",
            "description": "Higher frequency, larger risk, shorter cooldowns.",
            "risk_per_trade_pct": Decimal("2.0"),
            "max_trades_per_day": 20,
            "max_concurrent_positions": 4,
            "max_drawdown_pct": Decimal("8.0"),
            "decision_min_score": Decimal("0.55"),
            "signal_quality_threshold": Decimal("0.60"),
            "cooldown_seconds": 60,
            "allowed_days": ["mon", "tue", "wed", "thu", "fri"],
            "trading_start": time(6, 0),
            "trading_end": time(20, 0),
        },
        {
            "slug": "scalper",
            "name": "Scalper",
            "description": "Ultra-tight trades, very short windows, and high cadence.",
            "risk_per_trade_pct": Decimal("3.0"),
            "max_trades_per_day": 60,
            "max_concurrent_positions": 6,
            "max_drawdown_pct": Decimal("10.0"),
            "decision_min_score": Decimal("0.45"),
            "signal_quality_threshold": Decimal("0.55"),
            "cooldown_seconds": 15,
            "allowed_days": ["mon", "tue", "wed", "thu", "fri"],
            "trading_start": time(7, 0),
            "trading_end": time(16, 0),
        },
        {
            "slug": "long_term",
            "name": "Long-Term Investor",
            "description": "Rare trades, multi-day focus, wide drawdown cushion.",
            "risk_per_trade_pct": Decimal("0.75"),
            "max_trades_per_day": 1,
            "max_concurrent_positions": 2,
            "max_drawdown_pct": Decimal("7.0"),
            "decision_min_score": Decimal("0.7"),
            "signal_quality_threshold": Decimal("0.75"),
            "cooldown_seconds": 1800,
            "allowed_days": ["mon", "tue", "wed", "thu", "fri"],
            "trading_start": time(8, 0),
            "trading_end": time(20, 0),
        },
        {
            "slug": "custom",
            "name": "Custom / Advanced",
            "description": "No automatic guardrails—user sets every parameter.",
            "risk_per_trade_pct": Decimal("1.5"),
            "max_trades_per_day": 10,
            "max_concurrent_positions": 3,
            "max_drawdown_pct": Decimal("12.0"),
            "decision_min_score": Decimal("0.5"),
            "signal_quality_threshold": Decimal("0.5"),
            "cooldown_seconds": 120,
            "allowed_days": ["mon", "tue", "wed", "thu", "fri"],
            "trading_start": time(0, 0),
            "trading_end": time(23, 59),
        },
    ]


class TradingProfile(models.Model):
    slug = models.CharField(max_length=64, unique=True)
    name = models.CharField(max_length=128)
    description = models.TextField(blank=True, default="")
    risk_per_trade_pct = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("1.0"))
    max_trades_per_day = models.PositiveIntegerField(default=5)
    max_concurrent_positions = models.PositiveIntegerField(default=3)
    max_drawdown_pct = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("5.0"))
    decision_min_score = models.DecimalField(max_digits=6, decimal_places=4, default=Decimal("0.5"))
    signal_quality_threshold = models.DecimalField(max_digits=6, decimal_places=4, default=Decimal("0.6"))
    cooldown_seconds = models.PositiveIntegerField(default=300)
    allowed_days = models.JSONField(default=list, blank=True)
    trading_start = models.TimeField(default=time(6, 0))
    trading_end = models.TimeField(default=time(18, 0))
    is_default = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["slug"]

    def __str__(self):
        return f"{self.name} ({self.slug})"

    @classmethod
    def get_default_profile(cls, slug):
        try:
            return cls.objects.get(slug=slug)
        except cls.DoesNotExist:
            for profile in default_trading_profile_data():
                if profile["slug"] == slug:
                    return profile
            return None

    @classmethod
    def get_profile_choices(cls):
        db_choices = list(cls.objects.values_list("slug", "name"))
        if db_choices:
            return db_choices
        defaults = default_trading_profile_data()
        return [(entry["slug"], entry["name"]) for entry in defaults]


class ScalperProfile(models.Model):
    """
    Configuration bundle for the high-frequency scalper.
    Stored separately from TradingProfile so bots can mix/match classic + scalper behavior.
    """

    slug = models.CharField(max_length=64, unique=True)
    name = models.CharField(max_length=128)
    description = models.TextField(blank=True, default="")
    config = models.JSONField(default=dict, blank=True)
    is_default = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["slug"]

    def __str__(self) -> str:
        return f"{self.name} ({self.slug})"

    def get_config(self) -> dict:
        base = default_scalper_profile_config()
        return _merge_scalper_dict(base, self.config or {})

    @classmethod
    def get_or_create_default(cls, slug: str = "core_scalper"):
        defaults = {
            "name": "Core Scalper",
            "description": "XAUUSD-led scalper tuned for 07:00–16:00 UTC with strict risk envelope.",
            "config": default_scalper_profile_config(),
            "is_default": True,
        }
        obj, _ = cls.objects.get_or_create(slug=slug, defaults=defaults)
        return obj


class ExecutionSetting(models.Model):
    """
    Singleton-style runtime settings that shape trading behavior.
    All fields default to the current code/env defaults so the admin starts pre-filled.
    """

    key = models.CharField(max_length=32, unique=True, default="default")

    # Decision guardrails
    decision_min_score = models.DecimalField(
        max_digits=6,
        decimal_places=4,
        default=Decimal("0.5"),
        help_text="Default minimum score required for opens when a bot has no per-symbol override.",
    )
    decision_flip_score = models.DecimalField(
        max_digits=6,
        decimal_places=4,
        default=Decimal("0.8"),
        help_text="Score threshold to flip an existing position instead of blocking opposite entries.",
    )
    decision_allow_hedging = models.BooleanField(
        default=False,
        help_text="Allow hedged (both-direction) exposure when evaluating new decisions and broker dispatch.",
    )
    decision_flip_cooldown_min = models.PositiveIntegerField(
        default=15,
        help_text="Minutes to wait after a flip before another flip_close can be created for the same bot/symbol.",
    )
    decision_max_flips_per_day = models.PositiveIntegerField(
        default=3,
        help_text="Daily cap on flip_close actions per bot/symbol (0 = no cap).",
    )
    decision_order_cooldown_sec = models.PositiveIntegerField(
        default=60,
        help_text="Minimum seconds between new open orders per bot/symbol (max of this and timeframe-derived gap).",
    )

    # Opposite scalp tuning
    decision_scalp_sl_offset = models.DecimalField(
        max_digits=12,
        decimal_places=6,
        default=Decimal("0.0003"),
        help_text="SL offset (price units) for opposite scalp trades when stacking is allowed.",
    )
    decision_scalp_tp_offset = models.DecimalField(
        max_digits=12,
        decimal_places=6,
        default=Decimal("0.0005"),
        help_text="TP offset (price units) for opposite scalp trades when stacking is allowed.",
    )
    decision_scalp_qty_multiplier = models.DecimalField(
        max_digits=8,
        decimal_places=4,
        default=Decimal("0.3"),
        help_text="Quantity multiplier applied to opposite scalp orders (relative to bot default qty).",
    )

    # Risk/ops tasks
    order_ack_timeout_seconds = models.PositiveIntegerField(
        default=180,
        help_text="Auto-cancel orders stuck in new/ack older than this many seconds.",
    )
    early_exit_max_unrealized_pct = models.DecimalField(
        max_digits=6,
        decimal_places=4,
        default=Decimal("0.02"),
        help_text="Kill positions when unrealized loss exceeds this fraction of notional (monitor task).",
    )
    trailing_trigger = models.DecimalField(
        max_digits=10,
        decimal_places=6,
        default=Decimal("0.0005"),
        help_text="Profit trigger (price units) before trailing stops start.",
    )
    trailing_distance = models.DecimalField(
        max_digits=10,
        decimal_places=6,
        default=Decimal("0.0003"),
        help_text="Distance (price units) to trail behind peak once trigger hit (ATR-adjusted if provided).",
    )

    # Account sizing defaults
    paper_start_balance = models.DecimalField(
        max_digits=20,
        decimal_places=2,
        default=Decimal("100000"),
        help_text="Displayed balance/equity for paper broker accounts.",
    )
    mt5_default_contract_size = models.PositiveIntegerField(
        default=100000,
        help_text="Fallback contract size for notional checks when MT5 symbol_info is unavailable.",
    )

    # Hard caps to prevent runaway exposure
    max_order_lot = models.DecimalField(
        max_digits=8,
        decimal_places=4,
        default=Decimal("0.05"),
        help_text="Maximum lot size per order; set 0 to disable.",
    )
    max_order_notional = models.DecimalField(
        max_digits=20,
        decimal_places=2,
        default=Decimal("5000"),
        help_text="Maximum notional (account currency) per order; set 0 to disable.",
    )
    bot_min_default_qty = models.DecimalField(
        max_digits=20,
        decimal_places=8,
        default=Decimal("0.01"),
        help_text="Global minimum lot size enforced for bot default qty selections.",
    )

    # Psychology / behavior guardrails
    max_loss_streak_before_pause = models.PositiveIntegerField(
        default=0,
        help_text="If >0, automatically pause a bot after this many consecutive losing trades (0 = disabled).",
    )
    loss_streak_cooldown_min = models.PositiveIntegerField(
        default=0,
        help_text="Minutes to keep a bot paused after exceeding the loss streak (0 = no automatic resume).",
    )
    drawdown_soft_limit_pct = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text="Optional soft daily drawdown limit as a percentage of starting balance; size will be reduced when breached (0 = disabled).",
    )
    drawdown_hard_limit_pct = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text="Optional hard daily drawdown limit as a percentage of starting balance; size will be heavily reduced when breached (0 = disabled).",
    )
    soft_size_multiplier = models.DecimalField(
        max_digits=8,
        decimal_places=4,
        default=Decimal("1.0000"),
        help_text="Position size multiplier once the soft drawdown limit is breached (e.g. 0.5 = half size).",
    )
    hard_size_multiplier = models.DecimalField(
        max_digits=8,
        decimal_places=4,
        default=Decimal("1.0000"),
        help_text="Position size multiplier once the hard drawdown limit is breached (e.g. 0.25 = quarter size).",
    )

    class Meta:
        verbose_name = "Execution setting"
        verbose_name_plural = "Execution settings"
        
    
    def get_description(self) -> str:
        """
        Short human-readable description for the settings list view.
        """
        return "Global execution parameters and trading guardrails"

    def get_category(self) -> str:
        """
        Category used by the UI for filtering/badges.
        Right now we treat the singleton as 'decision'.
        """
        return "decision"

    @classmethod
    def defaults_from_settings(cls):
        """
        Build defaults from django settings so env overrides remain the initial values.
        """
        return {
            "key": "default",
            "decision_min_score": getattr(settings, "DECISION_MIN_SCORE", 0.5),
            "decision_flip_score": getattr(settings, "DECISION_FLIP_SCORE", 0.8),
            "decision_allow_hedging": getattr(settings, "DECISION_ALLOW_HEDGING", False),
            "decision_flip_cooldown_min": getattr(settings, "DECISION_FLIP_COOLDOWN_MIN", 15),
            "decision_max_flips_per_day": getattr(settings, "DECISION_MAX_FLIPS_PER_DAY", 3),
            "decision_order_cooldown_sec": getattr(settings, "DECISION_ORDER_COOLDOWN_SEC", 60),
            "decision_scalp_sl_offset": getattr(settings, "DECISION_SCALP_SL_OFFSET", Decimal("0.0003")),
            "decision_scalp_tp_offset": getattr(settings, "DECISION_SCALP_TP_OFFSET", Decimal("0.0005")),
            "decision_scalp_qty_multiplier": getattr(settings, "DECISION_SCALP_QTY_MULTIPLIER", Decimal("0.3")),
            "order_ack_timeout_seconds": getattr(settings, "ORDER_ACK_TIMEOUT_SECONDS", 180),
            "early_exit_max_unrealized_pct": getattr(settings, "EARLY_EXIT_MAX_UNREALIZED_PCT", Decimal("0.02")),
            "trailing_trigger": getattr(settings, "TRAILING_TRIGGER", Decimal("0.0005")),
            "trailing_distance": getattr(settings, "TRAILING_DISTANCE", Decimal("0.0003")),
            "paper_start_balance": getattr(settings, "PAPER_START_BALANCE", Decimal("100000")),
            "mt5_default_contract_size": getattr(settings, "MT5_DEFAULT_CONTRACT_SIZE", 100000),
            "max_order_lot": getattr(settings, "MAX_ORDER_LOT", Decimal("0.05")),
            "max_order_notional": getattr(settings, "MAX_ORDER_NOTIONAL", Decimal("5000")),
            "bot_min_default_qty": getattr(settings, "BOT_MIN_DEFAULT_QTY", Decimal("0.01")),
            "max_loss_streak_before_pause": getattr(settings, "MAX_LOSS_STREAK_BEFORE_PAUSE", 0),
            "loss_streak_cooldown_min": getattr(settings, "LOSS_STREAK_COOLDOWN_MIN", 0),
            "drawdown_soft_limit_pct": getattr(settings, "DRAWDOWN_SOFT_LIMIT_PCT", Decimal("0.00")),
            "drawdown_hard_limit_pct": getattr(settings, "DRAWDOWN_HARD_LIMIT_PCT", Decimal("0.00")),
            "soft_size_multiplier": getattr(settings, "SOFT_SIZE_MULTIPLIER", Decimal("1.0000")),
            "hard_size_multiplier": getattr(settings, "HARD_SIZE_MULTIPLIER", Decimal("1.0000")),
        }

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        # Clear cached runtime config so admin changes take effect immediately.
        try:
            from execution.services.runtime_config import clear_runtime_config_cache
            clear_runtime_config_cache()
        except Exception:
            # Avoid cascading failures during migrations or early startup
            pass
