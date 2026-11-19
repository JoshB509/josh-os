import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import Mock, patch
from studio.youtube_auth import YouTubeAuth, protect, SCOPE
from studio.youtube_cache import YouTubeCache
from studio.youtube_analytics import AnalyticsError
from studio.scorecard import Scorecard
from studio.store import Store


class YouTubeConnectionTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
    def tearDown(self):
        self.temp.cleanup()
    def test_dpapi_roundtrip(self):
        encrypted = protect(b'nonsecret-test-fixture')
        self.assertNotIn(b'nonsecret-test-fixture', encrypted)
        self.assertEqual(protect(encrypted, decrypt=True), b'nonsecret-test-fixture')
    def test_token_saved_encrypted_and_refreshed(self):
        auth = YouTubeAuth(self.root)
        auth._save(dict(client_id='test', client_secret='fixture-secret', refresh_token='fixture-refresh', scope=SCOPE))
        self.assertNotIn(b'fixture-refresh', auth.path.read_bytes())
        self.assertTrue(auth.connected)
        with patch('studio.youtube_auth.exchange', return_value=dict(access_token='fixture-access', expires_in=3600)) as exchange:
            self.assertEqual(auth.token(), 'fixture-access')
            self.assertEqual(auth.token(), 'fixture-access')
            self.assertEqual(exchange.call_count, 1)
    def test_unconnected_cache_does_not_call_google(self):
        client = Mock()
        YouTubeCache(self.root, client=client).refresh('2026-11-01', '2026-11-03')
        client.daily.assert_not_called()
    def test_failed_refresh_preserves_last_good(self):
        client = Mock()
        client.daily.side_effect = AnalyticsError('Test unavailable')
        cache = YouTubeCache(self.root, auth=Mock(connected=True), client=client)
        cache._write('snapshot.json', {'daily': [{'date':'2026-11-01','views':5}]})
        cache.refresh('2026-11-01','2026-11-03')
        self.assertEqual(cache.snapshot()['daily'][0]['views'], 5)
        self.assertEqual(cache.attempt()['state'], 'failed')
    def test_empty_refresh_preserves_last_good(self):
        cache = YouTubeCache(self.root, auth=Mock(connected=True), client=Mock())
        cache.client.daily.return_value = {'daily': []}
        cache._write('snapshot.json', {'daily': [{'date':'2026-11-01','views':5}]})
        cache.refresh('2026-11-01','2026-11-03')
        self.assertEqual(cache.snapshot()['daily'][0]['views'], 5)
    def test_scorecard_uses_direct_analytics_with_gaps(self):
        sc = Scorecard(Store(self.root), self.root, self.root)
        snap = dict(today_utc='2026-11-04',fetched_at='2026-11-04T00:00:00+00:00',daily=[],status={})
        (sc.dir/'snapshot.json').write_text(json.dumps(snap))
        sc.youtube._write('snapshot.json', dict(data_through='2026-11-02',fetched_at='2026-11-04T00:00:00+00:00',
            daily=[dict(date='2026-11-02',views=10,net_subscribers=-2)]))
        view=sc.view();card=view['cards'][0]
        self.assertEqual(card['headline']['value'],10)
        self.assertEqual(card['supporting'][1]['value'],-2)
        self.assertTrue(card['headline']['current_partial'])
        self.assertIsNone(card['headline']['delta'])
        self.assertEqual(card['headline']['timezone'],'America/Los_Angeles')
        self.assertIn('Pacific',card['headline']['note'])
    def test_bad_client_file_does_not_open_browser(self):
        path=self.root/'client.json';path.write_text('{}')
        with patch('studio.youtube_auth.webbrowser.open') as browser, self.assertRaises(AnalyticsError):
            YouTubeAuth(self.root).connect(path)
        browser.assert_not_called()


if __name__ == '__main__':
    unittest.main()
