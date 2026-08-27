import 'dart:async';
import 'dart:convert';
import 'dart:ui' show AppExitResponse;

import 'package:flutter/material.dart';

import 'api_client.dart';

void main() => runApp(const EzTradeApp());

const bg = Color(0xFF050A0E);
const sidebar = Color(0xFF080F14);
const panel = Color(0xFF0C141A);
const panel2 = Color(0xFF111D24);
const border = Color(0xFF1C2B34);
const green = Color(0xFF44E1A1);
const blue = Color(0xFF4DA8FF);
const amber = Color(0xFFF4B860);
const muted = Color(0xFF82949E);
const danger = Color(0xFFFF6070);

class EzTradeApp extends StatefulWidget {
  const EzTradeApp({super.key});

  @override
  State<EzTradeApp> createState() => _EzTradeAppState();
}

class _EzTradeAppState extends State<EzTradeApp> with WidgetsBindingObserver {
  ApiClient? client;
  String? loginNotice;
  final List<ApiClient> runtimeClients = [];
  final GlobalKey<ScaffoldMessengerState> messengerKey =
      GlobalKey<ScaffoldMessengerState>();
  bool exitInProgress = false;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    super.dispose();
  }

  @override
  Future<AppExitResponse> didRequestAppExit() async {
    if (exitInProgress) return AppExitResponse.cancel;
    exitInProgress = true;
    try {
      await Future.wait(
        runtimeClients.map((value) => value.stopRuntimeOnExit()),
      ).timeout(const Duration(seconds: 8));
      return AppExitResponse.exit;
    } catch (_) {
      exitInProgress = false;
      messengerKey.currentState?.showSnackBar(
        const SnackBar(
          content: Text(
            'Could not confirm that bots stopped. The app will remain open.',
          ),
          backgroundColor: danger,
        ),
      );
      return AppExitResponse.cancel;
    }
  }

  void acceptClient(ApiClient value) {
    if (value.runtimeStopToken != null && !runtimeClients.contains(value)) {
      runtimeClients.add(value);
    }
    value.onSessionExpired = () {
      if (!mounted || client != value) return;
      setState(() {
        client = null;
        loginNotice = 'Your session expired. Sign in again to continue.';
      });
    };
    setState(() {
      client = value;
      loginNotice = null;
    });
  }

  void logout() {
    client?.onSessionExpired = null;
    client?.logout();
    setState(() {
      client = null;
      loginNotice = null;
    });
  }

  @override
  Widget build(BuildContext context) => MaterialApp(
    title: 'EZ Trade',
    scaffoldMessengerKey: messengerKey,
    debugShowCheckedModeBanner: false,
    theme: ThemeData(
      brightness: Brightness.dark,
      scaffoldBackgroundColor: bg,
      colorScheme: ColorScheme.fromSeed(
        seedColor: green,
        brightness: Brightness.dark,
        surface: panel,
        error: danger,
      ),
      fontFamily: 'Segoe UI',
      cardTheme: const CardThemeData(
        color: panel,
        elevation: 0,
        margin: EdgeInsets.zero,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.all(Radius.circular(12)),
          side: BorderSide(color: border),
        ),
      ),
      dividerColor: border,
      filledButtonTheme: FilledButtonThemeData(
        style: FilledButton.styleFrom(
          backgroundColor: green,
          foregroundColor: bg,
          textStyle: const TextStyle(fontWeight: FontWeight.w700),
          shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(8)),
        ),
      ),
      outlinedButtonTheme: OutlinedButtonThemeData(
        style: OutlinedButton.styleFrom(
          foregroundColor: const Color(0xFFDDE8E4),
          side: const BorderSide(color: Color(0xFF31434D)),
          textStyle: const TextStyle(fontWeight: FontWeight.w700),
          shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(8)),
        ),
      ),
      inputDecorationTheme: const InputDecorationTheme(
        filled: true,
        fillColor: panel2,
        border: OutlineInputBorder(
          borderRadius: BorderRadius.all(Radius.circular(10)),
          borderSide: BorderSide.none,
        ),
      ),
    ),
    home: client == null
        ? LoginScreen(onLogin: acceptClient, notice: loginNotice)
        : DesktopShell(client: client!, onLogout: logout),
  );
}

class LoginScreen extends StatefulWidget {
  const LoginScreen({super.key, required this.onLogin, this.notice});
  final ValueChanged<ApiClient> onLogin;
  final String? notice;

  @override
  State<LoginScreen> createState() => _LoginScreenState();
}

class _LoginScreenState extends State<LoginScreen> {
  final server = TextEditingController(text: 'http://127.0.0.1:8000');
  final username = TextEditingController();
  final password = TextEditingController();
  bool busy = false;
  String? error;

  Future<void> login() async {
    setState(() {
      busy = true;
      error = null;
    });
    final api = ApiClient(server.text.trim());
    try {
      await api.login(username.text.trim(), password.text);
      await api.openRuntimeSession();
      widget.onLogin(api);
    } catch (e) {
      if (mounted) setState(() => error = e.toString());
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  @override
  Widget build(BuildContext context) => Scaffold(
    body: Center(
      child: SizedBox(
        width: 430,
        child: Card(
          child: Padding(
            padding: const EdgeInsets.all(34),
            child: AutofillGroup(
              child: Column(
                mainAxisSize: MainAxisSize.min,
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  const Brand(centered: true),
                  const SizedBox(height: 10),
                  const Text(
                    'Personal MT5 control centre',
                    textAlign: TextAlign.center,
                    style: TextStyle(color: muted),
                  ),
                  if (widget.notice != null) ...[
                    const SizedBox(height: 18),
                    _InlineNotice(
                      icon: Icons.lock_clock_outlined,
                      text: widget.notice!,
                      color: amber,
                    ),
                  ],
                  const SizedBox(height: 30),
                  TextField(
                    controller: server,
                    decoration: const InputDecoration(labelText: 'Backend URL'),
                  ),
                  const SizedBox(height: 12),
                  TextField(
                    controller: username,
                    autofillHints: const [AutofillHints.username],
                    decoration: const InputDecoration(labelText: 'Username'),
                  ),
                  const SizedBox(height: 12),
                  TextField(
                    controller: password,
                    obscureText: true,
                    autofillHints: const [AutofillHints.password],
                    onSubmitted: (_) => login(),
                    decoration: const InputDecoration(labelText: 'Password'),
                  ),
                  if (error != null) ...[
                    const SizedBox(height: 12),
                    Text(error!, style: const TextStyle(color: danger)),
                  ],
                  const SizedBox(height: 22),
                  FilledButton.icon(
                    onPressed: busy ? null : login,
                    icon: busy
                        ? const SizedBox.square(
                            dimension: 16,
                            child: CircularProgressIndicator(strokeWidth: 2),
                          )
                        : const Icon(Icons.lock_open_rounded),
                    label: const Padding(
                      padding: EdgeInsets.symmetric(vertical: 13),
                      child: Text('Sign in securely'),
                    ),
                  ),
                  const SizedBox(height: 14),
                  const Text(
                    'Credentials go only to your Django backend. Session tokens remain in memory.',
                    textAlign: TextAlign.center,
                    style: TextStyle(color: muted, fontSize: 12),
                  ),
                ],
              ),
            ),
          ),
        ),
      ),
    ),
  );
}

class NavItem {
  const NavItem(this.label, this.icon, this.path);
  final String label;
  final IconData icon;
  final String path;
}

const navigation = [
  NavItem('Dashboard', Icons.grid_view_rounded, '/api/personal/dashboard/'),
  NavItem('Markets', Icons.candlestick_chart_rounded, '/api/personal/markets/'),
  NavItem('Bots', Icons.smart_toy_outlined, '/api/bots/'),
  NavItem(
    'Positions',
    Icons.swap_vert_circle_outlined,
    '/api/personal/positions/',
  ),
  NavItem('Orders', Icons.receipt_long_outlined, '/api/orders/'),
  NavItem('Trade history', Icons.query_stats_rounded, '/api/personal/history/'),
  NavItem('Risk', Icons.shield_outlined, '/api/personal/risk/'),
  NavItem('Backtesting', Icons.science_outlined, '/api/personal/backtesting/'),
  NavItem('Logs', Icons.terminal_rounded, '/api/personal/logs/'),
  NavItem('Settings', Icons.settings_outlined, '/api/personal/accounts/'),
];

class DesktopShell extends StatefulWidget {
  const DesktopShell({super.key, required this.client, required this.onLogout});
  final ApiClient client;
  final VoidCallback onLogout;

  @override
  State<DesktopShell> createState() => _DesktopShellState();
}

class _DesktopShellState extends State<DesktopShell> {
  int selected = 0;
  int refreshKey = 0;

