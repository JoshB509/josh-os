"""The editable clean transcript on a small made-up cut with the same shape as a real AutoCut pass (exact ticks)."""
import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from studio import clean_transcript as ct

T, F = ct.T, 8467191533
s = lambda x: int(round(x * T))


def info(pieces):
    """A read-back with A1 pieces (mic seconds) laid end to end on whole frames."""
    items, at = [], 0
    for a, b in pieces:
        n = round((s(b) - s(a)) / F)
        items.append(dict(kind='a', track=0, start=str(at), end=str(at + n * F), inp=str(s(a))))
        at += n * F
    return dict(items=items)


def snap(pieces):
    """Make pieces end on whole frames from their start, like Premiere's own pieces."""
    return [(a, (s(a) + round((s(b) - s(a)) / F) * F) / T) for a, b in pieces]


# words: "one two three, three four five six" with a repeat at 3.0-3.6 and a pause at 5.0-6.0
WORDS = [dict(word=w, start=a, end=b) for w, a, b in [('one', 1.0, 1.4), ('two', 1.5, 1.9), ('three,', 2.0, 2.5),
         ('three', 3.0, 3.5), ('four', 3.6, 4.0), ('five', 4.1, 4.9), ('six', 6.2, 6.8), ('', 7.0, 7.5)]]
RAW = dict(mic_in=s(0.5), mic_out=s(0.5) + round((s(7.5) - s(0.5)) / F) * F, camera_in=s(0.5) - s(0.25))
SIL = snap([(0.5, 5.0), (6.0, 7.5)])
SIL[-1] = (SIL[-1][0], RAW['mic_out'] / T)
AUTO = snap([(0.5, 1.95), (2.75, 5.0)]) + [SIL[-1]]


class Model(unittest.TestCase):
    def model(self, edits=()):
        return ct.CleanTranscript(WORDS, RAW, info(SIL), info(AUTO), F, edits)

    def test_ids_provenance_and_no_edit_identity(self):
        m = self.model()
        st = {r['text']: r for r in m.states()}
        self.assertEqual([w['id'] for w in m.words], ['n0001', 'n0002', 'n0003', 'n0004', 'n0005', 'n0006', 'n0007'])  # pause marker dropped
        self.assertEqual(st['three,']['state'], 'removed')
        self.assertEqual(st['three,']['removed_by'], 'repeat')
        self.assertEqual(st['three']['state'], 'kept')
        pm, new = m.plan_map()
        self.assertEqual([[p['mic_in'], p['mic_out']] for p in pm['pieces']], m.k_auto)
        self.assertEqual(new, [])
        self.assertEqual({r['by'] for r in m.removals}, {'outside_camera', 'silences', 'repeat'})

    def test_remove_then_restore_round_trip(self):
        rm = self.model([dict(op='remove', words=['n0005'], by='josh')])
        st = {r['id']: r for r in rm.states()}
        self.assertEqual((st['n0005']['state'], st['n0005']['removed_by']), ('removed', 'josh'))
        self.assertEqual(st['n0004']['state'], 'kept')
        self.assertEqual(st['n0006']['state'], 'kept')
        back = self.model([dict(op='remove', words=['n0005'], by='josh'), dict(op='restore', words=['n0005'], by='josh')])
        self.assertEqual({r['id']: r['state'] for r in back.states()}, {r['id']: r['state'] for r in self.model().states()})

    def test_restore_brings_back_an_autocut_removal_with_real_media(self):
        m = self.model([dict(op='restore', words=['n0003'], by='josh')])
        st = {r['id']: r for r in m.states()}
        self.assertEqual(st['n0003']['state'], 'kept')
        self.assertTrue(st['n0003']['restored'])
        pm, new = m.plan_map()
        for p in pm['pieces']:
            self.assertEqual((p['mic_out'] - p['mic_in']) % F, 0)
            self.assertEqual(p['mic_in'] - p['camera_in'], RAW['mic_in'] - RAW['camera_in'])
        self.assertTrue(all(q['mic_in'] >= p['mic_out'] for p, q in zip(pm['pieces'], pm['pieces'][1:])))
        self.assertTrue(new)

    def test_untouched_autocut_edges_stay_to_the_tick(self):
        m = self.model([dict(op='remove', words=['n0006'], by='josh')])
        keep, _ = m.keeps()
        self.assertIn(m.k_auto[0], keep)          # the first piece is untouched by an edit later on

    def test_quiet_point_uses_the_mic(self):
        pcm = np.full(int(8 * ct.SR), 0.1, dtype='<f4')
        pcm[int(1.42 * ct.SR):int(1.45 * ct.SR)] = 0.0      # the only quiet spot between "one" and "two"
        m = self.model([dict(op='remove', words=['n0002'], by='josh')])
        keep, new = m.keeps(pcm)
        starts = [e for e in new if e['side'] == 'end' and e['piece'] == 0]
        self.assertTrue(starts)
        self.assertAlmostEqual(starts[0]['quiet_point'] / T, 1.435, delta=0.02)

    def test_restoring_a_whole_gap_loses_nothing(self):
        """Restoring 'three,' fills AutoCut's gap between its first two pieces. Every tick of both pieces and of the
        gap must stay in, with no new hole, and any rounding adds under one frame of word-free mic."""
        m = self.model([dict(op='restore', words=['n0003'], by='josh')])
        keep, _ = m.keeps()
        inside = lambda a, b: any(x <= a and b <= y for x, y in keep)
        self.assertTrue(inside(m.k_auto[0][0], m.k_auto[1][1]), 'the two pieces and the gap between them are one stretch')
        total_before = sum(b - a for a, b in m.k_auto) + (m.k_auto[1][0] - m.k_auto[0][1])
        total_after = sum(b - a for a, b in keep)
        self.assertGreaterEqual(total_after, total_before)
        self.assertLess(total_after - total_before, F * len(keep))
        for e in m.extended:
            self.assertLess(e['ticks'], F)
        for a, b in keep:
            self.assertEqual((b - a) % F, 0)

    def test_rounding_next_to_words_on_both_sides_is_refused(self):
        words = [dict(word=w, start=a, end=b) for w, a, b in [('one', 1.0, 1.4), ('two', 1.4, 1.9), ('three', 1.9, 2.5)]]
        raw = dict(mic_in=s(1.0) - F * 3, mic_out=s(1.0) - F * 3 + 60 * F, camera_in=s(0.5))
        auto = [((s(1.0) - F * 3) / T, (s(1.0) - F * 3 + 60 * F) / T)]
        m = ct.CleanTranscript(words, raw, info(auto), info(auto), F, [dict(op='remove', words=['n0002'], by='josh')])
        with self.assertRaises(ValueError) as e:
            m.keeps()
        self.assertIn('stops', str(e.exception))

    def test_bad_edits_are_refused(self):
        with self.assertRaises(ValueError):
            self.model([dict(op='remove', words=['n0001', 'n0003'], by='josh')]).keeps()
        with self.assertRaises(ValueError):
            self.model([dict(op='shuffle', words=['n0001'], by='josh')]).keeps()


if __name__ == '__main__':
    unittest.main()
