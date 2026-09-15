
from decimal import Decimal
from execution.models import Order
from execution.services.orchestrator import update_order_status
from celery import current_app 
from threading import RLock

_flip_lock = RLock()


class PaperConnector:
    broker_code = "paper"

    def place_order(self, order):
        from execution.services.flip import execute_flip, is_flip_order
        if is_flip_order(order):
            with _flip_lock:
                return execute_flip(order, self, self._submit_flip)
        # Immediately ACK
        update_order_status(order, "ack")
        # Schedule a simulated fill without importing execution.tasks
        current_app.send_task("execution.tasks.simulate_fill_task", args=[order.id])  

    def preflight_flip(self, order, positions):
        from execution.services.entry_contract import target_at_entry
        from execution.services.protection_policy import validate_order_protection
        valid, reason = validate_order_protection(order)
        if not valid:
            raise ValueError(reason)
        price = Decimal("1.1000") if order.side == "buy" else Decimal("1.1005")
        if order.qty <= 0 or order.sl is None:
            raise ValueError("invalid_paper_replacement")
        target_at_entry(order.side, price, order.sl, order.decision.params.get("target_rr", "1"))

    def _submit_flip(self, order):
        from execution.tasks import simulate_fill_task
        if order.intent == "entry":
            self.preflight_flip(order, ())
        simulate_fill_task.run(order.pk)

    def positions_for_account(self, account):
        from types import SimpleNamespace
        from execution.models import Position
        return [SimpleNamespace(ticket=position.pk) for position in Position.objects.filter(broker_account=account, status="open")]

    def cancel_order(self, order):
        update_order_status(order, "canceled")
