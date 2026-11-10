"""Tariq's editing workflow: the planner on synthetic audio and the revision and approval rules."""
import shutil
import tempfile
import unittest
from pathlib import Path

import numpy as np

from dajoshi import edit_plan as ep
from studio.edit_flow import EditFlow, verify_readback
from studio.store import Conflict, Store

SR = 16000
CAM = Path(r'E:\Videos\DaJoshiCut\tests\sample-5min.mp4')
MIC = Path(r'E:\Videos\DaJoshiCut\tests\sample-5min-mic.wav')


def synth(spans, total_s, noise=1e-4, seed=1):
    """Speech-like bursts at the given (start, end, amplitude) seconds over a low noise floor."""
    rng = np.random.default_rng(seed)
    x = rng.normal(0, noise, int(total_s * SR))
    for a, b, amp in spans:
        n = int((b - a) * SR)
        x[int(a * SR):int(a * SR) + n] += amp * np.sin(np.arange(n) * 2 * np.pi * 180 / SR)
    return x.astype(np.float32)


def words_at(spans):
    return ep.words_from_segments([dict(words=[dict(start=a, end=b, word=f' w{i}') for i, (a, b) in enumerate(spans)])], SR)


class PlannerTests(unittest.TestCase):
    def setUp(self):
        # words, a 1.0 s pause, an isolated loud sound inside a 1.5 s gap, and a word right after a short sound
        self.wspans = [(0.5, 0.9), (0.95, 1.4), (2.4, 2.8), (2.85, 3.2), (4.7, 5.1), (5.15, 5.6)]
        sound = [(3.8, 4.05, 0.3)]
        self.pcm = synth([(a, b, 0.3) for a, b in self.wspans] + sound, 7.0)
        self.words = words_at(self.wspans)
        self.events, self.meta = ep.discover(self.words, self.pcm, SR)

    def kinds(self):
        return [(e['kind'], e['label'], e['proposed_action']) for e in self.events]

    def test_pauses_are_kept_and_long_ones_flagged(self):
        pauses = [e for e in self.events if e['kind'] == 'pause' and not e.get('outer')]
        self.assertTrue(pauses)
        self.assertTrue(all(e['proposed_action'] == 'keep' for e in pauses))
        self.assertTrue(any(e['review'] for e in pauses if e['measurements']['duration_ms'] >= 700))

    def test_isolated_sound_is_a_possible_cough_never_a_cough(self):
        snd = [e for e in self.events if e['kind'] == 'other_sound']
        self.assertEqual(len(snd), 1)
        self.assertEqual(snd[0]['label'], '[possible cough]')
        self.assertTrue(snd[0]['review'])
        self.assertNotIn('cough', [e['kind'] for e in self.events])

    def test_default_plan_never_cuts_a_word_or_a_pause(self):
        rem, issues = ep.plan(self.events, self.words, {}, len(self.pcm), SR)
        for w in self.words:
            self.assertFalse(any(a < w['end'] and w['start'] < b for a, b in rem), w)
        pauses = [e for e in self.events if e['kind'] == 'pause' and not e.get('outer')]
        for p in pauses:
            inside = sum(max(0, min(b, p['end']) - max(a, p['start'])) for a, b in rem)
            snd = sum(e['end'] - e['start'] + 2 * ep.ms(30, SR) for e in self.events if e['kind'] == 'other_sound' and p['start'] <= e['start'] < p['end'])
            self.assertLessEqual(inside, snd)

    def test_removing_words_keeps_neighbours_whole(self):
        rem, issues = ep.plan(self.events, self.words, {}, len(self.pcm), SR, word_actions={'w00003': 'remove', 'w00004': 'remove'})
        t = ep.transcript(self.words, self.events, {}, rem, SR, {'w00003': 'remove', 'w00004': 'remove'})
        state = {i['id']: i['state'] for i in t if i['type'] == 'word'}
        self.assertEqual(state['w00003'], 'removed')
        self.assertEqual(state['w00004'], 'removed')
        for k in ('w00002', 'w00005'):
            w = next(x for x in self.words if x['id'] == k)
            self.assertFalse(any(a < w['end'] and w['start'] < b for a, b in rem))

    def test_shortened_pause_keeps_the_target(self):
        p = next(e for e in self.events if e['kind'] == 'pause' and not e.get('outer') and e['measurements']['duration_ms'] >= 900)
        rem, _ = ep.plan(self.events, self.words, {p['event_id']: dict(action='shorten', target_ms=400)}, len(self.pcm), SR)
        cut = sum(max(0, min(b, p['end']) - max(a, p['start'])) for a, b in rem)
        self.assertAlmostEqual((p['end'] - p['start'] - cut) / SR, 0.4, delta=0.02)

    def test_restart_candidates_default_to_keep(self):
        w = ep.words_from_segments([dict(words=[dict(start=i * .3, end=i * .3 + .25, word=t) for i, t in
                                                enumerate('so the first thing so the first thing is clear'.split())])], SR)
        c = ep.restart_candidates(w, SR)
        self.assertEqual(len(c), 1)
        self.assertEqual(c[0]['proposed_action'], 'keep')
        self.assertEqual(c[0]['word_ids'], ['w00001', 'w00002', 'w00003', 'w00004'])

    def test_transcribed_ahem_is_a_possible_throat_clear(self):
        w = ep.words_from_segments([dict(words=[dict(start=.5, end=.9, word=' Right.'), dict(start=1.5, end=1.8, word=' ahem,'),
                                                dict(start=2.5, end=2.9, word=' So')])], SR)
        pcm = synth([(.5, .9, .3), (1.5, 1.8, .3), (2.5, 2.9, .3)], 4)
        ev, _ = ep.discover(w, pcm, SR)
        tc = [e for e in ev if e['kind'] == 'throat_clear']
        self.assertEqual(tc[0]['label'], '[possible throat clear]')
        rem, _ = ep.plan(ev, w, {}, len(pcm), SR)
        self.assertTrue(any(a <= w[1]['start'] and w[1]['end'] <= b for a, b in rem))


