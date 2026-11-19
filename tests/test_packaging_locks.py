"""A locked-in title or main thumbnail can't be overwritten by a save (13 Nov 2026, the Packaging page)."""
import tempfile
import unittest
import uuid
from pathlib import Path

from PIL import Image
import io

from studio.store import Store, Conflict
from studio.release_ready import Release


def png(colour):
    b = io.BytesIO()
    Image.new('RGB', (64, 36), colour).save(b, 'PNG')
    return b.getvalue()


class LockedPackaging(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / 'Data')
        self.pid = str(uuid.uuid4())
        self.store.create('Test video', self.pid)
        self.t1, self.t2 = str(uuid.uuid4()), str(uuid.uuid4())
        self.a = self.store.import_image('a.png', png((1, 2, 3)))
        self.b = self.store.import_image('b.png', png((4, 5, 6)))
        p = self.store.get(self.pid)
        self.store.save(self.pid, dict(titles=[dict(id=self.t1, text='First title'), dict(id=self.t2, text='Second title')], chosen_title=self.t1,
                                       thumbnails=[self.a, self.b], top_three=[self.a['asset'], self.b['asset']],
                                       locks=dict(title='2026-11-13T09:00:00', thumbnail='2026-11-13T09:00:00')), p['revision'])

    def tearDown(self):
        self.tmp.cleanup()

    def save(self, **changes):
        return self.store.save(self.pid, changes, self.store.get(self.pid)['revision'])

    def test_locked_title_cannot_change(self):
        with self.assertRaises(Conflict):
            self.save(chosen_title=self.t2)
        with self.assertRaises(Conflict):
            self.save(chosen_title='')
        with self.assertRaises(Conflict):
            self.save(titles=[dict(id=self.t1, text='Edited words'), dict(id=self.t2, text='Second title')])
        self.assertEqual(self.store.get(self.pid)['chosen_title'], self.t1)

    def test_new_ideas_can_still_be_added(self):
        t3 = str(uuid.uuid4())
        p = self.save(titles=self.store.get(self.pid)['titles'] + [dict(id=t3, text='A new idea')])
        self.assertEqual(len(p['titles']), 3)

    def test_unlocking_first_allows_a_change(self):
        p = self.save(locks=dict(thumbnail='2026-11-13T09:00:00'), chosen_title=self.t2)
        self.assertEqual(p['chosen_title'], self.t2)

    def test_locked_main_thumbnail_cannot_change(self):
        with self.assertRaises(Conflict):
            self.save(top_three=[self.b['asset'], self.a['asset']])
        p = self.save(top_three=[self.a['asset']])   # the test thumbnails can still change
        self.assertEqual(p['top_three'], [self.a['asset']])

    def test_release_view_lists_links_and_lead_magnet(self):
        self.save(lead_magnet=dict(decision='yes', name='Test checklist'),
                  release=dict(links=[dict(url='https://example.test/x', source_type='pinned_comment', secret='not shown')]))
        v = Release(self.store, None, Path(self.tmp.name) / 'pub').view(self.pid)
        self.assertEqual(v['links'], [dict(url='https://example.test/x', source='pinned_comment')])
        self.assertEqual(v['lead_magnet'], dict(decision='yes', name='Test checklist'))


class RecordPublished(unittest.TestCase):
    """A video YouTube reports as public is recorded locally without touching drafts, approvals or history."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / 'Data')
        self.pid = str(uuid.uuid4())
        self.store.create('Test video', self.pid)
        self.t1 = str(uuid.uuid4())
        p = self.store.get(self.pid)
        self.store.save(self.pid, dict(stage='Editing', publish_date='2026-11-11', titles=[dict(id=self.t1, text='Test title')], chosen_title=self.t1,
                                       locks=dict(title='2026-11-12T06:59:07'),
                                       release=dict(description=dict(text='Old draft', by='sam'), history=[dict(at='x', what='description')])), p['revision'])
        self.rel = Release(self.store, None, Path(self.tmp.name) / 'pub')
        self.video = dict(id='TESTVIDEO01', snippet=dict(title='Test title', channelId='UCTEST', description='Live text', publishedAt='2026-11-12T15:18:10Z'),
                          status=dict(privacyStatus='public', uploadStatus='processed'))

    def tearDown(self):
        self.tmp.cleanup()

    def test_records_the_live_video_and_keeps_everything_else(self):
        rev = self.store.get(self.pid)['revision']
        p = self.rel.record_published(self.pid, self.video, rev, 'UCTEST', read_at='2026-11-13T09:00:00+00:00')
        self.assertEqual((p['stage'], p['publish_date'], p['locks']['publish'], p['locks']['title']), ('Published', '2026-11-12', '2026-11-12T15:18:10Z', '2026-11-12T06:59:07'))
        self.assertEqual(p['release']['description'], dict(text='Old draft', by='sam'))
        self.assertEqual(p['release']['history'][0], dict(at='x', what='description'))
        self.assertEqual(p['release']['history'][-1]['what'], 'youtube')
        self.assertEqual(p['release']['youtube']['description'], 'Live text')
        v = self.rel.view(self.pid)
        self.assertEqual(v['youtube']['video_id'], 'TESTVIDEO01')
        self.assertEqual(next(i for i in v['items'] if i['key'] == 'upload')['state'], 'done')
        self.assertNotEqual(next(i for i in v['items'] if i['key'] == 'description')['state'], 'done')   # no approval is made up

    def test_refuses_a_stale_revision_or_a_mismatch(self):
        rev = self.store.get(self.pid)['revision']
        with self.assertRaises(ValueError):
            self.rel.record_published(self.pid, self.video, rev - 1, 'UCTEST')
        with self.assertRaises(ValueError):
            self.rel.record_published(self.pid, self.video, rev, 'UCOTHER')
        bad = dict(self.video, snippet=dict(self.video['snippet'], title='Another title'))
        with self.assertRaises(ValueError):
            self.rel.record_published(self.pid, bad, rev, 'UCTEST')
        private = dict(self.video, status=dict(privacyStatus='private', uploadStatus='processed'))
        with self.assertRaises(ValueError):
            self.rel.record_published(self.pid, private, rev, 'UCTEST')
        self.assertEqual(self.store.get(self.pid)['stage'], 'Editing')


if __name__ == '__main__':
    unittest.main()
