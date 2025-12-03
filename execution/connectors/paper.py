
from decimal import Decimal
from execution.models import Order
from execution.services.orchestrator import update_order_status
from celery import current_app 


class PaperConnector:
    broker_code = "paper"

    def place_order(self, order):
        # Immediately ACK
        update_order_status(order, "ack")
        # Schedule a simulated fill without importing execution.tasks
        current_app.send_task("execution.tasks.simulate_fill_task", args=[order.id])  

    def cancel_order(self, order):
        update_order_status(order, "canceled")