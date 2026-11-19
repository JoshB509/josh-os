"""Sam's chapters and description (studio/chapters.py). Fixtures only: no YouTube, no Linklr, no live links."""
import sys
import unittest
from pathlib import Path

ROOT = Path(r'E:\Videos\DaJoshiResolve')
sys.path.insert(0, str(ROOT))
from studio import chapters as C  # noqa: E402

T, R = C.TICKS, C.MIC_RATE


def words(spec):
    """[(text, mic seconds)] as mic-sample words, each 0.3 s long."""
    return [dict(id=f'w{i:05d}', text=t, start=int(s * R), end=int((s + 0.3) * R)) for i, (t, s) in enumerate(spec, 1)]


# Cut: export 0-60 s is mic 100-160 s; export 60-120 s is mic 300-360 s. Mic 160-300 s was cut out.
MAP = dict(duration_ticks=120 * T, pieces=[dict(kind='source', mic_in=100 * T, mic_out=160 * T, out_start=0, out_end=60 * T),
                                           dict(kind='source', mic_in=300 * T, mic_out=360 * T, out_start=60 * T, out_end=120 * T)])


class Placement(unittest.TestCase):
    def test_words_land_on_the_export_and_cut_words_are_dropped(self):
        w = words([('Hello', 100.0), ('there.', 130.0), ('gone', 200.0), ('straddle', 159.9), ('Back', 300.5)])
        placed, dropped = C.place_words(w, MAP)
        self.assertEqual([(x['text'], x['start']) for x in placed], [('Hello', 0.0), ('there.', 30.0), ('Back', 60.5)])
        self.assertEqual(dropped, dict(cut_out=1, straddling=1, repeated=0))

    def test_a_stretch_used_twice_gets_unique_ids(self):
        rep_map = dict(duration_ticks=20 * T, pieces=[dict(kind='source', mic_in=100 * T, mic_out=110 * T, out_start=0, out_end=10 * T),
                                                      dict(kind='source', mic_in=100 * T, mic_out=110 * T, out_start=10 * T, out_end=20 * T)])
        placed, dropped = C.place_words(words([('Again', 101.0)]), rep_map)
        self.assertEqual([(x['id'], x['source_id'], x['start']) for x in placed], [('w00001', 'w00001', 1.0), ('w00001#2', 'w00001', 11.0)])
        self.assertEqual(dropped['repeated'], 1)


class Signposts(unittest.TestCase):
    def placed(self, text):
        return [dict(id=f'w{i}', text=t, start=float(i), end=i + 0.3) for i, t in enumerate(text.split())]

    def test_the_sentence_that_says_the_number_wins_over_a_bridge(self):
        p = self.placed('But most of us never listen in the first place, Which leads us to mistake number three, which is waiting.')
        j, why = C.signpost('Mistake 3', p, near=0)
        self.assertEqual((p[j]['text'], why), ('Which', 'says "mistake three" there'))

    def test_without_a_number_the_sentence_naming_the_item(self):
        p = self.placed('Some setup first. And now that you know the frameworks, this mistake is to stop making them dig. So if you ask.')
        j, why = C.signpost('Mistake 4', p, near=len(p) - 3)
        self.assertEqual(p[j]['text'], 'And')
        self.assertIn('no spoken number', why)

    def test_nothing_found_leaves_sams_pick(self):
        self.assertEqual(C.signpost('Mistake 4', self.placed('Nothing numbered here at all.'), near=0), (None, ''))
        self.assertEqual(C.signpost('The Promotion Rule', self.placed('Mistake number one.'), near=0), (None, ''))


