"""Exact owner delegation and Laia dispatch with existing mock insertion service."""
import copy,json,time,unittest,uuid
from pathlib import Path
from unittest.mock import patch
from studio.kit_visual_dispatch import KitVisualDispatch,DELEGATE
from studio.kit_visual_insertions import OwnerReview
from studio.kit_visual_review import KitVisualReview
from studio.agent import show_only,CHROME,Agents
from studio.team import Team
from tests import test_kit_visual_insertions as fixtures
from tests.test_kit_visual_review import Handler

class DispatchTests(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.InsertionTests();self.f.setUp();self.addCleanup(self.f.doCleanups);self.addCleanup(self.f.tearDown)
        self.service=self.f.service;self.dispatch=KitVisualDispatch(self.service);self.row=self.f.approve()
        p=self.row['packet'];self.args=dict(packet_id=self.row['id'],digest=self.row['digest'],email_id=p['email_id'],revision=p['email_revision'])
        self.message='Run the approved image job for "'+self.f.fresh()['subject']+'" in Kit.'
    def review(self,**changes):
        data=dict(actor_id='owner',channel='authenticated-owner-browser',event_id='owner-event',packet_id=self.row['id'],packet_digest=self.row['digest'],action=DELEGATE);data.update(changes);return OwnerReview(**data)
    def grant(self):return self.dispatch.grant(self.row,self.review())
    def run_job(self,**changes):
        kw=dict(role='laia',message=self.message,request_id='owner-message');kw.update(changes);return self.dispatch.dispatch(self.args,**kw)
    def test_owner_grant_then_exact_dispatch_uses_existing_verified_service(self):
        self.grant();self.assertEqual(self.f.browser.writes,[])
        self.assertEqual(self.dispatch.context(),[self.args])
        result=self.run_job();self.assertEqual(result['state'],'verified_complete');self.assertEqual(result['broadcast_id'],555)
        self.assertEqual(self.f.browser.writes,['upload','insert'])
        with self.assertRaises(ValueError):self.run_job()
        self.assertEqual(self.f.browser.writes,['upload','insert'])
    def test_approval_alone_is_not_delegation(self):
        with self.assertRaises(PermissionError):self.run_job()
        self.assertEqual(self.dispatch.state(self.row),'owner_confirmation_required');self.assertEqual(self.f.browser.writes,[])
    def test_model_dict_wrong_actor_channel_or_action_cannot_grant(self):
        for review in ({'actor_id':'owner'},self.review(actor_id='laia'),self.review(channel='chat'),self.review(action='send'),self.review(packet_digest='bad')):
            with self.assertRaises(PermissionError):self.dispatch.grant(self.row,review)
        self.assertFalse(self.dispatch.root.exists())
    def test_roles_proactive_and_mixed_targets_rejected(self):
        self.grant()
        for kw in [dict(role='sam'),dict(proactive=True),dict(message='Show the email'),dict(message='Send the email'),dict(message='Do not '+self.message),dict(message='Run approved image job for another email'),dict(message='Quote: '+self.message)]:
            with self.subTest(kw=kw),self.assertRaises(PermissionError):self.run_job(**kw)
        self.assertEqual(self.f.browser.writes,[])
    def test_exact_context_and_unique_title_binding(self):
        email=self.f.fresh()
        self.assertTrue(self.dispatch.requested('Run approved image job for this email',email,dict(kind='email',id=email['id'])))
        self.assertFalse(self.dispatch.requested('Run approved image job for this email',email,dict(kind='project',id=email['id'])))
        with patch.object(self.service.emails,'all',return_value=[email,email]):self.assertFalse(self.dispatch.requested(self.message,email,{}))
    def test_stale_revision_images_and_foreign_arguments_rejected(self):
        self.grant();p=self.f.fresh();self.f.emails.save(p['id'],dict(subject='Changed'),p['revision'],sync_remote=False)
        with self.assertRaises(ValueError):self.run_job()
        self.assertEqual(self.f.browser.writes,[])
        with self.assertRaises(ValueError):self.dispatch.dispatch(dict(self.args,paths=['arbitrary']),role='laia',message=self.message)
    def test_expired_grant_never_executes(self):
        self.grant()
        with patch('studio.kit_visual_dispatch.time.time',return_value=time.time()+2000):
            self.assertEqual(self.dispatch.state(self.row),'expired')
            with self.assertRaises(PermissionError):self.run_job()
        self.assertEqual(self.f.browser.writes,[])
    def test_unavailable_adapter_preserves_unclaimed_grant(self):
        self.grant();self.f.browser.available=False
        with self.assertRaises(ValueError):self.run_job()
        self.assertEqual(self.dispatch.state(self.row),'authorized_once');self.assertEqual(self.f.browser.writes,[])
    def test_uncertain_execution_cannot_dispatch_again(self):
        self.grant()
        with patch.object(self.service,'execute',side_effect=TimeoutError('uncertain')) as execute:
            with self.assertRaises(TimeoutError):self.run_job()
            with self.assertRaises(PermissionError):self.run_job()
            self.assertEqual(execute.call_count,1)
    def test_manual_mapping_does_not_grant_owned_draft_access(self):
        self.grant()
        with patch.object(self.service.drafts,'made_by_us',return_value=None):
            with self.assertRaisesRegex(ValueError,'owned'):self.run_job()
        self.assertEqual(self.f.browser.writes,[])
    def test_owner_http_nonce_and_token_boundaries_apply_to_delegation(self):
        review=KitVisualReview(self.service);review.dispatch=self.dispatch
        path='/api/kit-visual-review/packet/'+self.row['id'];h=Handler(path);review.route(h,'GET',path);row=h.response[1]
        data=dict(id=row['id'],digest=row['digest'],nonce=row['delegate_nonce']);path='/api/kit-visual-review/delegate'
        denied=Handler(path,data,**{'X-DJ-Token':'test-owner-session'});review.route(denied,'POST',path);self.assertEqual(denied.response[0],403)
        allowed=Handler(path,data);review.route(allowed,'POST',path);self.assertEqual(allowed.response[0],200);self.assertEqual(allowed.response[1]['delegation_state'],'authorized_once')
        replay=Handler(path,data);review.route(replay,'POST',path);self.assertEqual(replay.response[0],400);self.assertEqual(self.f.browser.writes,[])
    def test_show_only_removes_job_and_general_chrome_remains_absent(self):
        answer,held=show_only(dict(reply='Show',show_email=self.args['email_id'],kit_visual_job=self.args))
        self.assertNotIn('kit_visual_job',answer);self.assertNotIn('laia',CHROME)

    def test_actual_agent_dispatch_schema_and_verified_reply(self):
        self.grant();agents=Agents(self.f.store,self.f.root,Team(self.f.store));agents.emails=self.f.emails;agents.kit_visual_dispatch=self.dispatch
        with patch.object(agents,'run',return_value=('Speculative claim',dict(reply='Speculative claim',kit_visual_job=self.args))) as run:
            result=agents.chat(dict(agent_id='laia',message=self.message,request_id='fixture-request'),[])
        self.assertIn('kit_visual_job',run.call_args.args[2]['properties']);self.assertFalse(run.call_args.kwargs['chrome'])
        self.assertEqual(self.f.browser.writes,['upload','insert']);self.assertNotIn('Speculative claim',json.dumps(result))

    def test_actual_agent_mixed_mutation_never_dispatches_or_edits(self):
        self.grant();agents=Agents(self.f.store,self.f.root,Team(self.f.store));agents.emails=self.f.emails;agents.kit_visual_dispatch=self.dispatch
        before=self.f.fresh()
        answer=dict(reply='Speculative claim',kit_visual_job=self.args,emails=[dict(id=before['id'],body='Changed')])
        with patch.object(agents,'run',return_value=('Speculative claim',answer)):
            result=agents.chat(dict(agent_id='laia',message=self.message),[])
        self.assertIn('separately',json.dumps(result));self.assertEqual(self.f.fresh(),before);self.assertEqual(self.f.browser.writes,[])

if __name__=='__main__':unittest.main()
