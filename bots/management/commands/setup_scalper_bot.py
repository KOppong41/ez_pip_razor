from django.core.management.base import BaseCommand, CommandError
from django.contrib.auth import get_user_model
from decimal import Decimal

from bots.models import Bot, Asset, STRATEGY_CHOICES
from brokers.models import BrokerAccount

User = get_user_model()


class Command(BaseCommand):
    help = "Create or update a scalper bot for high-frequency trading on XAUUSDm"

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
            "--strategies",
            type=str,
            default="price_action_pinbar,trend_pullback,doji_breakout,range_reversion",
            help="Comma-separated list of enabled strategies",
        )

    def handle(self, *args, **options):
        symbol = options["symbol"]
        user_id = options["user_id"]
        account_id = options["account_id"]
        auto_trade = options["auto_trade"]
        strategies_str = options["strategies"]

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
            self.stdout.write(self.style.WARNING(f"Using broker account: {broker_account.mt5_login}"))

        # Validate and parse strategies
        strategies = [s.strip() for s in strategies_str.split(",")]
        invalid = [s for s in strategies if s not in STRATEGY_CHOICES]
        if invalid:
            raise CommandError(f"Invalid strategies: {', '.join(invalid)}")

        # Create or update bot
        bot_name = f"{symbol} Scalper M1"
        bot, created = Bot.objects.update_or_create(
            owner=user,
            asset=asset,
            engine_mode="scalper",
            defaults={
                "name": bot_name,
                "status": "active",
                "broker_account": broker_account,
                "default_timeframe": "1m",
                "default_qty": Decimal("0.01") if "XAU" in symbol else Decimal("0.10"),
                "default_tp_pips": Decimal("5"),
                "default_sl_pips": Decimal("3"),
                "auto_trade": auto_trade,
                "enabled_strategies": strategies,
                "decision_min_score": Decimal("0.3"),
                "risk_max_concurrent_positions": 2,
                "risk_max_positions_per_symbol": 1,
                "kill_switch_enabled": True,
                "kill_switch_max_unrealized_pct": Decimal("0.02"),
            },
        )

        if created:
            self.stdout.write(self.style.SUCCESS(f"✓ Created scalper bot: {bot.name} (ID {bot.id})"))
        else:
            self.stdout.write(self.style.SUCCESS(f"✓ Updated scalper bot: {bot.name} (ID {bot.id})"))

        self.stdout.write(self.style.SUCCESS(f"  Symbol: {symbol}"))
        self.stdout.write(self.style.SUCCESS(f"  User: {user.username}"))
        self.stdout.write(self.style.SUCCESS(f"  Account: {broker_account.mt5_login}"))
        self.stdout.write(self.style.SUCCESS(f"  Auto-trade: {auto_trade}"))
        self.stdout.write(self.style.SUCCESS(f"  Strategies: {', '.join(strategies)}"))
        self.stdout.write(self.style.SUCCESS(f"  Engine mode: scalper (M1 high-frequency)"))
        self.stdout.write(self.style.SUCCESS(f""))
        self.stdout.write(
            self.style.WARNING(
                "Next step: Ensure Celery Beat is running to trigger scalper engine every 45 seconds."
            )
        )
