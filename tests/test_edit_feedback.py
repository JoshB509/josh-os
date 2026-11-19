"""Josh's edit feedback on 4 Signs (11 Nov 2026): restarts cut, nothing hidden from review, long silences cut,
animations held on screen (L-cut), talking head gaps found, and old proposals redone under the new rules."""
import sys
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dajoshi import edit_plan as ep  # noqa: E402
from studio import edit_animations as ea  # noqa: E402

SR = 16000


def words(spec):
    """[(text, start s, end s)] as transcript words."""
    return [dict(id=f'w{i:05d}', text=t, start=int(a * SR), end=int(b * SR), probability=0.9) for i, (t, a, b) in enumerate(spec, 1)]


def speech(spans, total_s):
    """A mic track with tone where the words are and near silence elsewhere."""
    pcm = np.full(int(total_s * SR), 1e-5, dtype=np.float32)
    t = np.arange(int(total_s * SR)) / SR
    for a, b in spans:
        sl = slice(int(a * SR), int(b * SR))
        pcm[sl] = 0.3 * np.sin(2 * np.pi * 180 * t[sl])
    return pcm


class Restarts(unittest.TestCase):
    def test_the_reason_the_reason_is_cut_and_listed_as_cut(self):
        w = words([('Now,', 0.5, 0.9), ('the', 1.6, 1.7), ('reason,', 1.7, 2.0), ('the', 2.1, 2.3), ('reason', 2.7, 3.1), ('so', 3.1, 3.4),
                   ('many', 3.7, 3.9)])
        ev = ep.restart_candidates(w, SR)
        self.assertEqual(len(ev), 1)
        e = ev[0]
        self.assertEqual((e['label'], e['proposed_action'], e['review'], e['text']), ('[restart]', 'remove', False, 'the reason,'))
        removals, _ = ep.plan(ev, w, {}, int(4.5 * SR), SR)
        self.assertEqual(len(removals), 1)
        a, b = removals[0][:2]
        self.assertTrue(w[0]['end'] <= a and b <= w[3]['start'])   # between "Now," and the second "the"
        # The review lists it as cut.
        self.assertTrue(ep.queue(ev, {}, []))

    def filler(self, start, n):
        return [(f'x{i}', start + i * 0.5, start + 0.3 + i * 0.5) for i in range(n)]

    def test_a_whole_line_said_again_is_a_retake_and_cut(self):
        line = 'which leads us to mistake number three'.split()
        w = words([(t, 1 + i * 0.3, 1.25 + i * 0.3) for i, t in enumerate(line)] + self.filler(3.5, 6)
                  + [(t, 7 + i * 0.3, 7.25 + i * 0.3) for i, t in enumerate(line)])
        e = ep.restart_candidates(w, SR)[0]
        self.assertEqual((e['label'], e['proposed_action'], e['review']), ('[retake]', 'remove', False))
        self.assertIn('again 6s later, so the first take is cut', e['reason'])

    def test_a_short_repeat_a_few_seconds_later_is_for_review(self):
        w = words([('the', 1, 1.2), ('central', 1.2, 1.5), ('team', 1.5, 1.7)] + self.filler(2, 12)
                  + [('the', 9, 9.2), ('central', 9.2, 9.5), ('team', 9.5, 9.7)])
        e = ep.restart_candidates(w, SR)[0]
        self.assertEqual((e['proposed_action'], e['review'], e['label']), ('keep', True, '[possible retake]'))

    def test_everyday_wording_far_apart_is_not_flagged(self):
        w = words([('in', 1, 1.2), ('the', 1.2, 1.4)] + self.filler(2, 40) + [('in', 25, 25.2), ('the', 25.2, 25.4)])
        self.assertEqual(ep.restart_candidates(w, SR), [])

    def test_a_list_with_the_same_opening_is_not_cut(self):
        spec = []
        t = 1.0
        for item in ('detail.', 'complexity.', 'background.'):
            for x in ("There's", 'so', 'much', item):
                spec.append((x, t, t + 0.25))
                t += 0.3
        ev = ep.restart_candidates(words(spec), SR)
        self.assertTrue(ev)
        self.assertTrue(all(e['proposed_action'] == 'keep' and e['review'] for e in ev))

    def test_a_triple_restart_is_still_cut(self):
        spec = [('I', 1, 1.1), ('know', 1.1, 1.3), ('because...', 1.3, 1.6), ('I', 2.0, 2.1), ('know', 2.1, 2.3), ('because', 2.3, 2.5),
                ('after', 2.5, 2.7), ('I', 3.2, 3.3), ('know', 3.3, 3.5), ('because', 3.5, 3.7), ('after', 3.7, 3.9), ('teaching', 3.9, 4.3)]
        ev = ep.restart_candidates(words(spec), SR)
        self.assertEqual([e['proposed_action'] for e in ev], ['remove', 'remove'])

    def test_a_repeat_tariq_did_not_remove_stays_in_review(self):
        w = words([('the', 1, 1.2), ('central', 1.2, 1.5), ('team', 1.5, 1.7)] + self.filler(2, 12)
                  + [('the', 9, 9.2), ('central', 9.2, 9.5), ('team', 9.5, 9.7)])
        ev = ep.apply_editorial(ep.restart_candidates(w, SR), w)
        self.assertTrue(ev[0]['review'])
        self.assertIn('Have a listen', ev[0]['reason'])

    def test_editorial_pass_cannot_bring_a_restart_back(self):
        w = words([('the', 1.6, 1.7), ('reason,', 1.7, 2.0), ('the', 2.1, 2.3), ('reason', 2.7, 3.1)])
        ev = ep.apply_editorial(ep.restart_candidates(w, SR), w)
        self.assertEqual((ev[0]['proposed_action'], ev[0]['review']), ('remove', False))


