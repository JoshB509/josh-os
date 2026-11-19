import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, Mock
from studio.store import Store, Conflict
from studio.team import Team
from studio.agent import Agents
from studio.release_ready import Release
from studio.chat_release_actions import ChatReleaseActions
from tests import test_chapters as chapter_tests

class ReleaseChat(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.store=Store(self.root/'Data');self.team=Team(self.store)
        self.agents=Agents(self.store,self.root,self.team)
        self.p=self.store.create('Exact video',initial={'script':'Original'})
        self.rel=Release(self.store,agents=self.agents);self.adapter=ChatReleaseActions(self.rel)
        self.agents.chat_release_actions=self.adapter
    def payload(self,action='publish_time'):
        return dict(action=action,project_id=self.p['id'],revision=self.store.get(self.p['id'])['revision'],time='17:00',timezone='Asia/Dubai')
    def test_shared_handler_sam_discord_without_open_window(self):
        with patch.object(self.agents,'run',return_value=('Working',{'local_release_action':self.payload()})):
            out=self.agents.chat({'agent_id':'sam','message':'Set local planned time for Exact video to 17:00 Asia/Dubai','via':'discord','discord':{'message_id':'1','channel_id':'2','author_id':'3','is_dm':True}},[])
        self.assertIn('Nothing scheduled',out['reply']['text'])
        r=self.store.get(self.p['id'])['release'];self.assertEqual(r['publish_time']['time'],'17:00');self.assertNotIn('export_approval',r)
    def test_other_role_injection_cannot_execute(self):
        seen={}
        def run(a,b,s,*args,**kw):seen.update(s);return '',{'local_release_action':self.payload()}
        with patch.object(self.agents,'run',side_effect=run):self.agents.chat({'agent_id':'anouk','message':'Set local planned time for Exact video to 17:00 Asia/Dubai'},[])
        self.assertNotIn('local_release_action',seen['properties']);self.assertNotIn('publish_time',self.store.get(self.p['id']).get('release',{}))
    def test_unknown_or_ambiguous_target_does_not_write(self):
        self.store.create('Exact video')
        with patch.object(self.agents,'run',return_value=('',{'local_release_action':self.payload()})):
            out=self.agents.chat({'agent_id':'sam','message':'Set local planned time for Exact video to 17:00 Asia/Dubai'},[])
        self.assertIn('not completed',out['reply']['text']);self.assertNotIn('publish_time',self.store.get(self.p['id']).get('release',{}))
    def test_stale_negative_wrong_zone_unknown_fields_refused(self):
        for change,msg in [({'revision':0},'set local time 17:00 Asia/Dubai'),({},"don't set local time 17:00 Asia/Dubai"),({'timezone':'Mars/Orbit'},'set local time 17:00 Mars/Orbit'),({'approved':True},'set local time 17:00 Asia/Dubai')]:
            args=self.payload();args.update(change)
            with self.assertRaises(ValueError):self.adapter.dispatch('sam',args,project_id=self.p['id'],message=msg)
    def test_model_action_cannot_schedule_or_approve(self):
        for action in ['approve_export','schedule','publish','upload']:
            with self.assertRaises(ValueError):self.adapter.dispatch('sam',self.payload(action),project_id=self.p['id'],message='publish it')
    def test_packaging_rejects_stale_result_preserves_user_edits(self):
        def run(*a,**kw):
            p=self.store.get(self.p['id']);self.store.save(p['id'],{'script':'Josh latest'},p['revision'])
            return '',{'description':'stale','cta':'stale'}
        with self.assertRaises(Conflict):self.rel.draft_packaging(self.p['id'],run=run)
        p=self.store.get(self.p['id']);self.assertEqual(p['script'],'Josh latest');self.assertNotIn('description',p.get('release',{}))
    def test_packaging_preserves_unrelated_approvals_not_new_draft_approval(self):
        p=self.store.get(self.p['id']);self.store.save(p['id'],{'release':{'description':{'text':'old','approved':{'sha':'x'}},'export_approval':{'path':'existing'}}},p['revision'])
        out=self.rel.draft_packaging(p['id'],run=lambda *a,**k:('',{'description':'New','cta':'Review'}))
        self.assertNotIn('approved',out['release']['description']);self.assertEqual(out['release']['export_approval']['path'],'existing')
    def test_chapters_adapter_always_preserves_preview_gate(self):
        self.rel.chapters=Mock();self.rel.chapters.prepare.return_value={'preview':True}
        args=self.payload('chapters');out=self.adapter.dispatch('sam',args,project_id=self.p['id'],message='Prepare chapters for Exact video')
        self.rel.chapters.prepare.assert_called_once_with(self.p['id'],preview=True,expected_revision=1)
        self.assertIn('preview',out['note'])

class ChapterStale(unittest.TestCase):
    def setUp(self):
        self.fixture=chapter_tests.Binding();self.fixture.setUp();self.addCleanup(self.fixture.tearDown)
    def test_project_change_during_generation_does_not_save(self):
        f=self.fixture
        def run(*a,**kw):
            p=f.store.get(f.p['id']);f.store.save(p['id'],{'notes':'New owner edit'},p['revision'])
            return f.sam(*a,**kw)
        with self.assertRaises(Conflict):f.c.prepare(f.p['id'],run=run,preview=True)
        self.assertNotIn('chapters',f.store.get(f.p['id']).get('release',{}))
    def test_export_change_during_generation_does_not_save(self):
        f=self.fixture
        def run(*a,**kw):
            f.sha='changed';f.export.write_bytes(b'changed export bytes');return f.sam(*a,**kw)
        with self.assertRaises(Conflict):f.c.prepare(f.p['id'],run=run,preview=True)
        self.assertNotIn('chapters',f.store.get(f.p['id']).get('release',{}))
