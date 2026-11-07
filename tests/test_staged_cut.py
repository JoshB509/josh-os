import copy,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import numpy as np
from dajoshi.staged_cut import silence_map,join_audio,sample_map,context
from dajoshi.clean_cut import remap_words,digest
from dajoshi.timing import build_map
from studio.store import Conflict
from tests import test_clean_workflow as fixture

class AudioTests(unittest.TestCase):
    def mapping(self,fps='25',keeps=None):
        m=build_map(keeps or [[0,2],[3,5]],fps,0,6,6)
        info=dict(path='fixture',fps=fps,duration=6)
        m.update(source_video=info,source_audio=info,sync=dict(verified=True,offset_seconds=0,timeline_offset_seconds=0))
        return m
    def test_silence_only_preserves_repeated_words_and_untranscribed_sound(self):
        pcm=np.zeros(6*16000,dtype=np.float32)
        words=[dict(word='Repeat',start=.4,end=.6),dict(word='Repeat',start=3.4,end=3.6)]
        for a,b in [(.35,.65),(1.5,1.8),(3.35,3.65)]:pcm[int(a*16000):int(b*16000)]=.1
        info=dict(path='fixture',fps='25',duration=6)
        m,decisions=silence_map(info,info,words,pcm,0);m['sync']=dict(verified=True,offset_seconds=0)
        mapped,issues=remap_words(m,words);self.assertEqual(len(mapped),2);self.assertFalse(issues)
        self.assertTrue(any(s['source_in_frame']<=38 and s['source_out_frame']>=45 for s in m['spans']))
        self.assertLess(m['duration_frames'],150);self.assertTrue(all(x['kind']=='measured_silence' for x in decisions['automatic']))
    def test_disabled_fade_is_exact_sample_concatenation(self):
        m=self.mapping();pcm=np.arange(6000,dtype=np.float32)/10000
        out,report=join_audio(m,pcm,1000,[],[],0)
        np.testing.assert_array_equal(out[:,0],np.r_[pcm[:2000],pcm[3000:5000]])
        self.assertFalse(report['xml_audio_fades_supported'])
    def test_quiet_fade_uses_handles_without_changing_clock(self):
        m=self.mapping();before=copy.deepcopy(m);pcm=np.zeros(6000,dtype=np.float32);pcm[1990:2010]=.001;pcm[2990:3010]=-.001
        out,r=join_audio(m,pcm,1000,[],[],10)
        self.assertEqual(len(out),4000);self.assertEqual(m,before);self.assertTrue(r['joins'][0]['applied'])
        np.testing.assert_array_equal(out[:1995,0],pcm[:1995]);np.testing.assert_array_equal(out[2005:,0],pcm[3005:5000])
    def test_word_touching_handle_forces_hard_join(self):
        out,r=join_audio(self.mapping(),np.zeros(6000),1000,[dict(start=1.99,end=2.02)],[],10)
        self.assertFalse(r['joins'][0]['applied']);self.assertIn('word',r['joins'][0]['reason'])
    def test_rejected_speech_cannot_return_via_handle(self):
        d=dict(start=2,end=2.2,source_in_frame=50,source_out_frame=55)
        _,r=join_audio(self.mapping(),np.zeros(6000),1000,[],[d],10)
        self.assertFalse(r['joins'][0]['applied']);self.assertIn('rejected',r['joins'][0]['reason'])
    def test_loud_or_overlapping_handles_are_not_faded(self):
        _,r=join_audio(self.mapping(),np.ones(6000)*.02,1000,[],[],10);self.assertFalse(r['joins'][0]['applied'])
        _,r=join_audio(self.mapping('1000',[[0,2],[2.004,5]]),np.zeros(6000),1000,[],[],10);self.assertFalse(r['joins'][0]['applied'])
    def test_rational_rate_sample_count_no_accumulating_drift(self):
        m=self.mapping('30000/1001',[[.11,.9],[1.11,1.9],[2.11,2.9],[3.11,3.9],[4.11,4.9]])
        before=copy.deepcopy(m);rows,total=sample_map(m,48000)
        out,_=join_audio(m,np.zeros(6*48000),48000,[],[],0)
        self.assertEqual(len(out),total);self.assertEqual(total,round(m['duration_frames']*48000*1001/30000));self.assertEqual(m,before)
        self.assertTrue(all(x['padding_samples']<=1 for x in rows))
    def test_invalid_fades_rejected(self):
        for value in (-1,21,float('nan'),True):
            with self.assertRaises(ValueError):join_audio(self.mapping(),np.zeros(6000),1000,[],[],value)

