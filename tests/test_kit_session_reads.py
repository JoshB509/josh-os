"""Exact session admission and email-specific audience evidence; no live service."""
import hashlib,json,os,tempfile,time,unittest
from pathlib import Path
from studio.kit_chrome_upload_guard import allowed_read
from studio.kit_chrome_gate import publish_policy,retire_policy
from studio.kit_drafts import KitDrafts,GET,CREATE
from tests.test_kit_drafts import Base,confirmed
from tests import test_kit_visual_insertions as insertion_fixtures
from studio.kit_chrome_upload import stage_session_assets

class ReadAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name).resolve();self.audit=self.root/'audit';self.audit.mkdir()
        self.png=self.root/'exact.png';self.png.write_bytes(b'exact fixture bytes');self.sha=hashlib.sha256(self.png.read_bytes()).hexdigest()
        (self.audit/'admitted-pngs.json').write_text(json.dumps({str(self.png):self.sha}))
        self.policy=dict(id='fixture',tool='mcp__claude-in-chrome__file_upload',args=dict(tabId=12,ref='ref_9',paths=[str(self.png)]),expires=time.time()+60)
        publish_policy(self.audit,self.policy)
    def event(self,file=None):return dict(tool_name='Read',tool_input=dict(file_path=str(file or self.png)))
    def test_exact_read_only_during_bound_upload(self):
        self.assertTrue(allowed_read(self.audit,self.event()))
        self.assertFalse(allowed_read(self.audit,dict(tool_name='Bash',tool_input={})))
        retire_policy(self.audit,'fixture')
        with self.assertRaises(ValueError):allowed_read(self.audit,self.event())
    def test_hash_change_or_unadmitted_path_fails(self):
        other=self.root/'other.png';other.write_bytes(self.png.read_bytes())
        self.assertFalse(allowed_read(self.audit,self.event(other)))
        self.png.write_bytes(b'changed');self.assertFalse(allowed_read(self.audit,self.event()))
    def test_known_file_outside_this_call_is_not_readable(self):
        retire_policy(self.audit,'fixture');publish_policy(self.audit,dict(self.policy,id='second',args=dict(tabId=12,ref='ref_9',paths=[])))
        self.assertFalse(allowed_read(self.audit,self.event()))
    def test_nonupload_call_does_not_open_read_permission(self):
        retire_policy(self.audit,'fixture');publish_policy(self.audit,dict(self.policy,id='second',tool='mcp__claude-in-chrome__read_page'))
        self.assertFalse(allowed_read(self.audit,self.event()))
    def test_multiple_hardlinks_rejected(self):
        other=self.root/'hardlink.png';os.link(self.png,other)
        self.assertFalse(allowed_read(self.audit,self.event()))
    def test_changed_published_permit_is_rejected(self):
        path=self.audit/'fixture.policy.json';policy=json.loads(path.read_text());policy['args']['ref']='ref_99';path.write_text(json.dumps(policy))
        with self.assertRaises(ValueError):allowed_read(self.audit,self.event())

class AudiencePersistenceTests(Base):
    def test_different_email_rules_are_persisted_independently(self):
        one={'all':[{'type':'segment','ids':[101]}]};two={'none':[{'type':'tag','ids':[202]}]}
        second=self.emails.create(dict(changes=dict(subject='Second email',body='Second body.')))
        a=self.drafts.propose(self.fresh(),one);b=self.drafts.propose(second,two)
        reread=KitDrafts(self.store,self.root)
        self.assertEqual(reread.get(a['id'])['payload']['subscriber_filter'],[one]);self.assertEqual(reread.get(b['id'])['payload']['subscriber_filter'],[two])
        self.assertEqual((b['email_id'],b['email_revision']),(second['id'],second['revision']))
        self.assertEqual(second['audience'],'')
    def test_unspecified_is_unresolved_even_after_another_proposal(self):
        self.drafts.propose(self.fresh(),{'all':[{'type':'segment','ids':[607863]}]})
        second=self.emails.create(dict(changes=dict(subject='No audience supplied',body='Body.')))
        for missing in (None,{},'',[]):
            with self.assertRaises(ValueError):self.drafts.propose(second,missing)
        self.assertEqual(self.drafts.for_email(second['id']),[]);self.assertEqual(self.emails.get(second['id'])['audience'],'')
    def test_exact_audience_transfer_and_verified_readback_persisted_without_date(self):
        email=self.emails.save(self.email['id'],dict(planned_date='2099-01-05',send_time='17:00',audience='Explicit fixture segment 202'),self.email['revision'],sync_remote=False)
        rule={'all':[{'type':'segment','ids':[202]}]};d=self.drafts.propose(email,rule)
        d=self.drafts.approve(d['id'],d['hash'],email,origin=dict(channel='fixture-owner',message_id='specific-email',actor_id='josh'))
        def run(*args,**kw):
            payload=kw['guard']['write']['input'];self.assertEqual(payload['subscriber_filter'],[rule])
            for field in ('send_at','published_at','scheduled_at'):self.assertNotIn(field,payload)
            return 'ok',confirmed(kw['guard'])
        result=self.drafts.write(d['id'],email,run,request=d['authorization']);saved=self.drafts.get(d['id'])
        self.assertEqual(saved['verified_subscriber_filter'],[rule]);self.assertEqual(saved['payload']['subscriber_filter'],[rule]);self.assertIsNone(saved['verified_send_at'])
        self.assertEqual(self.emails.get(email['id']),email)
    def test_wrong_audience_readback_is_uncertain_without_verified_selection(self):
        rule={'all':[{'type':'segment','ids':[202]}]};d=self.drafts.propose(self.fresh(),rule)
        d=self.drafts.approve(d['id'],d['hash'],self.fresh(),origin=dict(channel='fixture',message_id='specific-email',actor_id='josh'))
        def run(*args,**kw):return 'bad',confirmed(kw['guard'],subscriber_filter=[{'all':[{'type':'segment','ids':[607863]}]}])
        with self.assertRaises(ValueError):self.drafts.write(d['id'],self.fresh(),run,request=d['authorization'])
        saved=self.drafts.get(d['id']);self.assertEqual(saved['status'],'uncertain');self.assertNotIn('verified_subscriber_filter',saved)

class SessionCopyTests(unittest.TestCase):
    def test_only_approved_originals_are_copied_with_one_link(self):
        f=insertion_fixtures.InsertionTests();f.setUp();self.addCleanup(f.doCleanups);self.addCleanup(f.tearDown)
        row=f.approve();folder=stage_session_assets(f.service,row['id']);files=list(folder.iterdir())
        self.assertEqual(len(files),1);self.assertEqual(files[0].read_bytes(),f.png);self.assertEqual(files[0].stat().st_nlink,1)
        self.assertEqual(stage_session_assets(f.service,row['id']),folder)
        (folder/'unrelated').write_text('unexpected')
        with self.assertRaises(ValueError):stage_session_assets(f.service,row['id'])
        self.assertEqual(f.browser.writes,[])

if __name__=='__main__':unittest.main()
