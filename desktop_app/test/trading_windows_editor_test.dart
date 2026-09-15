import 'package:ez_trade_desktop/trading_windows_editor.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  testWidgets(
    'multiple windows retain independent timezones and can be edited',
    (tester) async {
      await tester.binding.setSurfaceSize(const Size(1000, 900));
      addTearDown(() => tester.binding.setSurfaceSize(null));
      var windows = <Map<String, dynamic>>[
        {
          'label': 'London',
          'timezone': 'Europe/London',
          'start': '08:00',
          'end': '11:00',
          'allowed_days': ['mon'],
        },
      ];
      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(
            body: StatefulBuilder(
              builder: (context, update) => TradingWindowsEditor(
                windows: windows,
                onChanged: (value) => update(() => windows = value),
              ),
            ),
          ),
        ),
      );
      await tester.tap(find.text('Add trading window'));
      await tester.pumpAndSettle();
      await tester.enterText(
        find.widgetWithText(TextFormField, 'Session name'),
        'New York',
      );
      await tester.enterText(
        find.widgetWithText(TextFormField, 'IANA timezone'),
        'America/New_York',
      );
      await tester.enterText(
        find.widgetWithText(TextFormField, 'Starts (HH:mm)'),
        '07:30',
      );
      await tester.enterText(
        find.widgetWithText(TextFormField, 'Ends (HH:mm)'),
        '14:00',
      );
      await tester.tap(find.text('Save window'));
      await tester.pumpAndSettle();
      expect(windows.length, 2);
      expect(windows[0]['timezone'], 'Europe/London');
      expect(windows[1]['timezone'], 'America/New_York');
      expect(windows[1]['start'], '07:30');
      await tester.tap(find.byTooltip('Remove window').first);
      await tester.pumpAndSettle();
      expect(windows.single['label'], 'New York');
      expect(tester.takeException(), isNull);
    },
  );
}