class StagedGateTests(unittest.TestCase):
    setUp=fixture.CleanWorkflowTests.setUp
    tearDown=fixture.CleanWorkflowTests.tearDown
    pass1=fixture.CleanWorkflowTests.pass1
    cut=fixture.CleanWorkflowTests.cut
    def test_launch_requires_pass1_and_rejects_old_revision(self):
        with self.assertRaisesRegex(ValueError,'Approve'):self.w.launch(self.p['id'],self.p['revision'],'silence')
        with self.assertRaises(Conflict):self.w.launch(self.p['id'],1,'silence')
    def test_launch_binds_snapshot_without_changing_project(self):
        a=self.pass1();before=self.store.get(self.p['id'])
        with patch.object(self.jobs,'_spawn'):
            j=self.w.launch(self.p['id'],self.p['revision'],'silence')
        cfg=json.loads(Path(j['request']).read_text());self.assertEqual(cfg['workflow']['digest'],a['digest']);self.assertEqual(before,self.store.get(self.p['id']))
        self.assertEqual(context(cfg,self.root)[2]['id'],a['id'])
        self.store.save(self.p['id'],{'notes':'Changed'},self.p['revision'])
        with self.assertRaises(Conflict):context(cfg,self.root)
    def test_approved_manual_cut_blocks_auto_recut(self):
        a=self.cut();self.w.approve(self.p['id'],self.p['revision'],a['id'],a['digest'],dict(semantic_sweep=True,all_joins_listened=True,transcript_checked=True,native_playback=True),'Fixture review')
        with self.assertRaisesRegex(Conflict,'owns timing'):self.w.launch(self.p['id'],self.p['revision'],'silence')
    def test_review_job_binds_exact_latest_candidate(self):
        a=self.cut();new=self.w.revise_cut(self.p['id'],self.p['revision'],a['id'],a['digest'],[dict(start=0,end=1.5,reason='Fragment',coordinate_space='source_video_seconds')])
        with self.assertRaisesRegex(Conflict,'latest'):self.w.launch(self.p['id'],self.p['revision'],'review',a['id'],a['digest'])
        with patch.object(self.jobs,'_spawn'):
            job=self.w.launch(self.p['id'],self.p['revision'],'review',new['id'],new['digest'])
        self.assertEqual(job['artifact_digest'],new['digest'])
    def test_manual_timing_spans_preserved(self):
        self.pass1();m=copy.deepcopy(self.mapping);m['spans'][0].update(source_in_frame=45,out_out_frame=255,audio_in_seconds=1.5);m['duration_frames']=255
        with patch('dajoshi.media.probe',return_value=self.info):a=self.w.import_map(self.p['id'],self.p['revision'],m,self.w.source(self.p)['files'],'Manually polished fixture')
        got=json.loads((Path(a['payload']['folder'])/'timing_map.json').read_text())
        self.assertEqual(got['spans'],m['spans']);self.assertEqual(got['duration_frames'],m['duration_frames']);self.assertFalse(a['payload']['provenance']['timing_replanned'])
    def test_semantic_removal_evidence_is_actual(self):
        a=self.cut();new=self.w.revise_cut(self.p['id'],self.p['revision'],a['id'],a['digest'],[dict(start=0,end=1.5,reason='Fragment',coordinate_space='source_video_seconds')])
        evidence=json.loads((Path(new['payload']['folder'])/'semantic_decisions.json').read_text())
        self.assertTrue(evidence['actual_omission_verified']);self.assertEqual(evidence['result_map_digest'],new['payload']['map_digest'])

if __name__=='__main__':unittest.main(verbosity=2)
