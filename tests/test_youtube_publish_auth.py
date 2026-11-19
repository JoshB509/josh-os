"""Offline publishing auth tests: fake Google transport and in-memory callbacks."""
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit, urlencode
from studio.youtube_publish_auth import (PublishingAuth, PublishingAuthError,
    SCOPES, SCOPE, validate_reply, exchange, callback_handler)
from studio.youtube_auth import YouTubeAuth


class PublishingAuthTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.auth = PublishingAuth(self.root)
        self.body = dict(client_id='fixture.apps.googleusercontent.com', client_secret='fixture-secret',
                         refresh_token='fixture-refresh', scope=SCOPE)

    def reply(self, **changes):
        body = dict(access_token='fixture-access', token_type='Bearer', scope=SCOPE, expires_in=3600)
        body.update(changes)
        return body

    def test_storage_is_separate_encrypted_and_analytics_untouched(self):
        analytics = YouTubeAuth(self.root)
        analytics.path.parent.mkdir(parents=True)
        analytics.path.write_bytes(b'unchanged-existing-analytics-fixture')
        self.auth._save(self.body)
        self.assertNotEqual(self.auth.path, analytics.path)
        self.assertNotIn(b'fixture-refresh', self.auth.path.read_bytes())
        self.assertEqual(self.auth._load(), self.body)
        self.assertEqual(analytics.path.read_bytes(), b'unchanged-existing-analytics-fixture')

    def test_token_cache_and_invalidate(self):
        self.auth._save(self.body)
        with patch('studio.youtube_publish_auth.exchange', return_value=self.reply()) as fake:
            self.assertEqual(self.auth.token(), 'fixture-access')
            self.auth.token()
            self.assertEqual(fake.call_count, 1)
            self.auth.invalidate()
            self.auth.token()
            self.assertEqual(fake.call_count, 2)

    def test_rotated_refresh_token_is_saved(self):
        self.auth._save(self.body)
        with patch('studio.youtube_publish_auth.exchange', return_value=self.reply(refresh_token='rotated-fixture')):
            self.auth.token()
        self.assertEqual(self.auth._load()['refresh_token'], 'rotated-fixture')

    def test_unconnected_fails_without_network(self):
        with patch('studio.youtube_publish_auth.exchange') as fake:
            with self.assertRaises(PublishingAuthError): self.auth.token()
            fake.assert_not_called()

    def test_extra_missing_and_blank_scopes_rejected(self):
        for scopes in (SCOPE + ' email', next(iter(SCOPES)), ''):
            with self.subTest(scopes=scopes), self.assertRaises(ValueError):
                validate_reply(self.reply(scope=scopes), require_scope=True)
        response = self.reply()
        del response['scope']
        with self.assertRaises(ValueError): validate_reply(response, require_scope=True)
        self.assertEqual(validate_reply(response), response)

    def test_saved_wrong_scope_rejected(self):
        self.auth._save(dict(self.body, scope='https://www.googleapis.com/auth/yt-analytics.readonly'))
        with self.assertRaises(PublishingAuthError): self.auth._load()

    def test_bad_token_types_and_expiries(self):
        for change in (dict(access_token=''), dict(token_type='mac'), dict(expires_in=0),
                       dict(expires_in=True), dict(expires_in=999999), dict(expires_in='oops')):
            with self.subTest(change=change), self.assertRaises((ValueError, TypeError)):
                validate_reply(self.reply(**change))

    def test_exchange_sanitizes_errors(self):
        with patch('studio.youtube_publish_auth.build_opener', side_effect=RuntimeError('secret-token-response')):
            with self.assertRaises(PublishingAuthError) as caught:
                exchange({'grant_type': 'refresh_token'})
        self.assertNotIn('secret-token-response', str(caught.exception))

    def test_exchange_initial_scope_confirmation(self):
        response = Mock()
        body = self.reply()
        del body['scope']
        response.read.return_value = json.dumps(body).encode()
        opener = Mock()
        opener.open.return_value.__enter__ = Mock(return_value=response)
        opener.open.return_value.__exit__ = Mock(return_value=False)
        with patch('studio.youtube_publish_auth.build_opener', return_value=opener):
            with self.assertRaises(PublishingAuthError): exchange({'grant_type': 'authorization_code'})
            self.assertEqual(exchange({'grant_type': 'refresh_token'})['access_token'], 'fixture-access')

    @staticmethod
    def callback(handler_type, path):
        handler = handler_type.__new__(handler_type)
        handler.path = path
        handler.wfile = io.BytesIO()
        handler.send_response = Mock()
        handler.send_error = Mock()
        handler.send_header = Mock()
        handler.end_headers = Mock()
        handler.do_GET()
        return handler

    def test_callback_rejects_wrong_or_duplicate_state_and_path(self):
        for path in ('/callback?state=wrong&code=x', '/callback?state=ok&state=ok&code=x',
                     '/bad?state=ok&code=x', '/callback?state=&code=x'):
            outcome = {}
            handler = self.callback(callback_handler('ok', outcome), path)
            self.assertEqual(outcome, {})
            handler.send_error.assert_called_once()

    def test_callback_accepts_once_and_does_not_echo_code(self):
        outcome = {}
        cls = callback_handler('ok', outcome)
        handler = self.callback(cls, '/callback?state=ok&code=private-code')
        self.assertEqual(outcome, {'code': 'private-code'})
        self.callback(cls, '/callback?state=ok&code=replacement')
        self.assertEqual(outcome, {'code': 'private-code'})
        self.assertNotIn(b'private-code', handler.wfile.getvalue())

    def test_callback_denied_or_duplicate_code_is_not_success(self):
        for query in ('state=ok&error=access_denied', 'state=ok&code=a&code=b',
                      'state=ok&code=a&error=denied'):
            outcome = {}
            self.callback(callback_handler('ok', outcome), '/callback?' + query)
            self.assertEqual(outcome, {'error': True})

    def test_consent_uses_pkce_exact_scopes_and_separate_storage(self):
        client = self.root / 'client.json'
        client.write_text(json.dumps({'installed': self.body}))
        opened = {}
        outer = self
        class FakeServer:
            def __init__(self, address, handler):
                outer.assertEqual(address, ('127.0.0.1', 0))
                self.handler = handler
                self.server_port = 43210
            def handle_request(self):
                state = opened['query']['state'][0]
                outer.callback(self.handler, '/callback?' + urlencode({'state': state, 'code': 'fixture-code'}))
            def server_close(self): pass
        def open_browser(url):
            opened['query'] = parse_qs(urlsplit(url).query)
            return True
        with patch('studio.youtube_publish_auth.HTTPServer', FakeServer), \
             patch('studio.youtube_publish_auth.webbrowser.open', side_effect=open_browser), \
             patch('studio.youtube_publish_auth.exchange', return_value=self.reply(refresh_token='fixture-refresh')) as fake:
            self.auth.connect(client)
        query = opened['query']
        self.assertEqual(set(query['scope'][0].split()), SCOPES)
        self.assertEqual(query['include_granted_scopes'], ['false'])
        self.assertEqual(query['code_challenge_method'], ['S256'])
        self.assertEqual(query['redirect_uri'], ['http://127.0.0.1:43210/callback'])
        self.assertGreaterEqual(len(fake.call_args.args[0]['code_verifier']), 43)
        self.assertEqual(self.auth._load()['scope'], SCOPE)

    def test_invalid_client_never_opens_browser(self):
        client = self.root / 'client.json'
        client.write_text('{}')
        with patch('studio.youtube_publish_auth.webbrowser.open') as browser:
            with self.assertRaises(PublishingAuthError): self.auth.connect(client)
            browser.assert_not_called()

    def test_cancel_does_not_replace_existing_credentials(self):
        self.auth._save(self.body)
        before = self.auth.path.read_bytes()
        client = self.root / 'client.json'
        client.write_text(json.dumps({'installed': self.body}))
        with patch('studio.youtube_publish_auth.HTTPServer') as server, \
             patch('studio.youtube_publish_auth.webbrowser.open', return_value=False):
            server.return_value.server_port = 12345
            with self.assertRaises(PublishingAuthError): self.auth.connect(client)
        self.assertEqual(self.auth.path.read_bytes(), before)


if __name__ == '__main__': unittest.main()
