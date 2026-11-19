import unittest, tempfile, threading, os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch
from studio.edit_flow import EditFlow
from studio.store import Conflict
from studio import deck_source as ds

class FinishClean(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
  self.slides=self.root/'deck.html';self.shots=self.root/'shots.md'
  for p in (self.slides,self.shots):p.write_text('original');os.utime(p,(1,1))
  self.f=object.__new__(EditFlow);f=self.f
  f.store=SimpleNamespace(get=Mock(return_value={'name':'Video'}));f.lock=threading.Lock();f.jobs={}
  self.cut=dict(id='clean',payload=dict(state='verified',plan=dict(sequence_name='AUTOCUT_RAW_r01')))
  f.clean_cut=Mock(return_value=self.cut);f.latest=Mock(return_value=None);f.approved=Mock(return_value=False)
  f.edit_review=SimpleNamespace(deck_files=lambda pid:(self.slides,self.shots),review=lambda pid:dict(slides='deck.html',rendered='render'))
  f._after_script=Mock();f.visuals=Mock(return_value={'id':'vis'});f._approve=Mock();f._tell=Mock();f.final=Mock()
  self.approval=dict(text='I approve these animations',at='2026-11-18T14:01:02+00:00')
 def test_clean_approval_dispatches_without_legacy_proposal(self):
  with patch.object(ds,'require') as guard,patch('studio.edit_flow.threading.Thread') as th:
   self.f.finish('p',self.approval)
   guard.assert_called_once();th.return_value.start.assert_called_once()
   self.assertIsNone(th.call_args.kwargs['args'][1]);self.f.latest.assert_not_called()
 def test_missing_clean_approval_fails_before_thread(self):
  self.f.clean_cut.side_effect=Conflict('Approve clean cut')
  with patch('studio.edit_flow.threading.Thread') as th,self.assertRaises(Conflict):self.f.finish('p',self.approval)
  th.assert_not_called()
 def test_stale_binding_blocks_dispatch(self):
  with patch.object(ds,'require',side_effect=ValueError('stale')),patch('studio.edit_flow.threading.Thread') as th,self.assertRaises(ValueError):self.f.finish('p',self.approval)
  th.assert_not_called()
 def test_legacy_still_needs_script_approval(self):
  self.f.clean_cut.return_value=None
  with self.assertRaisesRegex(Conflict,'script first'):self.f.finish('p',self.approval)
 def test_duplicate_running_job_rejected(self):
  self.f.jobs[('final','p')]=SimpleNamespace(is_alive=lambda:True)
  with patch.object(ds,'require'),self.assertRaisesRegex(Conflict,'already working'):self.f.finish('p',self.approval)
 def test_clean_finish_never_rebuilds_and_uses_exact_approval(self):
  with patch.object(ds,'require'):self.f._finish('p',None,self.approval)
  self.f._after_script.assert_not_called();self.f.latest.assert_not_called();self.f.final.assert_called_once_with('p','vis')
  self.assertEqual(self.f._approve.call_args.args[1]['said_at'],self.approval['at'])
 def test_changed_shot_list_after_approval_does_not_start(self):
  os.utime(self.shots,(1900000000,1900000000))
  with patch.object(ds,'require'):self.f._finish('p',None,self.approval)
  self.f.final.assert_not_called();self.f._approve.assert_not_called()
 def test_live_cut_change_before_worker_does_not_fall_back(self):
  self.f.clean_cut.side_effect=Conflict('timeline changed')
  with patch.object(ds,'require'):self.f._finish('p',None,self.approval)
  self.f.final.assert_not_called();self.f._after_script.assert_not_called()

if __name__=='__main__':unittest.main()
