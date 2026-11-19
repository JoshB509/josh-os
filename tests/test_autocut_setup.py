"""Automatic Setup contracts; no real media, desktop, Premiere or AutoCut calls."""
import json
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from studio import autocut_setup, edit_master
from studio.autocut_driver import Driver, AutoCutError
from studio.edit_flow import EditFlow
from studio.store import Conflict


class Setup(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.project = self.root / 'fresh.prproj'
        self.project.write_bytes(b'project')
        self.src = dict(id='source', digest='d' * 64, payload=dict(files={k: dict(path=k) for k in ('camera', 'mic')}))
        self.driver = Mock()
        self.driver.premiere_ops.side_effect = lambda ops: [dict(project=str(self.project), sequences=['GREEN_MASTER']) if op['op'] == 'project' else dict(frame_ticks=8467191533) for op in ops]
        self.flow = SimpleNamespace(folder=lambda pid: self.root, latest=lambda *a: self.src,
                                    sources_ok=lambda src: None, _setup_step=Mock(), _hash=lambda v: v['path'] * 10,
                                    _decode=lambda *a: [0] * 100, _cache=lambda: self.root,
                                    clean_edit=Mock(), autocut_driver_factory=lambda: self.driver)
        (self.root / ('pcm16k-' + ('mic' * 10)[:24] + '.f32')).write_bytes(b'pcm')
        self.stages, self.calls = {}, []
        owner = self
        class Pass:
            def __init__(self, *a, **kw): pass
            def stage(self, key): return owner.stages.get(key)
            def __getattr__(self, key):
                def call(*args, **kwargs):
                    owner.calls.append(key)
                    owner.stages[key] = dict(sequence='AUTOCUT_RAW_r01')
                    return owner.stages[key]
                return call
        self.factory = Pass
        self.premiere = dict(state='built', project=str(self.project))
        self.sync = patch('dajoshi.media.sync', return_value=dict(verified=True, offset_seconds=-3.7))
        self.sync.start()
        self.addCleanup(self.sync.stop)

    def run_pass(self):
        return autocut_setup.run(self.flow, 'pid', self.src, self.premiere, 'Josh clicked Start processing', self.factory)

    def test_order_and_register_after_native_review(self):
        result = self.run_pass()
        self.assertEqual(self.calls, ['raw', 'silences', 'repeat', 'native', 'review'])
        self.assertEqual(result['state'], 'review')
        self.driver.ensure_open.assert_called_once()
        self.flow.clean_edit.register.assert_called_once()
        self.flow.clean_edit.cleanup.assert_called_once_with('pid', reuse=True)
        self.assertEqual([c[0] for c in self.flow.clean_edit.method_calls], ['register', 'cleanup'])
        self.flow.clean_edit.build.assert_not_called()
        self.flow.clean_edit.approve.assert_not_called()

    def test_retry_never_repeats_completed_cuts_or_discards_review_edits(self):
        first = self.run_pass()
        (self.root / 'clean-edit.json').write_text(json.dumps(dict(evidence=first['evidence'], approved={'sequence': 'kept'}, edits=['keep'])))
        self.flow.clean_edit.register.reset_mock()
        self.calls.clear()
        self.driver.ensure_open.reset_mock()
        self.run_pass()
        self.driver.ensure_open.assert_not_called()
        self.assertEqual(self.calls, [])
        self.flow.clean_edit.register.assert_not_called()

    def test_uncertain_attempt_is_not_repeated(self):
        ev = self.root / 'autocut' / self.src['digest'][:24]
        ev.mkdir(parents=True)
        (ev / 'setup-job.json').write_text(json.dumps(dict(pending_stage='silences')))
        with self.assertRaisesRegex(Conflict, 'uncertain'):
            self.run_pass()
        self.assertEqual(self.calls, [])

    def test_wrong_project_no_sequence_or_cut(self):
        self.driver.premiere_ops.side_effect = lambda ops: [dict(project='other.prproj'), dict(frame_ticks=10)]
        with self.assertRaisesRegex(Conflict, 'another project'):
            self.run_pass()
        self.assertEqual(self.calls, [])

    def test_unverified_sync_no_cut(self):
        with patch('dajoshi.media.sync', return_value=dict(verified=False)):
            with self.assertRaisesRegex(Conflict, 'not verified'):
                self.run_pass()
        self.assertEqual(self.calls, [])

    def test_cancel_stops_before_next_stage(self):
        (self.root / 'AUTOCUT_CANCEL').write_text('cancel')
        with self.assertRaisesRegex(Conflict, 'cancelled'):
            self.run_pass()
        self.assertEqual(self.calls, [])

    def test_new_run_has_separate_evidence(self):
        (self.root / 'active-edit-run.json').write_text(json.dumps(dict(id='fresh-run')))
        result = self.run_pass()
        self.assertEqual(Path(result['evidence']).name, 'fresh-run')

    def test_native_blocker_preserves_cuts_and_reports_actual_blocked_stage(self):
        from studio.autocut_pass import NativeTranscriptBlocked
        self.stages.update({key: dict(sequence='AUTOCUT_RAW_r01') for key in ('raw','silences','repeat')})
        base = self.factory
        class BlockedPass(base):
            def native(self, **kwargs):
                raise NativeTranscriptBlocked('Load the native transcription bridge')
        self.factory = BlockedPass
        with self.assertRaises(NativeTranscriptBlocked):
            self.run_pass()
        job = json.loads((self.root/'autocut'/self.src['digest'][:24]/'setup-job.json').read_text())
        self.assertEqual((job['state'], job['stage']), ('blocked','native'))
        self.flow._setup_step.assert_any_call('pid', 'transcribe', 'blocked', 'Load the native transcription bridge')
        self.driver.ensure_open.assert_not_called()
        self.assertEqual(self.calls, [])
        self.flow.clean_edit.register.assert_not_called()

    def test_cleanup_failure_stays_at_cleanup_and_retains_completed_autocut(self):
        self.flow.clean_edit.cleanup.side_effect = RuntimeError('cleanup fixture unavailable')
        with self.assertRaisesRegex(RuntimeError, 'cleanup fixture unavailable'):
            self.run_pass()
        self.assertEqual(self.calls, ['raw','silences','repeat','native','review'])
        job = json.loads((self.root/'autocut'/self.src['digest'][:24]/'setup-job.json').read_text())
        self.assertEqual((job['state'], job['stage']), ('failed','cleanup'))
        self.flow._setup_step.assert_any_call('pid', 'cleanup', 'failed', 'cleanup fixture unavailable')
        self.flow.clean_edit.register.assert_called_once()
        self.flow.clean_edit.build.assert_not_called()
        self.flow.clean_edit.approve.assert_not_called()

    def test_explicit_authorization_required(self):
        with self.assertRaisesRegex(Conflict, 'explicit go-ahead'):
            autocut_setup.run(self.flow, 'pid', self.src, self.premiere, '', self.factory)
        self.assertEqual(self.calls, [])


class Routing(unittest.TestCase):
    def test_go_dispatch_uses_autocut_and_never_legacy_analysis(self):
        f = object.__new__(EditFlow)
        f.store = Mock()
        f.store.get.return_value = dict(name='Video')
        src = dict(id='source', digest='digest', payload=dict(files={k: dict(path=k) for k in ('camera', 'mic')}))
        f.latest = Mock(return_value=src)
        f.sources_ok = Mock(return_value=None)
        f._setup_new = Mock(); f._setup_step = Mock(); f._setup_finish = Mock(); f._setup_running = Mock(return_value='project')
        f._put = Mock(); f._tell = Mock(); f._setup_picture = Mock(return_value=('keyed', 'line')); f.analyse = Mock()
        with tempfile.TemporaryDirectory() as tmp:
            f.data = Path(tmp); (f.data / 'look.json').write_text('{}')
            with patch('studio.edit_master.prepare_run') as fresh, patch('studio.edit_master.project_path', return_value=f.data / 'new.prproj'), patch('studio.edit_master.build_master', return_value=dict(state='built', project='new.prproj')), patch('studio.autocut_setup.run', return_value=dict(sequence='AUTOCUT_RAW_r01')) as run:
                f._go('pid', '', True)
            fresh.assert_called_once_with(f, 'pid', src, fresh=True)
            run.assert_called_once()
            f.analyse.assert_not_called()
            self.assertEqual(f._setup_finish.call_args.args[1], 'done')

    def test_active_legacy_job_blocks_autocut(self):
        f = object.__new__(EditFlow); f.store = Mock(); f.lock = threading.Lock()
        t = Mock(); t.is_alive.return_value = True; f.jobs = {'pid': t}
        with self.assertRaisesRegex(Conflict, 'already running'):
            f.go_ahead('pid')

    def test_fresh_projects_preserve_existing_project_and_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            flow = SimpleNamespace(folder=lambda pid: root, folders=SimpleNamespace(path=lambda pid: root), store=Mock())
            flow.store.get.return_value = dict(name='Video')
            flow._setup_step = Mock()
            original = edit_master.default_project_path(flow, 'pid'); original.parent.mkdir(parents=True); original.write_bytes(b'manual mask')
            review = root / 'clean-edit.json'; review.write_text('{"edits":["manual"]}')
            flow.bridge_call = Mock(return_value=dict(project=str(original)))
            with patch('studio.edit_flow.wake_premiere'), patch('studio.edit_master.ops', return_value=[dict(project=str(original))]) as ops:
                first = edit_master.prepare_run(flow, 'pid', dict(id='source', digest='digest'))
                repeat = edit_master.prepare_run(flow, 'pid', dict(id='source', digest='digest'))
                self.assertEqual(first, repeat)
                second = edit_master.prepare_run(flow, 'pid', dict(id='source', digest='digest'), fresh=True)
            self.assertNotEqual(first['project'], second['project'])
            self.assertEqual(original.read_bytes(), b'manual mask')
            self.assertEqual(review.read_text(), '{"edits":["manual"]}')
            self.assertEqual(edit_master.project_path(flow, 'pid'), Path(second['project']))

    def test_no_open_project_uses_read_only_probe_and_never_guarded_read_or_save(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            flow = SimpleNamespace(folder=lambda pid: root, folders=SimpleNamespace(path=lambda pid: root), store=Mock(), _setup_step=Mock(), bridge_call=Mock(return_value=dict(notes=['No project open'])))
            flow.store.get.return_value = dict(name='Video')
            with patch('studio.edit_flow.wake_premiere'), patch('studio.edit_master.ops') as ops:
                run = edit_master.prepare_run(flow, 'pid', dict(id='source', digest='digest'))
            flow.bridge_call.assert_called_once_with('tq_probe', dict(master='GREEN_MASTER'))
            ops.assert_not_called()
            self.assertEqual(edit_master.project_path(flow, 'pid'), Path(run['project']))

    def test_unrelated_open_project_is_never_saved_or_edited(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            flow = SimpleNamespace(folder=lambda pid: root, folders=SimpleNamespace(path=lambda pid: root), store=Mock(), _setup_step=Mock(), bridge_call=Mock(return_value=dict(project='E:/Other/user-work.prproj')))
            flow.store.get.return_value = dict(name='Video')
            with patch('studio.edit_flow.wake_premiere'), patch('studio.edit_master.ops') as ops:
                with self.assertRaisesRegex(edit_master.MasterError, 'Another Premiere project'):
                    edit_master.prepare_run(flow, 'pid', dict(id='source', digest='digest'))
            ops.assert_not_called()
            self.assertFalse((root / 'active-edit-run.json').exists())

    def test_create_result_must_match_before_importing_media(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'Tariq' / 'fresh.prproj'
            with patch('studio.edit_master.project_path', return_value=path), patch('studio.edit_flow.wake_premiere'), patch('studio.edit_master.ops', return_value=[dict(project='E:/Other/project.prproj')]) as ops, patch('studio.edit_master.mask_fill') as mask:
                with self.assertRaisesRegex(edit_master.MasterError, 'requested fresh project'):
                    edit_master.build_master(Mock(), 'pid', 'camera', 'mic', look=dict(presenter=[]))
            self.assertEqual(ops.call_count, 1)
            self.assertEqual(ops.call_args.args[1], [dict(op='create_project', path=str(path))])
            mask.assert_not_called()

    def test_active_project_must_match_newly_created_project_before_import(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'Tariq' / 'fresh.prproj'
            with patch('studio.edit_master.project_path', return_value=path), patch('studio.edit_flow.wake_premiere'), patch('studio.edit_master.ops', side_effect=[[dict(project=str(path))], [dict(project='E:/Other/project.prproj')]]) as ops, patch('studio.edit_master.mask_fill') as mask:
                with self.assertRaisesRegex(edit_master.MasterError, 'not active'):
                    edit_master.build_master(Mock(), 'pid', 'camera', 'mic', look=dict(presenter=[]))
            self.assertEqual(ops.call_count, 2)
            mask.assert_not_called()

    def test_panel_launch_only_when_closed(self):
        d = Driver(); d.snapshot = Mock(return_value=dict(ok=True)); d.ui = Mock()
        d.ensure_open(); d.ui.assert_not_called()
        d.snapshot = Mock(side_effect=[AutoCutError('AutoCut is not open.'), dict(ok=True)])
        d.ensure_open(); d.ui.assert_called_once_with('open')


if __name__ == '__main__':
    unittest.main()