class Silences(unittest.TestCase):
    def setUp(self):
        spec = [('Fine.', 1.0, 1.5), ('Next', 1.8, 2.1), ('one.', 2.1, 2.5), ('Then', 9.5, 9.9), ('more.', 9.9, 10.4)]
        self.w = words(spec)
        self.events, _ = ep.discover(self.w, speech([(a, b) for _, a, b in spec], 11), SR)
        self.pauses = [e for e in self.events if e['kind'] == 'pause' and not e.get('outer')]

    def test_a_long_pause_is_cut_to_a_natural_gap_with_no_review(self):
        long = max(self.pauses, key=lambda e: e['end'] - e['start'])
        self.assertEqual((long['proposed_action'], long['target_ms'], long['review']), ('shorten', 400, False))
        removals, _ = ep.plan(self.events, self.w, {}, 11 * SR, SR)
        left = sum(b - a for a, b in [r[:2] for r in removals] if a > 2.5 * SR and b < 9.5 * SR)
        self.assertGreater(left / SR, 6.0)   # about 7s of the 7.4s gap goes

    def test_a_short_pause_is_kept(self):
        short = min(self.pauses, key=lambda e: e['end'] - e['start'])
        self.assertEqual(short['proposed_action'], 'keep')

    def test_tariq_cannot_keep_a_long_silence(self):
        long = max(self.pauses, key=lambda e: e['end'] - e['start'])
        out = ep.apply_editorial(self.events, self.w, pauses=[dict(event_id=long['event_id'], action='keep', reason='dramatic')])
        self.assertEqual(next(e for e in out if e['event_id'] == long['event_id'])['proposed_action'], 'shorten')

    def test_josh_can_still_put_a_pause_back(self):
        long = max(self.pauses, key=lambda e: e['end'] - e['start'])
        removals, _ = ep.plan(self.events, self.w, {long['event_id']: dict(action='keep')}, 11 * SR, SR)
        self.assertFalse(any(a > 2.5 * SR and b < 9.5 * SR for a, b in [r[:2] for r in removals]))


class AnimationHold(unittest.TestCase):
    F = 8467191533   # Josh's sequence frame

    def shot(self, n, a_s, b_s):
        fps = ep.TICKS_PER_SECOND / self.F
        return dict(animation=n, press=1, start_f=round(a_s * fps), end_f=round(b_s * fps))

    def test_a_short_card_stays_up_and_runs_into_the_next_sentence(self):
        fps = ep.TICKS_PER_SECOND / self.F
        shots = [self.shot(1, 10.0, 11.3)]   # "The point stays yours", 1.3s
        ea.hold(shots, self.F, round(100 * fps))
        self.assertAlmostEqual((shots[0]['end_f'] - shots[0]['start_f']) / fps, 2.2, delta=0.05)

    def test_a_longer_one_is_held_a_moment_past_its_last_word(self):
        fps = ep.TICKS_PER_SECOND / self.F
        shots = [self.shot(1, 10.0, 15.0), self.shot(1, 15.0, 18.0)]
        ea.hold(shots, self.F, round(100 * fps))
        self.assertAlmostEqual(shots[1]['end_f'] / fps, 18.8, delta=0.05)
        self.assertAlmostEqual(shots[0]['end_f'] / fps, 15.0, delta=0.05)   # only the last press of a run grows

    def test_never_runs_into_the_next_animation(self):
        fps = ep.TICKS_PER_SECOND / self.F
        shots = [self.shot(1, 10.0, 11.0), self.shot(2, 11.5, 14.0)]
        ea.hold(shots, self.F, round(100 * fps))
        self.assertEqual(shots[0]['end_f'], shots[1]['start_f'])

    def test_talking_head_gaps(self):
        fps = ep.TICKS_PER_SECOND / self.F
        shots = [self.shot(1, 5, 8), self.shot(2, 70, 75)]
        gaps = ea.talking_head_gaps(shots, self.F, round(120 * fps))
        self.assertEqual([(round(a), round(b)) for a, b in gaps], [(8, 70), (75, 120)])


class OldProposals(unittest.TestCase):
    def test_a_proposal_made_under_old_rules_is_redone(self):
        from studio.edit_flow import EditFlow
        from dajoshi.media import VERBATIM_PROMPT
        new = dict(payload=dict(asr=dict(prompt=VERBATIM_PROMPT), measurements=dict(config=dict(ep.CONFIG))))
        old = dict(payload=dict(asr=dict(), measurements=dict(config={k: v for k, v in ep.CONFIG.items() if 'long_pause' not in k})))
        self.assertFalse(EditFlow.rules_changed(None, new))
        self.assertTrue(EditFlow.rules_changed(None, old))

    def test_transcription_keeps_repeats(self):
        src = (ROOT / 'dajoshi' / 'media.py').read_text(encoding='utf-8')
        self.assertIn('initial_prompt=VERBATIM_PROMPT', src)


if __name__ == '__main__':
    unittest.main()
