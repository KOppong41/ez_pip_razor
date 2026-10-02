from decimal import Decimal

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class PositionOutcomeMigrationTests(TransactionTestCase):
    migrate_from = [("bots", "0061_harden_pause_ownership"),
                    ("execution", "0064_brokerposition_loss_streak_accounted_at_and_more")]
    migrate_to = [("bots", "0061_harden_pause_ownership"),
                  ("execution", "0065_alter_position_trade_pnl")]

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.latest_targets = MigrationExecutor(connection).loader.graph.leaf_nodes()

    def setUp(self):
        super().setUp()
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_from)
        apps = executor.loader.project_state(self.migrate_from).apps
        User = apps.get_model("auth", "User")
        Account = apps.get_model("brokers", "BrokerAccount")
        Asset = apps.get_model("bots", "Asset")
        Bot = apps.get_model("bots", "Bot")
        Order = apps.get_model("execution", "Order")
        Execution = apps.get_model("execution", "Execution")
        BrokerPosition = apps.get_model("execution", "BrokerPosition")
        Position = apps.get_model("execution", "Position")

        owner = User.objects.create(username="legacy-outcome-owner")
        account = Account.objects.create(
            owner=owner, name="Legacy outcomes", broker="mt5",
            connector="mt5_local", account_ref="legacy-outcomes",
        )
        asset = Asset.objects.create(symbol="LEGACYOUTCOMEUSD", recommended_config={})
        bot = Bot.objects.create(owner=owner, name="Legacy outcome bot", asset=asset,
                                 broker_account=account)
        exit_order = Order.objects.create(
            owner=owner, bot=bot, broker_account=account,
            client_order_id="legacy-outcome-exit", symbol=asset.symbol,
            side="sell", intent="exit", qty=Decimal(".1"),
        )
        Execution.objects.create(
            order=exit_order, qty=Decimal(".1"), price=Decimal("99"),
            broker_position_ticket=1002, profit=Decimal("-1"),
        )
        ids = []
        for ticket, status in ((1001, "closed"), (1002, "open"), (1003, "open")):
            ids.append(BrokerPosition.objects.create(
                broker_account=account, bot=bot, broker_position_ticket=ticket,
                ownership="ez_trade", symbol=asset.symbol, side="buy",
                volume=Decimal(".1"), open_price=Decimal("100"), status=status,
            ).pk)
        self.position_ids = ids
        self.paper_position_id = Position.objects.create(
            broker_account=account, symbol=asset.symbol,
            qty=Decimal(".1"), avg_price=Decimal("100"),
        ).pk

        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_to)
        self.apps = executor.loader.project_state(self.migrate_to).apps

    def tearDown(self):
        MigrationExecutor(connection).migrate(self.latest_targets)
        super().tearDown()

    def test_legacy_closed_and_partially_exited_positions_cannot_be_recounted(self):
        BrokerPosition = self.apps.get_model("execution", "BrokerPosition")
        Position = self.apps.get_model("execution", "Position")
        closed, partial, untouched = [BrokerPosition.objects.get(pk=pk)
                                      for pk in self.position_ids]
        self.assertIsNotNone(closed.loss_streak_accounted_at)
        self.assertIsNotNone(partial.loss_streak_accounted_at)
        self.assertIsNone(untouched.loss_streak_accounted_at)
        self.assertIsNone(Position.objects.get(pk=self.paper_position_id).trade_pnl)
