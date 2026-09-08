part of 'main.dart';

String _backtestUtc(dynamic value) {
  final at = DateTime.tryParse('$value')?.toUtc();
  if (at == null) return 'Unavailable';
  return at.toIso8601String().replaceFirst('T', ' ').substring(0, 19);
}

String _decodeCandleCsv(List<int> bytes) {
  if (bytes.length >= 2 &&
      ((bytes[0] == 0xff && bytes[1] == 0xfe) ||
          (bytes[0] == 0xfe && bytes[1] == 0xff))) {
    if (bytes.length.isOdd) {
      throw const FormatException('Incomplete UTF-16 CSV.');
    }
    final littleEndian = bytes[0] == 0xff;
    return String.fromCharCodes([
      for (var i = 2; i < bytes.length; i += 2)
        littleEndian
            ? bytes[i] | (bytes[i + 1] << 8)
            : (bytes[i] << 8) | bytes[i + 1],
    ]);
  }
  return utf8.decode(bytes);
}

class BacktestingPage extends StatelessWidget {
  const BacktestingPage({super.key, required this.client, this.pickCsv});
  final ApiClient client;
  final Future<XFile?> Function()? pickCsv;

  @override
  Widget build(BuildContext context) => DefaultTabController(
    length: 2,
    child: Column(
      children: [
        const TabBar(
          tabs: [
            Tab(text: 'Historical backtests'),
            Tab(text: 'Run evidence'),
          ],
        ),
        Expanded(
          child: TabBarView(
            children: [
              _HistoricalBacktests(client: client, pickCsv: pickCsv),
              RunEvidencePage(client: client),
            ],
          ),
        ),
      ],
    ),
  );
}

class _HistoricalBacktests extends StatefulWidget {
  const _HistoricalBacktests({required this.client, this.pickCsv});
  final ApiClient client;
  final Future<XFile?> Function()? pickCsv;

  @override
  State<_HistoricalBacktests> createState() => _HistoricalBacktestsState();
}

