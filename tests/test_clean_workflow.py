import copy,json,tempfile,unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch
from studio.store import Store,Conflict
from studio.jobs import Jobs
from studio.workflow import Workflow,file_identity
from dajoshi.timing import build_map
from dajoshi.clean_cut import reject_ranges,remap_words,digest
from dajoshi.export import export_xml

class CleanWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=Path(__file__).parent);self.root=Path(self.tmp.name)
        self.video=self.root/'source.mp4';self.video.write_bytes(b'fixture video')
        self.info=dict(path=str(self.video),duration=10,fps='30',width=1920,height=1080,channels=1,sample_rate=48000)
        self.mapping=build_map([[0,10]],'30',0,10,10)
        self.mapping.update(source_video=self.info,source_audio=self.info,sync=dict(verified=True,offset_seconds=0,timeline_offset_seconds=0))
        self.words=[dict(word=t,start=a,end=b) for t,a,b in [('So',.2,.4),('there',.5,.7),('thin',.8,1),('So',2,2.2),('the',2.3,2.5),('thing',2.6,2.9),('is',3,3.2),('clear.',3.3,3.7)]]
        self.store=Store(self.root/'Data');self.p=self.store.create('Fixture');self.p=self.store.save(self.p['id'],dict(media=dict(video=str(self.video))),1)
        self.jobs=Jobs(self.store,self.root);self.w=Workflow(self.store,self.root,self.jobs)
        self.transcript=self.root/'transcript.json';self.transcript.write_text(json.dumps([dict(words=self.words)]))
        self.sync=self.root/'sync.json';self.sync.write_text(json.dumps(self.mapping['sync']))
    def tearDown(self):self.tmp.cleanup()
    def pass1(self):
        with patch('dajoshi.media.probe',return_value=self.info):
            a=self.w.register_pass1(self.p['id'],self.p['revision'],dict(review_media=str(self.video),transcript=str(self.transcript),sync_report=str(self.sync),note='Fixture supplied review'))
        self.w.approve(self.p['id'],self.p['revision'],a['id'],a['digest'],dict(sync=True,appearance=True,words=True),'Fixture explicit review')
        return a
    def cut(self):
        self.pass1();folder=self.root/'Runs'/'fixture';folder.mkdir(parents=True)
        for name,value in [('timing_map.json',self.mapping),('transcript.json',[dict(words=self.words)]),('inputs.json',dict(video=file_identity(self.video))),('status.json',dict(state='complete'))]:
            (folder/name).write_text(json.dumps(value))
        self.store.put_job(dict(id='fixture-job',project_id=self.p['id'],path=str(folder),created_at='2026-10-16T00:00:00Z'))
        return self.w.import_cut(self.p['id'],self.p['revision'],'fixture-job')
    def test_rephrased_start_actually_absent_map_and_xml(self):
        m,ds=reject_ranges(self.mapping,[dict(start=0,end=1.5,reason='Incomplete rephrased attempt',coordinate_space='source_video_seconds')])
        self.assertEqual(m['spans'][0]['source_in_frame'],45)
        words,issues=remap_words(m,self.words);self.assertFalse(issues);self.assertEqual(words[0]['word'],'So');self.assertEqual(len(words),5)
        xml=export_xml(self.root,m,self.info,self.info,[],[])
        camera=ET.parse(xml).findall('.//sequence/media/video/track')[1]
        self.assertTrue(all(int(c.findtext('in'))>=45 for c in camera.findall('clipitem')))
        self.assertEqual(self.mapping['spans'][0]['source_in_frame'],0)
    def test_deliberate_repeat_is_preserved_without_explicit_rejection(self):
        m,_=reject_ranges(self.mapping,[]);self.assertEqual(m,self.mapping)
        words,issues=remap_words(m,self.words);self.assertEqual(len(words),8);self.assertFalse(issues)
    def test_subframe_removal_cannot_return_via_rounding(self):
        m,ds=reject_ranges(self.mapping,[dict(start=1.011,end=1.021,reason='Rejected fragment',coordinate_space='source_video_seconds')])
        self.assertEqual((ds[0]['source_in_frame'],ds[0]['source_out_frame']),(30,31))
        self.assertEqual(m['duration_frames'],299)
        self.assertEqual(m['spans'][0]['source_out_frame'],30);self.assertEqual(m['spans'][1]['source_in_frame'],31)
    def test_partial_word_requires_realignment(self):
        m,_=reject_ranges(self.mapping,[dict(start=.3,end=.35,reason='Boundary test',coordinate_space='source_video_seconds')])
        _,issues=remap_words(m,self.words);self.assertEqual(issues[0]['kind'],'partial_word')
    def test_bad_coordinate_space_rejected(self):
        with self.assertRaisesRegex(ValueError,'source_video_seconds'):reject_ranges(self.mapping,[dict(start=0,end=1,reason='x',coordinate_space='output_seconds')])
    def test_rational_rate_and_offset_preserved(self):
        info=dict(self.info,fps='30000/1001');m=build_map([[1,5]],'30000/1001',.2,10,10)
        m.update(source_video=info,source_audio=info,sync=dict(verified=True,offset_seconds=.2,timeline_offset_seconds=.2))
        n,_=reject_ranges(m,[dict(start=2,end=3,reason='x',coordinate_space='source_video_seconds')])
        self.assertEqual(n['fps'],'30000/1001');self.assertEqual(n['audio_minus_video_seconds'],.2)
    def test_pass1_required_for_import(self):
        with self.assertRaisesRegex(ValueError,'Approve'):self.w.import_cut(self.p['id'],self.p['revision'],'fixture-job')
    def test_sample_cannot_be_pass1(self):
        with patch('dajoshi.media.probe',side_effect=[self.info,dict(self.info,duration=5)]):
            with self.assertRaisesRegex(ValueError,'uncut source duration'):self.w.register_pass1(self.p['id'],self.p['revision'],dict(review_media=str(self.video),transcript=str(self.transcript),sync_report=str(self.sync),note='sample'))
    def test_new_cut_is_immutable_and_unapproved(self):
        a=self.cut();original=copy.deepcopy(a);checks=dict(semantic_sweep=True,all_joins_listened=True,transcript_checked=True,native_playback=True)
        self.w.approve(self.p['id'],self.p['revision'],a['id'],a['digest'],checks,'Fixture review only')
        self.assertTrue(self.w.approved(self.p,a))
        new=self.w.revise_cut(self.p['id'],self.p['revision'],a['id'],a['digest'],[dict(start=0,end=1.5,reason='Incomplete start',coordinate_space='source_video_seconds')])
        self.assertNotEqual(a['id'],new['id']);self.assertEqual(self.w.get(self.p['id'],a['id']),original)
        self.assertFalse(self.w.approved(self.p,new));self.assertFalse(self.w.approved(self.p,a))
        self.assertTrue((Path(new['payload']['folder'])/'EDITABLE_DRAFT.xml').is_file())
    def test_changed_file_invalidates_approval(self):
        a=self.pass1();self.video.write_bytes(b'changed original')
        self.assertFalse(self.w.approved(self.p,a))
    def test_missing_checks_and_partial_words_block_approval(self):
        a=self.cut()
        with self.assertRaisesRegex(ValueError,'every review'):self.w.approve(self.p['id'],self.p['revision'],a['id'],a['digest'],{},'test')
        a=self.w.revise_cut(self.p['id'],self.p['revision'],a['id'],a['digest'],[dict(start=.3,end=.35,reason='Boundary',coordinate_space='source_video_seconds')])
        with self.assertRaisesRegex(ValueError,'boundary issues'):self.w.approve(self.p['id'],self.p['revision'],a['id'],a['digest'],dict(semantic_sweep=True,all_joins_listened=True,transcript_checked=True,native_playback=True),'test')
    def test_stale_project_revision_blocked(self):
        with self.assertRaises(Conflict):self.w.import_cut(self.p['id'],1,'fixture-job')
    def test_reopen_and_additive_schema_preserve_project(self):
        a=self.cut();before=self.store.get(self.p['id']);w=Workflow(Store(self.root/'Data'),self.root,self.jobs)
        self.assertEqual(before,w.store.get(self.p['id']));self.assertEqual(a,w.get(self.p['id'],a['id']))
    def test_tampered_output_blocks_approval(self):
        a=self.cut();(Path(a['payload']['folder'])/'timing_map.json').write_text('{}')
        self.assertEqual(self.w.health(self.p,a),'Clean-cut artifact changed on disk')

if __name__=='__main__':unittest.main(verbosity=2)
