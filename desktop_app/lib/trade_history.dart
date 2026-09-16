part of 'main.dart';

class HistoryPage extends StatefulWidget {
  const HistoryPage({super.key, required this.client});
  final ApiClient client;
  @override
  State<HistoryPage> createState() => _HistoryPageState();
}

class _HistoryPageState extends State<HistoryPage> {
  List<Map<String, dynamic>> accounts = [];
  String? accountId;
  String? baselineId;
  final filters = <String, String>{};
  String? from;
  String? to;
  int page = 1;
  late Future<dynamic> future = load();

  Future<dynamic> load() async {
    if (accounts.isEmpty) {
      accounts = listOfMaps(await widget.client.get('/api/personal/accounts/'));
      if (accounts.isEmpty) {
        throw const ApiException('Connect an MT5 account to view history.');
      }
      accountId ??= '${accounts.first['id']}';
    }
    final query = <String, String>{
      'broker_account_id': accountId!,
      ...filters,
      'page': '$page',
      'baseline_id': ?baselineId,
      'from': ?from,
      'to': ?to,
    };
    return widget.client.get(
      Uri(path: '/api/personal/history/', queryParameters: query).toString(),
    );
  }

  Future<void> reload({bool resetPage = false}) {
    if (resetPage) page = 1;
    final next = load();
    setState(() {
      future = next;
    });
    return next.then<void>((_) {}, onError: (Object _) {});
  }

  void setFilter(String key, String? value) {
    if (value == null || value.isEmpty) {
      filters.remove(key);
    } else {
      filters[key] = value;
    }
    reload(resetPage: true);
  }

  Future<void> pickDate(bool isFrom) async {
    final current = isFrom ? from : to;
    final day = await showDatePicker(
      context: context,
      initialDate: DateTime.tryParse(current ?? '') ?? DateTime.now(),
      firstDate: DateTime(2000),
      lastDate: DateTime.now(),
    );
    if (day == null || !mounted) return;
    final value =
        '${day.year}-${day.month.toString().padLeft(2, '0')}-${day.day.toString().padLeft(2, '0')}';
    if (isFrom) {
      from = value;
    } else {
      to = value;
    }
    reload(resetPage: true);
  }

