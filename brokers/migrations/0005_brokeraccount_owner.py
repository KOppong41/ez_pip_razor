from django.db import migrations, models
from django.conf import settings


class Migration(migrations.Migration):

    dependencies = [
        ("brokers", "0004_remove_brokeraccount_creds"),
    ]

    operations = [
        migrations.AddField(
            model_name="brokeraccount",
            name="owner",
            field=models.ForeignKey(
                on_delete=models.CASCADE,
                related_name="broker_accounts",
                blank=True,
                null=True,
                to=settings.AUTH_USER_MODEL,
            ),
        ),
    ]
