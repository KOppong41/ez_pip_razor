import os
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Event
from types import SimpleNamespace
from unittest import skipUnless

from django.db import close_old_connections, connection, transaction
from django.test import SimpleTestCase, TransactionTestCase, override_settings

from execution.models import Order
from execution.services.live_risk import RiskRejected, enforce_pretrade_risk
from execution.tests import test_live_risk


class PostgresCIGuard(SimpleTestCase):
    def test_required_postgres_job_cannot_silently_use_sqlite(self):
        if os.environ.get("REQUIRE_POSTGRES_TESTS", "").lower() == "true":
            self.assertEqual(connection.vendor, "postgresql")


@skipUnless(connection.vendor == "postgresql", "Requires real PostgreSQL row locks")
@override_settings(MAX_ORDER_LOT=Decimal("10"))
class PostgresAdmissionConcurrencyTests(TransactionTestCase):
    setUp = test_live_risk.LiveRiskTest.setUp
    _bot = test_live_risk.LiveRiskTest._bot
    _order = test_live_risk.LiveRiskTest._order
    _risk_day = test_live_risk.LiveRiskTest._risk_day

    def concurrent_admission(self, *, position_limit, lot_limit, expected_code):
        self._risk_day(Decimal(10000))
        self.policy.max_total_open_positions = position_limit
        self.policy.max_aggregate_open_lots = Decimal(lot_limit)
        self.policy.save()
        orders = [self._order(self._bot(suffix=str(i), position_sizing_mode="fixed", default_qty=Decimal(".60"),
                                       risk_max_concurrent_positions=1), qty=Decimal(".60")) for i in range(2)]
        holding, release, second_started, second_finished = Event(), Event(), Event(), Event()

        def admit(index):
            close_old_connections()
            connector = SimpleNamespace(**vars(self.connector))
            if index == 0:
                def margin(*args):
                    holding.set()
                    if not release.wait(10):
                        raise RuntimeError("Test admission lock was not released")
                    return Decimal(10)
                connector.calc_margin_for_account = margin
            else:
                second_started.set()
            try:
                with transaction.atomic():
                    with connection.cursor() as cursor:
                        cursor.execute("SET LOCAL lock_timeout = '8s'")
                    order = Order.objects.get(pk=orders[index].pk)
                    enforce_pretrade_risk(order, connector, self.tick, self.symbol_info, self.account_info, broker_positions=())
                return "admitted"
            except RiskRejected as exc:
                return exc.code
            finally:
                if index == 1:
                    second_finished.set()
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(admit, 0)
            try:
                self.assertTrue(holding.wait(10), "First transaction did not reach admission")
                second = pool.submit(admit, 1)
                self.assertTrue(second_started.wait(3))
                self.assertFalse(second_finished.wait(.25), "Competing admission did not wait on the account lock")
            finally:
                release.set()
            self.assertEqual(first.result(timeout=12), "admitted")
            self.assertEqual(second.result(timeout=12), expected_code)
        self.assertEqual(Order.objects.filter(risk_reserved_at__isnull=False).count(), 1)

    def test_different_bots_cannot_race_past_aggregate_lot_limit(self):
        self.concurrent_admission(position_limit=10, lot_limit="1", expected_code="ACCOUNT_MAX_AGGREGATE_LOTS")

    def test_different_bots_cannot_race_past_position_limit(self):
        self.concurrent_admission(position_limit=1, lot_limit="10", expected_code="ACCOUNT_MAX_POSITIONS")
