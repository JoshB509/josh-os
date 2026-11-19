"""Tariq's shot shortlist on Discord. Everything is mocked: no Discord connection, no real channel, no Claude call.

The shortlist is ONE Discord message. Each segment is a whole talking head passage or a whole stretch on one animation,
shown fully built, and Previous and Next edit that same message.
"""
import asyncio
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord

from studio import discord_review, shot_review
from studio.agent import Agents, asked_for_shortlist
from studio.discord_bridge import Bridge
from studio.store import Store
from studio.team import Team

SLIDES = '<div class="scene full" data-from="1" data-to="3"></div><div class="scene pip" data-from="4" data-to="4"></div>'
# Talking head, then animation 1 building in three presses, then talking head, then animation 2 in picture in picture.
SHOTS = '''| Script | Shot |
|---|---|
| **1 · Opening** | |
| 0:00 · Hi, I'm Josh. Today is about meetings. | Talking head |
| 0:05 · Most people lose the room in the first ten seconds. Here is why it happens, and what to do. | Insert Animation 1 (Full screen) A test chart.<br>Press 1 on "first ten seconds"<br>Press 2 on "why it happens"<br>Press 3 on "what to do" |
| 0:15 · So that is the first part. | Talking head |
| 0:20 · Lead with the answer, then the detail. | Insert Animation 2 (Picture in picture) A test card. Press 1 on "Lead with the answer"<br>Press 2 on "words that are not there" |
'''


class FakeEditReview:
    """Stands in for EditReview over a temporary video folder, with one rendered picture per press."""

    def __init__(self, root, stale=False, pictures=True):
        self.root, self.stale = root, stale
        root.mkdir(parents=True, exist_ok=True)
        (root / 'v_slides_v01.html').write_text(SLIDES, encoding='utf-8')
        (root / 'v_shot-list_v01.md').write_text(SHOTS, encoding='utf-8')
        self.out = root / 'review'
        self.out.mkdir()
        self.images = {}
        if pictures:
            for n, count in ((1, 3), (2, 1)):
                self.images[str(n)] = [f'anim{n:02d}_b{k}.jpg' for k in range(1, count + 1)]
                for f in self.images[str(n)]:
                    from PIL import Image
                    Image.new('RGB', (160, 90), 'white').save(self.out / f, 'JPEG')
        self.store = SimpleNamespace(get=lambda pid: dict(id=pid, name='Test video'))

    def deck_files(self, pid):
        return self.root / 'v_slides_v01.html', self.root / 'v_shot-list_v01.md'

    def out_folder(self, pid):
        return self.out

    def review(self, pid):
        from studio.edit_review import parse_shot_list
        parts = parse_shot_list((self.root / 'v_shot-list_v01.md').read_text(encoding='utf-8'))
        for p in parts:
            if p['kind'] == 'animation':
                p['images'] = [] if self.stale else self.images.get(str(p['n']), [])
        return dict(parts=parts, rendered='2026-11-11T00:00:00Z' if self.images else None, stale=self.stale)


def fresh(name):
    tmp = tempfile.TemporaryDirectory()
    return tmp, FakeEditReview(Path(tmp.name) / name)


