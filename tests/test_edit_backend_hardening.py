import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock
from studio.edit_flow import EditFlow
from studio.store import Store, Conflict
from studio.clean_edit import CleanEditError
from tests import test_clean_handoff as fixture

class ProposalHardening(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.store=Store(Path(self.tmp.name));self.pid=self.store.create('Synthetic')['id']
        self.flow=EditFlow(self.store,Path(self.tmp.name));self.flow.view=Mock(return_value={})
        self.prop=self.flow._put(self.pid,'edit_proposal',dict(events=[{'event_id':'e1'}],words=[],measurements={'samples':10},decisions={},word_actions={},notes=[],revision=1))
    def test_tariq_actor_and_owner_default(self):
        self.flow.revise(self.pid,self.prop['id'],self.prop['digest'],changes=[{'event_id':'e1','action':'remove'}],note='requested',actor='tariq')
        new=self.flow.latest(self.pid,'edit_proposal')
        self.assertEqual(new['payload']['decisions']['e1']['by'],'tariq');self.assertEqual(new['payload']['notes'][-1]['by'],'tariq')
        self.assertFalse(self.flow.approved(new))
        self.flow.revise(self.pid,new['id'],new['digest'],note='owner')
        self.assertEqual(self.flow.latest(self.pid,'edit_proposal')['payload']['notes'][-1]['by'],'josh')
    def test_invalid_actor(self):
        with self.assertRaises(ValueError):self.flow.revise(self.pid,self.prop['id'],self.prop['digest'],actor='sam')
    def test_concurrent_revisions_only_one_wins(self):
        original=self.flow._put;barrier=threading.Barrier(2);results=[]
        def put(*args,**kwargs):
            barrier.wait(timeout=5);return original(*args,**kwargs)
        self.flow._put=put
        def revise():
            try:self.flow.revise(self.pid,self.prop['id'],self.prop['digest'],note='racing',actor='tariq');results.append('ok')
            except Conflict:results.append('stale')
        threads=[threading.Thread(target=revise) for _ in range(2)]
        for t in threads:t.start()
        for t in threads:t.join(10)
        self.assertEqual(sorted(results),['ok','stale'])
        self.assertEqual(len(self.flow._all(self.pid,'edit_proposal')),2)

class TranscriptHardening(unittest.TestCase):
    def setUp(self):
        fixture.Handoff.setUp(self)
        self.svc.approve('p1','Josh',True,'AUTOCUT_RAW_r01')
    def final(self,name='FINAL'):
        return self.svc.final_transcript('p1',name)
    def tamper(self,f):
        p=Path(f['file']);body=json.loads(p.read_text(encoding='utf-8'));body['words'][0]['text']='tampered';p.write_text(json.dumps(body),encoding='utf-8')
    def test_equal_words_different_sequences_have_distinct_provenance(self):
        a=self.final('A');b=self.final('B');self.assertNotEqual(a['digest'],b['digest']);self.assertNotEqual(a['file'],b['file'])
        body=json.loads(Path(b['file']).read_text(encoding='utf-8'));self.assertEqual(body['sequence'],'B');self.assertEqual(body['project_id'],'p1')
    def test_approval_checks_real_content(self):
        f=self.final();self.tamper(f)
        with self.assertRaises(CleanEditError):self.svc.approve_final('p1',f['digest'],'Josh',True)
    def test_handoff_checks_real_content(self):
        f=self.final();self.svc.approve_final('p1',f['digest'],'Josh',True);self.tamper(f)
        with self.assertRaises(CleanEditError):self.svc.sam_handoff('p1')
    def test_changed_evidence_blocks_handoff(self):
        f=self.final();self.svc.approve_final('p1',f['digest'],'Josh',True)
        p=self.tmp/'evidence'/'after-repeat-readback.json';p.write_text(p.read_text()+' ')
        with self.assertRaises(CleanEditError):self.svc.sam_handoff('p1')
    def test_legacy_digest_requires_regeneration(self):
        f=self.final();p=Path(f['file']);body=json.loads(p.read_text(encoding='utf-8'));body.pop('schema');p.write_text(json.dumps(body),encoding='utf-8')
        with self.assertRaisesRegex(CleanEditError,'new final transcript'):self.svc.approve_final('p1',f['digest'],'Josh',True)
    def test_existing_tampered_artifact_not_overwritten(self):
        f=self.final();self.tamper(f)
        with self.assertRaises(CleanEditError):self.final()

if __name__=='__main__':unittest.main()
