"""Real frontend assets, intercepted requests, synthetic data; no live server."""
import json
import mimetypes
from pathlib import Path
import tempfile
import unittest
from urllib.parse import urlparse
from playwright.sync_api import sync_playwright
from studio.lead_magnets import LeadMagnets
from studio.store import Store

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / 'studio' / 'web'
HOST = 'http://127.0.0.1:59982'


class DirectoryBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.data = LeadMagnets(Store(Path(cls.tmp.name) / 'Data'), ROOT).directory()
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(headless=True, channel='chrome')

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()
        cls.tmp.cleanup()

    def setUp(self):
        self.errors = []
        self.fail = False
        self.context = self.browser.new_context(viewport={'width': 1366, 'height': 1000})
        self.context.route('**/*', self.handle)
        self.page = self.context.new_page()
        self.page.on('pageerror', lambda e: self.errors.append(str(e)))
        self.addCleanup(self.context.close)

    def handle(self, route):
        if not route.request.url.startswith(HOST):
            return route.abort()
        path = urlparse(route.request.url).path
        if path == '/icon.png':
            return route.fulfill(path=str(ROOT / 'CareerOS.png'), content_type='image/png')
        if path == '/api/lead-magnets':
            return route.fulfill(status=503 if self.fail else 200, content_type='application/json', body=json.dumps(self.data))
        if path == '/api/state':
            return route.fulfill(content_type='application/json', body=json.dumps(dict(projects=[], jobs=[], stages=['Idea'])))
        if path.startswith('/api/'):
            return route.fulfill(content_type='application/json', body='[]' if path.endswith('s') else '{}')
        file = WEB / ('lead-magnets.html' if path == '/lead-magnets' else 'index.html' if path == '/' else path.lstrip('/'))
        if not file.is_file():
            return route.fulfill(status=404, body='')
        return route.fulfill(path=str(file), content_type=mimetypes.guess_type(str(file))[0] or 'text/plain')

    def open_directory(self):
        self.page.goto(HOST + '/lead-magnets')
        self.page.wait_for_selector('.magnet-card')

    def test_cards_search_links_and_mobile_layout(self):
        self.open_directory()
        self.assertEqual(self.page.locator('.magnet-card').count(), 16)
        self.assertEqual(self.page.locator('.magnet-links a').count(), 16)
        self.assertEqual(self.page.locator('.rail a[href="/lead-magnets"][aria-current="page"]').count(), 1)
        self.page.locator('#magnet-search').fill('worksheet')
        self.assertGreater(self.page.locator('.magnet-card').count(), 0)
        self.assertLess(self.page.locator('.magnet-card').count(), 16)
        self.page.locator('#magnet-search').fill('no matching resource xyz')
        self.assertEqual(self.page.locator('.magnet-card').count(), 0)
        self.assertIn('No lead magnets match', self.page.locator('#magnet-status').inner_text())
        self.page.locator('#magnet-search').fill('')
        self.assertNotIn('source_id', self.page.locator('main').inner_text())
        self.assertNotIn('magnet.md', self.page.locator('main').inner_text())
        evidence = ROOT / 'test-evidence'
        evidence.mkdir(exist_ok=True)
        self.page.screenshot(path=str(evidence / 'lead-magnets-desktop.png'), full_page=True)
        self.page.set_viewport_size({'width': 390, 'height': 844})
        self.assertTrue(self.page.evaluate('document.documentElement.scrollWidth <= innerWidth'))
        self.page.screenshot(path=str(evidence / 'lead-magnets-mobile.png'), full_page=True)
        self.page.locator('#shell-more').click()
        self.assertTrue(self.page.locator('.more-sheet a[href="/lead-magnets"]').is_visible())
        self.assertEqual(self.errors, [])

    def test_failure_retry_and_escaping(self):
        self.fail = True
        self.page.goto(HOST + '/lead-magnets')
        self.page.wait_for_selector('#magnet-error:not([hidden])')
        self.fail = False
        self.page.locator('#magnet-retry').click()
        self.page.wait_for_selector('.magnet-card')
        self.assertEqual(self.page.locator('.magnet-card').count(), 16)
        original = self.data
        try:
            self.data = {'magnets': [dict(id='x', name='<img src=x onerror=alert(1)>', landing_url='javascript:alert(1)', description='A <script>bad</script> value')]}
            self.page.reload()
            self.page.wait_for_selector('.magnet-card')
            self.assertEqual(self.page.locator('.magnet-card img,.magnet-card script,.magnet-card a').count(), 0)
            self.assertIn('<img', self.page.locator('.magnet-card h2').inner_text())
        finally:
            self.data = original
        self.assertEqual(self.errors, [])

    def test_main_sidebar_only_offers_supported_directory(self):
        self.fail = True
        self.page.goto(HOST + '/')
        self.page.wait_for_load_state('networkidle')
        self.assertEqual(self.page.locator('.rail a[href="/lead-magnets"]').count(), 0)
        self.fail = False
        self.page.reload()
        self.page.wait_for_selector('.rail a[href="/lead-magnets"]', state='attached')
        self.assertEqual(self.page.locator('.rail a[href="/lead-magnets"]').count(), 1)


if __name__ == '__main__':
    unittest.main()
