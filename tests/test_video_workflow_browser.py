"""The simplified video workflow (16 Nov 2026).

Josh's brief: the header shows the confirmed title (or the idea's name when none is confirmed) and the planned publish
date, with no stage menu or save note. Packaging ends in one slim publish row instead of the big readiness checklist,
with Open Publishing still closed until the release check has nothing open. Recording is the first stage of Editing
with every recording action kept, and Export is its last stage (final export approval, chapters, Frame.io). Video data
shows only what is saved for the video, and says so when nothing is.

Projects go through the real studio.store.Store on a disposable folder, so titles, dates, locks and stage are saved and
checked the way the app does. The recording, release, Frame.io, edit and results endpoints are stubs that record every
call, so OBS, YouTube, Frame.io and the live data are never touched. The real web files are served through Playwright
routing. Nothing reaches the live app.
"""
import io
import json
import mimetypes
import tempfile
import unittest
import uuid
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urlparse

from PIL import Image
from playwright.sync_api import sync_playwright

from studio.store import Conflict, Store

WEB = Path(__file__).resolve().parents[1] / 'studio' / 'web'
HOST = 'http://127.0.0.1:59984'
STAGES = ['Idea', 'Scripting', 'Ready to record', 'Recording', 'Recorded', 'Editing', 'Review', 'Scheduled', 'Published']


def png(colour):
    b = io.BytesIO()
    Image.new('RGB', (32, 18), colour).save(b, 'PNG')
    return b.getvalue()


def release(done=False, export_candidate=r'E:\test\exports\final_v1.mp4'):
    items = [dict(key='export', label='Final export', state='done' if done else 'todo', detail='final_v1.mp4 is rendered.',
                  next='' if done else 'Watch it and approve it.', action='' if done else 'approve_export'),
             dict(key='chapters', label='Chapters', state='done' if done else 'todo', detail='Not made yet.', next='', action='' if done else 'preview_chapters'),
             dict(key='title', label='Title', state='done' if done else 'todo', detail='', next=''),
             dict(key='thumbnail', label='Thumbnail', state='done' if done else 'todo', detail='', next=''),
             dict(key='description', label='Description', state='done' if done else 'todo', detail='', next='', action='' if done else 'draft_packaging'),
             dict(key='cta', label='CTA', state='done' if done else 'todo', detail='', next=''),
             dict(key='date', label='Publish date and time', state='done' if done else 'todo', detail='', next=''),
             dict(key='upload', label='Upload', state='todo', detail='', next='')]
    return dict(items=items, done=sum(i['state'] == 'done' for i in items), total=len(items),
                export_candidate=None if done else export_candidate, description='', cta='', links=[], lead_magnet={})