  Future<void> createBaseline() async {
    final values = await showDialog<Map<String, String>>(
      context: context,
      builder: (context) => const _HistoryBaselineDialog(),
    );
    if (values == null || !mounted) return;
    try {
      final saved = mapOf(
        await widget.client.post('/api/personal/history/baselines/', {
          'broker_account_id': accountId,
          'filters': Map<String, String>.from(filters),
          ...values,
        }),
      );
      if (!mounted) return;
      baselineId = '${saved['id']}';
      from = null;
      to = null;
      await reload(resetPage: true);
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(
          context,
        ).showSnackBar(SnackBar(content: Text('$e')));
      }
    }
  }

  Widget dropdown(
    String title,
    String? value,
    Map<String, String> options,
    void Function(String?) changed, {
    bool enabled = true,
  }) {
    options = {
      ...options,
      if (value != null && value.isNotEmpty && !options.containsKey(value))
        value: value,
    };
    return SizedBox(
      width: 205,
      child: DropdownButtonFormField<String>(
        key: ValueKey('$title:$value'),
        initialValue: options.containsKey(value) ? value : '',
        isExpanded: true,
        decoration: InputDecoration(labelText: title),
        items: [
          for (final item in options.entries)
            DropdownMenuItem(
              value: item.key,
              child: Text(item.value, overflow: TextOverflow.ellipsis),
            ),
        ],
        onChanged: enabled ? changed : null,
      ),
    );
  }

  @override
  Widget build(BuildContext context) => FutureBuilder<dynamic>(
    future: future,
    builder: (context, snapshot) {
      final root = mapOf(snapshot.data);
      final options = mapOf(root['options']);
      final baselines = listOfMaps(root['baselines']);
      final baseline = mapOf(root['baseline']);
      final stats = mapOf(root['summary']);
      final overlay = mapOf(root['opposite_scalp']);
      final trades = listOfMaps(root['trades']);
      final quality = mapOf(root['data_quality']);
      final busy = snapshot.connectionState != ConnectionState.done;
      Map<String, String> choices(String key, String all) => {
        '': all,
        for (final item in (options[key] as List? ?? []))
          '$item': item == 'unknown' ? 'Unknown / not recorded' : '$item',
      };
      final metrics = [
        ('TRADES', '${integerValue(stats['total_trades']) ?? 0}', blue),
        ('WINS', '${integerValue(stats['wins']) ?? 0}', green),
        ('LOSSES', '${integerValue(stats['losses']) ?? 0}', danger),
        ('WIN RATE', optionalPercent(stats['win_rate']), green),
        ('GROSS PROFIT', compactNumber(stats['gross_profit']), green),
        ('GROSS LOSS', compactNumber(stats['gross_loss']), danger),
        (
          'NET P/L',
          compactNumber(stats['net_profit']),
          valueColor(stats['net_profit']),
        ),
        ('PROFIT FACTOR', compactNumber(stats['profit_factor']), amber),
      ];
      return RefreshIndicator(
        onRefresh: reload,
        child: ListView(
          padding: const EdgeInsets.all(24),
          children: [
            _WorkspaceHeader(
              eyebrow: 'PERFORMANCE LEDGER',
              title: 'Trade history',
              description:
                  'Filtered closed outcomes across the complete recorded history.',
              badge: baseline.isEmpty
                  ? 'FILTERED HISTORY'
                  : 'PERFORMANCE SINCE BASELINE',
              action: OutlinedButton.icon(
                onPressed: busy ? null : reload,
                icon: const Icon(Icons.refresh),
                label: const Text('Reload history'),
              ),
            ),
            const SizedBox(height: 16),
            Wrap(
              spacing: 8,
              runSpacing: 8,
              children: [
                for (final market in {
                  'all': 'All',
                  'gold': 'Gold',
                  'btc': 'BTC',
                  'eth': 'ETH',
                  'forex': 'Forex',
                }.entries)
                  ChoiceChip(
                    label: Text(market.value),
                    selected: (filters['market'] ?? 'all') == market.key,
                    onSelected: busy || baselineId != null
                        ? null
                        : (_) {
                            filters.remove('symbol');
                            setFilter(
                              'market',
                              market.key == 'all' ? null : market.key,
                            );
                          },
                  ),
              ],
            ),
            const SizedBox(height: 16),
            Wrap(
              spacing: 12,
              runSpacing: 16,
              children: [
                dropdown(
                  'Account',
                  accountId,
                  {
                    for (final account in accounts)
                      '${account['id']}': '${account['name']}',
                  },
                  (value) {
                    accountId = value;
                    baselineId = null;
                    filters.clear();
                    from = null;
                    to = null;
                    reload(resetPage: true);
                  },
                  enabled: !busy,
                ),
                dropdown(
                  'Bot',
                  filters['bot_id'],
                  {
                    '': 'All bots',
                    for (final bot in listOfMaps(options['bots']))
                      '${bot['id']}': '${bot['name']}',
                  },
                  (value) => setFilter('bot_id', value),
                  enabled: !busy && baselineId == null,
                ),
                dropdown(
                  'Symbol',
                  filters['symbol'],
                  choices('symbols', 'All symbols'),
                  (value) {
                    filters.remove('market');
                    setFilter('symbol', value);
                  },
                  enabled: !busy && baselineId == null,
                ),
                dropdown(
                  'Strategy',
                  filters['strategy'],
                  choices('strategies', 'All strategies'),
                  (value) => setFilter('strategy', value),
                  enabled: !busy && baselineId == null,
                ),
                dropdown(
                  'Preset version',
                  filters['preset_version'],
                  choices('preset_versions', 'All presets'),
                  (value) => setFilter('preset_version', value),
                  enabled: !busy && baselineId == null,
                ),
                dropdown(
                  'Baseline',
                  baselineId,
                  {
                    '': 'All history',
                    for (final item in baselines)
                      '${item['id']}': '${item['name']}',
                  },
                  (value) {
                    baselineId = value == '' ? null : value;
                    filters.clear();
                    from = null;
                    to = null;
                    if (baselineId != null) {
                      final saved = baselines.firstWhere(
                        (item) => '${item['id']}' == baselineId,
                      );
                      mapOf(
                        saved['filters'],
                      ).forEach((key, value) => filters[key] = '$value');
                    }
                    reload(resetPage: true);
                  },
                  enabled: !busy,
                ),
              ],
            ),
            const SizedBox(height: 16),
            Wrap(
              spacing: 12,
              runSpacing: 8,
              children: [
                OutlinedButton(
                  onPressed: busy ? null : () => pickDate(true),
                  child: Text('From: ${from ?? 'Any date'} (UTC)'),
                ),
                OutlinedButton(
                  onPressed: busy ? null : () => pickDate(false),
                  child: Text('To: ${to ?? 'Any date'} (UTC)'),
                ),
                TextButton(
                  onPressed: busy
                      ? null
                      : () {
                          from = null;
                          to = null;
                          reload(resetPage: true);
                        },
                  child: const Text('Clear dates'),
                ),
                OutlinedButton.icon(
                  onPressed: busy || accountId == null || baselineId != null
                      ? null
                      : createBaseline,
                  icon: const Icon(Icons.flag_outlined),
                  label: const Text('Save baseline'),
                ),
              ],
            ),
            if (busy)
              const Padding(
                padding: EdgeInsets.all(16),
                child: LinearProgressIndicator(),
              ),
            if (snapshot.hasError)
              Padding(
                padding: const EdgeInsets.all(16),
                child: Text(
                  '${snapshot.error}',
                  style: const TextStyle(color: danger),
                ),
              ),
            if (baseline.isNotEmpty && !busy)
              Padding(
                padding: const EdgeInsets.symmetric(vertical: 16),
                child: Text(
                  '${baseline['name']} · Entries from ${baseline['started_at']}\nClear the baseline to change its saved scope.',
                  style: const TextStyle(color: blue),
                ),
              ),
            if (snapshot.hasData && !snapshot.hasError && !busy) ...[
              const SizedBox(height: 16),
              LayoutBuilder(
                builder: (context, constraints) => GridView.builder(
                  shrinkWrap: true,
                  physics: const NeverScrollableScrollPhysics(),
                  gridDelegate: SliverGridDelegateWithFixedCrossAxisCount(
                    crossAxisCount: constraints.maxWidth >= 1100
                        ? 4
                        : constraints.maxWidth >= 560
                        ? 2
                        : 1,
                    crossAxisSpacing: 10,
                    mainAxisSpacing: 10,
                    mainAxisExtent: 74,
                  ),
                  itemCount: metrics.length,
                  itemBuilder: (context, index) => _HistoryMetricCard(
                    label: metrics[index].$1,
                    value: metrics[index].$2,
                    color: metrics[index].$3,
                  ),
                ),
              ),
              const SizedBox(height: 16),
              Text(
                'Opposite scalps in this selection: ${overlay['total_trades'] ?? 0} trades · WR ${optionalPercent(overlay['win_rate'])} · PF ${compactNumber(overlay['profit_factor'])} · Net ${compactNumber(overlay['net_profit'])}',
              ),
              const SizedBox(height: 16),
              Text(
                '${root['basis'] ?? ''}',
                style: const TextStyle(color: muted, fontSize: 12),
              ),
              Text(
                'Unknown preset: ${quality['unknown_preset_trades'] ?? 0} trades. Unverified historical completion: ${quality['unverified_completion_trades'] ?? 0}.',
                style: const TextStyle(color: muted, fontSize: 12),
              ),
              const SizedBox(height: 20),
              const Text(
                'PERFORMANCE BY STRATEGY',
                style: TextStyle(color: blue),
              ),
              SingleChildScrollView(
                scrollDirection: Axis.horizontal,
                child: DataTable(
                  columns: const [
                    DataColumn(label: Text('Symbol')),
                    DataColumn(label: Text('Strategy')),
                    DataColumn(label: Text('Trades')),
                    DataColumn(label: Text('Win rate')),
                    DataColumn(label: Text('PF')),
                    DataColumn(label: Text('Net P/L')),
                  ],
                  rows: [
                    for (final row in listOfMaps(root['strategy_breakdown']))
                      DataRow(
                        cells: [
                          DataCell(Text('${row['symbol']}')),
                          DataCell(Text(label('${row['strategy']}'))),
                          DataCell(Text('${row['total_trades']}')),
                          DataCell(Text(optionalPercent(row['win_rate']))),
                          DataCell(Text(compactNumber(row['profit_factor']))),
                          DataCell(Text(compactNumber(row['net_profit']))),
                        ],
                      ),
                  ],
                ),
              ),
              const SizedBox(height: 20),
              Row(
                children: [
                  const Expanded(
                    child: Text(
                      'CLOSED OUTCOMES',
                      style: TextStyle(color: blue),
                    ),
                  ),
                  IconButton(
                    tooltip: 'Previous page',
                    onPressed: busy || page <= 1
                        ? null
                        : () {
                            page--;
                            reload();
                          },
                    icon: const Icon(Icons.chevron_left),
                  ),
                  Text('Page $page / ${root['total_pages'] ?? 1}'),
                  IconButton(
                    tooltip: 'Next page',
                    onPressed:
                        busy || page >= (integerValue(root['total_pages']) ?? 1)
                        ? null
                        : () {
                            page++;
                            reload();
                          },
                    icon: const Icon(Icons.chevron_right),
                  ),
                ],
              ),
              if (trades.isEmpty)
                const _EmptyWorkspace(
                  icon: Icons.query_stats_rounded,
                  title: 'No closed trades in this selection',
                  text: 'Previous trades remain available under All history.',
                ),
              for (final trade in trades)
                Padding(
                  padding: const EdgeInsets.only(top: 8),
                  child: _TradeHistoryRow(trade: trade),
                ),
            ],
          ],
        ),
      );
    },
  );
}

