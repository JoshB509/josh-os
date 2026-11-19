"""The 12 Nov 2026 fixes for 4 Signs: a fresh assembly per render read back against the cut, layers checked after placing,
the export's sound checked against the mic, and words stretched over separate sounds flagged."""
import sys
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(r'E:\Videos\DaJoshiResolve')
sys.path.insert(0, str(ROOT))
from studio import edit_flow as E  # noqa: E402
from dajoshi import edit_plan as ep  # noqa: E402

T = ep.TICKS_PER_SECOND
F = 8467191533   # Josh's sequence frame (slightly off 30 fps)


def plan(pieces):
    """pieces: [(out_start_frames, out_end_frames, mic_in_seconds)]"""
    return dict(mic_path='mic.wav', map=dict(frame_ticks=F, ticks_per_second=T, duration_ticks=pieces[-1][1] * F, pieces=[
        dict(kind='source', out_start=a * F, out_end=b * F, mic_in=int(m * T), camera_in=int(m * T) - 7 * T, camera_out=0) for a, b, m in pieces]))


def info(pieces, layers=(), shift_from=None, shift=0):
    items = []
    for a, b, m in pieces:
        d = shift if shift_from is not None and a >= shift_from else 0
        items.append(dict(kind='v', track=0, start=str((a - d) * F), end=str((b - d) * F), inp=str(int(m * T) - 7 * T)))
        items.append(dict(kind='a', track=0, start=str((a - d) * F), end=str((b - d) * F), inp=str(int(m * T)), path='mic.wav'))
    for track, s, e in layers:
        items.append(dict(kind='v', track=track, start=str(s * F), end=str(e * F), inp='0'))
    return dict(frame_ticks=str(F), items=items)


PIECES = [(0, 300, 0.0), (300, 900, 12.0), (900, 1500, 40.0)]