class OutputMapTests(unittest.TestCase):
    F = 8467191533  # Executive Questions' own ticks per frame, not a rounded 30 fps

    def test_rounding_once_slivers_and_merges(self):
        keeps = [[0, 16000 * 2], [16000 * 2 + 100, 16000 * 4], [16000 * 5, 16000 * 5 + 200], [16000 * 6, 16000 * 8]]
        m = ep.output_map(keeps, SR, 0, self.F)
        src = [p for p in m['pieces'] if p['kind'] == 'source']
        self.assertEqual(len(src), 2)  # the sub-frame cut merged, the 12 ms sliver dropped
        for p in src:
            self.assertEqual(p['camera_in'] % self.F, 0)
            self.assertEqual(p['camera_out'] % self.F, 0)
        self.assertTrue(all(a['out_end'] == b['out_start'] for a, b in zip(m['pieces'], m['pieces'][1:])))
        self.assertLessEqual(m['max_rounding_ticks'], self.F // 2 + 1)

    def test_room_tone_is_separate_and_removes_nothing(self):
        keeps = [[0, SR * 10]]
        ins = [dict(after_sample=SR * 10, media_path='rt.wav', media_in_ticks=0, media_out_ticks=self.F * 15)]
        m = ep.output_map(keeps, SR, 0, self.F, ins)
        self.assertEqual([p['kind'] for p in m['pieces']], ['source', 'room_tone'])
        self.assertEqual(ep.map_word(m['pieces'], SR * 5, SR), m['pieces'][0]['out_start'] + (SR * 5) * (ep.TICKS_PER_SECOND // SR) - m['pieces'][0]['mic_in'])

    def test_mic_before_camera_is_refused(self):
        with self.assertRaises(ValueError):
            ep.output_map([[0, SR * 3]], SR, 2.0, self.F)


class AnimationHandoffTests(unittest.TestCase):
    def test_presses_are_found_loosely_and_in_order(self):
        from studio import edit_animations as ea
        text = "so here's the thing anyway you're on slide three and the CEO asks you a question you can't answer then what"
        w = ep.words_from_segments([dict(words=[dict(start=i * .4, end=i * .4 + .3, word=' ' + t) for i, t in enumerate(text.split())])], SR)
        self.assertEqual(ea.locate(w, 'you on slide three'), 5)       # script says "you", transcript says "you're"
        self.assertIsNone(ea.locate(w, 'completely different words here'))
        self.assertIsNone(ea.locate(w, 'slide three', start=10))       # never searches backwards
        m = ep.output_map([[0, len(w) * int(.4 * SR) + SR]], SR, 0, ep.TICKS_PER_SECOND // 30)
        parts = [dict(kind='talk', script="so here's the thing"),
                 dict(kind='animation', n=1, script="anyway you're on slide three and the CEO asks you a question",
                      presses=[dict(press=1, words='you on slide three'), dict(press=2, words='the CEO asks')]),
                 dict(kind='talk', script="you can't answer then what")]
        shots, problems = ea.plan_shots(w, [], m['pieces'], parts, {1: dict(start=4, end=5, type='pip')}, ep.TICKS_PER_SECOND // 30, SR)
        self.assertEqual([(s['press'], s['beat'], s['previous'], s['alpha']) for s in shots], [(1, 4, None, True), (2, 5, 4, True)])
        self.assertTrue(shots[0]['end_f'] == shots[1]['start_f'] and shots[1]['end_f'] > shots[1]['start_f'])
        self.assertEqual(problems, [])

    def test_a_press_in_cut_material_is_left_out_and_reported(self):
        from studio import edit_animations as ea
        w = ep.words_from_segments([dict(words=[dict(start=i * .4, end=i * .4 + .3, word=' ' + t) for i, t in enumerate('one two three four five six'.split())])], SR)
        removals = [[w[2]['start'] - 10, w[3]['end'] + 10]]
        m = ep.output_map(ep.keeps_from(removals, int(3 * SR)), SR, 0, ep.TICKS_PER_SECOND // 30)
        parts = [dict(kind='animation', n=1, script='one two three four five six', presses=[dict(press=1, words='one two'), dict(press=2, words='three four')])]
        shots, problems = ea.plan_shots(w, removals, m['pieces'], parts, {1: dict(start=0, end=1, type='full')}, ep.TICKS_PER_SECOND // 30, SR)
        self.assertEqual([s['press'] for s in shots], [1])
        self.assertTrue(any('press 2' in p for p in problems))


@unittest.skipUnless(CAM.is_file() and MIC.is_file(), 'sample media missing')
class FlowRuleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.store = Store(self.tmp)
        self.p = self.store.create('Test video')
        self.flow = EditFlow(self.store, Path(__file__).resolve().parents[1])
        self.src = self.flow.register(self.p['id'], CAM, MIC)
        wspans = [(0.5, 0.9), (0.95, 1.4), (2.4, 2.8), (2.85, 3.2)]
        pcm = synth([(a, b, .3) for a, b in wspans], 4)
        f = self.tmp / 'pcm.f32'
        pcm.tofile(f)
        words = words_at(wspans)
        events, meta = ep.discover(words, pcm, SR)
        self.payload = dict(sources_id=self.src['id'], sources_digest=self.src['digest'], hashes=dict(mic='x' * 64, camera='y' * 64),
                            headers={}, sync=dict(verified=True, offset_seconds=0.0), asr={}, measurements=meta, words=words,
                            events=events, decisions={}, word_actions={}, notes=[], editorial=dict(ran=False), revision=1, inserts=[], pcm=str(f))
        self.prop = self.flow._put(self.p['id'], 'edit_proposal', self.payload, parent=self.src['id'])

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_unverified_sync_blocks_approval(self):
        bad = dict(self.payload, sync=dict(verified=False))
        prop = self.flow._put(self.p['id'], 'edit_proposal', bad, parent=self.src['id'])
        with self.assertRaises(ValueError):
            self.flow.approve_script(self.p['id'], prop['id'], prop['digest'])

    def test_revisions_approvals_and_staleness(self):
        pid, prop = self.p['id'], self.prop
        with self.assertRaises(Conflict):
            self.flow.approve_script(pid, prop['id'], 'stale')
        with self.assertRaises(Conflict):
            self.flow.cut_plan(pid, prop['id'], prop['digest'], 8467191533)
        v = self.flow.revise(pid, prop['id'], prop['digest'], [], [dict(ids=['w00002'], action='remove')], 'a note')
        self.assertEqual(v['proposal']['revision'], 2)
        with self.assertRaises(Conflict):  # the old revision can't be approved now
            self.flow.approve_script(pid, prop['id'], prop['digest'])
        new = v['proposal']
        self.flow.approve_script(pid, new['id'], new['digest'], 'ok')
        plan = self.flow.cut_plan(pid, new['id'], new['digest'], 8467191533)
        self.assertEqual(plan['sequence_name'], 'CLEAN_CUT_r02')
        # a further change makes the approval stale
        self.flow.revise(pid, new['id'], new['digest'], [], [dict(ids=['w00002'], action='default')])
        with self.assertRaises(Conflict):
            self.flow.cut_plan(pid, new['id'], new['digest'], 8467191533)
        # state survives a restart
        again = EditFlow(self.store, Path(__file__).resolve().parents[1])
        self.assertEqual(again.view(pid)['proposal']['revision'], 3)
        self.assertEqual(again.view(pid)['notes'][0]['text'], 'a note')

    def test_build_without_premiere_is_blocked_not_claimed(self):
        pid, prop = self.p['id'], self.prop
        self.flow.approve_script(pid, prop['id'], prop['digest'])
        self.flow.bridge = 'http://127.0.0.1:9'  # nothing listens here
        r = self.flow.build_cut(pid, prop['id'], prop['digest'], 8467191533)
        self.assertEqual(r['payload']['state'], 'blocked')
        self.assertEqual(self.flow.status(pid)[3]['state'], 'blocked')

    def test_native_proof_logic_with_a_fake_premiere(self):
        """MOCK: a fake bridge whose frames depend on the master's key. Checks the routine, not Premiere."""
        from studio.edit_flow import native_proof
        pid, prop = self.p['id'], self.prop
        self.flow.approve_script(pid, prop['id'], prop['digest'])
        plan = self.flow.cut_plan(pid, prop['id'], prop['digest'], 8467191533)
        key = {'Choke': 7.7}

        def fake(cmd, args, timeout=0):
            if cmd == 'tq_master_read':
                return dict(effects=[dict(role='presenter', effect='AE.ADBE Ultra Key', params=[dict(name='Choke', value=key['Choke'])]),
                                     dict(role='shadow', effect='AE.ADBE Ultra Key', params=[dict(name='Choke', value=0)])])
            if cmd == 'tq_master_set':
                key[args['param']] = args['value']
                return {}
            if cmd == 'tq_export_frames':
                for i, t in enumerate(args['times']):
                    (Path(args['dir']) / f"{args['prefix']}-{i:03d}.png").write_bytes(f"{t}:{key['Choke']}".encode())
                return {}
        self.flow._put(pid, 'edit_cut', dict(state='verified', plan=plan), parent=prop['id'])
        self.flow.bridge_call = fake
        r = native_proof(self.flow, pid)
        self.assertTrue(r['passed'])
        self.assertTrue(all(x['changed'] and x['restored'] for x in r['pieces']))
        self.assertEqual(key['Choke'], 7.7)  # put back

    def test_bad_changes_are_refused(self):
        pid, prop = self.p['id'], self.prop
        for ch, words in [([dict(event_id='nope', action='keep')], []), ([], [dict(ids=['w99999'], action='remove')]),
                          ([dict(event_id=self.payload['events'][0]['event_id'], action='explode')], [])]:
            with self.assertRaises(ValueError):
                self.flow.revise(pid, prop['id'], prop['digest'], ch, words)

    def test_readback_catches_camera_audio_and_slips(self):
        m = ep.output_map([[0, SR * 4], [SR * 6, SR * 9]], SR, 0, 8467191533)
        plan = dict(map=m)
        src = [p for p in m['pieces'] if p['kind'] == 'source']
        good = dict(video=[dict(start=p['out_start'], end=p['out_end']) for p in src],
                    audio=[dict(start=p['out_start'], end=p['out_end'], inp=p['mic_in'], role='mic') for p in src])
        self.assertEqual(verify_readback(plan, good), [])
        bad = dict(good, audio=good['audio'] + [dict(start=0, end=1, inp=0, role='camera')])
        self.assertTrue(verify_readback(plan, bad))
        slip = dict(good, audio=[dict(a, inp=a['inp'] + 8467191533) for a in good['audio']])
        self.assertTrue(verify_readback(plan, slip))


if __name__ == '__main__':
    unittest.main()
