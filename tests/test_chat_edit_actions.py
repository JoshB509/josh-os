import unittest
import tempfile
from pathlib import Path
from unittest.mock import Mock, ANY
from studio.chat_edit_actions import ChatEditActions,ACTIONS

class ChatEditActionsTests(unittest.TestCase):
    def setUp(self):
        self.store=Mock();self.store.get.return_value={'id':'p1','title':'Test video'}
        self.clean=Mock();self.clean._file.return_value.is_file.return_value=True
        self.clean._load.return_value={'edits':[]};self.clean.revision.return_value={'digest':'e1'}
        self.clean.view.return_value={'words':[]}
        self.archive=Mock();self.archive.manifest.return_value={'digest':'m1'};self.archive.settings.return_value={'passport':'configured'}
        self.authorize=Mock(return_value=True)
        self.adapter=ChatEditActions(self.store,self.clean,self.archive,self.authorize)
        self.rev=self.adapter.context('p1')['revision']
    def dispatch(self,action='remove_words',**extra):
        return self.adapter.dispatch(dict(action=action,project_id='p1',revision=self.rev,**extra),role='tariq',project_id='p1',current_message='Remove these words in Test video')
    def test_edit_attributes_tariq(self):
        self.dispatch(words=['w1']);self.clean.edit.assert_called_once_with('p1','remove',['w1'],'tariq',expected_state_digest=ANY)
    def test_stale_revision(self):
        self.clean._load.return_value={'edits':[1]}
        with self.assertRaises(ValueError):self.dispatch(words=['w1'])
        self.clean.edit.assert_not_called()
    def test_current_request_required(self):
        self.authorize.return_value=False
        with self.assertRaises(ValueError):self.dispatch(words=['w1'])
        self.clean.edit.assert_not_called()
    def test_role_and_project(self):
        for role,pid in [('sam','p1'),('tariq','p2')]:
            with self.assertRaises(ValueError):self.adapter.dispatch({'action':'inspect','project_id':pid},role=role,project_id='p1',current_message='inspect')
    def test_schema_is_not_security_boundary(self):
        for args in ({'words':['w1'],'approve':True},{'words':[]},{'words':['w1','w1']},{'words':[1]}):
            with self.assertRaises(ValueError):self.dispatch(**args)
    def test_forbidden_actions(self):
        for action in ('approve','approve_final','publish','cleanup','rebuild','final_transcript','shell'):
            self.assertNotIn(action,ACTIONS)
            with self.assertRaises(ValueError):self.dispatch(action)
    def test_gate_errors_propagate(self):
        self.clean.sam_handoff.side_effect=ValueError('Owner approval required')
        with self.assertRaisesRegex(ValueError,'Owner approval'):self.dispatch('transcript_handoff')
    def test_handoff_only_returns_data(self):
        self.clean.sam_handoff.return_value={'text':'local transcript'}
        self.assertEqual(self.dispatch('transcript_handoff')['result'],{'text':'local transcript'})
        self.archive.copy.assert_not_called()
    def test_archive_configured_drive_only(self):
        with self.assertRaises(ValueError):self.dispatch('archive_copy',drive='C:/elsewhere')
        self.dispatch('archive_copy',drive='passport');self.archive.copy.assert_called_once_with('p1','passport')
    def test_no_manifest(self):
        self.archive.manifest.return_value=None;self.rev=self.adapter.context('p1')['revision']
        with self.assertRaises(ValueError):self.dispatch('archive_copy',drive='passport')
    def test_context_is_read_only(self):
        self.adapter.context('p1');self.clean.edit.assert_not_called();self.archive.prepare.assert_not_called()
    def test_undo_cleanup_transcript_routes(self):
        for action,method in [('undo_words','undo'),('cleanup_proposals','cleanup'),('clean_transcript','clean_transcript')]:
            self.dispatch(action)
            if action=='undo_words':getattr(self.clean,method).assert_called_once_with('p1',expected_state_digest=ANY)
            else:getattr(self.clean,method).assert_called_once_with('p1')

    def flow_fixture(self, folder):
        flow=Mock();flow.latest.return_value=None;flow.progress.return_value=None
        flow.folder.return_value=folder
        flow.suggest.return_value={'folder':str(folder),'videos':[str(folder/'camera.mp4')],'audio':[str(folder/'mic.wav')]}
        self.adapter.flow=flow
        self.rev=self.adapter.context('p1')['revision']
        return flow
    def test_register_project_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp);(folder/'camera.mp4').write_bytes(b'fake');(folder/'mic.wav').write_bytes(b'fake')
            flow=self.flow_fixture(folder)
            self.dispatch('register_sources',camera=str(folder/'camera.mp4'),mic=str(folder/'mic.wav'))
            flow.register.assert_called_once_with('p1',str(folder/'camera.mp4'),str(folder/'mic.wav'))
    def test_unknown_source_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            flow=self.flow_fixture(Path(tmp))
            with self.assertRaises(ValueError):self.dispatch('register_sources',camera='other.mp4',mic='other.wav')
            flow.register.assert_not_called()
    def test_cancel_only_current_project_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp);flow=self.flow_fixture(folder)
            flow.progress.return_value={'state':'running','run':str(folder/'runs'/'r1')}
            self.rev=self.adapter.context('p1')['revision']
            self.dispatch('cancel_analysis');flow.cancel.assert_called_once_with('p1')
    def test_cancel_outside_project_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp);flow=self.flow_fixture(folder)
            flow.progress.return_value={'state':'running','run':str(folder/'elsewhere'/'r1')}
            self.rev=self.adapter.context('p1')['revision']
            with self.assertRaises(ValueError):self.dispatch('cancel_analysis')
            flow.cancel.assert_not_called()

