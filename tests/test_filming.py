"""Ready to film (studio/filming.py). No OBS, Camera Hub or Stream Deck: fakes stand in for each, and nothing records."""
import hashlib
import json
import sys
import tempfile
import threading
import unittest
import uuid
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(r'E:\Videos\DaJoshiResolve')
sys.path.insert(0, str(ROOT))
from studio import filming as F  # noqa: E402
from studio.store import Conflict, Store  # noqa: E402

SCRIPT = """# Promotion isn't luck · YouTube script v01
Oct 28, 2026 · @Josh

## Hook
Why does the promotion always go to someone else? [On screen: the question]

Hook them early. That's the rule I follow.

Mistake 1 · Apologising before you speak
You open with sorry. (pause) Stop doing that.
B-roll: office shots

Section two
Step 1 is simple. Say what you did.
[revealing the 4th thing]

Close
See you in the next one.
"""
SPOKEN = ("Why does the promotion always go to someone else? Hook them early. That's the rule I follow. You open with sorry. "
          "Stop doing that. Step 1 is simple. Say what you did. See you in the next one.")


class Prompter(unittest.TestCase):
    def test_headings_and_directions_go_and_every_spoken_word_stays(self):
        out = F.teleprompter(SCRIPT)
        self.assertEqual(F.words(out['text']), F.words(SPOKEN))
        for gone in ('## Hook', 'Mistake 1 · Apologising before you speak', 'Section two', 'Close', '[On screen: the question]', '(pause)',
                     'B-roll: office shots', 'Oct 28, 2026 · @Josh', '[revealing the 4th thing]'):
            self.assertIn(gone, out['removed'])
        self.assertNotIn('[', out['text'])
        self.assertEqual(len(out['chapters']), 5)

    def test_spoken_lines_that_look_like_headings_are_kept(self):
        for line in ('Hook them early.', 'Step 1 is simple.', 'Close the laptop, then breathe.', 'Part of the job is saying no.'):
            self.assertEqual(F.words(F.teleprompter(line)['text']), F.words(line), line)

    def test_a_script_with_no_spoken_words_is_refused(self):
        with self.assertRaises(ValueError):
            F.teleprompter('## Hook\n[On screen]\n')


