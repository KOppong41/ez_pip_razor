import 'dart:async';
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
  HistoricalClient({
    this.failRun = false,
    this.noTrades = false,
    this.failPreview = false,
    this.missingDefaults = false,
    this.defaultsResponse,
  }) : super('http://localhost');
  final bool failRun;
  final bool noTrades;
  final bool failPreview;
  final bool missingDefaults;
  final Future<Map<String, dynamic>>? defaultsResponse;
  Map<String, dynamic>? previewed;
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
          {
            'id': 4,
            'name': 'Forex demo',
            'symbol': 'EURUSD',
            'timeframe': '5m',
            'quantity': '0.02',
          },
        ],
        'strategies': ['trend_pullback', 'momentum_ignition'],
        'timeframes': ['1m', '5m'],
      };
    }
    if (path == '/api/personal/backtests/42/') {
      return savedRun(noTrades: noTrades);
    }
    if (path.startsWith('/api/personal/backtests/defaults/')) {
      if (defaultsResponse != null) return await defaultsResponse!;
      return {
        'values': missingDefaults
            ? {}
            : {
                'contract_size': path.endsWith('/4/') ? '100000' : '1',
                'point_size': path.endsWith('/4/') ? '0.00001' : '0.01',
                'currency': 'USD',
                'spread_points': '50',
              },
        'source': missingDefaults ? 'unavailable' : 'broker_snapshot',
        'message': missingDefaults
            ? 'Broker specifications unavailable. Unknown sizes are left blank.'
            : 'Defaults from connected broker. Spread is a snapshot.',
        'as_of': missingDefaults ? null : '2025-01-08T12:00:00Z',
      };
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
    if (path == '/api/personal/backtests/preview/') {
      previewed = body;
      if (failPreview) {
        throw const ApiException(
          'Provide at least 102 candles, including warmup.',
        );
      }
      return {
        'bars': 3000,
        'first_at': '2025-01-06T23:00:00Z',
        'last_at': '2025-01-09T00:59:00Z',
        'first_tradable_at': '2025-01-07T00:40:00Z',
        'start_date': '2025-01-07',
        'end_date': '2025-01-09',
        'warmup': 100,
        'gap_count': 0,
      };
    }
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

Uint8List testBomlessUtf16CsvBytes() => Uint8List.fromList([
  for (final code
      in '<DATE>\t<TIME>\t<OPEN>\t<HIGH>\t<LOW>\t<CLOSE>\t<TICKVOL>\r\n'
              '2025.01.06\t10:00:00\t100\t102\t98\t100\t80'
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
  testWidgets('decodes a BOM-less UTF-16 MT5 export without embedded NULs', (
    tester,
  ) async {
    final client = HistoricalClient();
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: BacktestingPage(
            client: client,
            pickCsv: () async => XFile.fromData(
              testBomlessUtf16CsvBytes(),
              name: 'mt5-no-bom.csv',
            ),
          ),
        ),
      ),
    );
    await tester.pumpAndSettle();
    await tester.tap(find.text('Import candle CSV'));
    await tester.pumpAndSettle();
    final decoded = '${client.previewed?['csv']}';
    expect(decoded, startsWith('<DATE>\t<TIME>\t<OPEN>'));
    expect(decoded, isNot(contains('\u0000')));
    expect(decoded, contains('2025.01.06\t10:00:00'));
  });

  testWidgets('switching bots resets symbol-specific defaults', (tester) async {
    await mount(tester, HistoricalClient());
    await tester.ensureVisible(field('Contract size per lot'));
    await tester.enterText(field('Contract size per lot'), '123');
    await tester.ensureVisible(find.text('Bot / instrument'));
    await tester.tap(find.text('Bitcoin demo · BTCUSDm'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Forex demo · EURUSD').last);
    await tester.pumpAndSettle();
    expect(
      tester
          .widget<TextFormField>(field('Contract size per lot'))
          .controller!
          .text,
      '100000',
    );
    expect(
      tester.widget<TextFormField>(field('Point size')).controller!.text,
      '0.00001',
    );
    expect(
      tester
          .widget<TextFormField>(field('Fixed quantity (lots)'))
          .controller!
          .text,
      '0.02',
    );
    expect(tester.takeException(), isNull);
  });

  testWidgets('late defaults do not overwrite manual input', (tester) async {
    final response = Completer<Map<String, dynamic>>();
    await mount(tester, HistoricalClient(defaultsResponse: response.future));
    await tester.ensureVisible(field('Contract size per lot'));
    await tester.enterText(field('Contract size per lot'), '25');
    response.complete({
      'values': {'contract_size': '1', 'point_size': '0.01', 'currency': 'USD'},
      'message': 'Read from broker',
    });
    await tester.pumpAndSettle();
    expect(
      tester
          .widget<TextFormField>(field('Contract size per lot'))
          .controller!
          .text,
      '25',
    );
    expect(
      tester.widget<TextFormField>(field('Point size')).controller!.text,
      '0.01',
    );
    expect(tester.takeException(), isNull);
  });

  testWidgets(
    'CSV dates default after warmup and use bounded calendar selectors',
    (tester) async {
      final client = HistoricalClient();
      await mount(tester, client);
      final startFinder = find.byKey(const ValueKey('backtest-start_date'));
      final endFinder = find.byKey(const ValueKey('backtest-end_date'));
      expect(tester.widget<TextFormField>(startFinder).enabled, isFalse);
      await tester.tap(find.text('Import candle CSV'));
      await tester.pumpAndSettle();
      expect(
        tester.widget<TextFormField>(startFinder).controller!.text,
        '2025-01-07',
      );
      expect(
        tester.widget<TextFormField>(endFinder).controller!.text,
        '2025-01-09',
      );
      await tester.ensureVisible(startFinder);
      await tester.tap(startFinder);
      await tester.pumpAndSettle();
      final picker = tester.widget<DatePickerDialog>(
        find.byType(DatePickerDialog),
      );
      expect(picker.firstDate, DateTime(2025, 1, 7));
      expect(picker.lastDate, DateTime(2025, 1, 9));
      await tester.tap(find.text('8'));
      await tester.tap(find.text('OK'));
      await tester.pumpAndSettle();
      expect(
        tester.widget<TextFormField>(startFinder).controller!.text,
        '2025-01-08',
      );
      await tester.ensureVisible(find.text('Run historical backtest'));
      await tester.tap(find.text('Run historical backtest'));
      await tester.pumpAndSettle();
      expect(client.submitted?['start_date'], '2025-01-08');
      expect(client.submitted?['end_date'], '2025-01-09');
      expect(client.submitted?['contract_size'], '1');
      expect(client.submitted?['spread_points'], '50');
      expect(tester.takeException(), isNull);
    },
  );

  testWidgets('rejects reversed dates before starting a simulation', (
    tester,
  ) async {
    final client = HistoricalClient();
    await mount(tester, client);
    await tester.tap(find.text('Import candle CSV'));
    await tester.pumpAndSettle();
    tester
            .widget<TextFormField>(
              find.byKey(const ValueKey('backtest-start_date')),
            )
            .controller!
            .text =
        '2025-01-09';
    tester
            .widget<TextFormField>(
              find.byKey(const ValueKey('backtest-end_date')),
            )
            .controller!
            .text =
        '2025-01-07';
    await tester.ensureVisible(find.text('Run historical backtest'));
    await tester.tap(find.text('Run historical backtest'));
    await tester.pumpAndSettle();
    expect(
      find.text('End date must be on or after start date'),
      findsOneWidget,
    );
    expect(client.submitted, isNull);
  });

  testWidgets('invalid CSV has no fabricated default dates', (tester) async {
    await mount(tester, HistoricalClient(failPreview: true));
    await tester.tap(find.text('Import candle CSV'));
    await tester.pumpAndSettle();
    expect(find.textContaining('Provide at least 102 candles'), findsOneWidget);
    final start = tester.widget<TextFormField>(
      find.byKey(const ValueKey('backtest-start_date')),
    );
    expect(start.enabled, isFalse);
    expect(start.controller!.text, isEmpty);
    expect(tester.takeException(), isNull);
  });

  testWidgets(
    'missing specs stay explicit and changing timezone invalidates CSV dates',
    (tester) async {
      await mount(tester, HistoricalClient(missingDefaults: true));
      expect(
        tester
            .widget<TextFormField>(field('Contract size per lot'))
            .controller!
            .text,
        isEmpty,
      );
      expect(
        tester.widget<TextFormField>(field('Point size')).controller!.text,
        isEmpty,
      );
      expect(
        find.textContaining('Unknown sizes are left blank'),
        findsOneWidget,
      );
      expect(
        find.textContaining('Default 0 assumes perfect fills'),
        findsOneWidget,
      );
      await tester.tap(find.text('Import candle CSV'));
      await tester.pumpAndSettle();
      await tester.ensureVisible(field('CSV UTC offset (minutes)'));
      await tester.enterText(field('CSV UTC offset (minutes)'), '120');
      await tester.pumpAndSettle();
      final start = tester.widget<TextFormField>(
        find.byKey(const ValueKey('backtest-start_date')),
      );
      expect(start.controller!.text, isEmpty);
      expect(start.enabled, isFalse);
      expect(tester.takeException(), isNull);
    },
  );

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
