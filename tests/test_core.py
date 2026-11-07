import unittest,tempfile,json
from pathlib import Path
from fractions import Fraction
import numpy as np
from dajoshi.timing import *
from dajoshi.editor import plan
from dajoshi.media import sync
from dajoshi.export import export_xml,local_file_url
import xml.etree.ElementTree as E

class TimingTests(unittest.TestCase):
    def test_ntsc_no_accumulating_drift(self):
        fps='30000/1001';keeps=[[i*2+.1,i*2+1.3] for i in range(300)]
        m=build_map(keeps,fps,1.5,1000,1002)
        self.assertEqual(m['duration_frames'],sum(s['source_out_frame']-s['source_in_frame'] for s in m['spans']))
        for a,b in zip(m['spans'],m['spans'][1:]):self.assertEqual(a['out_out_frame'],b['out_in_frame'])
    def test_removed_time_never_snaps_cue(self):
        m=build_map([[1,2],[4,5]],25,0,10,10)
        self.assertIsNone(map_source(m,3));self.assertEqual(map_source(m,4),25)
        self.assertEqual(map_interval(m,1.5,4.5),[(13,25),(25,38)])
    def test_arbitrary_rational_map(self):
        for fps in ['24','24000/1001','25','30','30000/1001','50','60000/1001','55/2','120']:
            m=build_map([[.13,5.7]],fps,.27,10,10)
            self.assertLessEqual(float(Fraction(m['spans'][0]['source_in_frame'],1)/rate(fps)),.13)
    def test_negative_offset_clamped(self):
        m=build_map([[0,2]],30,-.51,4,4);self.assertGreaterEqual(m['spans'][0]['audio_in_seconds'],0)
    def test_round_overlap_is_merged(self):
        m=build_map([[0,.99],[1.01,2]],25,0,3,3);self.assertEqual(len(m['spans']),1)
    def test_overrides(self):self.assertEqual(subtract([[0,10]],[[2,3],[5,8]]),[[0,2],[3,5],[8,10]])

class EditorTests(unittest.TestCase):
    def words(self,text,start,step=.3):return [dict(word=w,start=start+i*step,end=start+i*step+.2,probability=.99) for i,w in enumerate(text.split())]
    def pcm(self,words):
        x=np.zeros(20*16000,dtype=np.float32)
        for w in words:x[int(w['start']*16000):int(w['end']*16000)]=.1
        return x
    def test_speech_onsets_and_tails(self):
        words=self.words('And the example ends',1)+self.words('Next take starts',5)
        keeps,decisions,flags,_=plan(words,self.pcm(words),16000,0,0,10)
        for w in words:self.assertTrue(any(a<=w['start'] and b>=w['end'] for a,b in keeps))
        self.assertTrue(any(d['kind']=='silence' for d in decisions))
    def test_strong_prefix_restart_removed(self):
        words=self.words('We need the',1)+self.words('We need the complete version now',4)
        keeps,decisions,flags,_=plan(words,self.pcm(words),16000,0,0,10)
        self.assertTrue(any(d['kind']=='short_restart' for d in decisions));self.assertFalse(any(a<=1.2<=b for a,b in keeps))
    def test_identical_rhetorical_repetition_retained(self):
        words=self.words('We can do it',1)+self.words('We can do it',4)
        keeps,decisions,flags,_=plan(words,self.pcm(words),16000,0,0,10)
        self.assertFalse(any(d['kind']=='short_restart' for d in decisions));self.assertTrue(any(f['kind']=='possible_retake' for f in flags))
    def test_untranscribed_audio_retained(self):
        words=self.words('First clause',1)+self.words('Second clause',5);pcm=self.pcm(words);pcm[3*16000:4*16000]=.1
        keeps,decisions,flags,_=plan(words,pcm,16000,0,0,10)
        self.assertTrue(any(a<=3.5<=b for a,b in keeps));self.assertTrue(any(f['kind']=='untranscribed_sound' for f in flags))
    def test_explicit_restore_beats_automatic_removal(self):
        words=self.words('We need the',1)+self.words('We need the complete version',4)
        keeps,*_=plan(words,self.pcm(words),16000,0,0,10,{'keep':[[.7,2.2]]})
        self.assertTrue(any(a<=1.2<=b for a,b in keeps))

class SyncTests(unittest.TestCase):
    def test_measured_positive_and_negative_lag(self):
        rng=np.random.default_rng(3);blocks=rng.uniform(.001,.5,6000);cam=np.repeat(blocks,160).astype('float32')
        for lag in [1.78,-.63]:
            n=int(abs(lag)*16000)
            mic=np.r_[np.zeros(n),cam] if lag>0 else cam[n:]
            result=sync(cam,mic)
            self.assertTrue(result['verified']);self.assertAlmostEqual(result['offset_seconds'],lag,places=2)

class ExportTests(unittest.TestCase):
    def test_original_paths_handles_links_and_ntsc(self):
        with tempfile.TemporaryDirectory() as d:
            v=dict(path=str(Path(d)/'source.mov'),duration=60,fps='30000/1001',width=1920,height=1080,channels=0,sample_rate=None)
            a=dict(path=str(Path(d)/'mic.wav'),duration=62,fps=None,width=0,height=0,channels=1,sample_rate=48000)
            m=build_map([[10,12],[20,22]],v['fps'],1,60,62)
            # Semantic validation deliberately requires existing local files.
            Path(v['path']).touch();Path(a['path']).touch()
            p=export_xml(d,m,v,a,[],[]);root=E.parse(p)
            self.assertEqual(root.findtext('.//sequence/rate/ntsc'),'TRUE')
            paths=[x.text for x in root.findall('.//pathurl')];self.assertIn(local_file_url(v['path']),paths)
            self.assertGreater(int(root.findtext('.//clipitem/in')),0)
            self.assertEqual(len(root.findall('.//media/video/track/clipitem')),2)
            ids={e.get('id') for e in root.findall('.//clipitem')}
            for link in root.findall('.//linkclipref'):self.assertIn(link.text,ids)
    def test_unsupported_xml_rate_is_explicit(self):
        from dajoshi.export import xml_rate
        with self.assertRaises(ValueError):xml_rate(E.Element('rate'),'55/2')

if __name__=='__main__':unittest.main()
