import json
import unittest
from pathlib import Path
from unittest.mock import patch
from tests import test_clean_handoff as fixture
from studio.clean_edit import CleanEditError


FakeDriver, AUTO, F = fixture.FakeDriver, fixture.AUTO, fixture.F


class TimingGuard(unittest.TestCase):
    setUp = fixture.Handoff.setUp

    def state(self):
        return json.loads((self.tmp/'project/clean-edit.json').read_text())

    def approve(self):
        return self.svc.approve('p1','Josh',True,'AUTOCUT_RAW_r01')

    def test_approval_commits_transcript_and_exact_map_together(self):
        self.approve()
        st=self.state(); tx=json.loads(Path(st['clean_transcript']['file']).read_text())
        self.assertEqual(tx['approved'],st['approved'])
        self.assertEqual(tx['timeline_digest'],st['approved']['timeline_digest'])
        inputs=self.svc.animation_inputs('p1')
        self.assertEqual(inputs['map'],tx['map'])
        self.assertEqual(inputs['provenance']['clean_transcript'],tx['digest'])
        self.assertNotIn('three,',[w['text'] for w in tx['words']])
        self.assertTrue(all(0 <= w['start'] <= w['end'] <= int(tx['map']['duration_ticks'])/254016000000 for w in tx['words']))
        self.approve()
        self.assertEqual(len(self.state()['transcripts']),1)

    def test_manual_trim_after_approval_blocks_transcript_and_animation(self):
        self.approve(); before=self.state()
        self.pieces=AUTO[:1]+AUTO[2:]
        for action in (self.svc.animation_inputs,self.svc.clean_transcript):
            with self.assertRaisesRegex(CleanEditError,'timing changed'):
                action('p1')
        self.assertEqual(self.state(),before)

    def test_manual_trim_before_approval_does_not_record_approval(self):
        self.pieces=AUTO[:1]+AUTO[2:]
        with self.assertRaisesRegex(CleanEditError,'timing changed'): self.approve()
        self.assertIsNone(self.state()['approved'])

    def test_wrong_open_project_or_mic_is_rejected(self):
        for driver in (FakeDriver(AUTO,project='other.prproj'),FakeDriver(AUTO,mic='camera.wav')):
            self.svc.driver_factory=lambda:driver
            with self.assertRaises(CleanEditError): self.approve()
            self.assertIsNone(self.state()['approved'])

    def test_shifted_video_or_audio_gap_is_rejected(self):
        for kind in ('v','a'):
            class Changed(FakeDriver):
                def premiere_ops(inner,ops,timeout=300):
                    r=super().premiere_ops(ops,timeout)
                    x=next(x for x in r[1]['items'] if x['kind']==kind)
                    x['start']=str(int(x['start'])+F)
                    return r
            self.svc.driver_factory=lambda:Changed(AUTO)
            with self.assertRaisesRegex(CleanEditError,'timing changed'): self.approve()
            self.assertIsNone(self.state()['approved'])

    def test_transcript_write_failure_does_not_record_approval(self):
        with patch.object(self.svc,'_write_version',side_effect=OSError('disk full')):
            with self.assertRaises(OSError): self.approve()
        self.assertIsNone(self.state()['approved'])

    def test_tampered_clean_transcript_blocks_planner(self):
        self.approve()
        f=Path(self.state()['clean_transcript']['file']); tx=json.loads(f.read_text())
        tx['words'][0]['start']=999
        f.write_text(json.dumps(tx))
        with self.assertRaisesRegex(CleanEditError,'transcript changed'): self.svc.animation_inputs('p1')

    def test_changed_sources_block_planner(self):
        self.approve()
        f=self.tmp/'evidence/premiere-native-words-mic.json'; tx=json.loads(f.read_text())
        tx[0]['words'][0]['word']='changed';f.write_text(json.dumps(tx))
        with self.assertRaisesRegex(CleanEditError,'sources changed'): self.svc.animation_inputs('p1')

    def test_missing_readback_does_not_approve_offline(self):
        self.svc.driver_factory=None
        with self.assertRaisesRegex(CleanEditError,'readback is unavailable'): self.approve()
        self.assertIsNone(self.state()['approved'])

    def test_partial_word_is_clipped_to_the_actual_cut_piece(self):
        f=self.tmp/'evidence/premiere-native-words-mic.json'; tx=json.loads(f.read_text())
        tx[0]['words'][1]['end']=2.1;f.write_text(json.dumps(tx))
        self.approve()
        body=json.loads(Path(self.state()['clean_transcript']['file']).read_text())
        word=next(w for w in body['words'] if w['text']=='two')
        self.assertTrue(word['partial'])
        self.assertEqual(word['end'],body['map']['pieces'][0]['out_end']/254016000000)

    def test_trim_between_validation_and_transcript_prevents_approval(self):
        class Racing(FakeDriver):
            calls=0
            def premiere_ops(inner,ops,timeout=300):
                inner.calls+=1
                if inner.calls==2: inner.pieces=AUTO[:1]+AUTO[2:]
                return super().premiere_ops(ops,timeout)
        driver=Racing(AUTO);self.svc.driver_factory=lambda:driver
        with self.assertRaisesRegex(CleanEditError,'timing changed'): self.approve()
        self.assertIsNone(self.state()['approved'])