class _HistoricalBacktestsState extends State<_HistoricalBacktests>
    with AutomaticKeepAliveClientMixin {
  final _form = GlobalKey<FormState>();
  final _resultsKey = GlobalKey();
  final _scroll = ScrollController();
  final fields = <String, TextEditingController>{
    for (final entry in <String, String>{
      'quantity': '0.01',
      'contract_size': '',
      'point_size': '',
      'initial_balance': '10000',
      'currency': 'USD',
      'spread_points': '0',
      'slippage_points': '0',
      'commission_per_lot': '0',
      'start_date': '',
      'end_date': '',
      'warmup': '100',
      'min_score': '0',
      'csv_utc_offset_minutes': '0',
    }.entries)
      entry.key: TextEditingController(text: entry.value),
  };
  late Future<dynamic> options = _loadOptions();
  late Future<dynamic> history = widget.client.get('/api/personal/backtests/');
  String? botId;
  String strategy = 'trend_pullback';
  String timeframe = '1m';
  String sameBarPolicy = 'stop_first';
  String? csvText;
  String? sourceName;
  String? error;
  bool busy = false;
  int historyPage = 1;
  Map<String, dynamic>? selectedResult;
  Map<String, dynamic>? csvPreview;
  bool previewBusy = false;
  bool defaultsBusy = false;
  int previewRequest = 0;
  int defaultsRequest = 0;
  String defaultsMessage = 'Loading instrument defaults...';

  @override
  bool get wantKeepAlive => true;

  void _showError(String value) {
    if (!mounted) return;
    setState(() => error = value);
    if (_scroll.hasClients) {
      _scroll.animateTo(
        0,
        duration: const Duration(milliseconds: 200),
        curve: Curves.easeOut,
      );
    }
  }

  Future<dynamic> _loadOptions() async {
    final value = await widget.client.get('/api/personal/backtests/options/');
    final bots = listOfMaps(mapOf(value)['bots']);
    if (mounted && bots.isNotEmpty && botId == null) _selectBot(bots.first);
    return value;
  }

  void _selectBot(Map<String, dynamic> bot) {
    botId = '${bot['id']}';
    timeframe = '${bot['timeframe'] ?? '1m'}';
    fields['quantity']!.text = '${bot['quantity'] ?? '0.01'}';
    _invalidatePreview();
    _loadDefaults();
  }

  Future<void> _loadDefaults() async {
    final request = ++defaultsRequest;
    final selectedBot = botId;
    final baseline = <String, String>{
      'contract_size': '',
      'point_size': '',
      'currency': '',
      'spread_points': '0',
      'slippage_points': '0',
      'commission_per_lot': '0',
    };
    for (final entry in baseline.entries) {
      fields[entry.key]!.text = entry.value;
    }
    defaultsBusy = true;
    defaultsMessage = 'Loading instrument defaults...';
    try {
      final value = mapOf(
        await widget.client.get(
          '/api/personal/backtests/defaults/$selectedBot/',
        ),
      );
      if (!mounted || request != defaultsRequest) return;
      setState(() {
        for (final entry in mapOf(value['values']).entries) {
          // Preserve edits made while the optional request was in flight.
          if (baseline.containsKey(entry.key) &&
              fields[entry.key]!.text == baseline[entry.key]) {
            fields[entry.key]!.text = '${entry.value}';
          }
        }
        defaultsMessage =
            '${value['message'] ?? 'Review instrument specifications before running.'}';
        if (value['as_of'] != null) {
          defaultsMessage += ' As of ${_backtestUtc(value['as_of'])} UTC.';
        }
      });
    } catch (_) {
      if (!mounted || request != defaultsRequest) return;
      setState(
        () => defaultsMessage =
            'Defaults could not load. Copy Contract size, Point and Profit currency from MT5 Symbol Specification. You can still enter them manually.',
      );
    } finally {
      if (mounted && request == defaultsRequest) {
        setState(() => defaultsBusy = false);
      }
    }
  }

  void _invalidatePreview() {
    previewRequest++;
    csvPreview = null;
    previewBusy = false;
    fields['start_date']!.clear();
    fields['end_date']!.clear();
  }

  Future<bool> _previewCsv() async {
    if (csvText == null) return false;
    final request = ++previewRequest;
    setState(() {
      previewBusy = true;
      error = null;
    });
    try {
      final value = mapOf(
        await widget.client.post('/api/personal/backtests/preview/', {
          'csv': csvText,
          'timeframe': timeframe,
          'strategy': strategy,
          'warmup': fields['warmup']!.text.trim(),
          'csv_utc_offset_minutes': fields['csv_utc_offset_minutes']!.text
              .trim(),
        }),
      );
      if (!mounted || request != previewRequest) return false;
      setState(() {
        csvPreview = value;
        fields['start_date']!.text = '${value['start_date']}';
        fields['end_date']!.text = '${value['end_date']}';
      });
      return true;
    } catch (e) {
      if (mounted && request == previewRequest) {
        setState(() => csvPreview = null);
        _showError('CSV date range: $e');
      }
      return false;
    } finally {
      if (mounted && request == previewRequest) {
        setState(() => previewBusy = false);
      }
    }
  }

  @override
  void dispose() {
    for (final controller in fields.values) {
      controller.dispose();
    }
    _scroll.dispose();
    super.dispose();
  }

  Future<void> _importCsv() async {
    try {
      final file =
          await (widget.pickCsv?.call() ??
              openFile(
                acceptedTypeGroups: const [
                  XTypeGroup(
                    label: 'Candle CSV',
                    extensions: ['csv', 'tsv', 'txt'],
                  ),
                ],
              ));
      if (file == null || !mounted) return;
      if (await file.length() > 1000000) {
        throw const ApiException(
          'Choose a CSV smaller than 1 MB (up to 10,000 candles).',
        );
      }
      final contents = _decodeCandleCsv(await file.readAsBytes());
      if (!mounted) return;
      setState(() {
        _invalidatePreview();
        csvText = contents;
        sourceName = file.name;
        error = null;
      });
      await _previewCsv();
    } catch (e) {
      _showError('CSV import: $e');
    }
  }

  Future<void> _submit() async {
    if (csvText == null) {
      _showError('Choose a historical candle CSV before running.');
      return;
    }
    if (csvPreview == null && !await _previewCsv()) return;
    if (!mounted || !_form.currentState!.validate()) return;
    setState(() {
      busy = true;
      error = null;
    });
    try {
      final value = await widget.client.post('/api/personal/backtests/', {
        for (final entry in fields.entries) entry.key: entry.value.text.trim(),
        'bot_id': int.parse(botId!),
        'strategy': strategy,
        'timeframe': timeframe,
        'same_bar_policy': sameBarPolicy,
        'csv': csvText,
        'source_name': sourceName,
      });
      if (!mounted) return;
      setState(() {
        selectedResult = mapOf(value);
        historyPage = 1;
        history = widget.client.get('/api/personal/backtests/');
      });
      _revealResults();
    } catch (e) {
      _showError(e.toString());
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  void _revealResults() => WidgetsBinding.instance.addPostFrameCallback((_) {
    final target = _resultsKey.currentContext;
    if (mounted && target != null) {
      Scrollable.ensureVisible(
        target,
        duration: const Duration(milliseconds: 250),
      );
    }
  });

  Future<void> _openSaved(Map<String, dynamic> run) async {
    setState(() {
      busy = true;
      error = null;
    });
    try {
      final value = await widget.client.get(
        '/api/personal/backtests/${run['id']}/',
      );
      if (!mounted) return;
      setState(() => selectedResult = mapOf(value));
      _revealResults();
    } catch (e) {
      _showError(e.toString());
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  Widget _field(
    String key,
    String title, {
    String? hint,
    bool optional = false,
  }) => TextFormField(
    controller: fields[key],
    decoration: InputDecoration(
      labelText: title,
      helperText: hint,
      helperMaxLines: 5,
      errorMaxLines: 3,
    ),
    onChanged: (value) {
      if (key == 'warmup' || key == 'csv_utc_offset_minutes') {
        setState(_invalidatePreview);
      }
    },
    validator: (value) {
      final text = value?.trim() ?? '';
      if (text.isEmpty) return optional ? null : 'Required';
      if (key == 'currency') {
        if (!RegExp(r'^[a-zA-Z]{3}$').hasMatch(text)) {
          return 'Use a three-letter currency, e.g. USD';
        }
      } else {
        final number = double.tryParse(text);
        if (number == null || !number.isFinite) return 'Enter a finite number';
        if ([
              'quantity',
              'contract_size',
              'point_size',
              'initial_balance',
            ].contains(key) &&
            number <= 0) {
          return 'Must be greater than zero';
        }
        if ([
              'spread_points',
              'slippage_points',
              'commission_per_lot',
              'min_score',
            ].contains(key) &&
            number < 0) {
          return 'Cannot be negative';
        }
        if (key == 'warmup' &&
            (int.tryParse(text) == null || number < 30 || number > 1000)) {
          return 'Enter a whole number from 30 to 1,000';
        }
        if (key == 'csv_utc_offset_minutes' &&
            (int.tryParse(text) == null || number < -840 || number > 840)) {
          return 'Enter whole minutes from -840 to 840';
        }
      }
      return null;
    },
  );

  Widget _dateField(String key, String title) => TextFormField(
    key: ValueKey('backtest-$key'),
    controller: fields[key],
    readOnly: true,
    enabled: csvPreview != null && !previewBusy,
    decoration: InputDecoration(
      labelText: title,
      hintText: 'Import and check CSV first',
      helperText: key == 'start_date'
          ? 'First UTC day to test. Earlier CSV candles supply warmup.'
          : 'Last UTC day to test, including all its available candles.',
      helperMaxLines: 4,
      errorMaxLines: 3,
      suffixIcon: const Icon(Icons.calendar_month_outlined),
    ),
    onTap: () async {
      final first = DateTime.parse('${csvPreview!['start_date']}');
      final last = DateTime.parse('${csvPreview!['end_date']}');
      var initial = DateTime.tryParse(fields[key]!.text) ?? first;
      if (initial.isBefore(first)) initial = first;
      if (initial.isAfter(last)) initial = last;
      final selected = await showDatePicker(
        context: context,
        initialDate: initial,
        firstDate: first,
        lastDate: last,
        helpText: key == 'start_date'
            ? 'First test day (UTC)'
            : 'Last test day (UTC, inclusive)',
      );
      if (selected != null && mounted) {
        setState(
          () => fields[key]!.text =
              '${selected.year.toString().padLeft(4, '0')}-${selected.month.toString().padLeft(2, '0')}-${selected.day.toString().padLeft(2, '0')}',
        );
      }
    },
    validator: (value) {
      if (csvPreview == null) return 'Check the CSV date range first';
      final date = value ?? '';
      if (date.compareTo('${csvPreview!['start_date']}') < 0 ||
          date.compareTo('${csvPreview!['end_date']}') > 0) {
        return 'Choose a date within the CSV test range';
      }
      if (key == 'end_date' && date.compareTo(fields['start_date']!.text) < 0) {
        return 'End date must be on or after start date';
      }
      return null;
    },
  );

  Widget _fieldsWrap(List<Widget> children) => LayoutBuilder(
    builder: (context, constraints) {
      final columns = constraints.maxWidth >= 1050
          ? 4
          : constraints.maxWidth >= 700
          ? 3
          : constraints.maxWidth >= 430
          ? 2
          : 1;
      final width = (constraints.maxWidth - 14 * (columns - 1)) / columns;
      return Wrap(
        spacing: 14,
        runSpacing: 16,
        children: [
          for (final child in children) SizedBox(width: width, child: child),
        ],
      );
    },
  );

  Widget _configuration(Map<String, dynamic> data) {
    final bots = listOfMaps(data['bots']);
    final timeframes = (data['timeframes'] as List? ?? ['1m']).cast<String>();
    final strategies = (data['strategies'] as List? ?? ['trend_pullback'])
        .cast<String>();
    if (bots.isEmpty) {
      return const _EmptyWorkspace(
        icon: Icons.smart_toy_outlined,
        title: 'Add a bot with an instrument first',
        text:
            'The selected bot supplies the instrument identity. Simulations use their own settings.',
      );
    }
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(18),
        child: Form(
          key: _form,
          child: AbsorbPointer(
            absorbing: busy,
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                const Text(
                  'Configure a historical backtest',
                  style: TextStyle(fontSize: 16, fontWeight: FontWeight.w700),
                ),
                const SizedBox(height: 6),
                const Text(
                  'Replay one candle strategy with fixed-size trades. The bot supplies the symbol; these settings apply only to this simulation.',
                  style: TextStyle(color: muted, fontSize: 12),
                ),
                const SizedBox(height: 20),
                _fieldsWrap([
                  DropdownButtonFormField<String>(
                    initialValue: botId,
                    isExpanded: true,
                    decoration: const InputDecoration(
                      labelText: 'Bot / instrument',
                    ),
                    items: [
                      for (final bot in bots)
                        DropdownMenuItem(
                          value: '${bot['id']}',
                          child: Text(
                            '${bot['name']} · ${bot['symbol']}',
                            overflow: TextOverflow.ellipsis,
                          ),
                        ),
                    ],
                    onChanged: (id) => setState(
                      () => _selectBot(
                        bots.firstWhere((bot) => '${bot['id']}' == id),
                      ),
                    ),
                  ),
                  DropdownButtonFormField<String>(
                    initialValue: strategy,
                    isExpanded: true,
                    decoration: const InputDecoration(labelText: 'Strategy'),
                    items: [
                      for (final name in strategies)
                        DropdownMenuItem(
                          value: name,
                          child: Text(
                            label(name),
                            overflow: TextOverflow.ellipsis,
                          ),
                        ),
                    ],
                    onChanged: (value) => setState(() {
                      strategy = value!;
                      _invalidatePreview();
                    }),
                  ),
                  DropdownButtonFormField<String>(
                    key: ValueKey('backtest-tf-$timeframe'),
                    initialValue: timeframes.contains(timeframe)
                        ? timeframe
                        : null,
                    decoration: const InputDecoration(
                      labelText: 'CSV candle timeframe',
                    ),
                    items: [
                      for (final name in timeframes)
                        DropdownMenuItem(value: name, child: Text(name)),
                    ],
                    onChanged: (value) => setState(() {
                      timeframe = value!;
                      _invalidatePreview();
                    }),
                    validator: (value) =>
                        value == null ? 'Select a timeframe' : null,
                  ),
                  _field(
                    'csv_utc_offset_minutes',
                    'CSV UTC offset (minutes)',
                    hint:
                        '0 assumes UTC for times without a zone; 120 means UTC+2. Explicit CSV time zones take precedence. Confirm the export timezone.',
                  ),
                ]),
                const SizedBox(height: 18),
                Wrap(
                  spacing: 12,
                  runSpacing: 8,
                  crossAxisAlignment: WrapCrossAlignment.center,
                  children: [
                    OutlinedButton.icon(
                      onPressed: _importCsv,
                      icon: const Icon(Icons.upload_file),
                      label: const Text('Import candle CSV'),
                    ),
                    Text(
                      sourceName ?? 'No CSV selected',
                      style: const TextStyle(color: muted),
                    ),
                  ],
                ),
                const SizedBox(height: 6),
                const SelectableText(
                  'Bid prices: time,open,high,low,close,tick_volume. MT5 tab-separated exports are accepted. Up to 10,000 completed candles / 1 MB.',
                  style: TextStyle(color: muted, fontSize: 11),
                ),
                const SizedBox(height: 18),
                if (csvPreview != null)
                  SelectableText(
                    'CSV: ${csvPreview!['bars']} candles, ${_backtestUtc(csvPreview!['first_at'])} to ${_backtestUtc(csvPreview!['last_at'])} UTC. '
                    'First test candle after ${csvPreview!['warmup']} warmup bars: ${_backtestUtc(csvPreview!['first_tradable_at'])} UTC. '
                    '${csvPreview!['gap_count']} gaps; dates do not create missing candles.',
                    style: const TextStyle(color: muted, fontSize: 12),
                  )
                else
                  const Text(
                    'Import a CSV to select dates. Defaults use its full available test period after warmup, not dates relative to today.',
                    style: TextStyle(color: muted, fontSize: 12),
                  ),
                Align(
                  alignment: Alignment.centerLeft,
                  child: TextButton.icon(
                    onPressed: csvText == null || previewBusy
                        ? null
                        : _previewCsv,
                    icon: const Icon(Icons.date_range_outlined),
                    label: Text(
                      previewBusy
                          ? 'Checking CSV dates...'
                          : 'Use full CSV date range',
                    ),
                  ),
                ),
                _fieldsWrap([
                  _dateField('start_date', 'Start date (UTC)'),
                  _dateField('end_date', 'End date (UTC, inclusive)'),
                  _field(
                    'quantity',
                    'Fixed quantity (lots)',
                    hint:
                        'Starts from the bot quantity. Each trade uses this many lots; 0.01 is one hundredth of a lot.',
                  ),
                  _field(
                    'initial_balance',
                    'Initial balance',
                    hint:
                        'Simulation starting cash in Profit currency. Default 10,000; use the amount you want to evaluate.',
                  ),
                ]),
                const SizedBox(height: 18),
                Text(
                  defaultsMessage,
                  style: const TextStyle(color: muted, fontSize: 12),
                ),
                Align(
                  alignment: Alignment.centerLeft,
                  child: TextButton.icon(
                    onPressed: defaultsBusy
                        ? null
                        : () => setState(() {
                            _loadDefaults();
                          }),
                    icon: const Icon(Icons.refresh),
                    label: Text(
                      defaultsBusy
                          ? 'Loading defaults...'
                          : 'Reload instrument defaults',
                    ),
                  ),
                ),
                const Text(
                  'Broker values or your last completed run supply defaults when available. Missing sizes are not guessed. '
                  'Cost defaults of 0 mean no cost is simulated, not that your broker charges nothing.',
                  style: TextStyle(color: muted, fontSize: 12),
                ),
                const SizedBox(height: 14),
                _fieldsWrap([
                  _field(
                    'contract_size',
                    'Contract size per lot',
                    hint:
                        'Underlying units in 1 lot, from MT5 Specification > Contract size. Example: if 1 lot = 1 BTC, enter 1. Broker-specific.',
                  ),
                  _field(
                    'point_size',
                    'Point size',
                    hint:
                        'Price change for 1 broker point, not money per point or a pip. Example: point 0.01 means 100 points = 1.00 in price.',
                  ),
                  _field(
                    'currency',
                    'Profit currency',
                    hint:
                        'MT5 Specification > Profit currency, e.g. USD. Balance, P&L and commission use this currency; no FX conversion.',
                  ),
                  _field(
                    'spread_points',
                    'Spread (points)',
                    hint:
                        '(Ask - Bid) / Point size. Example: price gap 0.50 / point 0.01 = 50. Fixed for the test; CSV spread is not used.',
                  ),
                  _field(
                    'slippage_points',
                    'Slippage per side (points)',
                    hint:
                        'Adverse fill movement at entry and exit. Example: 2 points with point 0.01 = 0.02 each side. Default 0 assumes perfect fills.',
                  ),
                  _field(
                    'commission_per_lot',
                    'Round-trip commission / lot',
                    hint:
                        'Opening + closing fee for 1 lot in Profit currency. Example: 3.50 each side = 7; at 0.01 lot, cost is 0.07. Default 0 excludes fees.',
                  ),
                ]),
                const SizedBox(height: 10),
                ExpansionTile(
                  tilePadding: EdgeInsets.zero,
                  title: const Text(
                    'Replay settings',
                    style: TextStyle(fontSize: 13),
                  ),
                  subtitle: const Text(
                    'Warmup, raw signal score, and ambiguous SL/TP candles',
                    style: TextStyle(color: muted, fontSize: 11),
                  ),
                  children: [
                    Padding(
                      padding: const EdgeInsets.only(bottom: 16),
                      child: _fieldsWrap([
                        _field(
                          'warmup',
                          'History window / warmup bars',
                          hint: '30–1,000 bars before each signal',
                        ),
                        _field(
                          'min_score',
                          'Minimum raw signal score',
                          hint: '0 keeps all strategy open signals',
                        ),
                        DropdownButtonFormField<String>(
                          initialValue: sameBarPolicy,
                          isExpanded: true,
                          decoration: const InputDecoration(
                            labelText: 'SL and TP in one candle',
                          ),
                          items: const [
                            DropdownMenuItem(
                              value: 'stop_first',
                              child: Text('Stop first (conservative)'),
                            ),
                            DropdownMenuItem(
                              value: 'target_first',
                              child: Text('Target first (optimistic)'),
                            ),
                          ],
                          onChanged: (value) =>
                              setState(() => sameBarPolicy = value!),
                        ),
                      ]),
                    ),
                  ],
                ),
                const SizedBox(height: 8),
                Align(
                  alignment: Alignment.centerLeft,
                  child: FilledButton.icon(
                    onPressed: busy || previewBusy || defaultsBusy
                        ? null
                        : _submit,
                    icon: busy
                        ? const SizedBox(
                            width: 16,
                            height: 16,
                            child: CircularProgressIndicator(strokeWidth: 2),
                          )
                        : const Icon(Icons.play_arrow_rounded),
                    label: Text(
                      busy ? 'Processing…' : 'Run historical backtest',
                    ),
                  ),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }

  Widget _history() => FutureBuilder(
    future: history,
    builder: (context, snapshot) {
      if (snapshot.hasError) {
        return Text(
          'Saved runs could not load: ${snapshot.error}',
          style: const TextStyle(color: danger),
        );
      }
      if (!snapshot.hasData) return const LinearProgressIndicator();
      final root = mapOf(snapshot.data);
      final runs = listOfMaps(root['results']);
      final count = (root['count'] as num?)?.toInt() ?? 0;
      return Card(
        child: Padding(
          padding: const EdgeInsets.all(18),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Wrap(
                spacing: 16,
                crossAxisAlignment: WrapCrossAlignment.center,
                children: [
                  Text(
                    'Saved backtests ($count)',
                    style: const TextStyle(
                      fontSize: 16,
                      fontWeight: FontWeight.w700,
                    ),
                  ),
                  TextButton.icon(
                    onPressed: busy
                        ? null
                        : () => setState(
                            () => history = widget.client.get(
                              '/api/personal/backtests/?page=$historyPage',
                            ),
                          ),
                    icon: const Icon(Icons.refresh, size: 17),
                    label: const Text('Refresh history'),
                  ),
                ],
              ),
              if (runs.isEmpty)
                const Padding(
                  padding: EdgeInsets.symmetric(vertical: 18),
                  child: Text(
                    'No saved historical backtests yet.',
                    style: TextStyle(color: muted),
                  ),
                ),
              for (final run in runs)
                ListTile(
                  contentPadding: EdgeInsets.zero,
                  leading: Icon(
                    run['status'] == 'completed'
                        ? Icons.check_circle_outline
                        : Icons.info_outline,
                    color: run['status'] == 'completed' ? green : amber,
                  ),
                  title: Text(
                    '${run['symbol']} · ${label('${mapOf(run['config'])['strategy']}')} · ${mapOf(run['config'])['timeframe']}',
                  ),
                  subtitle: Text(
                    '${_backtestUtc(run['created_at'])} UTC · ${run['source_name']} · ${label('${run['status']}')}',
                  ),
                  trailing: const Icon(Icons.chevron_right),
                  onTap: busy ? null : () => _openSaved(run),
                ),
              if (count > 20)
                Row(
                  mainAxisAlignment: MainAxisAlignment.end,
                  children: [
                    Text('Page $historyPage of ${(count / 20).ceil()}'),
                    IconButton(
                      tooltip: 'Previous saved runs',
                      onPressed: busy || historyPage <= 1
                          ? null
                          : () => setState(() {
                              historyPage--;
                              history = widget.client.get(
                                '/api/personal/backtests/?page=$historyPage',
                              );
                            }),
                      icon: const Icon(Icons.chevron_left),
                    ),
                    IconButton(
                      tooltip: 'Next saved runs',
                      onPressed: busy || historyPage * 20 >= count
                          ? null
                          : () => setState(() {
                              historyPage++;
                              history = widget.client.get(
                                '/api/personal/backtests/?page=$historyPage',
                              );
                            }),
                      icon: const Icon(Icons.chevron_right),
                    ),
                  ],
                ),
            ],
          ),
        ),
      );
    },
  );

  @override
  Widget build(BuildContext context) {
    super.build(context);
    return ScrollConfiguration(
      behavior: ScrollConfiguration.of(context).copyWith(scrollbars: false),
      child: FutureBuilder(
        future: options,
        builder: (context, snapshot) {
          if (snapshot.hasError) {
            return Center(
              child: Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  Text('Backtesting could not load: ${snapshot.error}'),
                  TextButton(
                    onPressed: () => setState(() => options = _loadOptions()),
                    child: const Text('Retry'),
                  ),
                ],
              ),
            );
          }
          if (!snapshot.hasData) {
            return const Center(child: CircularProgressIndicator());
          }
          return Scrollbar(
            controller: _scroll,
            thumbVisibility: true,
            child: SingleChildScrollView(
              controller: _scroll,
              padding: const EdgeInsets.fromLTRB(24, 22, 24, 30),
              child: Align(
                alignment: Alignment.topCenter,
                child: ConstrainedBox(
                  constraints: const BoxConstraints(maxWidth: 1600),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.stretch,
                    children: [
                      const Text(
                        'STRATEGY LAB',
                        style: TextStyle(
                          color: blue,
                          fontSize: 10,
                          fontWeight: FontWeight.w800,
                          letterSpacing: 1.2,
                        ),
                      ),
                      const SizedBox(height: 6),
                      const Text(
                        'Historical backtesting',
                        style: TextStyle(
                          fontSize: 22,
                          fontWeight: FontWeight.w800,
                        ),
                      ),
                      const SizedBox(height: 6),
                      const Text(
                        'Import past candles, replay a strategy, and inspect the results after trading costs.',
                        style: TextStyle(color: muted, fontSize: 12),
                      ),
                      const SizedBox(height: 16),
                      if (error != null)
                        Padding(
                          padding: const EdgeInsets.only(bottom: 12),
                          child: SelectableText(
                            error!,
                            style: const TextStyle(color: danger),
                          ),
                        ),
                      _configuration(mapOf(snapshot.data)),
                      if (selectedResult != null)
                        Padding(
                          key: _resultsKey,
                          padding: const EdgeInsets.only(top: 18),
                          child: _HistoricalResult(
                            key: ValueKey(selectedResult!['id']),
                            run: selectedResult!,
                          ),
                        ),
                      const SizedBox(height: 18),
                      _history(),
                    ],
                  ),
                ),
              ),
            ),
          );
        },
      ),
    );
  }
}

class _HistoricalResult extends StatefulWidget {
  const _HistoricalResult({super.key, required this.run});
  final Map<String, dynamic> run;

  @override
  State<_HistoricalResult> createState() => _HistoricalResultState();
}

class _HistoricalResultState extends State<_HistoricalResult> {
  int tradePage = 0;
  String tradeFilter = 'all';
  final _horizontal = ScrollController();

  @override
  void dispose() {
    _horizontal.dispose();
    super.dispose();
  }

  Future<void> _showTrade(Map<String, dynamic> trade) => showDialog<void>(
    context: context,
    builder: (context) => AlertDialog(
      title: const Text('Simulated trade details'),
      scrollable: true,
      content: SizedBox(
        width: 560,
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            const Text(
              'Times identify candles in UTC; the exact intrabar exit time is unknown.',
              style: TextStyle(color: muted, fontSize: 11),
            ),
            const SizedBox(height: 12),
            for (final key in [
              'strategy',
              'direction',
              'signal_time',
              'entry_time',
              'exit_time',
              'quantity',
              'entry_price',
              'exit_price',
              'sl',
              'tp',
              'entry_reason',
              'reason',
              'score',
              'gross_pnl',
              'spread_cost',
              'slippage_cost',
              'commission',
              'pnl',
              'balance',
            ])
              Padding(
                padding: const EdgeInsets.only(bottom: 10),
                child: SelectableText(
                  '${label(key)}: ${key.endsWith('_time') ? _backtestUtc(trade[key]) : trade[key] ?? 'Not recorded'}',
                  style: const TextStyle(fontSize: 12),
                ),
              ),
          ],
        ),
      ),
      actions: [
        TextButton(
          onPressed: () => Navigator.pop(context),
          child: const Text('Close details'),
        ),
      ],
    ),
  );

  Future<void> _export() async {
    try {
      final location = await getSaveLocation(
        suggestedName: 'backtest-${widget.run['id']}-trades.csv',
        acceptedTypeGroups: const [
          XTypeGroup(label: 'CSV', extensions: ['csv']),
        ],
      );
      if (location == null) return;
      final trades = listOfMaps(mapOf(widget.run['result'])['trades']);
      const headers = [
        'strategy',
        'direction',
        'signal_time',
        'entry_time',
        'exit_time',
        'quantity',
        'entry_price',
        'exit_price',
        'sl',
        'tp',
        'reason',
        'gross_pnl',
        'spread_cost',
        'slippage_cost',
        'commission',
        'pnl',
        'balance',
      ];
      String cell(dynamic value) =>
          '"${'${value ?? ''}'.replaceAll('"', '""')}"';
      final csv = [
        headers.join(','),
        for (final trade in trades)
          headers.map((key) => cell(trade[key])).join(','),
      ].join('\r\n');
      await XFile.fromData(
        utf8.encode(csv),
        mimeType: 'text/csv',
      ).saveTo(location.path);
      if (mounted) message(context, 'Trade results exported.');
    } catch (e) {
      if (mounted) message(context, 'Export failed: $e', isError: true);
    }
  }

  @override
  Widget build(BuildContext context) {
    final run = widget.run;
    final result = mapOf(run['result']);
    final summary = mapOf(result['summary']);
    final config = mapOf(run['config']);
    final dataset = mapOf(run['dataset']);
    final currency = '${config['currency'] ?? ''}';
    final trades = listOfMaps(result['trades']);
    final filtered = trades.where((trade) {
      final pnl = double.tryParse('${trade['pnl']}') ?? 0;
      return tradeFilter == 'all' ||
          (tradeFilter == 'wins' && pnl > 0) ||
          (tradeFilter == 'losses' && pnl < 0);
    }).toList();
    final pageTrades = filtered.skip(tradePage * 20).take(20).toList();
    final skips = listOfMaps(result['skip_reasons']);
    final metrics = <(String, String, Color)>[
      (
        'NET P&L ($currency)',
        compactNumber(summary['net_pnl']),
        valueColor(summary['net_pnl']),
      ),
      ('RETURN', '${compactNumber(summary['return_pct'])}%', blue),
      ('TRADES', '${summary['trades'] ?? 0}', blue),
      ('WIN RATE', '${compactNumber(summary['win_rate_pct'])}%', green),
      (
        'PROFIT FACTOR',
        summary['profit_factor'] == null
            ? 'N/A · no losses'
            : compactNumber(summary['profit_factor']),
        blue,
      ),
      ('MAX DRAWDOWN', '${compactNumber(summary['max_drawdown_pct'])}%', amber),
      ('ENDING BALANCE', compactNumber(summary['ending_balance']), blue),
      ('AVERAGE TRADE', compactNumber(summary['avg_trade']), muted),
      ('SPREAD COST', compactNumber(summary['spread_cost']), amber),
      ('SLIPPAGE COST', compactNumber(summary['slippage_cost']), amber),
      ('COMMISSION', compactNumber(summary['commission']), amber),
      (
        'WINS / LOSSES / FLAT',
        '${summary['wins'] ?? 0} / ${summary['losses'] ?? 0} / ${summary['breakeven'] ?? 0}',
        muted,
      ),
    ];
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Wrap(
          spacing: 12,
          runSpacing: 8,
          crossAxisAlignment: WrapCrossAlignment.center,
          children: [
            Text(
              'Backtest #${run['id']} · ${run['symbol']}',
              style: const TextStyle(fontSize: 20, fontWeight: FontWeight.w800),
            ),
            _StatusPill(
              text: label('${run['status']}'),
              color: run['status'] == 'completed' ? green : danger,
            ),
            OutlinedButton.icon(
              onPressed: trades.isEmpty ? null : _export,
              icon: const Icon(Icons.download_rounded, size: 17),
              label: const Text('Export all trades'),
            ),
          ],
        ),
        const SizedBox(height: 8),
        Text(
          '${label('${config['strategy']}')} · ${config['timeframe']} · ${run['source_name']} · $currency',
          style: const TextStyle(color: muted),
        ),
        if (run['status'] != 'completed')
          Padding(
            padding: const EdgeInsets.all(12),
            child: Text(
              '${run['error'] ?? 'Run is still processing.'}',
              style: const TextStyle(color: danger),
            ),
          )
        else ...[
          const SizedBox(height: 8),
          Text(
            '${_backtestUtc(summary['first_at'])} — ${_backtestUtc(summary['last_at'])} UTC · ${dataset['bars']} source candles · ${dataset['gap_count']} gaps',
            style: const TextStyle(color: muted, fontSize: 11),
          ),
          const SizedBox(height: 14),
          LayoutBuilder(
            builder: (context, constraints) {
              final columns = constraints.maxWidth >= 1100
                  ? 6
                  : constraints.maxWidth >= 700
                  ? 4
                  : constraints.maxWidth >= 430
                  ? 3
                  : 2;
              return Wrap(
                spacing: 10,
                runSpacing: 10,
                children: [
                  for (final metric in metrics)
                    SizedBox(
                      width:
                          (constraints.maxWidth - 10 * (columns - 1)) / columns,
                      child: _BacktestMetric(
                        label: metric.$1,
                        value: metric.$2,
                        color: metric.$3,
                      ),
                    ),
                ],
              );
            },
          ),
          const SizedBox(height: 16),
          _BacktestEquityChart(
            points: listOfMaps(result['equity']),
            initial: double.tryParse('${config['initial_balance']}') ?? 0,
            currency: currency,
          ),
          const SizedBox(height: 16),
          Card(
            child: Padding(
              padding: const EdgeInsets.all(16),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  Wrap(
                    spacing: 16,
                    runSpacing: 8,
                    crossAxisAlignment: WrapCrossAlignment.center,
                    children: [
                      const Text(
                        'Trade ledger',
                        style: TextStyle(
                          fontSize: 16,
                          fontWeight: FontWeight.w700,
                        ),
                      ),
                      for (final filter in ['all', 'wins', 'losses'])
                        ChoiceChip(
                          label: Text(label(filter)),
                          selected: tradeFilter == filter,
                          onSelected: (_) => setState(() {
                            tradeFilter = filter;
                            tradePage = 0;
                          }),
                        ),
                    ],
                  ),
                  const SizedBox(height: 10),
                  if (trades.isEmpty)
                    const Text(
                      'No trades met the selected strategy and score criteria. Inspect the skip reasons below.',
                      style: TextStyle(color: muted),
                    )
                  else if (filtered.isEmpty)
                    const Text(
                      'No trades match this filter.',
                      style: TextStyle(color: muted),
                    )
                  else ...[
                    Scrollbar(
                      controller: _horizontal,
                      thumbVisibility: true,
                      child: SingleChildScrollView(
                        controller: _horizontal,
                        scrollDirection: Axis.horizontal,
                        padding: const EdgeInsets.only(bottom: 14),
                        child: DataTable(
                          showCheckboxColumn: false,
                          columnSpacing: 24,
                          columns: const [
                            DataColumn(label: Text('SIDE')),
                            DataColumn(label: Text('ENTRY UTC')),
                            DataColumn(label: Text('EXIT BAR UTC')),
                            DataColumn(label: Text('ENTRY')),
                            DataColumn(label: Text('EXIT')),
                            DataColumn(label: Text('SL')),
                            DataColumn(label: Text('TP')),
                            DataColumn(label: Text('EXIT REASON')),
                            DataColumn(label: Text('NET P&L')),
                            DataColumn(label: Text('BALANCE')),
                          ],
                          rows: [
                            for (final trade in pageTrades)
                              DataRow(
                                onSelectChanged: (_) => _showTrade(trade),
                                cells: [
                                  DataCell(
                                    Text(label('${trade['direction']}')),
                                  ),
                                  DataCell(
                                    Text(_backtestUtc(trade['entry_time'])),
                                  ),
                                  DataCell(
                                    Text(_backtestUtc(trade['exit_time'])),
                                  ),
                                  DataCell(Text('${trade['entry_price']}')),
                                  DataCell(Text('${trade['exit_price']}')),
                                  DataCell(Text('${trade['sl']}')),
                                  DataCell(Text('${trade['tp']}')),
                                  DataCell(Text(label('${trade['reason']}'))),
                                  DataCell(Text(compactNumber(trade['pnl']))),
                                  DataCell(
                                    Text(compactNumber(trade['balance'])),
                                  ),
                                ],
                              ),
                          ],
                        ),
                      ),
                    ),
                    Row(
                      mainAxisAlignment: MainAxisAlignment.end,
                      children: [
                        Text(
                          '${tradePage * 20 + 1}–${tradePage * 20 + pageTrades.length} of ${filtered.length}',
                        ),
                        IconButton(
                          tooltip: 'Previous trades',
                          onPressed: tradePage > 0
                              ? () => setState(() => tradePage--)
                              : null,
                          icon: const Icon(Icons.chevron_left),
                        ),
                        IconButton(
                          tooltip: 'Next trades',
                          onPressed: (tradePage + 1) * 20 < filtered.length
                              ? () => setState(() => tradePage++)
                              : null,
                          icon: const Icon(Icons.chevron_right),
                        ),
                      ],
                    ),
                  ],
                ],
              ),
            ),
          ),
          const SizedBox(height: 12),
          Card(
            child: Padding(
              padding: const EdgeInsets.all(16),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    'Signal diagnostics · ${summary['evaluated_bars']} evaluations · ${summary['open_signals']} open signals',
                    style: const TextStyle(fontWeight: FontWeight.w700),
                  ),
                  const SizedBox(height: 10),
                  if (skips.isEmpty)
                    const Text(
                      'No skipped signals were recorded.',
                      style: TextStyle(color: muted),
                    ),
                  for (final skip in skips)
                    Padding(
                      padding: const EdgeInsets.only(bottom: 6),
                      child: Text(
                        '${skip['count']} × ${label('${skip['reason']}')}',
                        style: const TextStyle(color: muted),
                      ),
                    ),
                ],
              ),
            ),
          ),
          Card(
            child: ExpansionTile(
              title: const Text('Model assumptions & saved settings'),
              subtitle: const Text(
                'Execution rules, candle provenance, and replay parameters',
                style: TextStyle(color: muted, fontSize: 11),
              ),
              childrenPadding: const EdgeInsets.fromLTRB(16, 0, 16, 16),
              children: [
                for (final assumption in result['assumptions'] as List? ?? [])
                  Padding(
                    padding: const EdgeInsets.only(bottom: 8),
                    child: Align(
                      alignment: Alignment.centerLeft,
                      child: Text(
                        '$assumption',
                        style: const TextStyle(color: muted, fontSize: 12),
                      ),
                    ),
                  ),
                Align(
                  alignment: Alignment.centerLeft,
                  child: SelectableText(
                    'CSV SHA-256: ${dataset['sha256']}\n${const JsonEncoder.withIndent('  ').convert(config)}',
                    style: const TextStyle(
                      color: muted,
                      fontFamily: 'Consolas',
                      fontSize: 11,
                    ),
                  ),
                ),
              ],
            ),
          ),
        ],
      ],
    );
  }
}

