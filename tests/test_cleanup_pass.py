"""Tariq's cleanup pass after AutoCut (studio/cleanup_pass.py). Word timings are copied from the real 4 Signs native
transcript (autocut-evidence-2026-11-12/review.json), so the positives and negatives are the ones Josh's video has."""
import unittest

import numpy as np

from studio.cleanup_pass import cough_candidates, find


def words(*items, state='kept'):
    """(word, start, end) triples, in order."""
    return [dict(word=w, start=a, end=b, state=state, seq_start=a) for w, a, b in items]


def cuts(ws):
    return [(c['kind'], c['cut_text']) for c in find(ws)]


class Real4Signs(unittest.TestCase):
    def test_restarts_left_after_autocut_are_found_and_the_second_take_is_kept(self):
        theyd = words(('knew,', 241.41, 241.89), ("they'd", 242.25, 242.73), ('have,', 242.73, 243.03), ("they'd", 243.75, 244.23),
                      ('have', 244.23, 244.44), ('no', 244.44, 244.68), ('choice', 244.68, 245.40))
        c = find(theyd)
        self.assertEqual([(x['kind'], x['cut_text'], x['confidence']) for x in c], [('restart', "they'd have,", 'high')])
        self.assertTrue(c[0]['keep_text'].startswith("they'd have no choice"))
        self.assertEqual(c[0]['cut_ids'], ['n0002', 'n0003'])
        reason = words(('up.', 468.75, 469.05), ('Now', 476.10, 476.46), ('the', 476.46, 476.58), ('reason,', 476.58, 476.97),
                       ('the', 477.54, 477.72), ('reason', 477.72, 478.29), ('so', 478.29, 478.74))
        self.assertEqual(cuts(reason), [('restart', 'the reason,')])
        and_and = words(('out.', 417.36, 417.66), ('And', 417.96, 418.38), ('And', 424.10, 424.30), ("that's", 424.30, 424.60))
        and_and[2]['seq_start'], and_and[3]['seq_start'] = 418.49, 418.69   # AutoCut took out the 5.7 s between, so they sit together
        self.assertEqual(cuts(and_and), [('restart', 'And')])

    def test_okay_okay_and_sorry_sorry_are_no_longer_exempt(self):
        okay = words(('day.', 294.45, 294.78), ('Okay.', 300.78, 301.23), ('Okay.', 302.10, 302.52), ('How', 302.79, 303.03))
        self.assertEqual(cuts(okay), [('filler', 'Okay.')])
        sorry = words(('in.', 604.95, 605.28), ('And', 605.85, 606.03), ('then', 606.03, 606.36), ('sorry', 606.84, 607.16),
                      ('sorry.', 607.17, 607.46), ('So', 607.50, 607.92), ("it's", 608.37, 608.61))
        c = find(sorry)
        self.assertEqual([(x['kind'], x['cut_text']) for x in c], [('apology', 'And then sorry sorry.')])
        self.assertTrue(c[0]['keep_text'].startswith("So it's"))

    def test_again_and_again_is_emphasis_not_a_repeat(self):
        again = words(('told', 232.02, 232.26), ('them', 232.26, 232.41), ('again', 232.41, 232.83), ('and', 233.25, 233.58),
                      ('again,', 233.58, 234.03), ('and', 234.33, 234.51), ('I', 234.51, 234.60), ('kept', 234.60, 234.90))
        self.assertEqual(cuts(again), [])

    def test_acted_out_sorry_is_left_alone(self):
        self.assertEqual(cuts(words(('sounding', 105.9, 106.3), ('like', 106.3, 106.4), ("they're", 106.4, 106.65), ('sorry', 106.65, 107.0),
                                    ('for', 107.0, 107.1), ('wasting', 107.1, 107.5))), [])
        self.assertEqual(cuts(words(('as,', 127.4, 127.7), ('oh,', 127.7, 128.0), ('sorry.', 128.01, 128.4), ('This', 128.6, 128.8),
                                    ('might', 128.8, 129.0))), [])


