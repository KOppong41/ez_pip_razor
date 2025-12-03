from django.core.management.base import BaseCommand
from execution.services.reconcile import reconcile_orders_and_positions


class Command(BaseCommand):
    help = "Reconcile filled orders into executions/positions; defaults to dry-run."

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Apply fixes (create missing executions).",
        )

    def handle(self, *args, **options):
        apply = options.get("apply", False)
        result = reconcile_orders_and_positions(apply=apply)
        prefix = "[APPLY]" if apply else "[DRY-RUN]"
        self.stdout.write(f"{prefix} reconciled: {result}")
