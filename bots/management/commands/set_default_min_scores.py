from decimal import Decimal

from django.core.management.base import BaseCommand
from bots.models import Bot, BotConfig


DEFAULT_MIN_SCORES = {
    "XAUUSDm": Decimal("0.70"),
    "EURUSDm": Decimal("0.60"),
    "USDJPYm": Decimal("0.50"),
}


class Command(BaseCommand):
    help = "Set per-bot decision_min_score using defaults per asset symbol."

    def handle(self, *args, **options):
        updated = 0
        for bot in Bot.objects.select_related("asset"):
            sym = bot.asset.symbol if bot.asset else None
            if not sym:
                continue
            target = DEFAULT_MIN_SCORES.get(sym)
            if target is None:
                continue
            cfg, _ = BotConfig.objects.get_or_create(bot=bot, key="decision_min_score", defaults={"value": {sym: str(target)}})
            if cfg.value == target or cfg.value == {sym: str(target)}:
                continue
            # overwrite with the target for this bot's asset
            cfg.value = {sym: str(target)}
            cfg.save(update_fields=["value"])
            updated += 1
        self.stdout.write(self.style.SUCCESS(f"Updated decision_min_score for {updated} bot(s)."))
