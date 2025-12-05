from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

class Command(BaseCommand):
    help = "Prune journal and notification logs."

    def add_arguments(self, parser):
        parser.add_argument(
            "--days",
            type=int,
            default=30,
            help="Delete records older than this many days (default: 30).",
        )
        parser.add_argument(
            "--include",
            nargs="+",
            choices=["notifications", "journal"],
            default=["journal", "notifications"],
            help="Which tables to prune (default: journal + notifications).",
        )

    def handle(self, *args, **options):
        days = options["days"]
        include = set(options["include"])
        cutoff = timezone.now() - timedelta(days=days)

        total_deleted = 0

        if "notifications" in include:
            try:
                from notifications.models import NotificationLog
            except Exception:
                NotificationLog = None
            if NotificationLog:
                deleted, _ = NotificationLog.objects.filter(created_at__lt=cutoff).delete()
                total_deleted += deleted
                self.stdout.write(f"Pruned {deleted} NotificationLog rows older than {days} days")
            else:
                self.stdout.write("Skipped notifications (model import failed)")

        if "journal" in include:
            try:
                from execution.models import JournalEntry
            except Exception:
                JournalEntry = None
            if JournalEntry:
                deleted, _ = JournalEntry.objects.filter(created_at__lt=cutoff).delete()
                total_deleted += deleted
                self.stdout.write(f"Pruned {deleted} JournalEntry rows older than {days} days")
            else:
                self.stdout.write("Skipped journal (model import failed)")

        self.stdout.write(self.style.SUCCESS(f"Done. Total deleted: {total_deleted}"))
