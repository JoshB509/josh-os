"""Library filters keep inspiration, the style reference and Josh's own thumbnails apart (16 Nov 2026).

Inspiration is Creator Hooks plus the outside videos Josh links Sam to save. Thumbnail formats are the reference for
Josh's own visual style. Thumbnails on Josh's own video projects (catalogue kind "saved") are his output and are never
counted as inspiration. Before this, "Saved" mixed 2 outside videos with 96 of his own thumbnails. The split is a view
only, so the catalogue keeps every item and kind. The real index.html and web files are served through Playwright
routing with a small made-up catalogue. Nothing reaches the live app.
"""
import json
import mimetypes
import unittest
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright

WEB = Path(__file__).resolve().parents[1] / 'studio' / 'web'
HOST = 'http://127.0.0.1:59982'
YT = 'https://www.youtube.com/watch?v=abcdefghijk'
CATALOG = [
    dict(id='hook:1', kind='hooks', title='Test hook about bosses', credit='Jake Thomas / Creator Hooks', video=YT, tags=[]),
    dict(id='hook:2', kind='hooks', title='Test hook two', credit='Jake Thomas / Creator Hooks', tags=[]),
    dict(id='video:1', kind='videos', title='Test saved video, same video as hook 1', credit='Test channel', video=YT, source=YT, tags=['Saved video']),
    dict(id='video:2', kind='videos', title='Test saved video two', credit='Other channel', video='https://youtu.be/zzzzzzzzzzz', tags=['Saved video']),
    dict(id='format:1', kind='formats', title='Test format', credit='Test creator', source=YT, tags=[]),
    dict(id='saved:a1', kind='saved', title='own_thumb_v01.png', credit='Saved in Test video', source=YT, tags=['Saved thumbnail']),
    dict(id='saved:a2', kind='saved', title='own_thumb_v02.png', credit='Saved in Test video', tags=['Saved thumbnail']),
]


def handle(route):
    p = urlparse(route.request.url).path
    if not route.request.url.startswith(HOST):
        return route.abort()
    if p == '/':
        return route.fulfill(status=200, content_type='text/html', body=(WEB / 'index.html').read_text(encoding='utf-8'))
    if p == '/api/state':
        return route.fulfill(status=200, content_type='application/json', body=json.dumps(dict(projects=[], jobs=[], version='test',
                             stages=['Idea', 'Scripting', 'Ready to record', 'Recording', 'Recorded', 'Editing', 'Review', 'Scheduled', 'Published'])))
    if p == '/api/inspiration':
        return route.fulfill(status=200, content_type='application/json', body=json.dumps(CATALOG))
    if p.startswith('/api/'):
        return route.fulfill(status=200, content_type='application/json', body='[]' if p.endswith('s') else '{}')
    f = WEB / p.lstrip('/')
    if not f.is_file():
        return route.fulfill(status=404, body='')
    return route.fulfill(path=str(f), content_type=mimetypes.guess_type(str(f))[0] or 'text/plain')


class LibraryFilters(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(headless=True, channel='chrome')
        ctx = cls.browser.new_context(viewport={'width': 1366, 'height': 900})
        ctx.route('**/*', handle)
        cls.page = ctx.new_page()
        cls.page.goto(HOST + '/')
        cls.page.wait_for_function("typeof visibleLibrary === 'function' && Array.isArray(state?.projects)", timeout=20000)
        cls.page.evaluate("()=>{view='inspiration';library=[];render()}")
        cls.page.wait_for_selector('[data-library-kind]', timeout=20000)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()

    def kinds(self, k):
        return self.page.evaluate("k=>visibleLibrary(k).map(g=>[g.id,...g.also.map(a=>a.id)])", k)

    def test_every_item_is_in_exactly_one_set(self):
        seen = [i for k in ('hooks', 'videos', 'formats', 'own') for g in self.kinds(k) for i in g]
        self.assertEqual(sorted(seen), sorted(c['id'] for c in CATALOG))

    def test_all_inspiration_is_hooks_and_saved_videos_only(self):
        ids = [i for g in self.kinds('all') for i in g]
        self.assertEqual(sorted(ids), ['hook:1', 'hook:2', 'video:1', 'video:2'])
        counts = self.page.evaluate("()=>Object.fromEntries([...document.querySelectorAll('[data-library-kind]')].map(b=>[b.dataset.libraryKind,b.querySelector('span')?+b.querySelector('span').textContent:null]))")
        self.assertEqual({k: counts[k] for k in ('all', 'hooks', 'videos', 'formats', 'own')}, dict(all=3, hooks=2, videos=2, formats=1, own=2))

    def test_a_saved_video_of_a_creator_hooks_example_shows_as_one_card_with_both_sources(self):
        groups = self.kinds('all')
        self.assertIn(['hook:1', 'video:1'], groups)
        # The format and Josh's own thumbnail point at the same video and must not join it.
        self.assertFalse(any('format:1' in g or 'saved:a1' in g for g in groups))

    def test_own_thumbnails_are_labelled_as_his_and_never_as_saved(self):
        self.page.click("[data-library-kind='own']")
        self.page.wait_for_timeout(400)
        text = self.page.locator('.inspiration-grid').inner_text()
        self.assertIn('Your thumbnail', text)
        self.assertNotIn('Saved thumbnail', text)
        self.assertIn('apart from the inspiration counts', self.page.locator('.library-notice').inner_text())


if __name__ == '__main__':
    unittest.main()
