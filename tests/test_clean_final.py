"""Final assembly from the approved AutoCut clean cut, with a mocked Premiere bridge. Nothing reaches Premiere."""
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from studio import clean_edit as ce
from studio import edit_flow as efm
from studio import edit_master as em
from tests.test_clean_transcript import AUTO, RAW, SIL, WORDS, F, info

MIC = r'E:\rec\good-mic.wav'


class Flow(efm.EditFlow):
    """EditFlow with its record store in memory, so the clean path runs without the database or Premiere."""

    def __init__(self, data, svc):
        self.data, self.clean_edit, self.lock, self.jobs = data, svc, threading.Lock(), {}
        self.recs, self.calls, self.said = {}, [], []
        self.edit_review = type('ER', (), {'review': lambda s, pid: dict(slides='s1', rendered='r1')})()
        self.folders = type('Folders', (), {'path': lambda s, pid: str(data / 'video-folder')})()
        self.store = type('Store', (), {'get': lambda s, pid: dict(name='4 Signs')})()
        self.ffmpeg = 'ffmpeg'
        self.seq_info = None
        self.existed = False

    # records
    def _put(self, pid, kind, payload, parent=None):
        r = dict(id=f'{kind}-{len(self.recs) + 1}', kind=kind, payload=payload, parent=parent, approvals=[])
        self.recs[r['id']] = r
        return r

    def get(self, pid, rid):
        return self.recs[rid]

    def _all(self, pid, kind):
        return [r for r in reversed(list(self.recs.values())) if r['kind'] == kind]

    def latest(self, pid, kind):
        a = self._all(pid, kind)
        return a[0] if a else None

    def _approve(self, rec, meaning):
        rec['approvals'].append(meaning)

    def approved(self, rec):
        return bool(rec.get('approvals'))

    # progress and messages
    def _setup_new(self, *a, **k): pass
    def _setup_step(self, *a, **k): pass
    def _setup_finish(self, *a, **k): pass
    def _phase_fail(self, *a, **k): pass
    def _tell(self, pid, text): self.said.append(text)
    def animation_gaps(self, pid): return []
    def animation_omissions(self, pid): return {}

    # Premiere, mocked
    def bridge_call(self, cmd, args, timeout=1800):
        self.calls.append((cmd, args))
        if cmd == 'tq_probe':
            return dict(frame=[3840, 2160])
        if cmd == 'tq_assemble':
            return dict(sequence=args['sequence'], existed=self.existed)
        return {}

    def read_sequence(self, name):
        self.calls.append(('info', name))
        return self.seq_info

    def clear_layer_tracks(self, name):
        self.calls.append(('clear', name))

    def animation_layers(self, pid, cut, frame, job, say=print, render_limit=None):
        self.layers_cut = cut
        return dict(result=dict(layers=[], fps_num=30, fps_den=1), shots=[], problems=[], gate=[], source='clean')


def readback_of(plan, mic=MIC):
    """What Premiere would read back from a faithful copy: the nest on V1 and the good mic on A1, piece for piece."""
    items = []
    for p in plan['map']['pieces']:
        items.append(dict(kind='v', track=0, start=str(p['out_start']), end=str(p['out_end']), inp=str(p['camera_in']), path=''))
        items.append(dict(kind='a', track=0, start=str(p['out_start']), end=str(p['out_end']), inp=str(p['mic_in']), path=mic))
    return dict(frame_ticks=str(F), items=items)


