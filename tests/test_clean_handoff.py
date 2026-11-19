"""The approved clean cut feeding the animation planner, and the final transcript reaching Sam, on a small pass laid out
the way studio/autocut_pass.py saves one (fixtures from tests/test_clean_transcript.py). No Premiere."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from studio import clean_edit as ce
from studio import edit_animations as ea
from dajoshi import edit_plan as ep
from tests.test_clean_transcript import AUTO, RAW, SIL, WORDS, F, info, s

T = ce.T


def info_av(pieces):
    """A read-back with V1 and A1 paired, like Premiere's."""
    a = info(pieces)['items']
    return dict(items=a + [dict(x, kind='v', inp=str(int(x['inp']) - (RAW['mic_in'] - RAW['camera_in']))) for x in a])


class FakeDriver:
    def __init__(self, pieces, mic="mic.wav", project="x.prproj"):
        self.pieces, self.mic, self.project = pieces, mic, project

    def premiere_ops(self, ops, timeout=300):
        data = info_av(self.pieces)
        data.update(name=ops[-1]['seq'], frame_ticks=str(F))
        for item in data['items']:
            item['path'] = self.mic if item['kind'] == 'a' else ''
        return [dict(project=self.project, sequences=['AUTOCUT_RAW_r01']), data]


class Handoff(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='clean-handoff-'))
        ev = self.tmp / 'evidence'
        ev.mkdir()
        (ev / 'premiere-native-words-mic.json').write_text(json.dumps([dict(media=None, words=WORDS)]))
        (ev / 'after-silences-readback.json').write_text(json.dumps(dict(info=info(SIL))))
        (ev / 'after-repeat-readback.json').write_text(json.dumps(dict(info=info(AUTO))))
        (ev / 'autocut-raw-r01-build.json').write_text(json.dumps(dict(plan=dict(map=dict(pieces=[RAW])))))
        np.zeros(8 * 16000, dtype='<f4').tofile(self.tmp / 'mic.f32')
        (self.tmp / 'project').mkdir()
        self.pieces = AUTO
        self.svc = ce.CleanEdit(lambda pid: self.tmp / 'project', driver_factory=lambda: FakeDriver(self.pieces))
        self.svc.register('p1', ev, 'x.prproj', 'mic.wav', self.tmp / 'mic.f32', 'AUTOCUT_RAW_r01', F)

    # ---------------------------------------------------------------- animation planner on clean words
    def test_animation_inputs_need_an_approval_that_still_matches(self):
        with self.assertRaises(ce.CleanEditError):
            self.svc.animation_inputs('p1')
        self.svc.approve('p1', 'Josh', True, 'AUTOCUT_RAW_r01')
        ci = self.svc.animation_inputs('p1')
        self.assertEqual(ci['provenance']['words'], 'premiere-native')
        self.assertEqual(ci['sequence'], 'AUTOCUT_RAW_r01')
        st = json.loads((self.tmp / 'project' / 'clean-edit.json').read_text())
        st['edits'] = [dict(op='remove', words=['n0005'], by='Josh', at='x')]     # changed behind the approval
        (self.tmp / 'project' / 'clean-edit.json').write_text(json.dumps(st))
        with self.assertRaises(ce.CleanEditError):
            self.svc.animation_inputs('p1')

    def test_planner_places_presses_through_the_clean_map(self):
        self.svc.approve('p1', 'Josh', True, 'AUTOCUT_RAW_r01')
        ci = self.svc.animation_inputs('p1')
        words, rem, m = ci['words'], ci['removals'], ci['map']
        by = {w['text']: w for w in words}
        self.assertIsNone(ep.map_word(m['pieces'], by['three,']['start'], 16000))          # AutoCut took the first "three,"
        t = ep.map_word(m['pieces'], by['four']['start'], 16000)
        self.assertIsNotNone(t)
        parts = [dict(kind='animation', n=1, script='one two', presses=[dict(press=1, words='one two'), dict(press=2, words='three four five')])]
        scenes = {1: dict(n=1, start=1, end=2, type='card')}
        shots, problems = ea.plan_shots(words, rem, m['pieces'], parts, scenes, m['frame_ticks'])
        self.assertEqual(problems, [])
        self.assertEqual([x['press'] for x in shots], [1, 2])
        # press 2 is anchored on the kept "three", not the removed "three,"
        self.assertEqual(shots[1]['start_f'], round(ep.map_word(m['pieces'], by['three']['start'], 16000) / F))

    # ---------------------------------------------------------------- final transcript, Josh's approval, Sam
    def test_final_transcript_approval_and_sam_handoff(self):
        with self.assertRaises(ce.CleanEditError):
            self.svc.sam_handoff('p1')
        self.svc.approve('p1', 'Josh', True, 'AUTOCUT_RAW_r01')
        f = self.svc.final_transcript('p1', 'FINAL_TEST')
        self.assertTrue(f['same_as_approved'])
        with self.assertRaises(ce.CleanEditError):
            self.svc.sam_handoff('p1')                                              # made, but not approved yet
        with self.assertRaises(ce.CleanEditError):
            self.svc.approve_final('p1', f['digest'], 'tariq', False)              # only Josh
        self.svc.approve_final('p1', f['digest'], 'Josh', True)
        h = self.svc.sam_handoff('p1')
        self.assertEqual(h['digest'], f['digest'])
        self.assertEqual(h['provenance']['clean_cut']['sequence'], 'AUTOCUT_RAW_r01')
        self.assertTrue(h['text'].startswith('one two'))
        self.assertNotIn('three,', h['text'].split())

    def test_a_hand_edit_makes_a_new_final_that_josh_must_approve(self):
        self.svc.approve('p1', 'Josh', True, 'AUTOCUT_RAW_r01')
        f1 = self.svc.final_transcript('p1', 'FINAL_TEST')
        self.svc.approve_final('p1', f1['digest'], 'Josh', True)
        self.pieces = AUTO[:1] + AUTO[2:]                                          # Josh cut a piece out by hand in Premiere
        f2 = self.svc.final_transcript('p1', 'FINAL_TEST')
        self.assertFalse(f2['same_as_approved'])
        self.assertTrue(f2['changes']['taken_out'])
        self.assertTrue(f2['words_changed_by_hand'])
        self.assertNotEqual(f1['file'], f2['file'])                                # the first version is kept
        self.assertTrue(Path(f1['file']).is_file())
        with self.assertRaises(ce.CleanEditError):
            self.svc.sam_handoff('p1')                                              # newer final, not approved
        with self.assertRaises(ce.CleanEditError):
            self.svc.approve_final('p1', f1['digest'], 'Josh', True)               # can't approve the older one now
        self.svc.approve_final('p1', f2['digest'], 'Josh', True)
        self.assertEqual(self.svc.sam_handoff('p1')['digest'], f2['digest'])

    def test_release_is_recorded_only_against_an_approved_final(self):
        with self.assertRaises(ce.CleanEditError):
            self.svc.record_release('p1', 'published', 'abc123', 'sam')
        self.svc.approve('p1', 'Josh', True, 'AUTOCUT_RAW_r01')
        f = self.svc.final_transcript('p1', 'FINAL_TEST')
        self.svc.approve_final('p1', f['digest'], 'Josh', True)
        with self.assertRaises(ce.CleanEditError):
            self.svc.record_release('p1', 'live', 'abc123', 'sam')
        r = self.svc.record_release('p1', 'scheduled', 'abc123', 'sam')
        self.assertEqual((r['state'], r['final']), ('scheduled', f['digest']))
        self.assertEqual(self.svc.view('p1')['release']['video_id'], 'abc123')

    def test_chapters_use_the_handoff_and_wait_for_approvals(self):
        from studio import chapters as ch
        flow = type('Flow', (), {})()
        flow.clean_edit = self.svc
        flow.folder = lambda pid: self.tmp / 'project'
        store = type('Store', (), {'get': lambda self, pid: dict(release={})})()
        c = ch.Chapters.__new__(ch.Chapters)
        c.flow, c.store = flow, store
        with self.assertRaises(ValueError) as e:
            c.basis('p1')
        self.assertIn('final transcript', str(e.exception))                        # Sam waits for Josh's final approval
        self.svc.approve('p1', 'Josh', True, 'AUTOCUT_RAW_r01')
        f = self.svc.final_transcript('p1', 'FINAL_TEST')
        self.svc.approve_final('p1', f['digest'], 'Josh', True)
        with self.assertRaises(ValueError) as e:
            c.basis('p1')
        self.assertIn('approve the final export', str(e.exception))                # and for the approved export


if __name__ == '__main__':
    unittest.main()
