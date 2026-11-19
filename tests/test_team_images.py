"""Max's approved images and the team group picture (added 11 Nov 2026). Checks the exact originals are in place,
the display copies are whole, the routes find them, and Max's portrait is added once without overriding Josh."""
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image

ROOT = Path(r'E:\Videos\DaJoshiResolve')
sys.path.insert(0, str(ROOT))
from studio import team as T  # noqa: E402
from studio.store import Store  # noqa: E402

APPROVED = {  # exact files Josh approved, by sha256
    ROOT / 'team/offices/max.png': '9844fd451d023fe8d55b24da15a4691253a61239ca6959490757185598a7df57',
    ROOT / 'team/originals/max-profile.png': '86931a798f90b6d02586e3f1fa39aa65d2bb0021791cd40003a6969da1a285e6',
    ROOT / 'team/group/team.png': 'b3e0bcaecb8396598d773c9f41218ba8ada3e48d867c8bb8afcda1295ef0fdce',
}


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


class Files(unittest.TestCase):
    def test_originals_are_the_exact_approved_bytes(self):
        for p, digest in APPROVED.items():
            self.assertEqual(sha(p), digest, p)

    def test_display_copies_keep_the_full_frame(self):
        self.assertEqual(Image.open(ROOT / 'studio/team_photos/max.jpg').size, (512, 512))
        self.assertEqual(Image.open(ROOT / 'team/group/team.jpg').size, Image.open(ROOT / 'team/group/team.png').size)

    def test_routes_find_the_images(self):
        self.assertEqual(T.office_file('max').name, 'max.png')
        self.assertEqual(T.group_file().name, 'team.jpg')
        self.assertIsNone(T.office_file('../max'))

    def test_office_is_ready_and_manifest_matches(self):
        self.assertEqual(T.offices(['max'])['max']['status'], 'ready')
        m = json.loads((ROOT / 'team/offices/offices.json').read_text(encoding='utf-8'))['offices']['max']
        self.assertEqual((m['file'], m['sha256']), ('max.png', APPROVED[ROOT / 'team/offices/max.png']))


class Portrait(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name))

    def tearDown(self):
        self.temp.cleanup()

    def max(self, team):
        return next(a for a in team.all()['agents'] if a['id'] == 'max')

    def test_new_install_gets_max_portrait_and_group(self):
        t = T.Team(self.store)
        self.assertTrue(self.max(t)['photo'])
        self.assertTrue(t.all()['group']['ready'])

    def test_existing_max_without_photo_gets_it_once_and_a_removal_sticks(self):
        T.Team(self.store)
        with self.store.connect() as c:   # Max as he was before 11 Nov: no photo
            b = json.loads(c.execute("SELECT body FROM team_agents WHERE id='max'").fetchone()['body'])
            b['photo'] = ''
            b.pop('photo_backfilled', None)
            c.execute("UPDATE team_agents SET body=? WHERE id='max'", (json.dumps(b),))
        t = T.Team(self.store)
        m = self.max(t)
        self.assertTrue(m['photo'])
        self.assertTrue(m['photo_backfilled'])
        m = t.save('max', {'photo': ''}, m['revision'])   # Josh removes it
        self.assertEqual(self.max(T.Team(self.store))['photo'], '')

    def test_photo_josh_chose_is_never_replaced(self):
        t = T.Team(self.store)
        sam_photo = next(a for a in t.all()['agents'] if a['id'] == 'sam')['photo']
        with self.store.connect() as c:
            b = json.loads(c.execute("SELECT body FROM team_agents WHERE id='max'").fetchone()['body'])
            b.update(photo=sam_photo)
            b.pop('photo_backfilled', None)
            c.execute("UPDATE team_agents SET body=? WHERE id='max'", (json.dumps(b),))
        self.assertEqual(self.max(T.Team(self.store))['photo'], sam_photo)


if __name__ == '__main__':
    unittest.main()
