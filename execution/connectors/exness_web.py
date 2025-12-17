from __future__ import annotations

import logging

from execution.connectors.base import BaseConnector, ConnectorError
from execution.models import Order
from execution.services.orchestrator import update_order_status

logger = logging.getLogger(__name__)


class ExnessWebConnector(BaseConnector):
    """
    Placeholder connector for the Exness Web Terminal API.
    Once the HTTPS endpoints are finalized, implement place/cancel here.
    """

    broker_code = "exness_web"

    def _mark_unimplemented(self, order: Order, action: str) -> None:
        msg = (
            "Exness Web Terminal adapter not configured. "
            "Provide API credentials and implementation before enabling this account."
        )
        update_order_status(order, "error", error_msg=msg)
        logger.error("%s order %s failed: %s", action, order.id, msg)
        raise ConnectorError(msg)

    def place_order(self, order: Order) -> None:
        self._mark_unimplemented(order, "place")

    def cancel_order(self, order: Order) -> None:
        self._mark_unimplemented(order, "cancel")
