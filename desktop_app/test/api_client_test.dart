import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:ez_trade_desktop/api_client.dart';
import 'package:flutter_test/flutter_test.dart';

Future<void> respondJson(
  HttpRequest request,
  int statusCode,
  Map<String, dynamic> body,
) async {
  request.response.statusCode = statusCode;
  request.response.headers.contentType = ContentType.json;
  request.response.write(jsonEncode(body));
  await request.response.close();
}

String jwtExpiringAt(DateTime expiry) {
  final header = base64Url.encode(utf8.encode(jsonEncode({'alg': 'none'})));
  final payload = base64Url.encode(
    utf8.encode(
      jsonEncode({'exp': expiry.toUtc().millisecondsSinceEpoch ~/ 1000}),
    ),
  );
  return '$header.$payload.signature';
}

void main() {
  test('refreshes an expired access token and retries the request', () async {
    final server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
    addTearDown(() => server.close(force: true));
    var protectedCalls = 0;

    server.listen((request) async {
      if (request.uri.path == '/api/auth/token/refresh/') {
        final body = jsonDecode(await utf8.decoder.bind(request).join());
        expect(body['refresh'], 'valid-refresh');
        await respondJson(request, HttpStatus.ok, {'access': 'new-access'});
        return;
      }

      protectedCalls++;
      if (request.headers.value(HttpHeaders.authorizationHeader) ==
          'Bearer new-access') {
        await respondJson(request, HttpStatus.ok, {'ok': true});
      } else {
        await respondJson(request, HttpStatus.unauthorized, {
          'detail': 'Token is invalid or expired',
        });
      }
    });

    final client = ApiClient('http://${server.address.address}:${server.port}')
      ..accessToken = 'expired-access'
      ..refreshToken = 'valid-refresh';

    expect(await client.get('/protected/'), {'ok': true});
    expect(client.accessToken, 'new-access');
    expect(client.refreshToken, 'valid-refresh');
    expect(protectedCalls, 2);
  });

  test(
    'clears tokens and reports session expiry when refresh is invalid',
    () async {
      final server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
      addTearDown(() => server.close(force: true));

      server.listen((request) async {
        await respondJson(request, HttpStatus.unauthorized, {
          'detail': 'Given token not valid for any token type',
        });
      });

      var expiryNotifications = 0;
      final client =
          ApiClient('http://${server.address.address}:${server.port}')
            ..accessToken = 'expired-access'
            ..refreshToken = 'invalid-refresh'
            ..onSessionExpired = () => expiryNotifications++;

      await expectLater(
        client.get('/protected/'),
        throwsA(
          isA<ApiException>()
              .having((error) => error.statusCode, 'statusCode', 401)
              .having(
                (error) => error.message,
                'message',
                'Your session expired. Please sign in again.',
              ),
        ),
      );

      expect(client.accessToken, isNull);
      expect(client.refreshToken, isNull);
      expect(expiryNotifications, 1);
    },
  );

  test('expires the UI session when the refresh JWT reaches exp', () async {
    final server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
    addTearDown(() => server.close(force: true));
    final expiresAt = DateTime.now().toUtc().add(const Duration(seconds: 1));

    server.listen((request) async {
      await respondJson(request, HttpStatus.ok, {
        'access': jwtExpiringAt(expiresAt),
        'refresh': jwtExpiringAt(expiresAt),
      });
    });

    final expired = Completer<void>();
    final client = ApiClient('http://${server.address.address}:${server.port}')
      ..onSessionExpired = () => expired.complete();
    addTearDown(client.logout);

    await client.login('user', 'password');
    await expired.future.timeout(const Duration(seconds: 3));

    expect(client.accessToken, isNull);
    expect(client.refreshToken, isNull);
  });

  test('uses a stop-only token when the app exits after logout', () async {
    final server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
    addTearDown(() => server.close(force: true));
    Map<String, dynamic>? stopBody;

    server.listen((request) async {
      if (request.uri.path == '/api/personal/runtime/session/') {
        await respondJson(request, HttpStatus.ok, {'stop_token': 'stop-only'});
        return;
      }
      stopBody = jsonDecode(await utf8.decoder.bind(request).join());
      await respondJson(request, HttpStatus.ok, {'bots_stopped': 1});
    });

    final client = ApiClient('http://${server.address.address}:${server.port}')
      ..accessToken = 'access';
    await client.openRuntimeSession();
    client.logout();
    await client.stopRuntimeOnExit();

    expect(stopBody, {'stop_token': 'stop-only'});
    expect(client.runtimeStopToken, isNull);
  });
}
