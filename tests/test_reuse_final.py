import unittest,tempfile,threading,json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch
from studio.edit_flow import EditFlow

class ReuseFinal(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name);self.data=self.root/'data';self.data.mkdir()
  self.output=self.root/'video/exports/FINAL_ASSEMBLY_r01_v01.mp4';self.output.parent.mkdir(parents=True);self.output.write_bytes(b'preserved')
  self.proof=self.data/'final/FINAL_ASSEMBLY_r01_v01/assembly-check.json';self.proof.parent.mkdir(parents=True);self.proof.write_text(json.dumps(dict(sequence='FINAL_ASSEMBLY_r01_v01',problems=[])))
  self.f=object.__new__(EditFlow);f=self.f;f.lock=threading.Lock();f.jobs={};f.folder=lambda pid:self.data
  self.cut=dict(id='cut',payload=dict(plan=dict(map_digest='map')))
  self.vis=dict(id='vis',kind='edit_visuals',parent='cut',payload=dict(map_digest='map',storyboard_digest='deck|rendered'))
  self.previous=dict(id='previous',kind='edit_final',parent='vis',payload=dict(output=str(self.output)))
  f.get=lambda pid,aid:{'vis':self.vis,'cut':self.cut,'previous':self.previous}[aid]
  f.clean_cut=Mock(return_value=None);f.approved=Mock(return_value=True);f.latest=Mock(return_value=self.cut)
  f.edit_review=SimpleNamespace(review=lambda pid:dict(slides='deck',rendered='rendered'))
  f.folders=SimpleNamespace(path=lambda pid:self.root)
 def test_same_approval_and_successful_readback_allow_new_copy(self):
  with patch('studio.edit_flow.threading.Thread') as thread:self.f.final('p','vis',reuse_final_id='previous')
  self.assertEqual(thread.call_args.kwargs['args'][-1],'FINAL_ASSEMBLY_r01_v01');self.assertEqual(self.output.read_bytes(),b'preserved')
 def test_other_storyboard_cannot_be_reused(self):
  self.previous['parent']='other'
  with self.assertRaisesRegex(ValueError,'approved storyboard'):self.f.final('p','vis',reuse_final_id='previous')
 def test_missing_successful_readback_blocks(self):
  self.proof.write_text(json.dumps(dict(sequence='FINAL_ASSEMBLY_r01_v01',problems=['missing clips'])))
  with self.assertRaisesRegex(ValueError,'successful placement'):self.f.final('p','vis',reuse_final_id='previous')
 def test_output_outside_project_blocks(self):
  self.previous['payload']['output']=str(self.root/'other.mp4')
  with self.assertRaisesRegex(ValueError,'preserved export'):self.f.final('p','vis',reuse_final_id='previous')
 def test_reuse_copies_existing_layers_without_place_or_clear(self):
  f=self.f;f._setup_new=Mock();f._setup_step=Mock();f._setup_finish=Mock();f._phase_fail=Mock();f._tell=Mock();f._put=Mock(return_value={'id':'req'})
  f.store=SimpleNamespace(get=lambda pid:dict(name='video'));f.ffmpeg='unused';f.clear_layer_tracks=Mock();f.animation_omissions=Mock(return_value={});f.animation_gaps=Mock(return_value=[])
  layer=dict(kind='animation',path='image.mp4',track=4,start_f=0,in_f=0,out_f=3,name='animation')
  f.animation_layers=Mock(return_value=dict(result=dict(layers=[layer]),shots=[{}],problems=[],gate=[]))
  f.read_sequence=Mock(return_value=dict(frame_ticks=10,items=[dict(kind='v',track=4,start='0',path='image.mp4')]))
  f.bridge_call=Mock(side_effect=lambda cmd,args,**kw:dict(frame=[1920,1080]) if cmd=='tq_probe' else {})
  cut=dict(id='cut',payload=dict(plan=dict(revision=1,master='GREEN_MASTER',sequence_name='CUT',map={})))
  with patch('studio.edit_master.open_project'),patch('studio.edit_flow.assembly_problems',return_value=[]),patch('studio.edit_flow.verify_export',return_value=[]),patch('studio.edit_flow.verify_sync',return_value=([],[],[])),patch('studio.edit_flow.verify_windows',return_value=dict(problems=[])):
   f._final('p',self.vis,cut,self.root,'FINAL_ASSEMBLY_r01_v01')
  commands=[c.args[0] for c in f.bridge_call.call_args_list]
  self.assertNotIn('tq_place_layers',commands);f.clear_layer_tracks.assert_not_called()
  clone=next(c for c in f.bridge_call.call_args_list if c.args[0]=='tq_assemble');self.assertEqual(clone.args[1]['cut'],'FINAL_ASSEMBLY_r01_v01')
  self.assertLess(commands.index('tq_save'),commands.index('tq_export'));f._phase_fail.assert_not_called()
