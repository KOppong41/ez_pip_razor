from __future__ import annotations

import logging

from execution.connectors.base import BaseConnector, ConnectorError
from execution.models import Order
from execution.services.orchestrator import update_order_status

logger = logging.getLogger(__name__)


class CTraderConnector(BaseConnector):
    """
    Placeholder connector for the cTrader Open API.
    Implement actual REST/WebSocket calls once credentials + routing are finalized.
    """

    broker_code = "ctrader_api"

    def _mark_unimplemented(self, order: Order, action: str) -> None:
        msg = (
            "cTrader API adapter not configured. "
            "Provide API credentials and implementation before enabling this account."
        )
        update_order_status(order, "error", error_msg=msg)
        logger.error("%s order %s failed: %s", action, order.id, msg)
        raise ConnectorError(msg)

    def place_order(self, order: Order) -> None:
        self._mark_unimplemented(order, "place")

    def cancel_order(self, order: Order) -> None:
        self._mark_unimplemented(order, "cancel")
