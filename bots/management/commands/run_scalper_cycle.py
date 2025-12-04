from django.core.management.base import BaseCommand, CommandError

from bots.models import Bot
from execution.tasks import trade_scalper_strategies_for_bot


class Command(BaseCommand):
    help = "Run the scalper strategies synchronously for debugging and testing."

    def add_arguments(self, parser):
        parser.add_argument(
            "--bot-id",
            type=int,
            help="Optional bot ID to target. Defaults to all active scalper bots.",
        )
        parser.add_argument(
            "--timeframe",
            default="1m",
            help="Timeframe to request from the market data connector (default: 1m).",
        )
        parser.add_argument(
            "--bars",
            type=int,
            default=150,
            help="Number of candles to fetch for each bot (default: 150).",
        )

    def handle(self, *args, **options):
        bot_id = options.get("bot_id")
        timeframe = options.get("timeframe") or "1m"
        n_bars = options.get("bars") or 150

        bots = Bot.objects.filter(engine_mode="scalper", status="active")
        if bot_id:
            bots = bots.filter(id=bot_id)
            if not bots.exists():
                raise CommandError(f"No active scalper bot found with id={bot_id}")

        if not bots.exists():
            self.stdout.write(self.style.WARNING("No active scalper bots detected."))
            return

        self.stdout.write(
            f"Running scalper cycle for {bots.count()} bot(s) "
            f"(tf={timeframe}, bars={n_bars})..."
        )

        summary = []
        total_signals = 0
        total_orders = 0

        for bot in bots:
            self.stdout.write(f"-> Bot {bot.id} / {bot.name}")
            result = trade_scalper_strategies_for_bot.apply(
                args=(bot.id,),
                kwargs={"timeframe": timeframe, "n_bars": n_bars},
            ).get()

            signals = result.get("signals", 0)
            orders = result.get("orders", 0)
            total_signals += signals
            total_orders += orders

            summary.append(
                f"bot={bot.id} signals={signals} decisions={result.get('decisions', 0)} orders={orders}"
            )
            self.stdout.write(
                self.style.SUCCESS(
                    f"   Signals={signals} Decisions={result.get('decisions', 0)} Orders={orders}"
                )
            )

        self.stdout.write("Run complete.")
        for line in summary:
            self.stdout.write(f"   {line}")
        self.stdout.write(
            self.style.SUCCESS(
                f"Totals -> signals={total_signals} orders={total_orders}"
            )
        )