class Patterns(unittest.TestCase):
    def test_okay_yeah_yeah_okay_keeps_the_final_word_and_phrase(self):
        ws = words(('done.', 9.0, 9.4), ('okay', 10.0, 10.3), ('yeah', 10.5, 10.7), ('yeah', 10.75, 10.95), ('okay', 11.4, 11.7),
                   ('so', 11.8, 12.0), ('the', 12.0, 12.1), ('first', 12.1, 12.4))
        c = find(ws)
        self.assertEqual([(x['kind'], x['cut_text']) for x in c], [('filler', 'okay yeah yeah')])
        self.assertTrue(c[0]['keep_text'].startswith('okay so the first'))

    def test_other_emphasis_idioms_and_intensifiers_stay(self):
        for ws in (words(('step', 1, 1.3), ('by', 1.3, 1.4), ('step', 1.4, 1.8)),
                   words(('over', 1, 1.3), ('and', 1.3, 1.4), ('over', 1.4, 1.8)),
                   words(('very', 1, 1.2), ('very', 1.25, 1.5), ('good', 1.5, 1.8))):
            self.assertEqual(cuts(ws), [], ws)

    def test_a_paused_intensifier_double_is_a_restart(self):
        self.assertEqual(cuts(words(('very', 1, 1.2), ('very', 1.6, 1.8), ('good', 1.8, 2.1))), [('restart', 'very')])

    def test_words_autocut_removed_are_ignored(self):
        ws = words(('the', 1, 1.1), ('reason', 1.1, 1.5)) + words(('the', 1.6, 1.7), ('reason', 1.7, 2.0), state='repeat') + \
            words(('so', 2.1, 2.3), ('many', 2.3, 2.6))
        self.assertEqual(cuts(ws), [])

    def test_so_right_and_yes_are_not_fillers(self):
        self.assertEqual(cuts(words(('done.', 1, 1.3), ('So', 2.0, 2.2), ('right', 2.2, 2.4), ('now', 2.4, 2.6))), [])


class Coughs(unittest.TestCase):
    def test_sound_between_words_is_a_candidate_and_words_are_never_candidates(self):
        rate = 16000
        x = np.random.default_rng(1).normal(0, 0.0005, rate * 3).astype(np.float32)
        x[int(0.2 * rate):int(0.8 * rate)] += np.sin(np.arange(int(0.6 * rate)) / 8).astype(np.float32) * 0.3   # a word
        x[int(1.5 * rate):int(1.7 * rate)] += np.random.default_rng(2).normal(0, 0.2, int(0.2 * rate)).astype(np.float32)  # a cough
        c = cough_candidates(x, [(0, 3)], [dict(start=0.2, end=0.8)])
        self.assertEqual(len(c), 1)
        self.assertAlmostEqual(c[0]['at'], 1.5, delta=0.05)
        self.assertGreater(c[0]['flatness'], 0.3)                    # noise-like, unlike a voiced word
        self.assertEqual(cough_candidates(x, [(0, 1.0)], [dict(start=0.2, end=0.8)]), [])   # outside the kept audio


if __name__ == '__main__':
    unittest.main()


class Review(unittest.TestCase):
    """cleanup_review.assess: first take, then second take, then blocked; joins measured against the study."""

    def test_falls_back_to_the_second_take_then_blocks(self):
        from studio import cleanup_review as CR
        T = CR.T

        class Model:
            def __init__(self, edits):
                self.cut = edits[0]['words'] if edits else []
                self.words = [dict(id='n1', text='out.', start=0, end=T), dict(id='n2', text='And', start=int(1.2 * T), end=int(1.5 * T)),
                              dict(id='n3', text='And', start=int(2.0 * T), end=int(2.3 * T)), dict(id='n4', text="that's", start=int(2.3 * T), end=3 * T)]

            def keeps(self, pcm=None):
                if self.cut == ['n2'] or self.cut == ['n9']:
                    raise ValueError('both sides have a word right there')
                if self.cut == ['n3']:
                    return [[0, int(1.5 * T)], [int(2.3 * T), 3 * T]], []
                return [[0, 3 * T]], []
        props = [dict(kind='restart', at=1.2, cut_ids=['n2'], cut_text='And', alt_cut_ids=['n3'], alt_cut_text='And'),
                 dict(kind='filler', at=1.2, cut_ids=['n9'], cut_text='x', alt_cut_ids=[], alt_cut_text='')]
        a, b = CR.assess(Model, props, None)
        self.assertEqual((a['status'], a['use_ids'], a['join']['before'], a['join']['after'], a['join_flag']), ('alt', ['n3'], 'And', "that's", 'below_reference_lead_unreviewed'))
        self.assertEqual(b['status'], 'blocked')
        self.assertIn('both sides have a word', b['why_blocked'])