class SegmentTests(unittest.TestCase):
    def setUp(self):
        self.tmp, self.er = fresh('video')
        self.addCleanup(self.tmp.cleanup)
        self.s = shot_review.build(self.er, 'p1')

    def test_locate_and_mark_only_real_words(self):
        script = 'Most people lose the room, in the first ten seconds.'
        self.assertEqual(script[slice(*shot_review.locate(script, 'the room in the first'))], 'the room, in the first')
        self.assertIsNone(shot_review.locate(script, 'words that are not there'))
        self.assertEqual(shot_review.marked(script, [(0, 4), (2, 11)]), '**Most people** lose the room, in the first ten seconds.')

    def test_segments_follow_the_script_in_order_including_talking_head(self):
        self.assertEqual([(i['kind'], i['n']) for i in self.s['items']], [('talk', None), ('animation', 1), ('talk', None), ('animation', 2)])
        self.assertEqual([i['time'] for i in self.s['items']], ['0:00', '0:05', '0:15', '0:20'])
        self.assertEqual(self.s['items'][0]['script'], "Hi, I'm Josh. Today is about meetings.")
        self.assertEqual(self.s['items'][0]['section'], 'Opening')

    def test_three_build_steps_make_one_final_segment(self):
        anim = [i for i in self.s['items'] if i['kind'] == 'animation' and i['n'] == 1]
        self.assertEqual(len(anim), 1)
        self.assertEqual((anim[0]['builds'], anim[0]['picture_name']), (3, 'anim01_b3.jpg'))
        self.assertEqual([p['press'] for p in anim[0]['presses']], [1, 2, 3])
        self.assertTrue(all(p['found'] for p in anim[0]['presses']))

    def test_whole_script_is_covered_word_for_word(self):
        cov = self.s['source']['coverage']
        self.assertTrue(cov['complete'])
        self.assertEqual(cov['script_words'], cov['matched'])
        joined = ' '.join(i['script'] for i in self.s['items'])
        self.assertEqual(joined, ' '.join(shot_review.script_rows(SHOTS)))
        for i in self.s['items']:
            self.assertEqual(i['marked'].replace('**', ''), i['script'])

    def test_unfound_cues_and_missing_pictures_are_said_not_faked(self):
        pip = self.s['items'][3]
        self.assertEqual([(p['press'], p['found']) for p in pip['presses']], [(1, True), (2, False)])
        talk = self.s['items'][0]
        self.assertEqual((talk['picture'], talk['standin']), ('', False))
        self.assertIn('No frame from this video', talk['missing'])
        tmp, stale = fresh('stale')
        stale.stale = True
        self.addCleanup(tmp.cleanup)
        self.assertIn('older version of the deck', shot_review.build(stale, 'p1')['items'][1]['missing'])

    def test_same_source_catches_any_change(self):
        src = self.s['source']
        self.assertTrue(shot_review.same_source(self.er, 'p1', src))
        (self.er.root / 'v_shot-list_v01.md').write_text(SHOTS.replace('Lead with', 'Open with'), encoding='utf-8')
        self.assertFalse(shot_review.same_source(self.er, 'p1', src))


class ViewTests(unittest.TestCase):
    def setUp(self):
        self.tmp, self.er = fresh('video')
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name) / 'Data')
        self.notes = []
        self.reviews = discord_review.Reviews(self.store, None, lambda pid, note: self.notes.append((pid, note)))
        self.r = self.reviews.create_shots(shot_review.build(self.er, 'p1'), cards=False)

    def test_talking_head_and_animation_views(self):
        v = self.reviews.view(self.r)
        self.assertIn('Shot 1 of 4. Talking head, at 0:00.', v['text'])
        self.assertIn('No frame from this video', v['text'])
        r = self.reviews.move(self.r['id'], 'next')
        v = self.reviews.view(r)
        self.assertIn('Shot 2 of 4. Animation 1, full screen, at 0:05.', v['text'])
        self.assertIn('**first ten seconds**', v['text'])
        self.assertTrue(v['file'].endswith('anim01_b3.jpg'))
        r = self.reviews.move(r['id'], 'next'); r = self.reviews.move(r['id'], 'next')
        self.assertIn('Not found in the script, so not marked: press 2 "words that are not there"', self.reviews.view(r)['text'])

    def test_feedback_reaches_tariqs_animation_notes_unless_test(self):
        r = self.reviews.move(self.r['id'], 'next')
        self.reviews.add_feedback(r['id'], 'Hold the chart longer.', '111', slide=1)
        self.reviews.add_feedback(r['id'], 'Good overall.', '111')
        self.assertIn('animation 1 (shot 2): Hold the chart longer.', self.notes[0][1])
        self.assertIn('the whole shortlist: Good overall.', self.notes[1][1])
        t = self.reviews.create_shots(shot_review.build(self.er, 'p1'), test=True)
        self.reviews.add_feedback(t['id'], 'Test only.', '111', slide=0)
        self.assertEqual(len(self.notes), 2)