  @override
  Widget build(BuildContext context) {
    final current = navigation[selected];
    return Scaffold(
      body: Row(
        children: [
          Container(
            width: 224,
            decoration: const BoxDecoration(
              color: sidebar,
              border: Border(right: BorderSide(color: border)),
            ),
            child: Column(
              children: [
                const Padding(
                  padding: EdgeInsets.fromLTRB(20, 22, 20, 20),
                  child: Brand(),
                ),
                const Divider(height: 1),
                const Padding(
                  padding: EdgeInsets.fromLTRB(20, 18, 20, 10),
                  child: Align(
                    alignment: Alignment.centerLeft,
                    child: Text(
                      'OPERATIONS',
                      style: TextStyle(
                        color: Color(0xFF647681),
                        fontSize: 10,
                        fontWeight: FontWeight.w700,
                        letterSpacing: 1.5,
                      ),
                    ),
                  ),
                ),
                Expanded(
                  child: ListView.builder(
                    padding: const EdgeInsets.symmetric(horizontal: 10),
                    itemCount: navigation.length,
                    itemBuilder: (_, index) {
                      final item = navigation[index];
                      final active = selected == index;
                      return Padding(
                        padding: const EdgeInsets.only(bottom: 3),
                        child: Material(
                          color: active
                              ? const Color(0xFF11251F)
                              : Colors.transparent,
                          borderRadius: BorderRadius.circular(8),
                          clipBehavior: Clip.antiAlias,
                          child: ListTile(
                            dense: true,
                            visualDensity: const VisualDensity(vertical: -1),
                            contentPadding: const EdgeInsets.symmetric(
                              horizontal: 13,
                            ),
                            selected: active,
                            leading: Icon(
                              item.icon,
                              size: 19,
                              color: active ? green : const Color(0xFF9AABB3),
                            ),
                            title: Text(
                              item.label,
                              style: TextStyle(
                                color: active
                                    ? const Color(0xFFE9F5F0)
                                    : const Color(0xFFB6C3C8),
                                fontSize: 13,
                                fontWeight: active
                                    ? FontWeight.w700
                                    : FontWeight.w500,
                              ),
                            ),
                            onTap: () => setState(() => selected = index),
                          ),
                        ),
                      );
                    },
                  ),
                ),
                Container(
                  margin: const EdgeInsets.fromLTRB(14, 8, 14, 10),
                  padding: const EdgeInsets.symmetric(
                    horizontal: 12,
                    vertical: 10,
                  ),
                  decoration: BoxDecoration(
                    color: const Color(0xFF0C171D),
                    borderRadius: BorderRadius.circular(8),
                    border: Border.all(color: border),
                  ),
                  child: const Row(
                    children: [
                      _PulseDot(color: green),
                      SizedBox(width: 9),
                      Expanded(
                        child: Text(
                          'LOCAL ENGINE',
                          style: TextStyle(
                            color: muted,
                            fontSize: 10,
                            fontWeight: FontWeight.w700,
                            letterSpacing: 1.1,
                          ),
                        ),
                      ),
                      Text(
                        'ONLINE',
                        style: TextStyle(
                          color: green,
                          fontSize: 10,
                          fontWeight: FontWeight.w800,
                        ),
                      ),
                    ],
                  ),
                ),
                Material(
                  color: Colors.transparent,
                  child: ListTile(
                    dense: true,
                    contentPadding: const EdgeInsets.symmetric(horizontal: 20),
                    leading: const Icon(
                      Icons.logout_rounded,
                      size: 18,
                      color: muted,
                    ),
                    title: const Text(
                      'Sign out',
                      style: TextStyle(color: muted, fontSize: 13),
                    ),
                    onTap: widget.onLogout,
                  ),
                ),
                const SizedBox(height: 8),
              ],
            ),
          ),
          Expanded(
            child: Column(
              children: [
                Container(
                  height: 72,
                  padding: const EdgeInsets.symmetric(horizontal: 24),
                  decoration: const BoxDecoration(
                    color: Color(0xFF070D12),
                    border: Border(bottom: BorderSide(color: border)),
                  ),
                  child: Row(
                    children: [
                      Column(
                        mainAxisAlignment: MainAxisAlignment.center,
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          const Text(
                            'EZ TRADE / TERMINAL',
                            style: TextStyle(
                              color: muted,
                              fontSize: 9,
                              fontWeight: FontWeight.w700,
                              letterSpacing: 1.4,
                            ),
                          ),
                          const SizedBox(height: 4),
                          Text(
                            current.label,
                            style: const TextStyle(
                              fontSize: 20,
                              fontWeight: FontWeight.w700,
                              letterSpacing: -0.3,
                            ),
                          ),
                        ],
                      ),
                      const Spacer(),
                      Container(
                        padding: const EdgeInsets.symmetric(
                          horizontal: 11,
                          vertical: 7,
                        ),
                        decoration: BoxDecoration(
                          color: panel,
                          borderRadius: BorderRadius.circular(7),
                          border: Border.all(color: border),
                        ),
                        child: Row(
                          children: [
                            const _PulseDot(color: blue, size: 6),
                            const SizedBox(width: 8),
                            Text(
                              widget.client.baseUrl,
                              style: const TextStyle(
                                color: muted,
                                fontFamily: 'Consolas',
                                fontSize: 11,
                              ),
                            ),
                          ],
                        ),
                      ),
                      const SizedBox(width: 8),
                      IconButton(
                        tooltip: 'Refresh',
                        onPressed: () => setState(() => refreshKey++),
                        icon: const Icon(
                          Icons.refresh_rounded,
                          size: 20,
                          color: Color(0xFFB8C6CC),
                        ),
                      ),
                    ],
                  ),
                ),
                Expanded(
                  key: ValueKey('$selected:$refreshKey'),
                  child: pageFor(selected, current),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }

  Widget pageFor(int index, NavItem item) => switch (index) {
    0 => DashboardPage(client: widget.client),
    1 => MarketsPage(client: widget.client),
    2 => BotsPage(client: widget.client),
    3 => PositionsPage(client: widget.client),
    5 => HistoryPage(client: widget.client),
    6 => RiskPage(client: widget.client),
    7 => BacktestingPage(client: widget.client),
    8 => LogsPage(client: widget.client),
    9 => SettingsPage(client: widget.client),
    _ => JsonPage(client: widget.client, title: item.label, path: item.path),
  };
}

class Brand extends StatelessWidget {
  const Brand({super.key, this.centered = false});
  final bool centered;
  @override
  Widget build(BuildContext context) => Row(
    mainAxisAlignment: centered
        ? MainAxisAlignment.center
        : MainAxisAlignment.start,
    children: [
      Container(
        width: 36,
        height: 36,
        decoration: BoxDecoration(
          gradient: const LinearGradient(
            colors: [green, Color(0xFF37BFFF)],
            begin: Alignment.topLeft,
            end: Alignment.bottomRight,
          ),
          borderRadius: BorderRadius.circular(9),
          boxShadow: const [
            BoxShadow(
              color: Color(0x332FE7AB),
              blurRadius: 16,
              spreadRadius: -2,
            ),
          ],
        ),
        child: const Icon(Icons.bolt_rounded, color: bg, size: 22),
      ),
      const SizedBox(width: 12),
      const Flexible(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              'EZ TRADE',
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: TextStyle(
                fontSize: 18,
                fontWeight: FontWeight.w900,
                letterSpacing: 1.2,
              ),
            ),
            SizedBox(height: 1),
            Text(
              'AUTOMATION TERMINAL',
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: TextStyle(
                color: muted,
                fontSize: 8,
                fontWeight: FontWeight.w700,
                letterSpacing: 1.25,
              ),
            ),
          ],
        ),
      ),
    ],
  );
}

class DashboardPage extends StatefulWidget {
  const DashboardPage({super.key, required this.client});
  final ApiClient client;
  @override
  State<DashboardPage> createState() => _DashboardPageState();
}

class _DashboardPageState extends State<DashboardPage> {
  dynamic data;
  String? error;
  Timer? timer;
  bool controlling = false;

  @override
  void initState() {
    super.initState();
    load();
    timer = Timer.periodic(const Duration(seconds: 15), (_) => load());
  }

  @override
  void dispose() {
    timer?.cancel();
    super.dispose();
  }

  Future<void> load() async {
    try {
      final value = await widget.client.get('/api/personal/dashboard/');
      if (mounted) {
        setState(() {
          data = value;
          error = null;
        });
      }
    } catch (e) {
      if (mounted) setState(() => error = e.toString());
    }
  }

  Future<void> control(String action, {bool close = false}) async {
    if (action == 'emergency_stop' &&
        !await confirm(
          context,
          'Emergency stop',
          'Disable entries now${close ? ' and close every EZ Trade-owned position' : ''}?',
        )) {
      return;
    }
    if (mounted) setState(() => controlling = true);
    try {
      await widget.client.post('/api/personal/control/', {
        'action': action,
        'close_owned_positions': close,
      });
      await load();
      if (mounted) {
        final text = switch (action) {
          'start' => 'Automation started.',
          'stop' => 'New entries stopped.',
          _ when close =>
            'Emergency stop active. Owned positions queued to close.',
          _ => 'Emergency stop active.',
        };
        message(context, text);
      }
    } catch (e) {
      if (mounted) message(context, e.toString(), isError: true);
    } finally {
      if (mounted) setState(() => controlling = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    if (error != null) return Empty(icon: Icons.cloud_off, text: error!);
    if (data is! Map) return const Center(child: CircularProgressIndicator());
    final root = mapOf(data);
    final financial = mapOf(root['financial']);
    final trading = mapOf(root['trading']);
    final bot = mapOf(root['bot']);
    final mt5 = mapOf(root['mt5']);
    final account = mapOf(root['account']);
    final connected = mt5['connected'] == true;
    final running = bot['running'] == true;
    final emergency = bot['emergency_stop'] == true;
    final currency = account['currency']?.toString() ?? '';
    final statuses = bot['statuses'] is List
        ? (bot['statuses'] as List)
              .whereType<Map>()
              .map((row) => Map<String, dynamic>.from(row))
              .toList()
        : <Map<String, dynamic>>[];

    return CustomPaint(
      painter: const _TerminalGridPainter(),
      child: LayoutBuilder(
        builder: (context, constraints) {
          final metricColumns = constraints.maxWidth >= 1280
              ? 6
              : constraints.maxWidth >= 760
              ? 3
              : 2;
          final metrics = [
            _TerminalMetric(
              label: 'BALANCE',
              value: money(financial['balance'], currency),
              icon: Icons.account_balance_wallet_outlined,
              accent: blue,
              caption: 'Broker balance',
            ),
            _TerminalMetric(
              label: 'EQUITY',
              value: money(financial['equity'], currency),
              icon: Icons.show_chart_rounded,
              accent: green,
              caption: 'Live account value',
            ),
            _TerminalMetric(
              label: 'FLOATING P&L',
              value: signedMoney(financial['floating_pnl'], currency),
              icon: Icons.swap_vert_rounded,
              accent: valueColor(financial['floating_pnl']),
              caption: 'Open exposure',
            ),
            _TerminalMetric(
              label: 'REALIZED TODAY',
              value: signedMoney(financial['realized_pnl_today'], currency),
              icon: Icons.payments_outlined,
              accent: valueColor(financial['realized_pnl_today']),
              caption: 'Closed trades',
            ),
            _TerminalMetric(
              label: 'DRAWDOWN',
              value: percent(financial['drawdown_pct']),
              icon: Icons.trending_down_rounded,
              accent: asDouble(financial['drawdown_pct']) > 0 ? danger : green,
              caption: 'From intraday peak',
            ),
            _TerminalMetric(
              label: 'OPEN POSITIONS',
              value: '${trading['active_positions'] ?? 0}',
              icon: Icons.layers_outlined,
              accent: amber,
              caption: 'Broker reconciled',
            ),
          ];

          return ListView(
            padding: const EdgeInsets.fromLTRB(24, 22, 24, 30),
            children: [
              _CommandCenter(
                running: running,
                connected: connected,
                emergency: emergency,
                controlling: controlling,
                account: account,
                mt5: mt5,
                onStart: () => control('start'),
                onStop: () => control('stop'),
                onEmergency: () => control('emergency_stop'),
                onEmergencyClose: () => control('emergency_stop', close: true),
              ),
              const SizedBox(height: 14),
              GridView.count(
                crossAxisCount: metricColumns,
                shrinkWrap: true,
                physics: const NeverScrollableScrollPhysics(),
                mainAxisSpacing: 10,
                crossAxisSpacing: 10,
                childAspectRatio: metricColumns == 6 ? 1.7 : 1.85,
                children: metrics,
              ),
              const SizedBox(height: 14),
              if (constraints.maxWidth >= 1120)
                IntrinsicHeight(
                  child: Row(
                    crossAxisAlignment: CrossAxisAlignment.stretch,
                    children: [
                      Expanded(
                        flex: 7,
                        child: _BotFleetPanel(
                          statuses: statuses,
                          running: running,
                        ),
                      ),
                      const SizedBox(width: 12),
                      Expanded(
                        flex: 5,
                        child: _CapitalRiskPanel(
                          financial: financial,
                          currency: currency,
                        ),
                      ),
                      const SizedBox(width: 12),
                      Expanded(
                        flex: 5,
                        child: _SessionPulsePanel(mt5: mt5, trading: trading),
                      ),
                    ],
                  ),
                )
              else ...[
                _BotFleetPanel(statuses: statuses, running: running),
                const SizedBox(height: 12),
                _CapitalRiskPanel(financial: financial, currency: currency),
                const SizedBox(height: 12),
                _SessionPulsePanel(mt5: mt5, trading: trading),
              ],
            ],
          );
        },
      ),
    );
  }
}

class _PulseDot extends StatelessWidget {
  const _PulseDot({required this.color, this.size = 7});
  final Color color;
  final double size;

  @override
  Widget build(BuildContext context) => Container(
    width: size,
    height: size,
    decoration: BoxDecoration(
      color: color,
      shape: BoxShape.circle,
      boxShadow: [BoxShadow(color: color, blurRadius: 7, spreadRadius: -1)],
    ),
  );
}

class _TerminalGridPainter extends CustomPainter {
  const _TerminalGridPainter();

  @override
  void paint(Canvas canvas, Size size) {
    final line = Paint()
      ..color = const Color(0x101C2B34)
      ..strokeWidth = 1;
    const step = 32.0;
    for (double x = 0; x < size.width; x += step) {
      canvas.drawLine(Offset(x, 0), Offset(x, size.height), line);
    }
    for (double y = 0; y < size.height; y += step) {
      canvas.drawLine(Offset(0, y), Offset(size.width, y), line);
    }
  }

  @override
  bool shouldRepaint(covariant CustomPainter oldDelegate) => false;
}

class _CommandCenter extends StatelessWidget {
  const _CommandCenter({
    required this.running,
    required this.connected,
    required this.emergency,
    required this.controlling,
    required this.account,
    required this.mt5,
    required this.onStart,
    required this.onStop,
    required this.onEmergency,
    required this.onEmergencyClose,
  });

  final bool running;
  final bool connected;
  final bool emergency;
  final bool controlling;
  final Map<String, dynamic> account;
  final Map<String, dynamic> mt5;
  final VoidCallback onStart;
  final VoidCallback onStop;
  final VoidCallback onEmergency;
  final VoidCallback onEmergencyClose;

  @override
  Widget build(BuildContext context) {
    final statusColor = emergency
        ? danger
        : running
        ? green
        : amber;
    final status = emergency
        ? 'EMERGENCY STOP ACTIVE'
        : running
        ? 'AUTOMATION ACTIVE'
        : 'AUTOMATION STANDBY';
    final subtitle = [account['alias'], account['server']]
        .where((value) => value != null && value.toString().isNotEmpty)
        .join('  •  ');

    final identity = Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        Container(
          width: 46,
          height: 46,
          decoration: BoxDecoration(
            color: statusColor.withValues(alpha: 0.1),
            borderRadius: BorderRadius.circular(10),
            border: Border.all(color: statusColor.withValues(alpha: 0.32)),
          ),
          child: Icon(
            emergency
                ? Icons.warning_amber_rounded
                : running
                ? Icons.graphic_eq_rounded
                : Icons.pause_rounded,
            color: statusColor,
            size: 23,
          ),
        ),
        const SizedBox(width: 14),
        Flexible(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                mainAxisSize: MainAxisSize.min,
                children: [
                  _PulseDot(color: statusColor),
                  const SizedBox(width: 8),
                  Flexible(
                    child: Text(
                      status,
                      overflow: TextOverflow.ellipsis,
                      style: TextStyle(
                        color: statusColor,
                        fontSize: 11,
                        fontWeight: FontWeight.w900,
                        letterSpacing: 1.15,
                      ),
                    ),
                  ),
                ],
              ),
              const SizedBox(height: 5),
              Text(
                subtitle.isEmpty ? 'Personal trading workspace' : subtitle,
                overflow: TextOverflow.ellipsis,
                style: const TextStyle(
                  color: Color(0xFFD9E5E1),
                  fontSize: 15,
                  fontWeight: FontWeight.w700,
                ),
              ),
              const SizedBox(height: 7),
              Wrap(
                spacing: 7,
                runSpacing: 6,
                children: [
                  _StatusPill(
                    text: connected ? 'MT5 CONNECTED' : 'MT5 OFFLINE',
                    color: connected ? green : danger,
                  ),
                  _StatusPill(
                    text: '${mt5['account_mode'] ?? 'unknown'}'.toUpperCase(),
                    color: blue,
                  ),
                  if (account['login'] != null &&
                      account['login'].toString().isNotEmpty)
                    _StatusPill(
                      text: 'LOGIN ${account['login']}',
                      color: muted,
                    ),
                ],
              ),
            ],
          ),
        ),
      ],
    );

    final commands = Wrap(
      alignment: WrapAlignment.end,
      spacing: 8,
      runSpacing: 8,
      children: [
        FilledButton.icon(
          onPressed: connected && !running && !controlling ? onStart : null,
          icon: const Icon(Icons.play_arrow_rounded, size: 18),
          label: const Text('Start engine'),
        ),
        OutlinedButton.icon(
          onPressed: running && !controlling ? onStop : null,
          icon: const Icon(Icons.stop_rounded, size: 17),
          label: const Text('Stop entries'),
        ),
        FilledButton.icon(
          style: FilledButton.styleFrom(
            backgroundColor: danger,
            foregroundColor: Colors.white,
          ),
          onPressed: controlling ? null : onEmergency,
          icon: const Icon(Icons.emergency_rounded, size: 17),
          label: const Text('Emergency stop'),
        ),
        OutlinedButton.icon(
          style: OutlinedButton.styleFrom(
            foregroundColor: danger,
            side: const BorderSide(color: Color(0xFF71313B)),
          ),
          onPressed: controlling ? null : onEmergencyClose,
          icon: const Icon(Icons.close_fullscreen_rounded, size: 16),
          label: const Text('Stop + close owned'),
        ),
      ],
    );

    return Container(
      padding: const EdgeInsets.all(17),
      decoration: BoxDecoration(
        gradient: const LinearGradient(
          colors: [Color(0xFF101B21), Color(0xFF0B1419)],
        ),
        borderRadius: BorderRadius.circular(12),
        border: Border.all(
          color: statusColor.withValues(alpha: emergency ? 0.5 : 0.24),
        ),
      ),
      child: LayoutBuilder(
        builder: (_, constraints) {
          if (constraints.maxWidth < 980) {
            return Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [identity, const SizedBox(height: 16), commands],
            );
          }
          return Row(
            children: [
              Expanded(child: identity),
              const SizedBox(width: 20),
              Flexible(child: commands),
            ],
          );
        },
      ),
    );
  }
}

class _StatusPill extends StatelessWidget {
  const _StatusPill({required this.text, required this.color});
  final String text;
  final Color color;

  @override
  Widget build(BuildContext context) => Container(
    padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
    decoration: BoxDecoration(
      color: color.withValues(alpha: 0.08),
      borderRadius: BorderRadius.circular(5),
      border: Border.all(color: color.withValues(alpha: 0.22)),
    ),
    child: Text(
      text,
      style: TextStyle(
        color: color,
        fontFamily: 'Consolas',
        fontSize: 9,
        fontWeight: FontWeight.w700,
        letterSpacing: 0.65,
      ),
    ),
  );
}

class _TerminalMetric extends StatelessWidget {
  const _TerminalMetric({
    required this.label,
    required this.value,
    required this.icon,
    required this.accent,
    required this.caption,
  });
  final String label;
  final String value;
  final IconData icon;
  final Color accent;
  final String caption;

  @override
  Widget build(BuildContext context) => Container(
    padding: const EdgeInsets.fromLTRB(14, 13, 14, 12),
    decoration: BoxDecoration(
      color: panel,
      borderRadius: BorderRadius.circular(10),
      border: Border.all(color: border),
    ),
    child: Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          children: [
            Container(
              width: 26,
              height: 26,
              decoration: BoxDecoration(
                color: accent.withValues(alpha: 0.09),
                borderRadius: BorderRadius.circular(6),
              ),
              child: Icon(icon, color: accent, size: 15),
            ),
            const SizedBox(width: 9),
            Expanded(
              child: Text(
                label,
                overflow: TextOverflow.ellipsis,
                style: const TextStyle(
                  color: muted,
                  fontSize: 9,
                  fontWeight: FontWeight.w800,
                  letterSpacing: 0.9,
                ),
              ),
            ),
          ],
        ),
        const Spacer(),
        Text(
          value,
          maxLines: 1,
          overflow: TextOverflow.ellipsis,
          style: TextStyle(
            color: accent,
            fontFamily: 'Consolas',
            fontSize: 19,
            fontWeight: FontWeight.w700,
            letterSpacing: -0.4,
          ),
        ),
        const SizedBox(height: 3),
        Text(
          caption,
          overflow: TextOverflow.ellipsis,
          style: const TextStyle(color: Color(0xFF687B84), fontSize: 10),
        ),
      ],
    ),
  );
}

class _TradingPanel extends StatelessWidget {
  const _TradingPanel({
    required this.eyebrow,
    required this.title,
    required this.trailing,
    required this.children,
  });
  final String eyebrow;
  final String title;
  final Widget trailing;
  final List<Widget> children;

  @override
  Widget build(BuildContext context) => Container(
    padding: const EdgeInsets.all(16),
    decoration: BoxDecoration(
      color: panel,
      borderRadius: BorderRadius.circular(11),
      border: Border.all(color: border),
    ),
    child: Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Row(
          children: [
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    eyebrow,
                    style: const TextStyle(
                      color: blue,
                      fontSize: 9,
                      fontWeight: FontWeight.w800,
                      letterSpacing: 1.2,
                    ),
                  ),
                  const SizedBox(height: 4),
                  Text(
                    title,
                    style: const TextStyle(
                      fontSize: 15,
                      fontWeight: FontWeight.w800,
                    ),
                  ),
                ],
              ),
            ),
            trailing,
          ],
        ),
        const SizedBox(height: 14),
        ...children,
      ],
    ),
  );
}

class _BotFleetPanel extends StatelessWidget {
  const _BotFleetPanel({required this.statuses, required this.running});
  final List<Map<String, dynamic>> statuses;
  final bool running;

  @override
  Widget build(BuildContext context) {
    final active = statuses
        .where((row) => '${row['status']}'.toLowerCase() == 'active')
        .length;
    return _TradingPanel(
      eyebrow: 'STRATEGY FLEET',
      title: 'Automation engines',
      trailing: _StatusPill(
        text: '$active / ${statuses.length} ACTIVE',
        color: running ? green : amber,
      ),
      children: [
        if (statuses.isEmpty)
          const _InlineNotice(
            icon: Icons.route_outlined,
            text: 'No strategy engines are assigned to this account.',
          )
        else
          for (var index = 0; index < statuses.length; index++) ...[
            _BotStatusRow(row: statuses[index]),
            if (index != statuses.length - 1) const SizedBox(height: 8),
          ],
      ],
    );
  }
}

class _BotStatusRow extends StatelessWidget {
  const _BotStatusRow({required this.row});
  final Map<String, dynamic> row;

  @override
  Widget build(BuildContext context) {
    final status = '${row['status'] ?? 'unknown'}'.toLowerCase();
    final color = status == 'active'
        ? green
        : status.contains('error')
        ? danger
        : amber;
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 11),
      decoration: BoxDecoration(
        color: const Color(0xFF0A1217),
        borderRadius: BorderRadius.circular(8),
        border: Border.all(color: const Color(0xFF17262F)),
      ),
      child: Row(
        children: [
          Container(
            width: 31,
            height: 31,
            decoration: BoxDecoration(
              color: color.withValues(alpha: 0.08),
              borderRadius: BorderRadius.circular(7),
            ),
            child: Icon(Icons.auto_graph_rounded, color: color, size: 17),
          ),
          const SizedBox(width: 11),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  '${row['name'] ?? 'Unnamed engine'}',
                  overflow: TextOverflow.ellipsis,
                  style: const TextStyle(
                    fontSize: 12,
                    fontWeight: FontWeight.w700,
                  ),
                ),
                const SizedBox(height: 3),
                Text(
                  'ID ${row['id'] ?? '—'}  •  ${row['engine_mode'] ?? 'standard'}',
                  style: const TextStyle(
                    color: muted,
                    fontFamily: 'Consolas',
                    fontSize: 10,
                  ),
                ),
              ],
            ),
          ),
          _StatusPill(text: status.toUpperCase(), color: color),
        ],
      ),
    );
  }
}

class _CapitalRiskPanel extends StatelessWidget {
  const _CapitalRiskPanel({required this.financial, required this.currency});
  final Map<String, dynamic> financial;
  final String currency;

