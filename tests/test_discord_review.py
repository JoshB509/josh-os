"""Sam's carousel reviews on Discord. Everything here is mocked: no Discord connection and no real channel."""
import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord

from studio import discord_review
from studio.carousels import Carousels
from studio.discord_bridge import Bridge
from studio.store import Store

PNG = bytes.fromhex('89504e470d0a1a0a0000000d4948445200000001000000010806000000'
                    '1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082')


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.store = Store(root / 'Data')
        self.carousels = Carousels(self.store)
        slides = root / 'slides'
        slides.mkdir()
        for i in range(1, 4):
            (slides / f'slide-{i}.png').write_bytes(PNG)
        item = self.carousels.create(dict(title='Test carousel', owner='sam', slides='a\n---\nb\n---\nc', notes='Old note.'))
        self.carousels.folder = lambda it: slides if it['id'] == item['id'] else None
        self.cid = item['id']
        self.reviews = discord_review.Reviews(self.store, self.carousels)
        server = SimpleNamespace(store=self.store, carousels=self.carousels)
        self.bridge = Bridge(server)
        self.bridge.config = dict(owners=['111'], bots={'sam': dict(token='x', channels=['sam'])})
        self.bridge.reviews = self.reviews

    def test_custom_ids_round_trip_and_reject_others(self):
        r = self.reviews.create(self.cid)
        self.assertEqual(discord_review.parse(discord_review.custom_id(r['id'], 'next')), (r['id'], 'next'))
        for bad in ('crv:nope:next', f"crv:{r['id']}:delete", 'other:thing', None):
            self.assertIsNone(discord_review.parse(bad))

    def test_position_moves_and_stops_at_the_ends(self):
        r = self.reviews.create(self.cid)
        v = self.reviews.view(r)
        self.assertIn('Slide 1 of 3', v['text'])
        self.assertEqual([b[2] for b in v['buttons'][:2]], [True, False])
        for _ in range(5):
            r = self.reviews.move(r['id'], 'next')
        self.assertEqual(r['position'], 2)
        self.assertEqual([b[2] for b in self.reviews.view(r)['buttons'][:2]], [False, True])
        r = self.reviews.move(r['id'], 'prev')
        self.assertEqual(r['position'], 1)

    def test_state_survives_a_new_instance(self):
        r = self.reviews.create(self.cid)
        self.reviews.move(r['id'], 'next')
        again = discord_review.Reviews(self.store, self.carousels)
        self.assertEqual(again.get(r['id'])['position'], 1)

    def test_feedback_goes_to_notes_unless_test(self):
        r = self.reviews.create(self.cid)
        self.reviews.add_feedback(r['id'], 'Make the hook shorter.', '111', slide=0)
        self.reviews.add_feedback(r['id'], 'Good overall.', '111')
        notes = self.carousels.get(self.cid)['notes']
        self.assertIn('Old note.', notes)
        self.assertIn('on slide 1: Make the hook shorter.', notes)
        self.assertIn('on the whole carousel: Good overall.', notes)
        t = self.reviews.create(self.cid, test=True)
        self.reviews.add_feedback(t['id'], 'Test feedback.', '111', slide=1)
        self.assertNotIn('Test feedback.', self.carousels.get(self.cid)['notes'])
        self.assertEqual(len(self.reviews.get(t['id'])['feedback']), 1)
        with self.assertRaises(ValueError):
            self.reviews.add_feedback(t['id'], '   ', '111')

    def test_slide_files_become_a_review_for_sam_only(self):
        files = [str(f) for f in self.carousels.images(self.carousels.get(self.cid))] + ['C:/other/file.pdf']
        rest, cids = self.bridge.review_split('sam', files)
        self.assertEqual((rest, cids), (['C:/other/file.pdf'], [self.cid]))
        self.assertEqual(self.bridge.review_split('tariq', files), (files, []))

    def interaction(self, user, rid, action):
        i = MagicMock()
        i.type = discord.InteractionType.component
        i.data = {'custom_id': discord_review.custom_id(rid, action)}
        i.user = SimpleNamespace(id=user)
        i.response.send_message = AsyncMock()
        i.response.edit_message = AsyncMock()
        i.response.send_modal = AsyncMock()
        return i

    def test_only_owners_can_press_the_buttons(self):
        r = self.reviews.create(self.cid)
        i = self.interaction(222, r['id'], 'next')
        asyncio.run(self.bridge.on_review_interaction(i))
        i.response.send_message.assert_awaited_once()
        self.assertTrue(i.response.send_message.call_args.kwargs['ephemeral'])
        i.response.edit_message.assert_not_awaited()
        self.assertEqual(self.reviews.get(r['id'])['position'], 0)

    def test_next_edits_the_message_with_the_next_slide(self):
        r = self.reviews.create(self.cid)
        i = self.interaction(111, r['id'], 'next')
        asyncio.run(self.bridge.on_review_interaction(i))
        kw = i.response.edit_message.call_args.kwargs
        self.assertIn('Slide 2 of 3', kw['embed'].description)
        self.assertEqual(kw['attachments'][0].filename, 'slide-02.png')
        kw['attachments'][0].close()
        self.assertEqual([c.custom_id for c in kw['view'].children][:2], [discord_review.custom_id(r['id'], 'prev'), discord_review.custom_id(r['id'], 'next')])

    def test_feedback_button_opens_a_form_for_the_right_slide(self):
        r = self.reviews.create(self.cid)
        self.reviews.move(r['id'], 'next')
        i = self.interaction(111, r['id'], 'fb')
        asyncio.run(self.bridge.on_review_interaction(i))
        modal = i.response.send_modal.call_args.args[0]
        self.assertEqual(modal.title, 'Slide 2 of 3')
        modal.box._value = 'Tighten this.'
        sub = MagicMock(); sub.user = SimpleNamespace(id=111); sub.message = None
        sub.response.send_message = AsyncMock()
        asyncio.run(modal.on_submit(sub))
        self.assertEqual(self.reviews.get(r['id'])['feedback'][0]['slide'], 1)
        self.assertIn("saved to the carousel's notes", sub.response.send_message.call_args.args[0])
        stranger = MagicMock(); stranger.user = SimpleNamespace(id=999); stranger.response.send_message = AsyncMock()
        asyncio.run(modal.on_submit(stranger))
        self.assertEqual(len(self.reviews.get(r['id'])['feedback']), 1)

    def test_unknown_or_foreign_interactions_are_ignored(self):
        i = MagicMock(); i.type = discord.InteractionType.component; i.data = {'custom_id': 'something:else'}
        i.response.send_message = AsyncMock()
        asyncio.run(self.bridge.on_review_interaction(i))
        i.response.send_message.assert_not_awaited()

    def channel(self, name, everyone_can_see=True, can_post=True, members=2, owner=111, bots=('sam',)):
        """A fake server: Josh (111) owns it, `members` people and bots are in it, and these bridge bots are connected."""
        perms = lambda who: SimpleNamespace(view_channel=everyone_can_see if who == 'everyone' else True, send_messages=can_post, attach_files=can_post)
        g = SimpleNamespace(me='me', default_role='everyone', id=1, name='Test server', owner_id=owner, member_count=members)
        ch = SimpleNamespace(name=name, id=42, guild=g, permissions_for=perms)
        g.text_channels = [ch]
        self.bridge.clients = {a: SimpleNamespace(user='bot-' + a, get_guild=lambda gid: g) for a in bots}
        self.bridge.status['bots'] = {a: dict(connected=True) for a in bots}
        return SimpleNamespace(guilds=[g])

    def test_review_only_goes_where_everyone_in_the_server_is_josh_or_his_bots(self):
        ch, why, ev = self.bridge.review_channel(self.channel('sam'), 'sam')
        self.assertEqual(ch.id, 42)
        self.assertEqual((ev['member_count'], ev['owner_is_josh'], ev['josh_bots_in_server']), (2, True, ['sam']))
        self.assertIsNone(self.bridge.review_channel(self.channel('general'), 'sam')[0])
        ch, why, _ = self.bridge.review_channel(self.channel('sam', members=3), 'sam')
        self.assertIsNone(ch); self.assertIn('3 members but only 2 are accounted for', why)
        ch, why, _ = self.bridge.review_channel(self.channel('sam', members=None), 'sam')
        self.assertIsNone(ch); self.assertIn("didn't report", why)
        ch, why, _ = self.bridge.review_channel(self.channel('sam', owner=999), 'sam')
        self.assertIsNone(ch); self.assertIn("owner isn't Josh", why)
        self.assertIsNone(self.bridge.review_channel(self.channel('sam', can_post=False), 'sam')[0])
        # A private channel doesn't make a crowded server safe: its role grants can't be checked without extra access.
        self.assertIsNone(self.bridge.review_channel(self.channel('sam', everyone_can_see=False, members=5), 'sam')[0])
        client = self.channel('sam', members=3, bots=('sam', 'tariq'))
        self.assertEqual(self.bridge.review_channel(client, 'sam')[0].id, 42)
        self.bridge.config['bots']['tariq'] = dict(token='x'); self.bridge.status['bots']['tariq'] = dict(connected=False, error='bad token')
        self.assertIn('Not every one', self.bridge.review_channel(client, 'sam')[1])
        # Josh's own confirmation replaces only the member count, never the owner or channel checks.
        self.assertEqual(self.bridge.review_channel(self.channel('sam', members=6), 'sam', owner_confirmed=True)[0].id, 42)
        self.assertIsNone(self.bridge.review_channel(self.channel('sam', members=6, owner=999), 'sam', owner_confirmed=True)[0])
        self.assertIsNone(self.bridge.review_channel(self.channel('general', members=6), 'sam', owner_confirmed=True)[0])

    def test_a_request_is_sent_once_and_an_unclear_send_is_never_retried(self):
        d = discord_review.requests_dir(self.store.root); d.mkdir(parents=True)
        (d / 'one.json').write_text(json.dumps(dict(carousel_id=self.cid, agent_id='sam', test=True)), encoding='utf-8')
        client = self.channel('sam')
        self.bridge.post_review = AsyncMock(side_effect=TimeoutError('no answer'))
        asyncio.run(self.bridge.send_requested_reviews(client, 'sam'))
        out = json.loads((d / 'one.result.json').read_text(encoding='utf-8'))
        self.assertEqual(out['state'], 'uncertain')
        asyncio.run(self.bridge.send_requested_reviews(client, 'sam'))
        self.assertEqual(self.bridge.post_review.await_count, 1)
        (d / 'two.json').write_text(json.dumps(dict(carousel_id=self.cid, agent_id='sam', test=True)), encoding='utf-8')
        self.bridge.post_review = AsyncMock(return_value=SimpleNamespace(id=777, jump_url='https://discord.com/channels/1/42/777'))
        ch = client.guilds[0].text_channels[0]
        ch.fetch_message = AsyncMock(return_value=SimpleNamespace(embeds=[SimpleNamespace(description='**Test review. Test carousel** Slide 1 of 3.')],
                                                                  components=[SimpleNamespace(children=[1, 2]), SimpleNamespace(children=[3, 4])]))
        asyncio.run(self.bridge.send_requested_reviews(client, 'sam'))
        out = json.loads((d / 'two.result.json').read_text(encoding='utf-8'))
        self.assertEqual((out['state'], out['message_id'], out['confirmed'], out['confirmed_buttons']), ('sent', '777', True, 4))
        (d / 'three.json').write_text(json.dumps(dict(carousel_id=self.cid, agent_id='sam', test=True)), encoding='utf-8')
        self.bridge.post_review = AsyncMock()
        asyncio.run(self.bridge.send_requested_reviews(self.channel('sam', members=4), 'sam'))
        out = json.loads((d / 'three.result.json').read_text(encoding='utf-8'))
        self.assertEqual(out['state'], 'blocked'); self.bridge.post_review.assert_not_awaited()
        (d / 'four.json').write_text(json.dumps(dict(carousel_id=self.cid, agent_id='sam', test=True, expected_channel_id='999',
                                                     audience_confirmed_by_owner=dict(quote='private'))), encoding='utf-8')
        asyncio.run(self.bridge.send_requested_reviews(self.channel('sam', members=6), 'sam'))
        out = json.loads((d / 'four.result.json').read_text(encoding='utf-8'))
        self.assertIn('not the one this request names', out['reason']); self.bridge.post_review.assert_not_awaited()

if __name__ == '__main__':
    unittest.main()
