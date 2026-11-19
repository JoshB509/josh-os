"""The app recovers from, or says when, part of it fails to load (16 Nov 2026).

What was measured on 16 Nov: the server refused 16 to 58 of 54 to 100 connections opened at once on Josh's PC, and from
the real remote address 3 of 324 burst requests came back as a Cloudflare 502 page, one of them youtube.js. Through
Cloudflare a browser asks for all ~54 files at once, so a page load can lose a few files. A lost stylesheet leaves pages
as unstyled text (Josh's Scorecard), and a lost youtube.js makes opening a video fail with "ytChecks is not defined".

The guard at the top of writer.js fetches a failed stylesheet again in its own place, up to three times. A failed
script is named in a banner with a Reload button, because scripts must run in order. The page never reloads by itself,
so nothing Josh has typed is lost. Refused files here get a Cloudflare-style 502 HTML page. The real index.html and web
files are served from disk through Playwright routing, and every /api call gets an empty answer. Nothing reaches the
live app.
"""
import mimetypes
import unittest
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright

WEB = Path(__file__).resolve().parents[1] / 'studio' / 'web'
HOST = 'http://127.0.0.1:59981'
BAD_GATEWAY = '<!DOCTYPE html><title>502 Bad gateway</title><p>Cloudflare could not reach the host.</p>'
CSS = ['courses.css', 'emails.css', 'scorecard.css', 'team.css']
JS = ['youtube.js']
MISSING = CSS + JS
# What Cloudflare injects into the page for its optional Web Analytics. It is blocked here, as the app's CSP blocks it.
BEACON = ('<script defer src="https://static.cloudflareinsights.com/beacon.min.js/vcd15cbe7772f49c399c6a5babf22c1241717689176015" '
          'data-cf-beacon=\'{"token": "test"}\'></script><link rel="stylesheet" href="/cdn-cgi/test-optional.css">')
SETTLED = 'Array.isArray(window.careerosRetrying) && window.careerosRetrying.length === 0'


