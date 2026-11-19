"""Local-only chat card parity. Never calls a model, Discord or publishing service."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from studio.store import Store
from studio.team import Team
from studio.agent import Agents, PROJECT_PROPS

class ChatCardCreationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store = Store(self.root / 'Data')
        self.team = Team(self.store)
        self.agents = Agents(self.store, self.root, self.team)
        self.old = self.store.create('Original idea', initial={'script': 'Original script'})

    def test_create_preserves_old_and_returns_verified_card(self):
        out = self.agents.create_projects([{'name': 'Communication 86%', 'script': 'New script', 'add_titles': ['One', 'Two']}], 'one')
        self.assertEqual(out[0]['action'], 'added')
        p = self.store.get(out[0]['id'])
        self.assertEqual((p['name'],p['script'],p['stage']), ('Communication 86%','New script','Idea'))
        self.assertEqual(self.store.get(self.old['id']), self.old)

    def test_replay_never_duplicates_or_overwrites(self):
        one = self.agents.create_projects([{'name':'New', 'script':'First'}], 'same')[0]
        edited = self.store.save(one['id'], {'script':'Josh edited'}, 1)
        two = self.agents.create_projects([{'name':'New changed', 'script':'Overwrite'}], 'same')[0]
        self.assertEqual(one['id'], two['id'])
        self.assertEqual(len(self.store.projects()), 2)
        self.assertEqual(self.store.get(one['id']), edited)

    def test_invalid_input_leaves_no_partial_card(self):
        for item in ({'name':'X','script':None}, {'name':'X','publish_date':'yesterday'},
                     {'name':'X','project_id':self.old['id']}, {'name':'X','stage':'Published'},
                     {'name':'X','script':'a'*200001}, {'name':' '}, {'name':'X','add_titles':[4]}):
            self.assertEqual(self.agents.create_projects([item])[0]['action'], 'failed')
        self.assertEqual(len(self.store.projects()), 1)

    def test_same_name_creates_distinct_card_not_old(self):
        new = self.agents.create_projects([{'name':'Original idea'}], 'different')[0]
        self.assertNotEqual(new['id'],self.old['id'])
        self.assertEqual(self.store.get(self.old['id']),self.old)

    def test_each_role_gets_same_creation_action_on_each_surface(self):
        for aid in ('sam','tariq','laia','saskia','anouk','max'):
            for via in ('', 'discord'):
                seen = {}
                def run(system, prompt, schema, *args, **kwargs):
                    seen['schema'] = schema
                    return 'Prepared.', {'new_projects':[{'name':aid + via + ' card'}]}
                with patch.object(self.agents, 'run', side_effect=run):
                    result=self.agents.chat({'agent_id':aid,'message':'Create a new video idea card','via':via,
                        'discord':{'message_id':aid+'123','channel_id':'456','author_id':'789','is_dm':True}}, [])
                if aid not in ('sam', 'tariq'):
                    self.assertNotIn('new_projects', seen['schema']['properties'])
                    self.assertNotIn('saved', result['reply'])
                    continue
                self.assertIn('new_projects', seen['schema']['properties'])
                saved=result['reply']['saved'][0]
                self.assertEqual(saved['action'],'added')
                self.assertIn(saved['id'],result['reply']['text'])
                self.assertEqual(self.store.get(saved['id'])['stage'],'Idea')

    def test_new_request_cannot_repurpose_existing_card(self):
        with patch.object(self.agents, 'run', return_value=('Saved', {'projects':[{'project_id':self.old['id'], 'name':'New', 'script':'Wrong'}]})):
            out=self.agents.chat({'agent_id':'sam','message':'Create a new video card'}, [])
        self.assertEqual(self.store.get(self.old['id']),self.old)
        self.assertIn('left unchanged',out['reply']['text'])

    def test_concurrent_update_is_not_overwritten(self):
        self.store.save(self.old['id'], {'script':'Josh just changed it'}, self.old['revision'])
        result=self.agents.apply_projects([{'project_id':self.old['id'],'script':'Stale'}], {self.old['id']:self.old['revision']})
        self.assertEqual(result[0]['action'],'failed')
        self.assertEqual(self.store.get(self.old['id'])['script'],'Josh just changed it')

    def test_customer_success_cannot_execute_course_authoring(self):
        from unittest.mock import Mock
        courses=Mock()
        self.agents.courses=courses
        seen={}
        def run(system,prompt,schema,*args,**kwargs):
            seen['schema']=schema
            return 'Read only.', {'lessons':[{'id':'x','publish':True}]}
        with patch.object(self.agents,'lesson_snapshot',return_value='Course read context'), patch.object(self.agents,'run',side_effect=run):
            self.agents.chat({'agent_id':'anouk','message':'Check learner progress'}, [])
        self.assertNotIn('lessons',seen['schema']['properties'])
        courses.publish.assert_not_called()
        courses.save.assert_not_called()

    def test_project_metadata_clear_select_remove_and_restore(self):
        import uuid
        a,b=str(uuid.uuid4()),str(uuid.uuid4())
        current=self.store.save(self.old['id'],{'titles':[{'id':a,'text':'A'},{'id':b,'text':'B'}], 'chosen_title':a,'notes':'old'},1)
        out=self.agents.apply_projects([{'project_id':current['id'],'script':'','notes':'','chosen_title':b,
            'remove_title_ids':[a],'packaging':{'concrete_payoff':'Clearer work'},'lead_magnet':{'decision':'none'},'archived':True}])
        self.assertEqual(out[0]['action'],'updated')
        after=self.store.get(current['id'])
        self.assertEqual((after['script'],after['notes'],after['chosen_title']),('','',b))
        self.assertEqual(len(after['titles']),1)
        self.assertTrue(after['archived'])
        self.agents.apply_projects([{'project_id':current['id'],'archived':False}])
        self.assertFalse(self.store.get(current['id'])['archived'])

    def test_locked_title_is_not_removed_or_changed(self):
        import uuid
        tid=str(uuid.uuid4())
        p=self.store.save(self.old['id'],{'titles':[{'id':tid,'text':'Locked'}],'chosen_title':tid,'locks':{'title':'2026-11-15'}},1)
        out=self.agents.apply_projects([{'project_id':p['id'],'remove_title_ids':[tid]}])
        self.assertEqual(out[0]['action'],'failed')
        self.assertEqual(self.store.get(p['id']),p)

    def test_laia_metadata_saves_local_only(self):
        from studio.emails import Emails
        from unittest.mock import Mock
        mails=Emails(self.store,self.root)
        email=mails.create({'changes':{'subject':'A draft'}})
        mails.kit_step=Mock(side_effect=AssertionError('Must not use remote sync'))
        out=self.agents.apply_emails(mails,[{'id':email['id'],'audience':'New buyers','tags':['onboarding'],'archived':True,'type':'Newsletter'}])
        self.assertEqual(out[0]['action'],'updated')
        result=mails.get(email['id'])
        self.assertEqual(result['audience'],'New buyers')
        self.assertEqual(result['status'],'Draft')
        self.assertIsNone(result['kit'])
        mails.kit_step.assert_not_called()

    def test_read_refresh_role_and_request(self):
        from unittest.mock import Mock
        self.agents.learners=Mock()
        self.agents.learners.all.return_value={'items':{},'running':{}}
        self.agents.learners.lookup.return_value={'state':'running'}
        self.agents.scorecard=Mock()
        self.agents.scorecard.refresh.return_value={'state':'started'}
        for aid in ('sam','max','anouk'):
            with patch.object(self.agents,'run',return_value=('Okay',{'refresh_scorecard':True,'lookup_learner':{'email':'test@example.com'}})):
                self.agents.chat({'agent_id':aid,'message':'Refresh the requested data'},[])
        self.agents.learners.lookup.assert_called_once()
        self.agents.scorecard.refresh.assert_called_once()
        with patch.object(self.agents,'run',return_value=('Okay',{'refresh_scorecard':True})):
            self.agents.chat({'agent_id':'max','message':'Do not refresh the data'},[])
        self.agents.scorecard.refresh.assert_called_once()

    def test_tariq_adapter_is_shared_in_discord_without_open_page(self):
        from unittest.mock import Mock
        self.agents.chat_edit_actions=Mock()
        self.agents.chat_edit_actions.context.return_value={'revision':'r1','project_id':self.old['id']}
        self.agents.chat_edit_actions.dispatch.return_value={'action':'clean_transcript','result':{'ready':True}}
        action={'action':'clean_transcript','project_id':self.old['id'],'revision':'r1'}
        with patch.object(self.agents,'run',return_value=('Working',{'chat_edit_action':action})):
            self.agents.chat({'agent_id':'tariq','message':'Generate clean transcript for Original idea','via':'discord'},[])
        self.agents.chat_edit_actions.dispatch.assert_called_once_with(action,role='tariq',project_id=self.old['id'],current_message='Generate clean transcript for Original idea')

    def test_course_action_resolves_unique_saved_title_from_discord(self):
        import threading
        from unittest.mock import Mock
        from studio.chat_course_actions import course_action_context
        courses=Mock();courses.lock=threading.Lock();courses.recording=None
        courses.all.return_value={'items':[{'id':'lesson-1','title':'Clear recommendations'}]}
        courses._get.return_value={'id':'lesson-1','revision':3,'script':'Current script'}
        courses.cues.return_value='Old cues';courses.save_cues.return_value={'cues':True}
        self.agents.courses=courses
        action=dict(course_action_context(courses,'lesson-1'),action='cues',text='New cues')
        with patch.object(self.agents,'lesson_snapshot',return_value='Course read context'), patch.object(self.agents,'run',return_value=('Working',{'course_action':action})):
            out=self.agents.chat({'agent_id':'saskia','message':'save cues Clear recommendations: New cues','via':'discord'},[])
        courses.save_cues.assert_called_once_with('lesson-1','New cues')
        self.assertIn('Course action:',out['reply']['text'])

    def test_explicit_intent_required(self):
        from studio.agent import requests_new_project
        self.assertTrue(requests_new_project("Make a video called Communication. I'll improve it 86%"))
        self.assertFalse(requests_new_project("Don't create a new video card"))
        self.assertFalse(requests_new_project('What do you think of the idea?'))
        self.assertFalse(requests_new_project('Make this video more concise'))
        self.assertFalse(requests_new_project('Save the video notes'))

    def test_snapshots_include_exact_ids_for_non_youtube_roles(self):
        self.assertIn(self.old['id'], self.agents.desk_snapshot('laia', []))

if __name__ == '__main__':
    unittest.main()
