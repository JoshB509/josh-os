"""The open video shows what is saved, at all times (23 Nov 2026).

Josh asked Sam to put new links in a video's description. Sam saved them on the card, but the open video page kept
its old copy of the description and pinned comment, even after the chat refreshed the card. These tests change the
video the way a team member does (straight in the Store, not through the page) and check the page catches up by
itself, without losing anything Josh is typing.

Run from tests/ with PYTHONPATH set to the repo root, like the other video page browser tests.
"""
import unittest

import test_video_workflow_browser as harness  # the module, so its own tests aren't collected again here


class LiveSync(unittest.TestCase):
    setUpClass = classmethod(harness.VideoWorkflow.setUpClass.__func__)
    tearDownClass = classmethod(harness.VideoWorkflow.tearDownClass.__func__)
    setUp = harness.VideoWorkflow.setUp
    open = harness.VideoWorkflow.open

    def sam_changes_the_description(self, text):
        p = self.store.get(self.confirmed)
        self.store.save(self.confirmed, dict(release=dict(p.get('release') or {}, description=dict(text=text, by='sam'))), p['revision'])
        self.rel[self.confirmed] = dict(self.rel[self.confirmed], description=text, description_sha='new')

    def test_a_change_made_elsewhere_shows_on_the_open_video(self):
        self.rel[self.confirmed] = dict(self.rel[self.confirmed], description='Old description, no links.', description_sha='old')
        self.open(self.confirmed, 'packaging')
        self.page.wait_for_function("document.querySelector('.pk')?.innerText.includes('Old description')", timeout=10000)
        self.sam_changes_the_description('Get the toolkit: https://linklr.app/r/rc3nqzkv')
        self.page.wait_for_function("document.querySelector('.pk')?.innerText.includes('linklr.app/r/rc3nqzkv')", timeout=10000)
        self.assertNotIn('Old description', self.page.locator('.pk').inner_text())
        self.assertEqual(self.page.evaluate('current.revision'), self.store.get(self.confirmed)['revision'])
        self.assertEqual(self.errors, [])

    def test_unsaved_edits_are_kept_and_josh_is_told(self):
        self.rel[self.confirmed] = dict(self.rel[self.confirmed], description='Old description, no links.', description_sha='old')
        self.open(self.confirmed, 'packaging')
        self.page.evaluate("current.name='Josh is typing this';mark()")
        before = self.page.evaluate('current.revision')
        self.sam_changes_the_description('Get the toolkit: https://linklr.app/r/rc3nqzkv')
        self.page.wait_for_function("document.body.innerText.includes('This video changed while you were editing')", timeout=10000)
        self.assertEqual(self.page.evaluate('current.name'), 'Josh is typing this')
        self.assertEqual(self.page.evaluate('current.revision'), before)
        self.assertEqual(self.errors, [])


if __name__ == '__main__':
    unittest.main()
