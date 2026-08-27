import 'dart:async';
import 'dart:convert';
import 'dart:io';

class BackendLaunchException implements Exception {
  const BackendLaunchException(this.message);

  final String message;

  @override
  String toString() => message;
}

class _BackendCommand {
  const _BackendCommand(
    this.executable,
    this.arguments, {
    required this.isSourceCheckout,
  });

  final String executable;
  final List<String> arguments;
  final bool isSourceCheckout;
}

/// Owns the local Python backend for the lifetime of the Flutter process.
class BackendManager {
  BackendManager({
    this.host = '127.0.0.1',
    int? port,
    this.developmentPort = 8001,
    this.startupTimeout = const Duration(seconds: 75),
  }) : port = port ?? 8000,
       _portWasExplicit = port != null;

  final String host;
  int port;
  final int developmentPort;
  final Duration startupTimeout;
  final bool _portWasExplicit;

  Process? _process;
  File? _shutdownFile;
  bool _ownsBackend = false;
  final List<String> _recentOutput = [];

  String get baseUrl => 'http://$host:$port';

  String get logDirectory {
    final appData = Platform.environment['APPDATA'];
    if (appData == null || appData.isEmpty) return 'EzScalperBot/logs';
    return _join(appData, 'EzScalperBot', 'logs');
  }

  Future<void> start() async {
    final command = _resolveCommand();
    if (command == null) {
      throw const BackendLaunchException(
        'The EZ Trade backend was not found. For development, start Flutter '
        'from the repository. For a release, rebuild with '
        'desktop/build_desktop.ps1.',
      );
    }

    // A source checkout and an installed desktop build intentionally use
    // different default ports. Otherwise a VS Code run can silently attach to
    // an installed backend on port 8000 and display that installation's
    // separate database as if the user's bots and settings had disappeared.
    if (!_portWasExplicit) {
      final environmentPort = int.tryParse(
        Platform.environment['EZTRADE_BACKEND_PORT'] ?? '',
      );
      port = environmentPort ??
          (command.isSourceCheckout ? developmentPort : 8000);
    }

    if (await _isReady()) return;

    final shutdownName =
        'eztrade-$pid-${DateTime.now().microsecondsSinceEpoch}.stop';
    _shutdownFile = File(_join(Directory.systemTemp.path, shutdownName));
    if (_shutdownFile!.existsSync()) _shutdownFile!.deleteSync();

    try {
      _process = await Process.start(
        command.executable,
        [
          ...command.arguments,
          '--host',
          host,
          '--port',
          '$port',
          '--parent-pid',
          '$pid',
          '--shutdown-file',
          _shutdownFile!.path,
        ],
        runInShell: false,
        mode: ProcessStartMode.normal,
      );
      _ownsBackend = true;
      _capture(_process!.stdout);
      _capture(_process!.stderr);

      final deadline = DateTime.now().add(startupTimeout);
      while (DateTime.now().isBefore(deadline)) {
        if (await _isReady()) return;
        final exitCode = await _exitCodeWithin(
          _process!,
          const Duration(milliseconds: 350),
        );
        if (exitCode != null) {
          if (await _isReady()) {
            _ownsBackend = false;
            return;
          }
          throw BackendLaunchException(
            'The local backend exited with code $exitCode. '
            '${_diagnosticSuffix()}',
          );
        }
      }
      throw BackendLaunchException(
        'The local backend did not become ready within '
        '${startupTimeout.inSeconds} seconds. ${_diagnosticSuffix()}',
      );
    } catch (_) {
      await stop();
      rethrow;
    }
  }

  void requestStop() {
    if (!_ownsBackend) return;
    try {
      _shutdownFile?.writeAsStringSync('shutdown');
    } on FileSystemException {
      // Parent-PID monitoring remains as a crash-safe fallback.
    }
  }

  Future<void> stop() async {
    if (!_ownsBackend) return;
    final process = _process;
    requestStop();
    if (process != null) {
      try {
        await process.exitCode.timeout(const Duration(seconds: 15));
      } on TimeoutException {
        process.kill();
      }
    }
    _ownsBackend = false;
    _process = null;
    try {
      if (_shutdownFile?.existsSync() ?? false) {
        _shutdownFile!.deleteSync();
      }
    } on FileSystemException {
      // The supervisor also removes the marker during normal shutdown.
    }
  }

  Future<bool> _isReady() async {
    final client = HttpClient()..connectionTimeout = const Duration(seconds: 1);
    try {
      final request = await client
          .getUrl(Uri.parse('$baseUrl/api/health/'))
          .timeout(const Duration(seconds: 2));
      final response = await request.close().timeout(
        const Duration(seconds: 2),
      );
      final body = await utf8.decoder.bind(response).join();
      final value = jsonDecode(body);
      return value is Map &&
          value.containsKey('status') &&
          value.containsKey('db') &&
          value.containsKey('worker');
    } catch (_) {
      return false;
    } finally {
      client.close(force: true);
    }
  }

  _BackendCommand? _resolveCommand() {
    final override = Platform.environment['EZTRADE_BACKEND_EXECUTABLE'];
    if (override != null && override.trim().isNotEmpty) {
      return _commandForPath(File(override.trim()));
    }

    for (final root in <Directory>{
      Directory.current,
      File(Platform.resolvedExecutable).parent,
    }) {
      var directory = root;
      for (var depth = 0; depth < 9; depth++) {
        final packaged = File(
          _join(directory.path, 'backend', 'eztrade_backend.exe'),
        );
        if (packaged.existsSync()) return _commandForPath(packaged);

        final source = File(
          _join(directory.path, 'desktop', 'backend_launcher.py'),
        );
        final manage = File(_join(directory.path, 'manage.py'));
        if (source.existsSync() && manage.existsSync()) {
          return _commandForPath(source);
        }

        final parent = directory.parent;
        if (parent.path == directory.path) break;
        directory = parent;
      }
    }
    return null;
  }

  _BackendCommand _commandForPath(File file) {
    final isSourceCheckout = file.path.toLowerCase().endsWith('.py');
    if (isSourceCheckout) {
      final python = Platform.environment['EZTRADE_BACKEND_PYTHON'];
      return _BackendCommand(
        python == null || python.trim().isEmpty ? 'python' : python.trim(),
        [file.path],
        isSourceCheckout: true,
      );
    }
    return _BackendCommand(
      file.path,
      const [],
      isSourceCheckout: false,
    );
  }

  void _capture(Stream<List<int>> stream) {
    stream.transform(utf8.decoder).transform(const LineSplitter()).listen((
      line,
    ) {
      _recentOutput.add(line);
      if (_recentOutput.length > 12) _recentOutput.removeAt(0);
    });
  }

  String _diagnosticSuffix() {
    final output = _recentOutput
        .where((line) => line.trim().isNotEmpty)
        .join(' ');
    if (output.isNotEmpty) return output;
    return 'Check the logs in $logDirectory.';
  }
}

Future<int?> _exitCodeWithin(Process process, Duration duration) async {
  final result = await Future.any<Object?>([
    process.exitCode,
    Future<Object?>.delayed(duration),
  ]);
  return result is int ? result : null;
}

String _join(String first, String second, [String? third, String? fourth]) {
  final separator = Platform.pathSeparator;
  var value = first.endsWith(separator)
      ? '$first$second'
      : '$first$separator$second';
  if (third != null) value = '$value$separator$third';
  if (fourth != null) value = '$value$separator$fourth';
  return value;
}
