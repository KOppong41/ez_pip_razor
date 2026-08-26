import 'package:ez_trade_desktop/api_client.dart';
import 'package:ez_trade_desktop/main.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

class FakeApiClient extends ApiClient {
  FakeApiClient() : super('http://127.0.0.1:8000');

  @override
  Future<dynamic> get(String path) async {
    if (path == '/api/bots/') {
      return [
        {
          'id': 10,
          'bot_id': 'DEMO123',
          'name': 'Gold London Scalper',
          'status': 'stopped',
          'asset': 1,
          'asset_details': {
            'id': 1,
            'symbol': 'XAUUSDm',
            'display_name': 'Gold',
            'category': 'commodities',
          },
          'broker_account': 2,
          'broker_account_details': {'id': 2, 'name': 'Primary MT5'},
          'engine_mode': 'scalper',
          'default_timeframe': '1m',
          'default_qty': '0.01',
          'auto_trade': true,
          'enabled_strategies': ['momentum_ignition'],
          'trading_profile': 'scalper',
        },
      ];
    }
    if (path == '/api/bots/options/') {
      return {
        'assets': [
          {
            'id': 1,
            'symbol': 'XAUUSDm',
            'display_name': 'Gold',
            'category': 'commodities',
            'min_qty': '0.01',
            'recommended_qty': '0.01',
          },
        ],
        'accounts': [
          {
            'id': 2,
            'name': 'Primary MT5',
            'mt5_login': '100001',
            'is_verified': true,
          },
        ],
        'engine_modes': [
          {'value': 'scalper', 'label': 'Internal scalper'},
          {'value': 'harami', 'label': 'Internal engine'},
        ],
        'timeframes': ['1m', '5m', '15m'],
        'strategies': [
          {'value': 'momentum_ignition', 'label': 'Momentum Ignition'},
        ],
        'trading_profiles': [
          {'value': 'scalper', 'label': 'Scalper'},
        ],
        'usage': {'bots': 1, 'bot_limit': 3},
      };
    }
    if (path == '/api/personal/history/') {
      return {
        'summary': {
          'total_trades': 1,
          'wins': 1,
          'losses': 0,
          'win_rate': '100.0',
          'gross_profit': '24.50',
          'gross_loss': '0.0',
          'net_profit': '24.50',
          'profit_factor': null,
        },
        'trades': [
          {
            'id': 1,
            'symbol': 'XAUUSDm',
            'side': 'buy',
            'qty': '0.01',
            'price': '3370.10',
            'exit_price': '3372.55',
            'pnl': '24.50',
            'status': 'closed',
            'broker_ticket': '90001',
            'closed_at': '2026-08-26T12:30:00Z',
          },
        ],
      };
    }
    if (path == '/api/personal/risk/') {
      return {
        'risk_per_trade_pct': '0.5',
        'max_daily_loss_pct': '1.5',
        'max_account_drawdown_pct': '5.0',
        'max_positions': 1,
        'max_positions_per_symbol': 1,
        'max_entry_trades_per_day': 3,
        'max_lot': '0.05',
        'max_spread_points': '30',
        'deviation_points': 8,
        'stop_after_daily_profit_pct': '0',
        'emergency_close_owned_positions': false,
        'live_trading_confirmed': false,
      };
    }
    if (path == '/api/personal/backtesting/') {
      return [
        {
          'id': 13985,
          'bot_id': 3,
          'bot__name': 'Gold London Scalper',
          'timeframe': '5m',
          'session': 'london',
          'created_at': '2026-08-26T12:30:00Z',
          'summary': {
            'market': {'last_close': '2851.96', 'tick': 68},
            'volatility': {'bar_range': '1.23', 'atr_points': '3.47'},
            'strategies': [
              {
                'strategy': 'trend_pullback',
                'action': 'skip',
                'reason': 'trend_pullback_no_trend',
                'score': 0.0,
              },
              {
                'strategy': 'breakout_retest',
                'action': 'skip',
                'reason': 'breakout_retest_no_break',
                'score': 0.0,
              },
            ],
          },
        },
      ];
    }
    if (path == '/api/personal/markets/') {
      return [
        {
          'asset_id': 1,
          'canonical_symbol': 'XAUUSD',
          'symbol': 'XAUUSDm',
          'display_name': 'Gold',
          'category': 'commodities',
          'broker_symbol': 'XAUUSDm',
          'enabled': true,
          'bid': '3375.10',
          'ask': '3375.30',
          'spread': '0.20',
          'recommended_qty': '0.01',
          'trading_status': 'open',
        },
      ];
    }
    return {
      'bot': {
        'running': false,
        'emergency_stop': false,
        'statuses': [
          {
            'id': 1,
            'name': 'XAUUSDm Scalper M1',
            'status': 'active',
            'engine_mode': 'scalper',
          },
          {
            'id': 2,
            'name': 'BTC/USDm',
            'status': 'active',
            'engine_mode': 'scalper',
          },
        ],
      },
      'mt5': {
        'connected': false,
        'checked_at': null,
        'last_error': 'not checked',
        'account_mode': 'unknown',
      },
      'account': {
        'alias': 'Primary MT5',
        'login': '100001',
        'server': 'Demo-Server',
        'currency': 'USD',
      },
      'financial': {
        'balance': 10000,
        'equity': 10025,
        'floating_pnl': 25,
        'realized_pnl_today': 42,
        'drawdown_pct': 0.4,
        'start_equity': 9980,
        'margin': 120,
        'free_margin': 9905,
        'margin_level': 8354,
      },
      'trading': {
        'active_positions': 2,
        'today_entries': 4,
        'winning_trades_today': 2,
        'losing_trades_today': 1,
        'enabled_symbols': ['XAUUSD', 'EURUSD'],
      },
    };
  }
}

