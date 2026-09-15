from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.db import transaction
from django.test import TestCase, override_settings

from execution.connectors.base import ConnectorError
from execution.models import BrokerPosition, Decision, Order, Signal
from execution.services.flip import execute_flip
from execution.services.live_risk import enforce_pretrade_risk
from execution.tests import test_live_risk


@override_settings(MAX_ORDER_LOT=Decimal("10"), DECISION_FLIP_COOLDOWN_MIN=0, DECISION_MAX_FLIPS_PER_DAY=5)
class FlipPreflightTests(TestCase):
    setUp = test_live_risk.LiveRiskTest.setUp
    _bot = test_live_risk.LiveRiskTest._bot
    _order = test_live_risk.LiveRiskTest._order
    _position = test_live_risk.LiveRiskTest._position
    _risk_day = test_live_risk.LiveRiskTest._risk_day

    def scenario(self, *, child=False):
        self._risk_day(Decimal("10000"))
        owner = get_user_model().objects.create_user("flip-owner")
        self.account.owner = owner
        self.account.save()
        bot = self._bot(owner=owner, allow_opposite_scalp=True, trading_schedule_enabled=False)
        primary = self._position(bot, 700)
        positions = [primary]
        if child:
            signal = Signal.objects.create(bot=bot, symbol=self.asset.symbol, source="scalper_engine", direction="sell", timeframe="5m", dedupe_key="child")
            decision = Decision.objects.create(bot=bot, signal=signal, action="open", params={"is_opposite_scalp": True, "primary_position_id": primary.pk})
            child_order = self._order(bot, side="sell", status="filled", decision=decision)
            overlay = self._position(bot, 701)
            overlay.side, overlay.originating_order = "sell", child_order
            overlay.save()
            positions.append(overlay)
        signal = Signal.objects.create(bot=bot, symbol=self.asset.symbol, source="scalper_engine", direction="sell", timeframe="5m", dedupe_key="flip")
        decision = Decision.objects.create(bot=bot, signal=signal, action="open", score=1, params={
            "flip_requested": True, "flip_state": "pending", "flip_position_ids": [p.pk for p in positions],
            "target_rr": "2", "sl": "101", "tp": "98",
        })
        order = self._order(bot, side="sell", sl=Decimal("101"), tp=Decimal("98"), decision=decision)
        self.calls = []
        self.connector.positions_for_account = lambda *args: tuple(
            SimpleNamespace(ticket=p.broker_position_ticket, volume=p.volume, sl=p.sl)
            for p in BrokerPosition.objects.filter(broker_account=self.account, status="open")
        )
        def preflight(replacement, group):
            self.calls.append("preflight")
            try:
                with transaction.atomic():
                    enforce_pretrade_risk(replacement, self.connector, self.tick, self.symbol_info, self.account_info,
                        broker_positions=self.connector.positions_for_account(), replacing_position_ids=[p.pk for p in group])
                    transaction.set_rollback(True)
            finally:
                replacement.refresh_from_db()
        self.connector.preflight_flip = Mock(side_effect=preflight)
        self.conditions = patch("execution.services.brokers.validate_order_conditions", return_value=(True, "ok"))
        self.conditions.start()
        self.addCleanup(self.conditions.stop)
        return order, positions

    def submit(self, order):
        if order.intent == "exit":
            self.calls.append(order.broker_position_ticket)
            BrokerPosition.objects.filter(broker_position_ticket=order.broker_position_ticket).update(status="closed", volume=0)
        else:
            self.calls.append("reverse")
            enforce_pretrade_risk(order, self.connector, self.tick, self.symbol_info, self.account_info,
                                 broker_positions=self.connector.positions_for_account())
        order.status, order.filled_qty = "filled", order.qty
        order.save(update_fields=["status", "filled_qty"])

    def test_preflight_spread_rejection_preserves_entire_group(self):
        order, positions = self.scenario(child=True)
        self.tick.ask = Decimal("101")
        execute_flip(order, self.connector, self.submit)
        self.assertEqual(self.calls, ["preflight"])
        self.assertEqual(BrokerPosition.objects.filter(pk__in=[p.pk for p in positions], status="open").count(), 2)
        self.assertFalse(Order.objects.filter(intent="exit").exists())
        order.decision.refresh_from_db()
        self.assertEqual(order.decision.reason, "flip_preflight_rejected")

    def test_projection_releases_only_group_capacity_then_closes_child_first(self):
        order, _ = self.scenario(child=True)
        self.policy.max_total_open_positions = 1
        self.policy.max_positions_per_symbol = 1
        self.policy.save()
        order.bot.risk_max_concurrent_positions = 1
        order.bot.save()
        execute_flip(order, self.connector, self.submit)
        self.assertEqual(self.calls, ["preflight", 701, 700, "reverse"])
        order.decision.refresh_from_db()
        self.assertEqual(order.decision.params["flip_state"], "completed")
        self.assertEqual(Decision.objects.filter(reason="flip_close").count(), 2)
        execute_flip(order, self.connector, self.submit)
        self.assertEqual(self.calls, ["preflight", 701, 700, "reverse"])

    def test_margin_failure_never_closes_primary(self):
        order, _ = self.scenario()
        self.account_info.margin_free = Decimal("0")
        execute_flip(order, self.connector, self.submit)
        self.assertEqual(self.calls, ["preflight"])
        self.assertFalse(Order.objects.filter(intent="exit").exists())
        order.decision.refresh_from_db()
        self.assertIn("margin", order.decision.params["flip_error"].lower())

    def test_missing_broker_spec_never_closes_primary(self):
        order, _ = self.scenario()
        self.symbol_info.volume_step = 0
        execute_flip(order, self.connector, self.submit)
        self.assertEqual(self.calls, ["preflight"])
        self.assertFalse(Order.objects.filter(intent="exit").exists())

    def test_rejected_close_is_not_recreated_on_retry(self):
        order, _ = self.scenario()
        submit = Mock()
        def reject_close(close_order):
            close_order.status = "rejected"
            close_order.save(update_fields=["status"])
            raise ConnectorError("broker rejected close")
        submit.side_effect = reject_close
        execute_flip(order, self.connector, submit)
        execute_flip(order, self.connector, submit)
        submit.assert_called_once()
        order.decision.refresh_from_db()
        self.assertEqual(order.decision.reason, "flip_group_close_incomplete")
        self.assertEqual(BrokerPosition.objects.filter(status="open").count(), 1)

    def test_final_market_change_records_abort_after_close(self):
        order, _ = self.scenario()
        def move_market(submitted):
            self.submit(submitted)
            if submitted.intent == "exit":
                self.tick.ask = Decimal("101")
        execute_flip(order, self.connector, move_market)
        order.decision.refresh_from_db()
        self.assertEqual(order.decision.reason, "flip_reverse_aborted_after_close")
        self.assertEqual(self.calls, ["preflight", 700, "reverse"])
        order.refresh_from_db()
        self.assertEqual(order.status, "rejected")

    def test_ambiguous_close_keeps_identity_and_reverse_waits(self):
        order, _ = self.scenario()
        seen = []
        def pending_close(submitted):
            seen.append(submitted.pk)
            submitted.status = "ack"
            submitted.save(update_fields=["status"])
            raise ConnectorError("ambiguous")
        with self.assertRaises(ConnectorError):
            execute_flip(order, self.connector, pending_close)
        with self.assertRaises(ConnectorError):
            execute_flip(order, self.connector, pending_close)
        self.assertEqual(len(set(seen)), 1)
        self.assertEqual(Order.objects.filter(intent="exit").count(), 1)
        self.assertEqual(self.calls, ["preflight"])
        execute_flip(order, self.connector, self.submit)
        self.assertEqual(self.calls, ["preflight", 700, "reverse"])

    def test_unrelated_owned_position_blocks_group_without_closing_anything(self):
        order, _ = self.scenario()
        self._position(order.bot, 702)
        execute_flip(order, self.connector, self.submit)
        self.assertEqual(self.calls, [])
        self.assertFalse(Order.objects.filter(intent="exit").exists())

    def test_ambiguous_reverse_is_reconciled_without_repeating_closes(self):
        order, _ = self.scenario()
        def pending_reverse(submitted):
            if submitted.intent == "exit":
                return self.submit(submitted)
            self.calls.append("reverse_pending")
            submitted.status = "ack"
            submitted.save(update_fields=["status"])
            raise ConnectorError("submission result unknown")
        with self.assertRaises(ConnectorError):
            execute_flip(order, self.connector, pending_reverse)
        order.decision.refresh_from_db()
        self.assertEqual(order.decision.params["flip_state"], "reverse_pending")
        # Market guards may now fail, but the prior send must be reconciled.
        with patch("execution.services.brokers.validate_order_conditions", return_value=(False, "market_closed")):
            execute_flip(order, self.connector, self.submit)
        self.assertEqual(self.calls, ["preflight", 700, "reverse_pending", "reverse"])
        self.assertEqual(Order.objects.filter(intent="exit").count(), 1)
        order.decision.refresh_from_db()
        self.assertEqual(order.decision.params["flip_state"], "completed")

    def test_final_risk_does_not_trust_json_exposure_exemptions(self):
        order, positions = self.scenario()
        self.policy.max_positions_per_symbol = 1
        self.policy.save()
        order.decision.params["replacing_position_ids"] = [p.pk for p in positions]
        order.decision.save()
        with self.assertRaisesRegex(ValueError, "Maximum aggregate positions"):
            enforce_pretrade_risk(order, self.connector, self.tick, self.symbol_info, self.account_info,
                                 broker_positions=self.connector.positions_for_account())