  @override
  Widget build(BuildContext context) {
    final drawdown = asDouble(financial['drawdown_pct']);
    return _TradingPanel(
      eyebrow: 'CAPITAL CONTROL',
      title: 'Margin & drawdown',
      trailing: const Icon(Icons.shield_outlined, color: green, size: 20),
      children: [
        _DataLine(
          label: 'Start equity',
          value: money(financial['start_equity'], currency),
        ),
        _DataLine(
          label: 'Margin used',
          value: money(financial['margin'], currency),
        ),
        _DataLine(
          label: 'Free margin',
          value: money(financial['free_margin'], currency),
          valueColor: green,
        ),
        _DataLine(
          label: 'Margin level',
          value: optionalPercent(financial['margin_level']),
        ),
        const SizedBox(height: 13),
        Row(
          children: [
            const Text(
              'INTRADAY DRAWDOWN',
              style: TextStyle(
                color: muted,
                fontSize: 9,
                fontWeight: FontWeight.w800,
                letterSpacing: 0.75,
              ),
            ),
            const Spacer(),
            Text(
              percent(drawdown),
              style: TextStyle(
                color: drawdown > 0 ? danger : green,
                fontFamily: 'Consolas',
                fontSize: 11,
                fontWeight: FontWeight.w700,
              ),
            ),
          ],
        ),
        const SizedBox(height: 8),
        ClipRRect(
          borderRadius: BorderRadius.circular(4),
          child: LinearProgressIndicator(
            value: (drawdown / 100).clamp(0, 1).toDouble(),
            minHeight: 6,
            backgroundColor: const Color(0xFF17242B),
            valueColor: AlwaysStoppedAnimation<Color>(
              drawdown > 0 ? danger : green,
            ),
          ),
        ),
        const SizedBox(height: 9),
        const Text(
          'Measured from today’s broker equity peak.',
          style: TextStyle(color: Color(0xFF667982), fontSize: 10),
        ),
      ],
    );
  }
}

class _SessionPulsePanel extends StatelessWidget {
  const _SessionPulsePanel({required this.mt5, required this.trading});
  final Map<String, dynamic> mt5;
  final Map<String, dynamic> trading;

  @override
  Widget build(BuildContext context) {
    final connected = mt5['connected'] == true;
    final symbols = trading['enabled_symbols'] is List
        ? List<dynamic>.from(trading['enabled_symbols'])
        : <dynamic>[];
    return _TradingPanel(
      eyebrow: 'LIVE TELEMETRY',
      title: 'Session pulse',
      trailing: _StatusPill(
        text: connected ? 'FEED ONLINE' : 'FEED OFFLINE',
        color: connected ? green : danger,
      ),
      children: [
        Row(
          children: [
            _PulseDot(color: connected ? green : danger),
            const SizedBox(width: 8),
            Expanded(
              child: Text(
                connected ? 'MT5 heartbeat received' : 'MT5 is not connected',
                style: const TextStyle(
                  fontSize: 11,
                  fontWeight: FontWeight.w600,
                ),
              ),
            ),
            Text(
              formatTimestamp(mt5['checked_at']),
              style: const TextStyle(
                color: muted,
                fontFamily: 'Consolas',
                fontSize: 10,
              ),
            ),
          ],
        ),
        const Padding(
          padding: EdgeInsets.symmetric(vertical: 13),
          child: Divider(height: 1),
        ),
        Row(
          children: [
            Expanded(
              child: _SessionStat(
                label: 'ENTRIES',
                value: '${trading['today_entries'] ?? 0}',
                color: blue,
              ),
            ),
            const SizedBox(width: 8),
            Expanded(
              child: _SessionStat(
                label: 'WINS',
                value: '${trading['winning_trades_today'] ?? 0}',
                color: green,
              ),
            ),
            const SizedBox(width: 8),
            Expanded(
              child: _SessionStat(
                label: 'LOSSES',
                value: '${trading['losing_trades_today'] ?? 0}',
                color: danger,
              ),
            ),
          ],
        ),
        const SizedBox(height: 14),
        const Text(
          'ENABLED MARKETS',
          style: TextStyle(
            color: muted,
            fontSize: 9,
            fontWeight: FontWeight.w800,
            letterSpacing: 0.8,
          ),
        ),
        const SizedBox(height: 8),
        if (symbols.isEmpty)
          const Text(
            'No markets enabled',
            style: TextStyle(color: Color(0xFF667982), fontSize: 11),
          )
        else
          Wrap(
            spacing: 6,
            runSpacing: 6,
            children: [
              for (final symbol in symbols)
                _StatusPill(text: '$symbol', color: blue),
            ],
          ),
        if (!connected &&
            mt5['last_error'] != null &&
            '${mt5['last_error']}' != 'not checked') ...[
          const SizedBox(height: 12),
          _InlineNotice(
            icon: Icons.error_outline_rounded,
            text: '${mt5['last_error']}',
            color: danger,
          ),
        ],
      ],
    );
  }
}

class _SessionStat extends StatelessWidget {
  const _SessionStat({
    required this.label,
    required this.value,
    required this.color,
  });
  final String label;
  final String value;
  final Color color;

  @override
  Widget build(BuildContext context) => Container(
    padding: const EdgeInsets.symmetric(horizontal: 9, vertical: 9),
    decoration: BoxDecoration(
      color: const Color(0xFF091116),
      borderRadius: BorderRadius.circular(7),
      border: Border.all(color: const Color(0xFF17262F)),
    ),
    child: Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          label,
          overflow: TextOverflow.ellipsis,
          style: const TextStyle(
            color: muted,
            fontSize: 8,
            fontWeight: FontWeight.w800,
            letterSpacing: 0.7,
          ),
        ),
        const SizedBox(height: 5),
        Text(
          value,
          style: TextStyle(
            color: color,
            fontFamily: 'Consolas',
            fontSize: 16,
            fontWeight: FontWeight.w700,
          ),
        ),
      ],
    ),
  );
}

class _DataLine extends StatelessWidget {
  const _DataLine({required this.label, required this.value, this.valueColor});
  final String label;
  final String value;
  final Color? valueColor;

  @override
  Widget build(BuildContext context) => Padding(
    padding: const EdgeInsets.symmetric(vertical: 5),
    child: Row(
      children: [
        Expanded(
          child: Text(
            label,
            style: const TextStyle(color: muted, fontSize: 11),
          ),
        ),
        Text(
          value,
          style: TextStyle(
            color: valueColor ?? const Color(0xFFDDE8E4),
            fontFamily: 'Consolas',
            fontSize: 11,
            fontWeight: FontWeight.w700,
          ),
        ),
      ],
    ),
  );
}

class _InlineNotice extends StatelessWidget {
  const _InlineNotice({
    required this.icon,
    required this.text,
    this.color = muted,
  });
  final IconData icon;
  final String text;
  final Color color;

  @override
  Widget build(BuildContext context) => Container(
    padding: const EdgeInsets.all(11),
    decoration: BoxDecoration(
      color: color.withValues(alpha: 0.06),
      borderRadius: BorderRadius.circular(7),
      border: Border.all(color: color.withValues(alpha: 0.16)),
    ),
    child: Row(
      children: [
        Icon(icon, color: color, size: 17),
        const SizedBox(width: 9),
        Expanded(
          child: Text(text, style: TextStyle(color: color, fontSize: 11)),
        ),
      ],
    ),
  );
}

class JsonPage extends StatelessWidget {
  const JsonPage({
    super.key,
    required this.client,
    required this.title,
    required this.path,
  });
  final ApiClient client;
  final String title;
  final String path;
  @override
  Widget build(BuildContext context) => FutureBuilder(
    future: client.get(path),
    builder: (_, snapshot) {
      if (snapshot.hasError) {
        return Empty(icon: Icons.cloud_off, text: snapshot.error.toString());
      }
      if (!snapshot.hasData) {
        return const Center(child: CircularProgressIndicator());
      }
      return Records(
        data: snapshot.data,
        empty: 'No ${title.toLowerCase()} records yet.',
      );
    },
  );
}

class LogsPage extends StatefulWidget {
  const LogsPage({super.key, required this.client});
  final ApiClient client;

  @override
  State<LogsPage> createState() => _LogsPageState();
}

class _LogsPageState extends State<LogsPage> {
  late Future<dynamic> future;
  final search = TextEditingController();
  String severity = 'all';

  @override
  void initState() {
    super.initState();
    future = widget.client.get('/api/personal/logs/');
  }

  @override
  void dispose() {
    search.dispose();
    super.dispose();
  }

  void reload() => setState(() {
    future = widget.client.get('/api/personal/logs/');
  });

  List<Map<String, dynamic>> visibleRows(List<Map<String, dynamic>> rows) {
    final query = search.text.trim().toLowerCase();
    return rows.where((row) {
      final level = '${row['severity'] ?? 'info'}'.toLowerCase();
      if (severity != 'all' && level != severity) return false;
      if (query.isEmpty) return true;
      final searchable = [
        row['event_type'],
        row['symbol'],
        row['message'],
        jsonEncode(row['context'] ?? const {}),
      ].join(' ').toLowerCase();
      return searchable.contains(query);
    }).toList();
  }

  @override
  Widget build(BuildContext context) => FutureBuilder(
    future: future,
    builder: (_, snapshot) {
      if (snapshot.hasError) {
        return Empty(icon: Icons.cloud_off, text: snapshot.error.toString());
      }
      if (!snapshot.hasData) {
        return const Center(child: CircularProgressIndicator());
      }
      final rows = listOfMaps(snapshot.data);
      final filtered = visibleRows(rows);
      final warnings = rows
          .where((row) => '${row['severity']}'.toLowerCase() == 'warning')
          .length;
      final errors = rows
          .where((row) => '${row['severity']}'.toLowerCase() == 'error')
          .length;

      return Padding(
        padding: const EdgeInsets.fromLTRB(24, 20, 24, 0),
        child: Column(
          children: [
            _LogsOverview(
              total: rows.length,
              warnings: warnings,
              errors: errors,
              onRefresh: reload,
            ),
            const SizedBox(height: 12),
            LayoutBuilder(
              builder: (_, constraints) {
                final searchBox = SizedBox(
                  width: constraints.maxWidth >= 900
                      ? 360
                      : constraints.maxWidth,
                  height: 40,
                  child: TextField(
                    controller: search,
                    onChanged: (_) => setState(() {}),
                    style: const TextStyle(fontSize: 12),
                    decoration: InputDecoration(
                      hintText: 'Search events, symbols or messages',
                      hintStyle: const TextStyle(color: muted, fontSize: 12),
                      prefixIcon: const Icon(
                        Icons.search_rounded,
                        color: muted,
                        size: 18,
                      ),
                      suffixIcon: search.text.isEmpty
                          ? null
                          : IconButton(
                              tooltip: 'Clear search',
                              onPressed: () {
                                search.clear();
                                setState(() {});
                              },
                              icon: const Icon(Icons.close_rounded, size: 16),
                            ),
                      contentPadding: const EdgeInsets.symmetric(vertical: 8),
                    ),
                  ),
                );
                final filters = Wrap(
                  spacing: 7,
                  runSpacing: 7,
                  children: [
                    _LogFilter(
                      label: 'All',
                      count: rows.length,
                      selected: severity == 'all',
                      color: green,
                      onSelected: () => setState(() => severity = 'all'),
                    ),
                    _LogFilter(
                      label: 'Info',
                      count: rows.length - warnings - errors,
                      selected: severity == 'info',
                      color: blue,
                      onSelected: () => setState(() => severity = 'info'),
                    ),
                    _LogFilter(
                      label: 'Warnings',
                      count: warnings,
                      selected: severity == 'warning',
                      color: amber,
                      onSelected: () => setState(() => severity = 'warning'),
                    ),
                    _LogFilter(
                      label: 'Errors',
                      count: errors,
                      selected: severity == 'error',
                      color: danger,
                      onSelected: () => setState(() => severity = 'error'),
                    ),
                  ],
                );
                if (constraints.maxWidth < 900) {
                  return Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [searchBox, const SizedBox(height: 9), filters],
                  );
                }
                return Row(
                  children: [
                    searchBox,
                    const SizedBox(width: 12),
                    Expanded(child: filters),
                    Text(
                      '${filtered.length} visible',
                      style: const TextStyle(color: muted, fontSize: 11),
                    ),
                  ],
                );
              },
            ),
            const SizedBox(height: 12),
            Expanded(
              child: filtered.isEmpty
                  ? const Empty(
                      icon: Icons.manage_search_rounded,
                      text: 'No journal events match the current filters.',
                    )
                  : ListView.separated(
                      padding: const EdgeInsets.only(bottom: 24),
                      itemCount: filtered.length,
                      separatorBuilder: (_, _) => const SizedBox(height: 7),
                      itemBuilder: (_, index) => _LogEventTile(
                        key: ValueKey('log-${filtered[index]['id'] ?? index}'),
                        row: filtered[index],
                      ),
                    ),
            ),
          ],
        ),
      );
    },
  );
}

class _LogsOverview extends StatelessWidget {
  const _LogsOverview({
    required this.total,
    required this.warnings,
    required this.errors,
    required this.onRefresh,
  });
  final int total;
  final int warnings;
  final int errors;
  final VoidCallback onRefresh;

  @override
  Widget build(BuildContext context) => Container(
    padding: const EdgeInsets.fromLTRB(18, 16, 14, 16),
    decoration: BoxDecoration(
      color: panel,
      borderRadius: BorderRadius.circular(11),
      border: Border.all(color: border),
    ),
    child: Row(
      children: [
        Container(
          width: 38,
          height: 38,
          decoration: BoxDecoration(
            color: blue.withValues(alpha: 0.10),
            borderRadius: BorderRadius.circular(9),
          ),
          child: const Icon(Icons.terminal_rounded, color: blue, size: 20),
        ),
        const SizedBox(width: 13),
        const Expanded(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                'OPERATIONS JOURNAL',
                style: TextStyle(
                  color: blue,
                  fontSize: 9,
                  fontWeight: FontWeight.w800,
                  letterSpacing: 1.25,
                ),
              ),
              SizedBox(height: 4),
              Text(
                'Automation activity',
                style: TextStyle(fontSize: 17, fontWeight: FontWeight.w800),
              ),
              SizedBox(height: 3),
              Text(
                'Engine decisions, order lifecycle events and risk alerts.',
                style: TextStyle(color: muted, fontSize: 11),
              ),
            ],
          ),
        ),
        _JournalStat(label: 'EVENTS', value: total, color: blue),
        const SizedBox(width: 8),
        _JournalStat(label: 'WARNINGS', value: warnings, color: amber),
        const SizedBox(width: 8),
        _JournalStat(label: 'ERRORS', value: errors, color: danger),
        const SizedBox(width: 6),
        IconButton(
          tooltip: 'Reload journal',
          onPressed: onRefresh,
          icon: const Icon(Icons.refresh_rounded, size: 19, color: muted),
        ),
      ],
    ),
  );
}

class _JournalStat extends StatelessWidget {
  const _JournalStat({
    required this.label,
    required this.value,
    required this.color,
  });
  final String label;
  final int value;
  final Color color;

  @override
  Widget build(BuildContext context) => Container(
    width: 82,
    padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 8),
    decoration: BoxDecoration(
      color: color.withValues(alpha: 0.06),
      borderRadius: BorderRadius.circular(8),
      border: Border.all(color: color.withValues(alpha: 0.16)),
    ),
    child: Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          label,
          style: const TextStyle(
            color: muted,
            fontSize: 8,
            fontWeight: FontWeight.w800,
            letterSpacing: .7,
          ),
        ),
        const SizedBox(height: 2),
        Text(
          '$value',
          style: TextStyle(
            color: color,
            fontFamily: 'Consolas',
            fontSize: 15,
            fontWeight: FontWeight.w800,
          ),
        ),
      ],
    ),
  );
}

class _LogFilter extends StatelessWidget {
  const _LogFilter({
    required this.label,
    required this.count,
    required this.selected,
    required this.color,
    required this.onSelected,
  });
  final String label;
  final int count;
  final bool selected;
  final Color color;
  final VoidCallback onSelected;

  @override
  Widget build(BuildContext context) => InkWell(
    onTap: onSelected,
    borderRadius: BorderRadius.circular(7),
    child: AnimatedContainer(
      duration: const Duration(milliseconds: 140),
      padding: const EdgeInsets.symmetric(horizontal: 11, vertical: 8),
      decoration: BoxDecoration(
        color: selected ? color.withValues(alpha: 0.11) : panel,
        borderRadius: BorderRadius.circular(7),
        border: Border.all(
          color: selected ? color.withValues(alpha: 0.48) : border,
        ),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Container(
            width: 6,
            height: 6,
            decoration: BoxDecoration(color: color, shape: BoxShape.circle),
          ),
          const SizedBox(width: 7),
          Text(
            label,
            style: TextStyle(
              color: selected ? const Color(0xFFE8F2EF) : muted,
              fontSize: 11,
              fontWeight: FontWeight.w700,
            ),
          ),
          const SizedBox(width: 7),
          Text(
            '$count',
            style: TextStyle(
              color: selected ? color : muted,
              fontFamily: 'Consolas',
              fontSize: 10,
            ),
          ),
        ],
      ),
    ),
  );
}

class _LogEventTile extends StatelessWidget {
  const _LogEventTile({super.key, required this.row});
  final Map<String, dynamic> row;

  Color get levelColor {
    final level = '${row['severity'] ?? 'info'}'.toLowerCase();
    if (level == 'error') return danger;
    if (level == 'warning') return amber;
    return blue;
  }

