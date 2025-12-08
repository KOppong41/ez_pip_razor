from decimal import Decimal

from django.db import migrations


NEW_MIN = Decimal("0.01")
OLD_MIN = Decimal("0.000005")


def bump_btc_min_qty(apps, schema_editor):
    Asset = apps.get_model("bots", "Asset")
    Bot = apps.get_model("bots", "Bot")

    asset = Asset.objects.filter(symbol="BTCUSDm").first()
    if asset:
        updates = []
        if asset.min_qty != NEW_MIN and asset.min_qty < NEW_MIN:
            asset.min_qty = NEW_MIN
            updates.append("min_qty")
        if asset.recommended_qty != NEW_MIN and asset.recommended_qty < NEW_MIN:
            asset.recommended_qty = NEW_MIN
            updates.append("recommended_qty")
        if updates:
            asset.save(update_fields=updates)

    bots = Bot.objects.filter(asset__symbol="BTCUSDm", default_qty__lt=NEW_MIN)
    for bot in bots:
        bot.default_qty = NEW_MIN
        bot.save(update_fields=["default_qty"])


def revert_btc_min_qty(apps, schema_editor):
    Asset = apps.get_model("bots", "Asset")
    Bot = apps.get_model("bots", "Bot")

    asset = Asset.objects.filter(symbol="BTCUSDm").first()
    if asset:
        asset.min_qty = OLD_MIN
        asset.recommended_qty = OLD_MIN
        asset.save(update_fields=["min_qty", "recommended_qty"])

    bots = Bot.objects.filter(
        asset__symbol="BTCUSDm",
        default_qty__gt=OLD_MIN,
        default_qty__lte=NEW_MIN,
    )
    for bot in bots:
        bot.default_qty = OLD_MIN
        bot.save(update_fields=["default_qty"])


class Migration(migrations.Migration):

    dependencies = [
        ("bots", "0037_alter_bot_decision_min_score"),
    ]

    operations = [
        migrations.RunPython(bump_btc_min_qty, reverse_code=revert_btc_min_qty),
    ]
