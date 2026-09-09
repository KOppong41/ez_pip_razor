from decimal import Decimal

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class RiskScopeMigrationTest(TransactionTestCase):
    """Legacy account controls are conservatively seeded onto attached bots."""

    migrate_from = [("bots", "0045_merge_20251217_0520"), ("execution", "0056_historicalbacktest")]
    migrate_to = [("bots", "0046_bot_execution_risk_controls"), ("execution", "0058_remove_bot_controls_from_risk_policy")]

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        executor = MigrationExecutor(connection)
        cls.latest_targets = executor.loader.graph.leaf_nodes()

    def setUp(self):
        super().setUp()
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_from)
        old_apps = executor.loader.project_state(self.migrate_from).apps

        BrokerAccount = old_apps.get_model("brokers", "BrokerAccount")
        Asset = old_apps.get_model("bots", "Asset")
        Bot = old_apps.get_model("bots", "Bot")
        RiskPolicy = old_apps.get_model("execution", "RiskPolicy")

        account = BrokerAccount.objects.create(
            name="Migrated MT5",
            broker="mt5",
            connector="mt5_local",
            account_ref="risk-migration-test",
        )
        asset = Asset.objects.create(symbol="MIGRISKUSD", is_active=True)
        self.bot_id = Bot.objects.create(
            name="Legacy defaults",
            broker_account=account,
            asset=asset,
            max_trades_per_day=10,
            risk_max_concurrent_positions=8,
        ).pk
        self.stricter_bot_id = Bot.objects.create(
            name="Legacy stricter bot",
            broker_account=account,
            asset=asset,
            max_trades_per_day=1,
            risk_max_concurrent_positions=2,
        ).pk
        self.account_id = account.pk
        RiskPolicy.objects.create(
            broker_account=account,
            risk_per_trade_pct=Decimal("0.750"),
            max_daily_loss_pct=Decimal("2.000"),
            max_account_drawdown_pct=Decimal("6.000"),
            max_positions=4,
            max_positions_per_symbol=2,
            max_entry_trades_per_day=3,
            max_lot=Decimal("0.40"),
            max_spread_points=Decimal("17"),
            deviation_points=6,
            stop_after_daily_profit_pct=Decimal("1.250"),
            live_trading_confirmed=True,
            emergency_close_owned_positions=True,
        )

        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_to)
        self.apps = executor.loader.project_state(self.migrate_to).apps

    def tearDown(self):
        # Other test classes import the current ORM, so always restore the full
        # project schema even when an assertion in this migration test fails.
        executor = MigrationExecutor(connection)
        executor.migrate(self.latest_targets)
        super().tearDown()

    def test_legacy_values_are_seeded_without_increasing_bot_risk(self):
        Bot = self.apps.get_model("bots", "Bot")
        RiskPolicy = self.apps.get_model("execution", "RiskPolicy")

        bot = Bot.objects.get(pk=self.bot_id)
        stricter_bot = Bot.objects.get(pk=self.stricter_bot_id)
        policy = RiskPolicy.objects.get(broker_account_id=self.account_id)

        self.assertEqual(bot.position_sizing_mode, "risk")
        self.assertEqual(bot.risk_per_trade_pct, Decimal("0.750"))
        self.assertEqual(bot.max_bot_lot_size, Decimal("0.40000000"))
        self.assertEqual(bot.max_spread_points, Decimal("17.0000"))
        self.assertEqual(bot.allowed_deviation_points, 6)
        self.assertTrue(bot.allow_live_account_execution)
        self.assertTrue(bot.close_positions_on_emergency_stop)
        self.assertEqual(bot.max_trades_per_day, 3)
        self.assertEqual(bot.risk_max_concurrent_positions, 4)
        self.assertEqual(bot.mt5_magic_number, 500_000_000 + bot.pk)

        # A bot already stricter than the old account setting remains stricter.
        self.assertEqual(stricter_bot.max_trades_per_day, 1)
        self.assertEqual(stricter_bot.risk_max_concurrent_positions, 2)

        self.assertEqual(policy.max_order_lot_size, Decimal("0.40000000"))
        self.assertEqual(policy.max_total_open_positions, 4)
        self.assertEqual(policy.max_aggregate_open_lots, Decimal("1.60000000"))
        self.assertEqual(policy.max_daily_loss_pct, Decimal("2.000"))
        self.assertEqual(policy.max_account_drawdown_pct, Decimal("6.000"))
        self.assertEqual(policy.stop_after_daily_profit_pct, Decimal("1.250"))

        final_field_names = {field.name for field in RiskPolicy._meta.get_fields()}
        self.assertNotIn("risk_per_trade_pct", final_field_names)
        self.assertNotIn("max_entry_trades_per_day", final_field_names)
        self.assertNotIn("max_spread_points", final_field_names)
        self.assertNotIn("deviation_points", final_field_names)
        self.assertNotIn("live_trading_confirmed", final_field_names)
        self.assertNotIn("emergency_close_owned_positions", final_field_names)
