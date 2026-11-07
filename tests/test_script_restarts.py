import json
import unittest
from pathlib import Path
import numpy as np
from dajoshi.script_align import find_restarts


class SyntheticRestartTests(unittest.TestCase):
    def words(self,parts):
        result=[];clock=1.0
        for part in parts:
            for i,token in enumerate(part.split()):
                result.append(dict(word=token,start=clock,end=clock+.18,probability=.99,segment_start=i==0));clock+=.4
        return result

    def test_intended_repetition_in_script_is_preserved(self):
        parts=['This is important so remember it well.','This is important so remember it well.','Then move on to the next action.']
        words=self.words(parts);removed,flags,report=find_restarts(words,' '.join(parts),np.zeros(30*16000),16000)
        self.assertEqual(removed,[])

    def test_unscripted_improvisation_is_not_deleted(self):
        words=self.words(['I want to show you the plan.','Here is a useful personal example from last year.','Then we move on to the next action.'])
        script='I want to show you the plan. Then we move on to the next action.'
        removed,_,_=find_restarts(words,script,np.zeros(30*16000),16000)
        self.assertEqual(removed,[])

    def test_no_quiet_boundary_means_flag_not_delete(self):
        parts=['So first take your slide','So first take your slide deck or your report and remove the background.']
        removed,flags,report=find_restarts(self.words(parts),parts[1],np.ones(30*16000),16000)
        self.assertEqual(removed,[]);self.assertTrue(flags)


class StorytellingRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source=Path(r'E:\Videos\DaJoshiResolve\Runs\Storytelling-2.0-json-test-20261014')
        script=Path(r'E:\CareerOS\12_YouTube\13_three-things-executives-need\2026-10-13_youtube_three-things-executives-need_script_final.md')
        cls.words=[dict(w,segment_start=i==0) for s in json.loads((source/'transcript.json').read_text()) for i,w in enumerate(s['words'])]
        cls.removed,cls.flags,cls.report=find_restarts(cls.words,script.read_text(encoding='utf-8-sig'),np.fromfile(source/'microphone.f32',dtype='<f4'),16000)

    def assert_restart(self,start,restart):
        found=[d for d in self.removed if abs(d['abandoned_start']-start)<.05 and abs(d['restart_start']-restart)<.05]
        self.assertTrue(found,'Expected actual restart %s -> %s'%(start,restart))
        self.assertLess(found[0]['source_start'],found[0]['source_end'])

    def test_first_slide_abandoned_start(self):self.assert_restart(184.26,186.02)
    def test_three_attempts_keep_final_completed_passage(self):self.assert_restart(583.58,592.28)
    def test_third_section_keep_final_take(self):self.assert_restart(743.55,762.54)
    def test_toolkit_close_keep_completed_take(self):self.assert_restart(912.45,935.91)
    def test_real_improvised_explanation_preserved(self):
        # Not in the script, but meaningful commentary instead of a retake.
        for a,b in [(210.44,212.28),(523.78,534.56),(907.73,912.3)]:
            self.assertFalse(any(d['source_start']<b and d['source_end']>a for d in self.removed))
    def test_script_coverage_and_boundary_flags(self):
        self.assertGreater(self.report['script_coverage'],.9)
        self.assertTrue(self.flags)


if __name__=='__main__':unittest.main(verbosity=2)
