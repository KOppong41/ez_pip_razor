
from execution.models import Order

class ConnectorError(Exception):
    pass

class BaseConnector:
    broker_code: str  # e.g. "paper", "exness_mt5", "binance"

    def place_order(self, order: Order) -> None:
        """Submit order to venue; must transition order to 'ack' or raise."""
        raise NotImplementedError

    def cancel_order(self, order: Order) -> None:
        """Cancel live order at venue."""
        raise NotImplementedError
