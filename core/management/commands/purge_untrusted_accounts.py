from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from django.db import transaction

from bots.models import Bot


class Command(BaseCommand):
    help = (
        "List (and optionally delete) non-staff, non-superuser accounts with no login "
        "along with their bots. Use --apply to actually delete."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Actually delete the matched users and their bots. Default is dry-run.",
        )
        parser.add_argument(
            "--usernames",
            nargs="+",
            help="Limit to specific usernames. Default is all matching candidates.",
        )

    def handle(self, *args, **options):
        User = get_user_model()
        qs = User.objects.filter(is_staff=False, is_superuser=False, last_login__isnull=True)
        if options.get("usernames"):
            qs = qs.filter(username__in=options["usernames"])

        candidates = list(qs)
        if not candidates:
            self.stdout.write(self.style.SUCCESS("No candidate users found."))
            return

        bot_count = Bot.objects.filter(owner__in=candidates).count()
        self.stdout.write(
            f"Found {len(candidates)} candidate users and {bot_count} bots owned by them."
        )
        for u in candidates:
            self.stdout.write(f" - {u.username}")

        if not options.get("apply"):
            self.stdout.write(self.style.WARNING("Dry-run complete. Pass --apply to delete."))
            return

        with transaction.atomic():
            Bot.objects.filter(owner__in=candidates).delete()
            qs.delete()

        self.stdout.write(self.style.SUCCESS("Deleted candidates and their bots."))
