import 'dart:async';

import 'package:ez_trade_desktop/api_client.dart';
import 'package:ez_trade_desktop/main.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

class RefreshClient extends ApiClient {
  RefreshClient() : super('http://127.0.0.1');

  List<Map<String, dynamic>> markets = [
    {'canonical_symbol': 'OLDUSD', 'enabled': true, 'trading_status': 'open'},
  ];
  List<Map<String, dynamic>> orders = [
    {
      'id': 1,
      'symbol': 'ETHUSDm',
      'client_order_id': 'eth-first',
      'status': 'filled',
      'intent': 'entry',
      'side': 'buy',
      'qty': '0.1',
    },
  ];
  Completer<dynamic>? delayedMarketResponse;
  int marketReads = 0;
  int orderReads = 0;

  @override
  Future<dynamic> get(String path) {
    if (path == '/api/personal/markets/') {
      marketReads++;
      final delayed = delayedMarketResponse;
      if (delayed != null) {
        delayedMarketResponse = null;
        return delayed.future;
      }
      return Future.value([
        for (final row in markets) Map<String, dynamic>.from(row),
      ]);
    }
    if (path == '/api/orders/') {
      orderReads++;
      return Future.value([
        for (final row in orders) Map<String, dynamic>.from(row),
      ]);
    }
    throw StateError('Unexpected request: $path');
  }
}

Future<void> showPage(WidgetTester tester, Widget page) async {
  await tester.binding.setSurfaceSize(const Size(1400, 900));
  addTearDown(() => tester.binding.setSurfaceSize(null));
  await tester.pumpWidget(MaterialApp(home: Scaffold(body: page)));
  await tester.pumpAndSettle();
}

void main() {
  testWidgets(
    'markets reflect catalogue changes and stop polling after disposal',
    (tester) async {
      final client = RefreshClient();
      await showPage(tester, MarketsPage(client: client));
      expect(find.text('OLDUSD'), findsOneWidget);

      final delayed = Completer<dynamic>();
      client.delayedMarketResponse = delayed;
      await tester.pump(const Duration(seconds: 15));
      expect(client.marketReads, 2);
      expect(client.delayedMarketResponse, isNull);
      expect(delayed.isCompleted, isFalse);
      expect(find.text('OLDUSD'), findsOneWidget);
      expect(find.byType(CircularProgressIndicator), findsNothing);
      await tester.pump(const Duration(seconds: 15));
      expect(client.marketReads, 2);

      delayed.complete([
        {
          'canonical_symbol': 'NEWUSD',
          'enabled': true,
          'trading_status': 'open',
        },
      ]);
      await tester.pumpAndSettle();
      expect(find.text('OLDUSD'), findsNothing);
      expect(find.text('NEWUSD'), findsOneWidget);

      await tester.pumpWidget(const SizedBox());
      await tester.pump(const Duration(seconds: 30));
      expect(client.marketReads, 2);
    },
  );

  testWidgets('failed background refresh keeps the last good market list', (
    tester,
  ) async {
    final client = RefreshClient();
    await showPage(tester, MarketsPage(client: client));
    final delayed = Completer<dynamic>();
    client.delayedMarketResponse = delayed;

    await tester.pump(const Duration(seconds: 15));
    delayed.completeError(StateError('Temporary outage'));
    await tester.pumpAndSettle();
    expect(find.text('OLDUSD'), findsOneWidget);
    expect(find.textContaining('Temporary outage'), findsNothing);

    client.markets = [
      {'canonical_symbol': 'NEWUSD', 'enabled': true, 'trading_status': 'open'},
    ];
    await tester.pump(const Duration(seconds: 15));
    await tester.pumpAndSettle();
    expect(find.text('NEWUSD'), findsOneWidget);
    await tester.pumpWidget(const SizedBox());
  });

  testWidgets('orders refresh while preserving the active search filter', (
    tester,
  ) async {
    final client = RefreshClient();
    await showPage(tester, OrdersPage(client: client));
    await tester.enterText(find.byType(TextField), 'ETH');
    await tester.pump();

    client.orders.addAll([
      {
        'id': 2,
        'symbol': 'BTCUSDm',
        'client_order_id': 'btc-second',
        'status': 'filled',
        'intent': 'entry',
        'side': 'buy',
        'qty': '0.01',
      },
      {
        'id': 3,
        'symbol': 'ETHUSDm',
        'client_order_id': 'eth-third',
        'status': 'new',
        'intent': 'entry',
        'side': 'sell',
        'qty': '0.1',
      },
    ]);
    await tester.pump(const Duration(seconds: 15));
    await tester.pumpAndSettle();

    expect(client.orderReads, 2);
    expect(find.text('ETHUSDm'), findsNWidgets(2));
    expect(find.text('BTCUSDm'), findsNothing);
    expect(find.textContaining('eth-third'), findsOneWidget);
    await tester.pumpWidget(const SizedBox());
  });
}