class FakeOBS:
    def __init__(self, profile='YouTube', folder='E:/Videos/Input', busy=False, fail=None):
        self.profile, self.folder, self.busy, self.fail, self.calls = profile, folder, busy, fail, []

    def __call__(self):
        if self.fail:
            raise ConnectionError(self.fail)
        return self

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def idle(self):
        self.calls.append('idle')
        if self.busy:
            raise Conflict('OBS has an active or unknown output.')

    def call(self, kind, data=None):
        self.calls.append(kind)
        if kind == 'GetProfileList':
            return dict(currentProfileName=self.profile)
        if kind == 'GetRecordDirectory':
            return dict(recordDirectory=self.folder)
        if kind == 'SetRecordDirectory':
            self.folder = data['recordDirectory']
            return {}
        raise AssertionError('unexpected OBS call ' + kind)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = Path(self.tmp.name)
        self.store = Store(t / 'Data')
        self.video_dir = t / 'videos' / 'promotion'
        self.video_dir.mkdir(parents=True)
        self.texts = t / 'Texts'
        self.texts.mkdir()
        (self.texts / 'AAAA.json').write_text(json.dumps(dict(GUID='AAAA', chapters=['x'], friendlyName='career', index=4)), encoding='utf-8')
        self.app_settings = t / 'AppSettings.json'
        self.library = ['AAAA']
        self.clipped = []
        self.added = []
        self.adder_mode = 'works'
        self.camera_hub_saves(SPOKEN, 'GOOD')   # Josh already pasted and saved this version in Camera Hub
        self.ready, self.msg = t / 'last-ready.json', t / 'last-message.txt'
        self.p = self.store.create("Promotion Isn't Luck")
        self.store.create('Four Signs You Sound Junior')
        self.store.create('Five Signs You Sound Senior')
        self.set_script(SCRIPT, approve=True)
        self.obs = FakeOBS()
        self.launches, self.started, self.prompts = [], [], []
        self.launch_writes = 'ready'
        self.prompter_answer = None
        self.busy = ''
        self.f = self.make()

    def tearDown(self):
        self.tmp.cleanup()

    def set_script(self, text, approve):
        p = self.store.get(self.p['id'])
        self.store.save(p['id'], dict(script=text), p['revision'])
        with self.store.connect() as c:
            c.execute('CREATE TABLE IF NOT EXISTS script_versions(id TEXT PRIMARY KEY,project_id TEXT NOT NULL,project_revision INTEGER NOT NULL,body TEXT NOT NULL)')
            c.execute('DELETE FROM script_versions WHERE project_id=?', (p['id'],))
            vid = str(uuid.uuid4())
            body = dict(id=vid, project_id=p['id'], content=text, sha256=hashlib.sha256(text.encode('utf-8')).hexdigest(),
                        approval='approved' if approve else 'draft')
            c.execute('INSERT INTO script_versions VALUES(?,?,?,?)', (vid, p['id'], 1, json.dumps(body)))

    def camera_hub_saves(self, text, guid, chapters=None):
        """What Camera Hub itself writes when Josh presses +, pastes and saves in Prompter > Scripts."""
        (self.texts / f'{guid}.json').write_text(json.dumps(dict(GUID=guid, chapters=chapters or [text], friendlyName='Promotion', index=9),
                                                            ensure_ascii=False), encoding='utf-8')
        if guid not in self.library:
            self.library.append(guid)
        self.app_settings.write_text(json.dumps({'applogic.prompter.libraryList': self.library, 'other': {'keep': True}}), encoding='utf-8')

    def adder(self, name, text):
        """Stands in for Camera Hub's own +, name, Add and paste, then its own save a minute later."""
        self.added.append((name, text))
        if self.adder_mode == 'fails':
            return dict(ok=False, error='Camera Hub is not open.')
        if self.adder_mode == 'not_saved':
            return dict(ok=True)
        saved = text if self.adder_mode == 'works' else text.replace('Stop doing that.', 'Keep doing that.')
        g = f'NEW{len(self.added)}'
        (self.texts / f'{g}.json').write_text(json.dumps(dict(GUID=g, chapters=[''] + saved.split('\n\n'), friendlyName=name + '\x00')), encoding='utf-8')
        self.library.insert(0, g)
        self.app_settings.write_text(json.dumps({'applogic.prompter.libraryList': self.library}), encoding='utf-8')
        return dict(ok=True)

    def launcher(self):
        self.launches.append(1)
        if self.launch_writes == 'ready':
            self.ready.write_text(json.dumps(dict(at=datetime.now(timezone.utc).isoformat(), mode='youtube', recording=False, streaming=False,
                                                  profile='YouTube', scene='YouTube - Cam Link Full Screen')), encoding='utf-8')
        elif self.launch_writes == 'error':
            self.msg.write_text('OBS launched but its existing control connection is not ready.', encoding='utf-8')

    def prompter(self, args):
        self.prompts.append(args)
        if self.prompter_answer is not None:
            return self.prompter_answer
        return dict(ok=True, selected=args[1], mode=1, chapters=int(args[2]))

    def make(self):
        pieces = SimpleNamespace(start=lambda pid: self.started.append(pid))
        folders = SimpleNamespace(path=lambda pid: self.video_dir if pid == self.p['id'] else None)
        recording = SimpleNamespace(no_lesson=lambda c: None)
        f = F.Filming(self.store, Path(self.tmp.name) / 'Data', folders, pieces, recording, busy=lambda: self.busy, launcher=self.launcher,
                      obs=self.obs, prompter=self.prompter, texts=lambda: self.texts, ready_file=self.ready, message_file=self.msg,
                      device='VID 0x17E9 / PID 0xFF1A / Serial test', app_settings=lambda: self.app_settings, clip=self.clipped.append, adder=self.adder)
        f._sleep = lambda s: None
        f.save_wait_s = 0
        return f


