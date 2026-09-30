"""Legacy pause ownership must survive the pause-reason schema migration."""

from datetime import datetime, timezone

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class PauseReasonMigrationTests(TransactionTestCase):
    migrate_from = [("bots", "0057_alter_bot_allocation_amount_and_more")]
    migrate_to = [("bots", "0058_bot_pause_reason")]
    catchup_to = [("bots", "0060_backfill_legacy_pause_reasons")]
    harden_to = [("bots", "0061_harden_pause_ownership")]

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.latest_targets = MigrationExecutor(connection).loader.graph.leaf_nodes()

    def setUp(self):
        super().setUp()
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_from)
        OldBot = executor.loader.project_state(self.migrate_from).apps.get_model("bots", "Bot")
        expires = datetime(2026, 9, 30, tzinfo=timezone.utc)
        self.expires = expires
        for index, (name, status, schedule_paused, paused_until) in enumerate((
            ("legacy-schedule", "paused", True, None),
            ("legacy-schedule-with-time", "paused", True, expires),
            ("legacy-ambiguous-timer", "paused", False, expires),
            ("legacy-manual", "paused", False, None),
            ("legacy-active", "active", False, None),
            ("legacy-stopped", "stopped", False, expires),
        )):
            OldBot.objects.create(
                name=name, bot_id=f"PAUSE{index:05d}", status=status,
                schedule_paused=schedule_paused, paused_until=paused_until,
            )
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_to)
        self.apps = executor.loader.project_state(self.migrate_to).apps

    def tearDown(self):
        MigrationExecutor(connection).migrate(self.latest_targets)
        super().tearDown()

    def test_0058_classifies_legacy_pauses_without_starting_bots(self):
        Bot = self.apps.get_model("bots", "Bot")
        expected = {
            "legacy-schedule": ("paused", "schedule"),
            "legacy-schedule-with-time": ("paused", "schedule"),
            "legacy-ambiguous-timer": ("paused", "manual"),
            "legacy-manual": ("paused", "manual"),
            "legacy-active": ("active", ""),
            "legacy-stopped": ("stopped", ""),
        }
        for name, state in expected.items():
            with self.subTest(name=name):
                bot = Bot.objects.get(name=name)
                self.assertEqual((bot.status, bot.pause_reason), state)

    def test_0060_repairs_only_unclassified_pauses_on_already_migrated_databases(self):
        Bot = self.apps.get_model("bots", "Bot")
        Bot.objects.filter(name="legacy-ambiguous-timer").update(pause_reason="")
        Bot.objects.filter(name="legacy-manual").update(pause_reason="")
        Bot.objects.filter(name="legacy-schedule").update(pause_reason="manual")
        executor = MigrationExecutor(connection)
        executor.migrate(self.catchup_to)
        Bot = executor.loader.project_state(self.catchup_to).apps.get_model("bots", "Bot")
        self.assertEqual(Bot.objects.get(name="legacy-ambiguous-timer").pause_reason, "manual")
        self.assertEqual(Bot.objects.get(name="legacy-manual").pause_reason, "manual")
        self.assertEqual(Bot.objects.get(name="legacy-schedule").pause_reason, "manual")

    def test_0061_removes_unproven_cooldown_ownership_but_keeps_proven_pause(self):
        Bot = self.apps.get_model("bots", "Bot")
        Bot.objects.filter(name="legacy-ambiguous-timer").update(pause_reason="loss_cooldown")
        Bot.objects.create(
            name="proven-cooldown", bot_id="PROVEN0001", status="paused",
            pause_reason="loss_cooldown", paused_until=self.expires,
            scalper_params={"_loss_pause_owner": {"until": self.expires.isoformat()}},
        )
        Bot.objects.filter(name="legacy-stopped").update(kill_switch_triggered_at=self.expires)
        executor = MigrationExecutor(connection)
        executor.migrate(self.harden_to)
        Bot = executor.loader.project_state(self.harden_to).apps.get_model("bots", "Bot")
        ambiguous = Bot.objects.get(name="legacy-ambiguous-timer")
        self.assertEqual(ambiguous.pause_reason, "manual")
        self.assertIsNone(ambiguous.paused_until)
        proven = Bot.objects.get(name="proven-cooldown")
        self.assertEqual(proven.pause_reason, "loss_cooldown")
        self.assertEqual(proven.paused_until, self.expires)
        self.assertEqual(Bot.objects.get(name="legacy-schedule").pause_reason, "schedule")
        stopped = Bot.objects.get(name="legacy-stopped")
        self.assertEqual(stopped.status, "stopped")
        self.assertEqual(stopped.pause_reason, "")
        self.assertEqual(stopped.kill_switch_triggered_at, self.expires)
