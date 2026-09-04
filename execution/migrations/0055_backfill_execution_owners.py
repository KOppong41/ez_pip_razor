from django.db import migrations


def backfill_execution_owners(apps, schema_editor):
    Signal = apps.get_model("execution", "Signal")
    Decision = apps.get_model("execution", "Decision")
    Order = apps.get_model("execution", "Order")
    BrokerPosition = apps.get_model("execution", "BrokerPosition")

    for signal in Signal.objects.filter(owner__isnull=True).select_related("bot"):
        if signal.bot_id and signal.bot.owner_id:
            Signal.objects.filter(pk=signal.pk, owner__isnull=True).update(
                owner_id=signal.bot.owner_id
            )

    for decision in Decision.objects.filter(owner__isnull=True).select_related("bot", "signal"):
        owner_id = (
            decision.bot.owner_id
            if decision.bot_id and decision.bot.owner_id
            else decision.signal.owner_id
        )
        if owner_id:
            Decision.objects.filter(pk=decision.pk, owner__isnull=True).update(owner_id=owner_id)

    for order in Order.objects.filter(owner__isnull=True).select_related("bot", "broker_account"):
        bot_owner_id = order.bot.owner_id if order.bot_id else None
        account_owner_id = order.broker_account.owner_id if order.broker_account_id else None
        if bot_owner_id and bot_owner_id == account_owner_id:
            Order.objects.filter(pk=order.pk, owner__isnull=True).update(owner_id=bot_owner_id)

    for position in BrokerPosition.objects.filter(owner__isnull=True).select_related("broker_account"):
        if position.broker_account.owner_id:
            BrokerPosition.objects.filter(pk=position.pk, owner__isnull=True).update(
                owner_id=position.broker_account.owner_id
            )


class Migration(migrations.Migration):
    dependencies = [("execution", "0054_normalize_forex_execution_units")]
    operations = [
        migrations.RunPython(backfill_execution_owners, migrations.RunPython.noop),
    ]