class Prepare(Base):
    def test_everything_ready_and_nothing_recorded(self):
        r = self.f.prepare(pid=self.p['id'])
        self.assertEqual(r['state'], 'ready', r)
        self.assertTrue(all(s['state'] == 'done' for s in r['steps']))
        dest = self.video_dir / 'Recorded' / 'Input'
        self.assertEqual(self.obs.folder, str(dest).replace('\\', '/'))
        self.assertEqual(set(self.obs.calls) - {'idle', 'GetProfileList', 'GetRecordDirectory', 'SetRecordDirectory'}, set())
        self.assertEqual(self.started, [self.p['id']])
        self.assertEqual(len(self.launches), 1)
        self.assertEqual(self.prompts[0][:3], ['select', 'GOOD', '1'])
        self.assertEqual(r['camera_hub_text'], 'GOOD')
        self.assertIn("Recording hasn't started", r['message'])
        self.assertTrue((self.video_dir / 'script' / 'teleprompter.txt').is_file())
        self.assertNotIn('StartRecord', F.film_obs.__code__.co_consts.__repr__())

    def test_no_approved_script(self):
        self.set_script(SCRIPT, approve=False)
        r = self.f.prepare(pid=self.p['id'])
        self.assertEqual((r['state'], self.launches, self.obs.calls), ('failed', [], []))
        self.assertIn('no approved script', r['message'])

    def test_script_changed_after_approval(self):
        self.set_script(SCRIPT, approve=True)
        p = self.store.get(self.p['id'])
        self.store.save(p['id'], dict(script=SCRIPT + '\nOne more line.'), p['revision'])
        r = self.f.prepare(pid=self.p['id'])
        self.assertEqual(r['state'], 'failed')
        self.assertIn('changed after it was approved', r['message'])
        self.assertEqual(self.launches, [])

    def test_ambiguous_and_unknown_videos(self):
        r = self.f.prepare(query='signs you sound')
        self.assertEqual(r['state'], 'ambiguous')
        self.assertIn('Four Signs', r['message'])
        self.assertIn('Five Signs', r['message'])
        self.assertEqual(self.f.prepare(query='cooking with Biscuit')['state'], 'not_found')
        self.assertEqual(self.f.resolve(query='promotion isnt luck')['id'], self.p['id'])
        self.assertEqual(self.launches, [])

    def test_recording_or_rendering_blocks_it(self):
        self.busy = 'OBS is open and might be recording'
        r = self.f.prepare(pid=self.p['id'])
        self.assertEqual(r['state'], 'failed')
        self.assertEqual((self.launches, self.started, self.obs.calls), ([], [], []))

    def test_obs_unavailable(self):
        self.launch_writes = 'error'
        r = self.f.prepare(pid=self.p['id'])
        self.assertEqual(r['state'], 'partial')
        self.assertIn('control connection', r['message'])
        self.assertEqual(self.obs.calls, [])
        self.assertEqual(next(s for s in r['steps'] if s['key'] == 'prompter')['state'], 'skipped')

    def test_obs_on_the_wrong_profile_or_busy_is_left_alone(self):
        for obs in (FakeOBS(profile='Lessons'), FakeOBS(busy=True), FakeOBS(fail='refused')):
            self.obs.__dict__.update(obs.__dict__)
            r = self.f.prepare(pid=self.p['id'], force=True)
            self.assertEqual(r['state'], 'partial')
            self.assertNotIn('SetRecordDirectory', self.obs.calls)
            self.assertIn('Set the recording path in OBS', r['manual'])

    def test_camera_hub_unavailable(self):
        self.prompter_answer = dict(ok=False, error='Camera Hub is not open or its control connection is not ready.')
        r = self.f.prepare(pid=self.p['id'])
        self.assertEqual(r['state'], 'partial')
        self.assertIn('Camera Hub is not open', r['message'])
        self.assertIn('OBS is ready', r['manual'])
        self.assertEqual(next(s for s in r['steps'] if s['key'] == 'obs_folder')['state'], 'done')

    def test_wrong_prompter_device_config(self):
        self.f.device = ''
        self.assertEqual(self.f.prepare(pid=self.p['id'])['state'], 'partial')

    def test_duplicate_clicks(self):
        gate, out = threading.Event(), []
        real = self.launcher

        def slow():
            gate.wait(5)
            real()
        self.f.launcher = slow
        t = threading.Thread(target=lambda: out.append(self.f.prepare(pid=self.p['id'])))
        t.start()
        for _ in range(100):
            if self.f.lock.locked():
                break
            threading.Event().wait(0.01)
        second = self.f.prepare(pid=self.p['id'])
        gate.set()
        t.join(5)
        self.assertEqual(second['state'], 'busy')
        self.assertEqual(out[0]['state'], 'ready')
        again = self.f.prepare(pid=self.p['id'])
        self.assertTrue(again.get('reused'))
        self.assertTrue(self.f.prepare(pid=self.p['id']).get('reused'))
        self.assertEqual(self.f.latest(self.p['id'])['state'], 'ready')
        self.assertEqual(len(self.launches), 1)
        self.f.prepare(pid=self.p['id'], force=True)
        self.assertEqual(len(self.launches), 2)

    def test_restore_after_a_take_is_locked(self):
        self.f.prepare(pid=self.p['id'])
        self.f.restore_default()
        self.assertEqual(self.obs.folder, 'E:/Videos/Input')


class TakeFolders(unittest.TestCase):
    def test_lock_take_accepts_the_videos_own_folder(self):
        from studio.youtube_recording import Recording
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        t = Path(tmp.name)
        r = Recording(Store(t / 'Data'), t, input_dir=str(t / 'Input'))
        (t / 'proj' / 'Recorded' / 'Input').mkdir(parents=True)
        folders = r.take_folders(dict(destination=str(t / 'proj' / 'Recorded' / 'Input')))
        self.assertEqual(folders, [(t / 'Input').resolve(), (t / 'proj' / 'Recorded' / 'Input').resolve()])
        self.assertEqual(r.take_folders({}), [(t / 'Input').resolve()])