class _BacktestEquityChart extends StatelessWidget {
  const _BacktestEquityChart({
    required this.points,
    required this.initial,
    required this.currency,
  });
  final List<Map<String, dynamic>> points;
  final double initial;
  final String currency;

  @override
  Widget build(BuildContext context) {
    final values = [
      initial,
      for (final point in points)
        double.tryParse('${point['equity']}') ?? initial,
    ];
    final low = values.reduce((a, b) => a < b ? a : b);
    final high = values.reduce((a, b) => a > b ? a : b);
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Text(
              'Equity curve ($currency)',
              style: const TextStyle(fontSize: 16, fontWeight: FontWeight.w700),
            ),
            const SizedBox(height: 6),
            Text(
              'Candle-close equity · initial ${initial.toStringAsFixed(2)} · low ${low.toStringAsFixed(2)} · high ${high.toStringAsFixed(2)}',
              style: const TextStyle(color: muted, fontSize: 11),
            ),
            const SizedBox(height: 12),
            Semantics(
              label:
                  'Equity from ${values.first.toStringAsFixed(2)} to ${values.last.toStringAsFixed(2)} $currency',
              child: SizedBox(
                height: 180,
                child: CustomPaint(painter: _EquityPainter(values)),
              ),
            ),
            const SizedBox(height: 6),
            const Text(
              'Candles in sequence; market gaps are not drawn to time scale.',
              style: TextStyle(color: muted, fontSize: 10),
            ),
          ],
        ),
      ),
    );
  }
}

class _EquityPainter extends CustomPainter {
  const _EquityPainter(this.values);
  final List<double> values;

  @override
  void paint(Canvas canvas, Size size) {
    if (values.length < 2) return;
    final low = values.reduce((a, b) => a < b ? a : b);
    final high = values.reduce((a, b) => a > b ? a : b);
    final span = high > low ? high - low : 1.0;
    final grid = Paint()
      ..color = border
      ..strokeWidth = 1;
    for (var i = 0; i <= 4; i++) {
      final y = size.height * i / 4;
      canvas.drawLine(Offset(0, y), Offset(size.width, y), grid);
    }
    final path = Path();
    for (var i = 0; i < values.length; i++) {
      final x = i / (values.length - 1) * size.width;
      final y = high == low
          ? size.height / 2
          : 8 + (high - values[i]) / span * (size.height - 16);
      if (i == 0) {
        path.moveTo(x, y);
      } else {
        path.lineTo(x, y);
      }
    }
    canvas.drawPath(
      path,
      Paint()
        ..color = green
        ..strokeWidth = 2
        ..style = PaintingStyle.stroke,
    );
  }

  @override
  bool shouldRepaint(covariant _EquityPainter oldDelegate) =>
      oldDelegate.values != values;
}
