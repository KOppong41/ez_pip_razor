from datetime import timedelta
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from execution.models import Order
from execution.services.orchestrator import update_order_status


class Command(BaseCommand):
    help = "Review old orders and optionally cancel only those never queued or submitted."

    def add_arguments(self, parser):
        parser.add_argument(
            "--minutes",
            type=int,
            default=5,
            help="Age threshold in minutes (default 5).",
        )
        parser.add_argument(
            "--apply", action="store_true",
            help="Cancel safe, never-submitted orders. Default is a dry run.",
        )

    def handle(self, *args, **options):
        minutes = options["minutes"]
        if minutes <= 0:
            raise CommandError("--minutes must be positive")
        cutoff = timezone.now() - timedelta(minutes=minutes)
        overdue = Order.objects.filter(status__in=["new", "ack"], created_at__lt=cutoff)
        safe = overdue.filter(
            status="new",
            submitted_at__isnull=True,
            execution_queued_at__isnull=True,
            mt5_worker_started_at__isnull=True,
            risk_reserved_at__isnull=True,
            order_send_called_at__isnull=True,
            broker_ticket__isnull=True,
            broker_order_ticket__isnull=True,
            broker_deal_ticket__isnull=True,
            broker_position_ticket__isnull=True,
            attempts__isnull=True,
            executions__isnull=True,
        )
        candidate_ids = list(safe.values_list("pk", flat=True))
        if not options["apply"]:
            self.stdout.write(
                f"Dry run: {len(candidate_ids)} never-submitted order(s) eligible; "
                f"{overdue.count() - len(candidate_ids)} submitted or uncertain order(s) left unchanged. "
                "Use --apply to cancel eligible orders."
            )
            return
        canceled = 0
        for order_id in candidate_ids:
            with transaction.atomic():
                order = safe.select_for_update(of=("self",)).filter(pk=order_id).first()
                if order is None:
                    continue
                update_order_status(
                    order, "canceled", error_msg="Canceled: never queued or submitted"
                )
                canceled += 1
        self.stdout.write(self.style.SUCCESS(f"Canceled {canceled} never-submitted order(s)."))
