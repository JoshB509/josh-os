"""Josh OS clean edit service on a small AutoCut pass laid out the way studio/autocut_pass.py saves one."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from studio import clean_edit as ce
from tests.test_clean_transcript import AUTO, RAW, SIL, WORDS, F, info


class Service(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='clean-edit-'))
        ev = self.tmp / 'evidence'
        ev.mkdir()
        (ev / 'premiere-native-words-mic.json').write_text(json.dumps([dict(media=None, words=WORDS)]))
        (ev / 'after-silences-readback.json').write_text(json.dumps(dict(info=info(SIL))))
        (ev / 'after-repeat-readback.json').write_text(json.dumps(dict(info=info(AUTO))))
        (ev / 'autocut-raw-r01-build.json').write_text(json.dumps(dict(plan=dict(map=dict(pieces=[RAW])))))
        pcm = self.tmp / 'mic.f32'
        # A real-looking mic: a quiet floor, and a voiced tone inside each word, so production edges have a speech level to
        # be quiet against (a silent mic has none, and production refuses to cut on it).
        x = np.random.default_rng(0).normal(0, 0.0003, 8 * 16000).astype('<f4')
        t = np.arange(len(x)) / 16000
        for w in WORDS:
            i, j = int(w['start'] * 16000), int(w['end'] * 16000)
            x[i:j] += (0.2 * np.sin(2 * np.pi * 140 * t[i:j])).astype('<f4')
        x.tofile(pcm)
        (self.tmp / 'project').mkdir()
        from tests.test_clean_handoff import FakeDriver
        self.svc = ce.CleanEdit(lambda pid: self.tmp / 'project', driver_factory=lambda: FakeDriver(AUTO))
        self.view = self.svc.register('p1', ev, 'x.prproj', 'mic.wav', pcm, 'AUTOCUT_RAW_r01', F)

    def test_view_shows_autocut_removals_with_provenance(self):
        w = {x['text']: x for x in self.view['words']}
        self.assertEqual((w['three,']['state'], w['three,']['removed_by']), ('removed', 'repeat'))
        self.assertEqual(w['three']['state'], 'kept')
        self.assertEqual(self.view['edits'], [])

    def test_edit_and_undo(self):
        v = self.svc.edit('p1', 'remove', ['n0005'], 'Josh')
        self.assertEqual(next(x for x in v['words'] if x['id'] == 'n0005')['removed_by'], 'josh')
        v = self.svc.edit('p1', 'restore', ['n0003'], 'Josh')
        self.assertTrue(next(x for x in v['words'] if x['id'] == 'n0003')['restored'])
        v = self.svc.undo('p1')
        self.assertEqual(len(v['edits']), 1)
        with self.assertRaises(ValueError):
            self.svc.edit('p1', 'remove', ['n0001', 'n0004'], 'Josh')       # not one run of neighbouring words
        with self.assertRaises(ce.CleanEditError):
            self.svc.edit('p1', 'shuffle', ['n0001'], 'Josh')

    def test_only_josh_approves_and_only_what_was_built(self):
        with self.assertRaises(ce.CleanEditError):
            self.svc.approve('p1', 'tariq', False, 'AUTOCUT_RAW_r01')
        with self.assertRaises(ce.CleanEditError):
            self.svc.approved('p1')                                         # animations and assembly wait
        self.svc.edit('p1', 'remove', ['n0005'], 'Josh')
        with self.assertRaises(ce.CleanEditError):
            self.svc.approve('p1', 'Josh', True, 'AUTOCUT_RAW_r01')         # edits since: must be built first
        self.svc.undo('p1')
        self.svc.approve('p1', 'Josh', True, 'AUTOCUT_RAW_r01')             # AutoCut's own cut, no edits
        self.assertEqual(self.svc.approved('p1'), 'AUTOCUT_RAW_r01')
        with self.assertRaises(ce.CleanEditError):
            self.svc.edit('p1', 'remove', ['n0005'], 'Josh')                # an approved cut is fixed

    def test_preview_and_clean_transcript_versions(self):
        pv = self.svc.preview('p1', 'n0003', 1)
        self.assertTrue(pv['source_cuts'])                                   # "three," sits in a removed stretch
        self.assertTrue(self.svc.preview_file('p1', pv['source']).is_file())
        with self.assertRaises(ce.CleanEditError):
            self.svc.preview_file('p1', '../clean-edit.json')
        with self.assertRaises(ce.CleanEditError):
            self.svc.clean_transcript('p1')                                  # not approved yet
        self.svc.approve('p1', 'Josh', True, 'AUTOCUT_RAW_r01')
        a = self.svc.clean_transcript('p1')
        b = self.svc.clean_transcript('p1')
        self.assertEqual(a['file'], b['file'])                               # same words, same version, never overwritten
        tx = json.loads(Path(a['file']).read_text())
        self.assertEqual([w['id'] for w in tx['words']], ['n0001', 'n0002', 'n0004', 'n0005', 'n0006', 'n0007'])
        self.assertEqual(tx['approved']['sequence'], 'AUTOCUT_RAW_r01')
        self.assertTrue(all(x['start'] <= y['start'] for x, y in zip(tx['words'], tx['words'][1:])))

    def test_animation_and_assembly_callers_wait_for_approval(self):
        from studio import edit_flow as efm
        flow = efm.EditFlow.__new__(efm.EditFlow)
        flow.data = self.tmp
        (self.tmp / 'p1').mkdir(exist_ok=True)
        (self.tmp / 'p1' / 'clean-edit.json').write_text((self.tmp / 'project' / 'clean-edit.json').read_text())
        for call in (lambda: flow.visuals('p1', 'x'), lambda: flow.final('p1', 'x'), lambda: flow.animation_layers('p1', None, 0, None)):
            with self.assertRaises(efm.Conflict) as e:
                call()
            self.assertIn('approved', str(e.exception))
        self.assertIsNone(flow.clean_gate('no-clean-edit'))                 # videos on the older planner are unaffected

    def test_a_new_pass_keeps_history(self):
        self.svc.edit('p1', 'remove', ['n0005'], 'Josh')
        v = self.svc.register('p1', self.tmp / 'evidence', 'x.prproj', 'mic.wav', self.tmp / 'mic.f32', 'AUTOCUT_RAW_r02', F)
        st = json.loads((self.tmp / 'project' / 'clean-edit.json').read_text())
        self.assertEqual(v['edits'], [])
        self.assertEqual(st['history'][0]['edits'][0]['words'], ['n0005'])


if __name__ == '__main__':
    unittest.main()