class Anchors(unittest.TestCase):
    PLACED = [dict(id=f'w{i}', text=t, start=float(i), end=i + 0.3) for i, t in enumerate(
        'Welcome back. So mistake number one is the apology. Then mistake number two is the lecture.'.split())]

    def test_an_anchor_in_the_middle_of_a_line_resolves_to_that_exact_word(self):
        w, why = C.resolve_anchor('mistake number two', self.PLACED, near_id='w0')
        self.assertEqual((w['id'], w['text']), ('w10', 'mistake'))

    def test_anchors_must_be_real_unique_words(self):
        self.assertIsNone(C.resolve_anchor('mistake number five', self.PLACED)[0])
        self.assertIsNone(C.resolve_anchor('mistake number', self.PLACED)[0])        # appears twice
        self.assertIsNone(C.resolve_anchor('mistake', self.PLACED)[0])               # too short
        out = C.build_chapters([dict(word_id='w0', anchor='mistake number five', title='Mistake 5')], self.PLACED, 300)
        self.assertIn('not in the final transcript', out['flags'][0])

    def test_evidence_is_the_words_actually_there(self):
        out = C.build_chapters([dict(word_id='w0', anchor='So mistake number one', title='Mistake 1'),
                                dict(word_id='w0', anchor='Then mistake number two', title='Mistake 2')],
                               [dict(w, start=w['start'] * 20) for w in self.PLACED], 400)
        self.assertTrue(out['chapters'][1]['evidence'].startswith('So mistake number one'))


class Rules(unittest.TestCase):
    def setUp(self):
        self.placed = [dict(id=f'w{i}', text='x', start=float(i), end=i + 0.3) for i in range(0, 300)]

    def pick(self, *items):
        return [dict(word_id=f'w{s}', title=t, evidence='said here') for s, t in items]

    def test_youtube_rules_after_rounding(self):
        out = C.build_chapters(self.pick((40, 'Sign 1'), (100, 'Sign 2'), (200, 'What Executives Want to See')), self.placed, 299.9)
        self.assertTrue(out['ok'])
        self.assertEqual(C.chapter_lines(out['chapters']), ['00:00 Introduction', '00:40 Sign 1', '01:40 Sign 2', '03:20 What Executives Want to See'])
        shown = [c['shown'] for c in out['chapters']]
        self.assertEqual(shown, sorted(shown))
        self.assertTrue(all(b - a >= 10 for a, b in zip(shown, shown[1:] + [299])))

    def test_too_early_boundaries_merge_and_flag_rather_than_shift(self):
        out = C.build_chapters(self.pick((5, 'Sign 1'), (60, 'Sign 2'), (65, 'Sign 3'), (120, 'Sign 4'), (295, 'Sign 5')), self.placed, 299.9)
        self.assertEqual([c['title'] for c in out['chapters']], ['Introduction', 'Sign 2', 'Sign 4'])
        self.assertEqual([c['shown'] for c in out['chapters']], [0, 60, 120])   # nothing moved away from its words
        self.assertEqual(len([f for f in out['flags'] if 'merged' in f]), 3)

    def test_cta_and_ending_chapters_fold_into_the_one_before(self):
        out = C.build_chapters(self.pick((40, 'Sign 1'), (90, 'Get the free handbook'), (150, 'Sign 2'), (250, 'Thanks for watching')),
                               self.placed, 299.9)
        self.assertEqual([c['title'] for c in out['chapters']], ['Introduction', 'Sign 1', 'Sign 2'])
        self.assertEqual(len([f for f in out['flags'] if 'folds into' in f]), 2)

    def test_list_items_never_give_away_the_answer(self):
        out = C.build_chapters(self.pick((40, 'Sign 1 · Apologising before you speak'), (100, 'Rule 2: say less')), self.placed, 299.9)
        self.assertEqual([c['title'] for c in out['chapters']][1:], ['Sign 1', 'Rule 2'])
        self.assertTrue(all(c.get('review') for c in out['chapters'][1:]))

    def test_a_numbered_video_keeps_only_its_items_and_offers_extras(self):
        out = C.build_chapters(self.pick((40, 'Mistake 1'), (80, 'Mistake 2'), (120, 'How to Build the Question'), (160, 'Mistake 3'),
                                         (200, 'Mistake 4'), (240, 'The Answer Pyramid')), self.placed, 299.9)
        self.assertEqual([c['title'] for c in out['chapters']], ['Introduction', 'Mistake 1', 'Mistake 2', 'Mistake 3', 'Mistake 4'])
        self.assertEqual([c['title'] for c in out['optional']], ['How to Build the Question', 'The Answer Pyramid'])
        self.assertEqual(len([f for f in out['flags'] if 'Optional extra' in f]), 2)

    def test_too_few_is_flagged_not_padded(self):
        out = C.build_chapters(self.pick((40, 'Sign 1')), self.placed, 299.9)
        self.assertFalse(out['ok'])
        self.assertEqual(len(out['chapters']), 2)
        self.assertIn("won't pad it", out['flags'][-1])

    def test_a_pick_not_in_the_export_is_dropped(self):
        out = C.build_chapters([dict(word_id='w99999', title='Sign 1')] + self.pick((60, 'Sign 2'), (120, 'Sign 3')), self.placed, 299.9)
        self.assertEqual([c['title'] for c in out['chapters']], ['Introduction', 'Sign 2', 'Sign 3'])


