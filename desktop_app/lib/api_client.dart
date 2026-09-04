import 'dart:async';
import 'dart:convert';
import 'dart:io';

class ApiException implements Exception {
  const ApiException(this.message, {this.statusCode});
  final String message;
  final int? statusCode;
  @override
  String toString() => message;
}

class ApiClient {
  ApiClient(String baseUrl) : baseUrl = baseUrl.replaceAll(RegExp(r'/+$'), '');
  final String baseUrl;
  final HttpClient _http = HttpClient();
  String? accessToken;
  String? refreshToken;
  String? runtimeStopToken;
  void Function()? onSessionExpired;
  bool _sessionExpired = false;
  Timer? _expiryTimer;
  Timer? _runtimeSessionRefreshTimer;
  Future<void>? _refreshInFlight;

  Future<bool> needsDesktopSetup() async {
    final value = await request(
      'GET',
      '/api/desktop/bootstrap/',
      authenticated: false,
      retry: false,
    );
    return value is Map && value['needs_setup'] == true;
  }

  Future<void> createDesktopAccount(String username, String password) async {
    await request(
      'POST',
      '/api/desktop/bootstrap/',
      body: {'username': username, 'password': password},
      authenticated: false,
      retry: false,
    );
  }

  Future<void> login(String username, String password) async {
    final value = await request(
      'POST',
      '/api/auth/token/',
      body: {'username': username, 'password': password},
      authenticated: false,
    );
    if (value is! Map || value['access'] == null) {
      throw const ApiException('The server did not return an access token.');
    }
    accessToken = value['access'].toString();
    refreshToken = value['refresh']?.toString();
    _sessionExpired = false;
    _scheduleSessionExpiry();
  }

  Future<void> _requestRuntimeStopToken() async {
    final value = await post('/api/personal/runtime/session/');
    if (value is! Map || value['stop_token'] == null) {
      throw const ApiException(
        'The server did not create an app-close safety session.',
      );
    }
    runtimeStopToken = value['stop_token'].toString();
  }

  Future<void> openRuntimeSession() async {
    await _requestRuntimeStopToken();
    _runtimeSessionRefreshTimer?.cancel();
    // Server tokens expire after one hour. Refresh while the authenticated
    // desktop session is open so an all-day session still stops safely on exit.
    _runtimeSessionRefreshTimer = Timer.periodic(
      const Duration(minutes: 30),
      (_) async {
        try {
          await _requestRuntimeStopToken();
        } catch (_) {
          // Keep the last still-valid stop token; normal API health handling
          // reports the connectivity/authentication failure to the UI.
        }
      },
    );
  }

  Future<void> stopRuntimeOnExit() async {
    _runtimeSessionRefreshTimer?.cancel();
    _runtimeSessionRefreshTimer = null;
    final token = runtimeStopToken;
    if (token == null || token.isEmpty) return;
    await request(
      'POST',
      '/api/personal/runtime/stop/',
      body: {'stop_token': token},
      authenticated: false,
      retry: false,
    );
    runtimeStopToken = null;
  }

  void logout() {
    _expiryTimer?.cancel();
    _runtimeSessionRefreshTimer?.cancel();
    _runtimeSessionRefreshTimer = null;
    accessToken = null;
    refreshToken = null;
    _sessionExpired = false;
  }

  Future<dynamic> get(String path) => request('GET', path);
  Future<dynamic> post(String path, [Map<String, dynamic>? body]) =>
      request('POST', path, body: body);
  Future<dynamic> patch(String path, Map<String, dynamic> body) =>
      request('PATCH', path, body: body);