class OneMessageTests(unittest.TestCase):
    """A requested shortlist is one channel.send. Previous and Next edit that message and never post another."""

    def setUp(self):
        self.tmp, self.er = fresh('video')
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name) / 'Data')
        self.bridge = Bridge(SimpleNamespace(store=self.store, carousels=SimpleNamespace()))
        self.bridge.reviews = discord_review.Reviews(self.store, None)
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self.loop.run_forever, daemon=True).start()
        self.addCleanup(lambda: self.loop.call_soon_threadsafe(self.loop.stop))
        self.files = []

    def connect(self, agents=('sam', 'tariq')):
        self.bridge.config = dict(owners=['111'], bots={a: dict(token='x') for a in agents})
        self.bridge.loop = self.loop
        self.bridge.clients = {a: SimpleNamespace(user=a) for a in agents}
        self.bridge.status['bots'] = {a: dict(connected=True) for a in agents}

    def channel(self, result=None, error=None):
        ch = MagicMock(); ch.id = 2
        ch.send = AsyncMock(return_value=result, side_effect=error)
        self.bridge.channels['tariq'] = ch
        return ch

    def press(self, rid, action, user=111):
        i = MagicMock()
        i.type = discord.InteractionType.component
        i.data = {'custom_id': discord_review.custom_id(rid, action)}
        i.user = SimpleNamespace(id=user)
        i.response.edit_message = AsyncMock(); i.response.send_message = AsyncMock(); i.response.send_modal = AsyncMock()
        asyncio.run(self.bridge.on_review_interaction(i))
        for a in (i.response.edit_message.call_args.kwargs.get('attachments') or []) if i.response.edit_message.await_count else []:
            a.close()
        return i

    def tearDown(self):
        for ch in getattr(self, 'chs', []):
            for c in ch.send.await_args_list:
                if c.kwargs.get('file'):
                    c.kwargs['file'].close()

    def test_one_send_then_next_and_previous_only_edit_that_message(self):
        self.connect()
        ch = self.channel(SimpleNamespace(id=9, jump_url='https://discord.com/channels/1/2/9')); self.chs = [ch]
        out = self.bridge.send_shortlist('p1', self.er)
        self.assertEqual(ch.send.await_count, 1)
        self.assertIn('one message you page through: 4 shots (2 talking head, 2 animations', out)
        self.assertIn('covering the whole script', out)
        sent = ch.send.await_args.kwargs
        self.assertIn('Shot 1 of 4. Talking head', sent['embed'].description)
        rid = discord_review.parse(sent['view'].children[0].custom_id)[0]
        nxt = self.press(rid, 'next')
        self.assertIn('Shot 2 of 4. Animation 1', nxt.response.edit_message.call_args.kwargs['embed'].description)
        self.assertEqual(nxt.response.edit_message.call_args.kwargs['attachments'][0].filename, 'shot-02.png')
        prev = self.press(rid, 'prev')
        self.assertIn('Shot 1 of 4. Talking head', prev.response.edit_message.call_args.kwargs['embed'].description)
        self.assertEqual(ch.send.await_count, 1)
        self.assertEqual(self.bridge.reviews.get(rid)['message_id'], '9')
        stranger = self.press(rid, 'next', user=222)
        stranger.response.edit_message.assert_not_awaited()
        self.assertIn('a few minutes ago', self.bridge.send_shortlist('p1', self.er))
        self.assertEqual(ch.send.await_count, 1)

    def test_never_uses_sam_when_tariq_is_not_set_up(self):
        self.connect(('sam',))
        self.bridge.channels['sam'] = MagicMock()
        self.assertIn('not connected to Discord yet', self.bridge.send_shortlist('p1', self.er))
        self.bridge.channels['sam'].send.assert_not_called()

    def test_unclear_send_is_not_retried(self):
        self.connect()
        ch = self.channel(error=TimeoutError('no answer')); self.chs = [ch]
        self.assertIn("won't send it again on my own", self.bridge.send_shortlist('p1', self.er))
        self.assertEqual(ch.send.await_count, 1)