class CleanFinal(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='clean-final-'))
        ev = self.tmp / 'evidence'
        ev.mkdir()
        (ev / 'premiere-native-words-mic.json').write_text(json.dumps([dict(media=None, words=WORDS)]))
        (ev / 'after-silences-readback.json').write_text(json.dumps(dict(info=info(SIL))))
        (ev / 'after-repeat-readback.json').write_text(json.dumps(dict(info=info(AUTO))))
        (ev / 'autocut-raw-r01-build.json').write_text(json.dumps(dict(plan=dict(map=dict(pieces=[RAW])))))
        np.zeros(8 * 16000, dtype='<f4').tofile(self.tmp / 'mic.f32')
        from tests.test_clean_handoff import FakeDriver
        self.svc = ce.CleanEdit(lambda pid: self.tmp / pid, driver_factory=lambda: FakeDriver(AUTO, mic=MIC))
        (self.tmp / 'p1').mkdir()
        self.svc.register('p1', ev, 'x.prproj', MIC, self.tmp / 'mic.f32', 'AUTOCUT_RAW_r01', F)
        self.flow = Flow(self.tmp, self.svc)
        self._open, self._vx, self._vs = em.open_project, efm.verify_export, efm.verify_sync
        em.open_project = lambda flow, pid: None
        efm.verify_export = lambda out, m: []
        efm.verify_sync = lambda out, plan, ff: ([], [dict(at=0)], [])

    def tearDown(self):
        em.open_project, efm.verify_export, efm.verify_sync = self._open, self._vx, self._vs

    def approve_storyboard(self, cut_id='CLEAN_CUT_stale_id'):
        return self.flow.visuals('p1', cut_id, storyboard_digest='s1|r1', approve=True)

    def test_clean_cut_record_comes_from_the_approval(self):
        with self.assertRaises(efm.Conflict):
            self.flow.clean_cut('p1')                                               # not approved yet
        self.svc.approve('p1', 'Josh', True, 'AUTOCUT_RAW_r01')
        cc = self.flow.clean_cut('p1')
        plan = cc['payload']['plan']
        self.assertEqual((plan['sequence_name'], plan['master'], plan['mic_path'], plan['stem']), ('AUTOCUT_RAW_r01', 'GREEN_MASTER', MIC, 'FINAL_ASSEMBLY_AC01'))
        self.assertEqual([[p['mic_in'], p['mic_out']] for p in plan['map']['pieces']], self.svc.model('p1').k_auto)
        self.assertEqual(plan['provenance']['words'], 'premiere-native')
        self.assertEqual([s for s in self.flow.status('p1') if s['key'] == 'cut'][0]['state'], 'verified')

    def test_storyboard_belongs_to_the_clean_cut_whatever_the_page_sent(self):
        self.svc.approve('p1', 'Josh', True, 'AUTOCUT_RAW_r01')
        v = self.approve_storyboard()
        self.assertEqual(v['parent'], self.flow.clean_cut('p1')['id'])
        self.assertEqual([s for s in self.flow.status('p1') if s['key'] == 'visuals'][0]['state'], 'approved')

    def test_final_rejects_a_storyboard_from_an_older_clean_approval(self):
        self.svc.approve('p1', 'Josh', True, 'AUTOCUT_RAW_r01')
        v = self.approve_storyboard()
        st = json.loads((self.tmp / 'p1' / 'clean-edit.json').read_text())
        st['approved']['at'] = 'a later approval'                                   # Josh approved again since
        (self.tmp / 'p1' / 'clean-edit.json').write_text(json.dumps(st))
        with self.assertRaises(efm.Conflict) as e:
            self.flow.final('p1', v['id'])
        self.assertIn('stale', str(e.exception))
        self.assertEqual(self.flow.calls, [])

    def test_final_refuses_without_the_clean_approval(self):
        self.svc.approve('p1', 'Josh', True, 'AUTOCUT_RAW_r01')
        v = self.approve_storyboard()
        st = json.loads((self.tmp / 'p1' / 'clean-edit.json').read_text())
        st['approved'] = None
        (self.tmp / 'p1' / 'clean-edit.json').write_text(json.dumps(st))
        with self.assertRaises(efm.Conflict):
            self.flow.final('p1', v['id'])

    def run_final(self):
        self.svc.approve('p1', 'Josh', True, 'AUTOCUT_RAW_r01')
        v = self.approve_storyboard()
        cut = self.flow.clean_cut('p1')
        return self.flow._final('p1', v, cut, self.tmp / 'video-folder'), cut

    def test_fresh_assembly_copies_the_approved_clean_sequence_and_exports_only_the_copy(self):
        self.svc.approve('p1', 'Josh', True, 'AUTOCUT_RAW_r01')
        self.flow.seq_info = readback_of(self.flow.clean_cut('p1')['payload']['plan'])
        rec, cut = self.run_final()
        self.assertEqual(rec['payload']['state'], 'verified', rec['payload'])
        cmds = [(c, a) for c, a in self.flow.calls if isinstance(a, dict)]
        self.assertEqual(cmds[0], ('tq_probe', dict(master='GREEN_MASTER')))
        self.assertEqual(cmds[1], ('tq_assemble', dict(cut='AUTOCUT_RAW_r01', sequence='FINAL_ASSEMBLY_AC01_v01')))
        exported = [a for c, a in cmds if c == 'tq_export']
        self.assertEqual(exported[0]['sequence'], 'FINAL_ASSEMBLY_AC01_v01')
        # nothing is ever asked of the approved clean sequence except to be copied
        touched = {a.get('sequence') for c, a in cmds if c != 'tq_assemble'} | {a for c, a in self.flow.calls if c in ('info', 'clear')}
        self.assertNotIn('AUTOCUT_RAW_r01', touched)
        self.assertEqual(self.flow.layers_cut['id'], cut['id'])                     # animations planned on the clean cut

    def test_an_existing_assembly_name_is_never_reused(self):
        self.svc.approve('p1', 'Josh', True, 'AUTOCUT_RAW_r01')
        self.flow.seq_info = readback_of(self.flow.clean_cut('p1')['payload']['plan'])
        self.flow.existed = True
        rec, _ = self.run_final()
        self.assertEqual(rec['payload']['state'], 'blocked')
        self.assertNotIn('tq_export', [c for c, _ in self.flow.calls])

    def test_a_copy_with_the_wrong_mic_or_moved_picture_is_blocked(self):
        self.svc.approve('p1', 'Josh', True, 'AUTOCUT_RAW_r01')
        plan = self.flow.clean_cut('p1')['payload']['plan']
        self.flow.seq_info = readback_of(plan, mic=r'E:\rec\camera.mp4')
        rec, _ = self.run_final()
        self.assertEqual(rec['payload']['state'], 'blocked')
        self.assertIn('good mic', rec['payload']['error'])
        bad = readback_of(plan)
        bad['items'][0]['inp'] = str(int(bad['items'][0]['inp']) + 5 * F)            # V1 nest piece from the wrong place
        self.flow.calls.clear()
        self.flow.seq_info = bad
        rec, _ = self.run_final()
        self.assertEqual(rec['payload']['state'], 'blocked')
        self.assertNotIn('tq_export', [c for c, _ in self.flow.calls])

    def test_other_videos_keep_the_older_path(self):
        self.assertIsNone(self.flow.clean_cut('another-video'))
        self.assertIsNone(self.flow.clean_gate('another-video'))
        cut = self.flow._put('another-video', 'edit_cut', dict(state='verified', plan=dict(map_digest='m1')), parent='prop')
        v = self.flow.visuals('another-video', cut['id'])
        self.assertEqual(v['parent'], cut['id'])


if __name__ == '__main__':
    unittest.main()
