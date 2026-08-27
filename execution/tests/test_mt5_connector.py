from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import TestCase
from django.utils import timezone

from bots.models import Bot
from brokers.models import BrokerAccount
from execution.connectors.base import ConnectorError
from execution.connectors.mt5 import MT5Connector
from execution.models import BrokerPosition, Decision, ExecutionAttempt, Signal
from execution.services.live_risk import PreTradeRiskResult
from execution.services.orchestrator import create_order_from_decision


class MT5ConnectorTest(TestCase):
    def setUp(self):
        self.account = BrokerAccount.objects.create(
            name="MT5 demo",
            broker="mt5",
            connector="mt5_local",
            account_ref="mt5-test",
            is_verified=True,
        )
        self.bot = Bot.objects.create(
            name="MT5 bot",
            status="active",
            auto_trade=True,
            broker_account=self.account,
        )
        signal = Signal.objects.create(
            bot=self.bot,
            source="engine_v1",
            symbol="EURUSD",
            timeframe="5m",
            direction="buy",
            payload={},
            dedupe_key="mt5-order",
        )
        decision = Decision.objects.create(
            bot=self.bot,
            signal=signal,
            action="open",
            reason="test",
            score=1,
            params={"sl": "1.0900", "tp": "1.1200"},
        )
        self.order, _ = create_order_from_decision(decision, self.account, "0.04")

    def _risk_result(self):
        return PreTradeRiskResult(
            volume=Decimal("0.04"),
            entry_price=Decimal("1.1002"),
            margin_required=Decimal("100"),
            risk_amount=Decimal("20"),
            loss_per_lot=Decimal("500"),
            spread_points=Decimal("2"),
            spread_limit_points=Decimal("15"),
            deviation_points=17,
        )

    def _configure_api(self, api):
        api.TRADE_RETCODE_DONE = 10009
        api.TRADE_RETCODE_DONE_PARTIAL = 10010
        api.TRADE_RETCODE_PLACED = 10008
        api.ORDER_TYPE_BUY = 0
        api.ORDER_TYPE_SELL = 1
        api.TRADE_ACTION_DEAL = 1
        api.TRADE_ACTION_REMOVE = 8
        api.ORDER_TIME_GTC = 0
        api.ORDER_FILLING_FOK = 0
        api.positions_get.return_value = ()
        api.symbol_info_tick.return_value = SimpleNamespace(bid=1.1000, ask=1.1002)
        api.symbol_info.return_value = SimpleNamespace(
            point=0.0001,
            digits=5,
            trade_contract_size=100000,
            filling_mode=0,
            trade_stops_level=0,
            stops_level=0,
        )
        api.account_info.return_value = SimpleNamespace(balance=10000)
        api.order_check.return_value = SimpleNamespace(retcode=0, comment="ok")
        api.history_deals_get.return_value = ()
        api.last_error.return_value = (0, "ok")

    @patch("execution.connectors.mt5.mt5")
    @patch("execution.services.live_risk.enforce_pretrade_risk")
    def test_done_records_attempt_and_fill(self, risk, api):
        self._configure_api(api)
        risk.return_value = self._risk_result()
        api.order_send.return_value = SimpleNamespace(
            retcode=10009,
            price=1.1002,
            volume=0.04,
            order=111,
            deal=222,
            position=333,
            comment="done",
        )
        connector = MT5Connector()
        with patch.object(connector, "_login_from_order"), patch.object(connector, "_ensure_symbol"), patch(
            "execution.connectors.mt5._check_ready"
        ):
            connector.place_order(self.order)

        self.order.refresh_from_db()
        self.assertEqual(self.order.status, "filled")
        self.assertEqual(self.order.broker_order_ticket, 111)
        self.assertEqual(self.order.broker_deal_ticket, 222)
        self.assertEqual(self.order.broker_position_ticket, 333)
        self.assertEqual(ExecutionAttempt.objects.get(order=self.order).status, "accepted")
        self.assertEqual(api.order_send.call_args.args[0]["deviation"], 17)

    @patch("execution.connectors.mt5.mt5")
    @patch("execution.services.live_risk.enforce_pretrade_risk")
    def test_done_recovers_position_ticket_from_deal(self, risk, api):
        self._configure_api(api)
        risk.return_value = self._risk_result()
        api.order_send.return_value = SimpleNamespace(
            retcode=10009,
            price=1.1002,
            volume=0.04,
            order=111,
            deal=222,
            comment="done",
        )
        api.history_deals_get.return_value = (
            SimpleNamespace(
                ticket=222,
                order=111,
                position_id=333,
                profit=0,
                commission=0,
                swap=0,
            ),
        )
        raw_position = SimpleNamespace(
            ticket=333,
            identifier=333,
            type=0,
            symbol="EURUSD",
            volume=0.04,
            price_open=1.1002,
            price_current=1.1002,
            sl=1.09,
            tp=1.12,
            profit=0,
            swap=0,
            magic=20250813,
            comment="ez:test",
            time=0,
        )
        api.positions_get.side_effect = lambda **kwargs: (
            (raw_position,) if kwargs.get("ticket") == 333 else ()
        )

        connector = MT5Connector()
        with patch.object(connector, "_login_from_order"), patch.object(connector, "_ensure_symbol"), patch(
            "execution.connectors.mt5._check_ready"
        ):
            connector.place_order(self.order)

        self.order.refresh_from_db()
        self.assertEqual(self.order.broker_position_ticket, 333)
        self.assertEqual(
            ExecutionAttempt.objects.get(order=self.order).broker_position_ticket,
            333,
        )
        self.assertTrue(
            BrokerPosition.objects.filter(
                originating_order=self.order,
                broker_position_ticket=333,
                ownership="ez_trade",
                status="open",
            ).exists()
        )

    @patch("execution.connectors.mt5.mt5")
    @patch("execution.services.live_risk.enforce_pretrade_risk")
    def test_ambiguous_submission_is_never_resent(self, risk, api):
        self._configure_api(api)
        risk.return_value = self._risk_result()
        api.order_send.return_value = None
        connector = MT5Connector()
        with patch.object(connector, "_login_from_order"), patch.object(connector, "_ensure_symbol"), patch(
            "execution.connectors.mt5._check_ready"
        ):
            with self.assertRaises(ConnectorError):
                connector.place_order(self.order)
            with patch.object(connector, "reconcile_order", return_value=False):
                with self.assertRaisesRegex(ConnectorError, "automatic resend blocked"):
                    connector.place_order(self.order)

        self.assertEqual(api.order_send.call_count, 1)
        self.assertEqual(ExecutionAttempt.objects.get(order=self.order).status, "ambiguous")

    @patch("execution.connectors.mt5.mt5")
    @patch("execution.services.live_risk.enforce_pretrade_risk")
    def test_partial_fill_without_reported_volume_requires_reconciliation(self, risk, api):
        self._configure_api(api)
        risk.return_value = self._risk_result()
        api.order_send.return_value = SimpleNamespace(
            retcode=10010,
            price=1.1002,
            volume=0,
            order=111,
            deal=222,
            position=333,
            comment="partial",
        )

        connector = MT5Connector()
        with patch.object(connector, "_login_from_order"), patch.object(
            connector, "_ensure_symbol"
        ), patch("execution.connectors.mt5._check_ready"):
            with self.assertRaisesRegex(ConnectorError, "partial fill without filled volume"):
                connector.place_order(self.order)

        self.order.refresh_from_db()
        self.assertEqual(self.order.status, "ack")
        self.assertEqual(self.order.filled_qty, Decimal("0"))
        self.assertEqual(self.order.executions.count(), 0)
        self.assertEqual(
            ExecutionAttempt.objects.get(order=self.order).status,
            "ambiguous",
        )

    @patch("execution.connectors.mt5.mt5")
    def test_acknowledged_order_is_canceled_only_after_broker_confirmation(self, api):
        self._configure_api(api)
        self.order.status = "ack"
        self.order.submitted_at = timezone.now()
        self.order.broker_order_ticket = 555
        self.order.save(
            update_fields=["status", "submitted_at", "broker_order_ticket"]
        )
        api.orders_get.side_effect = [
            (SimpleNamespace(ticket=555),),
            (),
        ]
        api.order_send.return_value = SimpleNamespace(
            retcode=10009,
            comment="removed",
            order=555,
            deal=0,
            position=0,
        )

        connector = MT5Connector()
        with patch.object(connector, "_login_from_order"):
            connector.cancel_order(self.order)

        self.order.refresh_from_db()
        self.assertEqual(self.order.status, "canceled")
        self.assertEqual(api.order_send.call_args.args[0]["action"], 8)
        self.assertEqual(api.order_send.call_args.args[0]["order"], 555)

    @patch("execution.connectors.mt5.mt5")
    def test_unverified_acknowledged_order_is_not_locally_canceled(self, api):
        self._configure_api(api)
        self.order.status = "ack"
        self.order.submitted_at = timezone.now()
        self.order.save(update_fields=["status", "submitted_at"])
        connector = MT5Connector()

        with patch.object(connector, "_login_from_order"), patch.object(
            connector, "reconcile_order", return_value=False
        ):
            with self.assertRaisesRegex(ConnectorError, "cancellation is unverified"):
                connector.cancel_order(self.order)

        self.order.refresh_from_db()
        self.assertEqual(self.order.status, "ack")

    @patch("execution.connectors.mt5.mt5")
    def test_never_submitted_order_can_be_canceled_locally(self, api):
        self._configure_api(api)
        connector = MT5Connector()
        with patch.object(connector, "_login_from_order") as login:
            connector.cancel_order(self.order)

        self.order.refresh_from_db()
        self.assertEqual(self.order.status, "canceled")
        login.assert_not_called()