  Future<dynamic> request(
    String method,
    String path, {
    Map<String, dynamic>? body,
    bool authenticated = true,
    bool retry = true,
  }) async {
    final req = await _http
        .openUrl(method, Uri.parse('$baseUrl$path'))
        .timeout(
          const Duration(seconds: 12),
          onTimeout: () => throw const ApiException('The server timed out.'),
        );
    req.headers.contentType = ContentType.json;
    req.headers.set(HttpHeaders.acceptHeader, 'application/json');
    if (authenticated && accessToken != null) {
      req.headers.set(HttpHeaders.authorizationHeader, 'Bearer $accessToken');
    }
    if (body != null) {
      final encodedBody = utf8.encode(jsonEncode(body));
      req.contentLength = encodedBody.length;
      req.add(encodedBody);
    }
    final response = await req.close();
    final raw = await utf8.decoder.bind(response).join();
    dynamic decoded;
    if (raw.isNotEmpty) {
      try {
        decoded = jsonDecode(raw);
      } on FormatException {
        decoded = raw;
      }
    }
    if (authenticated && _isSessionRejection(response.statusCode, decoded)) {
      if (retry && refreshToken != null) {
        try {
          await _refreshOnce();
          return await request(method, path, body: body, retry: false);
        } on ApiException {
          _expireSession();
          throw const ApiException(
            'Your session expired. Please sign in again.',
            statusCode: HttpStatus.unauthorized,
          );
        }
      }
      _expireSession();
      throw const ApiException(
        'Your session expired. Please sign in again.',
        statusCode: HttpStatus.unauthorized,
      );
    }
    if (response.statusCode < 200 || response.statusCode >= 300) {
      throw ApiException(
        _errorMessage(decoded) ?? 'Request failed (${response.statusCode}).',
        statusCode: response.statusCode,
      );
    }
    return decoded;
  }

  Future<void> _refreshOnce() async {
    final activeRefresh = _refreshInFlight;
    if (activeRefresh != null) return activeRefresh;

    final refresh = _refresh();
    _refreshInFlight = refresh;
    try {
      await refresh;
    } finally {
      if (identical(_refreshInFlight, refresh)) _refreshInFlight = null;
    }
  }

  Future<void> _refresh() async {
    final value = await request(
      'POST',
      '/api/auth/token/refresh/',
      body: {'refresh': refreshToken},
      authenticated: false,
      retry: false,
    );
    if (value is! Map || value['access'] == null) {
      throw const ApiException('Your session expired. Please sign in again.');
    }
    accessToken = value['access'].toString();
    if (value['refresh'] != null) {
      refreshToken = value['refresh'].toString();
    }
    _scheduleSessionExpiry();
  }

  void _expireSession() {
    _expiryTimer?.cancel();
    accessToken = null;
    refreshToken = null;
    if (_sessionExpired) return;
    _sessionExpired = true;
    onSessionExpired?.call();
  }

  void _scheduleSessionExpiry() {
    _expiryTimer?.cancel();
    final expiresAt = _jwtExpiry(refreshToken) ?? _jwtExpiry(accessToken);
    if (expiresAt == null) return;
    final delay = expiresAt.difference(DateTime.now().toUtc());
    if (delay <= Duration.zero) {
      scheduleMicrotask(_expireSession);
      return;
    }
    _expiryTimer = Timer(delay, _expireSession);
  }
}

DateTime? _jwtExpiry(String? token) {
  if (token == null) return null;
  final parts = token.split('.');
  if (parts.length != 3) return null;
  try {
    final payload = jsonDecode(
      utf8.decode(base64Url.decode(base64Url.normalize(parts[1]))),
    );
    final expiry = payload is Map ? payload['exp'] : null;
    final seconds = expiry is int ? expiry : int.tryParse('$expiry');
    if (seconds == null) return null;
    return DateTime.fromMillisecondsSinceEpoch(
      seconds * Duration.millisecondsPerSecond,
      isUtc: true,
    );
  } catch (_) {
    return null;
  }
}

String? _errorMessage(dynamic decoded) {
  if (decoded == null) return null;
  if (decoded is! Map) return decoded.toString();
  if (decoded['detail'] != null) return decoded['detail'].toString();
  final messages = <String>[];
  for (final entry in decoded.entries) {
    final value = entry.value;
    final text = value is List ? value.join(' ') : value.toString();
    messages.add('${entry.key}: $text');
  }
  return messages.isEmpty ? null : messages.join('\n');
}

bool _isSessionRejection(int statusCode, dynamic decoded) {
  if (statusCode == HttpStatus.unauthorized) return true;
  if (statusCode != HttpStatus.forbidden || decoded is! Map) return false;

  final code = decoded['code']?.toString().toLowerCase();
  if (code == 'token_not_valid' || code == 'token_expired') return true;

  // Older backend builds can report SimpleJWT authentication failures as 403
  // because SessionAuthentication was ordered before JWTAuthentication.
  final detail = decoded['detail']?.toString().toLowerCase() ?? '';
  return detail.contains('token') &&
      (detail.contains('not valid') ||
          detail.contains('invalid') ||
          detail.contains('expired'));
}