  IconData get eventIcon {
    final type = '${row['event_type'] ?? ''}'.toLowerCase();
    if (type.contains('risk') || type.contains('kill')) {
      return Icons.shield_outlined;
    }
    if (type.contains('order') || type.contains('execution')) {
      return Icons.receipt_long_outlined;
    }
    if (type.contains('position') || type.contains('trade')) {
      return Icons.swap_vert_circle_outlined;
    }
    if (type.contains('engine') || type.contains('signal')) {
      return Icons.memory_rounded;
    }
    return Icons.terminal_rounded;
  }

  @override
  Widget build(BuildContext context) {
    final contextData = mapOf(row['context']);
    final highlights = _logHighlights(contextData);
    final eventType = '${row['event_type'] ?? 'journal_event'}';
    final messageText = '${row['message'] ?? ''}'.trim();
    final symbol = '${row['symbol'] ?? ''}'.trim();
    final timestamp = DateTime.tryParse(
      '${row['created_at'] ?? ''}',
    )?.toLocal();
    String two(int value) => value.toString().padLeft(2, '0');
    final day = timestamp == null
        ? 'UNKNOWN'
        : '${timestamp.year}-${two(timestamp.month)}-${two(timestamp.day)}';
    final time = timestamp == null
        ? '--:--:--'
        : '${two(timestamp.hour)}:${two(timestamp.minute)}:${two(timestamp.second)}';

    return Material(
      color: panel,
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(10),
        side: const BorderSide(color: border),
      ),
      clipBehavior: Clip.antiAlias,
      child: Theme(
        data: Theme.of(context).copyWith(dividerColor: Colors.transparent),
        child: ExpansionTile(
          tilePadding: const EdgeInsets.fromLTRB(14, 8, 12, 8),
          childrenPadding: const EdgeInsets.fromLTRB(70, 0, 16, 14),
          leading: SizedBox(
            width: 40,
            child: Column(
              mainAxisAlignment: MainAxisAlignment.center,
              children: [
                Icon(eventIcon, size: 18, color: levelColor),
                const SizedBox(height: 5),
                Container(
                  width: 5,
                  height: 5,
                  decoration: BoxDecoration(
                    color: levelColor,
                    shape: BoxShape.circle,
                  ),
                ),
              ],
            ),
          ),
          title: Row(
            children: [
              Flexible(
                child: Text(
                  label(eventType.replaceAll('.', '_')),
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: const TextStyle(
                    color: Color(0xFFE4ECE9),
                    fontSize: 13,
                    fontWeight: FontWeight.w800,
                  ),
                ),
              ),
              const SizedBox(width: 9),
              _LogBadge(
                text: '${row['severity'] ?? 'info'}'.toUpperCase(),
                color: levelColor,
              ),
              if (symbol.isNotEmpty) ...[
                const SizedBox(width: 6),
                _LogBadge(text: symbol, color: green),
              ],
              const Spacer(),
              Text(
                day,
                style: const TextStyle(
                  color: muted,
                  fontFamily: 'Consolas',
                  fontSize: 9,
                ),
              ),
              const SizedBox(width: 9),
              Text(
                time,
                style: const TextStyle(
                  color: Color(0xFFB8C7CC),
                  fontFamily: 'Consolas',
                  fontSize: 10,
                  fontWeight: FontWeight.w700,
                ),
              ),
            ],
          ),
          subtitle: Padding(
            padding: const EdgeInsets.only(top: 6),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  messageText.isEmpty
                      ? 'No event message supplied.'
                      : messageText,
                  maxLines: 2,
                  overflow: TextOverflow.ellipsis,
                  style: const TextStyle(
                    color: Color(0xFFAAB9BF),
                    fontSize: 11,
                    height: 1.35,
                  ),
                ),
                if (highlights.isNotEmpty) ...[
                  const SizedBox(height: 7),
                  Wrap(
                    spacing: 6,
                    runSpacing: 5,
                    children: [
                      for (final entry in highlights.entries)
                        _ContextPill(name: entry.key, value: entry.value),
                    ],
                  ),
                ],
              ],
            ),
          ),
          children: [
            Align(
              alignment: Alignment.centerLeft,
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  const Text(
                    'TECHNICAL CONTEXT',
                    style: TextStyle(
                      color: muted,
                      fontSize: 9,
                      fontWeight: FontWeight.w800,
                      letterSpacing: 1.0,
                    ),
                  ),
                  const SizedBox(height: 7),
                  Container(
                    width: double.infinity,
                    constraints: const BoxConstraints(maxHeight: 260),
                    padding: const EdgeInsets.all(12),
                    decoration: BoxDecoration(
                      color: bg,
                      borderRadius: BorderRadius.circular(7),
                      border: Border.all(color: border),
                    ),
                    child: SingleChildScrollView(
                      child: SelectionArea(
                        child: Text(
                          contextData.isEmpty
                              ? 'No additional context.'
                              : const JsonEncoder.withIndent(
                                  '  ',
                                ).convert(contextData),
                          style: const TextStyle(
                            color: Color(0xFF94A8B2),
                            fontFamily: 'Consolas',
                            fontSize: 10,
                            height: 1.45,
                          ),
                        ),
                      ),
                    ),
                  ),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _LogBadge extends StatelessWidget {
  const _LogBadge({required this.text, required this.color});
  final String text;
  final Color color;

  @override
  Widget build(BuildContext context) => Container(
    padding: const EdgeInsets.symmetric(horizontal: 7, vertical: 3),
    decoration: BoxDecoration(
      color: color.withValues(alpha: 0.08),
      borderRadius: BorderRadius.circular(5),
      border: Border.all(color: color.withValues(alpha: 0.22)),
    ),
    child: Text(
      text,
      style: TextStyle(
        color: color,
        fontFamily: 'Consolas',
        fontSize: 8,
        fontWeight: FontWeight.w800,
        letterSpacing: .35,
      ),
    ),
  );
}

class _ContextPill extends StatelessWidget {
  const _ContextPill({required this.name, required this.value});
  final String name;
  final String value;

  @override
  Widget build(BuildContext context) => Container(
    padding: const EdgeInsets.symmetric(horizontal: 7, vertical: 3),
    decoration: BoxDecoration(
      color: const Color(0xFF101B21),
      borderRadius: BorderRadius.circular(5),
      border: Border.all(color: border),
    ),
    child: Text.rich(
      TextSpan(
        children: [
          TextSpan(
            text: '${label(name)}  ',
            style: const TextStyle(color: muted),
          ),
          TextSpan(
            text: value,
            style: const TextStyle(color: Color(0xFFD3DFDB)),
          ),
        ],
      ),
      style: const TextStyle(fontFamily: 'Consolas', fontSize: 9),
    ),
  );
}

Map<String, String> _logHighlights(Map<String, dynamic> context) {
  const keys = [
    'outcome',
    'session',
    'timeframe',
    'signals',
    'decisions',
    'orders',
    'strategy_profile',
    'reason',
  ];
  final values = <String, String>{};
  for (final key in keys) {
    final value = context[key];
    if (value == null || value is Map || value is List || '$value'.isEmpty) {
      continue;
    }
    values[key] = '$value';
    if (values.length == 5) break;
  }
  return values;
}

class BotsPage extends StatefulWidget {
  const BotsPage({super.key, required this.client});
  final ApiClient client;

  @override
  State<BotsPage> createState() => _BotsPageState();
}

class _BotsPageState extends State<BotsPage> {
  dynamic botsData;
  Map<String, dynamic> options = {};
  String? error;
  bool loading = true;
  int? busyBotId;

  @override
  void initState() {
    super.initState();
    load();
  }

  Future<void> load() async {
    if (mounted) {
      setState(() {
        loading = true;
        error = null;
      });
    }
    try {
      final values = await Future.wait([
        widget.client.get('/api/bots/'),
        widget.client.get('/api/bots/options/'),
      ]);
      if (!mounted) return;
      setState(() {
        botsData = values[0];
        options = mapOf(values[1]);
        loading = false;
      });
    } catch (e) {
      if (mounted) {
        setState(() {
          error = e.toString();
          loading = false;
        });
      }
    }
  }

  Future<void> edit([Map<String, dynamic>? bot]) async {
    final payload = await showDialog<Map<String, dynamic>>(
      context: context,
      barrierDismissible: false,
      builder: (_) => _BotEditorDialog(bot: bot, options: options),
    );
    if (payload == null) return;
    try {
      await widget.client.request(
        bot == null ? 'POST' : 'PATCH',
        bot == null ? '/api/bots/' : '/api/bots/${bot['id']}/',
        body: payload,
      );
      if (mounted) {
        message(context, bot == null ? 'Bot created.' : 'Bot updated.');
      }
      await load();
    } catch (e) {
      if (mounted) message(context, e.toString(), isError: true);
    }
  }

  Future<void> control(Map<String, dynamic> bot, String action) async {
    final id = int.tryParse('${bot['id']}');
    if (id == null) return;
    if (action == 'stop' &&
        !await confirm(
          context,
          'Stop ${bot['name']}?',
          'The bot will stop opening new trades. Existing positions remain managed.',
        )) {
      return;
    }
    setState(() => busyBotId = id);
    try {
      await widget.client.post('/api/bots/$id/control/', {'action': action});
      if (mounted) {
        message(context, 'Bot ${label(action).toLowerCase()} command applied.');
      }
      await load();
    } catch (e) {
      if (mounted) message(context, e.toString(), isError: true);
    } finally {
      if (mounted) setState(() => busyBotId = null);
    }
  }

  @override
  Widget build(BuildContext context) {
    if (loading && botsData == null) {
      return const Center(child: CircularProgressIndicator());
    }
    if (error != null && botsData == null) {
      return Empty(icon: Icons.cloud_off, text: error!);
    }
    final bots = listOfMaps(botsData);
    final usage = mapOf(options['usage']);
    final count =
        int.tryParse('${usage['bots'] ?? bots.length}') ?? bots.length;
    final limit = int.tryParse('${usage['bot_limit'] ?? 0}') ?? 0;
    final canCreate = limit == 0 || count < limit;
    final hasAccounts = listOfMaps(options['accounts']).isNotEmpty;
    final hasAssets = listOfMaps(options['assets']).isNotEmpty;

    return RefreshIndicator(
      onRefresh: load,
      child: ListView(
        padding: const EdgeInsets.fromLTRB(24, 22, 24, 30),
        children: [
          _WorkspaceHeader(
            eyebrow: 'CLIENT WORKSPACE',
            title: 'Bot manager',
            description:
                'Create, configure and operate your automation engines. Every bot is isolated to your account.',
            badge: limit > 0 ? '$count / $limit BOTS' : '$count BOTS',
            action: FilledButton.icon(
              onPressed: canCreate && hasAccounts && hasAssets
                  ? () => edit()
                  : null,
              icon: const Icon(Icons.add_rounded, size: 18),
              label: const Text('Create bot'),
            ),
          ),
          if (!hasAccounts) ...[
            const SizedBox(height: 12),
            const _InlineNotice(
              icon: Icons.account_balance_outlined,
              text: 'Add an MT5 account in Settings before creating a bot.',
              color: amber,
            ),
          ],
          if (!hasAssets) ...[
            const SizedBox(height: 12),
            const _InlineNotice(
              icon: Icons.candlestick_chart_outlined,
              text:
                  'No tradable assets are currently available for bot creation.',
              color: amber,
            ),
          ],
          if (!canCreate) ...[
            const SizedBox(height: 12),
            _InlineNotice(
              icon: Icons.lock_outline_rounded,
              text:
                  'Your current plan allows $limit bot${limit == 1 ? '' : 's'}.',
              color: amber,
            ),
          ],
          const SizedBox(height: 14),
          if (bots.isEmpty)
            _EmptyWorkspace(
              icon: Icons.smart_toy_outlined,
              title: 'No bots configured',
              text:
                  'Create your first bot, select a broker account and asset, then review its risk settings before starting it.',
              action: canCreate && hasAccounts && hasAssets
                  ? FilledButton.icon(
                      onPressed: () => edit(),
                      icon: const Icon(Icons.add_rounded),
                      label: const Text('Create first bot'),
                    )
                  : null,
            )
          else
            for (var index = 0; index < bots.length; index++) ...[
              _BotCard(
                bot: bots[index],
                busy: busyBotId == bots[index]['id'],
                onEdit: () => edit(bots[index]),
                onControl: (action) => control(bots[index], action),
              ),
              if (index != bots.length - 1) const SizedBox(height: 10),
            ],
        ],
      ),
    );
  }
}

class _BotEditorDialog extends StatefulWidget {
  const _BotEditorDialog({required this.bot, required this.options});
  final Map<String, dynamic>? bot;
  final Map<String, dynamic> options;

  @override
  State<_BotEditorDialog> createState() => _BotEditorDialogState();
}

class _BotEditorDialogState extends State<_BotEditorDialog> {
  late final TextEditingController name;
  late final TextEditingController qty;
  late final TextEditingController decisionScore;
  late final TextEditingController maxPositions;
  late final TextEditingController maxTrades;
  late final TextEditingController interval;
  final editorScroll = ScrollController();
  int? assetId;
  int? accountId;
  String engineMode = 'harami';
  String timeframe = '5m';
  String tradingProfile = 'very_safe';
  bool autoTrade = true;
  final selectedStrategies = <String>{};

  List<Map<String, dynamic>> get assets => listOfMaps(widget.options['assets']);
  List<Map<String, dynamic>> get accounts =>
      listOfMaps(widget.options['accounts']);
  List<Map<String, dynamic>> get engineModes =>
      listOfMaps(widget.options['engine_modes']);
  List<Map<String, dynamic>> get strategies =>
      listOfMaps(widget.options['strategies']);
  List<Map<String, dynamic>> get tradingProfiles =>
      listOfMaps(widget.options['trading_profiles']);
  List<String> get timeframes => widget.options['timeframes'] is List
      ? List<dynamic>.from(
          widget.options['timeframes'],
        ).map((v) => '$v').toList()
      : const ['1m', '5m', '15m', '30m', '1h', '4h', '1d'];

  @override
  void initState() {
    super.initState();
    final bot = widget.bot;
    assetId =
        integerValue(bot?['asset']) ?? integerValue(assets.firstOrNull?['id']);
    accountId =
        integerValue(bot?['broker_account']) ??
        integerValue(accounts.firstOrNull?['id']);
    engineMode = '${bot?['engine_mode'] ?? 'harami'}';
    timeframe = '${bot?['default_timeframe'] ?? '5m'}';
    tradingProfile = '${bot?['trading_profile'] ?? 'very_safe'}';
    autoTrade = bot?['auto_trade'] != false;
    selectedStrategies.addAll(
      bot?['enabled_strategies'] is List
          ? List<dynamic>.from(bot!['enabled_strategies']).map((v) => '$v')
          : const ['harami'],
    );
    name = TextEditingController(text: '${bot?['name'] ?? ''}');
    qty = TextEditingController(
      text:
          '${bot?['default_qty'] ?? selectedAsset?['recommended_qty'] ?? '0.01'}',
    );
    decisionScore = TextEditingController(
      text: '${bot?['decision_min_score'] ?? '0.5'}',
    );
    maxPositions = TextEditingController(
      text: '${bot?['risk_max_concurrent_positions'] ?? '5'}',
    );
    maxTrades = TextEditingController(
      text: '${bot?['max_trades_per_day'] ?? '10'}',
    );
    interval = TextEditingController(
      text: '${bot?['trade_interval_minutes'] ?? '15'}',
    );
  }

  Map<String, dynamic>? get selectedAsset {
    for (final asset in assets) {
      if (integerValue(asset['id']) == assetId) return asset;
    }
    return null;
  }

  @override
  void dispose() {
    editorScroll.dispose();
    name.dispose();
    qty.dispose();
    decisionScore.dispose();
    maxPositions.dispose();
    maxTrades.dispose();
    interval.dispose();
    super.dispose();
  }

  void submit() {
    if (name.text.trim().isEmpty || assetId == null || accountId == null) {
      message(
        context,
        'Name, broker account and asset are required.',
        isError: true,
      );
      return;
    }
    if (!autoTrade && selectedStrategies.isEmpty) {
      message(
        context,
        'Select at least one strategy for manual routing.',
        isError: true,
      );
      return;
    }
    Navigator.pop(context, <String, dynamic>{
      'name': name.text.trim(),
      'asset': assetId,
      'broker_account': accountId,
      'engine_mode': engineMode,
      'default_timeframe': timeframe,
      'default_qty': qty.text.trim(),
      'auto_trade': autoTrade,
      'enabled_strategies': selectedStrategies.toList()..sort(),
      'decision_min_score': decisionScore.text.trim(),
      'risk_max_concurrent_positions': maxPositions.text.trim(),
      'max_trades_per_day': maxTrades.text.trim(),
      'trade_interval_minutes': interval.text.trim(),
      'trading_profile': tradingProfile,
    });
  }

  Widget _responsiveFields(List<Widget> fields, {List<int>? flexes}) =>
      LayoutBuilder(
        builder: (context, constraints) {
          if (constraints.maxWidth < 620) {
            return Column(
              children: [
                for (var index = 0; index < fields.length; index++) ...[
                  fields[index],
                  if (index != fields.length - 1) const SizedBox(height: 12),
                ],
              ],
            );
          }
          return Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              for (var index = 0; index < fields.length; index++) ...[
                Expanded(
                  flex: flexes == null ? 1 : flexes[index],
                  child: fields[index],
                ),
                if (index != fields.length - 1) const SizedBox(width: 12),
              ],
            ],
          );
        },
      );

  Widget _sectionHeading(String title, String description) => Column(
    crossAxisAlignment: CrossAxisAlignment.start,
    children: [
      Text(
        title.toUpperCase(),
        style: const TextStyle(
          color: blue,
          fontSize: 10,
          fontWeight: FontWeight.w800,
          letterSpacing: 1.1,
        ),
      ),
      const SizedBox(height: 3),
      Text(
        description,
        style: const TextStyle(color: muted, fontSize: 11, height: 1.35),
      ),
    ],
  );

  Widget _dropdownText(String value) => Text(
    value,
    maxLines: 1,
    overflow: TextOverflow.ellipsis,
    style: const TextStyle(fontWeight: FontWeight.w600),
  );

  String _strategyTitle(Map<String, dynamic> strategy) {
    final value = '${strategy['label'] ?? label('${strategy['value']}')}';
    final detailIndex = value.indexOf(' (');
    return detailIndex < 0 ? value : value.substring(0, detailIndex);
  }

  Widget _strategyPicker() => Wrap(
    spacing: 8,
    runSpacing: 8,
    children: [
      for (final strategy in strategies)
        Builder(
          builder: (context) {
            final value = '${strategy['value']}';
            final fullLabel =
                '${strategy['label'] ?? label('${strategy['value']}')}';
            final selected = selectedStrategies.contains(value);
            return Tooltip(
              message: fullLabel,
              waitDuration: const Duration(milliseconds: 450),
              child: FilterChip(
                label: Text(_strategyTitle(strategy)),
                selected: selected,
                showCheckmark: true,
                checkmarkColor: bg,
                selectedColor: green.withValues(alpha: 0.75),
                backgroundColor: const Color(0xFF0A1217),
                side: BorderSide(
                  color: selected ? green.withValues(alpha: 0.5) : border,
                ),
                labelStyle: TextStyle(
                  color: selected ? bg : const Color(0xFFD8E2E6),
                  fontSize: 11,
                  fontWeight: FontWeight.w700,
                ),
                onSelected: (enabled) => setState(() {
                  enabled
                      ? selectedStrategies.add(value)
                      : selectedStrategies.remove(value);
                }),
              ),
            );
          },
        ),
    ],
  );

  Widget _automaticExecutionCard() => Container(
    padding: const EdgeInsets.fromLTRB(14, 10, 10, 10),
    decoration: BoxDecoration(
      color: const Color(0xFF0A1217),
      borderRadius: BorderRadius.circular(11),
      border: Border.all(
        color: autoTrade ? green.withValues(alpha: 0.4) : border,
      ),
    ),
    child: Row(
      children: [
        Container(
          width: 34,
          height: 34,
          decoration: BoxDecoration(
            color: autoTrade ? green.withValues(alpha: 0.12) : panel2,
            borderRadius: BorderRadius.circular(9),
          ),
          child: Icon(
            Icons.bolt_rounded,
            color: autoTrade ? green : muted,
            size: 19,
          ),
        ),
        const SizedBox(width: 11),
        const Expanded(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                'Automatic execution',
                style: TextStyle(fontSize: 13, fontWeight: FontWeight.w700),
              ),
              SizedBox(height: 2),
              Text(
                'Place accepted orders automatically. Account risk limits always apply.',
                style: TextStyle(color: muted, fontSize: 10, height: 1.3),
              ),
            ],
          ),
        ),
        const SizedBox(width: 8),
        Switch(
          value: autoTrade,
          onChanged: (value) => setState(() => autoTrade = value),
        ),
      ],
    ),
  );

  @override
  Widget build(BuildContext context) {
    final viewport = MediaQuery.sizeOf(context);
    return AlertDialog(
      backgroundColor: panel,
      surfaceTintColor: Colors.transparent,
      insetPadding: EdgeInsets.symmetric(
        horizontal: viewport.width < 700 ? 12 : 32,
        vertical: 20,
      ),
      clipBehavior: Clip.antiAlias,
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(18),
        side: const BorderSide(color: border),
      ),
      titlePadding: const EdgeInsets.fromLTRB(24, 20, 14, 18),
      contentPadding: const EdgeInsets.fromLTRB(24, 0, 24, 0),
      title: Row(
        children: [
          Container(
            width: 38,
            height: 38,
            decoration: BoxDecoration(
              color: green.withValues(alpha: 0.12),
              borderRadius: BorderRadius.circular(10),
            ),
            child: const Icon(Icons.smart_toy_outlined, color: green, size: 20),
          ),
          const SizedBox(width: 12),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  widget.bot == null
                      ? 'Create automation bot'
                      : 'Edit bot configuration',
                  style: const TextStyle(
                    fontSize: 20,
                    fontWeight: FontWeight.w700,
                  ),
                ),
                const SizedBox(height: 3),
                const Text(
                  'Configure execution, strategy routing and risk limits.',
                  style: TextStyle(color: muted, fontSize: 11),
                ),
              ],
            ),
          ),
          IconButton(
            tooltip: 'Close',
            onPressed: () => Navigator.pop(context),
            icon: const Icon(Icons.close_rounded, color: muted),
          ),
        ],
      ),
      content: SizedBox(
        width: 760,
        height: viewport.height * 0.68,
        child: Scrollbar(
          controller: editorScroll,
          thumbVisibility: true,
          child: SingleChildScrollView(
            controller: editorScroll,
            padding: const EdgeInsets.fromLTRB(0, 18, 12, 20),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                _sectionHeading(
                  'Execution setup',
                  'Choose where and how this bot trades.',
                ),
                const SizedBox(height: 12),
                TextField(
                  controller: name,
                  textInputAction: TextInputAction.next,
                  decoration: const InputDecoration(labelText: 'Bot name'),
                ),
                const SizedBox(height: 12),
                _responsiveFields([
                  DropdownButtonFormField<int>(
                    initialValue: accountId,
                    isExpanded: true,
                    decoration: const InputDecoration(
                      labelText: 'Broker account',
                    ),
                    items: [
                      for (final account in accounts)
                        DropdownMenuItem(
                          value: integerValue(account['id']),
                          child: _dropdownText(
                            '${account['name']} · ${account['mt5_login']}',
                          ),
                        ),
                    ],
                    onChanged: (value) => setState(() => accountId = value),
                  ),
                  DropdownButtonFormField<int>(
                    initialValue: assetId,
                    isExpanded: true,
                    decoration: const InputDecoration(
                      labelText: 'Trading asset',
                    ),
                    items: [
                      for (final asset in assets)
                        DropdownMenuItem(
                          value: integerValue(asset['id']),
                          child: _dropdownText(
                            '${asset['symbol']} · ${asset['display_name']}',
                          ),
                        ),
                    ],
                    onChanged: (value) {
                      setState(() {
                        assetId = value;
                        if (widget.bot == null && selectedAsset != null) {
                          qty.text = '${selectedAsset!['recommended_qty']}';
                        }
                      });
                    },
                  ),
                ]),
                const SizedBox(height: 12),
                _responsiveFields(
                  [
                    DropdownButtonFormField<String>(
                      initialValue: engineMode,
                      isExpanded: true,
                      decoration: const InputDecoration(
                        labelText: 'Engine mode',
                      ),
                      items: [
                        for (final mode in engineModes)
                          DropdownMenuItem(
                            value: '${mode['value']}',
                            child: _dropdownText('${mode['label']}'),
                          ),
                      ],
                      onChanged: (value) =>
                          setState(() => engineMode = value ?? engineMode),
                    ),
                    DropdownButtonFormField<String>(
                      initialValue: timeframe,
                      isExpanded: true,
                      decoration: const InputDecoration(
                        labelText: 'Primary timeframe',
                      ),
                      items: [
                        for (final value in timeframes)
                          DropdownMenuItem(
                            value: value,
                            child: _dropdownText(value.toUpperCase()),
                          ),
                      ],
                      onChanged: (value) =>
                          setState(() => timeframe = value ?? timeframe),
                    ),
                  ],
                  flexes: const [2, 1],
                ),
                const SizedBox(height: 12),
                _responsiveFields(
                  [
                    DropdownButtonFormField<String>(
                      initialValue: tradingProfile,
                      isExpanded: true,
                      decoration: const InputDecoration(
                        labelText: 'Trading profile',
                      ),
                      items: [
                        for (final profile in tradingProfiles)
                          DropdownMenuItem(
                            value: '${profile['value']}',
                            child: _dropdownText('${profile['label']}'),
                          ),
                      ],
                      onChanged: (value) => setState(
                        () => tradingProfile = value ?? tradingProfile,
                      ),
                    ),
                    TextField(
                      controller: qty,
                      keyboardType: const TextInputType.numberWithOptions(
                        decimal: true,
                      ),
                      decoration: InputDecoration(
                        labelText: 'Default lot size',
                        suffixText: 'lots',
                        helperText: selectedAsset == null
                            ? null
                            : 'Minimum ${selectedAsset!['min_qty']}',
                      ),
                    ),
                  ],
                  flexes: const [2, 1],
                ),
                const SizedBox(height: 16),
                _automaticExecutionCard(),
                const SizedBox(height: 24),
                Row(
                  crossAxisAlignment: CrossAxisAlignment.end,
                  children: [
                    Expanded(
                      child: _sectionHeading(
                        'Strategy routing',
                        'Select the signal models this bot may execute.',
                      ),
                    ),
                    TextButton(
                      onPressed: selectedStrategies.length == strategies.length
                          ? () => setState(selectedStrategies.clear)
                          : () => setState(() {
                              selectedStrategies
                                ..clear()
                                ..addAll(
                                  strategies.map(
                                    (strategy) => '${strategy['value']}',
                                  ),
                                );
                            }),
                      child: Text(
                        selectedStrategies.length == strategies.length
                            ? 'Clear all'
                            : 'Select all',
                      ),
                    ),
                  ],
                ),
                const SizedBox(height: 10),
                _strategyPicker(),
                const SizedBox(height: 24),
                _sectionHeading(
                  'Risk & cadence',
                  'Set conservative entry thresholds and activity limits.',
                ),
                const SizedBox(height: 12),
                _responsiveFields([
                  TextField(
                    controller: decisionScore,
                    keyboardType: const TextInputType.numberWithOptions(
                      decimal: true,
                    ),
                    decoration: const InputDecoration(
                      labelText: 'Minimum signal score',
                      hintText: '0.50',
                    ),
                  ),
                  TextField(
                    controller: maxPositions,
                    keyboardType: TextInputType.number,
                    decoration: const InputDecoration(
                      labelText: 'Maximum open positions',
                    ),
                  ),
                ]),
                const SizedBox(height: 12),
                _responsiveFields([
                  TextField(
                    controller: maxTrades,
                    keyboardType: TextInputType.number,
                    decoration: const InputDecoration(
                      labelText: 'Maximum trades per day',
                    ),
                  ),
                  TextField(
                    controller: interval,
                    keyboardType: TextInputType.number,
                    decoration: const InputDecoration(
                      labelText: 'Minimum trade interval',
                      suffixText: 'min',
                    ),
                  ),
                ]),
              ],
            ),
          ),
        ),
      ),
      actionsPadding: const EdgeInsets.fromLTRB(18, 12, 18, 14),
      actions: [
        TextButton(
          onPressed: () => Navigator.pop(context),
          child: const Text('Cancel'),
        ),
        FilledButton.icon(
          onPressed: submit,
          icon: Icon(
            widget.bot == null ? Icons.add_rounded : Icons.save_outlined,
            size: 17,
          ),
          label: Text(widget.bot == null ? 'Create bot' : 'Save changes'),
        ),
      ],
    );
  }
}

