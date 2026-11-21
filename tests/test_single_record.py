"""Josh OS is the one record (21 Nov 2026).

Josh: "the app is the single source of truth at all times everything references off of". Two misses led to this:
- Sam put new description and pinned comment links into a separate release packet, because a chat could not change
  the card's own description or pinned comment. The video page never showed them.
- Asked to swap today's carousel, Sam added the new one on 21 Nov and only wrote "bumped" in the old one's notes, so
  both stayed on 21 Nov and Today kept showing the old one.

These use the real Store and the real carousel tracker on a disposable folder. The model is a stub, so nothing is sent.
"""
import tempfile
import unittest
from pathlib import Path

from studio.carousels import Carousels, date_clashes
from studio.project_publishing import summary
from tests import test_sam_publishing
from studio.agent import Agents

SamPublishing = test_sam_publishing.SamPublishing
from studio.chat_release_actions import ChatReleaseActions
from studio.team import Team


class CardTexts(unittest.TestCase):
    def setUp(self):
        base = SamPublishing('test_summary_is_what_the_video_page_shows')
        base.setUp()
        self.addCleanup(base.doCleanups)
        self.b = base
        self.agents = Agents(base.store, base.root, Team(base.store))
        self.agents.training = lambda owner: ('', [])
        self.agents.publishing = base.pub
        self.agents.chat_release_actions = ChatReleaseActions(base.release)
        self.calls, self.answer = [], dict(reply='Done.')

        def run(system, prompt, schema, *args, **kwargs):
            self.calls.append(dict(system=system, prompt=prompt, schema=schema))
            return self.answer['reply'], self.answer
        self.agents.run = run

    def ask(self, message, agent='sam'):
        return self.agents.chat(dict(agent_id=agent, message=message), [])

    def test_new_links_go_on_the_card_and_the_video_page_shows_them(self):
        before = summary(self.b.store, self.b.pid)
        self.assertTrue(before['description_approved'])
        desc = 'Join the masterclass: https://linklr.app/r/5xyd9qap\n\nThe rest of the description.'
        pin = 'Get the free guide: https://linklr.app/r/fgagdvc3'
        self.answer = dict(reply='Swapped the links in.', projects=[dict(project_id=self.b.pid, description=desc, pinned_comment=pin)])
        self.ask(f'Put the tracked links in the description and pinned comment of {self.b.pid}')
        after = summary(self.b.store, self.b.pid)
        self.assertEqual(after['description'], desc)
        self.assertTrue(after['pinned_comment'].startswith(pin))
        self.assertFalse(after['description_approved'], 'changed words need Josh to approve them again')
        self.assertFalse(after['pinned_comment_approved'])
        r = self.b.store.get(self.b.pid)['release']
        self.assertEqual(r['description']['by'], 'sam')
        self.assertEqual(r['history'][-1]['what'], 'description, cta')
        self.assertEqual(r['export_approval']['path'], str(self.b.export.resolve()), 'the rest of the release is kept')
        self.assertEqual(self.b.pub.sources, [], 'no packet is made to change words')

    def test_the_same_text_changes_nothing(self):
        rev = self.b.store.get(self.b.pid)['revision']
        self.answer = dict(reply='Done.', projects=[dict(project_id=self.b.pid, description=self.b.desc)])
        self.ask(f'Keep the description of {self.b.pid}')
        self.assertTrue(summary(self.b.store, self.b.pid)['description_approved'])
        self.assertEqual(self.b.store.get(self.b.pid)['revision'], rev)

    def test_every_team_member_is_told_the_app_is_the_record(self):
        for agent in ('sam', 'laia', 'tariq'):
            self.ask('Hello', agent)
            system = self.calls[-1]['system']
            self.assertIn('Josh OS is the one record of everything', system, agent)
            self.assertIn('the old one comes off it', system, agent)
        props = self.calls[-1]['schema']['properties']['projects']['items']['properties']
        self.assertIn('description', props)
        self.assertIn('pinned_comment', props)


class CarouselSwap(unittest.TestCase):
    def setUp(self):
        base = SamPublishing('test_summary_is_what_the_video_page_shows')
        base.setUp()
        self.addCleanup(base.doCleanups)
        self.store = base.store
        self.tracker = Carousels(self.store)
        self.old = self.tracker.create(dict(title='Be the one whose numbers nobody questions', stage='Idea', post_date='2026-11-21', owner='sam'))
        self.agents = Agents(self.store, base.root, Team(self.store))
        self.agents.training = lambda owner: ('', [])
        self.answer = dict(reply='Done.')
        self.agents.run = lambda system, prompt, schema, *a, **k: (self.answer['reply'], self.answer)

    def ask(self, message):
        items = self.tracker.all()['items']
        return self.agents.chat(dict(agent_id='sam', message=message), items, tracker=self.tracker)['reply']['text']

    def test_a_swap_left_on_the_same_day_is_flagged(self):
        self.answer = dict(reply='Done.', carousels=[
            dict(title='Fix these 4 slides (repeat)', stage='Approved', post_date='2026-11-21'),
            dict(id=self.old['id'], notes='Bumped for the repeat. Needs a new date from Josh.')])
        out = self.ask("Change today's carousel to our best one")
        self.assertIn('are all set for 2026-11-21', out)
        self.assertIn('Be the one whose numbers nobody questions', out)

    def test_a_proper_swap_clears_the_old_date_and_is_not_flagged(self):
        self.answer = dict(reply='Done.', carousels=[
            dict(title='Fix these 4 slides (repeat)', stage='Approved', post_date='2026-11-21'),
            dict(id=self.old['id'], post_date='')])
        out = self.ask("Change today's carousel to our best one")
        self.assertNotIn('all set for', out)
        today = [c['title'] for c in self.tracker.all()['items'] if c['post_date'] == '2026-11-21']
        self.assertEqual(today, ['Fix these 4 slides (repeat)'])
        self.assertEqual(self.tracker.get(self.old['id'])['post_date'], '')

    def test_posted_and_archived_posts_do_not_count(self):
        items = [dict(id='a', title='A', post_date='2026-11-21', stage='Approved'),
                 dict(id='b', title='B', post_date='2026-11-21', stage='Posted'),
                 dict(id='c', title='C', post_date='2026-11-21', stage='Idea', archived=True)]
        self.assertEqual(date_clashes(items, [dict(id='a', action='added')]), [])
        items.append(dict(id='d', title='D', post_date='2026-11-21', stage='Idea'))
        self.assertEqual(len(date_clashes(items, [dict(id='a', action='added'), dict(id='d', action='updated')])), 1, 'one line per clash')


if __name__ == '__main__':
    unittest.main()
