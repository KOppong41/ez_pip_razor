from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("bots", "0055_btc_entry_quality")]
    operations = [migrations.AddField(model_name="bot", name="kill_switch_triggered_at",
                                     field=models.DateTimeField(null=True, blank=True, editable=False))]