class VideoWorkflow(unittest.TestCase):
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
        self.shot = self.store.import_image('test_round.png', png((10, 120, 200)))['asset']
        self.graph = self.store.import_image('retention.png', png((200, 120, 10)))['asset']
        t_idea, t_conf = str(uuid.uuid4()), str(uuid.uuid4())
        self.confirmed = str(uuid.uuid4())
        self.store.create('Working name of the idea', pid=self.confirmed)
        self.store.save(self.confirmed, dict(titles=[dict(id=t_idea, text='An idea title'), dict(id=t_conf, text='The confirmed title')],
                                             chosen_title=t_conf, locks=dict(title='2026-11-10T10:00:00'), stage='Recording'), 1)
        self.starred = str(uuid.uuid4())  # a starred title that isn't confirmed
        self.store.create('Starred but not confirmed idea', pid=self.starred)
        self.store.save(self.starred, dict(titles=[dict(id=t_idea, text='Only starred')], chosen_title=t_idea), 1)
        self.calls, self.outside = [], []
        self.rel = {self.confirmed: release(), self.starred: release()}
        self.results = {}
        self.status = {self.confirmed: dict(folder=r'E:\test\videos\confirmed', recording=None, active=False), self.starred: dict(recording=None, active=False)}

        def js(route, body, status=200):
            return route.fulfill(status=status, content_type='application/json', body=json.dumps(body))

        def handle(route):
            req = route.request
            path = urlparse(req.url).path
            if not req.url.startswith(HOST):
                self.outside.append(req.url)
                return route.abort()
            if path == '/':
                return route.fulfill(status=200, content_type='text/html', body=(WEB / 'index.html').read_text(encoding='utf-8'))
            parts = path.strip('/').split('/')
            body = json.loads(req.post_data) if req.post_data else None
            if parts[:2] == ['api', 'projects'] and len(parts) >= 3:
                pid, rest = parts[2], '/'.join(parts[3:])
                if req.method != 'GET':
                    self.calls.append((req.method, pid, rest, body))
                if rest == '' and req.method == 'PUT':
                    try:
                        return js(route, self.store.save(pid, body['changes'], body['revision']))
                    except Conflict as e:
                        return js(route, dict(error=str(e)), 409)
                if rest == 'pieces':
                    return js(route, self.status.get(pid, {}))
                if rest == 'film' and req.method == 'GET':
                    return js(route, {})
                if rest == 'film':
                    return js(route, dict(state='needs_you', message='Check the teleprompter', prompter_text='Hello script',
                                          steps=[dict(key='obs_folder', label='OBS saves into the video folder', state='done'),
                                                 dict(key='prompter', label='Script on the teleprompter', state='waiting')]))
                if rest == 'film/prompter':
                    return js(route, dict(state='ready', message='Ready to film', steps=[dict(key='obs_folder', label='OBS', state='done')]))
                if rest == 'recording/start':
                    self.status[pid] = dict(self.status.get(pid, {}), active=True)
                    return js(route, self.status[pid])
                if rest == 'recording/takes':
                    return js(route, [dict(path=r'E:\Videos\Input\take_2.mkv', name='take_2.mkv', size_mb=812, modified='2026-11-16T10:30:00', settling=False, also=[]),
                                      dict(path=r'E:\Videos\Input\take_1.mkv', name='take_1.mkv', size_mb=640, modified='2026-11-16T10:00:00', settling=False, also=[])])
                if rest == 'recording/done':
                    saved_path = r'E:\test\videos\confirmed\Recorded\Input\Working name of the idea.mkv'
                    p = self.store.get(pid)
                    self.store.save(pid, dict(media=dict(video=saved_path), stage='Recorded',
                                              locks=dict(p.get('locks') or {}, recording='2026-11-18T10:00:00')), p['revision'])
                    self.status[pid] = dict(self.status.get(pid, {}), active=False, recording=dict(name='Working name of the idea.mkv', path=saved_path))
                    return js(route, dict(status=self.status[pid]))
                if rest == 'release' and req.method == 'GET':
                    return js(route, self.rel[pid])
                if rest.startswith('release/'):
                    return js(route, dict(release=self.rel[pid]))
                if rest == 'frameio':
                    return js(route, dict(connection=dict(state='not_connected', detail='Frame.io is not connected.')))
                return js(route, {})
            if parts[:2] == ['api', 'results'] and len(parts) == 3:
                r = self.results.get(parts[2])
                if r == 'fail':
                    return js(route, dict(error='Test: the results could not be read.'), 500)
                return js(route, r or dict(project_id=parts[2], revision=0, funnel='', metrics={}, tests=[], retention={}, review='', lessons=[], transcript=''))
            if path == '/api/state':
                return js(route, dict(projects=self.store.projects(), jobs=[], stages=STAGES, version='test'))
            if path == '/api/team':
                return js(route, dict(agents=[], rooms=[]))
            if path in ('/api/carousels', '/api/people'):
                return js(route, dict(items=[]))
            if path.startswith('/api/'):
                return route.fulfill(status=200, content_type='application/json', body='[]' if path.endswith('s') else '{}')
            if path.startswith('/assets/'):
                try:
                    f, mime = self.store.asset(parts[1])
                    return route.fulfill(path=str(f), content_type=mime)
                except ValueError:
                    return route.fulfill(status=404, body='')
            f = WEB / path.lstrip('/')
            if not f.is_file():
                return route.fulfill(status=404, body='')
            return route.fulfill(path=str(f), content_type=mimetypes.guess_type(str(f))[0] or 'text/plain')

        self.ctx = self.browser.new_context(viewport={'width': 1366, 'height': 900})
        self.addCleanup(self.ctx.close)
        self.ctx.route('**/*', handle)
        self.page = self.ctx.new_page()
        self.page.on('dialog', lambda d: d.accept())
        self.errors = []
        mine = ('video-page.js', 'app.js', 'autosave.js', 'results.js', 'studio.js')
        self.page.on('pageerror', lambda e: self.errors.append(str(e)) if any(m in (e.stack or '') for m in mine) else None)
        self.page.goto(HOST + '/')
        self.page.wait_for_function("typeof vpPublishRow==='function'&&state.projects.length===2", timeout=20000)

    def open(self, pid, piece=None, edit_tab=None):
        self.page.evaluate("([id,k,t])=>{openProject(id);if(k){vpOpen[id]=k;if(t)vpEditTab[id]=t;renderDetail()}}", [pid, piece, edit_tab])
        self.page.wait_for_selector('.vp-pieces', timeout=10000)
        self.page.wait_for_timeout(600)

    def click(self, selector, wait=600):
        self.page.click(selector)
        self.page.wait_for_timeout(wait)

    def posts(self, rest):
        return [c for c in self.calls if c[2] == rest]

    def test_header_shows_the_confirmed_title_and_no_status_pill(self):
        self.open(self.confirmed)
        pg = self.page
        self.assertEqual(pg.locator('.vp-top .vp-heading').inner_text(), 'The confirmed title')
        self.assertEqual(pg.locator('.vp-top .name').count(), 0)
        self.assertEqual(pg.locator('.vp-top select[data-field=stage], #save-note, .vp-stage').count(), 0, 'no stage pill or save note')
        self.assertIn('Choose a publish date', pg.locator('.vp-when').inner_text())
        self.assertTrue(pg.locator('.vp-when').evaluate("e=>e.classList.contains('empty')"))
        self.assertEqual(self.errors, [])

    def test_header_falls_back_to_the_idea_name_when_no_title_is_confirmed(self):
        self.open(self.starred)
        pg = self.page
        self.assertEqual(pg.locator('.vp-top .vp-heading').count(), 0, 'a starred title that is not locked in is not confirmed')
        self.assertEqual(pg.locator('.vp-top textarea.vp-name').input_value(), 'Starred but not confirmed idea')
        self.assertEqual(self.errors, [])

    def test_the_publish_date_is_saved_with_the_project(self):
        self.open(self.confirmed)
        day = (date.today() + timedelta(days=9)).isoformat()
        self.page.fill('.vp-when input[type=date]', day)
        self.page.dispatch_event('.vp-when input[type=date]', 'change')
        self.page.wait_for_function("dirty===false", timeout=10000)
        self.page.wait_for_timeout(300)
        self.assertEqual(self.store.get(self.confirmed)['publish_date'], day)
        self.assertIn('Publishes', self.page.locator('.vp-when').inner_text())
        self.assertFalse(self.page.locator('.vp-when').evaluate("e=>e.classList.contains('empty')"))
        self.assertEqual(self.errors, [])

    def test_navigation_and_old_links(self):
        self.open(self.confirmed)
        pg = self.page
        self.assertEqual(pg.locator('.vp-main-nav .vp-nav').all_inner_texts(), ['Packaging', 'Script', 'Editing', 'Video data'])
        self.assertEqual(pg.locator('.vp-more-nav .vp-more').all_inner_texts(), ['Notes'])
        self.assertEqual(pg.locator('[data-vp-piece="recording"],[data-vp-piece="publish"]').count(), 0)
        pg.evaluate(f"()=>{{vpOpen['{self.confirmed}']='recording';renderDetail()}}")  # an old saved link
        pg.wait_for_timeout(400)
        self.assertEqual(pg.evaluate('vpPiece(current)'), 'edit')
        self.assertIn('on', pg.locator('[data-vp-edit-tab="recording"]').get_attribute('class'))
        pg.evaluate(f"()=>{{vpOpen['{self.confirmed}']='publish';renderDetail()}}")
        pg.wait_for_timeout(400)
        self.assertEqual(pg.evaluate('vpPiece(current)'), 'data')
        self.assertEqual(self.errors, [])

    def test_every_recording_action_from_editing_stage_one(self):
        self.open(self.confirmed, 'edit')
        pg = self.page
        self.assertEqual([' '.join(t.split()) for t in pg.locator('.vp-etab').all_inner_texts()], ['1 Recording', '2 Setup', '3 Clean Cut', '4 Animations', '5 Export'])
        self.assertIn('on', pg.locator('[data-vp-edit-tab="recording"]').get_attribute('class'), 'not recorded, so it opens on Recording')
        self.click('[data-vp-film]', 900)
        self.assertEqual(len(self.posts('film')), 1)
        self.assertEqual(self.posts('film')[0][3], dict(force=False))
        self.click('[data-vp-film-check]', 900)
        self.assertEqual(len(self.posts('film/prompter')), 1)
        self.click('[data-vp-rec-start]', 900)
        self.assertEqual(len(self.posts('recording/start')), 1)
        self.click('[data-vp-rec-find]', 900)
        self.assertEqual(pg.locator('input[name=vp-take]').count(), 2)
        pg.check('input[name=vp-take][value*="take_1"]')
        self.click('[data-vp-rec-move]', 1200)
        done = self.posts('recording/done')
        self.assertEqual(len(done), 1)
        self.assertEqual(done[0][3], dict(path=r'E:\Videos\Input\take_1.mkv'))
        self.assertTrue(all(c[1] == self.confirmed for c in self.calls), 'every call is for this video')
        self.assertIn('Recorded', pg.locator('.vp-epane').inner_text())
        self.assertIn('done', pg.locator('[data-vp-edit-tab="recording"]').get_attribute('class'))
        self.assertIn('on', pg.locator('[data-vp-edit-tab="setup"]').get_attribute('class'))
        self.assertEqual(pg.locator('#vp-setup-camera').input_value(), self.store.get(self.confirmed)['media']['video'])
        self.assertEqual(self.errors, [])

    def test_uncommitted_file_does_not_hide_completion_or_require_teleprompter(self):
        # Also tolerate an older running server's folder-discovery status until restart.
        self.status[self.confirmed].update(active=True, recording=dict(name='unbound.mp4', path=r'E:\test\unbound.mp4'))
        self.open(self.confirmed, 'edit', 'recording')
        pg = self.page
        pg.evaluate("""id=>{vpFilm[id]={state:'needs_you',message:"Recording hasn't started.",
          steps:[{key:'prompter',label:'Teleprompter',state:'needs_you',detail:'Old Camera Hub warning'}]};renderDetail()}""", self.confirmed)
        self.assertEqual(pg.locator('[data-vp-rec-find]').count(), 1)
        self.assertNotIn('done', pg.locator('[data-vp-edit-tab="recording"]').get_attribute('class'))
        self.assertIn('This setup check did not start recording.', pg.locator('.vp-film').inner_text())
        self.click('[data-vp-rec-find]')
        self.assertEqual(pg.locator('input[name=vp-take]:checked').count(), 0)
        self.assertIn("this video's Recorded/Input", pg.locator('.vp-takes').inner_text())
        pg.check('input[name=vp-take][value*="take_1"]')
        # Two queued clicks must produce one completion request.
        pg.evaluate("()=>{const b=document.querySelector('[data-vp-rec-move]');b.dispatchEvent(new MouseEvent('click',{bubbles:true}));b.dispatchEvent(new MouseEvent('click',{bubbles:true}))}")
        pg.wait_for_timeout(1200)
        self.assertEqual(len(self.posts('recording/done')), 1)
        self.assertIn('Recorded', pg.locator('.vp-epane').inner_text())
        self.assertEqual(self.errors, [])

    def setup_edit_fixture(self, picker_path=r'E:\Audio\good microphone.wav', source_error=False):
        camera = r'E:\test\videos\confirmed\Recorded\Input\saved recording.mp4'
        p = self.store.get(self.confirmed)
        self.store.save(p['id'], dict(stage='Recorded', media=dict(video=camera),
                                     locks=dict(p['locks'], recording='2026-11-18T10:00:00')), p['revision'])
        self.status[p['id']] = dict(active=False, recording=dict(path=camera, name='saved recording.mp4'))
        self.edit_requests = []
        old_message = 'Pick the camera and the good mic on the Script tab, then ask me to start again.'
        data = dict(processor='autocut-v1', proposal=None, setup=dict(state='needs_you', steps=[], message=old_message), phases={}, status=[], sources=None,
                    suggest=dict(videos=[camera, r'E:\test\unrelated.mp4'], audio=[], ambiguous=True))
        def route_edit(route):
            request = route.request
            action = urlparse(request.url).path.split('/edit-flow')[-1]
            body = json.loads(request.post_data) if request.post_data else None
            if request.method == 'POST':
                self.edit_requests.append((action, body))
            if action == '/sources':
                if source_error:
                    return route.fulfill(status=400, content_type='application/json', body=json.dumps(dict(error='Mic file could not be read')))
                data['sources'] = dict(id='chosen-pair', payload=dict(files=dict(camera=dict(path=body['camera']), mic=dict(path=body['mic']))))
                result = data['sources']
            elif action == '/go':
                data['setup'] = dict(state='running', steps=[], message='Preparing')
                data['phases'] = dict(setup=data['setup'])
                result = dict(state='started')
            else:
                result = data
            route.fulfill(status=200, content_type='application/json', body=json.dumps(result))
        def route_picker(route):
            self.edit_requests.append(('picker', json.loads(route.request.post_data)))
            route.fulfill(status=200, content_type='application/json', body=json.dumps(dict(path=picker_path)))
        self.ctx.route('**/edit-flow**', route_edit)
        self.ctx.route('**/api/pick', route_picker)
        self.page.evaluate('async()=>{await refresh()}')
        self.open(self.confirmed, 'edit', 'setup')
        return camera, data

    def test_setup_defaults_saved_recording_picks_mic_and_registers_before_start(self):
        camera, data = self.setup_edit_fixture()
        pg = self.page
        self.assertEqual(pg.locator('#vp-setup-camera').input_value(), camera)
        self.assertEqual(pg.locator('#vp-setup-mic').input_value(), '')
        self.assertNotIn('Script tab', pg.locator('.vp-epane').inner_text())
        self.click('[data-vp-setup-pick="mic"]')
        self.assertEqual(pg.locator('#vp-setup-mic').input_value(), r'E:\Audio\good microphone.wav')
        self.assertEqual(self.edit_requests, [('picker', dict(kind='audio'))])
        self.click('[data-vp-edit-tab="script"]')
        self.assertEqual(pg.locator('#vp-setup-mic, #ef-mic, [data-ef-sources]').count(), 0)
        self.assertIn('Clean cut review', pg.locator('.vp-epane').inner_text())
        self.click('[data-vp-edit-tab="setup"]')
        self.assertEqual(pg.locator('#vp-setup-mic').input_value(), r'E:\Audio\good microphone.wav')
        pg.evaluate("()=>{const b=document.querySelector('[data-vp-go]');b.dispatchEvent(new MouseEvent('click',{bubbles:true}));b.dispatchEvent(new MouseEvent('click',{bubbles:true}))}")
        pg.wait_for_timeout(1000)
        self.assertEqual(self.edit_requests, [('picker', dict(kind='audio')),
                         ('/sources', dict(camera=camera, mic=r'E:\Audio\good microphone.wav')), ('/go', {})])
        self.assertTrue(pg.locator('[data-vp-go]').is_disabled())
        self.assertEqual(self.store.get(self.confirmed)['media'], dict(video=camera))
        self.assertEqual(self.errors, [])

    def test_native_blocked_progress_does_not_claim_work_is_running(self):
        camera, data = self.setup_edit_fixture()
        data['setup'] = dict(state='blocked', message='Native transcription bridge needs to reload', steps=[
            dict(key='silences', label='AutoCut Silences', state='done', message='Complete'),
            dict(key='repeat', label='AutoCut Repeat', state='done', message='Complete'),
            dict(key='transcribe', label='Native microphone transcript', state='blocked', message='Native transcription bridge needs to reload')])
        self.page.evaluate('async()=>{await efLoad()}')
        pane = self.page.locator('.vp-epane')
        self.assertIn('Processing is blocked', pane.inner_text())
        self.assertIn('2 of 3 steps done', pane.inner_text())
        self.assertIn('Blocked', self.page.locator('.vp-step.blocked').inner_text())
        self.assertEqual(self.page.locator('.vp-step.running').count(), 0)
        self.assertEqual(self.edit_requests, [])

    def test_saved_recording_has_direct_setup_route_with_same_camera(self):
        camera, data = self.setup_edit_fixture()
        self.click('[data-vp-edit-tab="recording"]')
        self.assertIn('Recorded', self.page.locator('.vp-epane').inner_text())
        self.click('.vp-epane [data-vp-edit-tab="setup"]')
        self.assertEqual(self.page.locator('#vp-setup-camera').input_value(), camera)
        self.assertEqual(self.edit_requests, [])

    def test_old_backend_cannot_start_legacy_processing(self):
        camera, data = self.setup_edit_fixture()
        data.pop('processor')
        self.page.evaluate('async()=>{await efLoad()}')
        self.assertTrue(self.page.locator('[data-vp-go]').is_disabled())
        self.assertIn('reload the updated processing service', self.page.locator('#workspace').inner_text())
        self.assertEqual(self.edit_requests, [])

    def test_picker_cancel_preserves_manual_choice_and_no_processing_without_mic(self):
        self.setup_edit_fixture(picker_path='')
        pg = self.page
        self.click('[data-vp-go]')
        self.assertEqual(self.edit_requests, [])
        pg.fill('#vp-setup-mic', r'E:\Audio\keep this.wav')
        self.click('[data-vp-setup-pick="mic"]')
        self.assertEqual(pg.locator('#vp-setup-mic').input_value(), r'E:\Audio\keep this.wav')
        self.assertEqual(self.edit_requests, [('picker', dict(kind='audio'))])

    def test_source_registration_failure_never_starts_processing(self):
        self.setup_edit_fixture(source_error=True)
        self.page.fill('#vp-setup-mic', r'E:\Audio\bad.wav')
        self.click('[data-vp-go]')
        self.assertEqual([action for action, _ in self.edit_requests], ['/sources'])
        self.assertEqual(self.page.locator('#vp-setup-mic').input_value(), r'E:\Audio\bad.wav')
        self.assertFalse(self.page.locator('[data-vp-go]').is_disabled())

    def test_registered_pair_survives_refresh_and_does_not_register_again(self):
        camera, data = self.setup_edit_fixture()
        data['sources'] = dict(id='existing', payload=dict(files=dict(camera=dict(path=camera), mic=dict(path=r'E:\Audio\approved.wav'))))
        self.page.evaluate('async id=>{delete vpSetupDraft[id];await efLoad()}', self.confirmed)
        self.assertEqual(self.page.locator('#vp-setup-mic').input_value(), r'E:\Audio\approved.wav')
        self.click('[data-vp-go]')
        self.assertEqual(self.edit_requests, [('/go', {})])

    def test_packaging_has_no_bottom_checklist(self):
        self.open(self.confirmed)
        self.assertEqual(self.page.locator('.pk-still,.pk-pub').count(),0)
        self.assertEqual(self.page.locator('[data-vp-publish]').count(),1)
        self.assertNotIn('Sam drafts it',self.page.locator('.pk').inner_text())
        self.assertEqual(self.errors,[])

    def test_video_data_when_nothing_is_saved_and_when_it_cannot_be_read(self):
        future = (date.today() + timedelta(days=2)).isoformat()
        self.store.save(self.confirmed, dict(publish_date=future), self.store.get(self.confirmed)['revision'])
        self.page.reload()
        self.page.wait_for_function("typeof vpPublishRow==='function'&&state.projects.length===2", timeout=20000)
        self.open(self.confirmed, 'data')
        text = self.page.locator('.vd').inner_text()
        self.assertIn('No numbers saved for this video yet.', text)
        self.assertIn('No thumbnail test screenshots yet.', text)
        due = date.fromisoformat(future) + timedelta(days=5)
        self.assertIn('five days after publishing', text)
        self.assertIn(str(due.day), text)
        self.assertNotIn('As of', text, 'no date for numbers that do not exist')
        self.assertEqual(self.page.locator('[data-vp-lock]').count(), 0, 'Video data has no lock-in button')
        self.assertNotIn('undefined', self.page.locator('#workspace').inner_text())
        self.results[self.confirmed] = 'fail'
        self.page.evaluate("()=>{vpRes={}}")
        self.click('[data-vp-piece="data"]', 900)
        self.assertIn('could not be read', self.page.locator('.vd').inner_text())
        self.assertEqual(self.errors, [])

    def test_video_data_shows_only_what_is_saved(self):
        self.results[self.confirmed] = dict(project_id=self.confirmed, revision=3, funnel='top', review='A review', lessons=[], transcript='',
                                            metrics=dict(views=12345, ctr=5.2, avg_view_duration=185, as_of='2026-11-12'),
                                            tests=[dict(round=1, screenshot=self.shot, winner='B', variants=[]), dict(round=2, screenshot='', winner='', variants=[])],
                                            retention=dict(screenshot=self.graph, summary='Holds well after the hook.', dips=[], notes=''))
        self.open(self.confirmed, 'data')
        pg = self.page
        text = pg.locator('.vd').inner_text()
        self.assertRegex(text, r'As of (12 Nov 2026|Nov 12, 2026)')
        self.assertEqual(pg.locator('.vd-nums dt').all_inner_texts(), ['Views', 'Click-through rate', 'Average view'])
        self.assertEqual(pg.locator('.vd-nums dd').all_inner_texts(), ['12,345', '5.2%', '3:05'])
        self.assertEqual(pg.locator('.vd-shots figure').count(), 1, 'only rounds with a saved screenshot')
        self.assertIn('Round 1, B won', text)
        self.assertIn('Holds well after the hook.', text)
        for absent in ('Subscribers', 'Customers', 'Revenue', 'Impressions'):
            self.assertNotIn(absent, text, 'nothing that is not saved')
        self.click('[data-vp-piece="results"]', 1200)
        self.assertEqual(pg.evaluate('vpPiece(current)'), 'results')
        self.assertIn('Back to Video data', pg.locator('.vp-head').inner_text())
        self.assertEqual(self.outside, [])
        self.assertEqual(self.errors, [])


if __name__ == '__main__':
    unittest.main()
