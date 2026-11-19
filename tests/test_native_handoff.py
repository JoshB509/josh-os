import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from studio import premiere_transcript as pt
from studio.autocut_pass import AutoCutPass, NativeTranscriptBlocked

MIC = r'C:\Audio\microphone.wav'
PROJECT = r'E:\Video\Tariq\fresh.prproj'
WORDS = [dict(word='Hello', start=1.0, end=1.2)]


class NativeHandoff(unittest.TestCase):
    def setUp(self):
        automatic = patch('studio.native_background.capabilities', return_value=dict(enabled=False, reason='fixture-disabled'))
        automatic.start(); self.addCleanup(automatic.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.driver = Mock()
        self.driver.premiere_ops.return_value = [dict(project=PROJECT)]
        self.pass_ = AutoCutPass(self.temp.name, PROJECT, MIC, 'pcm', -3.7, 10000, 100,
                                 driver=self.driver, say=lambda message: None)
        self.pass_.state['stages'] = {k: dict(sequence='AUTOCUT_RAW_r01') for k in ('raw', 'silences', 'repeat')}

    def test_source_identity_selected_among_camera_and_unknown_transcripts(self):
        tx = [dict(media='camera.mp4', words=WORDS), dict(media=None, words=WORDS), dict(media=MIC.upper(), words=WORDS)]
        with patch.object(pt, 'words', return_value=tx):
            result = self.pass_.native()
        self.assertEqual(result['media'], MIC)
        self.driver.native_transcript.assert_not_called()
        saved = json.loads((Path(self.temp.name) / 'premiere-native-words-mic.json').read_text())
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0]['media'], MIC.upper())

    def test_missing_transcript_dispatches_once_polls_and_verifies_saved_source(self):
        self.driver.native_transcript.side_effect = [dict(supported=True), dict(state='running'),
            dict(state='done', project=PROJECT, mic=MIC, saved=True)]
        progress = Mock()
        with patch.object(pt, 'words', side_effect=[[], [dict(media=MIC, words=WORDS)]]), patch('studio.autocut_pass.time.sleep'):
            self.pass_.native(progress=progress)
        self.assertEqual([c.args[0]['action'] for c in self.driver.native_transcript.call_args_list], ['capabilities','start','status'])
        self.assertIn('transcribing', progress.call_args.args[0])
        self.assertEqual(set(self.pass_.state['stages']), {'raw','silences','repeat','native'})

    def test_resume_retains_request_and_never_resubmits_or_cuts(self):
        self.pass_.state['native_request'] = dict(request_id='existing-request', expected_project=PROJECT, mic_path=MIC)
        self.driver.native_transcript.side_effect = [dict(supported=True), dict(state='missing')]
        with patch.object(pt, 'words', return_value=[]), self.assertRaises(NativeTranscriptBlocked):
            self.pass_.native()
        self.assertEqual([c.args[0]['action'] for c in self.driver.native_transcript.call_args_list], ['capabilities','status'])
        self.assertEqual(set(self.pass_.state['stages']), {'raw','silences','repeat'})

    def test_unsupported_api_is_blocked_before_request(self):
        self.driver.native_transcript.return_value = dict(supported=False, reason='API unavailable')
        identity = dict(project=pt.media_key(PROJECT), mic=pt.media_key(MIC), mic_sha256='abc', sequence='AUTOCUT_RAW_r01', run='run1')
        rb = dict(problems=[], info=dict(items=[], name='AUTOCUT_RAW_r01', frame_ticks=100))
        with patch.object(pt, 'words', return_value=[]), patch.object(self.pass_, '_native_ui_identity', return_value=identity), patch('studio.autocut_pass.readback', return_value=rb), self.assertRaisesRegex(NativeTranscriptBlocked, 'API unavailable'):
            self.pass_.native()
        self.assertNotIn('native_request', self.pass_.state)
        self.assertEqual(json.loads((Path(self.temp.name)/'native-ui-request.json').read_text())['state'], 'pending')

    def test_wrong_project_blocks_before_save_or_transcription(self):
        self.driver.premiere_ops.return_value = [dict(project='other.prproj')]
        with self.assertRaisesRegex(NativeTranscriptBlocked, 'active Premiere project changed'):
            self.pass_.native()
        self.assertEqual(self.driver.premiere_ops.call_count, 1)
        self.driver.native_transcript.assert_not_called()

    def test_timeout_resume_does_not_submit_new_request(self):
        self.driver.native_transcript.side_effect = [dict(supported=True), dict(state='running')]
        with patch.object(pt, 'words', return_value=[]), self.assertRaisesRegex(NativeTranscriptBlocked, 'not finished'):
            self.pass_.native(timeout=0)
        self.assertIn('native_request', self.pass_.state)
        self.assertNotIn('native', self.pass_.state['stages'])

    def test_result_requires_correct_mic_and_confirmed_save(self):
        self.driver.native_transcript.side_effect = [dict(supported=True), dict(state='done', project=PROJECT, mic='camera.mp4', saved=True)]
        with patch.object(pt, 'words', return_value=[]), self.assertRaisesRegex(NativeTranscriptBlocked, 'unverified'):
            self.pass_.native()
        self.assertNotIn('native', self.pass_.state['stages'])

    def test_unknown_single_transcript_is_never_assumed_to_be_mic(self):
        self.assertIsNone(pt.microphone_transcript([dict(media=None, words=WORDS)], MIC))
        with self.assertRaisesRegex(ValueError, 'Multiple'):
            pt.microphone_transcript([dict(media=MIC)] * 2, MIC)

    def test_decoder_resolves_masterclip_audio_sibling_not_synthetic_source(self):
        xml = '''<PremiereData>
          <ExternallyProvidedTranscriptDocument ObjectID="10"><TranscriptData>mock</TranscriptData></ExternallyProvidedTranscriptDocument>
          <TranscriptClip ObjectID="11"><TranscriptTextSegments ObjectRef="10"/></TranscriptClip>
          <MasterClip ObjectUID="master"><Clips><Clip ObjectRef="11"/><Clip ObjectRef="12"/></Clips></MasterClip>
          <AudioClip ObjectID="12"><Clip><Source ObjectRef="13"/></Clip></AudioClip>
          <AudioMediaSource ObjectID="13"><MediaSource><Media ObjectURef="mic"/></MediaSource></AudioMediaSource>
          <Media ObjectUID="mic"><FilePath>C:\\Audio\\microphone.wav</FilePath></Media>
        </PremiereData>'''
        p = Path(self.temp.name)/'fixture.prproj'; p.write_text(xml)
        with patch.object(pt, '_decode', return_value=WORDS):
            transcripts = pt.words(p)
        self.assertEqual(transcripts[0]['media'], MIC)

if __name__ == '__main__':
    unittest.main()
