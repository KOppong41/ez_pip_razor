from django.core.management.base import BaseCommand, CommandError
from django.contrib.auth import get_user_model
from decimal import Decimal

from bots.models import Bot, Asset, STRATEGY_CHOICES
from brokers.models import BrokerAccount
from execution.models import default_scalper_profile_config

User = get_user_model()


class Command(BaseCommand):
    help = "Create or update a scalper bot for high-frequency trading (default symbol XAUUSDm)"

    def add_arguments(self, parser):
        parser.add_argument(
            "--symbol",
            type=str,
            default="XAUUSDm",
            help="Symbol to trade (default: XAUUSDm)",
        )
        parser.add_argument(
            "--user-id",
            type=int,
            default=None,
            help="User ID (owner). If not provided, uses first active user.",
        )
        parser.add_argument(
            "--account-id",
            type=int,
            default=None,
            help="BrokerAccount ID. If not provided, uses first active MT5 account.",
        )
        parser.add_argument(
            "--auto-trade",
            action="store_true",
            help="Enable auto_trade for live order dispatch",
        )
        parser.add_argument(
            "--profile",
            type=str,
            default=None,
            help="Strategy profile key (default: core_standard; legacy keys still accepted)",
        )
        parser.add_argument(
            "--risk-profile",
            type=str,
            default=None,
            help="Risk preset key (default derived from profile config)",
        )
        parser.add_argument(
            "--psychology-profile",
            type=str,
            default=None,
            help="Psychology profile key (default derived from profile config)",
        )

    def handle(self, *args, **options):
        symbol = options["symbol"]
        user_id = options["user_id"]
        account_id = options["account_id"]
        auto_trade = options["auto_trade"]
        cfg = default_scalper_profile_config()
        profile_key = options["profile"]
        default_profile = cfg.get("default_strategy_profile", "core_standard")
        strategy_profiles = cfg.get("strategy_profiles", {})
        if not profile_key:
            profile_key = default_profile
        profile_cfg = strategy_profiles.get(profile_key)
        if not profile_cfg:
            raise CommandError(f"Unknown strategy profile '{profile_key}'. Available: {', '.join(strategy_profiles.keys())}")
        strategies = profile_cfg.get("enabled_strategies", [])
        if not strategies:
            raise CommandError(f"Strategy profile '{profile_key}' does not define enabled_strategies.")

        risk_profiles = cfg.get("risk_presets", {})
        risk_profile_key = options["risk_profile"] or cfg.get("default_risk_preset")
        risk_profile = risk_profiles.get(risk_profile_key)
        if not risk_profile:
            raise CommandError(
                f"Unknown risk profile '{risk_profile_key}'. Available: {', '.join(risk_profiles.keys())}"
            )

        psychology_profiles = cfg.get("psychology_profiles", {})
        psychology_profile_key = options["psychology_profile"] or cfg.get("default_psychology_profile")
        psychology_profile = psychology_profiles.get(psychology_profile_key)
        if not psychology_profile:
            raise CommandError(
                f"Unknown psychology profile '{psychology_profile_key}'. Available: {', '.join(psychology_profiles.keys())}"
            )

        # Get or create user
        if user_id:
            try:
                user = User.objects.get(id=user_id)
            except User.DoesNotExist:
                raise CommandError(f"User with ID {user_id} not found")
        else:
            user = User.objects.filter(is_active=True).first()
            if not user:
                raise CommandError("No active user found. Specify --user-id or create a user first.")
            self.stdout.write(self.style.WARNING(f"Using user: {user.username} (ID {user.id})"))

        # Get or create asset
        try:
            asset = Asset.objects.get(symbol=symbol)
        except Asset.DoesNotExist:
            raise CommandError(
                f"Asset {symbol} not found. Run 'python manage.py load_assets' first."
            )

        # Get or create broker account
        if account_id:
            try:
                broker_account = BrokerAccount.objects.get(id=account_id)
            except BrokerAccount.DoesNotExist:
                raise CommandError(f"BrokerAccount with ID {account_id} not found")
        else:
            broker_account = BrokerAccount.objects.filter(is_active=True, owner=user).first()
            if not broker_account:
                raise CommandError(
                    f"No active BrokerAccount found for user {user.username}. "
                    "Specify --account-id or create a broker account first."
                )
            acct_label = broker_account.mt5_login or broker_account.account_ref or broker_account.name
            self.stdout.write(self.style.WARNING(f"Using broker account: {acct_label}"))

        # Validate strategies
        invalid = [s for s in strategies if s not in STRATEGY_CHOICES]
        if invalid:
            raise CommandError(f"Invalid strategies in profile '{profile_key}': {', '.join(invalid)}")

        # Create or update bot
        bot_name = f"{symbol} Scalper M1"
        default_qty = Decimal("0.01") if "XAU" in symbol else Decimal("0.10")
        bot_defaults = {
            "name": bot_name,
            "status": "active",
            "broker_account": broker_account,
            "default_timeframe": "1m",
            "default_qty": default_qty,
            "default_tp_pips": Decimal(str(risk_profile.get("tp_pips", 120))),
            "default_sl_pips": Decimal(str(risk_profile.get("sl_pips", 70))),
            "auto_trade": auto_trade,
            "enabled_strategies": strategies,
            # Require higher-quality signals by default; admins can still lower it manually later.
            "decision_min_score": Decimal("0.6"),
            "risk_max_concurrent_positions": 2,
            "kill_switch_enabled": True,
            "kill_switch_max_unrealized_pct": Decimal(str(risk_profile.get("kill_switch_pct", 5.0))),
            # Default to NO autopause unless the psychology profile explicitly enables it.
            "loss_streak_autopause_enabled": bool(psychology_profile.get("autopause", False)),
            "max_loss_streak_before_pause": int(psychology_profile.get("max_loss_streak", 0)),
            "loss_streak_cooldown_min": int(psychology_profile.get("cooldown_min", 0)),
            "soft_drawdown_limit_pct": Decimal(str(psychology_profile.get("soft_dd_pct", 3.0))),
            "hard_drawdown_limit_pct": Decimal(str(psychology_profile.get("hard_dd_pct", 5.0))),
            "soft_size_multiplier": Decimal(str(psychology_profile.get("soft_multiplier", 0.5))),
            "hard_size_multiplier": Decimal(str(psychology_profile.get("hard_multiplier", 0.25))),
        }

        bot, created = Bot.objects.update_or_create(
            owner=user,
            asset=asset,
            engine_mode="scalper",
            defaults=bot_defaults,
        )

        scalper_params = bot.scalper_params or {}
        scalper_params["strategy_profile"] = profile_key
        scalper_params["risk_profile"] = risk_profile_key
        scalper_params["psychology_profile"] = psychology_profile_key
        scalper_params.setdefault("score_profile", "default")
        bot.scalper_params = scalper_params
        bot.enabled_strategies = strategies
        bot.save(update_fields=["scalper_params", "enabled_strategies"])

        if created:
            self.stdout.write(self.style.SUCCESS(f"✓ Created scalper bot: {bot.name} (ID {bot.id})"))
        else:
            self.stdout.write(self.style.SUCCESS(f"✓ Updated scalper bot: {bot.name} (ID {bot.id})"))

        self.stdout.write(self.style.SUCCESS(f"  Symbol: {symbol}"))
        self.stdout.write(self.style.SUCCESS(f"  User: {user.username}"))
        acct_label = broker_account.mt5_login or broker_account.account_ref or broker_account.name
        self.stdout.write(self.style.SUCCESS(f"  Account: {acct_label}"))
        self.stdout.write(self.style.SUCCESS(f"  Auto-trade: {auto_trade}"))
        self.stdout.write(self.style.SUCCESS(f"  Strategies: {', '.join(strategies)}"))
        self.stdout.write(self.style.SUCCESS(f"  Strategy profile: {profile_key}"))
        self.stdout.write(self.style.SUCCESS(f"  Risk profile: {risk_profile_key}"))
        self.stdout.write(self.style.SUCCESS(f"  Psychology profile: {psychology_profile_key}"))
        self.stdout.write(self.style.SUCCESS(f"  Engine mode: scalper (M1 high-frequency)"))
        self.stdout.write(self.style.SUCCESS(f""))
        self.stdout.write(
            self.style.WARNING(
                "Next step: Ensure Celery Beat is running to trigger scalper engine every 45 seconds."
            )
        )
