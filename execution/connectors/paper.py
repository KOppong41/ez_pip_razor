
from decimal import Decimal
from execution.models import Order, RiskPolicy
from execution.services.orchestrator import update_order_status
from celery import current_app 
from threading import RLock

_flip_lock = RLock()


class PaperConnector:
    broker_code = "paper"

    def _check_entry_operating_limits(self, order):
        """Apply entry stops that do not require broker market data."""
        from django.utils import timezone
        from execution.services.decision import get_last_bot_trade, get_today_filled_trades

        order.bot.refresh_from_db()
        bot = order.bot
        policy = RiskPolicy.objects.filter(broker_account=order.broker_account).first()
        if policy and policy.emergency_stop:
            raise ValueError("ACCOUNT_EMERGENCY_STOP_ACTIVE")
        if bot.kill_switch_triggered_at:
            raise ValueError("BOT_KILL_SWITCH_ACTIVE")
        if bot.max_trades_per_day > 0 and get_today_filled_trades(bot, order.symbol) >= bot.max_trades_per_day:
            raise ValueError("BOT_DAILY_TRADE_LIMIT")
        if bot.trade_interval_minutes > 0:
            last_entry = get_last_bot_trade(bot)
            if last_entry and last_entry.submitted_at > timezone.now() - timezone.timedelta(minutes=bot.trade_interval_minutes):
                raise ValueError("BOT_MIN_TRADE_INTERVAL")

    def place_order(self, order):
        from execution.services.flip import execute_flip, is_flip_order
        if is_flip_order(order):
            with _flip_lock:
                return execute_flip(order, self, self._submit_flip)
        if order.intent == "entry":
            try:
                self._check_entry_operating_limits(order)
            except ValueError as exc:
                update_order_status(order, "rejected", error_msg=str(exc))
                raise
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
        self._check_entry_operating_limits(order)
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
