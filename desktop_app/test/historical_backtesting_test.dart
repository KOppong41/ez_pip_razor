import 'dart:convert';
import 'dart:typed_data';

import 'package:ez_trade_desktop/api_client.dart';
import 'package:ez_trade_desktop/main.dart';
import 'package:file_selector/file_selector.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

Map<String, dynamic> savedRun({bool noTrades = false}) => {
  'id': 42,
  'symbol': 'BTCUSDm',
  'bot_name': 'Bitcoin demo',
  'status': 'completed',
  'source_name': 'btc-history.csv',
  'created_at': '2025-01-06T12:00:00Z',
  'config': {
    'strategy': 'trend_pullback',
    'timeframe': '1m',
    'currency': 'USD',
    'initial_balance': '10000',
  },
  'dataset': {'bars': 240, 'gap_count': 0, 'sha256': 'recorded-hash'},
  'result': {
    'summary': {
      'trades': noTrades ? 0 : 25,
      'wins': noTrades ? 0 : 25,
      'losses': 0,
      'breakeven': 0,
      'net_pnl': noTrades ? '0' : '250',
      'return_pct': '2.5',
      'win_rate_pct': '100',
      'profit_factor': null,
      'max_drawdown_pct': '0',
      'ending_balance': '10250',
      'avg_trade': '10',
      'spread_cost': '5',
      'slippage_cost': '2',
      'commission': '3',
      'evaluated_bars': 140,
      'open_signals': 25,
      'first_at': '2025-01-06T10:00:00Z',
      'last_at': '2025-01-06T12:00:00Z',
    },
    'equity': [
      {'time': '2025-01-06T10:01:00Z', 'equity': '10000'},
      {'time': '2025-01-06T12:00:00Z', 'equity': '10250'},
    ],
    'trades': noTrades
        ? []
        : List.generate(
            25,
            (index) => {
              'direction': 'buy',
              'entry_time': '2025-01-06T10:00:00Z',
              'exit_time': '2025-01-06T10:01:00Z',
              'entry_price': '100',
              'exit_price': '110',
              'sl': '95',
              'tp': '110',
              'reason': 'take_profit',
              'pnl': '10',
              'balance': '${10010 + index * 10}',
            },
          ),
    'skip_reasons': [
      {'reason': 'trend_pullback_no_trend', 'count': 115},
    ],
    'assumptions': ['One fixed-size position; no live broker orders.'],
  },
};

class HistoricalClient extends ApiClient {
  HistoricalClient({this.failRun = false, this.noTrades = false})
    : super('http://localhost');
  final bool failRun;
  final bool noTrades;
  Map<String, dynamic>? submitted;

  @override
  Future<dynamic> get(String path) async {
    if (path == '/api/personal/backtests/options/') {
      return {
        'bots': [
          {
            'id': 3,
            'name': 'Bitcoin demo',
            'symbol': 'BTCUSDm',
            'timeframe': '1m',
            'quantity': '0.01',
          },
        ],
        'strategies': ['trend_pullback', 'momentum_ignition'],
        'timeframes': ['1m', '5m'],
      };
    }
    if (path == '/api/personal/backtests/42/') {
      return savedRun(noTrades: noTrades);
    }
    if (path.startsWith('/api/personal/backtests/')) {
      return {
        'count': 1,
        'page': 1,
        'page_size': 20,
        'results': [savedRun(noTrades: noTrades)],
      };
    }
    if (path == '/api/personal/backtesting/') return [];
    throw StateError('Unexpected GET $path');
  }

  @override
  Future<dynamic> post(String path, [Map<String, dynamic>? body]) async {
    if (path != '/api/personal/backtests/') {
      throw StateError('Unexpected POST $path');
    }
    submitted = body;
    if (failRun) {
      throw const ApiException(
        'CSV row 2: timestamps must be unique and increasing.',
      );
    }
    return savedRun(noTrades: noTrades);
  }
}

Finder field(String label) =>
    find.ancestor(of: find.text(label), matching: find.byType(TextFormField));

Future<void> mount(WidgetTester tester, HistoricalClient client) async {
  await tester.binding.setSurfaceSize(const Size(1000, 700));
  addTearDown(() => tester.binding.setSurfaceSize(null));
  await tester.pumpWidget(
    MaterialApp(
      theme: ThemeData.dark().copyWith(platform: TargetPlatform.windows),
      home: Scaffold(
        body: BacktestingPage(
          client: client,
          pickCsv: () async => XFile.fromData(
            utf8.encode(
              'time,open,high,low,close,tick_volume\n2025-01-06T10:00:00Z,100,102,98,100,80',
            ),
            name: 'btc-history.csv',
          ),
        ),
      ),
    ),
  );
  await tester.pumpAndSettle();
}