void main() {
  testWidgets('shows secure sign-in screen', (tester) async {
    await tester.pumpWidget(const EzTradeApp());
    expect(find.text('EZ TRADE'), findsOneWidget);
    expect(find.text('Sign in securely'), findsOneWidget);
  });

  testWidgets('shows a clear notice after session expiry', (tester) async {
    await tester.pumpWidget(
      MaterialApp(
        home: LoginScreen(
          onLogin: (_) {},
          notice: 'Your session expired. Sign in again to continue.',
        ),
      ),
    );

    expect(
      find.text('Your session expired. Sign in again to continue.'),
      findsOneWidget,
    );
  });

  testWidgets('renders trading terminal dashboard without layout errors', (
    tester,
  ) async {
    await tester.binding.setSurfaceSize(const Size(1760, 831));
    addTearDown(() => tester.binding.setSurfaceSize(null));

    await tester.pumpWidget(
      MaterialApp(
        theme: ThemeData.dark(),
        home: DesktopShell(client: FakeApiClient(), onLogout: () {}),
      ),
    );
    await tester.pump();

    expect(find.text('Automation engines'), findsOneWidget);
    expect(find.text('Session pulse'), findsOneWidget);
    expect(find.text('USD 10000.00'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  testWidgets('renders client bot and asset management workspaces', (
    tester,
  ) async {
    await tester.binding.setSurfaceSize(const Size(1760, 831));
    addTearDown(() => tester.binding.setSurfaceSize(null));

    await tester.pumpWidget(
      MaterialApp(
        theme: ThemeData.dark(),
        home: DesktopShell(client: FakeApiClient(), onLogout: () {}),
      ),
    );
    await tester.pump();

    await tester.tap(find.text('Bots'));
    await tester.pumpAndSettle();
    expect(find.text('Bot manager'), findsOneWidget);
    expect(find.text('Gold London Scalper'), findsOneWidget);

    await tester.tap(find.text('Markets'));
    await tester.pumpAndSettle();
    expect(find.text('Assets & markets'), findsOneWidget);
    expect(find.text('XAUUSD'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  testWidgets('bot editor fits a compact desktop window', (tester) async {
    await tester.binding.setSurfaceSize(const Size(810, 830));
    addTearDown(() => tester.binding.setSurfaceSize(null));

    await tester.pumpWidget(
      MaterialApp(
        theme: ThemeData.dark(),
        home: DesktopShell(client: FakeApiClient(), onLogout: () {}),
      ),
    );
    await tester.pump();
    await tester.tap(find.text('Bots'));
    await tester.pumpAndSettle();
    await tester.tap(find.byTooltip('Edit bot'));
    await tester.pumpAndSettle();

    expect(find.text('Edit bot configuration'), findsOneWidget);
    expect(find.text('Save changes'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  testWidgets('renders polished trading workspaces without raw records', (
    tester,
  ) async {
    await tester.binding.setSurfaceSize(const Size(1100, 830));
    addTearDown(() => tester.binding.setSurfaceSize(null));

    await tester.pumpWidget(
      MaterialApp(
        theme: ThemeData.dark(),
        home: DesktopShell(client: FakeApiClient(), onLogout: () {}),
      ),
    );
    await tester.pump();

    await tester.tap(find.text('Trade history'));
    await tester.pumpAndSettle();
    expect(find.text('PERFORMANCE LEDGER'), findsOneWidget);
    expect(find.text('XAUUSDm'), findsOneWidget);
    expect(tester.takeException(), isNull);

    await tester.tap(find.text('Risk'));
    await tester.pumpAndSettle();
    expect(find.text('ACCOUNT GUARDRAILS'), findsOneWidget);
    expect(find.text('Capital protection'), findsOneWidget);
    expect(tester.takeException(), isNull);

    await tester.tap(find.text('Backtesting'));
    await tester.pumpAndSettle();
    expect(find.text('STRATEGY LAB'), findsOneWidget);
    expect(find.text('Strategy decisions (2)'), findsOneWidget);
    expect(tester.takeException(), isNull);

    await tester.tap(find.text('Markets'));
    await tester.pumpAndSettle();
    expect(find.text('Assets & markets'), findsOneWidget);
    expect(find.text('XAUUSD'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });
}