class LoadGuard(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(headless=True, channel='chrome')

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()

    def open(self, refuse='never', width=1366, height=900, beacon=False):
        """refuse: 'never', 'first_request' (each missing file fails once, then works), 'first_load' (every request
        during the first page load fails) or 'always'."""
        loads, asked = {'n': 0}, {'outside': 0}

        def handle(route):
            p = urlparse(route.request.url).path
            f = p.lstrip('/')
            if not route.request.url.startswith(HOST):
                asked['outside'] += 1
                return route.abort('blockedbyclient')
            if p == '/':
                loads['n'] += 1
                html = (WEB / 'index.html').read_text(encoding='utf-8')
                return route.fulfill(status=200, content_type='text/html', body=html.replace('</body>', BEACON + '</body>') if beacon else html)
            if p.startswith('/cdn-cgi/'):
                asked[f] = asked.get(f, 0) + 1
                return route.fulfill(status=403, content_type='text/html', body='blocked')
            if p.startswith('/api/'):
                return route.fulfill(status=200, content_type='application/json', body='[]' if p.endswith('s') else '{}')
            if f in MISSING:
                asked[f] = asked.get(f, 0) + 1
                if (refuse == 'always' or (refuse == 'first_load' and loads['n'] == 1)
                        or (refuse == 'first_request' and asked[f] == 1)):
                    return route.fulfill(status=502, content_type='text/html; charset=UTF-8', body=BAD_GATEWAY)
            path = WEB / f
            if not path.is_file():
                return route.fulfill(status=404, body='')
            return route.fulfill(path=str(path), content_type=mimetypes.guess_type(str(path))[0] or 'text/plain')

        ctx = self.browser.new_context(viewport={'width': width, 'height': height})
        self.addCleanup(ctx.close)
        ctx.route('**/*', handle)
        page = ctx.new_page()
        page.goto(HOST + '/')
        page.wait_for_function(SETTLED, timeout=20000)
        return page, loads, asked

    def settle(self, page):
        page.wait_for_timeout(300)
        page.wait_for_function(SETTLED, timeout=20000)

    def rules(self, page, file):
        return page.evaluate("""f=>[...document.styleSheets].filter(s=>new URL(s.href||location.href).pathname==='/'+f)
            .reduce((n,s)=>{try{return n+s.cssRules.length}catch{return n}},0)""", file)

    def test_a_full_load_shows_nothing_and_never_reloads(self):
        page, loads, _ = self.open()
        self.settle(page)
        self.assertEqual(page.evaluate('window.careerosLoadFailed'), [])
        self.assertEqual(page.locator('#load-failed').count(), 0)
        self.assertEqual(loads['n'], 1)
        self.assertEqual(page.evaluate('typeof ytChecks'), 'function')

    def test_stylesheets_that_fail_once_come_back_without_a_reload(self):
        page, loads, asked = self.open(refuse='first_request')
        self.settle(page)
        for f in CSS:
            self.assertGreaterEqual(asked[f], 2, f + ' should have been fetched again')
            self.assertGreater(self.rules(page, f), 0, f + ' should be applied after the retry')
        # Scorecard's layout rule is live again: .sc lays out as a column of cards.
        self.assertEqual(page.evaluate("()=>{const d=document.createElement('div');d.className='sc';document.body.append(d);return getComputedStyle(d).display}"), 'flex')
        self.assertEqual(loads['n'], 1, 'it must never reload by itself')
        # Scripts are never run again out of order, so youtube.js is named for Josh to reload.
        self.assertEqual(page.evaluate('window.careerosLoadFailed'), JS)
        self.assertIn('youtube.js', page.locator('#load-failed').inner_text())

    def test_files_that_keep_failing_are_named_and_the_page_never_reloads_by_itself(self):
        page, loads, asked = self.open(refuse='always')
        self.settle(page)
        self.assertEqual(page.evaluate('window.careerosLoadFailed'), sorted(MISSING))
        for f in CSS:
            self.assertEqual(asked[f], 4, f + ': the first request and three retries, then it stops')
        bar = page.locator('#load-failed')
        for f in MISSING:
            self.assertIn(f, bar.inner_text())
        self.assertEqual(bar.get_by_role('button', name='Reload').count(), 1)
        page.wait_for_timeout(2000)
        self.assertEqual(loads['n'], 1, 'it must never reload without Josh pressing Reload')

    def test_reload_button_recovers_and_the_video_opens(self):
        page, loads, _ = self.open(refuse='first_load')
        self.settle(page)
        self.assertEqual(page.evaluate('typeof ytChecks'), 'undefined')
        page.locator('#load-failed').get_by_role('button', name='Reload').click()
        page.wait_for_function("typeof ytChecks === 'function'", timeout=20000)
        self.settle(page)
        self.assertEqual(loads['n'], 2)
        self.assertEqual(page.evaluate('window.careerosLoadFailed'), [])
        self.assertEqual(page.locator('#load-failed').count(), 0)
        # brief() is what threw "ytChecks is not defined" when a video opened.
        html = page.evaluate("""()=>brief.call(null,current={id:'t',name:'Test video',stage:'Ready to record',script:'one two',
            titles:[],chosen_title:null,thumbnails:[],top_three:[],packaging:{},notes:'',publish_date:''})""")
        self.assertIn('glance-checks', html)

    def test_typing_survives_stylesheet_retries_and_a_late_failure(self):
        page, loads, _ = self.open(refuse='first_request')
        page.evaluate("""()=>{const t=document.createElement('textarea');t.id='typing';document.body.append(t)}""")
        page.locator('#typing').fill('Unsaved words Josh is typing')
        self.settle(page)
        # A script that fails long after load, the way a lazy load or a dropped request would.
        page.evaluate("""()=>{const s=document.createElement('script');s.src='/late-missing.js';document.body.append(s)}""")
        page.wait_for_function("window.careerosLoadFailed.includes('late-missing.js')", timeout=10000)
        page.wait_for_timeout(1500)
        self.assertEqual(loads['n'], 1, 'neither a retry nor a late failure may reload the page')
        self.assertEqual(page.locator('#typing').input_value(), 'Unsaved words Josh is typing')
        self.assertIn('late-missing.js', page.locator('#load-failed').inner_text())

    def test_blocked_optional_analytics_is_ignored_entirely(self):
        page, loads, asked = self.open(beacon=True)
        self.settle(page)
        page.wait_for_timeout(5000)  # longer than every retry wait
        self.assertEqual(asked['outside'], 1, 'the beacon must not be fetched again')
        self.assertEqual(asked.get('cdn-cgi/test-optional.css'), 1, 'files under /cdn-cgi/ must not be fetched again')
        self.assertEqual(page.evaluate('window.careerosLoadFailed'), [])
        self.assertEqual(page.evaluate('window.careerosRetrying'), [])
        self.assertEqual(page.locator('#load-failed').count(), 0)
        self.assertEqual(loads['n'], 1)
        self.assertEqual(page.evaluate('typeof ytChecks'), 'function')

    def test_phone_email_rows_wrap_the_subject_beside_the_date(self):
        page, _, _ = self.open(width=390, height=844)
        rect = page.evaluate("""()=>{const d=document.createElement('div');d.className='eq-list';d.innerHTML=
            '<button class="eq-row"><span class="eq-row-main"><b>Struggling to get your proposal considered? Read this now</b>'+
            '<span>Choose the person who needs to act first.</span></span><span class="eq-row-when">Wed, Nov 18</span></button>';
            document.body.append(d);const b=d.querySelector('b').getBoundingClientRect(),w=d.querySelector('.eq-row-when').getBoundingClientRect();
            return {bRight:b.right,whenLeft:w.left,bHeight:b.height}}""")
        self.assertLessEqual(rect['bRight'], rect['whenLeft'] + 0.5, 'the subject runs under the date')
        self.assertGreater(rect['bHeight'], 30, 'a long subject should wrap onto a second line')


if __name__ == '__main__':
    unittest.main()