class Description(unittest.TestCase):
    CH = [dict(title='Introduction', shown=0), dict(title='Sign 1', shown=40), dict(title='Sign 2', shown=100)]

    def test_order_lead_magnet_then_sales_then_brief_then_chapters(self):
        text, flags = C.description([dict(role='sales', url='https://lnk/sales', label='Work with me'),
                                     dict(role='lead_magnet', url='https://lnk/lm', label='Free handbook')], 'Brief.', self.CH)
        self.assertEqual(text.split('\n'), ['Free handbook: https://lnk/lm', 'Work with me: https://lnk/sales', '', 'Brief.', '',
                                            '00:00 Introduction', '00:40 Sign 1', '01:40 Sign 2'])
        self.assertEqual(flags, [])

    def test_sales_alone_and_missing_links(self):
        self.assertEqual(C.description([dict(role='sales', url='https://lnk/s')], 'B.', [])[0].split('\n')[0], 'Work with me: https://lnk/s')
        text, flags = C.description([], 'B.', self.CH)
        self.assertTrue(text.startswith('[Linklr link for this video goes here]'))
        self.assertIn('Make it in Linklr first', flags[0])
        self.assertEqual(C.description([dict(role='lead_magnet', url='http://not-https')], 'B.', [])[1][0][:20], 'No tracked link is r')


