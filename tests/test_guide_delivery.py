"""Saskia's approved guides into CareerOS Downloads (studio/guide_delivery.py). Temp folders and a fake Drive check only."""
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(r'E:\Videos\DaJoshiResolve')
sys.path.insert(0, str(ROOT))
from studio.guide_delivery import GuideDelivery  # noqa: E402
from studio.store import Store  # noqa: E402

OK = dict(by='josh', said='Approved, put it in Downloads', at='2026-11-12T10:00:00Z')


class Delivery(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = Path(self.tmp.name)
        self.down = t / 'Downloads'
        self.down.mkdir()
        self.src = t / 'Guides' / '2026-11-11_v14_performance-reviews_guide.pdf'
        self.src.parent.mkdir()
        self.src.write_bytes(b'%PDF-1.7 v14 guide')
        self.drive = dict(state='confirmed')
        self.asked = []
        self.d = GuideDelivery(Store(t / 'Data'), t, self.down, checker=lambda n, s: (self.asked.append((n, s)), self.drive)[1])

    def tearDown(self):
        self.tmp.cleanup()

    def test_a_new_version_is_copied_checked_and_confirmed_with_drive(self):
        r = self.d.deliver(self.src, 'Performance reviews', OK)
        self.assertEqual(r['state'], 'synced')
        self.assertEqual((self.down / 'Performance reviews v14.pdf').read_bytes(), self.src.read_bytes())
        self.assertEqual(self.asked, [('Performance reviews v14.pdf', self.src.stat().st_size)])
        self.assertEqual(list(self.down.glob('*.partial')), [])

    def test_the_existing_v14_working_document_is_left_alone(self):
        existing = self.down / 'Performance reviews - goals and the review - working document v14.pdf'
        existing.write_bytes(self.src.read_bytes())
        before = existing.stat().st_mtime_ns
        r = self.d.deliver(self.src, 'Performance reviews', OK)
        self.assertEqual(r['state'], 'already_there')
        self.assertEqual(existing.stat().st_mtime_ns, before)
        self.assertEqual(sorted(f.name for f in self.down.iterdir()), [existing.name])

    def test_older_versions_and_other_files_are_never_replaced(self):
        (self.down / 'Performance reviews v14.pdf').write_bytes(b'%PDF other contents')
        (self.down / 'Story Builder playbook.pdf').write_bytes(b'%PDF story')
        r = self.d.deliver(self.src, 'Performance reviews', OK)
        self.assertEqual(Path(r['file']).name, 'Performance reviews v15.pdf')
        self.assertEqual((self.down / 'Performance reviews v14.pdf').read_bytes(), b'%PDF other contents')
        self.assertEqual((self.down / 'Story Builder playbook.pdf').read_bytes(), b'%PDF story')

    def test_not_synced_yet_is_said_plainly(self):
        self.drive = dict(state='not_yet', reason='Google Drive does not show the file yet.')
        r = self.d.deliver(self.src, 'Performance reviews', OK)
        self.assertEqual(r['state'], 'copied')
        self.assertIn('does not show the file yet', GuideDelivery.describe(r))
        self.drive = dict(state='confirmed')
        self.assertEqual(self.d.recheck(r['id'])['state'], 'synced')

    def test_no_approval_or_no_folder_means_no_copy(self):
        with self.assertRaises(ValueError):
            self.d.deliver(self.src, 'Performance reviews', None)
        with self.assertRaises(ValueError):
            self.d.deliver(self.src, 'Performance reviews', dict(by='josh', said=''))
        gone = GuideDelivery(self.d.store, self.tmp.name, Path(self.tmp.name) / 'missing', checker=lambda n, s: {})
        with self.assertRaises(ValueError):
            gone.deliver(self.src, 'Performance reviews', OK)
        self.assertEqual(list(self.down.iterdir()), [])


if __name__ == '__main__':
    unittest.main()
