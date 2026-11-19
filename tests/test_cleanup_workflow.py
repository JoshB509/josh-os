"""Tariq's cleanup pass as a workflow step: word-free span removal, Josh-only decisions, the AutoCut pass running it after
its review, YAMNet's hash check and candidate merging, and more emphasis negatives. No Premiere, no network."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

from studio import autocut_pass as ap
from studio import cleanup_pass as cp
from studio.clean_edit import CleanEdit, CleanEditError
import studio.clean_edit_more  # noqa: F401  (attaches cleanup, decide, edit_span)


def words(*items):
    return [dict(word=w, start=a, end=b, state='kept', seq_start=a) for w, a, b in items]


class Emphasis(unittest.TestCase):
    def test_more_emphasis_stays(self):
        for ws in (words(('no', 1, 1.2), ('no', 1.25, 1.45), ('no', 1.5, 1.7), ('way', 1.7, 2.0)),
                   words(('really', 1, 1.3), ('really', 1.35, 1.7), ('good', 1.7, 2.0)),
                   words(('bit', 1, 1.2), ('by', 1.2, 1.3), ('bit', 1.3, 1.5)),
                   words(('more', 1, 1.2), ('and', 1.2, 1.3), ('more', 1.3, 1.6), ('people', 1.6, 2.0))):
            self.assertEqual(cp.find(ws), [], ws)

    def test_a_paused_restart_of_a_content_word_is_proposed_with_its_second_take_as_fallback(self):
        c = cp.find(words(('the', 1, 1.1), ('reason,', 1.1, 1.5), ('the', 2.1, 2.2), ('reason', 2.2, 2.6), ('so', 2.6, 2.8)))
        self.assertEqual([(x['cut_text'], x['alt_cut_text'], x['confidence']) for x in c], [('the reason,', 'the reason', 'high')])


class CleanTimeline(unittest.TestCase):
    def test_i_think_uh_i_think_is_a_restart(self):
        c = cp.find(words(('I', 1.0, 1.1), ('think', 1.1, 1.4), ('uh', 1.6, 1.8), ('I', 2.0, 2.1), ('think', 2.1, 2.4), ('we', 2.4, 2.6)))
        self.assertEqual([(x['kind'], x['cut_text']) for x in c], [('restart', 'I think uh')])
        self.assertTrue(c[0]['keep_text'].startswith('I think we'))

    def test_distant_takes_in_the_clean_cut_are_not_a_restart_or_cluster(self):
        ws = words(('day.', 10.0, 10.3), ('Okay.', 10.5, 10.9), ('Okay.', 13.0, 13.4), ('How', 13.5, 13.7))
        self.assertEqual(cp.find(ws), [])                                     # 2.1 s apart in the clean cut
        ws = words(('the', 1, 1.1), ('reason,', 1.1, 1.5), ('the', 3.5, 3.6), ('reason', 3.6, 4.0))
        self.assertEqual(cp.find(ws), [])

    def test_gaps_are_read_on_the_clean_timeline_not_the_raw_recording(self):
        ws = words(('day.', 10.0, 10.3), ('Okay.', 10.5, 10.9), ('Okay.', 30.0, 30.4), ('How', 30.5, 30.7))
        ws[2]['seq_start'], ws[3]['seq_start'] = 11.3, 11.8                  # AutoCut removed the 19 s between them
        self.assertEqual([(x['kind'], x['cut_text']) for x in cp.find(ws)], [('filler', 'Okay.')])

    def test_emphasis_with_a_filler_between_is_not_forced(self):
        self.assertEqual(cp.find(words(('very', 1, 1.2), ('very', 1.25, 1.45), ('good', 1.5, 1.8))), [])


class SpanRemoval(unittest.TestCase):
    """Removing a word-free sound by its times: the mic is checked, never just the transcript gap."""
    T = 254016000000

    def model(self, ws, edits, require_pcm=True):
        from studio import clean_transcript as ct
        m = ct.CleanTranscript.__new__(ct.CleanTranscript)
        m.frame, m.raw, m.k_auto, m.extended, m.edits, m.removals = self.T // 30, [0, 10 * self.T], [[0, 10 * self.T]], [], edits, []
        m.require_pcm, m._ref = require_pcm, None
        m.words = [dict(id=f'n{i}', text=t, start=int(a * self.T), end=int(b * self.T)) for i, (t, a, b) in enumerate(ws)]
        return m

    def mic(self, voiced=(), noise=()):
        rate = 16000
        x = np.random.default_rng(1).normal(0, 0.0003, rate * 10).astype(np.float32)
        t = np.arange(rate * 10) / rate
        for a, b in voiced:                                   # words, and any voiced sound
            i, j = int(a * rate), int(b * rate)
            x[i:j] += (0.2 * np.sin(2 * np.pi * 140 * t[i:j]) + 0.1 * np.sin(2 * np.pi * 280 * t[i:j])).astype(np.float32)
        for a, b in noise:                                    # an unvoiced breath or cough
            i, j = int(a * rate), int(b * rate)
            x[i:j] += np.random.default_rng(2).normal(0, 0.05, j - i).astype(np.float32)
        return x

    def span(self, a, b):
        return dict(op='remove_span', span=[int(a * self.T), int(b * self.T)], words=[])

    def test_an_unvoiced_sound_between_words_comes_out_on_quiet_grid_points(self):
        ws = [('So', 1.0, 1.4), ('the', 3.0, 3.4)]
        pcm = self.mic(voiced=[(1.0, 1.4), (3.0, 3.4)], noise=[(1.9, 2.4)])
        keep, _ = self.model(ws, [self.span(1.8, 2.5)]).keeps(pcm)
        self.assertEqual(len(keep), 2)
        cut = (keep[0][1] / self.T, keep[1][0] / self.T)
        self.assertTrue(1.4 < cut[0] <= 1.9 and 2.4 <= cut[1] < 3.0, cut)    # in quiet mic, the noise gone, no word touched
        self.assertTrue(all(a % (self.T // 30) == 0 and b % (self.T // 30) == 0 for a, b in keep))

    def test_voiced_sound_in_a_transcript_gap_is_refused(self):
        ws = [('So', 1.0, 1.4), ('the', 3.0, 3.4)]
        pcm = self.mic(voiced=[(1.0, 1.4), (3.0, 3.4), (2.0, 2.3)])          # speech Premiere did not write down
        with self.assertRaises(ValueError) as e:
            self.model(ws, [self.span(1.8, 2.5)]).keeps(pcm)
        self.assertIn('voiced speech', str(e.exception))

    def test_an_edge_with_no_quiet_point_nearby_is_refused(self):
        ws = [('So', 1.0, 1.4), ('the', 3.0, 3.4)]
        pcm = self.mic(voiced=[(1.0, 1.4), (3.0, 3.4)], noise=[(1.5, 2.9)])  # loud noise right up to both edges
        with self.assertRaises(ValueError):
            self.model(ws, [self.span(1.8, 2.5)]).keeps(pcm)

    def test_a_span_touching_a_word_is_refused(self):
        pcm = self.mic(voiced=[(1.0, 1.4)])
        with self.assertRaises(ValueError):
            self.model([('So', 1.0, 1.4)], [self.span(1.3, 2.0)]).keeps(pcm)

    def test_production_without_the_mic_fails_closed(self):
        with self.assertRaises(ValueError):
            self.model([('So', 1.0, 1.4)], [self.span(1.8, 2.5)]).keeps(None)
        with self.assertRaises(ValueError):
            self.model([('a', 1.0, 1.4), ('b', 1.4, 1.8)], [dict(op='remove', words=['n0'])]).keeps(None)


class StrictBoundaries(unittest.TestCase):
    """_quiet has no timestamp fallback in production."""

    def m(self, require_pcm):
        from studio import clean_transcript as ct
        m = ct.CleanTranscript.__new__(ct.CleanTranscript)
        m.require_pcm = require_pcm
        return m

    def test_touching_words_missing_mic_or_a_tiny_gap_fail_closed(self):
        from studio import clean_transcript as ct
        pcm = np.zeros(16000 * 3, np.float32)
        for window, mic in (((1000, 1000), pcm), ((ct.T, 2 * ct.T), None), ((ct.T, ct.T + ct.TPS * 50), pcm)):
            with self.assertRaises(ValueError):
                self.m(True)._quiet(window, mic)
            self.m(False)._quiet(window, mic)                 # unit-test helpers only


class Decisions(unittest.TestCase):
    """Decisions are bound to the revision (evidence, audio, edits) the proposals came from."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        ev = root / 'ev'
        ev.mkdir()
        for n in ('premiere-native-words-mic.json', 'after-silences-readback.json', 'after-repeat-readback.json', 'autocut-raw-r01-build.json'):
            (ev / n).write_text('{"x": 1}', encoding='utf-8')
        (root / 'mic.f32').write_bytes(np.zeros(160, np.float32).tobytes())
        self.ev, self.pcm = ev, root / 'mic.f32'
        self.ce = CleanEdit(lambda pid: root / pid)
        (root / 'v').mkdir()
        self.ce._save('v', dict(evidence=str(ev), prproj='', mic='', pcm=str(self.pcm), sequence='S', frame=1, master='M', edits=[],
                                builds=[], approved=None))
        st = self.ce._load('v')
        rev = self.ce.revision(st)
        st['cleanup'] = dict(revision=rev, proposals=[dict(key='k1', status='ok', use_ids=['n1'], cut_text='Okay.'),
                                                      dict(key='k2', status='blocked', use_ids=None, cut_text='the reason,'),
                                                      dict(key='k3', status='ok', use_ids=['n7'], cut_text='And')])
        self.ce._save('v', st)
        self.rev = rev['digest']
        self.ce.view = lambda pid: self.ce._load(pid)
        self.ce.model = lambda pid, st, edits=None: type('M', (), {'plan_map': lambda s, pcm: ({}, [])})()

    def tearDown(self):
        self.tmp.cleanup()

    def test_only_josh_decides_and_a_blocked_proposal_cannot_be_accepted(self):
        with self.assertRaises(CleanEditError):
            self.ce.decide('v', 'k1', 'accept', 'tariq', from_josh=False)
        with self.assertRaises(CleanEditError):
            self.ce.decide('v', 'k2', 'accept', 'Josh', from_josh=True)
        st = self.ce.decide('v', 'k2', 'manual', 'Josh', from_josh=True)
        self.assertEqual(st['cleanup_decisions']['k2']['decision'], 'manual')
        self.assertEqual(st['edits'], [])

    def test_accept_edits_once_then_every_other_proposal_is_stale(self):
        st = self.ce.decide('v', 'k1', 'accept', 'Josh', from_josh=True, revision_digest=self.rev)
        self.assertEqual([e['words'] for e in st['edits']], [['n1']])
        with self.assertRaises(CleanEditError):
            self.ce.decide('v', 'k1', 'accept', 'Josh', from_josh=True)          # a replay
        with self.assertRaises(CleanEditError):
            self.ce.decide('v', 'k3', 'accept', 'Josh', from_josh=True)          # the edits changed, so k3 is out of date
        self.assertEqual(len(self.ce._load('v')['edits']), 1)

    def test_a_change_to_the_audio_or_evidence_makes_proposals_stale(self):
        self.pcm.write_bytes(np.ones(160, np.float32).tobytes())
        with self.assertRaises(CleanEditError):
            self.ce.decide('v', 'k1', 'accept', 'Josh', from_josh=True)
        self.pcm.write_bytes(np.zeros(160, np.float32).tobytes())
        (self.ev / 'after-repeat-readback.json').write_text('{"x": 2}', encoding='utf-8')
        with self.assertRaises(CleanEditError):
            self.ce.decide('v', 'k1', 'dismiss', 'Josh', from_josh=True)

    def test_a_page_showing_an_older_revision_is_refused(self):
        with self.assertRaises(CleanEditError):
            self.ce.decide('v', 'k1', 'accept', 'Josh', from_josh=True, revision_digest='0' * 64)

    def test_two_accepts_at_once_make_one_edit(self):
        import threading
        errors = []

        def go():
            try:
                self.ce.decide('v', 'k1', 'accept', 'Josh', from_josh=True)
            except CleanEditError as e:
                errors.append(e)
        ts = [threading.Thread(target=go) for _ in range(4)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        self.assertEqual((len(self.ce._load('v')['edits']), len(errors)), (1, 3))

    def test_automatic_resume_reuses_same_revision_and_preserves_decisions(self):
        before = self.ce._file('v').read_bytes()
        with mock.patch('studio.cleanup_review.run_pass') as run:
            cleanup = self.ce.cleanup('v', reuse=True)
        run.assert_not_called()
        self.assertEqual(cleanup['proposals'][0]['key'], 'k1')
        self.assertEqual(self.ce._file('v').read_bytes(), before)

    def test_automatic_cleanup_refreshes_after_edit_without_applying_proposals(self):
        st = self.ce._load('v')
        st['edits'] = [dict(op='remove', words=['n9'], by='Josh')]
        st['cleanup_decisions'] = {'old': dict(decision='dismiss')}
        self.ce._save('v', st)
        full = dict(at='t', folder='f', proposals=[dict(kind='restart', use_ids=['n1'], status='ok')], nonspeech={}, buildable=1)
        with mock.patch('studio.cleanup_review.run_pass', return_value=full) as run:
            result = self.ce.cleanup('v', reuse=True)
        run.assert_called_once()
        saved = self.ce._load('v')
        self.assertEqual(saved['edits'], st['edits'])
        self.assertEqual(saved['cleanup_decisions'], st['cleanup_decisions'])
        self.assertIsNone(saved['approved'])
        self.assertIsNone(result['proposals'][0]['decision'])
        self.assertEqual(result['revision']['digest'], self.ce.revision(saved)['digest'])

    def test_a_run_that_finishes_after_an_edit_saves_nothing(self):
        from unittest import mock

        def slow_run(*a, **k):
            st = self.ce._load('v')
            st['edits'] = [dict(op='remove', words=['n9'])]                       # Josh edited while it ran
            self.ce._save('v', st)
            return dict(at='t', folder='f', proposals=[], nonspeech={}, buildable=0)
        with mock.patch('studio.cleanup_review.run_pass', slow_run):
            with self.assertRaises(CleanEditError):
                self.ce.cleanup('v')
        self.assertEqual(self.ce._load('v')['cleanup']['proposals'][0]['key'], 'k1')   # the old pass is untouched


class AutoCutPassRunsIt(unittest.TestCase):
    def test_the_cleanup_runs_after_the_review_and_a_failure_never_undoes_it(self):
        p = ap.AutoCutPass.__new__(ap.AutoCutPass)
        calls = []
        for name in ('raw', 'native'):
            setattr(p, name, lambda n=name: calls.append(n))
        p.silences = lambda by: calls.append('silences')
        p.repeat = lambda by: calls.append('repeat')
        p.review = lambda: calls.append('review') or {'waiting_for': 'Josh'}
        p.cleanup = lambda: calls.append('cleanup')
        p.say = lambda m: calls.append('said')
        self.assertEqual(p.run('Josh'), {'waiting_for': 'Josh'})
        self.assertEqual(calls, ['raw', 'silences', 'repeat', 'native', 'review', 'cleanup'])
        calls.clear()
        p.cleanup = lambda: (_ for _ in ()).throw(RuntimeError('no mic'))
        self.assertEqual(p.run('Josh'), {'waiting_for': 'Josh'})
        self.assertIn('said', calls)


class YAMNet(unittest.TestCase):
    def test_a_model_file_that_is_not_the_recorded_one_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / 'yamnet.tflite'
            f.write_bytes(b'not the model')
            with self.assertRaises(ValueError):
                cp.yamnet_scores(np.zeros(16000, np.float32), [(0, 1)], model=f)

    def test_candidates_merge_and_say_what_they_overlap(self):
        s = [dict(at=1.0, end=1.975, Speech=0.1, Cough=0.3, **{'Throat clearing': 0.0}, Breathing=0, Laughter=0, Sniff=0, Gasp=0),
             dict(at=1.25, end=2.225, Speech=0.2, Cough=0.5, **{'Throat clearing': 0.1}, Breathing=0, Laughter=0, Sniff=0, Gasp=0),
             dict(at=5.0, end=5.975, Speech=0.99, Cough=0.0, **{'Throat clearing': 0.0}, Breathing=0, Laughter=0, Sniff=0, Gasp=0)]
        c = cp.yamnet_candidates(s, [dict(word='So', start=2.0, end=2.3), dict(word='', start=1.0, end=1.5)])
        self.assertEqual(len(c), 1)
        self.assertEqual((c[0]['at'], c[0]['end'], c[0]['top'], c[0]['Cough'], c[0]['windows']), (1.0, 2.225, 'Cough', 0.5, 2))
        self.assertEqual(c[0]['overlaps_words'], ['So'])

    @unittest.skipUnless((cp.YAMNET_DIR / 'yamnet.tflite').is_file(), 'the official model is not on this PC')
    def test_the_real_model_runs_on_kept_spans_only(self):
        rate = 16000
        rng = np.random.default_rng(0)
        x = rng.normal(0, 0.001, rate * 4).astype(np.float32)
        scores = cp.yamnet_scores(x, [(1.0, 3.0)])
        self.assertEqual(scores['kept_samples'], scores['covered_samples'])
        self.assertTrue(all(1.0 <= w['at'] and w['end'] <= 3.0 for w in scores['windows']))
        self.assertTrue(set(cp.YAMNET_CLASSES) <= set(scores['windows'][0]))


class YAMNetWindows(unittest.TestCase):
    """What each window sends to the model, checked sample by sample with a stand-in classifier."""
    rate = 16000

    def run_mock(self, x, spans):
        seen = []
        out = cp.yamnet_scores(x, spans, classify=lambda seg: seen.append(seg.copy()) or {'Cough': 0.0})
        return out, seen

    def test_a_short_kept_clip_is_zero_padded_never_filled_with_the_removed_cough_after_it(self):
        x = np.zeros(self.rate * 2, np.float32)
        x[:1600] = 0.01                                       # 0.1 s kept
        x[1600:1600 + 8000] = 0.9                             # a loud cough right after, cut out
        out, seen = self.run_mock(x, [(0.0, 0.1)])
        self.assertEqual(len(seen), 1)
        self.assertTrue(np.all(seen[0][:1600] == np.float32(0.01)))
        self.assertTrue(np.all(seen[0][1600:] == 0))          # zero padding, not the removed cough
        self.assertEqual((out['windows'][0]['end'], out['windows'][0]['padded']), (0.1, True))
        self.assertEqual(out['kept_samples'], out['covered_samples'])

    def test_a_long_span_includes_its_last_sample_and_never_runs_past_it(self):
        x = np.arange(self.rate * 3, dtype=np.float32)
        out, seen = self.run_mock(x, [(0.5, 2.5)])
        self.assertEqual(seen[-1][-1], np.float32(int(2.5 * self.rate) - 1))   # the span's very last sample
        self.assertTrue(all(s.max() < 2.5 * self.rate and s.min() >= 0.5 * self.rate for s in seen))
        self.assertEqual(out['covered_samples'], int(2.0 * self.rate))

    def test_disjoint_spans_and_joins_never_share_a_window(self):
        x = np.arange(self.rate * 6, dtype=np.float32)
        spans = [(0.2, 0.9), (1.5, 3.2), (3.2, 3.5)]           # the last two meet at a join
        out, seen = self.run_mock(x, spans)
        for seg in seen:
            real = seg[seg > 0] if seg[0] > 0 else seg[1:][seg[1:] > 0]
            lo, hi = real.min(), real.max()
            self.assertTrue(any(a * self.rate <= lo and hi < b * self.rate for a, b in spans), (lo, hi))
        self.assertEqual(out['kept_samples'], out['covered_samples'])
        self.assertEqual(out['kept_samples'], int(round((0.7 + 1.7 + 0.3) * self.rate)))


if __name__ == '__main__':
    unittest.main()


class FinalRangeChecks(SpanRemoval):
    """The removed range is checked again after every edge is placed and snapped, not just the requested times."""

    def test_noise_just_after_a_short_word_never_pulls_the_cut_back_over_the_word(self):
        ws = [('So', 1.0, 1.4), ('uh', 2.00, 2.03), ('the', 3.0, 3.4)]      # a 30 ms word right before the noise
        pcm = self.mic(voiced=[(1.0, 1.4), (2.00, 2.03), (3.0, 3.4)], noise=[(2.06, 2.40)])
        pcm[int(1.94 * 16000):int(2.0 * 16000)] = 0                          # dead quiet just BEFORE the short word
        m = self.model(ws, [])
        edge = m._quiet_edge(int(2.035 * self.T), pcm, m._speech_ref(pcm))
        self.assertTrue(edge is None or edge >= int(2.03 * self.T), edge)  # never jumps back over "uh" to the quieter spot
        try:
            keep, _ = self.model(ws, [self.span(2.035, 2.45)]).keeps(pcm)
        except ValueError:
            return                                                           # refusing is also safe
        uh = (int(2.00 * self.T), int(2.03 * self.T))
        self.assertTrue(any(a <= uh[0] and uh[1] <= b for a, b in keep), keep)   # "uh" is still fully kept

    def test_a_grid_snap_that_would_take_a_word_is_caught_by_the_final_check(self):
        ws = [('So', 1.0, 1.4), ('the', 3.0, 3.4)]
        pcm = self.mic(voiced=[(1.0, 1.4), (3.0, 3.4)], noise=[(1.9, 2.4)])
        m = self.model(ws, [self.span(1.8, 2.5)])
        m._grid = lambda keep, pcm=None: [[0, int(1.2 * self.T)], [int(2.5 * self.T), 10 * self.T]]   # a snap gone wrong
        with self.assertRaises(ValueError) as e:
            m.keeps(pcm)
        self.assertIn('"So"', str(e.exception))

    def test_a_grid_snap_into_voiced_sound_is_caught(self):
        ws = [('So', 1.0, 1.4), ('the', 3.0, 3.4)]
        pcm = self.mic(voiced=[(1.0, 1.4), (3.0, 3.4), (2.55, 2.75)], noise=[(1.9, 2.4)])   # voiced, unwritten, after the span
        m = self.model(ws, [self.span(1.8, 2.5)])
        m._grid = lambda keep, pcm=None: [[0, int(1.6 * self.T)], [int(2.8 * self.T), 10 * self.T]]
        with self.assertRaises(ValueError) as e:
            m.keeps(pcm)
        self.assertIn('voiced', str(e.exception))

    def test_a_word_cut_with_a_loud_edge_is_refused(self):
        ws = [('a', 1.0, 1.4), ('b', 1.5, 1.9), ('c', 2.0, 2.4)]
        pcm = self.mic(voiced=[(1.0, 1.4), (1.5, 1.9), (2.0, 2.4)], noise=[(1.4, 1.5), (1.9, 2.0)])   # no quiet between words
        pcm[int(1.4 * 16000):int(1.5 * 16000)] *= 40
        pcm[int(1.9 * 16000):int(2.0 * 16000)] *= 40
        with self.assertRaises(ValueError) as e:
            self.model(ws, [dict(op='remove', words=['n1'])]).keeps(pcm)
        self.assertIn('not quiet', str(e.exception))