class Binding(unittest.TestCase):
    def setUp(self):
        import tempfile
        from types import SimpleNamespace
        from studio.store import Store
        from studio.release_ready import Release
        from studio import frameio_review
        self.tmp = tempfile.TemporaryDirectory()
        t = Path(self.tmp.name)
        self.store = Store(t / 'Data')
        self.p = self.store.create('4 Signs')
        self.export = t / 'FINAL.mp4'
        self.export.write_bytes(b'mp4')
        self.duration = 120.0
        self._probe = frameio_review.FrameioReview._probe
        frameio_review.FrameioReview._probe = staticmethod(lambda f: dict(duration=self.duration, fps='30'))
        w = words([('So', 100.0), ('the', 100.3), ('big', 100.6), ('sign.', 101.0), ('Next', 130.0), ('Second', 300.5), ('Third', 340.0)])
        arts = {'fin': dict(id='fin', parent='vis', payload=dict(state='verified', output=str(self.export))),
                'vis': dict(id='vis', parent='cut', payload={}),
                'cut': dict(id='cut', parent='prop', payload=dict(state='verified', plan=dict(map=MAP, map_digest='m1'))),
                'prop': dict(id='prop', payload=dict(words=w, revision=8, asr=dict(model='medium.en')))}
        flow = SimpleNamespace(_all=lambda pid, kind: [arts['fin']] if kind == 'edit_final' else [], get=lambda pid, aid: arts[aid])
        self.rel = Release(self.store, SimpleNamespace(path=lambda pid: t))
        self.sha = 'sha-1'
        self.arts = arts
        self.c = C.Chapters(self.store, flow, self.rel, agents=SimpleNamespace(team=SimpleNamespace(all=lambda: dict(agents=[dict(id='sam')])),
                                                                              desk_system_prompt=lambda a, l: 'sam'),
                            audio_check=False, hasher=lambda path: self.sha)
        self.sam = lambda *a, **k: ('', dict(chapters=[dict(word_id='w00005', title='Sign 1', evidence='Next'),
                                                       dict(word_id='w00006', title='Sign 2', evidence='Second')], brief='Brief.'))

    def tearDown(self):
        from studio import frameio_review
        frameio_review.FrameioReview._probe = self._probe
        self.tmp.cleanup()

    def test_no_prep_before_the_export_is_approved_except_a_labelled_preview(self):
        with self.assertRaises(ValueError):
            self.c.prepare(self.p['id'], run=self.sam)
        prep = self.c.prepare(self.p['id'], run=self.sam, preview=True)
        self.assertTrue(prep['preview'])
        self.assertEqual(C.chapter_lines(prep['chapters']), ['00:00 Introduction', '00:30 Sign 1', '01:00 Sign 2'])
        self.assertEqual(self.c.state(self.p['id'])['state'], 'preview')
        # Josh approves this exact export: the preview no longer counts, and a real prep is allowed.
        st = self.export.stat()
        p = self.store.get(self.p['id'])
        rel = dict(p['release'], export_approval=dict(path=str(self.export), size=st.st_size, mtime_ns=str(st.st_mtime_ns), sha256='sha-1', by='Josh'))
        self.store.save(p['id'], dict(release=rel), p['revision'])
        self.assertEqual(self.c.state(self.p['id'])['state'], 'stale')
        prep = self.c.prepare(self.p['id'], run=self.sam)
        self.assertFalse(prep['preview'])
        self.assertEqual(self.c.state(self.p['id'])['state'], 'review')

    def test_a_length_mismatch_rejects_the_transcript(self):
        self.duration = 150.0
        with self.assertRaises(ValueError):
            self.c.prepare(self.p['id'], run=self.sam, preview=True)

    def test_new_links_or_a_new_export_make_it_stale(self):
        self.c.prepare(self.p['id'], run=self.sam, preview=True)
        p = self.store.get(self.p['id'])
        rel = dict(p['release'], links=[dict(role='lead_magnet', url='https://lnk/lm')])
        self.store.save(p['id'], dict(release=rel), p['revision'])
        self.assertEqual(self.c.state(self.p['id'])['state'], 'stale')
        self.c.prepare(self.p['id'], run=self.sam, preview=True)
        self.assertEqual(self.c.state(self.p['id'])['state'], 'preview')
        import os, time
        time.sleep(0.02)
        self.export.write_bytes(b'mp4 v2')
        self.assertEqual(self.c.state(self.p['id'])['state'], 'stale')

    def test_the_real_hash_is_bound_even_when_size_and_time_match(self):
        self.c.prepare(self.p['id'], run=self.sam, preview=True)
        (Path(self.store.root) / 'chapters-hash-cache.json').unlink()
        self.sha = 'sha-2'
        self.assertEqual(self.c.state(self.p['id'])['state'], 'stale')

    def test_an_unverified_cut_or_failed_audio_check_is_refused(self):
        self.arts['cut']['payload']['state'] = 'requested'
        with self.assertRaises(ValueError):
            self.c.basis(self.p['id'])
        self.arts['cut']['payload']['state'] = 'verified'

    def test_chapter_starts_come_from_the_exports_own_sound(self):
        self.arts['cut']['payload']['plan']['mic_path'] = str(self.export)    # any existing file; locate is faked below
        self.c.audio_check = True
        self.c.check_audio = lambda *a, **k: dict(checked=6, matched=1, points=[])
        found = {'w00005': dict(start=28.2, corr=0.98, offset=-1.8)}           # Sign 1's sound is really 1.8 s earlier
        self.c.locate = lambda export, mic, cmap, w: found.get(w['id'])        # Sign 2's sound can't be found
        sam = lambda *a, **k: ('', dict(chapters=[dict(word_id='w00005', anchor='Next Second', title='Sign 1'),
                                                  dict(word_id='w00006', anchor='Second Third', title='Sign 2')], brief='B.'))
        prep = self.c.prepare(self.p['id'], run=sam, preview=True)
        ch = {c['title']: c for c in prep['chapters']}
        self.assertEqual((ch['Sign 1']['start'], ch['Sign 1']['shown'], ch['Sign 1']['audio']['corr']), (28.2, 28, 0.98))
        self.assertTrue(ch['Sign 2'].get('review'))
        self.assertIsNone(ch['Sign 2'].get('audio'))
        self.assertTrue(any('unproven' in f for f in prep['flags']))

    def test_same_inputs_same_digest(self):
        a = self.c.basis(self.p['id'])[0]['digest']
        self.assertEqual(self.c.basis(self.p['id'])[0]['digest'], a)


if __name__ == '__main__':
    unittest.main()
