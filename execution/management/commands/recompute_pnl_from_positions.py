from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone
from decimal import Decimal

from execution.models import Position, TradeLog, Order


class Command(BaseCommand):
    help = "Recompute realized PnL per closed position using executions, and update TradeLog entries accurately."

    def add_arguments(self, parser):
        parser.add_argument(
            "--days",
            type=int,
            default=7,
            help="Lookback window (in days) for closed positions to recompute PnL. Default: 7",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        days = options["days"]
        cutoff = timezone.now() - timezone.timedelta(days=days)

        positions = (
            Position.objects.filter(status="closed", updated_at__gte=cutoff)
            .select_related("broker_account", "owner")
            .prefetch_related("executions__order")
        )

        updated = 0
        created = 0
        skipped = 0

        for pos in positions:
            execs = list(pos.executions.order_by("exec_time", "id"))
            if not execs:
                skipped += 1
                continue

            # Net direction is determined by executions; for closed positions net ends at 0.
            # Compute realized PnL by summing each execution against running position state.
            running_qty = Decimal("0")
            avg_price = Decimal("0")
            realized_pnl = Decimal("0")

            for exe in execs:
                qty = Decimal(str(exe.qty))
                price = Decimal(str(exe.price))
                order = exe.order
                side = getattr(order, "side", "buy")
                delta = qty if side == "buy" else -qty

                # If adding same direction, update avg; if reducing, realize PnL on the reduced leg.
                if running_qty == 0 or (running_qty > 0 and delta > 0) or (running_qty < 0 and delta < 0):
                    # same direction add
                    new_qty = running_qty + delta
                    if running_qty != 0:
                        avg_price = (avg_price * abs(running_qty) + price * abs(delta)) / abs(new_qty)
                    else:
                        avg_price = price
                    running_qty = new_qty
                else:
                    # reducing/closing
                    closing_qty = min(abs(running_qty), abs(delta))
                    direction = Decimal("1") if running_qty > 0 else Decimal("-1")
                    realized_pnl += (price - avg_price) * closing_qty * direction
                    running_qty += delta
                    # If we crossed zero, reset avg to current price for any remaining leg
                    if running_qty == 0:
                        avg_price = Decimal("0")
                    elif (running_qty > 0 and delta < 0) or (running_qty < 0 and delta > 0):
                        avg_price = price

            # Update/attach TradeLog per order with accurate PnL
            for exe in execs:
                order = exe.order
                try:
                    tl = TradeLog.objects.filter(order=order).latest("created_at")
                except TradeLog.DoesNotExist:
                    tl = TradeLog.objects.create(
                        order=order,
                        bot=order.bot,
                        owner=getattr(order, "owner", None),
                        broker_account=order.broker_account,
                        symbol=order.symbol,
                        side=order.side,
                        qty=order.qty,
                        price=order.price,
                        status="filled",
                        pnl=None,
                    )
                    created += 1
                # For positions that end flat, attribute realized PnL to the last execution's order.
                if exe == execs[-1]:
                    tl.pnl = realized_pnl
                    if realized_pnl > 0:
                        tl.status = "win"
                    elif realized_pnl < 0:
                        tl.status = "loss"
                    else:
                        tl.status = "breakeven"
                    tl.save(update_fields=["pnl", "status"])
                    updated += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Recomputed PnL for {positions.count()} positions (updated={updated}, created_logs={created}, skipped={skipped})"
            )
        )
