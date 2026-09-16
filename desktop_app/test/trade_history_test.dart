import 'dart:async';
import 'package:ez_trade_desktop/api_client.dart';
import 'package:ez_trade_desktop/main.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

class HistoryClient extends ApiClient {
  HistoryClient() : super('http://127.0.0.1');
  final requests = <Uri>[];
  final baselines = <Map<String, dynamic>>[];
  final posts = <Map<String, dynamic>>[];
  Completer<dynamic>? pending;

  Map<String, dynamic> result(Uri uri) => {
    'summary': {'total_trades': 0, 'wins': 0, 'losses': 0, 'net_profit': 0},
    'options': {
      'bots': [
        {'id': 10, 'name': 'Gold demo'},
      ],
      'symbols': ['XAUUSD'],
      'strategies': ['trend_pullback'],
      'preset_versions': ['3', 'unknown'],
    },
    'baselines': baselines,
    'baseline': uri.queryParameters.containsKey('baseline_id')
        ? baselines.first
        : null,
    'page': 1,
    'total_pages': 2,
    'trades': [],
    'strategy_breakdown': [],
  };

  @override
  Future<dynamic> get(String path) async {
    if (path == '/api/personal/accounts/') {
      return [
        {'id': 2, 'name': 'Demo MT5'},
        {'id': 3, 'name': 'Other MT5'},
      ];
    }
    final uri = Uri.parse(path);
    requests.add(uri);
    if (pending != null) return pending!.future;
    return result(uri);
  }

  @override
  Future<dynamic> post(String path, [Map<String, dynamic>? body]) async {
    expect(path, '/api/personal/history/baselines/');
    posts.add(body!);
    final saved = {
      'id': 7,
      'name': body['name'],
      'filters': body['filters'],
      'started_at': '2026-09-15T15:00:00Z',
    };
    baselines.add(saved);
    return saved;
  }
}

Future<void> showHistory(WidgetTester tester, HistoryClient client) async {
  await tester.binding.setSurfaceSize(const Size(1200, 1000));
  addTearDown(() => tester.binding.setSurfaceSize(null));
  await tester.pumpWidget(
    MaterialApp(
      theme: ThemeData.dark(),
      home: Scaffold(body: HistoryPage(client: client)),
    ),
  );
  await tester.pumpAndSettle();
}

void main() {
  testWidgets('Gold filtering and pagination preserve account and scope', (
    tester,
  ) async {
    final client = HistoryClient();
    await showHistory(tester, client);
    await tester.tap(find.widgetWithText(ChoiceChip, 'Gold'));
    await tester.pumpAndSettle();
    expect(client.requests.last.queryParameters['market'], 'gold');
    expect(client.requests.last.queryParameters['broker_account_id'], '2');
    await tester.scrollUntilVisible(
      find.byTooltip('Next page'),
      300,
      scrollable: find.byType(Scrollable).first,
    );
    await tester.tap(find.byTooltip('Next page'));
    await tester.pumpAndSettle();
    expect(client.requests.last.queryParameters['page'], '2');
    expect(client.requests.last.queryParameters['market'], 'gold');
    expect(tester.takeException(), isNull);
  });

  testWidgets(
    'saving a baseline captures filters and never sends bot controls',
    (tester) async {
      final client = HistoryClient();
      await showHistory(tester, client);
      await tester.tap(find.widgetWithText(ChoiceChip, 'Gold'));
      await tester.pumpAndSettle();
      await tester.tap(find.widgetWithText(OutlinedButton, 'Save baseline'));
      await tester.pumpAndSettle();
      await tester.enterText(
        find.widgetWithText(TextField, 'Baseline name'),
        'Gold v3 demo',
      );
      await tester.enterText(
        find.widgetWithText(TextField, 'Start time (UTC)'),
        '2026-09-15T15:00:00Z',
      );
      await tester.tap(find.widgetWithText(FilledButton, 'Save baseline'));
      await tester.pumpAndSettle();
      expect(client.posts.single['filters'], {'market': 'gold'});
      expect(client.posts.single['broker_account_id'], '2');
      expect(client.requests.last.queryParameters['baseline_id'], '7');
      expect(find.textContaining('Entries from 2026-09-15'), findsOneWidget);
      expect(
        tester
            .widget<ChoiceChip>(find.widgetWithText(ChoiceChip, 'BTC'))
            .onSelected,
        isNull,
      );
      expect(tester.takeException(), isNull);
    },
  );

  testWidgets('loading new filters hides stale performance results', (
    tester,
  ) async {
    final client = HistoryClient();
    await showHistory(tester, client);
    client.pending = Completer<dynamic>();
    await tester.tap(find.widgetWithText(ChoiceChip, 'BTC'));
    await tester.pump();
    expect(find.byType(LinearProgressIndicator), findsOneWidget);
    expect(find.text('NET P/L'), findsNothing);
    client.pending!.complete(client.result(client.requests.last));
    await tester.pumpAndSettle();
    expect(find.text('NET P/L'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  testWidgets('history filters fit a narrow window', (tester) async {
    final client = HistoryClient();
    await showHistory(tester, client);
    await tester.binding.setSurfaceSize(const Size(420, 900));
    await tester.pumpAndSettle();
    await tester.scrollUntilVisible(
      find.text('No closed trades in this selection'),
      300,
      scrollable: find.byType(Scrollable).first,
    );
    expect(tester.takeException(), isNull);
  });
}
