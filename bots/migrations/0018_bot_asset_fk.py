from django.db import migrations, models
from decimal import Decimal


def assign_assets(apps, schema_editor):
    Bot = apps.get_model("bots", "Bot")
    Asset = apps.get_model("bots", "Asset")
    for bot in Bot.objects.all():
        if getattr(bot, "asset_id", None):
            continue
        sym = None
        try:
            symbols = bot.allowed_symbols or []
            sym = symbols[0] if symbols else None
        except Exception:
            sym = None
        if not sym:
            continue
        asset, _ = Asset.objects.get_or_create(
            symbol=sym,
            defaults={
                "display_name": sym,
                "min_qty": Decimal("0.01"),
                "recommended_qty": Decimal("0.10"),
            },
        )
        bot.asset = asset
        bot.save(update_fields=["asset"])


class Migration(migrations.Migration):

    dependencies = [
        ("bots", "0017_asset"),
    ]

    operations = [
        migrations.AddField(
            model_name="bot",
            name="asset",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=models.SET_NULL,
                related_name="bots",
                to="bots.asset",
                help_text="The primary symbol this bot trades. Required in non-test environments.",
            ),
        ),
        migrations.RunPython(assign_assets, reverse_code=migrations.RunPython.noop),
    ]
