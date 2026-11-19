"""The thumbnail workspace and its homepage preview (16 Nov 2026), against the real Store on a disposable folder.

Josh's brief: the thumbnails piece is the library plus three positions, Main, B and C, shown as YouTube home cards.
Placing a thumbnail in a position is the confirmation. There is no creator panel and no lock-in step. Every save the
page makes goes to studio.store.Store.save on a throwaway store, so the real lock guard (guard_locked) and revision check
decide what is saved. The tests cover a failed save, a double click, opening another video while a save is in flight,
and an older video placed before the lock followed Main.

The real index.html and web files are served from disk through Playwright routing, with the thumbnail creator added the
way the server adds it. Projects, titles and images are made-up test data. Nothing reaches the live app or its data.
"""
import io
import json
import mimetypes
import tempfile
import unittest
import uuid
from pathlib import Path
from urllib.parse import urlparse

from PIL import Image
from playwright.sync_api import sync_playwright

from studio.store import Conflict, Store

WEB = Path(__file__).resolve().parents[1] / 'studio' / 'web'
HOST = 'http://127.0.0.1:59983'
CATALOG = [
    dict(id='video:abc', kind='videos', title='Test saved video', credit='Test channel', image='/inspiration/youtube-ideas/abc.jpg',
         description='Published 2026-01-10 · 346,951 views when saved · Saved 2026-11-11'),
    *[dict(id=f'format:{i}', kind='formats', title=f'Test format {i}', credit='Format creator' if i else 'Original creator — see source screenshot',
           image=f'/inspiration/thumbnail-formats/crops/{i}.png', description='Style note') for i in range(3)],
    dict(id='saved:x', kind='saved', title='own_file.png', credit='Saved in Test video', image='/assets/x'),
    dict(id='hook:1', kind='hooks', title='Test hook', credit='Jake Thomas / Creator Hooks', image='/inspiration/creatorhooks-thumbs/1.jpg'),
]
LOCKED_AT = '2026-11-01T09:00:00'


def png(colour):
    b = io.BytesIO()
    Image.new('RGB', (32, 18), colour).save(b, 'PNG')
    return b.getvalue()


