import 'package:ez_trade_desktop/api_client.dart';
import 'package:ez_trade_desktop/main.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

class PositionsClient extends ApiClient {
  PositionsClient() : super('http://127.0.0.1');
  final rows = <Map<String, dynamic>>[
    {
      'id': 1,
      'broker_position_ticket': 3301519613,
      'ownership': 'ez_trade',
      'symbol': 'CLOSEDUSD',
      'side': 'sell',
      'volume': 0,
      'entry': 2668.35,
      'current_price': 2673.25,
      'sl': 2673.25,
      'opened_at': '2026-09-29T15:04:03Z',
      'closed_at': '2026-09-29T15:12:45Z',
      'status': 'closed',
      'manageable': false,
    },
    {'id': 2, 'symbol': 'OPENUSD', 'status': 'open', 'manageable': true},
    {'id': 3, 'symbol': 'MISSINGUSD', 'status': 'missing', 'manageable': false},
  ];
  int reads = 0;
  int clears = 0;
  bool failClear = false;
  bool failRead = false;

  @override
  Future<dynamic> get(String path) async {
    expect(path, '/api/personal/positions/');
    reads++;
    if (failRead) throw const ApiException('Cannot load positions');
    return [for (final row in rows) Map<String, dynamic>.from(row)];
  }

  @override
  Future<dynamic> post(String path, [Map<String, dynamic>? body]) async {
    expect(path, '/api/personal/positions/');
    expect(body, {'action': 'clear_closed'});
    clears++;
    if (failClear) throw const ApiException('Cannot clear positions');
    final count = rows.where((row) => row['status'] == 'closed').length;
    rows.removeWhere((row) => row['status'] == 'closed');
    return {'cleared': count};
  }
}

Future<void> showPositions(WidgetTester tester, PositionsClient client) async {
  await tester.binding.setSurfaceSize(const Size(1400, 900));
  addTearDown(() => tester.binding.setSurfaceSize(null));
  await tester.pumpWidget(
    MaterialApp(
      home: Scaffold(body: PositionsPage(client: client)),
    ),
  );
  await tester.pumpAndSettle();
}

void main() {
  testWidgets(
    'clear closed confirms, reloads, and retains active and missing tickets',
    (tester) async {
      final client = PositionsClient();
      await showPositions(tester, client);
      expect(find.text('Closed'), findsOneWidget);
      expect(
        find.text(
          'Opened (local)  ${formatDateTime(client.rows.first['opened_at'], includeSeconds: true)}',
        ),
        findsOneWidget,
      );
      expect(
        find.text(
          'Closed (local)  ${formatDateTime(client.rows.first['closed_at'], includeSeconds: true)}',
        ),
        findsOneWidget,
      );
      expect(find.text('Opened (local)  Date unavailable'), findsNWidgets(2));
      expect(find.byTooltip('Close ticket'), findsOneWidget);
      expect(find.byTooltip('Awaiting broker confirmation'), findsOneWidget);
      await tester.tap(find.text('Clear closed'));
      await tester.pumpAndSettle();
      expect(client.clears, 0);
      await tester.tap(find.text('Confirm'));
      await tester.pumpAndSettle();
      expect(client.clears, 1);
      expect(find.text('CLOSEDUSD'), findsNothing);
      expect(find.text('OPENUSD'), findsOneWidget);
      expect(find.text('MISSINGUSD'), findsOneWidget);
      expect(
        tester
            .widget<OutlinedButton>(
              find.widgetWithText(OutlinedButton, 'Clear closed'),
            )
            .onPressed,
        isNull,
      );
      await tester.pumpWidget(const SizedBox());
    },
  );

  testWidgets('cancel and failed clear retain closed entries', (tester) async {
    final client = PositionsClient()..failClear = true;
    await showPositions(tester, client);
    await tester.tap(find.text('Clear closed'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Cancel'));
    await tester.pumpAndSettle();
    expect(client.clears, 0);
    expect(find.text('CLOSEDUSD'), findsOneWidget);
    await tester.tap(find.text('Clear closed'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Confirm'));
    await tester.pumpAndSettle();
    expect(find.text('Cannot clear positions'), findsOneWidget);
    expect(find.text('CLOSEDUSD'), findsOneWidget);
    await tester.pumpWidget(const SizedBox());
  });

  testWidgets(
    'reload recovers errors and polling updates positions until disposed',
    (tester) async {
      final client = PositionsClient()..failRead = true;
      await showPositions(tester, client);
      expect(find.text('Cannot load positions'), findsOneWidget);
      client.failRead = false;
      await tester.tap(find.text('Reload positions'));
      await tester.pumpAndSettle();
      expect(find.text('CLOSEDUSD'), findsOneWidget);
      client.rows.clear();
      await tester.pump(const Duration(seconds: 15));
      await tester.pumpAndSettle();
      expect(find.text('No positions to display.'), findsOneWidget);
      final reads = client.reads;
      await tester.pumpWidget(const SizedBox());
      await tester.pump(const Duration(seconds: 30));
      expect(client.reads, reads);
      expect(tester.takeException(), isNull);
    },
  );
}