Uint8List testCsvBytes() => Uint8List.fromList([
  0xff,
  0xfe,
  for (final code
      in 'time,open,high,low,close\n2025-01-06T10:00:00Z,100,102,98,100'
          .codeUnits) ...[code & 0xff, code >> 8],
]);

Future<void> submit(WidgetTester tester) async {
  await tester.tap(find.text('Import candle CSV'));
  await tester.pumpAndSettle();
  await tester.ensureVisible(field('Contract size per lot'));
  await tester.enterText(field('Contract size per lot'), '1');
  await tester.enterText(field('Point size'), '0.01');
  await tester.ensureVisible(find.text('Run historical backtest'));
  await tester.tap(find.text('Run historical backtest'));
  await tester.pumpAndSettle();
}

void main() {
  testWidgets('imports UTF-16 MT5 exports in a compact window', (tester) async {
    await tester.binding.setSurfaceSize(const Size(760, 600));
    addTearDown(() => tester.binding.setSurfaceSize(null));
    final client = HistoricalClient();
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: BacktestingPage(
            client: client,
            pickCsv: () async =>
                XFile.fromData(testCsvBytes(), name: 'mt5.csv'),
          ),
        ),
      ),
    );
    await tester.pumpAndSettle();
    await tester.ensureVisible(find.text('Import candle CSV'));
    await submit(tester);
    expect(client.submitted?['csv'], startsWith('time,open,high,low,close'));
    expect(tester.takeException(), isNull);
  });
  testWidgets(
    'imports candles, runs simulation, and browses complete results',
    (tester) async {
      final client = HistoricalClient();
      await mount(tester, client);
      expect(find.text('Historical backtesting'), findsOneWidget);
      await submit(tester);
      expect(client.submitted?['bot_id'], 3);
      expect(client.submitted?['contract_size'], '1');
      expect(client.submitted?['csv'], contains('2025-01-06'));
      expect(find.text('Backtest #42 · BTCUSDm'), findsOneWidget);
      expect(find.text('Equity curve (USD)'), findsOneWidget);
      expect(find.text('NET P&L (USD)'), findsOneWidget);
      await tester.ensureVisible(find.text('Buy').first);
      await tester.tap(find.text('Buy').first);
      await tester.pumpAndSettle();
      expect(find.text('Simulated trade details'), findsOneWidget);
      expect(find.textContaining('Spread Cost:'), findsOneWidget);
      await tester.tap(find.text('Close details'));
      await tester.pumpAndSettle();
      await tester.ensureVisible(find.byTooltip('Next trades'));
      await tester.tap(find.byTooltip('Next trades'));
      await tester.pumpAndSettle();
      expect(find.text('21–25 of 25'), findsOneWidget);
      await tester.ensureVisible(find.text('Losses'));
      await tester.tap(find.text('Losses'));
      await tester.pumpAndSettle();
      expect(find.text('No trades match this filter.'), findsOneWidget);
      await tester.ensureVisible(
        find.text('Model assumptions & saved settings'),
      );
      await tester.tap(find.text('Model assumptions & saved settings'));
      await tester.pumpAndSettle();
      expect(find.textContaining('recorded-hash'), findsOneWidget);
      expect(tester.takeException(), isNull);
    },
  );

  testWidgets('shows backend validation errors without fabricating results', (
    tester,
  ) async {
    await mount(tester, HistoricalClient(failRun: true));
    await submit(tester);
    expect(find.textContaining('timestamps must be unique'), findsOneWidget);
    expect(find.text('Backtest #42 · BTCUSDm'), findsNothing);
    expect(find.text('Run historical backtest'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  testWidgets(
    'opens a saved zero-trade run and keeps run evidence accessible',
    (tester) async {
      await mount(tester, HistoricalClient(noTrades: true));
      await tester.ensureVisible(find.text('BTCUSDm · Trend Pullback · 1m'));
      await tester.tap(find.text('BTCUSDm · Trend Pullback · 1m'));
      await tester.pumpAndSettle();
      expect(
        find.textContaining('No trades met the selected strategy'),
        findsOneWidget,
      );
      final export = tester.widget<OutlinedButton>(
        find.widgetWithText(OutlinedButton, 'Export all trades'),
      );
      expect(export.onPressed, isNull);
      await tester.tap(find.text('Run evidence'));
      await tester.pumpAndSettle();
      expect(find.text('No strategy-run evidence yet'), findsOneWidget);
      await tester.tap(find.text('Historical backtests'));
      await tester.pumpAndSettle();
      expect(find.text('Backtest #42 · BTCUSDm'), findsOneWidget);
      expect(tester.takeException(), isNull);
    },
  );
}
