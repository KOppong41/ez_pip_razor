
from django.db import migrations


def prune_celery_and_audit(apps, schema_editor):
    JournalEntry = apps.get_model('execution', 'JournalEntry')
    JournalEntry.objects.filter(event_type__startswith='celery.').delete()
    JournalEntry.objects.filter(event_type__startswith='audit.').delete()


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('execution', '0041_tradelog_closed_at_tradelog_exit_price'),
    ]

    operations = [
        migrations.RunPython(prune_celery_and_audit, reverse_code=noop),
    ]