class _BotCard extends StatelessWidget {
  const _BotCard({
    required this.bot,
    required this.busy,
    required this.onEdit,
    required this.onControl,
  });
  final Map<String, dynamic> bot;
  final bool busy;
  final VoidCallback onEdit;
  final ValueChanged<String> onControl;

  @override
  Widget build(BuildContext context) {
    final status = '${bot['status'] ?? 'stopped'}'.toLowerCase();
    final statusAccent = status == 'active'
        ? green
        : status == 'paused'
        ? amber
        : muted;
    final asset = mapOf(bot['asset_details']);
    final account = mapOf(bot['broker_account_details']);
    final strategies = bot['enabled_strategies'] is List
        ? List<dynamic>.from(bot['enabled_strategies'])
        : <dynamic>[];
    return Container(
      padding: const EdgeInsets.all(17),
      decoration: BoxDecoration(
        color: panel,
        borderRadius: BorderRadius.circular(11),
        border: Border.all(
          color: status == 'active' ? const Color(0xFF205943) : border,
        ),
      ),
      child: Row(
        children: [
          Container(
            width: 44,
            height: 44,
            decoration: BoxDecoration(
              color: statusAccent.withValues(alpha: 0.08),
              borderRadius: BorderRadius.circular(9),
              border: Border.all(color: statusAccent.withValues(alpha: 0.2)),
            ),
            child: Icon(
              Icons.smart_toy_outlined,
              color: statusAccent,
              size: 22,
            ),
          ),
          const SizedBox(width: 14),
          SizedBox(
            width: 220,
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  '${bot['name']}',
                  overflow: TextOverflow.ellipsis,
                  style: const TextStyle(
                    fontSize: 14,
                    fontWeight: FontWeight.w800,
                  ),
                ),
                const SizedBox(height: 5),
                Row(
                  children: [
                    _PulseDot(color: statusAccent, size: 6),
                    const SizedBox(width: 7),
                    Text(
                      status.toUpperCase(),
                      style: TextStyle(
                        color: statusAccent,
                        fontFamily: 'Consolas',
                        fontSize: 9,
                        fontWeight: FontWeight.w800,
                        letterSpacing: 0.8,
                      ),
                    ),
                    const SizedBox(width: 9),
                    Flexible(
                      child: Text(
                        '#${bot['bot_id'] ?? bot['id']}',
                        overflow: TextOverflow.ellipsis,
                        style: const TextStyle(
                          color: muted,
                          fontFamily: 'Consolas',
                          fontSize: 9,
                        ),
                      ),
                    ),
                  ],
                ),
              ],
            ),
          ),
          const SizedBox(width: 18),
          Expanded(
            child: Wrap(
              spacing: 22,
              runSpacing: 8,
              children: [
                _BotDatum(
                  label: 'ASSET',
                  value: '${asset['symbol'] ?? '—'}',
                  accent: blue,
                ),
                _BotDatum(
                  label: 'ENGINE',
                  value: '${bot['engine_mode'] ?? '—'}'.toUpperCase(),
                ),
                _BotDatum(
                  label: 'TIMEFRAME',
                  value: '${bot['default_timeframe'] ?? '—'}'.toUpperCase(),
                ),
                _BotDatum(
                  label: 'LOT SIZE',
                  value: '${bot['default_qty'] ?? '—'}',
                ),
                _BotDatum(label: 'ACCOUNT', value: '${account['name'] ?? '—'}'),
                _BotDatum(
                  label: 'ROUTING',
                  value: bot['auto_trade'] == true
                      ? 'AUTO'
                      : '${strategies.length} MANUAL',
                  accent: bot['auto_trade'] == true ? green : amber,
                ),
              ],
            ),
          ),
          const SizedBox(width: 14),
          if (busy)
            const SizedBox.square(
              dimension: 22,
              child: CircularProgressIndicator(strokeWidth: 2),
            )
          else
            Wrap(
              spacing: 5,
              children: [
                IconButton(
                  tooltip: 'Edit bot',
                  onPressed: onEdit,
                  icon: const Icon(Icons.tune_rounded, size: 19),
                ),
                if (status != 'active')
                  FilledButton.icon(
                    onPressed: () => onControl('start'),
                    icon: const Icon(Icons.play_arrow_rounded, size: 17),
                    label: const Text('Start'),
                  )
                else
                  OutlinedButton.icon(
                    onPressed: () => onControl('pause'),
                    icon: const Icon(Icons.pause_rounded, size: 16),
                    label: const Text('Pause'),
                  ),
                if (status != 'stopped')
                  IconButton(
                    tooltip: 'Stop bot',
                    onPressed: () => onControl('stop'),
                    icon: const Icon(
                      Icons.stop_circle_outlined,
                      color: danger,
                      size: 20,
                    ),
                  ),
              ],
            ),
        ],
      ),
    );
  }
}

class _BotDatum extends StatelessWidget {
  const _BotDatum({required this.label, required this.value, this.accent});
  final String label;
  final String value;
  final Color? accent;

  @override
  Widget build(BuildContext context) => SizedBox(
    width: 94,
    child: Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          label,
          style: const TextStyle(
            color: muted,
            fontSize: 8,
            fontWeight: FontWeight.w800,
            letterSpacing: 0.8,
          ),
        ),
        const SizedBox(height: 4),
        Text(
          value,
          overflow: TextOverflow.ellipsis,
          style: TextStyle(
            color: accent ?? const Color(0xFFDDE8E4),
            fontFamily: 'Consolas',
            fontSize: 11,
            fontWeight: FontWeight.w700,
          ),
        ),
      ],
    ),
  );
}

class _WorkspaceHeader extends StatelessWidget {
  const _WorkspaceHeader({
    required this.eyebrow,
    required this.title,
    required this.description,
    required this.badge,
    required this.action,
  });
  final String eyebrow;
  final String title;
  final String description;
  final String badge;
  final Widget action;

