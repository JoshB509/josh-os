import unittest
from pathlib import Path
from unittest.mock import patch,Mock
from studio.edit_flow import verify_export
from studio.broll_timeline import scale_ops
from dajoshi.edit_plan import TICKS_PER_SECOND as T

class ExportFix(unittest.TestCase):
 def test_clean_map_uses_native_timebase(self):
  with patch.object(Path,'is_file',return_value=True),patch.object(Path,'stat',return_value=Mock(st_size=10)),patch('dajoshi.media.probe',return_value=dict(duration=10,channels=1,fps=30)):
   self.assertEqual(verify_export('x',dict(duration_ticks=10*T)),[])
 def test_legacy_timebase_still_honored(self):
  with patch.object(Path,'is_file',return_value=True),patch.object(Path,'stat',return_value=Mock(st_size=10)),patch('dajoshi.media.probe',return_value=dict(duration=10,channels=1,fps=30)):
   self.assertEqual(verify_export('x',dict(duration_ticks=10000,ticks_per_second=1000)),[])
 def test_wrong_duration_still_fails(self):
  with patch.object(Path,'is_file',return_value=True),patch.object(Path,'stat',return_value=Mock(st_size=10)),patch('dajoshi.media.probe',return_value=dict(duration=12,channels=1,fps=30)):
   self.assertTrue(verify_export('x',dict(duration_ticks=10*T)))
 def info(self):
  return dict(name='FINAL_ASSEMBLY_AC01_v02',frame=[3840,2160],frame_ticks=10,items=[dict(kind='v',track=4,index=3,path='clip.mp4',start='100',effects=[dict(match='AE.ADBE Motion',params=[dict(name='Scale',value=100),dict(name='Position',value=[.5,.5])])])])
 def layer(self):return [dict(kind='broll',path='clip.mp4',track=4,start_f=10,name='Broll')]
 def test_1080_clip_fills_4k_without_moving_or_retiming(self):
  ops,expected=scale_ops(self.info(),self.layer(),lambda p:dict(width=1920,height=1080,channels=0))
  self.assertEqual(ops,[dict(op='set',seq='FINAL_ASSEMBLY_AC01_v02',kind='v',track=4,index=3,effect='AE.ADBE Motion',param='Scale',value=200)])
  self.assertEqual(expected[0]['position'],[.5,.5])
 def test_4k_clip_stays_100(self):
  ops,_=scale_ops(self.info(),self.layer(),lambda p:dict(width=3840,height=2160,channels=0));self.assertEqual(ops[0]['value'],100)
 def test_wrong_identity_fails_before_any_dispatch(self):
  info=self.info();info['items'][0]['path']='other.mp4'
  with self.assertRaisesRegex(ValueError,'identity'):scale_ops(info,self.layer(),lambda p:{})
 def test_non_silent_clip_fails(self):
  with self.assertRaisesRegex(ValueError,'silent'):scale_ops(self.info(),self.layer(),lambda p:dict(width=1920,height=1080,channels=2))
