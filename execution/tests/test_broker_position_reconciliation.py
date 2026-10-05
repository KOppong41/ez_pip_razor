from datetime import datetime, timezone as dt_timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import TestCase
from django.contrib.auth import get_user_model

from bots.models import Asset, Bot
from brokers.models import BrokerAccount
from execution.models import BrokerPosition, Execution, Order, Position, TradeLog
from execution.services.orchestrator import create_close_order_for_position
from execution.services.portfolio import record_fill
from execution.connectors.mt5 import ConnectorError, MT5Connector
from execution.tasks import _reconcile_missing_owned_position


class BrokerPositionReconciliationTests(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_user("reconcile-owner", password="pw")
        account = BrokerAccount.objects.create(
            owner=user,
            name="MT5 demo",
            broker="mt5",
            connector="mt5_local",
            account_ref="reconcile-demo",
            is_verified=True,
        )
        asset, _ = Asset.objects.get_or_create(symbol="XAUUSDm")
        bot = Bot.objects.create(
            owner=user,
            name="Gold bot",
            status="active",
            auto_trade=True,
            broker_account=account,
            asset=asset,
        )
        entry = Order.objects.create(
            owner=user,
            bot=bot,
            broker_account=account,
            client_order_id="entry-reconcile-test",
            symbol="XAUUSDm",
            side="buy",
            qty=Decimal("0.01"),
            filled_qty=Decimal("0.01"),
            remaining_qty=Decimal("0"),
            intent="entry",
            status="filled",
            broker_position_ticket=333,
        )
        broker_position = BrokerPosition.objects.create(
            broker_account=account,
            bot=bot,
            originating_order=entry,
            broker_position_ticket=333,
            ownership="ez_trade",
            symbol="XAUUSDm",
            side="buy",
            volume=Decimal("0.01"),
            open_price=Decimal("4617.206"),
            status="open",
        )
        connector = Mock()
        connector.history_deals_for_position_account.return_value = (
            self.deal(443, 0, ".01", price="4617.206", time=0.5),
            SimpleNamespace(
                ticket=444,
                order=445,
                position_id=333,
                entry=1,
                volume=0.01,
                price=4615.326,
                profit=-1.88,
                commission=0,
                swap=0,
                time=1,
                time_msc=1000,
            ),
        )
        self.account = account
        self.entry = entry
        self.position = broker_position
        self.connector = connector

    @staticmethod
    def deal(ticket, entry, volume, *, price="100", profit="0", commission="0", swap="0", time=1, time_msc=0):
        return SimpleNamespace(
            ticket=ticket, order=ticket + 100, position_id=333, entry=entry,
            volume=Decimal(volume), price=Decimal(price), profit=Decimal(profit),
            commission=Decimal(commission), swap=Decimal(swap), time=time, time_msc=time_msc,
        )

    def partial_history(self):
        self.entry.qty = self.entry.filled_qty = Decimal(".1")
        self.entry.save(update_fields=["qty", "filled_qty"])
        return [
            self.deal(443, 0, ".1", profit=".1", commission="-.20", swap="-.01"),
            self.deal(444, 1, ".04", price="101", profit="1.2", commission="-.03", swap="-.05", time=2, time_msc=2000),
            self.deal(445, 1, ".06", price="102", profit="2.3", commission="-.04", swap="-.06", time=3),
        ]

    def reconcile(self, history=None):
        if history is not None:
            self.connector.history_deals_for_position_account.return_value = history
        result = _reconcile_missing_owned_position(self.connector, self.position)
        self.connector.place_order.assert_not_called()
        self.position.refresh_from_db()
        return result

    def assert_final_state(self):
        self.assertEqual(self.position.status, "closed")
        self.assertEqual(self.position.volume, Decimal(0))
        self.assertEqual(self.position.current_price, Decimal("102"))
        self.assertEqual(self.position.profit, Decimal("3.6"))
        self.assertEqual(self.position.commission, Decimal("-.27"))
        self.assertEqual(self.position.swap, Decimal("-.12"))
        self.assertEqual(self.position.closed_at, datetime.fromtimestamp(3, dt_timezone.utc))

    def test_imports_broker_stop_exit_and_flattens_local_position(self):
        imported = self.reconcile()

        self.assertEqual(imported, [444])
        self.assertEqual(self.position.status, "closed")
        self.assertEqual(self.position.volume, Decimal("0"))
        self.assertFalse(Position.objects.filter(broker_account=self.account).exists())
        close_order = Order.objects.get(intent="exit", broker_position_ticket=333)
        self.assertEqual(close_order.status, "filled")
        self.assertEqual(close_order.broker_deal_ticket, 444)
        self.assertTrue(
            Execution.objects.filter(
                order=close_order,
                broker_deal_ticket=444,
                profit=Decimal("-1.88"),
            ).exists()
        )

    def test_mt5_history_failure_is_not_treated_as_empty_history(self):
        connector = MT5Connector()
        mt5 = SimpleNamespace(
            history_deals_get=Mock(return_value=None),
            last_error=Mock(return_value=(500, "history unavailable")),
        )
        with patch("execution.connectors.mt5.mt5", mt5), patch.object(
            connector, "_call_for_account", side_effect=lambda account, action: action(),
        ):
            with self.assertRaisesRegex(ConnectorError, "history unavailable"):
                connector.history_deals_for_position_account(self.account, 333)
            mt5.history_deals_get.return_value = ()
            self.assertEqual(connector.history_deals_for_position_account(self.account, 333), ())

    def test_multiple_partial_exits_never_overfill_order_sized_from_stale_snapshot(self):
        history = self.partial_history()

        def checked_fill(order, qty, *args, **kwargs):
            order.refresh_from_db()
            filled = sum(order.executions.values_list("qty", flat=True), Decimal(0))
            self.assertLessEqual(filled + qty, order.qty)
            return record_fill(order, qty, *args, **kwargs)

        with patch("execution.tasks.record_fill", side_effect=checked_fill):
            self.assertEqual(self.reconcile(history), [444, 445])
        order = Order.objects.get(intent="exit")
        self.assertEqual(order.qty, Decimal(".1"))
        self.assertEqual(order.filled_qty, order.qty)
        self.assertEqual(order.remaining_qty, Decimal(0))
        self.assertEqual(order.status, "filled")
        self.assertEqual(list(order.executions.order_by("exec_time").values_list("qty", flat=True)),
                         [Decimal(".04"), Decimal(".06")])
        self.assert_final_state()

    def test_zero_cached_volume_does_not_prevent_authoritative_history_import(self):
        BrokerPosition.objects.filter(pk=self.position.pk).update(volume=0)
        self.assertEqual(self.reconcile(self.partial_history()), [444, 445])
        self.assert_final_state()

    def test_mixed_timestamps_and_unsorted_history_select_true_latest_exit(self):
        history = self.partial_history()
        # The later seconds-only exit must beat the earlier millisecond exit,
        # regardless of input order or a contradictory fallback seconds field.
        history[1].time = 9999
        self.assertEqual(self.reconcile([history[2], history[0], history[1]]), [444, 445])
        self.assert_final_state()
        order = Order.objects.get(intent="exit")
        self.assertEqual(order.broker_deal_ticket, 445)
        self.assertEqual(order.price, Decimal(102))

    def test_repeat_repairs_all_final_fields_without_duplicate_fills_or_pnl(self):
        history = self.partial_history()
        self.reconcile(history)
        execution_ids = list(Execution.objects.values_list("id", flat=True))
        pnl = TradeLog.objects.get(order__intent="exit").pnl
        for status in ("missing", "closed"):
            with self.subTest(status=status):
                BrokerPosition.objects.filter(pk=self.position.pk).update(
                    status=status, volume=".06", current_price="99", profit="999",
                    commission="99", swap="99", closed_at=datetime.fromtimestamp(1, dt_timezone.utc),
                )
                self.assertEqual(self.reconcile(history), [])
                self.assert_final_state()
                self.assertEqual(list(Execution.objects.values_list("id", flat=True)), execution_ids)
                self.assertEqual(Order.objects.filter(intent="exit").count(), 1)
                self.assertEqual(TradeLog.objects.get(order__intent="exit").pnl, pnl)
                self.assertEqual(self.position.broker_metadata["reconciled_close"]["source"], "mt5_position_history")

    def test_delayed_partial_history_preserves_exposure_then_imports_only_new_exit(self):
        history = self.partial_history()
        self.assertEqual(self.reconcile(history[:2]), [444])
        self.assertEqual(self.position.status, "missing")
        self.assertEqual(self.position.volume, Decimal(".06"))
        self.assertIsNone(self.position.closed_at)
        order = Order.objects.get(intent="exit")
        self.assertEqual(order.qty, Decimal(".04"))
        self.assertEqual(self.reconcile(history[:2]), [])
        self.assertEqual(self.position.volume, Decimal(".06"))
        self.assertEqual(self.reconcile(history), [445])
        order.refresh_from_db()
        self.assertEqual((order.qty, order.filled_qty, order.remaining_qty),
                         (Decimal(".1"), Decimal(".1"), Decimal(0)))
        self.assertEqual(order.executions.count(), 2)
        self.assertEqual(TradeLog.objects.get(order=order).qty, Decimal(".1"))
        self.assert_final_state()

    def test_exit_only_history_imports_without_clearing_or_reducing_uncertain_exposure(self):
        history = self.partial_history()
        self.assertEqual(self.reconcile(history[1:]), [444, 445])
        self.assertEqual(self.position.status, "missing")
        self.assertEqual(self.position.volume, Decimal(".01"))
        self.assertIsNone(self.position.closed_at)
        self.assertEqual(self.reconcile(history[1:]), [])
        self.assertEqual(self.position.status, "missing")
        self.assertEqual(self.position.volume, Decimal(".01"))
        self.assertEqual(self.reconcile(history), [])
        self.assert_final_state()
        self.assertEqual(Execution.objects.count(), 2)

    def test_empty_entry_only_and_reversal_history_remain_missing(self):
        history = self.partial_history()
        reversal = self.deal(446, 2, ".01")
        for deals in ([], history[:1], history + [reversal]):
            with self.subTest(deals=deals):
                self.assertEqual(self.reconcile(deals), [])
                self.assertEqual(self.position.status, "missing")
                self.assertEqual(self.position.volume, Decimal(".01"))
                self.assertIsNone(self.position.closed_at)
                self.assertFalse(Execution.objects.exists())
                self.assertFalse(Order.objects.filter(intent="exit").exists())

    def test_existing_live_close_sizing_and_fills_are_preserved(self):
        history = self.partial_history()
        self.position.volume = Decimal(".1")
        self.position.save(update_fields=["volume"])
        live_order, _ = create_close_order_for_position(self.position, self.account, close_qty=Decimal(".04"))
        record_fill(live_order, Decimal(".04"), Decimal(101), broker_deal_ticket=444,
                    broker_position_ticket=333, broker_profit=Decimal("1.2"), update_bot_state=False)
        live_order.status = "filled"
        live_order.filled_qty = Decimal(".04")
        live_order.remaining_qty = 0
        live_order.save()
        self.position.volume = Decimal(".06")
        self.position.save(update_fields=["volume"])
        with self.assertRaises(ValueError):
            create_close_order_for_position(self.position, self.account, close_qty=Decimal(".1"))

        self.assertEqual(self.reconcile(history), [445])
        live_order.refresh_from_db()
        self.assertEqual(live_order.qty, Decimal(".04"))
        self.assertEqual(live_order.executions.count(), 1)
        historical_order = Order.objects.exclude(pk__in=[self.entry.pk, live_order.pk]).get()
        self.assertEqual(historical_order.qty, Decimal(".06"))
        self.assertEqual(historical_order.executions.count(), 1)
        self.assertEqual(self.reconcile(history), [])
        self.assertEqual(Execution.objects.count(), 2)
        self.assert_final_state()

    def test_duplicate_deals_are_counted_once_and_conflicting_deals_do_not_close(self):
        history = self.partial_history()
        conflicting = self.deal(445, 1, ".05", time=3)
        self.assertEqual(self.reconcile(history + [conflicting]), [])
        self.assertEqual(self.position.status, "missing")
        self.assertFalse(Execution.objects.exists())
        self.assertEqual(self.reconcile(history + history), [444, 445])
        self.assert_final_state()
        self.assertEqual(Execution.objects.count(), 2)

    def test_failed_import_rolls_back_fills_and_can_be_retried(self):
        history = self.partial_history()

        def fail_second_fill(order, qty, *args, **kwargs):
            if kwargs["broker_deal_ticket"] == 445:
                raise RuntimeError("Interrupted history import")
            return record_fill(order, qty, *args, **kwargs)

        with patch("execution.tasks.record_fill", side_effect=fail_second_fill):
            with self.assertRaises(RuntimeError):
                self.reconcile(history)
        self.assertFalse(Execution.objects.exists())
        self.assertFalse(Order.objects.filter(intent="exit").exists())
        self.assertEqual(self.reconcile(history), [444, 445])
        self.assert_final_state()
