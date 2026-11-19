import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from studio import native_background as nb
from studio import premiere_transcript as pt
from studio.transcript_worker import RequestStore, WorkerBlocked, file_digest


class Background(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        mic = self.root/'mic.wav'; mic.write_bytes(b'unique-mic')
        self.identity = dict(project='e:/tariq/test.prproj', mic=pt.media_key(mic),
                             mic_sha256=file_digest(mic), sequence='AUTOCUT_RAW_r01', run='isolated-run')
        self.info = dict(name='AUTOCUT_RAW_r01', frame_ticks=100, items=[dict(kind='a', track=0, start='0', end='100')])
        self.store = RequestStore(self.root/'request.json'); self.store.prepare(self.identity, self.info)
        self.tx = dict(media=str(mic), words=[dict(word='Hello', start=0, end=1)])
        self.driver = Mock()
        self.driver.premiere_ops.side_effect = lambda ops: [dict(project=self.identity['project']) if x['op']=='project' else dict(saved=True) for x in ops]
        self.read_info = Mock(return_value=self.info)
        self.clock = Mock(); self.clock.monotonic.return_value=0
        self.progress = Mock()

    def wait(self, decode, **kwargs):
        return nb.wait(self.store, self.identity, self.info, self.driver, self.read_info,
                       decode, self.progress, lambda: None, clock=self.clock, **kwargs)

    def test_wait_ignores_other_media_then_verifies_saved_exact_mic(self):
        decode = Mock(side_effect=[[dict(self.tx, media='camera.mp4')], [self.tx]])
        self.assertEqual(self.wait(decode), self.tx)
        self.assertEqual(self.store.read()['state'], 'verified')
        self.clock.sleep.assert_called_once_with(5)
        self.assertEqual({c.args[0][0]['op'] for c in self.driver.premiere_ops.call_args_list}, {'project','save'})
        self.driver.native_transcript.assert_not_called()

    def test_timeout_resume_observes_same_request_without_resubmission(self):
        request = self.store.read()['request_id']
        with self.assertRaisesRegex(WorkerBlocked, 'timed out'): self.wait(lambda: [], timeout=0)
        self.assertEqual(self.store.read()['state'], 'observing')
        self.wait(lambda: [self.tx])
        self.assertEqual(self.store.read()['request_id'], request)

    def test_project_switch_stops_before_saving(self):
        self.driver.premiere_ops.return_value=[dict(project='other')]
        self.driver.premiere_ops.side_effect=None
        with self.assertRaisesRegex(WorkerBlocked,'project changed'): self.wait(lambda: [self.tx])
        self.assertEqual(self.driver.premiere_ops.call_count, 1)

    def test_cut_change_stops_before_save(self):
        self.read_info.return_value = dict(self.info, items=[])
        with self.assertRaisesRegex(WorkerBlocked,'cut changed'): self.wait(lambda: [self.tx])
        self.assertEqual(self.driver.premiere_ops.call_count, 1)

    def test_cut_change_during_save_rejects_transcript(self):
        self.read_info.side_effect = [self.info, dict(self.info, items=[])]
        with self.assertRaisesRegex(WorkerBlocked,'cut changed'): self.wait(lambda: [self.tx])
        self.assertNotEqual(self.store.read()['state'],'verified')

    def test_microphone_change_rejects_transcript(self):
        Path(self.identity['mic']).write_bytes(b'changed')
        with self.assertRaisesRegex(WorkerBlocked,'microphone file changed'): self.wait(lambda: [self.tx])

    def test_other_worker_claim_cannot_be_stolen(self):
        self.store.claim('other-worker',self.identity,self.info)
        with self.assertRaisesRegex(WorkerBlocked,'already attempted'): self.wait(lambda: [self.tx])
        self.driver.premiere_ops.assert_not_called()

    def test_save_ack_is_required(self):
        self.driver.premiere_ops.side_effect=[[dict(project=self.identity['project'])],[dict(saved=False)]]
        with self.assertRaisesRegex(WorkerBlocked,'confirm saving'): self.wait(lambda: [self.tx])

    def test_pref_read_never_enables_missing_false_or_malformed(self):
        p=self.root/'prefs'
        for text in ('<Prefs/>','<Prefs><BE.Prefs.Transcription.RunBackgroundTranscription>false</BE.Prefs.Transcription.RunBackgroundTranscription></Prefs>','invalid'):
            p.write_text(text); before=p.read_bytes()
            self.assertFalse(nb.capabilities(p)['enabled']); self.assertEqual(p.read_bytes(),before)
        p.write_text('<Prefs><BE.Prefs.Transcription.RunBackgroundTranscription>true</BE.Prefs.Transcription.RunBackgroundTranscription></Prefs>')
        self.assertTrue(nb.capabilities(p)['enabled'])

    def test_autocut_native_routes_unsupported_api_to_background_and_completes(self):
        from studio.autocut_pass import AutoCutPass
        p=AutoCutPass(self.root/'pass',self.identity['project'],self.identity['mic'],'pcm',0,16000,100,
                     driver=self.driver,say=lambda message: None)
        p.state['stages']={k:dict(sequence=self.identity['sequence']) for k in ('raw','silences','repeat')}
        self.driver.native_transcript.return_value=dict(supported=False)
        with patch.object(nb,'capabilities',return_value=dict(enabled=True)), patch.object(pt,'words',side_effect=[[],[self.tx],[self.tx]]), patch('studio.autocut_pass.readback',return_value=dict(info=self.info,problems=[])):
            result=p.native()
        self.assertEqual(result['words'],1)
        self.assertEqual([x.args[0]['action'] for x in self.driver.native_transcript.call_args_list],['capabilities'])
        request=json.loads((self.root/'pass/native-ui-request.json').read_text())
        self.assertEqual((request['route'],request['state']),('premiere-background','verified'))

    def test_existing_background_request_resumes_without_any_api_dispatch(self):
        from studio.autocut_pass import AutoCutPass
        p=AutoCutPass(self.root/'pass',self.identity['project'],self.identity['mic'],'pcm',0,16000,100,
                     driver=self.driver,say=lambda message: None)
        p.state['stages']={k:dict(sequence=self.identity['sequence']) for k in ('raw','silences','repeat')}
        store=RequestStore(self.root/'pass/native-ui-request.json');store.prepare(p._native_ui_identity(),self.info)
        store.observe_background(p._native_ui_identity(),self.info)
        with patch.object(nb,'capabilities',return_value=dict(enabled=True)), patch.object(pt,'words',side_effect=[[],[self.tx],[self.tx]]), patch('studio.autocut_pass.readback',return_value=dict(info=self.info,problems=[])):
            p.native()
        self.driver.native_transcript.assert_not_called()
        self.assertEqual(store.read()['state'],'verified')


if __name__ == '__main__': unittest.main()
