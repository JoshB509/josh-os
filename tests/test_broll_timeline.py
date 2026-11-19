import unittest
from studio.broll_timeline import plan
from dajoshi import edit_plan as ep

class BrollTimeline(unittest.TestCase):
 def setUp(self):
  self.words=[dict(id=str(i),text=t,start=i,end=i+1) for i,t in enumerate('begin the six parts next this is the end'.split())]
  self.rows=[dict(path='C:/library/a.mp4',sha256='hash',duration_seconds=6)]
  self.md='**Insert B-roll** `a.mp4` (full screen) from "the six parts"'
 def test_named_approved_clip_maps_without_source_audio(self):
  r=plan(self.md,self.words,ep.TICKS_PER_SECOND//30,[],self.rows)
  self.assertEqual(r[0]['start_f'],30);self.assertEqual(r[0]['kind'],'broll');self.assertNotIn('audio_db',r[0])
 def test_existing_animation_keeps_all_frames(self):
  r=plan(self.md,self.words,ep.TICKS_PER_SECOND//30,[dict(start_f=0,end_f=60),dict(start_f=120,end_f=150)],self.rows)
  self.assertEqual((r[0]['start_f'],r[0]['out_f']),(60,60))
 def test_missing_clip_fails(self):
  with self.assertRaisesRegex(ValueError,'missing or ambiguous'):plan(self.md,self.words,ep.TICKS_PER_SECOND//30,[],[])
 def test_ambiguous_library_fails(self):
  with self.assertRaisesRegex(ValueError,'ambiguous'):plan(self.md,self.words,ep.TICKS_PER_SECOND//30,[],self.rows*2)
 def test_unreadable_broll_not_silently_skipped(self):
  with self.assertRaisesRegex(ValueError,'no readable'):plan('Insert B-roll',self.words,ep.TICKS_PER_SECOND//30,[],self.rows)
 def test_complete_animation_overlap_fails(self):
  with self.assertRaisesRegex(ValueError,'No animation-free'):plan(self.md,self.words,ep.TICKS_PER_SECOND//30,[dict(start_f=0,end_f=300)],self.rows)
