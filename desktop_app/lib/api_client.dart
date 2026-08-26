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
  void Function()? onSessionExpired;
  bool _sessionExpired = false;

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
  }

  void logout() {
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
    if (response.statusCode == HttpStatus.unauthorized && authenticated) {
      if (retry && refreshToken != null) {
        try {
          await _refresh();
          return request(method, path, body: body, retry: false);
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
  }

  void _expireSession() {
    accessToken = null;
    refreshToken = null;
    if (_sessionExpired) return;
    _sessionExpired = true;
    onSessionExpired?.call();
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