class RequestAndAtomicGuardTests(unittest.TestCase):
    def test_default_request_matcher(self):
        from studio.chat_edit_actions import current_request_allows
        ctx={'project_id':'p1','project_title':'Test video'}
        args={'action':'remove_words','words':['w1','w2']}
        self.assertTrue(current_request_allows('Remove words w1 and w2 in Test video',args,ctx))
        self.assertFalse(current_request_allows('Remove words w1 only',args,ctx))
        self.assertFalse(current_request_allows("Do not remove words w1 and w2",args,ctx))
        self.assertFalse(current_request_allows('What are the editing capabilities?',args,ctx))
        self.assertFalse(current_request_allows('Copy the archive',{'action':'archive_copy','drive':'passport'},ctx))
        self.assertTrue(current_request_allows('Copy the archive to passport',{'action':'archive_copy','drive':'passport'},ctx))
    def test_backend_stale_guard_before_model_and_save(self):
        import threading
        from studio.clean_edit import CleanEdit,CleanEditError
        clean=CleanEdit.__new__(CleanEdit);clean.lock=threading.Lock()
        clean._load=Mock(return_value={'edits':[{'op':'remove'}],'approved':None})
        clean.model=Mock();clean._save=Mock()
        for call in (lambda:clean.edit('p1','remove',['w1'],'tariq',expected_state_digest='stale'),lambda:clean.undo('p1',expected_state_digest='stale')):
            with self.assertRaisesRegex(CleanEditError,'changed'):call()
        clean.model.assert_not_called();clean._save.assert_not_called()

class NaturalTargetTests(unittest.TestCase):
    def allows(self,message,args,context):
        from studio.chat_edit_actions import current_request_allows
        return current_request_allows(message,args,context)
    def test_unique_quoted_phrase(self):
        ctx={'clean':{'words':[{'id':'w1','text':'Hello,'},{'id':'w2','text':'world!'},{'id':'w3','text':'Again.'}]}}
        args={'action':'remove_words','words':['w1','w2']}
        self.assertTrue(self.allows('Remove "hello world"',args,ctx))
        self.assertTrue(self.allows("Remove 'hello world'",args,ctx))
        self.assertFalse(self.allows('Remove "hello again"',args,ctx))
        self.assertFalse(self.allows('Remove "hello world"',dict(args,words=['w2']),ctx))
    def test_repeated_phrase_is_ambiguous(self):
        ctx={'clean':{'words':[{'id':'w1','text':'hello'},{'id':'w2','text':'world'},{'id':'w3','text':'hello'},{'id':'w4','text':'world'}]}}
        self.assertFalse(self.allows('Remove "hello world"',{'action':'remove_words','words':['w1','w2']},ctx))
    def test_unique_candidate_basenames(self):
        ctx={'sources':{'videos':['C:/input/camera.mp4'],'audio':['C:/input/mic.wav']}}
        args={'action':'register_sources','camera':'C:/input/camera.mp4','mic':'C:/input/mic.wav'}
        self.assertTrue(self.allows('Use camera.mp4 and mic.wav as sources',args,ctx))
        self.assertFalse(self.allows('Use old-camera.mp4 and mic.wav as sources',args,ctx))
        ctx['sources']['videos'].append('C:/other/camera.mp4')
        self.assertFalse(self.allows('Use camera.mp4 and mic.wav as sources',args,ctx))
        self.assertTrue(self.allows('Use C:/input/camera.mp4 and C:/input/mic.wav as sources',args,ctx))

if __name__=='__main__':unittest.main()