  @override
  Widget build(BuildContext context) => Container(
    padding: const EdgeInsets.all(18),
    decoration: BoxDecoration(
      gradient: const LinearGradient(
        colors: [Color(0xFF101B21), Color(0xFF0B1419)],
      ),
      borderRadius: BorderRadius.circular(11),
      border: Border.all(color: border),
    ),
    child: LayoutBuilder(
      builder: (context, constraints) {
        final details = Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              eyebrow,
              style: const TextStyle(
                color: blue,
                fontSize: 9,
                fontWeight: FontWeight.w800,
                letterSpacing: 1.2,
              ),
            ),
            const SizedBox(height: 5),
            Text(
              title,
              style: const TextStyle(fontSize: 19, fontWeight: FontWeight.w800),
            ),
            const SizedBox(height: 5),
            Text(
              description,
              style: const TextStyle(color: muted, fontSize: 11, height: 1.35),
            ),
          ],
        );
        final controls = Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            _StatusPill(text: badge, color: blue),
            const SizedBox(width: 12),
            action,
          ],
        );
        if (constraints.maxWidth < 680) {
          return Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              details,
              const SizedBox(height: 14),
              Align(alignment: Alignment.centerRight, child: controls),
            ],
          );
        }
        return Row(
          children: [
            Expanded(child: details),
            controls,
          ],
        );
      },
    ),
  );
}

class _EmptyWorkspace extends StatelessWidget {
  const _EmptyWorkspace({
    required this.icon,
    required this.title,
    required this.text,
    this.action,
  });
  final IconData icon;
  final String title;
  final String text;
  final Widget? action;

  @override
  Widget build(BuildContext context) => Container(
    padding: const EdgeInsets.symmetric(horizontal: 30, vertical: 48),
    decoration: BoxDecoration(
      color: panel,
      borderRadius: BorderRadius.circular(11),
      border: Border.all(color: border),
    ),
    child: Column(
      children: [
        Icon(icon, color: muted, size: 38),
        const SizedBox(height: 12),
        Text(
          title,
          style: const TextStyle(fontSize: 16, fontWeight: FontWeight.w800),
        ),
        const SizedBox(height: 7),
        ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 520),
          child: Text(
            text,
            textAlign: TextAlign.center,
            style: const TextStyle(color: muted, fontSize: 11),
          ),
        ),
        if (action != null) ...[const SizedBox(height: 18), action!],
      ],
    ),
  );
}

class MarketsPage extends StatefulWidget {
  const MarketsPage({super.key, required this.client});
  final ApiClient client;
  @override
  State<MarketsPage> createState() => _MarketsPageState();
}

class _MarketsPageState extends State<MarketsPage> {
  late Future<dynamic> future = widget.client.get('/api/personal/markets/');

  void reload() {
    setState(() {
      future = widget.client.get('/api/personal/markets/');
    });
  }

  Future<void> toggle(Map<String, dynamic> row, bool enabled) async {
    try {
      await widget.client.patch('/api/personal/markets/', {
        'canonical_symbol': row['canonical_symbol'],
        'enabled': enabled,
      });
      reload();
      if (mounted) {
        message(
          context,
          '${row['canonical_symbol']} ${enabled ? 'enabled' : 'disabled'} for this account.',
        );
      }
    } catch (e) {
      if (mounted) message(context, e.toString(), isError: true);
    }
  }

  @override
  Widget build(BuildContext context) => FutureBuilder(
    future: future,
    builder: (_, snapshot) {
      if (snapshot.hasError) {
        return Empty(icon: Icons.cloud_off, text: snapshot.error.toString());
      }
      if (!snapshot.hasData) {
        return const Center(child: CircularProgressIndicator());
      }
      final markets = listOfMaps(snapshot.data);
      final enabled = markets.where((row) => row['enabled'] == true).length;
      return Column(
        children: [
          Padding(
            padding: const EdgeInsets.fromLTRB(24, 22, 24, 0),
            child: _WorkspaceHeader(
              eyebrow: 'ACCOUNT UNIVERSE',
              title: 'Assets & markets',
              description:
                  'Enable platform-approved instruments for your account and monitor broker symbol resolution.',
              badge: '$enabled / ${markets.length} ENABLED',
              action: OutlinedButton.icon(
                onPressed: reload,
                icon: const Icon(Icons.sync_rounded, size: 17),
                label: const Text('Reload markets'),
              ),
            ),
          ),
          const SizedBox(height: 14),
          Expanded(
            child: markets.isEmpty
                ? const _EmptyWorkspace(
                    icon: Icons.candlestick_chart_outlined,
                    title: 'No assets available',
                    text:
                        'The platform asset catalogue is empty. Contact the platform administrator.',
                  )
                : LayoutBuilder(
                    builder: (_, constraints) {
                      final columns = constraints.maxWidth >= 1250
                          ? 3
                          : constraints.maxWidth >= 760
                          ? 2
                          : 1;
                      return GridView.builder(
                        padding: const EdgeInsets.fromLTRB(24, 0, 24, 28),
                        gridDelegate: SliverGridDelegateWithFixedCrossAxisCount(
                          crossAxisCount: columns,
                          crossAxisSpacing: 10,
                          mainAxisSpacing: 10,
                          mainAxisExtent: 150,
                        ),
                        itemCount: markets.length,
                        itemBuilder: (_, index) => _MarketAssetCard(
                          market: markets[index],
                          onChanged: (value) => toggle(markets[index], value),
                        ),
                      );
                    },
                  ),
          ),
        ],
      );
    },
  );
}

class _MarketAssetCard extends StatelessWidget {
  const _MarketAssetCard({required this.market, required this.onChanged});
  final Map<String, dynamic> market;
  final ValueChanged<bool> onChanged;

  @override
  Widget build(BuildContext context) {
    final status = '${market['trading_status'] ?? 'not_synced'}'.toLowerCase();
    final accent = status == 'open'
        ? green
        : status == 'closed'
        ? amber
        : status == 'unavailable'
        ? danger
        : muted;
    final enabled = market['enabled'] == true;
    return Container(
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: panel,
        borderRadius: BorderRadius.circular(10),
        border: Border.all(color: enabled ? const Color(0xFF214E3D) : border),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Container(
                width: 34,
                height: 34,
                decoration: BoxDecoration(
                  color: accent.withValues(alpha: 0.08),
                  borderRadius: BorderRadius.circular(7),
                ),
                child: Icon(
                  Icons.candlestick_chart_rounded,
                  color: accent,
                  size: 18,
                ),
              ),
              const SizedBox(width: 10),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      '${market['canonical_symbol'] ?? market['symbol']}',
                      style: const TextStyle(
                        fontFamily: 'Consolas',
                        fontSize: 14,
                        fontWeight: FontWeight.w800,
                      ),
                    ),
                    const SizedBox(height: 2),
                    Text(
                      '${market['display_name'] ?? ''} · ${label('${market['category'] ?? ''}')}',
                      overflow: TextOverflow.ellipsis,
                      style: const TextStyle(color: muted, fontSize: 10),
                    ),
                  ],
                ),
              ),
              _StatusPill(
                text: status.replaceAll('_', ' ').toUpperCase(),
                color: accent,
              ),
              const SizedBox(width: 5),
              Switch(value: enabled, onChanged: onChanged),
            ],
          ),
          const Padding(
            padding: EdgeInsets.symmetric(vertical: 11),
            child: Divider(height: 1),
          ),
          Row(
            children: [
              Expanded(
                child: _MarketDatum(
                  label: 'BROKER SYMBOL',
                  value:
                      '${market['broker_symbol'] == '' ? 'Not resolved' : market['broker_symbol'] ?? 'Not resolved'}',
                ),
              ),
              Expanded(
                child: _MarketDatum(
                  label: 'BID',
                  value: compactNumber(market['bid']),
                ),
              ),
              Expanded(
                child: _MarketDatum(
                  label: 'ASK',
                  value: compactNumber(market['ask']),
                ),
              ),
              Expanded(
                child: _MarketDatum(
                  label: 'SPREAD',
                  value: compactNumber(market['spread']),
                  accent: accent,
                ),
              ),
              Expanded(
                child: _MarketDatum(
                  label: 'REC. LOT',
                  value: compactNumber(market['recommended_qty']),
                ),
              ),
            ],
          ),
        ],
      ),
    );
  }
}

class _MarketDatum extends StatelessWidget {
  const _MarketDatum({required this.label, required this.value, this.accent});
  final String label;
  final String value;
  final Color? accent;

  @override
  Widget build(BuildContext context) => Column(
    crossAxisAlignment: CrossAxisAlignment.start,
    children: [
      Text(
        label,
        overflow: TextOverflow.ellipsis,
        style: const TextStyle(
          color: muted,
          fontSize: 7,
          fontWeight: FontWeight.w800,
          letterSpacing: 0.6,
        ),
      ),
      const SizedBox(height: 4),
      Text(
        value,
        overflow: TextOverflow.ellipsis,
        style: TextStyle(
          color: accent ?? const Color(0xFFCFDAD6),
          fontFamily: 'Consolas',
          fontSize: 10,
          fontWeight: FontWeight.w700,
        ),
      ),
    ],
  );
}

class PositionsPage extends StatefulWidget {
  const PositionsPage({super.key, required this.client});
  final ApiClient client;
  @override
  State<PositionsPage> createState() => _PositionsPageState();
}

class _PositionsPageState extends State<PositionsPage> {
  late Future<dynamic> future = widget.client.get('/api/personal/positions/');
  Future<void> close(Map<String, dynamic> row) async {
    if (!await confirm(
      context,
      'Close position',
      'Close EZ Trade ticket ${row['broker_position_ticket']}?',
    )) {
      return;
    }
    await action(row, {'action': 'close'});
  }

  Future<void> modify(Map<String, dynamic> row) async {
    final sl = TextEditingController(text: row['sl']?.toString() ?? '');
    final tp = TextEditingController(text: row['tp']?.toString() ?? '');
    final ok =
        await showDialog<bool>(
          context: context,
          builder: (_) => AlertDialog(
            title: Text('Protection · ${row['symbol']}'),
            content: SizedBox(
              width: 360,
              child: Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  TextField(
                    controller: sl,
                    decoration: const InputDecoration(labelText: 'Stop loss'),
                  ),
                  const SizedBox(height: 12),
                  TextField(
                    controller: tp,
                    decoration: const InputDecoration(labelText: 'Take profit'),
                  ),
                ],
              ),
            ),
            actions: [
              TextButton(
                onPressed: () => Navigator.pop(context, false),
                child: const Text('Cancel'),
              ),
              FilledButton(
                onPressed: () => Navigator.pop(context, true),
                child: const Text('Queue change'),
              ),
            ],
          ),
        ) ??
        false;
    if (ok) {
      await action(row, {
        'action': 'modify_protection',
        if (sl.text.trim().isNotEmpty) 'sl': sl.text.trim(),
        if (tp.text.trim().isNotEmpty) 'tp': tp.text.trim(),
      });
    }
  }

  Future<void> action(
    Map<String, dynamic> row,
    Map<String, dynamic> body,
  ) async {
    try {
      await widget.client.post(
        '/api/personal/positions/${row['id']}/action/',
        body,
      );
      if (mounted) {
        message(context, 'Action queued on the serialized MT5 worker.');
      }
      setState(() {
        future = widget.client.get('/api/personal/positions/');
      });
    } catch (e) {
      if (mounted) message(context, e.toString(), isError: true);
    }
  }

  @override
  Widget build(BuildContext context) => FutureBuilder(
    future: future,
    builder: (_, snapshot) {
      if (snapshot.hasError) {
        return Empty(icon: Icons.cloud_off, text: snapshot.error.toString());
      }
      if (!snapshot.hasData) {
        return const Center(child: CircularProgressIndicator());
      }
      return Records(
        data: snapshot.data,
        empty: 'No broker positions reconciled.',
        trailing: (row) => row['manageable'] == true
            ? Wrap(
                spacing: 4,
                children: [
                  IconButton(
                    tooltip: 'Modify SL/TP',
                    onPressed: () => modify(row),
                    icon: const Icon(Icons.tune),
                  ),
                  IconButton(
                    tooltip: 'Close ticket',
                    onPressed: () => close(row),
                    icon: const Icon(Icons.close, color: danger),
                  ),
                ],
              )
            : const Tooltip(
                message: 'Manual/external positions are read-only',
                child: Icon(Icons.lock_outline, color: muted),
              ),
      );
    },
  );
}

class BacktestingPage extends StatefulWidget {
  const BacktestingPage({super.key, required this.client});
  final ApiClient client;

  @override
  State<BacktestingPage> createState() => _BacktestingPageState();
}

class _BacktestingPageState extends State<BacktestingPage> {
  late Future<dynamic> future = widget.client.get('/api/personal/backtesting/');

  Future<void> reload() async {
    final next = widget.client.get('/api/personal/backtesting/');
    setState(() => future = next);
    await next;
  }

  @override
  Widget build(BuildContext context) => FutureBuilder(
    future: future,
    builder: (context, snapshot) {
      if (snapshot.hasError) {
        return Empty(icon: Icons.cloud_off, text: snapshot.error.toString());
      }
      if (!snapshot.hasData) {
        return const Center(child: CircularProgressIndicator());
      }
      final runs = listOfMaps(snapshot.data);
      return RefreshIndicator(
        onRefresh: reload,
        child: ListView.builder(
          padding: const EdgeInsets.fromLTRB(24, 22, 24, 30),
          itemCount: runs.isEmpty ? 2 : runs.length + 1,
          itemBuilder: (context, index) {
            if (index == 0) {
              return Padding(
                padding: const EdgeInsets.only(bottom: 14),
                child: _WorkspaceHeader(
                  eyebrow: 'STRATEGY LAB',
                  title: 'Backtest evidence',
                  description:
                      'Review historical engine decisions, market context and strategy outcomes.',
                  badge: '${runs.length} RUNS',
                  action: OutlinedButton.icon(
                    onPressed: reload,
                    icon: const Icon(Icons.refresh_rounded, size: 17),
                    label: const Text('Reload results'),
                  ),
                ),
              );
            }
            if (runs.isEmpty) {
              return const _EmptyWorkspace(
                icon: Icons.science_outlined,
                title: 'No backtest evidence yet',
                text:
                    'Run a strategy simulation to populate market snapshots and decision results.',
              );
            }
            return Padding(
              padding: EdgeInsets.only(bottom: index == runs.length ? 0 : 10),
              child: _BacktestRunCard(run: runs[index - 1]),
            );
          },
        ),
      );
    },
  );
}

class _BacktestRunCard extends StatelessWidget {
  const _BacktestRunCard({required this.run});
  final Map<String, dynamic> run;

  @override
  Widget build(BuildContext context) {
    final summary = mapOf(run['summary']);
    final market = mapOf(summary['market']);
    final volatility = mapOf(summary['volatility']);
    final strategies = listOfMaps(summary['strategies']);
    final skipped = strategies.where((row) => row['action'] == 'skip').length;
    final actionable = strategies.length - skipped;
    final botName = '${run['bot__name'] ?? 'Bot ${run['bot_id'] ?? '—'}'}';
    final session = label('${run['session'] ?? 'unknown'}');

    return Card(
      clipBehavior: Clip.antiAlias,
      child: Padding(
        padding: const EdgeInsets.fromLTRB(18, 16, 18, 12),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            LayoutBuilder(
              builder: (context, constraints) {
                final identity = Row(
                  children: [
                    Container(
                      width: 38,
                      height: 38,
                      decoration: BoxDecoration(
                        color: blue.withValues(alpha: 0.1),
                        borderRadius: BorderRadius.circular(9),
                      ),
                      child: const Icon(
                        Icons.science_outlined,
                        color: blue,
                        size: 20,
                      ),
                    ),
                    const SizedBox(width: 11),
                    Expanded(
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Text(
                            botName,
                            overflow: TextOverflow.ellipsis,
                            style: const TextStyle(
                              fontSize: 14,
                              fontWeight: FontWeight.w800,
                            ),
                          ),
                          const SizedBox(height: 3),
                          Text(
                            '${'${run['timeframe'] ?? '—'}'.toUpperCase()} · $session session · Run #${run['id'] ?? '—'}',
                            style: const TextStyle(color: muted, fontSize: 10),
                          ),
                        ],
                      ),
                    ),
                  ],
                );
                final timestamp = Text(
                  formatDateTime(run['created_at']),
                  style: const TextStyle(
                    color: muted,
                    fontFamily: 'Consolas',
                    fontSize: 10,
                  ),
                );
                if (constraints.maxWidth < 560) {
                  return Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [identity, const SizedBox(height: 10), timestamp],
                  );
                }
                return Row(
                  children: [
                    Expanded(child: identity),
                    timestamp,
                  ],
                );
              },
            ),
            const Padding(
              padding: EdgeInsets.symmetric(vertical: 13),
              child: Divider(height: 1),
            ),
            LayoutBuilder(
              builder: (context, constraints) {
                final columns = constraints.maxWidth >= 760 ? 6 : 3;
                final width =
                    (constraints.maxWidth - ((columns - 1) * 8)) / columns;
                final metrics = [
                  ('LAST CLOSE', compactNumber(market['last_close']), blue),
                  ('TICK VOLUME', compactNumber(market['tick']), muted),
                  ('BAR RANGE', compactNumber(volatility['bar_range']), amber),
                  (
                    'ATR POINTS',
                    compactNumber(volatility['atr_points']),
                    amber,
                  ),
                  ('ACTIONABLE', '$actionable', green),
                  ('SKIPPED', '$skipped', muted),
                ];
                return Wrap(
                  spacing: 8,
                  runSpacing: 8,
                  children: [
                    for (final metric in metrics)
                      SizedBox(
                        width: width,
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
            if (strategies.isNotEmpty) ...[
              const SizedBox(height: 8),
              Theme(
                data: Theme.of(
                  context,
                ).copyWith(dividerColor: Colors.transparent),
                child: ExpansionTile(
                  tilePadding: EdgeInsets.zero,
                  childrenPadding: const EdgeInsets.only(bottom: 4),
                  title: Text(
                    'Strategy decisions (${strategies.length})',
                    style: const TextStyle(
                      fontSize: 12,
                      fontWeight: FontWeight.w700,
                    ),
                  ),
                  subtitle: const Text(
                    'Expand to inspect scores and rejection reasons.',
                    style: TextStyle(color: muted, fontSize: 10),
                  ),
                  children: [
                    for (final strategy in strategies)
                      _BacktestStrategyRow(strategy: strategy),
                  ],
                ),
              ),
            ],
          ],
        ),
      ),
    );
  }
}

