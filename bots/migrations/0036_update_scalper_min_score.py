from decimal import Decimal

from django.db import migrations


def set_scalper_min_score(apps, schema_editor):
    Bot = apps.get_model("bots", "Bot")
    Bot.objects.filter(
        engine_mode="scalper", decision_min_score__lt=Decimal("0.05")
    ).update(decision_min_score=Decimal("0.60"))


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("bots", "0035_bot_scalper_params_bot_scalper_profile_and_more"),
    ]

    operations = [
        migrations.RunPython(set_scalper_min_score, reverse_code=noop),
    ]