class ThumbnailWorkspace(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(headless=True, channel='chrome')

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name) / 'Data')
        self.a = [self.store.import_image(f'cand_{i}_file_v01.png', png(c))['asset']
                  for i, c in enumerate(((200, 40, 40), (40, 200, 40), (40, 40, 200), (200, 200, 40), (90, 90, 90)))]
        a = self.a
        title = str(uuid.uuid4())
        self.p1 = str(uuid.uuid4())
        self.store.create('Test video', pid=self.p1)
        self.store.save(self.p1, dict(titles=[dict(id=title, text='Test title for the card')], chosen_title=title,
                                      thumbnails=[dict(asset=x, name=f'{x[:6]}_file_v01.png', title_id='') for x in a[:4]],
                                      top_three=[a[0], a[1]], locks=dict(thumbnail=LOCKED_AT)), 1)
        # An older video: placed before the lock followed Main, so it has a Main and no lock.
        self.p2 = str(uuid.uuid4())
        self.store.create('Older test video', pid=self.p2)
        self.store.save(self.p2, dict(thumbnails=[dict(asset=a[4], name='older_file.png', title_id='')], top_three=[a[4]]), 1)
        self.puts, self.outside, self.fail_put, self.hold, self.held = [], [], set(), False, []

        def answer(route, pid, body):
            n = len(self.puts) + 1
            self.puts.append((pid, body['changes']))
            if n in self.fail_put:
                return route.fulfill(status=500, content_type='application/json', body=json.dumps(dict(error='Test failure: the save did not happen.')))
            try:
                saved = self.store.save(pid, body['changes'], body['revision'])
            except Conflict as e:
                return route.fulfill(status=409, content_type='application/json', body=json.dumps(dict(error=str(e))))
            except (ValueError, KeyError) as e:
                return route.fulfill(status=400, content_type='application/json', body=json.dumps(dict(error=str(e))))
            return route.fulfill(status=200, content_type='application/json', body=json.dumps(saved))
        self.answer = answer

        def handle(route):
            req = route.request
            path = urlparse(req.url).path
            if not req.url.startswith(HOST):
                self.outside.append(req.url)
                return route.abort()
            if path == '/':
                html = (WEB / 'index.html').read_text(encoding='utf-8')
                html = html.replace('<link rel="stylesheet" href="/brand-fonts.css">', '<link rel="stylesheet" href="/thumbnail-creator.css"><link rel="stylesheet" href="/brand-fonts.css">')
                return route.fulfill(status=200, content_type='text/html', body=html.replace('</body>', '<script src="/thumbnail-creator.js"></script></body>'))
            parts = path.strip('/').split('/')
            if req.method == 'PUT' and len(parts) == 3 and parts[:2] == ['api', 'projects']:
                body = json.loads(req.post_data)
                if self.hold:
                    self.hold = False
                    self.held.append((route, parts[2], body))
                    return None
                return self.answer(route, parts[2], body)
            if path == '/api/state':
                return route.fulfill(status=200, content_type='application/json', body=json.dumps(dict(projects=self.store.projects(), jobs=[], stages=['Idea', 'Scripting', 'Ready to record', 'Recording', 'Recorded', 'Editing', 'Review', 'Scheduled', 'Published'], version='test')))
            if path == '/api/inspiration':
                return route.fulfill(status=200, content_type='application/json', body=json.dumps(CATALOG))
            if path == '/api/team':
                return route.fulfill(status=200, content_type='application/json', body=json.dumps(dict(agents=[], rooms=[])))
            if path in ('/api/carousels', '/api/people'):
                return route.fulfill(status=200, content_type='application/json', body=json.dumps(dict(items=[])))
            if path.startswith('/api/'):
                return route.fulfill(status=200, content_type='application/json', body='[]' if path.endswith('s') else '{}')
            if path.startswith('/assets/'):
                try:
                    f, mime = self.store.asset(parts[1])
                    return route.fulfill(path=str(f), content_type=mime)
                except ValueError:
                    return route.fulfill(status=404, body='')
            if path.startswith('/inspiration/'):
                return route.fulfill(status=200, content_type='image/png', body=png((120, 120, 120)))
            f = WEB / path.lstrip('/')
            if not f.is_file():
                return route.fulfill(status=404, body='')
            return route.fulfill(path=str(f), content_type=mimetypes.guess_type(str(f))[0] or 'text/plain')

        self.ctx = self.browser.new_context(viewport={'width': 1366, 'height': 900})
        self.addCleanup(self.ctx.close)
        self.ctx.route('**/*', handle)
        self.page = self.ctx.new_page()
        self.errors = []
        # Other parts of the app (Today, the team dock) get empty stub data here and may complain. Only errors raised in
        # the code this workspace runs on count.
        mine = ('studio.js', 'video-page.js', 'app.js', 'thumbnail-creator.js', 'project-folders.js', 'autosave.js')
        self.page.on('pageerror', lambda e: self.errors.append(str(e)) if any(m in (e.stack or '') for m in mine) else None)
        self.page.goto(HOST + '/')
        self.page.wait_for_function("typeof twCommit==='function'&&state.projects.length===2", timeout=20000)
        self.open(self.p1)

    def open(self, pid):
        self.page.evaluate("id=>{openProject(id);vpOpen[id]='thumbnail';renderDetail()}", pid)
        self.page.wait_for_selector('.tw-slot', timeout=10000)

    def press(self, selector, wait=None):
        """Click, then wait until the change has finished saving (twBusy back to null), however slow the disk is. A fixed
        wait let the next click land while a two-step save was still running, where the page ignores it by design. With
        `wait` set (a save is being held on purpose), wait that long instead."""
        self.page.evaluate("s=>document.querySelector(s).click()", selector)
        if wait is not None:
            return self.page.wait_for_timeout(wait)
        self.page.wait_for_function('twBusy===null', timeout=30000)
        self.page.wait_for_timeout(50)

    def place(self, i, asset, wait=None):
        self.press(f'[data-tw-place="{i}"][data-asset="{asset}"]', wait)

    def assert_no_error(self, step):
        shown = self.page.evaluate("()=>{const t=document.querySelector('#toast');return t&&t.style.display==='block'&&t.classList.contains('error')?t.textContent:''}")
        self.assertEqual(shown, '', f'{step} showed an error')

    def saved(self, pid=None):
        return self.store.get(pid or self.p1)

    def shown(self):
        """The assets the three positions show, read from the page."""
        return self.page.evaluate("()=>[...document.querySelectorAll('.tw-slot')].map(s=>{const i=s.querySelector('.yc-thumb img');return i?decodeURIComponent(i.getAttribute('src').split('/assets/')[1].split('?')[0]):null})")

    def toast(self):
        return self.page.evaluate("()=>{const t=document.querySelector('#toast');return t&&t.style.display==='block'?t.textContent:''}")

    def test_cards_and_no_creator_or_lock_in_step(self):
        pg = self.page
        self.assertEqual(pg.locator('.tw-slot').count(), 3)
        self.assertFalse(pg.locator('.tc-entry').is_visible(), 'the creator panel must not show')
        self.assertEqual(pg.locator('[data-vp-lock],[data-vp-unlock]').count(), 0, 'no lock-in or unlock button')
        card = pg.locator('.tw-slot').first.inner_text()
        self.assertIn('Test title for the card', card)
        self.assertIn('Josh Brindley', card)
        # Josh asked for these preview values on his own cards, with no explanation shown or read out.
        self.assertIn('123K views • 3 days ago', card)
        self.assertIn('12:38', card)
        marked = pg.evaluate("()=>[...document.querySelector('.tw-slot').querySelectorAll('[data-example]')].map(e=>[e.className,e.getAttribute('title'),e.querySelectorAll('.sr-only').length])")
        self.assertEqual(marked, [['yc-dur', None, 0], ['yc-line', None, 0]])
        whole = pg.evaluate("()=>document.querySelector('.tw').textContent+[...document.querySelectorAll('.tw [title]')].map(e=>e.title).join(' ')").lower()
        for word in ('example', 'not real', 'mock'):
            self.assertNotIn(word, whole)
        self.assertNotIn('_file_v01', pg.locator('.tw').inner_text(), 'file names are not shown')
        self.assertEqual(self.shown(), [self.a[0], self.a[1], None])
        self.assertEqual(self.puts, [], 'opening the workspace saves nothing')
        self.assertEqual(self.errors, [])

    def test_the_real_guard_refuses_a_new_main_while_locked(self):
        with self.assertRaises(Conflict):
            self.store.save(self.p1, dict(top_three=[self.a[2], self.a[1]], locks=dict(thumbnail=LOCKED_AT)), self.saved()['revision'])

    def test_placing_in_main_on_a_locked_video_passes_the_real_guard(self):
        rev = self.saved()['revision']
        self.place(0, self.a[2])
        s = self.saved()
        self.assertEqual(s['top_three'], [self.a[2], self.a[1]])
        self.assertTrue(s['locks'].get('thumbnail'), 'placed in Main is confirmed')
        self.assertNotEqual(s['locks']['thumbnail'], LOCKED_AT)
        self.assertEqual(s['revision'], rev + 2, 'saved unlocked, then locked again')
        self.assertNotIn('thumbnail', self.puts[0][1]['locks'])
        self.assertEqual(set(self.puts[0][1]), {'top_three', 'thumbnails', 'locks'}, 'only the thumbnail fields are sent')
        self.assertEqual(len(s['thumbnails']), 4, 'every candidate stays in the library')
        self.assertEqual(self.shown(), [self.a[2], self.a[1], None])
        self.assertEqual(self.errors, [])

    def test_swap_reorder_and_take_out(self):
        self.place(0, self.a[1])  # B to Main, Main to B
        self.assert_no_error('swap into Main')
        self.assertEqual(self.saved()['top_three'], [self.a[1], self.a[0]])
        self.place(2, self.a[3])  # an empty C takes a new one
        self.assert_no_error('place in C')
        self.assertEqual(self.saved()['top_three'], [self.a[1], self.a[0], self.a[3]])
        self.press('.tw-slot[data-rank="1"] [data-tw-move="1"][data-dir="1"]')
        self.assert_no_error('move right')
        self.assertEqual(self.saved()['top_three'], [self.a[1], self.a[3], self.a[0]])
        n = len(self.puts)
        self.press('.tw-slot[data-rank="2"] [data-tw-unpick]')
        self.assert_no_error('take out of C')
        self.assertEqual(self.saved()['top_three'], [self.a[1], self.a[3]])
        self.assertEqual(len(self.puts), n + 1, 'Main is unchanged, so one save')
        self.press('.tw-slot[data-rank="0"] [data-tw-unpick]')
        self.assert_no_error('take out of Main')
        self.assertEqual(self.saved()['top_three'], [self.a[3]])
        self.press('.tw-slot[data-rank="0"] [data-tw-unpick]')
        self.assert_no_error('take the last one out of Main')
        s = self.saved()
        self.assertEqual(s['top_three'], [])
        self.assertNotIn('thumbnail', s.get('locks') or {}, 'with nothing in Main nothing is confirmed')
        self.assertEqual(len(s['thumbnails']), 4)
        self.assertEqual(self.shown(), [None, None, None])
        self.assertEqual(self.errors, [])

    def test_a_failed_save_changes_nothing_and_shows_nothing_new(self):
        before = self.saved()
        self.fail_put = {1}
        self.place(0, self.a[2])
        after = self.saved()
        self.assertEqual((after['top_three'], after['locks'], after['revision']), (before['top_three'], before['locks'], before['revision']))
        self.assertEqual(self.shown(), [self.a[0], self.a[1], None], 'the page still shows what is saved')
        self.assertIn('Test failure', self.toast())
        self.assertFalse(self.page.locator(f'[data-tw-place="0"][data-asset="{self.a[2]}"]').is_disabled(), 'controls work again')
        self.assertEqual(self.page.evaluate('twBusy'), None)
        self.place(0, self.a[2])  # trying again works
        self.assertEqual(self.saved()['top_three'], [self.a[2], self.a[1]])
        self.assertEqual(self.errors, [])

    def test_when_only_the_relock_fails_the_page_shows_what_was_saved(self):
        self.fail_put = {2}
        self.place(0, self.a[2])
        s = self.saved()
        self.assertEqual(s['top_three'], [self.a[2], self.a[1]])
        self.assertNotIn('thumbnail', s.get('locks') or {})
        self.assertEqual(self.shown(), [self.a[2], self.a[1], None])
        self.assertIn('Test failure', self.toast())
        self.place(2, self.a[3])  # the next change sets the lock again with Main
        s = self.saved()
        self.assertEqual(s['top_three'], [self.a[2], self.a[1], self.a[3]])
        self.assertTrue(s['locks'].get('thumbnail'))
        self.assertEqual(self.errors, [])

    def test_a_second_click_while_saving_is_ignored(self):
        self.hold = True
        self.place(0, self.a[2], wait=300)
        self.assertEqual(len(self.held), 1)
        self.assertTrue(self.page.evaluate("()=>[...document.querySelectorAll('.tw [data-tw-place]')].every(b=>b.disabled)"), 'placement controls are disabled')
        self.assertTrue(self.page.evaluate("()=>document.querySelector('.tw').classList.contains('busy')"))
        self.assertIsNone(self.page.evaluate(f"()=>twCommit(p=>twPlace(p,'{self.a[3]}',0),'x')??null"), 'a second change does not start')
        self.page.evaluate(f"()=>document.querySelector('[data-tw-place=\"0\"][data-asset=\"{self.a[3]}\"]').dispatchEvent(new MouseEvent('click',{{bubbles:true}}))")
        route, pid, body = self.held.pop()
        self.answer(route, pid, body)
        self.page.wait_for_timeout(900)
        self.assertEqual(self.saved()['top_three'], [self.a[2], self.a[1]], 'only the first change was saved')
        self.assertEqual(len(self.puts), 2)
        self.assertEqual(self.errors, [])

    def test_opening_another_video_during_a_save_leaves_it_alone(self):
        self.hold = True
        self.place(0, self.a[2], wait=300)
        self.open(self.p2)
        self.assertEqual(self.shown(), [self.a[4], None, None])
        route, pid, body = self.held.pop()
        self.assertEqual(pid, self.p1)
        self.answer(route, pid, body)
        self.page.wait_for_timeout(1200)
        self.assertEqual(self.page.evaluate('current.id'), self.p2)
        self.assertEqual(self.shown(), [self.a[4], None, None], 'the open video still shows its own three')
        self.assertEqual(self.page.evaluate('current.top_three'), [self.a[4]])
        self.assertEqual(self.saved(self.p2)['top_three'], [self.a[4]])
        self.assertEqual(self.saved()['top_three'], [self.a[2], self.a[1]], 'the first video got its change')
        self.assertTrue(self.saved()['locks'].get('thumbnail'))
        self.assertEqual(self.page.evaluate(f"state.projects.find(p=>p.id==='{self.p1}').top_three"), [self.a[2], self.a[1]])
        self.assertTrue(all(pid == self.p1 for pid, _ in self.puts), 'nothing was saved to the second video')
        self.assertEqual(self.errors, [])

    def test_an_older_placed_video_is_chosen_and_not_rewritten_by_opening_it(self):
        self.open(self.p2)
        self.assertEqual(self.shown(), [self.a[4], None, None])
        self.assertTrue(self.page.locator(f'[data-tw-place="0"][data-asset="{self.a[4]}"]').is_disabled(), 'already in Main')
        # Since 18 Nov Packaging has no publish row; publishing starts from the header's Publish button, whose panel reads
        # the project-bound preview. Packaging never tells Josh to place a placed thumbnail again.
        html = self.page.evaluate("""()=>{vpRel[current.id]={items:[{key:'thumbnail',label:'Thumbnail',state:'todo',detail:'1 options, none selected.',next:'Pick the thumbnail.'}]};return vpPublishRow(current)+vpPackaging(current)}""")
        self.assertNotIn('again', html)
        self.assertNotIn('pk-still', html)
        self.assertEqual(self.puts, [], 'opening an older video saves nothing')
        self.assertNotIn('thumbnail', self.saved(self.p2).get('locks') or {})
        self.assertEqual(self.errors, [])

    def test_homepage_preview_uses_saved_inspiration_and_formats_only(self):
        pg = self.page
        pg.click('.tw-head [data-tw-home]')
        pg.wait_for_selector('dialog.yh .yh-card', timeout=10000)
        cards = pg.evaluate("()=>[...document.querySelectorAll('dialog.yh .yh-card')].map(c=>({mine:c.classList.contains('mine'),text:c.innerText}))")
        self.assertEqual(len(cards), 5, 'the saved video, three formats and Josh\'s card')
        self.assertTrue(cards[1]['mine'], 'Josh\'s card sits in the first row')
        text = ' '.join(c['text'] for c in cards)
        self.assertNotIn('own_file', text, 'Josh\'s own library thumbnails are not inspiration')
        self.assertNotIn('Test hook', text)
        self.assertIn('347K views', next(c['text'] for c in cards if 'Test saved video' in c['text']))
        for c in cards:
            if 'Test format' in c['text']:
                self.assertNotIn('views', c['text'], 'no views where none are saved')
                self.assertNotIn('12:38', c['text'])
        mine = next(c['text'] for c in cards if c['mine'])
        self.assertIn('123K views • 3 days ago', mine)
        self.assertIn('12:38', mine)
        self.assertEqual(pg.evaluate("()=>document.querySelectorAll('dialog.yh .yh-card:not(.mine) [data-example]').length"), 0, 'examples only on Josh\'s card')
        self.assertEqual(pg.evaluate("()=>document.querySelectorAll('dialog.yh .yh-card.mine [data-example]').length"), 2)
        self.assertEqual(pg.evaluate("()=>getComputedStyle(document.querySelector('dialog.yh .yc-title')).fontWeight"), '600')
        self.assertEqual(pg.locator('dialog.yh .yh-note').count(), 0)
        said = pg.evaluate("()=>{const d=document.querySelector('dialog.yh');return d.textContent+[...d.querySelectorAll('[title]')].map(e=>e.title).join(' ')}").lower()
        for word in ('example', 'not real', 'mock'):
            self.assertNotIn(word, said, 'no commentary about the preview values')
        self.assertNotIn('Original creator', text)
        pg.click('dialog.yh [data-yh-rank="1"]')
        self.assertIn(self.a[1], pg.evaluate("()=>document.querySelector('dialog.yh .yh-card.mine img').getAttribute('src')"))
        pg.click('dialog.yh [data-yh-close]')
        # The dialog is removed in its close event, which the browser fires a moment after close().
        pg.wait_for_function("!document.querySelector('dialog.yh')", timeout=5000)
        self.assertEqual(pg.locator('dialog.yh').count(), 0)
        self.assertEqual(self.puts, [], 'the preview saves nothing')
        self.assertEqual(self.outside, [], 'nothing leaves the page')
        self.assertEqual(self.errors, [])


if __name__ == '__main__':
    unittest.main()
