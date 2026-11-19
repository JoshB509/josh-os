"""The video-change review page and its approval gate. No app server, OAuth or YouTube calls."""
import json
import threading
import unittest
from pathlib import Path

from studio.server import Handler


class FakeServer:
    token = 'session-token'
    server_port = 4242
    phone = None

    def __init__(self):
        self.publishing_nonces, self.publishing_nonce_lock = {}, threading.Lock()


class FakeHandler:
    """Just enough of a request for the gate methods: the session cookie and the browser's same-origin headers."""
    publishing_browser = Handler.publishing_browser
    update_review = Handler.update_review
    publishing_review = Handler.publishing_review
    publishing_consume = Handler.publishing_consume

    def __init__(self, server, browser=True):
        self.server = server
        self.headers = {'Cookie': 'dj_session=session-token'} if browser else {}
        if browser:
            self.headers.update({'Sec-Fetch-Site': 'same-origin', 'Origin': 'http://127.0.0.1:4242'})


class Gate(unittest.TestCase):
    def test_only_the_review_page_gets_a_code_and_it_works_once_for_that_version(self):
        srv = FakeServer()
        job = dict(id='a' * 32, digest='v1')
        self.assertNotIn('review_nonce', FakeHandler(srv, browser=False).update_review(job))       # a script or model gets no code
        code = FakeHandler(srv).update_review(job)['review_nonce']
        bad = dict(review_nonce=code, job_id='update:' + job['id'], packet_digest='v2')
        with self.assertRaises(ValueError):
            FakeHandler(srv).publishing_consume(bad)                                            # another version
        code = FakeHandler(srv).update_review(job)['review_nonce']
        ok = dict(review_nonce=code, job_id='update:' + job['id'], packet_digest='v1')
        with self.assertRaises(ValueError):
            FakeHandler(srv, browser=False).publishing_consume(ok)                              # not from the browser
        code = FakeHandler(srv).update_review(job)['review_nonce']
        ok['review_nonce'] = code
        FakeHandler(srv).publishing_consume(ok)
        with self.assertRaises(ValueError):
            FakeHandler(srv).publishing_consume(ok)                                             # used up

    def test_an_upload_review_code_cannot_approve_a_video_change(self):
        srv = FakeServer()
        upload = dict(id='b' * 32, packet=dict(packet_digest='v1'))
        code = FakeHandler(srv).publishing_review(upload)['review_nonce']
        with self.assertRaises(ValueError):
            FakeHandler(srv).publishing_consume(dict(review_nonce=code, job_id='update:' + upload['id'], packet_digest='v1'))


class Page(unittest.TestCase):
    def test_review_shows_before_and_after_and_approves_once(self):
        from playwright.sync_api import sync_playwright
        root = Path(__file__).resolve().parents[1] / 'studio' / 'web'
        posts = []
        job = dict(id='c' * 32, video_id='abcdefghijk', channel='UC3F6JjRjqDNrK_QzvyGwppA', digest='v1', state='needs_review',
                   review_nonce='code-1', before=dict(title='Old title', privacy='private'), changes=dict(title='New title'))

        def route(r):
            url = r.request.url.split('updates.test', 1)[1].split('?', 1)[0]
            if url == '/api/publishing/updates':
                value = dict(can_update=False, read_connected=True, expected_channel=job['channel'], updates=[job])
            elif url == '/api/publishing/updates/' + job['id']:
                value = job
            elif url.startswith('/api/publishing/updates/'):
                posts.append((url, r.request.post_data_json))
                job['state'] = 'approved_waiting'
                value = job
            else:
                name = 'publishing-updates.html' if url == '/publishing-updates' else url.lstrip('/')
                f = root / name
                if not f.is_file():
                    r.fulfill(status=404, body='')
                    return
                mime = 'text/html' if name.endswith('.html') else 'text/css' if name.endswith('.css') else 'text/javascript'
                r.fulfill(status=200, content_type=mime, body=f.read_text(encoding='utf-8-sig'))
                return
            r.fulfill(status=200, content_type='application/json', body=json.dumps(value))

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, channel='chrome')
            try:
                page = browser.new_page(viewport={'width': 1200, 'height': 900})
                page.route('**/*', route)
                page.goto('http://updates.test/publishing-updates#' + job['id'])
                page.locator('#job-state').filter(has_text='needs_review').wait_for()
                text = page.locator('#details').inner_text()
                self.assertIn('Now: Old title', text)
                self.assertIn('Becomes: New title', text)
                self.assertIn('can\'t change videos yet', page.locator('#connection').inner_text())
                self.assertTrue(page.locator('#approve').is_disabled())                          # needs the check box
                page.locator('#consent').check()
                page.locator('#approve').click()
                page.locator('#job-state').filter(has_text='approved_waiting').wait_for()
                self.assertEqual(posts[0], ('/api/publishing/updates/approve', dict(update_id=job['id'], digest='v1', review_nonce='code-1')))
                self.assertTrue(page.locator('#approve').is_disabled())
                self.assertTrue(page.locator('#retry').is_disabled())                            # no edit permission, so no API retry
                self.assertTrue(page.locator('#verify').is_enabled())                            # the Studio route is checked by reading
            finally:
                browser.close()


if __name__ == '__main__':
    unittest.main()