class _BacktestMetric extends StatelessWidget {
  const _BacktestMetric({
    required this.label,
    required this.value,
    required this.color,
  });
  final String label;
  final String value;
  final Color color;

  @override
  Widget build(BuildContext context) => Container(
    padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 9),
    decoration: BoxDecoration(
      color: const Color(0xFF091116),
      borderRadius: BorderRadius.circular(8),
      border: Border.all(color: border),
    ),
    child: Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          label,
          overflow: TextOverflow.ellipsis,
          style: const TextStyle(
            color: muted,
            fontSize: 7,
            fontWeight: FontWeight.w800,
            letterSpacing: 0.7,
          ),
        ),
        const SizedBox(height: 4),
        Text(
          value,
          overflow: TextOverflow.ellipsis,
          style: TextStyle(
            color: color,
            fontFamily: 'Consolas',
            fontSize: 12,
            fontWeight: FontWeight.w800,
          ),
        ),
      ],
    ),
  );
}

class _BacktestStrategyRow extends StatelessWidget {
  const _BacktestStrategyRow({required this.strategy});
  final Map<String, dynamic> strategy;

  @override
  Widget build(BuildContext context) {
    final action = '${strategy['action'] ?? 'unknown'}'.toLowerCase();
    final color = action == 'skip'
        ? muted
        : action == 'buy' || action == 'sell' || action == 'enter'
        ? green
        : amber;
    return Container(
      margin: const EdgeInsets.only(top: 6),
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
      decoration: BoxDecoration(
        color: const Color(0xFF091116),
        borderRadius: BorderRadius.circular(8),
        border: Border.all(color: border),
      ),
      child: Row(
        children: [
          _StatusPill(text: action.toUpperCase(), color: color),
          const SizedBox(width: 11),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  label('${strategy['strategy'] ?? 'strategy'}'),
                  style: const TextStyle(
                    fontSize: 11,
                    fontWeight: FontWeight.w700,
                  ),
                ),
                const SizedBox(height: 2),
                Text(
                  label('${strategy['reason'] ?? 'No reason recorded'}'),
                  overflow: TextOverflow.ellipsis,
                  style: const TextStyle(color: muted, fontSize: 9),
                ),
              ],
            ),
          ),
          const SizedBox(width: 10),
          Text(
            'Score ${compactNumber(strategy['score'])}',
            style: const TextStyle(
              color: muted,
              fontFamily: 'Consolas',
              fontSize: 9,
            ),
          ),
        ],
      ),
    );
  }
}

class HistoryPage extends StatefulWidget {
  const HistoryPage({super.key, required this.client});
  final ApiClient client;

  @override
  State<HistoryPage> createState() => _HistoryPageState();
}

class _HistoryPageState extends State<HistoryPage> {
  late Future<dynamic> future = widget.client.get('/api/personal/history/');

  Future<void> reload() async {
    final next = widget.client.get('/api/personal/history/');
    setState(() => future = next);
    await next;
  }

  @override
  Widget build(BuildContext context) => FutureBuilder(
    future: future,
    builder: (context, snapshot) {
      if (snapshot.hasError) {
        return Empty(icon: Icons.cloud_off, text: snapshot.error.toString());
      }
      if (!snapshot.hasData) {
        return const Center(child: CircularProgressIndicator());
      }
      final root = mapOf(snapshot.data);
      final summary = mapOf(root['summary']);
      final trades = listOfMaps(root['trades']);
      final metrics = [
        ('TOTAL TRADES', '${integerValue(summary['total_trades']) ?? 0}', blue),
        ('WINS', '${integerValue(summary['wins']) ?? 0}', green),
        ('LOSSES', '${integerValue(summary['losses']) ?? 0}', danger),
        ('WIN RATE', optionalPercent(summary['win_rate']), green),
        ('GROSS PROFIT', compactNumber(summary['gross_profit']), green),
        ('GROSS LOSS', compactNumber(summary['gross_loss']), danger),
        (
          'NET PROFIT',
          compactNumber(summary['net_profit']),
          valueColor(summary['net_profit']),
        ),
        ('PROFIT FACTOR', compactNumber(summary['profit_factor']), amber),
      ];
      return RefreshIndicator(
        onRefresh: reload,
        child: ListView(
          padding: const EdgeInsets.fromLTRB(24, 22, 24, 30),
          children: [
            _WorkspaceHeader(
              eyebrow: 'PERFORMANCE LEDGER',
              title: 'Trade history',
              description:
                  'Track closed trades, realized performance and execution outcomes.',
              badge: '${trades.length} TRADES',
              action: OutlinedButton.icon(
                onPressed: reload,
                icon: const Icon(Icons.refresh_rounded, size: 17),
                label: const Text('Reload history'),
              ),
            ),
            const SizedBox(height: 14),
            LayoutBuilder(
              builder: (context, constraints) {
                final columns = constraints.maxWidth >= 1100
                    ? 4
                    : constraints.maxWidth >= 560
                    ? 2
                    : 1;
                return GridView.builder(
                  shrinkWrap: true,
                  physics: const NeverScrollableScrollPhysics(),
                  gridDelegate: SliverGridDelegateWithFixedCrossAxisCount(
                    crossAxisCount: columns,
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
                );
              },
            ),
            const SizedBox(height: 20),
            Row(
              children: [
                const Expanded(
                  child: Text(
                    'EXECUTION LEDGER',
                    style: TextStyle(
                      color: blue,
                      fontSize: 10,
                      fontWeight: FontWeight.w800,
                      letterSpacing: 1.1,
                    ),
                  ),
                ),
                Text(
                  'Latest ${trades.length}',
                  style: const TextStyle(color: muted, fontSize: 10),
                ),
              ],
            ),
            const SizedBox(height: 10),
            if (trades.isEmpty)
              const _EmptyWorkspace(
                icon: Icons.query_stats_rounded,
                title: 'No closed trades yet',
                text:
                    'Completed positions will appear here with entry, exit and realized P&L.',
              )
            else
              for (var index = 0; index < trades.length; index++) ...[
                _TradeHistoryRow(trade: trades[index]),
                if (index != trades.length - 1) const SizedBox(height: 8),
              ],
          ],
        ),
      );
    },
  );
}

class _HistoryMetricCard extends StatelessWidget {
  const _HistoryMetricCard({
    required this.label,
    required this.value,
    required this.color,
  });
  final String label;
  final String value;
  final Color color;

  @override
  Widget build(BuildContext context) => Container(
    padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 11),
    decoration: BoxDecoration(
      color: panel,
      borderRadius: BorderRadius.circular(10),
      border: Border.all(color: border),
    ),
    child: Row(
      children: [
        Container(
          width: 4,
          height: 34,
          decoration: BoxDecoration(
            color: color,
            borderRadius: BorderRadius.circular(4),
          ),
        ),
        const SizedBox(width: 11),
        Expanded(
          child: Column(
            mainAxisAlignment: MainAxisAlignment.center,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                label,
                overflow: TextOverflow.ellipsis,
                style: const TextStyle(
                  color: muted,
                  fontSize: 8,
                  fontWeight: FontWeight.w800,
                  letterSpacing: 0.7,
                ),
              ),
              const SizedBox(height: 4),
              Text(
                value,
                overflow: TextOverflow.ellipsis,
                style: TextStyle(
                  color: color,
                  fontFamily: 'Consolas',
                  fontSize: 16,
                  fontWeight: FontWeight.w800,
                ),
              ),
            ],
          ),
        ),
      ],
    ),
  );
}

class _TradeHistoryRow extends StatelessWidget {
  const _TradeHistoryRow({required this.trade});
  final Map<String, dynamic> trade;

  @override
  Widget build(BuildContext context) {
    final pnl = numericValue(trade['pnl']);
    final pnlColor = valueColor(pnl);
    final side = '${trade['side'] ?? '—'}'.toUpperCase();
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 15, vertical: 12),
      decoration: BoxDecoration(
        color: panel,
        borderRadius: BorderRadius.circular(9),
        border: Border.all(color: border),
      ),
      child: LayoutBuilder(
        builder: (context, constraints) {
          final identity = Row(
            children: [
              _StatusPill(text: side, color: side == 'BUY' ? green : amber),
              const SizedBox(width: 10),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      '${trade['symbol'] ?? '—'}',
                      style: const TextStyle(
                        fontFamily: 'Consolas',
                        fontSize: 12,
                        fontWeight: FontWeight.w800,
                      ),
                    ),
                    const SizedBox(height: 2),
                    Text(
                      formatDateTime(trade['closed_at'] ?? trade['created_at']),
                      style: const TextStyle(color: muted, fontSize: 9),
                    ),
                  ],
                ),
              ),
            ],
          );
          final profit = Text(
            pnl == null ? '—' : signedMoney(pnl, ''),
            style: TextStyle(
              color: pnlColor,
              fontFamily: 'Consolas',
              fontSize: 13,
              fontWeight: FontWeight.w800,
            ),
          );
          if (constraints.maxWidth < 700) {
            return Row(
              children: [
                Expanded(child: identity),
                profit,
              ],
            );
          }
          return Row(
            children: [
              SizedBox(width: 220, child: identity),
              _TradeDatum(label: 'QTY', value: compactNumber(trade['qty'])),
              _TradeDatum(label: 'ENTRY', value: compactNumber(trade['price'])),
              _TradeDatum(
                label: 'EXIT',
                value: compactNumber(trade['exit_price']),
              ),
              _TradeDatum(
                label: 'TICKET',
                value: '${trade['broker_ticket'] ?? '—'}',
              ),
              const Spacer(),
              profit,
            ],
          );
        },
      ),
    );
  }
}

class _TradeDatum extends StatelessWidget {
  const _TradeDatum({required this.label, required this.value});
  final String label;
  final String value;

  @override
  Widget build(BuildContext context) => SizedBox(
    width: 100,
    child: Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          label,
          style: const TextStyle(
            color: muted,
            fontSize: 7,
            fontWeight: FontWeight.w800,
            letterSpacing: 0.7,
          ),
        ),
        const SizedBox(height: 4),
        Text(
          value,
          overflow: TextOverflow.ellipsis,
          style: const TextStyle(
            fontFamily: 'Consolas',
            fontSize: 10,
            fontWeight: FontWeight.w700,
          ),
        ),
      ],
    ),
  );
}

class RiskPage extends StatefulWidget {
  const RiskPage({super.key, required this.client});
  final ApiClient client;
  @override
  State<RiskPage> createState() => _RiskPageState();
}

class _RiskPageState extends State<RiskPage> {
  static const fields = [
    'risk_per_trade_pct',
    'max_daily_loss_pct',
    'max_account_drawdown_pct',
    'max_positions',
    'max_positions_per_symbol',
    'max_entry_trades_per_day',
    'max_lot',
    'max_spread_points',
    'deviation_points',
    'stop_after_daily_profit_pct',
  ];
  final controllers = <String, TextEditingController>{};
  bool closeOwned = false;
  bool liveConfirmed = false;
  bool loading = true;
  bool saving = false;
  String? error;
  @override
  void initState() {
    super.initState();
    load();
  }

  @override
  void dispose() {
    for (final controller in controllers.values) {
      controller.dispose();
    }
    super.dispose();
  }

  Future<void> load() async {
    try {
      final value = mapOf(await widget.client.get('/api/personal/risk/'));
      for (final field in fields) {
        controllers.putIfAbsent(field, TextEditingController.new).text =
            value[field]?.toString() ?? '';
      }
      closeOwned = value['emergency_close_owned_positions'] == true;
      liveConfirmed = value['live_trading_confirmed'] == true;
      error = null;
    } catch (e) {
      error = e.toString();
    }
    if (mounted) setState(() => loading = false);
  }

  Future<void> save() async {
    if (!await confirm(
      context,
      'Save risk policy',
      'Apply these limits to all future entries?',
    )) {
      return;
    }
    final body = <String, dynamic>{
      for (final field in fields) field: controllers[field]!.text.trim(),
      'emergency_close_owned_positions': closeOwned,
      'live_trading_confirmed': liveConfirmed,
    };
    if (mounted) setState(() => saving = true);
    try {
      await widget.client.patch('/api/personal/risk/', body);
      if (mounted) message(context, 'Risk policy saved.');
    } catch (e) {
      if (mounted) message(context, e.toString(), isError: true);
    } finally {
      if (mounted) setState(() => saving = false);
    }
  }

  Widget _riskInput(
    String field,
    String title, {
    String? suffix,
    String? help,
  }) => TextField(
    controller: controllers[field],
    keyboardType: const TextInputType.numberWithOptions(decimal: true),
    decoration: InputDecoration(
      labelText: title,
      suffixText: suffix,
      helperText: help,
      helperMaxLines: 2,
    ),
  );

  Widget _policySection({
    required IconData icon,
    required String title,
    required String description,
    required List<Widget> fields,
  }) => Container(
    padding: const EdgeInsets.all(18),
    decoration: BoxDecoration(
      color: panel,
      borderRadius: BorderRadius.circular(11),
      border: Border.all(color: border),
    ),
    child: Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Row(
          children: [
            Container(
              width: 34,
              height: 34,
              decoration: BoxDecoration(
                color: blue.withValues(alpha: 0.1),
                borderRadius: BorderRadius.circular(9),
              ),
              child: Icon(icon, color: blue, size: 18),
            ),
            const SizedBox(width: 11),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    title,
                    style: const TextStyle(
                      fontSize: 13,
                      fontWeight: FontWeight.w800,
                    ),
                  ),
                  const SizedBox(height: 2),
                  Text(
                    description,
                    style: const TextStyle(color: muted, fontSize: 10),
                  ),
                ],
              ),
            ),
          ],
        ),
        const Padding(
          padding: EdgeInsets.symmetric(vertical: 14),
          child: Divider(height: 1),
        ),
        LayoutBuilder(
          builder: (context, constraints) {
            final columns = constraints.maxWidth >= 1040
                ? 4
                : constraints.maxWidth >= 560
                ? 2
                : 1;
            final fieldWidth =
                (constraints.maxWidth - ((columns - 1) * 12)) / columns;
            return Wrap(
              spacing: 12,
              runSpacing: 12,
              children: [
                for (final field in fields)
                  SizedBox(width: fieldWidth, child: field),
              ],
            );
          },
        ),
      ],
    ),
  );

  Widget _safetyToggle({
    required IconData icon,
    required String title,
    required String description,
    required bool value,
    required ValueChanged<bool> onChanged,
    Color color = amber,
  }) => Container(
    padding: const EdgeInsets.fromLTRB(14, 12, 10, 12),
    decoration: BoxDecoration(
      color: panel,
      borderRadius: BorderRadius.circular(10),
      border: Border.all(color: value ? color.withValues(alpha: 0.45) : border),
    ),
    child: Row(
      children: [
        Container(
          width: 34,
          height: 34,
          decoration: BoxDecoration(
            color: color.withValues(alpha: 0.1),
            borderRadius: BorderRadius.circular(9),
          ),
          child: Icon(icon, color: color, size: 18),
        ),
        const SizedBox(width: 11),
        Expanded(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                title,
                style: const TextStyle(
                  fontSize: 12,
                  fontWeight: FontWeight.w700,
                ),
              ),
              const SizedBox(height: 2),
              Text(
                description,
                style: const TextStyle(color: muted, fontSize: 9, height: 1.3),
              ),
            ],
          ),
        ),
        const SizedBox(width: 8),
        Switch(value: value, onChanged: onChanged),
      ],
    ),
  );

  @override
  Widget build(BuildContext context) {
    if (loading) return const Center(child: CircularProgressIndicator());
    if (error != null) return Empty(icon: Icons.cloud_off, text: error!);
    return ListView(
      padding: const EdgeInsets.fromLTRB(24, 22, 24, 30),
      children: [
        _WorkspaceHeader(
          eyebrow: 'ACCOUNT GUARDRAILS',
          title: 'Risk policy',
          description:
              'Define exposure, loss and execution limits applied before every order.',
          badge: liveConfirmed ? 'LIVE ENABLED' : 'DEMO SAFE',
          action: FilledButton.icon(
            onPressed: saving ? null : save,
            icon: saving
                ? const SizedBox.square(
                    dimension: 15,
                    child: CircularProgressIndicator(strokeWidth: 2),
                  )
                : const Icon(Icons.save_outlined, size: 17),
            label: Text(saving ? 'Saving…' : 'Save policy'),
          ),
        ),
        const SizedBox(height: 14),
        _policySection(
          icon: Icons.account_balance_wallet_outlined,
          title: 'Capital protection',
          description: 'Cap loss per trade, per day and across the account.',
          fields: [
            _riskInput(
              'risk_per_trade_pct',
              'Risk per trade',
              suffix: '%',
              help: 'Equity at risk on one entry',
            ),
            _riskInput(
              'max_daily_loss_pct',
              'Maximum daily loss',
              suffix: '%',
              help: 'Stops new entries for the day',
            ),
            _riskInput(
              'max_account_drawdown_pct',
              'Maximum account drawdown',
              suffix: '%',
              help: 'Hard account-level guardrail',
            ),
            _riskInput(
              'stop_after_daily_profit_pct',
              'Daily profit lock',
              suffix: '%',
              help: '0 disables the profit lock',
            ),
          ],
        ),
        const SizedBox(height: 10),
        _policySection(
          icon: Icons.layers_outlined,
          title: 'Exposure limits',
          description: 'Control position size and trading frequency.',
          fields: [
            _riskInput('max_lot', 'Maximum lot size', suffix: 'lots'),
            _riskInput('max_positions', 'Maximum open positions'),
            _riskInput('max_positions_per_symbol', 'Positions per symbol'),
            _riskInput('max_entry_trades_per_day', 'Entry trades per day'),
          ],
        ),
        const SizedBox(height: 10),
        _policySection(
          icon: Icons.speed_rounded,
          title: 'Execution quality',
          description: 'Reject orders when broker conditions are unfavorable.',
          fields: [
            _riskInput('max_spread_points', 'Maximum spread', suffix: 'points'),
            _riskInput(
              'deviation_points',
              'Allowed deviation',
              suffix: 'points',
            ),
          ],
        ),
        const SizedBox(height: 10),
        LayoutBuilder(
          builder: (context, constraints) {
            final stacked = constraints.maxWidth < 760;
            final toggles = [
              _safetyToggle(
                icon: Icons.emergency_outlined,
                title: 'Close owned positions on emergency stop',
                description:
                    'Only EZ Trade-managed positions close; manual trades remain untouched.',
                value: closeOwned,
                onChanged: (value) => setState(() => closeOwned = value),
                color: danger,
              ),
              _safetyToggle(
                icon: Icons.verified_user_outlined,
                title: 'Confirm live-account trading',
                description:
                    'Keep disabled while testing on demo accounts and simulations.',
                value: liveConfirmed,
                onChanged: (value) => setState(() => liveConfirmed = value),
                color: amber,
              ),
            ];
            if (stacked) {
              return Column(
                children: [
                  toggles.first,
                  const SizedBox(height: 10),
                  toggles.last,
                ],
              );
            }
            return Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Expanded(child: toggles.first),
                const SizedBox(width: 10),
                Expanded(child: toggles.last),
              ],
            );
          },
        ),
      ],
    );
  }
}

