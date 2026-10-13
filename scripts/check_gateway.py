"""Run against a running local gateway; creates two disposable test accounts."""
import json
import os
import secrets
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ORIGIN = os.getenv('GATEWAY_URL', 'http://localhost:8080').rstrip('/')


def request(method, path, headers=None, body=None):
    headers = dict(headers or {})
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers['Content-Type'] = 'application/json'
    req = Request(ORIGIN + path, data=data, headers=headers, method=method)
    try:
        result = urlopen(req, timeout=30)
    except HTTPError as error:
        result = error
    with result:
        return result.status, result.headers, result.read()


class GatewayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.users = []
        for _ in range(2):
            credentials = {'username': 'gateway-test-' + secrets.token_hex(8),
                           'password': secrets.token_hex(16)}
            status, _, body = request('POST', '/api/v1/auth/register', body=credentials)
            if status != 201:
                raise AssertionError(f'Registration failed: {status} {body!r}')
            user = json.loads(body)
            status, _, body = request('POST', '/api/v1/auth/login', body=credentials)
            if status != 200:
                raise AssertionError(f'Login failed: {status}')
            cls.users.append((user['id'], json.loads(body)['access_token']))

    def test_identity_cannot_be_spoofed(self):
        user_id, token = self.users[0]
        status, _, body = request('GET', '/api/v1/auth/me', headers={
            'Authorization': 'Bearer ' + token, 'X-User-ID': str(self.users[1][0]),
        })
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)['id'], user_id)

    def test_protected_routes_reject_tokens_and_identity_spoofing(self):
        for method, path in [('GET', '/api/v1/auth/me'), ('GET', '/api/v1/games'),
                             ('POST', '/api/v1/steam/sync'), ('POST', '/api/v1/steam/link')]:
            for headers in [{}, {'X-User-ID': str(self.users[0][0])},
                            {'Authorization': 'Bearer invalid'}]:
                with self.subTest(path=path, headers=headers):
                    status, _, _ = request(method, path, headers=headers)
                    self.assertEqual(status, 401)

    def test_library_and_sync_routing(self):
        headers = {'Authorization': 'Bearer ' + self.users[0][1]}
        status, _, body = request('GET', '/api/v1/games', headers=headers)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), [])
        status, _, body = request('POST', '/api/v1/steam/sync', headers=headers)
        self.assertEqual(status, 409)
        self.assertEqual(json.loads(body)['detail'], 'Connect Steam before syncing')
        status, _, _ = request('PATCH', '/api/v1/games/2147483647', headers=headers,
                               body={'completion_status': 'finished'})
        self.assertEqual(status, 404)

    def test_steam_link_cookie_and_public_callback(self):
        status, headers, body = request('POST', '/api/v1/steam/link', headers={
            'Authorization': 'Bearer ' + self.users[0][1],
        })
        self.assertEqual(status, 200)
        self.assertIn('steamcommunity.com', json.loads(body)['login_url'])
        self.assertIn('HttpOnly', headers['Set-Cookie'])
        self.assertIn('Path=/api/v1/steam/callback', headers['Set-Cookie'])
        status, _, _ = request('GET', '/api/v1/steam/callback')
        self.assertEqual(status, 400)

    def test_internal_and_unknown_routes_are_hidden(self):
        for path in ['/internal/auth/check', '/_gateway_auth', '/internal/health',
                     '/docs', '/api/v1/unknown']:
            with self.subTest(path=path):
                self.assertEqual(request('GET', path)[0], 404)


if __name__ == '__main__':
    unittest.main()
