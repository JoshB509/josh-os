"""Completion and recovery against disposable SQLite databases and real temporary files."""
import io
import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from studio.store import Store, Conflict
from studio.youtube_recording import Recording
from studio.video_pieces import VideoPieces
from studio.lesson_audio import identity, sha256


class Completion(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.folder = self.root / 'videos' / 'communication'
        self.dest = self.folder / 'Recorded' / 'Input'
        self.dest.mkdir(parents=True)
        self.input = self.root / 'Input'
        self.input.mkdir()
        self.store = Store(self.root / 'Data')
        self.p = self.store.create('Communication', initial={'notes': 'Keep my work'})
        self.p = self.store.save(self.p['id'], {'stage': 'Recording', 'locks': {'script': 'approved'}}, self.p['revision'])
        self.pid = self.p['id']
        self.idle = Mock(side_effect=AssertionError('OBS must not be touched for an in-place completion'))
        self.validator = Mock(return_value={'audio': {'status': 'passed'}, 'duration': 12.0})
        self.rec = Recording(self.store, self.root / 'app', input_dir=self.input,
                             folder_roots=[self.root / 'videos'], idle=self.idle, validator=self.validator)
        self.pieces = VideoPieces(self.store, SimpleNamespace(path=lambda pid: str(self.folder)), self.rec,
                                  SimpleNamespace(deck_files=lambda pid: (None, None)))
        self.source = self.file(self.dest / 'original.mp4', b'finalized-mp4')
        self.mkv = self.file(self.dest / 'original.mkv', b'original-mkv')
        self.rec.activate(self.pid, self.p['revision'], str(self.folder))

    def file(self, path, data):
        path.write_bytes(data)
        os.utime(path, (time.time()-30, time.time()-30))
        return path

    def journal(self, **changes):
        self.rec.select(self.pid, self.p['revision'], str(self.source))
        with self.store.connect() as c:
            selection = self.rec.binding(c, self.pid)['selection']
            take = dict(id=selection['id'], project_id=self.pid, source=selection['source'],
                        destination=str(self.dest / 'planned-rename.mp4'), sha256=sha256(self.source),
                        expected_revision=self.p['revision'], phase='prepared', validation={}, prepared_at='before')
            take.update(changes)
            c.execute('INSERT INTO youtube_recording_takes VALUES(?,?,?)', (take['id'], self.pid, json.dumps(take)))
        return take

    def test_complete_despite_stale_teleprompter_preserves_files_and_commits_atomically(self):
        with self.store.connect() as c:
            c.execute('CREATE TABLE film_preps(id TEXT, project_id TEXT, body TEXT)')
            c.execute('INSERT INTO film_preps VALUES(?,?,?)', ('prep', self.pid, '{"state":"needs_you"}'))
        before = {f.name: (identity(f), sha256(f)) for f in self.dest.iterdir()}
        out = self.pieces.recorded(self.pid, str(self.source))
        p = self.store.get(self.pid)
        self.assertEqual(p['stage'], 'Recorded')
        self.assertEqual(p['media']['video'], str(self.source))
        self.assertTrue(p['locks']['recording'])
        self.assertEqual(p['locks']['script'], 'approved')
        self.assertEqual(p['notes'], 'Keep my work')
        self.assertEqual(p['revision'], self.p['revision']+1)
        self.assertTrue(out['take']['kept_in_place'])
        self.assertFalse(out['status']['active'])
        self.assertEqual(before, {f.name: (identity(f), sha256(f)) for f in self.dest.iterdir()})
        self.idle.assert_not_called()
        with self.store.connect() as c:
            self.assertEqual(c.execute('SELECT body FROM film_preps').fetchone()[0], '{"state":"needs_you"}')

    def test_pending_retry_uses_same_journal_and_retains_source(self):
        pending = self.journal()
        out = self.pieces.recorded(self.pid, str(self.source))
        self.assertEqual(out['take']['id'], pending['id'])
        self.assertEqual(out['take']['previous_destination'], pending['destination'])
        self.assertEqual(out['take']['phase'], 'complete')
        self.assertFalse(Path(pending['destination']).exists())
        self.assertTrue(self.source.exists())

    def test_repeated_clicks_and_later_editing_do_not_rewind_or_duplicate(self):
        first = self.pieces.recorded(self.pid, str(self.source))
        p = self.store.get(self.pid)
        self.store.save(self.pid, {'stage': 'Editing', 'notes': 'Later edit work'}, p['revision'])
        before = self.store.get(self.pid)
        for _ in range(3):
            out = self.pieces.recorded(self.pid, str(self.source))
            self.assertEqual(out['take']['id'], first['take']['id'])
            self.assertTrue(out['take']['reused'])
        self.assertTrue(self.rec.lock_take()['reused'])
        self.assertEqual(self.store.get(self.pid), before)
        self.assertEqual(self.validator.call_count, 1)
        self.idle.assert_not_called()
        with self.store.connect() as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM youtube_recording_takes').fetchone()[0], 1)

    def test_failed_validation_leaves_project_and_media_unchanged(self):
        before = self.store.get(self.pid)
        self.validator.side_effect = Conflict('Decode failed')
        with self.assertRaisesRegex(Conflict, 'Decode failed'):
            self.pieces.recorded(self.pid, str(self.source))
        self.assertEqual(self.store.get(self.pid), before)
        self.assertEqual(self.source.read_bytes(), b'finalized-mp4')

    def test_retry_rejects_changed_pending_file_without_reselection(self):
        pending = self.journal()
        self.source.write_bytes(b'changed')
        with self.assertRaisesRegex(Conflict, 'Selected take changed'):
            self.pieces.recorded(self.pid, str(self.source))
        with self.store.connect() as c:
            self.assertEqual(self.rec.binding(c, self.pid)['selection']['id'], pending['id'])
        self.assertEqual(self.store.get(self.pid), self.p)

    def test_pending_collision_and_different_selection_leave_everything_alone(self):
        pending = self.journal()
        Path(pending['destination']).write_bytes(b'existing-work')
        with self.assertRaisesRegex(Conflict, 'collision'):
            self.pieces.recorded(self.pid, str(self.source))
        with self.assertRaisesRegex(Conflict, 'Recover the pending'):
            self.pieces.recorded(self.pid, str(self.mkv))
        self.assertEqual(Path(pending['destination']).read_bytes(), b'existing-work')
        self.assertTrue(self.source.exists())

    def test_project_change_during_validation_does_not_get_overwritten(self):
        def validate(_):
            p = self.store.get(self.pid)
            self.store.save(self.pid, {'notes': 'Concurrent work'}, p['revision'])
            return {'duration': 12}
        self.validator.side_effect = validate
        with self.assertRaisesRegex(Conflict, 'changed during validation'):
            self.pieces.recorded(self.pid, str(self.source))
        p = self.store.get(self.pid)
        self.assertEqual((p['notes'], p['stage'], p['media']), ('Concurrent work', 'Recording', {}))

    def test_overlapping_completion_clicks_fail_without_duplicate_work(self):
        entered, release = threading.Event(), threading.Event()
        def validate(_):
            entered.set()
            self.assertTrue(release.wait(10))
            return {'duration': 12}
        self.validator.side_effect = validate
        results = []
        def run():
            try: results.append(self.pieces.recorded(self.pid, str(self.source)))
            except Exception as e: results.append(e)
        thread = threading.Thread(target=run)
        thread.start()
        try:
            self.assertTrue(entered.wait(10))
            with self.assertRaisesRegex(Conflict, 'Already checking'):
                self.pieces.recorded(self.pid, str(self.source))
        finally:
            release.set();thread.join(10)
        self.assertEqual(len(results), 1)
        self.assertIsInstance(results[0], dict, results)

    def test_recording_status_does_not_infer_completion_from_folder_contents(self):
        self.assertIsNone(self.pieces.status(self.pid)['recording'])
        self.assertEqual(len(self.pieces.takes(self.pid)), 1)
        self.file(self.input / self.source.name, b'other-video')
        self.assertEqual(len(self.pieces.takes(self.pid)), 2)

    def test_completion_endpoint_retries_without_resetting_obs(self):
        from studio.server import Handler
        h = object.__new__(Handler)
        h.path = '/api/projects/' + self.pid + '/recording/done'
        h.authorized = Mock(return_value=True)
        body = json.dumps({'path': str(self.source)}).encode()
        h.headers = {'Content-Length': str(len(body))}
        h.rfile = io.BytesIO(body)
        h.send = Mock()
        h.server = SimpleNamespace(pieces=self.pieces, filming=SimpleNamespace(restore_default=Mock()), store=self.store)
        h.route('POST')
        self.assertEqual(h.send.call_args.args[0]['take']['phase'], 'complete')
        before = self.store.get(self.pid)
        h.rfile.seek(0)
        h.route('POST')
        self.assertTrue(h.send.call_args.args[0]['take']['reused'])
        self.assertEqual(self.store.get(self.pid), before)
        h.server.filming.restore_default.assert_not_called()

    @unittest.skipUnless(os.name == 'nt', 'Windows sharing guard')
    def test_open_writer_blocks_completion(self):
        with self.source.open('ab'):
            with self.assertRaises(Conflict):
                self.pieces.recorded(self.pid, str(self.source))
        self.assertEqual(self.store.get(self.pid)['stage'], 'Recording')

    def test_shared_input_still_requires_idle_obs_and_preserves_original_on_failure(self):
        shared = self.file(self.input / 'shared.mp4', b'finalized-shared')
        self.idle.side_effect = Conflict('OBS unavailable')
        with self.assertRaisesRegex(Conflict, 'OBS unavailable'):
            self.pieces.recorded(self.pid, str(shared))
        self.assertEqual(shared.read_bytes(), b'finalized-shared')
        self.assertEqual(self.store.get(self.pid)['stage'], 'Recording')

    @unittest.skipUnless(os.name == 'nt', 'Windows no-overwrite rename')
    def test_shared_input_move_and_retry_preserve_mkv(self):
        self.idle.side_effect = None
        shared = self.file(self.input / 'shared.mp4', b'finalized-shared')
        mkv = self.file(self.input / 'shared.mkv', b'original-shared')
        out = self.pieces.recorded(self.pid, str(shared))
        self.assertFalse(shared.exists())
        self.assertEqual(Path(out['take']['destination']).read_bytes(), b'finalized-shared')
        self.assertEqual(mkv.read_bytes(), b'original-shared')
        before = self.store.get(self.pid)
        self.assertTrue(self.pieces.recorded(self.pid, str(shared))['take']['reused'])
        self.assertEqual(self.store.get(self.pid), before)


if __name__ == '__main__':
    unittest.main()
