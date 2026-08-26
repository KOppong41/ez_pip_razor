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
}
