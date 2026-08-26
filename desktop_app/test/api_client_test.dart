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
}