class Screen(unittest.TestCase):
    """nonspeech_screen looks across all kept audio, inside words too. It flags, it never decides."""

    def test_a_noisy_burst_is_flagged_and_a_voiced_vowel_is_not(self):
        from studio.cleanup_pass import nonspeech_screen
        rate = 16000
        t = np.arange(rate) / rate
        vowel = (0.2 * np.sin(2 * np.pi * 140 * t) + 0.1 * np.sin(2 * np.pi * 280 * t) + 0.05 * np.sin(2 * np.pi * 420 * t)).astype(np.float32)
        burst = np.random.default_rng(3).normal(0, 0.15, int(0.25 * rate)).astype(np.float32)
        from scipy.signal import butter, lfilter
        b, a = butter(2, [600 / 8000, 3000 / 8000], btype='band')
        burst = lfilter(b, a, burst).astype(np.float32) * 3
        x = np.concatenate([np.zeros(rate // 4, np.float32), vowel, np.zeros(rate // 4, np.float32), burst, np.zeros(rate // 4, np.float32)])
        x += np.random.default_rng(4).normal(0, 0.0003, len(x)).astype(np.float32)
        words = [dict(word='ah', start=0.25, end=1.25)]
        c = nonspeech_screen(x, [(0, len(x) / rate)], words)
        self.assertTrue(c, 'the burst should be flagged')
        self.assertTrue(all(x['at'] >= 1.4 for x in c), c)                        # nothing inside the voiced vowel
        self.assertTrue(all(x['overlaps_words'] == [] for x in c))
        self.assertEqual(nonspeech_screen(x, [(0, 1.3)], words), [])              # only kept audio is screened


class FrameGrid(unittest.TestCase):
    """The 13 Nov 2026 build: a piece whose two edges were both new was whole frames long but off the grid, and
    Premiere moved its audio in-point 27 ms while the video stayed. Every new edge must land on the grid."""

    def model(self, words):
        from studio import clean_transcript as ct
        m = ct.CleanTranscript.__new__(ct.CleanTranscript)
        m.frame, m.raw, m.k_auto, m.extended = 1000, [0, 100000], [[0, 100000]], []
        m.words = [dict(id=f'n{i}', text=t, start=a, end=b) for i, (t, a, b) in enumerate(words)]
        return m

    def test_new_edges_snap_onto_the_grid_in_word_free_mic(self):
        m = self.model([('Now', 47300, 47900), ('the', 48700, 49300)])
        out = m._on_grid([[0, 40000], [46500 + 690, 47900 + 690], [48300, 100000]])
        for a, b in out:
            self.assertEqual((a % 1000, b % 1000), (0, 0), out)
        self.assertEqual(out[1], [47000, 48000])                 # 47,190 -> 47,000 (before "Now"); 48,590 -> 48,000 (before "the")

    def test_autocut_edges_never_move_and_a_word_blocks(self):
        m = self.model([('word', 40100, 40990)])
        with self.assertRaises(ValueError):
            m._on_grid([[0, 40500], [60000, 100000]])            # both grid points next to 40,500 are inside the word
        m = self.model([])
        m.k_auto = [[0, 40500], [60000, 100000]]
        self.assertEqual(m._on_grid([[0, 40500], [60000, 100000]]), [[0, 40500], [60000, 100000]])