class Assembly(unittest.TestCase):
    def test_a_fresh_copy_of_the_cut_passes(self):
        self.assertEqual(E.assembly_problems(info(PIECES), plan(PIECES)), [])

    def test_a_ripple_made_after_the_cut_is_caught(self):
        # 55 frames rippled out at the start of piece 2, like FINAL_ASSEMBLY_r08 before the v02 render.
        bad = E.assembly_problems(info(PIECES, shift_from=300, shift=55), plan(PIECES))
        self.assertTrue(any('changed after the cut' in p for p in bad))

    def test_old_layers_left_on_the_tracks_are_caught(self):
        self.assertTrue(any('V5 has 1 clips, it should have 0' in p for p in E.assembly_problems(info(PIECES, [(4, 10, 40)]), plan(PIECES))))

    def test_wrong_source_words_or_wrong_media_are_caught(self):
        i = info(PIECES)
        mic = [x for x in i['items'] if x['kind'] == 'a']
        mic[1]['inp'] = str(int(mic[1]['inp']) + 2 * F)                          # right slot, 2 frames later in the mic
        self.assertTrue(any('away from its place in the mic file' in p for p in E.assembly_problems(i, plan(PIECES))))
        i = info(PIECES)
        [x for x in i['items'] if x['kind'] == 'v'][2]['inp'] = '0'                 # picture from the wrong part of the recording
        self.assertTrue(any('away from its place in the recording' in p for p in E.assembly_problems(i, plan(PIECES))))
        i = info(PIECES)
        [x for x in i['items'] if x['kind'] == 'a'][0]['path'] = 'camera.mp4'       # camera sound, not the good mic
        self.assertTrue(any('not the good mic' in p for p in E.assembly_problems(i, plan(PIECES))))
        i = info(PIECES)
        [x for x in i['items'] if x['kind'] == 'a'][0]['inp'] = str(int(PIECES[0][2] * T) + F // 3)   # Premiere's sub-frame rounding
        self.assertEqual(E.assembly_problems(i, plan(PIECES)), [])

    def test_a_camera_copy_across_a_cut_is_two_clips_split_at_that_cut(self):
        res = dict(layers=[dict(track=3, start_f=280, in_f=0, out_f=60), dict(track=4, start_f=280, in_f=0, out_f=60)])
        split = info(PIECES, [(3, 280, 300), (3, 300, 340), (4, 280, 340)])          # piece edge at frame 300
        self.assertEqual(E.assembly_problems(split, plan(PIECES), res), [])
        whole = info(PIECES, [(3, 280, 340), (4, 280, 340)])
        self.assertTrue(E.assembly_problems(whole, plan(PIECES), res))               # not split where Premiere splits it
        wrong = info(PIECES, [(3, 280, 310), (3, 310, 340), (4, 280, 340)])          # split somewhere else
        self.assertTrue(E.assembly_problems(wrong, plan(PIECES), res))

    def test_placed_layers_must_match_exactly(self):
        res = dict(layers=[dict(track=4, start_f=100, in_f=0, out_f=60), dict(track=3, start_f=100, in_f=0, out_f=60)])
        good = info(PIECES, [(4, 100, 160), (3, 100, 160)])
        self.assertEqual(E.assembly_problems(good, plan(PIECES), res), [])
        twice = info(PIECES, [(4, 100, 160), (4, 45, 105), (3, 100, 160)])          # an old copy still there
        probs = E.assembly_problems(twice, plan(PIECES), res)
        self.assertTrue(any('Old clips were left there' in p for p in probs) and any('overlap' in p for p in probs))
        shorter = info(PIECES, [(4, 100, 140), (3, 100, 160)])                     # a shorter replacement
        self.assertTrue(E.assembly_problems(shorter, plan(PIECES), res))
        moved = info(PIECES, [(4, 155, 215), (3, 100, 160)])                       # landed 55 frames late
        self.assertTrue(any('instead of' in p for p in E.assembly_problems(moved, plan(PIECES), res)))


class Sync(unittest.TestCase):
    """verify_sync on synthetic sound: a mic of random bursts, an export cut from it, one with a 1.833 s slip."""
    def setUp(self):
        rng = np.random.default_rng(3)
        sr = 16000
        env = np.repeat(rng.random(80 * 50) > 0.55, sr // 50)               # bursts every 20 ms, 80 s long
        self.mic = (env * rng.normal(0, 3000, len(env))).astype(np.float32)
        self.sr = sr
        fps = T / F
        self.p = plan([(0, int(20 * fps), 5.0), (int(20 * fps), int(40 * fps), 30.0)])

    def export(self, slip=0.0):
        sr, m = self.sr, self.mic
        a = m[int(5.0 * sr):int(25.0 * sr)]
        b0 = 30.0 + slip
        b = m[int(b0 * sr):int((b0 + 20) * sr)]
        return np.concatenate([a, b])

    def pcm_for(self, export):
        def pcm(src, start=None, dur=None):
            x = self.mic if src == 'mic.wav' else export
            if start is None:
                return x
            return x[int(start * self.sr):int((start + dur) * self.sr)]
        return pcm

    def test_in_sync_passes_and_a_slip_fails(self):
        ok, pts, _ = E.verify_sync('export.mp4', self.p, 'ffmpeg', pcm=self.pcm_for(self.export()))
        self.assertEqual(ok, [])
        self.assertTrue(pts and all(abs(x['lag_s']) < 0.02 for x in pts))
        bad, pts, _ = E.verify_sync('export.mp4', self.p, 'ffmpeg', pcm=self.pcm_for(self.export(slip=1.833)))
        self.assertTrue(any('off the cut (piece 2)' in p for p in bad))
        self.assertTrue(all(abs(x['lag_s']) < 0.02 for x in pts if x['piece'] == 1))


    def test_short_pieces_use_a_shorter_window_and_edges_are_checked(self):
        fps = T / F
        p = plan([(0, int(20 * fps), 5.0), (int(20 * fps), int(22.5 * fps), 30.0), (int(22.5 * fps), int(40 * fps), 50.0)])
        m = self.mic
        ex = np.concatenate([m[int(5.0 * 16000):int(25.0 * 16000)], m[int(30.0 * 16000):int(32.5 * 16000)], m[int(50.0 * 16000):int(67.5 * 16000)]])
        probs, pts, unchecked = E.verify_sync('export.mp4', p, 'ffmpeg', pcm=self.pcm_for(ex))
        self.assertEqual(probs, [])
        self.assertTrue(any(x['piece'] == 2 and x['window_s'] < 4 for x in pts))   # the 2.5 s piece was checked
        self.assertEqual(unchecked, [])

    def test_no_points_or_too_much_silence_is_never_verified(self):
        silent = np.zeros(40 * 16000, dtype=np.float32)
        probs, pts, unchecked = E.verify_sync('export.mp4', self.p, 'ffmpeg', pcm=self.pcm_for(silent))
        self.assertEqual(pts, [])
        self.assertTrue(any('None of the sound could be checked' in x for x in probs))
        tiny = plan([(0, 10, 5.0), (10, 20, 30.0)])                               # pieces of a third of a second
        probs, pts, unchecked = E.verify_sync('export.mp4', tiny, 'ffmpeg', pcm=self.pcm_for(self.export()))
        self.assertTrue(probs and len(unchecked) == 2)
        empty = np.zeros(0, dtype=np.float32)
        probs, _, _ = E.verify_sync('export.mp4', self.p, 'ffmpeg', pcm=self.pcm_for(empty))
        self.assertTrue(any('no sound' in x for x in probs))


class HiddenSounds(unittest.TestCase):
    def test_a_word_stretched_over_separate_sounds_is_flagged_and_kept(self):
        sr = 16000
        pcm = np.zeros(sr * 4, dtype=np.float32)
        rng = np.random.default_rng(1)
        for a, b in ((0.5, 0.8), (1.2, 1.5), (2.0, 2.3)):                 # three bursts with quiet between
            pcm[int(a * sr):int(b * sr)] = rng.normal(0, 0.3, int(b * sr) - int(a * sr))
        words = [dict(id='w1', start=int(0.5 * sr), end=int(2.3 * sr), text=' to', probability=0.9)]
        events, _ = ep.discover(words, pcm, sr)
        hid = [e for e in events if e['kind'] == 'hidden_sound']
        self.assertEqual(len(hid), 1)
        self.assertEqual((hid[0]['proposed_action'], hid[0]['review'], hid[0]['measurements']['bursts']), ('keep', True, 3))

    def test_transcription_settings_keep_every_word(self):
        from dajoshi.media import ASR
        self.assertEqual((ASR['vad'], ASR['condition_on_previous_text'], ASR['suppress_blank']), (False, False, False))
        src = (ROOT / 'dajoshi' / 'media.py').read_text(encoding='utf-8')
        self.assertIn('vad_filter=False', src)
        self.assertIn('condition_on_previous_text=False', src)


class Chunks(unittest.TestCase):
    def test_chunks_cover_the_whole_file_and_cut_in_the_quiet(self):
        from dajoshi.media import chunk_points
        sr = 16000
        rng = np.random.default_rng(5)
        audio = rng.normal(0, 0.2, sr * 100).astype(np.float32)          # 100 s of "speech"
        quiet = [15.0, 31.0, 52.0, 70.0, 88.0]
        for q in quiet:
            audio[int(q * sr):int((q + 0.3) * sr)] = 0                   # a short pause at each
        cuts = chunk_points(audio, sr)
        self.assertEqual((cuts[0][0], cuts[-1][1]), (0, len(audio)))
        self.assertTrue(all(a[1] == b[0] for a, b in zip(cuts, cuts[1:])))   # no gap, no overlap
        self.assertTrue(all(12 * sr <= b - a <= 24 * sr for a, b in cuts[:-1]))
        for a, _ in cuts[1:]:
            self.assertTrue(any(abs(a / sr - (q + 0.15)) < 0.2 for q in quiet), a / sr)


class LayerReplacement(unittest.TestCase):
    """What tq_place_layers really does (DaJoshiCut panel/index.js placeLayers): V5 renders use createOverwriteItemAction,
    which replaces only the frames each new clip covers, and V4 camera copies are cloned on top. Nothing is removed. This
    fake does the same, so the tests show what clearing first changes."""
    def fake_premiere(self, existing):
        tracks = {3: list(existing.get(3, [])), 4: list(existing.get(4, []))}
        calls = []

        def overwrite(track, s, e):
            kept = []
            for a, b in tracks[track]:
                if b <= s or a >= e:
                    kept.append((a, b))
                else:                                   # the old clip survives outside the new one's range
                    if a < s:
                        kept.append((a, s))
                    if b > e:
                        kept.append((e, b))
            tracks[track] = sorted(kept + [(s, e)])

        def bridge(cmd, args, timeout=None):
            calls.append(cmd if cmd != 'tq_ops' else 'tq_ops:' + ','.join(o['op'] for o in args['ops']))
            if cmd == 'tq_ops':
                out = []
                for o in args['ops']:
                    if o['op'] == 'remove_track':
                        tracks[o['track']] = []
                        out.append(dict(n=0, op='remove_track', ok=True, result=None))
                    elif o['op'] == 'info':
                        items = [dict(kind='v', track=t, start=str(a * F), end=str(b * F), inp='0') for t in (3, 4) for a, b in tracks[t]]
                        out.append(dict(n=0, op='info', ok=True, result=dict(frame_ticks=str(F), items=items)))
                return dict(ok=True, steps=out)
            if cmd == 'tq_place_layers':
                for l in args['result']['layers']:
                    s, e = l['start_f'], l['start_f'] + l['out_f'] - l['in_f']
                    if l['track'] == 4:
                        overwrite(4, s, e)
                    else:
                        tracks[3] = sorted(tracks[3] + [(s, e)])   # a camera copy is added, never replacing
                return dict(ok=True)
            raise AssertionError(cmd)
        return bridge, tracks, calls

    NEW = dict(layers=[dict(track=4, start_f=100, in_f=0, out_f=40), dict(track=3, start_f=100, in_f=0, out_f=40)])

    def flow(self, bridge):
        f = E.EditFlow.__new__(E.EditFlow)
        f.bridge_call = bridge
        return f

    def layer_checks(self, info):
        """assembly_problems on the layer tracks only."""
        return [p for p in E.assembly_problems(dict(info, items=info['items'] + info_items_v1a1()), plan(PIECES), self.NEW)]

    def test_without_clearing_an_old_longer_or_offset_layer_survives(self):
        bridge, tracks, _ = self.fake_premiere({4: [(80, 160)], 3: [(80, 160)]})   # an older, longer placement
        bridge('tq_place_layers', dict(result=self.NEW))
        self.assertEqual(tracks[4], [(80, 100), (100, 140), (140, 160)])         # old range left on both sides
        self.assertEqual(tracks[3], [(80, 160), (100, 140)])                     # old camera copy still under the new one

    def test_clearing_first_replaces_the_whole_range_and_the_check_proves_it(self):
        bridge, tracks, calls = self.fake_premiere({4: [(80, 160), (300, 320)], 3: [(80, 160)]})
        f = self.flow(bridge)
        f.clear_layer_tracks('FINAL_ASSEMBLY_r03_v01')
        bridge('tq_place_layers', dict(result=self.NEW))
        self.assertEqual((tracks[4], tracks[3]), ([(100, 140)], [(100, 140)]))
        self.assertEqual(calls[0], 'tq_ops:remove_track,remove_track')
        info = f.read_sequence('FINAL_ASSEMBLY_r03_v01')
        self.assertEqual(self.layer_checks(info), [])

    def test_the_check_catches_leftovers_if_clearing_were_skipped(self):
        bridge, tracks, _ = self.fake_premiere({4: [(80, 160)], 3: [(80, 160)]})
        bridge('tq_place_layers', dict(result=self.NEW))
        probs = self.layer_checks(self.flow(bridge).read_sequence('FINAL_ASSEMBLY_r03_v01'))
        self.assertTrue(any('Old clips were left there' in p for p in probs) and any('overlap' in p for p in probs))

    def test_only_tariqs_fresh_assembly_can_be_cleared(self):
        bridge, tracks, calls = self.fake_premiere({4: [(1, 2)]})
        for name in ('FINAL_ASSEMBLY_r08', 'CLEAN_CUT_r03', 'Josh main edit', 'FINAL_ASSEMBLY_r03_v01 copy'):
            with self.assertRaises(ValueError):
                self.flow(bridge).clear_layer_tracks(name)
        self.assertEqual((calls, tracks[4]), ([], [(1, 2)]))

    def test_final_clears_before_placing(self):
        src = (ROOT / 'studio' / 'edit_flow.py').read_text(encoding='utf-8')
        i, j = src.index('self.clear_layer_tracks(name)'), src.index("self.bridge_call('tq_place_layers'")
        self.assertLess(i, j)


def info_items_v1a1():
    return [i for i in info(PIECES)['items'] if int(i['track']) == 0]


class ProtectedKeep(unittest.TestCase):
    """The acted VP scene in 4 Signs (recording 586-632 s, from proposal fd960a54): the executive re-asks "the end of the
    quarter?", so an automatic retake rule wanted to cut 591.8-608.6 s, while Tariq had marked 593.0-612.6 s "check,
    keep as performance". The automatic cut used to win silently."""
    def setUp(self):
        import json
        self.fx = json.loads((ROOT / 'tests' / 'fixtures' / 'acted_vp_scene_4signs.json').read_text(encoding='utf-8'))
        self.sr = self.fx['sr']
        self.auto = next(e for e in self.fx['events'] if e['kind'] == 'take_group' and e.get('source') is None
                         and abs(e['start'] / self.sr - 591.75) < 0.2)
        self.scene = [w['id'] for w in self.fx['words'] if 593.2 * self.sr <= w['start'] < 608.0 * self.sr]

    def cut_ids(self, decisions=None, word_actions=None):
        total = max(w['end'] for w in self.fx['words']) + self.sr
        rem, _ = ep.plan(self.fx['events'], self.fx['words'], decisions or {}, total, self.sr, word_actions=word_actions or {})
        return {w['id'] for w in self.fx['words'] if sum(max(0, min(w['end'], b) - max(w['start'], a)) for a, b in rem) > 0.5 * (w['end'] - w['start'])}

    def test_tariqs_protected_keep_outranks_the_automatic_retake(self):
        cut = self.cut_ids()
        self.assertFalse(set(self.scene) & cut)
        # The real flubs around the scene are still cut: the restarted "The executive asks..." at 586 s.
        first = [w['id'] for w in self.fx['words'] if 586.3 * self.sr <= w['start'] < 587.9 * self.sr]
        self.assertTrue(first and set(first) <= cut)

    def test_josh_can_still_cut_it(self):
        self.assertTrue(set(self.scene) <= self.cut_ids(decisions={self.auto['event_id']: dict(action='remove', by='josh')}))
        one = self.scene[3]
        self.assertIn(one, self.cut_ids(word_actions={one: 'remove'}))

    def test_apply_editorial_marks_the_automatic_cut_kept_and_for_review(self):
        auto = dict(self.auto, proposed_action='remove', review=False)
        auto.pop('protected_by_check', None)
        words = self.fx['words']
        check = next(e for e in self.fx['events'] if e.get('check'))
        out = ep.apply_editorial([auto], words, checks=[dict(start_word=check['word_ids'][0], end_word=check['word_ids'][-1], reason='acted')])
        got = next(e for e in out if e['event_id'] == auto['event_id'])
        self.assertEqual((got['proposed_action'], got['review'], got.get('protected_by_check')), ('keep', True, True))


class ReworkedOpening(unittest.TestCase):
    """A shot-list row whose opening Josh said differently ("So the executive asks a question like" against the script's
    "The executive asks") still gets its presses, and the animation before it doesn't run on over it (4 Signs r04)."""
    def test_presses_found_and_previous_animation_ends(self):
        from studio import edit_animations as ea
        sr = 16000
        text = ('which leads us to mistake number three ' + 'filler ' * 100 +
                'so the executive asks a question like is the launch still on for the end of the quarter '
                'the main thing we found is that the supplier system is late and then is it moving or not '
                'yes by about six weeks what the executive heard was nothing').split()
        words = [dict(id=f'w{i}', start=i * sr // 4, end=i * sr // 4 + sr // 5, text=' ' + t) for i, t in enumerate(text)]
        total = len(words) * sr // 4 * (T // sr)
        pieces = [dict(kind='source', mic_in=0, mic_out=total, out_start=0, out_end=total)]
        parts = [dict(kind='animation', n=16, script='Which leads us to mistake number three', presses=[dict(press=1, words='Which leads us to mistake number three')]),
                 dict(kind='animation', n=17, script='The executive asks, "Is the launch still on for the end of the quarter?"',
                      presses=[dict(press=1, words='Is the launch still on'), dict(press=2, words='The main thing we found'),
                               dict(press=3, words='Is it moving or not'), dict(press=4, words='Yes, by about six weeks')]),
                 dict(kind='talk', script='What the executive heard was nothing')]
        scenes = {16: dict(start=1, end=1, type='full'), 17: dict(start=2, end=5, type='full')}
        shots, problems = ea.plan_shots(words, [], pieces, parts, scenes, F, sr)
        self.assertEqual(problems, [])
        a17 = [x for x in shots if x['animation'] == 17]
        self.assertEqual([x['press'] for x in a17], [1, 2, 3, 4])
        a16 = [x for x in shots if x['animation'] == 16]
        self.assertTrue(a16 and a16[-1]['end_f'] <= a17[0]['start_f'])


class NoSlivers(unittest.TestCase):
    def test_shot_edges_near_a_cut_move_onto_it_and_neighbours_still_touch(self):
        from studio import edit_animations as ea
        pieces = [dict(kind='source', out_start=0, out_end=6370 * F, mic_in=0, mic_out=0),
                  dict(kind='source', out_start=6370 * F, out_end=9000 * F, mic_in=0, mic_out=0)]
        shots = [dict(start_f=6197, end_f=6372, alpha=True), dict(start_f=6372, end_f=6521, alpha=True), dict(start_f=7000, end_f=7100, alpha=False)]
        ea.snap_to_cuts(shots, pieces, F)
        self.assertEqual([(x['start_f'], x['end_f']) for x in shots], [(6197, 6370), (6370, 6521), (7000, 7100)])
        # the real case: every camera shot boundary is either on a cut or at least SNAP_F + 1 frames from one
        edges = [0, 6370, 9000]
        for x in shots:
            for e in edges:
                for b in (x['start_f'], x['end_f']):
                    self.assertTrue(b == e or abs(b - e) > ea.SNAP_F)


class DeckGate(unittest.TestCase):
    """Nothing the deck asks for can quietly go missing before render (4 Signs, 12 Nov 2026: r04_v02 exported "verified"
    with card 3 unread and a speech box on the wrong "The"; r04_v05 with a chess press silently dropped)."""
    sr = 16000

    def setup(self, md):
        from studio import edit_animations as ea
        from studio.edit_review import parse_shot_list
        text = ('start with the conclusion you want to announce then ask yourself what is the one fact that got you there '
                'then ask a question about that fact and nothing else once everyone is in the central team who does sales go to '
                'the central team will do it okay okay how do we handle the requests the end').split()
        words = [dict(id=f'w{i}', start=i * self.sr // 3, end=i * self.sr // 3 + self.sr // 4, text=' ' + t) for i, t in enumerate(text)]
        total = len(words) * self.sr // 3 * (T // self.sr)
        pieces = [dict(kind='source', mic_in=0, mic_out=total, out_start=0, out_end=total)]
        parts = parse_shot_list(md)
        scenes = {13: dict(start=1, end=3, type='full'), 9: dict(start=4, end=6, type='full')}
        shots, problems = ea.plan_shots(words, [], pieces, parts, scenes, F, self.sr)
        return ea, md, parts, scenes, shots, problems, words, pieces

    ROW13 = ('| Start with the conclusion, then ask yourself the one fact, then ask about it. | **Insert Animation 13** (full screen)<br>'
             'Press 1 on "start with the conclusion you want"<br>Press 2 on "then ask yourself what is the one fact"<br>{p3} |')
    ROW9 = ('| Once everyone is in the central team | **Insert Animation 9** (full screen)<br>Press 1 on "once everyone is in the central team"<br>'
            'Press 2 on "{p2}"<br>Press 3 on "okay okay how do we handle" |')

    def gate(self, p3='Press 3 on "then ask a question about that fact"', p2='the central team will do it', omissions=None):
        nl = chr(10)
        md = '| Script | Shot |' + nl + '|---|---|' + nl + self.ROW13.format(p3=p3) + nl + self.ROW9.format(p2=p2) + nl
        ea, md, parts, scenes, shots, problems, words, pieces = self.setup(md)
        return ea.deck_gate(md, parts, scenes, shots, words, [], pieces, F, self.sr, omissions)

    def test_a_complete_deck_passes(self):
        self.assertEqual(self.gate(), [])

    def test_card_3_written_so_the_parser_skips_it_is_caught(self):
        g = self.gate(p3='Press 3 (card 3) on "then ask a question about that fact"')
        self.assertTrue(any("can't be read" in x for x in g))
        self.assertTrue(any('no press for step(s) [3]' in x for x in g))

    def test_a_one_word_anchor_is_flagged_not_first_matched(self):
        g = self.gate(p2='The')
        self.assertTrue(any('too few words' in x for x in g))

    def test_an_anchor_said_twice_nearby_is_ambiguous(self):
        g = self.gate(p2='the central team')
        self.assertTrue(any('said 2 times near there' in x for x in g))

    def test_a_recorded_omission_with_a_reason_is_allowed_and_one_without_is_not(self):
        missing = 'Press 3 on "words nobody ever says here"'
        self.assertTrue(any('was not placed' in x for x in self.gate(p3=missing)))
        self.assertEqual(self.gate(p3=missing, omissions={'13:3': dict(reason='Josh dropped card 3 for this cut', by='josh')}), [])
        self.assertTrue(self.gate(p3=missing, omissions={'13:3': dict(reason='  ', by='josh')}))

    def test_final_blocks_on_the_gate_and_on_unexplained_left_out_presses(self):
        src = (ROOT / 'studio' / 'edit_flow.py').read_text(encoding='utf-8')
        i = src.index("if layers.get('gate'):")
        j = src.index("raise ValueError('Some presses could not be placed, so nothing was exported: '")
        k = src.index("self.bridge_call('tq_place_layers'")
        self.assertLess(i, k)
        self.assertLess(j, k)


class FreshSequence(unittest.TestCase):
    def test_each_render_gets_its_own_sequence_and_a_reused_one_stops(self):
        src = (ROOT / 'studio' / 'edit_flow.py').read_text(encoding='utf-8')
        self.assertIn("name = f'{stem}_v{n:02d}'", src)
        self.assertIn("if made.get('existed'):", src)


if __name__ == '__main__':
    unittest.main()
