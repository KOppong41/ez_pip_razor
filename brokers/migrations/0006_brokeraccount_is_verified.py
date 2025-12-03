from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("brokers", "0005_brokeraccount_owner"),
    ]

    operations = [
        migrations.AddField(
            model_name="brokeraccount",
            name="is_verified",
            field=models.BooleanField(default=False, help_text="Set to True after credentials are validated/authorized."),
        ),
    ]
