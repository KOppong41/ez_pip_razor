from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from core.models import Audit, CeleryActivity


class Command(BaseCommand):
    help = "Prune old operational logs (Audit, CeleryActivity, NotificationLog)."

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
            choices=["audit", "celery", "notifications"],
            default=["audit", "celery", "notifications"],
            help="Which tables to prune (default: all).",
        )

    def handle(self, *args, **options):
        days = options["days"]
        include = set(options["include"])
        cutoff = timezone.now() - timedelta(days=days)

        total_deleted = 0

        if "audit" in include:
            deleted, _ = Audit.objects.filter(ts__lt=cutoff).delete()
            total_deleted += deleted
            self.stdout.write(f"Pruned {deleted} Audit rows older than {days} days")

        if "celery" in include:
            deleted, _ = CeleryActivity.objects.filter(ts__lt=cutoff).delete()
            total_deleted += deleted
            self.stdout.write(f"Pruned {deleted} CeleryActivity rows older than {days} days")

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

        self.stdout.write(self.style.SUCCESS(f"Done. Total deleted: {total_deleted}"))
