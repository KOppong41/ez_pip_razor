from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from bots.models import Asset, Bot
from brokers.models import BrokerAccount
from execution.models import BrokerPosition, Order, TradeLog
from execution.services.portfolio import record_closed_position_outcome, record_fill


class PositionOutcomeAccountingTests(TestCase):
    def setUp(self):
        owner = get_user_model().objects.create_user("outcome-owner")
        account = BrokerAccount.objects.create(
            owner=owner, name="Outcome MT5", broker="mt5", connector="mt5_local",
            account_ref="outcome-mt5",
        )
        asset = Asset.objects.create(symbol="OUTCOMEUSD", category="forex")
        bot = Bot.objects.create(
            owner=owner, name="Outcome bot", asset=asset, broker_account=account,
            status="active", loss_streak_autopause_enabled=True,
            max_loss_streak_before_pause=2, loss_streak_cooldown_min=60,
        )
        self.owner, self.account, self.bot = owner, account, bot

    def position(self, ticket, *, entry_commission="0"):
        entry = Order.objects.create(
            owner=self.owner, bot=self.bot, broker_account=self.account,
            client_order_id=f"outcome-entry-{ticket}", symbol="OUTCOMEUSD",
            side="buy", intent="entry", qty=Decimal(".3"), status="filled",
            broker_position_ticket=ticket,
        )
        record_fill(entry, Decimal(".3"), Decimal("100"), broker_deal_ticket=ticket * 10,
                    broker_position_ticket=ticket, broker_profit=Decimal(0),
                    commission=Decimal(entry_commission))
        return BrokerPosition.objects.create(
            broker_account=self.account, bot=self.bot, originating_order=entry,
            broker_position_ticket=ticket, ownership="ez_trade", symbol="OUTCOMEUSD",
            side="buy", volume=Decimal(".3"), open_price=Decimal("100"), status="open",
        )

    def exit(self, ticket, suffix, qty, profit, *, order=None):
        if order is None:
            order = Order.objects.create(
                owner=self.owner, bot=self.bot, broker_account=self.account,
                client_order_id=f"outcome-exit-{ticket}-{suffix}", symbol="OUTCOMEUSD",
                side="sell", intent="exit", qty=Decimal(str(qty)), status="filled",
                broker_position_ticket=ticket,
            )
        record_fill(order, Decimal(str(qty)), Decimal("101"),
                    broker_deal_ticket=ticket * 100 + suffix,
                    broker_position_ticket=ticket, broker_profit=Decimal(str(profit)))
        return order

    def close(self, position):
        position.status = "closed"
        position.volume = Decimal(0)
        position.closed_at = timezone.now()
        position.save(update_fields=["status", "volume", "closed_at"])
        return record_closed_position_outcome(position.pk)

    def test_three_losing_partial_exits_are_one_loss_and_retries_do_not_recount(self):
        position = self.position(1101)
        for i in range(3):
            self.exit(1101, i + 1, ".1", "-.25")
            self.bot.refresh_from_db()
            self.assertEqual(self.bot.current_loss_streak, 0)
        self.assertTrue(self.close(position))
        self.bot.refresh_from_db()
        self.assertEqual((self.bot.current_loss_streak, self.bot.status), (1, "active"))
        self.assertFalse(record_closed_position_outcome(position.pk))
        self.bot.refresh_from_db()
        self.assertEqual(self.bot.current_loss_streak, 1)

        second = self.position(1102)
        self.exit(1102, 1, ".3", "-1")
        self.close(second)
        self.bot.refresh_from_db()
        self.assertEqual((self.bot.current_loss_streak, self.bot.status, self.bot.pause_reason),
                         (2, "paused", "loss_cooldown"))

    def test_cumulative_trade_status_uses_total_not_last_fill(self):
        position = self.position(1201)
        order = self.exit(1201, 1, ".1", "20")
        self.exit(1201, 2, ".2", "-10", order=order)
        log = TradeLog.objects.get(order=order)
        self.assertEqual((log.pnl, log.status), (Decimal("10"), "win"))
        self.close(position)
        self.bot.refresh_from_db()
        self.assertEqual(self.bot.current_loss_streak, 0)

    def test_entry_side_costs_determine_full_position_outcome(self):
        position = self.position(1301, entry_commission="-2")
        self.exit(1301, 1, ".3", "1")
        self.close(position)
        self.bot.refresh_from_db()
        self.assertEqual(self.bot.current_loss_streak, 1)

    def test_incomplete_exit_chain_cannot_advance_loss_streak(self):
        position = self.position(1401)
        self.exit(1401, 1, ".1", "-1")
        self.assertFalse(self.close(position))
        self.bot.refresh_from_db()
        self.assertEqual(self.bot.current_loss_streak, 0)
        position.refresh_from_db()
        self.assertIsNone(position.loss_streak_accounted_at)
