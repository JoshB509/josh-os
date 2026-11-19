import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from studio.transcript_worker import RequestStore, WorkerBlocked
from tests.test_native_handoff import NativeHandoff, MIC, PROJECT, WORDS
from studio.premiere_transcript import media_key

IDENTITY = dict(project_id='pid', run='run1', project=media_key(PROJECT), mic=media_key(MIC), mic_sha256='abc', sequence='AUTOCUT_RAW_r01')
INFO = dict(name='AUTOCUT_RAW_r01', frame_ticks='100', items=[dict(kind='a', track=0, start='0', end='100', inp='0', path=MIC)])
SELECTED = dict(media=MIC, words=WORDS)


class WorkerContract(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.clock = 100
        self.store = RequestStore(Path(self.tmp.name)/'request.json', clock=lambda:self.clock)
        self.request = self.store.prepare(IDENTITY, INFO)

    def test_prepare_idempotent_and_no_dispatch(self):
        self.assertEqual(self.request, self.store.prepare(IDENTITY, INFO))
        self.assertFalse(self.request['dispatch_available'])

    def test_claim_single_worker_and_no_reclaim_after_expiry(self):
        claimed = self.store.claim('dot', IDENTITY, INFO, ttl=1)
        self.clock = 102
        with self.assertRaises(WorkerBlocked): self.store.report(claimed['claim_id'], 'started')
        self.assertEqual(self.store.read()['state'], 'uncertain')
        with self.assertRaises(WorkerBlocked): self.store.claim('claude', IDENTITY, INFO)

    def test_start_persisted_before_action_and_no_duplicate_start(self):
        claim = self.store.claim('dot', IDENTITY, INFO)
        self.store.report(claim['claim_id'], 'started')
        with self.assertRaises(WorkerBlocked): self.store.report(claim['claim_id'], 'started')
        with self.assertRaises(WorkerBlocked): self.store.report('other', 'awaiting_verification')
        self.store.report(claim['claim_id'], 'awaiting_verification')
        self.assertNotEqual(self.store.read()['state'], 'verified')

    def test_worker_cannot_report_success(self):
        claim = self.store.claim('dot', IDENTITY, INFO)
        with self.assertRaises(WorkerBlocked): self.store.report(claim['claim_id'], 'verified')

    def test_identity_or_cut_change_blocks(self):
        for identity in (dict(IDENTITY, mic='other'), dict(IDENTITY, run='run2'), dict(IDENTITY, mic_sha256='changed')):
            with self.assertRaises(WorkerBlocked): self.store.prepare(identity, INFO)
        with self.assertRaises(WorkerBlocked): self.store.verify(IDENTITY, dict(INFO, items=[]), SELECTED, True)

    def test_saved_source_and_nonempty_words_required(self):
        for selected, saved in ((SELECTED, False), (dict(media='camera', words=WORDS), True), (dict(media=MIC, words=[]), True)):
            with self.assertRaises(WorkerBlocked): self.store.verify(IDENTITY, INFO, selected, saved)
        self.assertEqual(self.store.verify(IDENTITY, INFO, SELECTED, True)['state'], 'verified')

    def test_crashed_lock_fails_closed(self):
        self.store.path.with_suffix('.lock').write_text('')
        with self.assertRaises(WorkerBlocked): self.store.claim('dot', IDENTITY, INFO)

    def test_out_of_order_and_unbounded_worker_rejected(self):
        with self.assertRaises(WorkerBlocked): self.store.claim('dot', IDENTITY, INFO, ttl=901)
        c = self.store.claim('dot', IDENTITY, INFO)
        with self.assertRaises(WorkerBlocked): self.store.report(c['claim_id'], 'awaiting_verification')


class NativeWorkerIntegration(NativeHandoff):
    def test_unsupported_api_is_blocked_before_request(self):
        self.driver.native_transcript.return_value = dict(supported=False, reason='API unavailable')
        with patch('studio.autocut_pass.pt.words', return_value=[]), patch.object(self.pass_, '_native_ui_identity', return_value=IDENTITY), patch('studio.autocut_pass.readback', return_value=dict(info=INFO, problems=[])):
            with self.assertRaisesRegex(Exception, 'No supported desktop worker'):
                self.pass_.native()
        self.assertNotIn('native_request', self.pass_.state)
        self.assertTrue((Path(self.tmp.name) if hasattr(self, 'tmp') else Path(self.temp.name)).joinpath('native-ui-request.json').is_file())

    def test_resume_saved_transcript_verifies_cut_before_native_completion(self):
        self.driver.premiere_ops.side_effect = [[dict(project=PROJECT)], [dict(saved=True)]]
        store = RequestStore(Path(self.temp.name)/'native-ui-request.json')
        store.prepare(IDENTITY, INFO)
        with patch('studio.autocut_pass.pt.words', return_value=[SELECTED]), patch.object(self.pass_, '_native_ui_identity', return_value=IDENTITY), patch('studio.autocut_pass.readback', return_value=dict(info=INFO, problems=[])):
            self.pass_.native()
        self.assertEqual(store.read()['state'], 'verified')
        self.driver.native_transcript.assert_not_called()

    def test_resume_changed_cut_does_not_mark_native_done(self):
        store = RequestStore(Path(self.temp.name)/'native-ui-request.json')
        store.prepare(IDENTITY, INFO)
        with patch('studio.autocut_pass.pt.words', return_value=[SELECTED]), patch.object(self.pass_, '_native_ui_identity', return_value=IDENTITY), patch('studio.autocut_pass.readback', return_value=dict(info=dict(INFO, items=[]), problems=[])):
            with self.assertRaisesRegex(Exception, 'cut changed'): self.pass_.native()
        self.assertNotIn('native', self.pass_.state['stages'])

    def test_existing_ui_request_never_falls_back_to_new_api_dispatch(self):
        store = RequestStore(Path(self.temp.name)/'native-ui-request.json')
        store.prepare(IDENTITY, INFO)
        with patch('studio.autocut_pass.pt.words', return_value=[]), patch.object(self.pass_, '_native_ui_identity', return_value=IDENTITY), patch('studio.autocut_pass.readback', return_value=dict(info=INFO, problems=[])):
            with self.assertRaisesRegex(Exception, 'retained'): self.pass_.native()
        self.driver.native_transcript.assert_not_called()

    def test_resume_without_save_acknowledgement_stays_blocked(self):
        store = RequestStore(Path(self.temp.name)/'native-ui-request.json')
        store.prepare(IDENTITY, INFO)
        with patch('studio.autocut_pass.pt.words', return_value=[SELECTED]), patch.object(self.pass_, '_native_ui_identity', return_value=IDENTITY), patch('studio.autocut_pass.readback', return_value=dict(info=INFO, problems=[])):
            with self.assertRaisesRegex(Exception, 'not verified'): self.pass_.native()
        self.assertEqual(store.read()['state'], 'pending')