class SettingsPage extends StatefulWidget {
  const SettingsPage({super.key, required this.client});
  final ApiClient client;
  @override
  State<SettingsPage> createState() => _SettingsPageState();
}

class _SettingsPageState extends State<SettingsPage> {
  late Future<dynamic> future = widget.client.get('/api/personal/accounts/');
  int? testingAccountId;
  Future<void> edit([Map<String, dynamic>? row]) async {
    final name = TextEditingController(
      text: row?['name']?.toString() ?? 'MT5 Account',
    );
    final login = TextEditingController(
      text: row?['mt5_login']?.toString() ?? '',
    );
    final server = TextEditingController(
      text: row?['mt5_server']?.toString() ?? '',
    );
    final path = TextEditingController(
      text: row?['mt5_path']?.toString() ?? '',
    );
    final password = TextEditingController();
    final controls = [
      ('Alias', name, false),
      ('Login', login, false),
      ('Server', server, false),
      ('Terminal path', path, false),
      (
        'Password${row == null ? '' : ' (blank keeps current)'}',
        password,
        true,
      ),
    ];
    final ok =
        await showDialog<bool>(
          context: context,
          builder: (_) => AlertDialog(
            title: Text(row == null ? 'Add MT5 account' : 'Edit MT5 account'),
            content: SizedBox(
              width: 520,
              child: Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  for (final item in controls) ...[
                    TextField(
                      controller: item.$2,
                      obscureText: item.$3,
                      decoration: InputDecoration(labelText: item.$1),
                    ),
                    const SizedBox(height: 10),
                  ],
                ],
              ),
            ),
            actions: [
              TextButton(
                onPressed: () => Navigator.pop(context, false),
                child: const Text('Cancel'),
              ),
              FilledButton(
                onPressed: () => Navigator.pop(context, true),
                child: const Text('Save'),
              ),
            ],
          ),
        ) ??
        false;
    if (!ok) return;
    try {
      await widget.client.request(
        row == null ? 'POST' : 'PATCH',
        '/api/personal/accounts/',
        body: {
          if (row != null) 'id': row['id'],
          'name': name.text.trim(),
          'mt5_login': login.text.trim(),
          'mt5_server': server.text.trim(),
          'mt5_path': path.text.trim(),
          if (password.text.isNotEmpty) 'password': password.text,
        },
      );
      setState(() {
        future = widget.client.get('/api/personal/accounts/');
      });
      if (mounted) {
        message(
          context,
          'MT5 account saved. Test the connection to verify the terminal.',
        );
      }
    } catch (e) {
      if (mounted) message(context, e.toString(), isError: true);
    }
  }

  Future<void> test(Map<String, dynamic> row) async {
    final accountId = row['id'] as int;
    setState(() => testingAccountId = accountId);
    try {
      final queued = await widget.client.post('/api/personal/accounts/test/', {
        'broker_account_id': accountId,
      });
      final queuedAt = DateTime.tryParse('${queued['queued_at']}')?.toUtc();
      for (var attempt = 0; attempt < 30; attempt++) {
        await Future<void>.delayed(const Duration(seconds: 1));
        final dashboard = await widget.client.get(
          '/api/personal/dashboard/?broker_account_id=$accountId',
        );
        final mt5 = dashboard is Map ? dashboard['mt5'] : null;
        final checkedAt = mt5 is Map
            ? DateTime.tryParse('${mt5['checked_at']}')?.toUtc()
            : null;
        if (checkedAt == null ||
            (queuedAt != null && checkedAt.isBefore(queuedAt))) {
          continue;
        }
        if (!mounted) return;
        if (mt5['connected'] == true) {
          message(context, 'MT5 connected. Market symbols are refreshing.');
        } else {
          message(
            context,
            '${mt5['last_error'] ?? 'MT5 connection failed.'}',
            isError: true,
          );
        }
        setState(() {
          future = widget.client.get('/api/personal/accounts/');
        });
        return;
      }
      throw const ApiException(
        'The MT5 worker did not respond within 30 seconds. Restart local services and try again.',
      );
    } catch (e) {
      if (mounted) message(context, e.toString(), isError: true);
    } finally {
      if (mounted) setState(() => testingAccountId = null);
    }
  }

  @override
  Widget build(BuildContext context) => FutureBuilder(
    future: future,
    builder: (_, snapshot) {
      if (snapshot.hasError) {
        return Empty(icon: Icons.cloud_off, text: snapshot.error.toString());
      }
      if (!snapshot.hasData) {
        return const Center(child: CircularProgressIndicator());
      }
      return Column(
        children: [
          Padding(
            padding: const EdgeInsets.fromLTRB(28, 22, 28, 8),
            child: Row(
              children: [
                const Text(
                  'Passwords are encrypted by Django and never returned.',
                  style: TextStyle(color: muted),
                ),
                const Spacer(),
                FilledButton.icon(
                  onPressed: () => edit(),
                  icon: const Icon(Icons.add),
                  label: const Text('Add account'),
                ),
              ],
            ),
          ),
          Expanded(
            child: Records(
              data: snapshot.data,
              empty: 'Add a local MT5 terminal account.',
              trailing: (row) => Wrap(
                spacing: 5,
                children: [
                  IconButton(
                    tooltip: 'Edit',
                    onPressed: () => edit(row),
                    icon: const Icon(Icons.edit_outlined),
                  ),
                  OutlinedButton.icon(
                    onPressed: testingAccountId == null
                        ? () => test(row)
                        : null,
                    icon: testingAccountId == row['id']
                        ? const SizedBox.square(
                            dimension: 16,
                            child: CircularProgressIndicator(strokeWidth: 2),
                          )
                        : const Icon(Icons.cable),
                    label: Text(
                      testingAccountId == row['id']
                          ? 'Connecting…'
                          : 'Test connection',
                    ),
                  ),
                ],
              ),
            ),
          ),
        ],
      );
    },
  );
}

class Records extends StatelessWidget {
  const Records({
    super.key,
    required this.data,
    this.trailing,
    this.empty = 'No records.',
    this.padding = const EdgeInsets.all(28),
  });
  final dynamic data;
  final Widget Function(Map<String, dynamic>)? trailing;
  final String empty;
  final EdgeInsets padding;
  @override
  Widget build(BuildContext context) {
    dynamic raw = data;
    if (raw is Map && raw['results'] is List) raw = raw['results'];
    final rows = raw is List
        ? raw.whereType<Map>().map((e) => Map<String, dynamic>.from(e)).toList()
        : <Map<String, dynamic>>[];
    if (rows.isEmpty) return Empty(icon: Icons.inbox_outlined, text: empty);
    return ListView.separated(
      padding: padding,
      itemCount: rows.length,
      separatorBuilder: (_, _) => const SizedBox(height: 9),
      itemBuilder: (_, index) {
        final row = rows[index];
        final headline =
            row['canonical_symbol'] ??
            row['symbol'] ??
            row['bot_name'] ??
            row['name'] ??
            row['client_order_id'] ??
            row['event_type'] ??
            'Record ${row['id'] ?? index + 1}';
        final details = row.entries
            .where(
              (e) => !{
                'id',
                'canonical_symbol',
                'symbol',
                'bot_name',
                'name',
              }.contains(e.key),
            )
            .take(7);
        return Card(
          child: Padding(
            padding: const EdgeInsets.symmetric(horizontal: 18, vertical: 14),
            child: Row(
              children: [
                Container(
                  width: 5,
                  height: 42,
                  decoration: BoxDecoration(
                    color: statusColor(row),
                    borderRadius: BorderRadius.circular(4),
                  ),
                ),
                const SizedBox(width: 14),
                SizedBox(
                  width: 150,
                  child: Text(
                    headline.toString(),
                    style: const TextStyle(fontWeight: FontWeight.w700),
                  ),
                ),
                Expanded(
                  child: Wrap(
                    spacing: 18,
                    runSpacing: 5,
                    children: [
                      for (final entry in details)
                        Text(
                          '${label(entry.key)}  ${display(entry.value)}',
                          style: const TextStyle(color: muted, fontSize: 12),
                        ),
                    ],
                  ),
                ),
                if (trailing != null) ...[
                  const SizedBox(width: 12),
                  trailing!(row),
                ],
              ],
            ),
          ),
        );
      },
    );
  }
}

class Metric extends StatelessWidget {
  const Metric(
    this.name,
    this.value,
    this.icon, {
    super.key,
    this.signed = false,
  });
  final String name;
  final dynamic value;
  final IconData icon;
  final bool signed;
  @override
  Widget build(BuildContext context) {
    final number = double.tryParse(value?.toString() ?? '');
    final color = signed && number != null
        ? (number < 0 ? danger : green)
        : Colors.white;
    return SizedBox(
      width: 205,
      child: Card(
        child: Padding(
          padding: const EdgeInsets.all(18),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                children: [
                  Icon(icon, size: 18, color: green),
                  const Spacer(),
                  Text(
                    name,
                    style: const TextStyle(color: muted, fontSize: 12),
                  ),
                ],
              ),
              const SizedBox(height: 16),
              Text(
                value?.toString() ?? '—',
                style: TextStyle(
                  color: color,
                  fontSize: 23,
                  fontWeight: FontWeight.w700,
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class InfoCard extends StatelessWidget {
  const InfoCard({super.key, required this.title, required this.values});
  final String title;
  final Map<String, dynamic> values;
  @override
  Widget build(BuildContext context) => Card(
    child: Padding(
      padding: const EdgeInsets.all(20),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            title,
            style: const TextStyle(fontSize: 16, fontWeight: FontWeight.w700),
          ),
          const SizedBox(height: 14),
          for (final entry in values.entries.take(10))
            Padding(
              padding: const EdgeInsets.symmetric(vertical: 5),
              child: Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Expanded(
                    child: Text(
                      label(entry.key),
                      style: const TextStyle(color: muted, fontSize: 12),
                    ),
                  ),
                  const SizedBox(width: 12),
                  Flexible(
                    child: Text(
                      display(entry.value),
                      textAlign: TextAlign.right,
                      style: const TextStyle(fontSize: 12),
                    ),
                  ),
                ],
              ),
            ),
        ],
      ),
    ),
  );
}

class Empty extends StatelessWidget {
  const Empty({super.key, required this.icon, required this.text});
  final IconData icon;
  final String text;
  @override
  Widget build(BuildContext context) => Center(
    child: Padding(
      padding: const EdgeInsets.all(40),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(icon, size: 46, color: muted),
          const SizedBox(height: 14),
          Text(
            text,
            textAlign: TextAlign.center,
            style: const TextStyle(color: muted),
          ),
        ],
      ),
    ),
  );
}

double? numericValue(dynamic value) {
  if (value is num) return value.toDouble();
  return double.tryParse(value?.toString() ?? '');
}

double asDouble(dynamic value) => numericValue(value) ?? 0;

int? integerValue(dynamic value) {
  if (value is int) return value;
  return int.tryParse(value?.toString() ?? '');
}

List<Map<String, dynamic>> listOfMaps(dynamic value) {
  dynamic raw = value;
  if (raw is Map && raw['results'] is List) raw = raw['results'];
  return raw is List
      ? raw
            .whereType<Map>()
            .map((row) => Map<String, dynamic>.from(row))
            .toList()
      : <Map<String, dynamic>>[];
}

String compactNumber(dynamic value) {
  final number = numericValue(value);
  if (number == null) return '—';
  final fixed = number.toStringAsFixed(number.abs() >= 100 ? 2 : 5);
  return fixed.replaceFirst(RegExp(r'\.?0+$'), '');
}

String money(dynamic value, String currency) {
  final number = numericValue(value);
  if (number == null) return '—';
  final prefix = currency.trim().isEmpty ? '' : '${currency.trim()} ';
  return '$prefix${number.toStringAsFixed(2)}';
}

String signedMoney(dynamic value, String currency) {
  final number = numericValue(value);
  if (number == null) return '—';
  final prefix = currency.trim().isEmpty ? '' : '${currency.trim()} ';
  final sign = number > 0
      ? '+'
      : number < 0
      ? '−'
      : '';
  return '$sign$prefix${number.abs().toStringAsFixed(2)}';
}

String percent(dynamic value) => '${asDouble(value).toStringAsFixed(2)}%';

String optionalPercent(dynamic value) {
  final number = numericValue(value);
  return number == null ? '—' : '${number.toStringAsFixed(2)}%';
}

Color valueColor(dynamic value) {
  final number = numericValue(value);
  if (number == null) return muted;
  if (number < 0) return danger;
  if (number > 0) return green;
  return blue;
}

String formatTimestamp(dynamic value) {
  final parsed = DateTime.tryParse(value?.toString() ?? '');
  if (parsed == null) return 'NEVER';
  final local = parsed.toLocal();
  String two(int part) => part.toString().padLeft(2, '0');
  return '${two(local.hour)}:${two(local.minute)}:${two(local.second)}';
}

String formatDateTime(dynamic value) {
  final parsed = DateTime.tryParse(value?.toString() ?? '');
  if (parsed == null) return 'Date unavailable';
  final local = parsed.toLocal();
  String two(int part) => part.toString().padLeft(2, '0');
  return '${local.year}-${two(local.month)}-${two(local.day)}  '
      '${two(local.hour)}:${two(local.minute)}';
}

Map<String, dynamic> mapOf(dynamic value) =>
    value is Map ? Map<String, dynamic>.from(value) : <String, dynamic>{};
String display(dynamic value) => value == null || value == ''
    ? '—'
    : value is Map || value is List
    ? const JsonEncoder.withIndent(' ').convert(value)
    : value.toString();
String label(String value) => value
    .replaceAll('_', ' ')
    .split(' ')
    .map(
      (part) =>
          part.isEmpty ? '' : '${part[0].toUpperCase()}${part.substring(1)}',
    )
    .join(' ');
Color statusColor(Map<String, dynamic> row) {
  final value =
      '${row['status'] ?? row['trading_status'] ?? row['severity'] ?? ''}'
          .toLowerCase();
  if (value.contains('error') ||
      value.contains('loss') ||
      value.contains('reject') ||
      value.contains('closed')) {
    return danger;
  }
  if (value.contains('warn') || value.contains('part')) return Colors.amber;
  return green;
}

void message(BuildContext context, String text, {bool isError = false}) =>
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(content: Text(text), backgroundColor: isError ? danger : panel2),
    );
Future<bool> confirm(BuildContext context, String title, String body) async =>
    await showDialog<bool>(
      context: context,
      builder: (_) => AlertDialog(
        title: Text(title),
        content: Text(body),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: const Text('Cancel'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(context, true),
            child: const Text('Confirm'),
          ),
        ],
      ),
    ) ??
    false;
