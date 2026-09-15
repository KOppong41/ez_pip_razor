import 'package:flutter/material.dart';

class TradingWindowsEditor extends StatelessWidget {
  const TradingWindowsEditor({
    super.key,
    required this.windows,
    required this.onChanged,
  });
  final List<Map<String, dynamic>> windows;
  final ValueChanged<List<Map<String, dynamic>>> onChanged;

  Future<void> edit(BuildContext context, int? index) async {
    final window = index == null
        ? <String, dynamic>{
            'label': 'Session',
            'timezone': 'Europe/London',
            'start': '08:00',
            'end': '11:00',
            'allowed_days': ['mon', 'tue', 'wed', 'thu', 'fri'],
          }
        : Map<String, dynamic>.from(windows[index]);
    final days = List<String>.from(window['allowed_days'] as List? ?? []);
    String? error;
    final result = await showDialog<Map<String, dynamic>>(
      context: context,
      builder: (context) => StatefulBuilder(
        builder: (context, update) => AlertDialog(
          title: const Text('Trading window'),
          content: SizedBox(
            width: 360,
            child: SingleChildScrollView(
              child: Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  for (final field in [
                    ('label', 'Session name'),
                    ('timezone', 'IANA timezone'),
                    ('start', 'Starts (HH:mm)'),
                    ('end', 'Ends (HH:mm)'),
                  ])
                    Padding(
                      padding: const EdgeInsets.only(bottom: 12),
                      child: TextFormField(
                        initialValue: '${window[field.$1] ?? ''}',
                        decoration: InputDecoration(labelText: field.$2),
                        onChanged: (value) => window[field.$1] = value.trim(),
                      ),
                    ),
                  Wrap(
                    spacing: 4,
                    children: [
                      for (final day in [
                        'mon',
                        'tue',
                        'wed',
                        'thu',
                        'fri',
                        'sat',
                        'sun',
                      ])
                        FilterChip(
                          label: Text(day.toUpperCase()),
                          selected: days.contains(day),
                          onSelected: (value) => update(() {
                            if (value) {
                              days.add(day);
                            } else {
                              days.remove(day);
                            }
                          }),
                        ),
                    ],
                  ),
                  if (error != null)
                    Text(error!, style: const TextStyle(color: Colors.red)),
                ],
              ),
            ),
          ),
          actions: [
            TextButton(
              onPressed: () => Navigator.pop(context),
              child: const Text('Cancel'),
            ),
            FilledButton(
              onPressed: () {
                final time = RegExp(r'^([01]\d|2[0-3]):[0-5]\d$');
                if (!time.hasMatch('${window['start']}') ||
                    !time.hasMatch('${window['end']}') ||
                    '${window['timezone']}'.isEmpty ||
                    days.isEmpty) {
                  update(
                    () => error =
                        'Enter HH:mm times, a timezone and at least one day.',
                  );
                  return;
                }
                window['allowed_days'] = days;
                Navigator.pop(context, window);
              },
              child: const Text('Save window'),
            ),
          ],
        ),
      ),
    );
    if (result == null) return;
    final next = [...windows];
    if (index == null) {
      next.add(result);
    } else {
      next[index] = result;
    }
    onChanged(next);
  }

  @override
  Widget build(BuildContext context) => Column(
    crossAxisAlignment: CrossAxisAlignment.start,
    children: [
      for (var i = 0; i < windows.length; i++)
        ListTile(
          contentPadding: EdgeInsets.zero,
          title: Text(
            '${windows[i]['label'] ?? 'Session'} · ${windows[i]['start']}–${windows[i]['end']}',
          ),
          subtitle: Text(
            '${windows[i]['timezone']} · ${(windows[i]['allowed_days'] as List? ?? []).join(', ')}',
          ),
          onTap: () => edit(context, i),
          trailing: IconButton(
            tooltip: 'Remove window',
            icon: const Icon(Icons.delete_outline),
            onPressed: () => onChanged([...windows]..removeAt(i)),
          ),
        ),
      if (windows.length < 8)
        TextButton.icon(
          onPressed: () => edit(context, null),
          icon: const Icon(Icons.add),
          label: const Text('Add trading window'),
        ),
      if (windows.isNotEmpty)
        const Text(
          'Entries are allowed in any listed window. Each timezone follows its own daylight-saving rules.',
        ),
    ],
  );
}
