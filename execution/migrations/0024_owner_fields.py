from django.db import migrations, models
from django.conf import settings


def populate_owner_forward(apps, schema_editor):
    UserModel = settings.AUTH_USER_MODEL.split(".")
    user_app, user_model = UserModel[0], UserModel[1]
    User = apps.get_model(user_app, user_model)  # noqa: F841 unused but keeps dependency

    Signal = apps.get_model("execution", "Signal")
    Decision = apps.get_model("execution", "Decision")
    Order = apps.get_model("execution", "Order")
    Execution = apps.get_model("execution", "Execution")
    Position = apps.get_model("execution", "Position")
    PnLDaily = apps.get_model("execution", "PnLDaily")
    TradeLog = apps.get_model("execution", "TradeLog")

    for obj in Signal.objects.select_related("bot").filter(owner__isnull=True):
        if obj.bot_id and getattr(obj.bot, "owner_id", None):
            obj.owner_id = obj.bot.owner_id
            obj.save(update_fields=["owner"])

    for obj in Decision.objects.select_related("bot", "signal__owner").filter(owner__isnull=True):
        if obj.bot_id and getattr(obj.bot, "owner_id", None):
            obj.owner_id = obj.bot.owner_id
        elif obj.signal_id and getattr(obj.signal, "owner_id", None):
            obj.owner_id = obj.signal.owner_id
        if obj.owner_id:
            obj.save(update_fields=["owner"])

    for obj in Order.objects.select_related("bot", "broker_account").filter(owner__isnull=True):
        if obj.bot_id and getattr(obj.bot, "owner_id", None):
            obj.owner_id = obj.bot.owner_id
        elif obj.broker_account_id and getattr(obj.broker_account, "owner_id", None):
            obj.owner_id = obj.broker_account.owner_id
        if obj.owner_id:
            obj.save(update_fields=["owner"])

    for obj in Execution.objects.select_related("order__bot", "order__owner").filter(owner__isnull=True):
        if obj.order_id:
            if getattr(obj.order, "owner_id", None):
                obj.owner_id = obj.order.owner_id
            elif getattr(obj.order, "bot", None) and getattr(obj.order.bot, "owner_id", None):
                obj.owner_id = obj.order.bot.owner_id
        if obj.owner_id:
            obj.save(update_fields=["owner"])

    for obj in Position.objects.select_related("broker_account").filter(owner__isnull=True):
        if obj.broker_account_id and getattr(obj.broker_account, "owner_id", None):
            obj.owner_id = obj.broker_account.owner_id
            obj.save(update_fields=["owner"])

    for obj in PnLDaily.objects.select_related("broker_account").filter(owner__isnull=True):
        if obj.broker_account_id and getattr(obj.broker_account, "owner_id", None):
            obj.owner_id = obj.broker_account.owner_id
            obj.save(update_fields=["owner"])

    for obj in TradeLog.objects.select_related("order__owner", "bot").filter(owner__isnull=True):
        if obj.bot_id and getattr(obj.bot, "owner_id", None):
            obj.owner_id = obj.bot.owner_id
        elif obj.order_id and getattr(obj.order, "owner_id", None):
            obj.owner_id = obj.order.owner_id
        if obj.owner_id:
            obj.save(update_fields=["owner"])


def populate_owner_backward(apps, schema_editor):
    Signal = apps.get_model("execution", "Signal")
    Decision = apps.get_model("execution", "Decision")
    Order = apps.get_model("execution", "Order")
    Execution = apps.get_model("execution", "Execution")
    Position = apps.get_model("execution", "Position")
    PnLDaily = apps.get_model("execution", "PnLDaily")
    TradeLog = apps.get_model("execution", "TradeLog")

    for model in (Signal, Decision, Order, Execution, Position, PnLDaily, TradeLog):
        model.objects.update(owner=None)


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("execution", "0023_remove_decision_decision_bot_decided_idx_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="decision",
            name="owner",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=models.SET_NULL,
                related_name="decisions_owned",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="execution",
            name="owner",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=models.SET_NULL,
                related_name="executions_owned",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="order",
            name="owner",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=models.SET_NULL,
                related_name="orders_owned",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="pnldaily",
            name="owner",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=models.SET_NULL,
                related_name="pnl_daily_owned",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="position",
            name="owner",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=models.SET_NULL,
                related_name="positions_owned",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="signal",
            name="owner",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=models.SET_NULL,
                related_name="signals_owned",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="tradelog",
            name="owner",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=models.SET_NULL,
                related_name="trade_logs_owned",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.RunPython(populate_owner_forward, populate_owner_backward),
    ]
