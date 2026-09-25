"""Recorded MT5 candles through the production BTC preset and Decision path."""
import csv
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from bots.models import Asset, Bot
from bots.services import apply_recommendations_to_bot
from brokers.models import BrokerAccount
from execution.models import Decision, ScalperRunLog
from execution.services.brokers import BrokerSymbolConstraints
from execution.tasks import trade_scalper_strategies_for_bot


@override_settings(ECONOMIC_CALENDAR_ENABLED=False)
class BtcPresetEndToEndTests(TestCase):
    def setUp(self):
        self.now = datetime(2026, 9, 15, 19, 30, tzinfo=timezone.utc)
        self.frames = {frame: [] for frame in ('5m', '15m', '1h')}
        with (Path(__file__).parent / 'fixtures/btc_confirmed_pullback.csv').open() as stream:
            for row in csv.DictReader(stream):
                self.frames[row['timeframe']].append({
                    'time': datetime.fromisoformat(row['time']),
                    **{key: Decimal(row[key]) for key in ('open', 'high', 'low', 'close')},
                    'tick_volume': int(row['tick_volume']),
                })
        owner = get_user_model().objects.create_user('btc-preset-e2e')
        account = BrokerAccount.objects.create(owner=owner, name='Recorded BTC', broker='mt5',
            connector='mt5_local', account_ref='btc-preset-e2e', is_active=True, is_verified=True)
        self.bot = Bot.objects.create(owner=owner, name='BTC preset E2E', broker_account=account,
            asset=Asset.objects.get(symbol='BTCUSDm'), engine_mode='scalper')
        apply_recommendations_to_bot(self.bot, save=False)
        self.bot.status, self.bot.auto_trade = 'active', True
        self.bot.save()

    def scan(self):
        def fetch(*, broker_account, symbol, timeframe, n_bars):
            self.assertEqual(symbol, 'BTCUSDm')
            self.assertEqual(broker_account.pk, self.bot.broker_account_id)
            bars = self.frames[timeframe][-n_bars:]
            minutes = {'5m': 5, '15m': 15, '1h': 60}[timeframe]
            self.assertTrue(all(bar['time'] + timedelta(minutes=minutes) <= self.now for bar in bars))
            return bars

        constraints = BrokerSymbolConstraints(point=Decimal('.01'), digits=2, min_lot=Decimal('.01'),
            max_lot=Decimal(100), lot_step=Decimal('.01'), stops_level_points=Decimal(0))
        close = self.frames['5m'][-1]['close']
        # Only broker I/O and wall time are replaced. Selection, context analysis,
        # config, indicators, detectors, Signal and Decision persistence are real.
        with (
            patch('django.utils.timezone.now', return_value=self.now),
            patch('execution.tasks.get_market_status_for_bot', return_value=SimpleNamespace(is_open=True, reason='recorded')),
            patch('execution.tasks.get_broker_symbol_constraints', return_value=constraints),
            patch('execution.tasks.get_candles_for_account', side_effect=fetch) as candles,
            patch('execution.tasks.MT5Connector') as connector,
            patch('execution.tasks._dispatch_scalper_candidate') as dispatch,
        ):
            connector.return_value.symbol_info_for_account.return_value = SimpleNamespace(visible=True, trade_mode=4)
            connector.return_value.tick_for_account.return_value = SimpleNamespace(
                bid=close, ask=close + Decimal(10), last=close, time=self.now.timestamp())
            result = trade_scalper_strategies_for_bot.run(self.bot.pk, timeframe='1m', defer_dispatch=True)
        dispatch.assert_not_called()
        self.assertFalse(self.bot.orders.exists())
        self.assertEqual([call.kwargs['timeframe'] for call in candles.call_args_list], ['5m', '15m', '1h'])
        return result, (
            ScalperRunLog.objects.filter(bot=self.bot)
            .order_by("-created_at", "-id")
            .first()
            .summary
        )

    def test_real_btc_candles_and_unmodified_preset_create_open_decision(self):
        result, summary = self.scan()
        self.assertEqual(result['status'], 'ok', result)
        decision = Decision.objects.get(bot=self.bot, reason='trend_pullback_bear')
        self.assertEqual(decision.action, 'open', decision.reason)
        self.assertEqual(decision.signal.direction, 'sell')
        self.assertEqual(decision.signal.timeframe, '5m')
        setup, confirmation = self.frames['5m'][-2:]
        self.assertEqual(Decimal(decision.params['sl']), setup['high'])
        self.assertEqual(Decimal(decision.params['entry']), confirmation['close'])
        self.assertEqual(Decimal(decision.params['entry_trigger']), setup['low'])
        self.assertLess(confirmation['close'], setup['low'])
        self.assertLess(confirmation['high'], setup['high'])
        stop_pct = (setup['high'] - confirmation['close']) / confirmation['close'] * 100
        self.assertGreaterEqual(stop_pct, Decimal('.35'))
        self.assertLessEqual(stop_pct, Decimal('.90'))
        self.assertGreaterEqual(decision.score, float(self.bot.decision_min_score))
        self.assertEqual(summary['outcome'], 'candidate_pending_allocation')
        self.assertEqual(summary["new_signals"], 1)
        self.assertEqual(summary["reused_signals"], 0)
        self.assertEqual(summary["new_decisions"], 1)
        self.assertEqual(summary["reused_decisions"], 0)
        self.assertEqual(summary["open_decisions"], 1)
        self.assertEqual(summary["ignored_decisions"], 0)
        self.assertIn('trend_pullback', summary['strategies_evaluated'])
        self.bot.refresh_from_db()
        self.assertEqual(self.bot.risk_per_trade_pct, Decimal('.25'))
        self.assertEqual(self.bot.decision_min_score, Decimal('.68'))

    def test_unconfirmed_pullback_does_not_create_a_pullback_signal(self):
        setup, confirmation = self.frames['5m'][-2:]
        confirmation['close'] = setup['low']
        _, summary = self.scan()
        pullback = next(event for event in summary['strategies'] if event['strategy'] == 'trend_pullback')
        self.assertEqual(pullback['reason'], 'trend_pullback_confirmation_failed')
        self.assertFalse(Decision.objects.filter(bot=self.bot, reason='trend_pullback_bear').exists())

    def test_valid_detector_with_tight_stop_creates_ignored_decision(self):
        setup, confirmation = self.frames["5m"][-2:]

        # Retain the genuine setup and its stop, but confirm just past its low.
        confirmation["open"] = setup["close"]
        confirmation["close"] = setup["low"] - Decimal(".01")
        confirmation["high"] = setup["close"] + Decimal(".01")
        confirmation["low"] = confirmation["close"] - Decimal(".01")

        _, summary = self.scan()

        decision = Decision.objects.get(
            bot=self.bot,
            reason="scalper:sl_below_min",
        )

        self.assertEqual(decision.action, "ignore")
        self.assertEqual(
            Decimal(decision.signal.payload["sl"]),
            setup["high"],
        )

        self.assertEqual(
            summary["outcome"],
            "decisions_rejected",
        )
        self.assertEqual(
            summary["rejection_reason"],
            "scalper:sl_below_min",
        )
        self.assertEqual(summary["new_decisions"], 1)
        self.assertEqual(summary["ignored_decisions"], 1)
        self.assertEqual(summary["open_decisions"], 0)

    def test_repeated_scan_reuses_signal_and_decision_without_reporting_them_as_new(self):
        self.scan()

        decision_count = Decision.objects.filter(bot=self.bot).count()

        _, summary = self.scan()

        self.assertEqual(
            Decision.objects.filter(bot=self.bot).count(),
            decision_count,
        )

        self.assertEqual(summary["new_signals"], 0)
        self.assertEqual(summary["reused_signals"], 1)
        self.assertEqual(summary["new_decisions"], 0)
        self.assertEqual(summary["reused_decisions"], 1)

        self.assertEqual(
            summary["decision_results"][0]["created"],
            False,
        )