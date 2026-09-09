import 'package:ez_trade_desktop/api_client.dart';
import 'package:ez_trade_desktop/main.dart';
import 'package:flutter/gestures.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

class FakeApiClient extends ApiClient {
  FakeApiClient({this.markets}) : super('http://127.0.0.1:8000');

  final List<Map<String, dynamic>>? markets;

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
          'position_sizing_mode': 'risk',
          'risk_per_trade_pct': '0.5',
          'max_bot_lot_size': '0.04',
          'risk_max_concurrent_positions': 2,
          'max_trades_per_day': 8,
          'trade_interval_minutes': 10,
          'max_spread_points': '25',
          'allowed_deviation_points': 6,
          'allow_live_account_execution': false,
          'close_positions_on_emergency_stop': false,
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
            'risk_limits': {
              'max_order_lot_size': '0.05',
              'max_total_open_positions': 3,
              'max_positions_per_symbol': 2,
              'max_aggregate_open_lots': '0.10',
            },
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
        'max_daily_loss_pct': '1.5',
        'max_account_drawdown_pct': '5.0',
        'max_total_open_positions': 3,
        'max_positions_per_symbol': 1,
        'max_order_lot_size': '0.05',
        'max_aggregate_open_lots': '0.10',
        'stop_after_daily_profit_pct': '0',
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
            'market': {
              'last_close': '2851.96',
              'tick': {'bid': 2851.95, 'ask': 2851.96},
              'volatility': {
                'bar_range': '1.23',
                'atr_price': '3.47',
                'tick_volume': 68,
              },
            },
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
      return markets ??
          [
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
    if (path == '/api/personal/logs/') {
      return [
        {
          'id': 101,
          'created_at': '2026-08-26T23:48:08Z',
          'event_type': 'scalper_engine_run',
          'severity': 'info',
          'message':
              'Scalper run tf=5m signals=0 decisions=0 orders=0 profile=eth_momentum',
          'symbol': 'ETHUSDm',
          'context': {
            'outcome': 'no_signals',
            'session': 'overnight',
            'timeframe': '5m',
            'signals': 0,
            'decisions': 0,
            'orders': 0,
          },
        },
        {
          'id': 102,
          'created_at': '2026-08-26T23:47:08Z',
          'event_type': 'risk.rejection',
          'severity': 'warning',
          'message': 'Spread exceeds configured limit',
          'symbol': 'XAUUSDm',
          'context': {'reason': 'spread_limit', 'spread_points': '52'},
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

  testWidgets('markets scrolls to the last asset and its enabled control', (
    tester,
  ) async {
    await tester.binding.setSurfaceSize(const Size(810, 600));
    addTearDown(() => tester.binding.setSurfaceSize(null));

    final markets = List.generate(
      24,
      (index) => <String, dynamic>{
        'canonical_symbol': 'ASSET$index',
        'broker_symbol': 'ASSET${index}m',
        'enabled': false,
        'trading_status': 'open',
      },
    );
    await tester.pumpWidget(
      MaterialApp(
        theme: ThemeData.dark().copyWith(platform: TargetPlatform.windows),
        home: Scaffold(
          body: MarketsPage(client: FakeApiClient(markets: markets)),
        ),
      ),
    );
    await tester.pumpAndSettle();

    expect(find.text('ASSET23').hitTestable(), findsNothing);
    await tester.sendEventToBinding(
      PointerScrollEvent(
        kind: PointerDeviceKind.mouse,
        position: tester.getCenter(find.text('ASSET0')),
        scrollDelta: const Offset(0, 3000),
      ),
    );
    await tester.pumpAndSettle();
    expect(find.text('ASSET23').hitTestable(), findsOneWidget);

    final lastSwitch = find.byType(Switch).last;
    expect(lastSwitch.hitTestable(), findsNothing);
    final horizontalBar = find.byWidgetPredicate(
      (widget) =>
          widget is Scrollbar &&
          widget.scrollbarOrientation == ScrollbarOrientation.bottom,
    );
    final barBounds = tester.getRect(horizontalBar);
    await tester.dragFrom(
      Offset(barBounds.left + 24, barBounds.bottom - 4),
      Offset(barBounds.width, 0),
      kind: PointerDeviceKind.mouse,
    );
    await tester.pumpAndSettle();
    expect(lastSwitch.hitTestable(), findsOneWidget);
    expect(tester.takeException(), isNull);

    await tester.pumpWidget(const SizedBox.shrink());
    await tester.pumpAndSettle();
    expect(tester.takeException(), isNull);
  });

  testWidgets('renders a compact searchable operations journal', (
    tester,
  ) async {
    await tester.binding.setSurfaceSize(const Size(1280, 830));
    addTearDown(() => tester.binding.setSurfaceSize(null));

    await tester.pumpWidget(
      MaterialApp(
        theme: ThemeData.dark(),
        home: DesktopShell(client: FakeApiClient(), onLogout: () {}),
      ),
    );
    await tester.pump();
    await tester.tap(find.text('Logs'));
    await tester.pumpAndSettle();

    expect(find.text('OPERATIONS JOURNAL'), findsOneWidget);
    expect(find.text('Automation activity'), findsOneWidget);
    expect(find.text('Scalper Engine Run'), findsOneWidget);
    expect(find.text('ETHUSDm'), findsOneWidget);
    expect(find.text('Context {'), findsNothing);

    await tester.tap(find.text('Warnings'));
    await tester.pumpAndSettle();
    expect(find.text('Risk Rejection'), findsOneWidget);
    expect(find.text('Scalper Engine Run'), findsNothing);
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
    await tester.drag(
      find.byType(SingleChildScrollView).last,
      const Offset(0, -1000),
    );
    await tester.pumpAndSettle();
    expect(find.text('Account hard limits'), findsOneWidget);
    expect(find.text('Risk per trade'), findsOneWidget);
    expect(find.text('Bot maximum lot size'), findsOneWidget);
    expect(find.text('Bot maximum open positions'), findsOneWidget);
    expect(
      find.text('Allow live-account execution for this bot'),
      findsOneWidget,
    );
    expect(tester.takeException(), isNull);
  });

  testWidgets('bot editor changes sizing fields without mixing semantics', (
    tester,
  ) async {
    await tester.binding.setSurfaceSize(const Size(1100, 1000));
    addTearDown(() => tester.binding.setSurfaceSize(null));

    await tester.pumpWidget(
      MaterialApp(
        theme: ThemeData.dark(),
        home: Scaffold(body: BotsPage(client: FakeApiClient())),
      ),
    );
    await tester.pumpAndSettle();
    await tester.tap(find.byTooltip('Edit bot'));
    await tester.pumpAndSettle();
    final sizingDropdown = find
        .byWidgetPredicate(
          (widget) => widget is DropdownButtonFormField<String>,
        )
        .last;
    await tester.ensureVisible(sizingDropdown);
    await tester.pumpAndSettle();

    expect(find.text('Risk per trade'), findsOneWidget);
    expect(find.text('Fixed lot size'), findsNothing);
    await tester.tap(sizingDropdown);
    await tester.pumpAndSettle();
    await tester.tap(find.text('Fixed lot size').last);
    await tester.pumpAndSettle();
    expect(find.text('Fixed lot size'), findsAtLeastNWidgets(1));
    expect(find.text('Risk per trade'), findsNothing);
    expect(tester.takeException(), isNull);
  });

  testWidgets('risk page contains only account capital and exposure limits', (
    tester,
  ) async {
    await tester.binding.setSurfaceSize(const Size(1200, 900));
    addTearDown(() => tester.binding.setSurfaceSize(null));

    await tester.pumpWidget(
      MaterialApp(
        theme: ThemeData.dark(),
        home: Scaffold(body: RiskPage(client: FakeApiClient())),
      ),
    );
    await tester.pumpAndSettle();

    expect(find.text('Capital protection'), findsOneWidget);
    expect(find.text('Hard aggregate exposure'), findsOneWidget);
    expect(find.text('Maximum lot size per order'), findsOneWidget);
    expect(find.text('Total bot-owned positions'), findsOneWidget);
    expect(find.text('Aggregate open volume'), findsOneWidget);
    expect(find.text('Risk per trade'), findsNothing);
    expect(find.text('Execution quality'), findsNothing);
    expect(find.text('Confirm live-account trading'), findsNothing);
    expect(tester.takeException(), isNull);
  });

  testWidgets('shows aggregate strategy skip counts and run details', (
    tester,
  ) async {
    await tester.binding.setSurfaceSize(const Size(1100, 1400));
    addTearDown(() => tester.binding.setSurfaceSize(null));

    await tester.pumpWidget(
      MaterialApp(
        theme: ThemeData.dark(),
        home: Scaffold(body: RunEvidencePage(client: FakeApiClient())),
      ),
    );
    await tester.pumpAndSettle();

    expect(find.text('24-hour skip counts'), findsOneWidget);
    expect(
      find.text('Trend Pullback / Trend Pullback No Trend'),
      findsOneWidget,
    );
    expect(
      find.text('Breakout Retest / Breakout Retest No Break'),
      findsOneWidget,
    );
    expect(find.text('LAST CLOSE'), findsOneWidget);
    expect(find.text('ACTIONABLE'), findsOneWidget);
    expect(find.text('SKIPPED'), findsOneWidget);
    expect(find.text('STRATEGIES'), findsOneWidget);
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
    expect(tester.takeException(), isNull);

    await tester.tap(find.text('Markets'));
    await tester.pumpAndSettle();
    expect(find.text('Assets & markets'), findsOneWidget);
    expect(find.text('XAUUSD'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });
}