class SamChat(Base):
    def chat(self, message, answer):
        from studio.agent import Agents
        from studio.team import Team
        agents = Agents(self.store, Path(self.tmp.name), Team(self.store))
        agents.filming = self.f
        with patch.object(Agents, 'run', return_value=('On it.', dict(reply='On it.', prepare_filming=answer))):
            return agents.chat(dict(agent_id='sam', message=message, via='discord'), [])['reply']['text']

    def test_sam_uses_the_same_preparation(self):
        text = self.chat("I'm ready to record promotion isn't luck", dict(video_name="promotion isn't luck"))
        self.assertIn('ready to film', text)
        self.assertEqual(len(self.launches), 1)
        self.assertEqual(self.f.latest(self.p['id'])['via'], 'discord')

    def test_sam_asks_when_its_unclear_and_never_sets_up_unasked(self):
        self.assertIn('Which one', self.chat('ready to film the signs one', dict(video_name='signs you sound')))
        self.assertIn("didn't set anything up", self.chat('what should the title be?', dict(video_name="promotion isn't luck")))
        self.assertEqual(self.launches, [])


if __name__ == '__main__':
    unittest.main()


class AddToCameraHub(Base):
    def setUp(self):
        super().setUp()
        self.library = ['AAAA']
        (self.texts / 'GOOD.json').unlink()
        self.app_settings.write_text(json.dumps({'applogic.prompter.libraryList': self.library}), encoding='utf-8')
        self.before = {f.name: f.read_bytes() for f in self.texts.iterdir()}

    def test_film_adds_the_script_the_native_way_and_checks_every_word(self):
        r = self.f.prepare(pid=self.p['id'])
        self.assertEqual(r['state'], 'ready', r)
        name, text = self.added[0]
        self.assertTrue(name.startswith("Promotion Isn't Luck · Josh OS "))
        self.assertEqual(F.words(text), F.words(SPOKEN))
        self.assertEqual(self.prompts[-1][:3], ['select', 'NEW1', str(1 + len(text.split('\n\n')))])
        for f, b in self.before.items():
            self.assertEqual((self.texts / f).read_bytes(), b)   # every other script left exactly as it was

    def test_the_same_version_is_never_added_twice(self):
        self.f.prepare(pid=self.p['id'])
        self.f.prepare(pid=self.p['id'], force=True)
        self.assertEqual(len(self.added), 1)
        self.assertEqual(self.prompts[-1][1], 'NEW1')

    def test_a_save_with_different_words_is_never_used(self):
        self.adder_mode = 'wrong_words'
        r = self.f.prepare(pid=self.p['id'])
        self.assertEqual(r['state'], 'partial')
        self.assertIn("don't match", r['message'])
        self.assertEqual(self.prompts, [])

    def test_camera_hub_never_saving_it_stops_safely(self):
        self.adder_mode = 'not_saved'
        r = self.f.prepare(pid=self.p['id'])
        self.assertEqual(r['state'], 'partial')
        self.assertIn("didn't add the new script", r['message'])

    def test_if_camera_hub_cant_be_driven_josh_gets_the_paste_steps(self):
        self.adder_mode = 'fails'
        r = self.f.prepare(pid=self.p['id'])
        self.assertEqual(r['state'], 'needs_you')
        self.assertEqual(F.words(self.clipped[0]), F.words(SPOKEN))
        self.assertIn('press +', r['manual'])
        self.adder_mode = 'works'
        self.assertEqual(self.f.check_prompter(self.p['id'])['state'], 'ready')
        self.assertEqual(len(self.launches), 1)

    def test_an_older_version_with_the_same_number_of_parts_is_not_reused(self):
        self.camera_hub_saves(SPOKEN.replace('Stop doing that.', 'Keep doing that.'), 'OLD')
        r = self.f.prepare(pid=self.p['id'])
        self.assertEqual(r['state'], 'ready')
        self.assertNotEqual(self.prompts[-1][1], 'OLD')

    def test_a_script_outside_the_library_never_counts(self):
        (self.texts / 'ORPHAN.json').write_text(json.dumps(dict(GUID='ORPHAN', chapters=[SPOKEN])), encoding='utf-8')
        self.f.prepare(pid=self.p['id'])
        self.assertNotEqual(self.prompts[-1][1], 'ORPHAN')

    def test_two_prompters_or_unreadable_settings_stop_safely(self):
        self.camera_hub_saves(SPOKEN, 'PASTED')
        self.prompter_answer = dict(ok=False, error='2 Prompters are connected.')
        r = self.f.prepare(pid=self.p['id'])
        self.assertEqual(r['state'], 'partial')
        self.assertIn('2 Prompters', r['message'])
        self.prompter_answer = None
        self.app_settings.write_text('{not json', encoding='utf-8')
        r = self.f.check_prompter(self.p['id'])
        self.assertEqual(r['state'], 'partial')
        self.assertIn("settings couldn't be read", r['message'])