class _HistoryBaselineDialog extends StatefulWidget {
  const _HistoryBaselineDialog();
  @override
  State<_HistoryBaselineDialog> createState() => _HistoryBaselineDialogState();
}

class _HistoryBaselineDialogState extends State<_HistoryBaselineDialog> {
  final name = TextEditingController();
  final start = TextEditingController();
  @override
  void dispose() {
    name.dispose();
    start.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => AlertDialog(
    title: const Text('Save performance baseline'),
    content: SizedBox(
      width: 440,
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          const Text(
            'Save the current market, bot, strategy and preset filters. Counts only entries opened from this time onward. Previous trades remain available. This does not start or change any bot.',
          ),
          const SizedBox(height: 16),
          TextField(
            controller: name,
            maxLength: 120,
            decoration: const InputDecoration(
              labelText: 'Baseline name',
              hintText: 'Gold demo baseline',
            ),
          ),
          TextField(
            controller: start,
            decoration: const InputDecoration(
              labelText: 'Start time (UTC)',
              hintText: 'Leave empty to start now',
              helperText: 'Optional: 2026-09-15T15:00:00Z',
            ),
          ),
        ],
      ),
    ),
    actions: [
      TextButton(
        onPressed: () => Navigator.pop(context),
        child: const Text('Cancel'),
      ),
      FilledButton(
        onPressed: () => Navigator.pop(context, {
          'name': name.text,
          'started_at': start.text,
        }),
        child: const Text('Save baseline'),
      ),
    ],
  );
}