class ChatGuardTests(unittest.TestCase):
    def test_only_josh_asking_counts(self):
        for m in ('Send me the shot shortlist for 4 signs', 'can you share the shots on discord'):
            self.assertTrue(asked_for_shortlist(m), m)
        for m in ('what do you think of the shot list?', 'send me the script', '', None):
            self.assertFalse(asked_for_shortlist(m), m)

    def test_tariq_sends_only_when_asked(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        st = Store(Path(tmp.name) / 'Data')
        agents = Agents(st, Path(tmp.name), Team(st))
        sent, seen = [], {}
        agents.send_shortlist = lambda pid: sent.append(pid) or 'Sent.'

        def run(system, prompt, schema, *a, **k):
            seen['schema'] = schema
            return 'ok', {'reply': 'ok', 'send_shortlist': 'p1'}
        agents.run = run
        agents.chat(dict(agent_id='tariq', message='how is the edit going?'), [])
        self.assertIn('send_shortlist', seen['schema']['properties'])
        self.assertEqual(sent, [])
        agents.chat(dict(agent_id='tariq', message='send me the shot shortlist'), [])
        self.assertEqual(sent, ['p1'])
        agents.chat(dict(agent_id='sam', message='send me the shot shortlist'), [])
        self.assertNotIn('send_shortlist', seen['schema']['properties'])
        self.assertEqual(sent, ['p1'])


class StoryboardTests(unittest.TestCase):
    """The storyboard card: the finished shot, up to three real build pictures and the segment's exact script."""

    def setUp(self):
        from PIL import Image
        self.tmp, self.er = fresh('video')
        self.addCleanup(self.tmp.cleanup)
        for f in self.er.out.glob('*.jpg'):
            Image.new('RGB', (160, 90), 'white').save(f, 'JPEG')
        self.s = shot_review.build(self.er, 'p1')

    def test_pieces_keep_the_script_exactly(self):
        from studio import storyboard
        for it in self.s['items']:
            parts = storyboard.pieces(it)
            rebuilt = ''.join((' ' if sp and i else '') + t for i, (t, k, b, sp) in enumerate(parts))
            self.assertEqual(rebuilt, ' '.join(it['script'].replace('**', '').split()))
            marked = {k for t, k, b, sp in parts if k}
            self.assertEqual(marked, {p['press'] for p in it['presses'] if p['found']})

    def test_three_builds_make_one_card_with_three_thumbnails(self):
        from PIL import Image
        from studio import storyboard
        it = self.s['items'][1]
        self.assertEqual(storyboard.pick_builds(it['build_pictures']), [0, 1, 2])
        out = storyboard.card(it, 1, 4, 'Test video', self.s['source'], Path(self.tmp.name) / 'card.png')
        w, h = Image.open(out).size
        self.assertEqual(w, 1080)
        self.assertEqual(storyboard.pick_builds(['a', 'b', 'c', 'd', 'e', 'f']), [0, 3, 5])
        # Pictures dominate: the finished shot and the build column fill the width, under a thin metadata strip.
        self.assertEqual(storyboard.PAD + storyboard.MAIN_W + storyboard.GAP + storyboard.THUMB_W, w - storyboard.PAD)
        self.assertLessEqual(storyboard.TOP, 48)
        self.assertGreater(storyboard.MAIN_W, 700)
        self.assertGreaterEqual(storyboard.THUMB_W * 9 // 16, storyboard.THUMB_H - 2)
        lines = storyboard.layout(storyboard.pieces(it), storyboard.font(storyboard.BODY), storyboard.font(storyboard.BODY, True), w - 2 * storyboard.PAD)
        self.assertEqual(h, storyboard.TOP + storyboard.MAIN_H + 22 + len(lines) * storyboard.LINE_H + storyboard.PAD)

    def test_long_script_wraps_inside_the_card_and_grows_it(self):
        from PIL import Image
        from studio import storyboard
        it = dict(self.s['items'][1], script=' '.join([self.s['items'][1]['script']] * 12))
        f, fb = storyboard.font(storyboard.BODY), storyboard.font(storyboard.BODY, True)
        lines = storyboard.layout(storyboard.pieces(it), f, fb, 1080 - 2 * storyboard.PAD)
        for ln in lines:
            t, k, b, x, w = ln[-1]
            self.assertLessEqual(x + w, 1080 - 2 * storyboard.PAD + 1)
        out = storyboard.card(it, 1, 4, 'A very long test video title ' * 6, self.s['source'], Path(self.tmp.name) / 'long.png')
        self.assertGreater(Image.open(out).size[1], 1200)

    def test_talking_head_card_shows_no_made_up_frame(self):
        from PIL import Image
        from studio import storyboard
        it = self.s['items'][0]
        out = storyboard.card(it, 0, 4, 'Test video', self.s['source'], Path(self.tmp.name) / 'talk.png')
        img = Image.open(out).convert('RGB')
        # The picture area is the plain grey "no frame" panel, not a photo.
        self.assertEqual(img.getpixel((storyboard.PAD + 10, storyboard.TOP + 10)), (245, 245, 244))

    def test_cards_are_the_message_image_and_next_still_edits_one_message(self):
        store = Store(Path(self.tmp.name) / 'Data')
        reviews = discord_review.Reviews(store, None)
        r = reviews.create_shots(self.s)
        self.assertTrue(r['cards'])
        v = reviews.view(r)
        self.assertTrue(v['file'].endswith('shot-01.png') and Path(v['file']).is_file())
        self.assertIn('Talking head', v['text'])
        r = reviews.move(r['id'], 'next')
        self.assertTrue(reviews.view(r)['file'].endswith('shot-02.png'))


if __name__ == '__main__':
    unittest.main()
