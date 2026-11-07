import json, tempfile, unittest, uuid
from pathlib import Path
from studio.store import Store, Conflict
from studio.team import Team, SEEDS
from studio.carousels import Carousels
from studio.phone import PhoneAccess


class TeamTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / 'Data')
        self.team = Team(self.store)

    def tearDown(self):
        self.temp.cleanup()

    def test_seeds_once_and_keeps_edits(self):
        agents = self.team.all()['agents']
        self.assertEqual([a['id'] for a in agents], [s['id'] for s in SEEDS])
        sam = self.team.save('sam', {'name': 'Sammy', 'workflows': ['youtube']}, 1)
        Team(self.store)
        again = {a['id']: a for a in self.team.all()['agents']}['sam']
        self.assertEqual(again, sam)
        self.assertEqual(again['revision'], 2)

    def test_stale_and_invalid_edits_are_rejected(self):
        self.team.save('laia', {'mission': 'New'}, 1)
        with self.assertRaises(Conflict):
            self.team.save('laia', {'mission': 'Older window'}, 1)
        for bad in [{'name': ''}, {'workflows': ['nope']}, {'status': 'Fired'}, {'color': 'red'}, {'photo': 'x' * 64}, {'salary': 1}]:
            with self.assertRaises(ValueError):
                self.team.save('laia', bad, 2)

    def test_default_photos_are_imported(self):
        agents = {a['id']: a for a in self.team.all()['agents']}
        for aid in ('sam', 'tariq', 'laia'):
            self.assertEqual(len(agents[aid]['photo']), 64)
            self.assertTrue(self.store.asset(agents[aid]['photo'])[0].is_file())
        self.assertEqual(agents['max']['photo'], '')

    def test_first_draft_names_are_migrated(self):
        with self.store.connect() as c:
            c.execute('DELETE FROM team_agents')
            for i, (aid, rev) in enumerate([('bob', 3), ('eddie', 1), ('vera', 1)]):
                c.execute('INSERT INTO team_agents VALUES(?,?,?,?)', (aid, rev, i, json.dumps(dict(id=aid, name=aid.title(), revision=rev))))
            c.execute('INSERT INTO team_learnings VALUES(?,?,?)', ('l1', 'vera', json.dumps(dict(id='l1', agent_id='vera', lesson='x'))))
        Carousels(self.store).create({'id': str(uuid.uuid4()), 'title': 'Old owner', 'owner': 'bob'})
        Team(self.store)
        data = self.team.all()
        ids = [a['id'] for a in data['agents']]
        self.assertEqual(sorted(ids), ['laia', 'max', 'sam', 'saskia', 'tariq'])
        agents = {a['id']: a for a in data['agents']}
        self.assertEqual(agents['sam']['name'], 'Bob')  # edited profile kept under the new id
        self.assertEqual(agents['tariq']['name'], 'Tariq')  # untouched profile replaced
        self.assertEqual(data['learnings'][0]['agent_id'], 'laia')
        self.assertEqual(Carousels(self.store).all()['items'][0]['owner'], 'sam')

    def test_new_member_joins_existing_team_in_place(self):
        with self.store.connect() as c:
            c.execute("DELETE FROM team_agents WHERE id='saskia'")
            c.execute("UPDATE team_agents SET position=3 WHERE id='max'")
        Team(self.store)
        ids = [a['id'] for a in self.team.all()['agents']]
        self.assertEqual(ids, ['sam', 'tariq', 'laia', 'saskia', 'max'])
        saskia = self.team.all()['agents'][3]
        self.assertEqual((saskia['name'], saskia['workflows']), ('Saskia', ['courses', 'calendar']))
        self.assertEqual(len(saskia['photo']), 64)

    def test_old_default_roles_update_but_edited_ones_stay(self):
        self.team.save('laia', {'role': 'Email Writer', 'personality': 'My own words.'}, 1)
        self.team.save('sam', {'role': 'Content & YouTube Manager'}, 1)
        Team(self.store)
        agents = {a['id']: a for a in self.team.all()['agents']}
        self.assertEqual(agents['laia']['role'], 'Email Copywriter')
        self.assertEqual(agents['laia']['personality'], 'My own words.')
        self.assertEqual(agents['sam']['role'], 'YouTube Channel Manager')

    def test_lessons_learned(self):
        lid = str(uuid.uuid4())
        item = self.team.learn({'id': lid, 'agent_id': 'sam', 'workflow': 'carousels', 'lesson': 'Lead with a number'})
        self.assertEqual(self.team.learn({'id': lid, 'lesson': 'retry'}), item)
        self.assertEqual(self.team.all()['learnings'], [item])
        with self.assertRaises(ValueError):
            self.team.learn({'lesson': ''})
        with self.assertRaises(ValueError):
            self.team.learn({'agent_id': 'nobody', 'lesson': 'x'})
        self.team.forget(lid)
        self.assertEqual(self.team.all()['learnings'], [])


class CarouselTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / 'Data')
        self.carousels = Carousels(self.store)

    def tearDown(self):
        self.temp.cleanup()

    def test_create_is_idempotent_and_saves(self):
        cid = str(uuid.uuid4())
        c = self.carousels.create({'id': cid, 'title': '3 promotion myths'})
        self.assertEqual(self.carousels.create({'id': cid, 'title': 'Double click'}), c)
        saved = self.carousels.save(cid, {'stage': 'Posted', 'link': 'https://instagram.com/p/1', 'post_date': '2026-11-09'}, 1)
        self.assertTrue(saved['posted_at'])
        self.assertEqual(self.carousels.all()['items'], [saved])
        reopened = self.carousels.save(cid, {'stage': 'Approved'}, 2)
        self.assertIsNone(reopened['posted_at'])

    def test_validation_and_conflicts(self):
        c = self.carousels.create({'title': 'Hook test'})
        for bad in [{'title': ' '}, {'stage': 'Viral'}, {'platforms': ['MySpace']}, {'post_date': '2026-02-30'},
                    {'link': 'javascript:alert(1)'}, {'project_id': str(uuid.uuid4())}, {'likes': 3}]:
            with self.assertRaises((ValueError, KeyError)):
                self.carousels.save(c['id'], bad, 1)
        self.carousels.save(c['id'], {'hook': 'New hook'}, 1)
        with self.assertRaises(Conflict):
            self.carousels.save(c['id'], {'hook': 'Old window'}, 1)


class PhoneTests(unittest.TestCase):
    def test_disabled_without_setting_and_stable_key_with_it(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            self.assertFalse(PhoneAccess(root, root).status('k')['enabled'])
            (root / 'settings.json').write_text(json.dumps({'phone_access': {'host': 'josh-pc.tail1234.ts.net', 'port': 8765}}))
            a, b = PhoneAccess(root, root), PhoneAccess(root, root)
            self.assertTrue(a.key)
            self.assertEqual(a.key, b.key)
            self.assertEqual(a.hosts(), ('josh-pc.tail1234.ts.net', 'josh-pc.tail1234.ts.net:443'))
            (root / 'settings.json').write_text(json.dumps({'phone_access': {'host': 'evil.com/x', 'port': 8765}}))
            bad = PhoneAccess(root, root)
            self.assertEqual(bad.hosts(), ())
            self.assertTrue(bad.status('k')['error'])


if __name__ == '__main__':
    unittest.main()
