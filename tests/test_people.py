import json, tempfile, unittest, uuid
from pathlib import Path
from studio.store import Store, Conflict
from studio.people import People
from studio.team import Team
from studio import agent as agent_mod
from studio.agent import Agents


class PeopleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = Store(self.root / 'Data')
        self.people = People(self.store)

    def tearDown(self):
        self.temp.cleanup()

    def test_create_save_and_history(self):
        p = self.people.create(dict(name='Priya', email='Priya@Example.com', pathway='reply', source='Replied to "Top of the list"'),
                               [dict(direction='in', summary='Asked how to raise promotion with her boss')])
        self.assertEqual((p['email'], p['stage'], len(p['history'])), ('priya@example.com', 'New reply', 1))
        p = self.people.save(p['id'], dict(stage='Replied', next_step='Check in after her 1:1', next_due='2026-11-20'), 1,
                             [dict(direction='out', summary='Josh sent the reply')])
        self.assertEqual((p['revision'], len(p['history'])), (2, 2))
        with self.assertRaises(Conflict):
            self.people.save(p['id'], {'stage': 'Warming'}, 1)
        moved = self.people.save(p['id'], {'pathway': 'buyer'}, 2)
        self.assertEqual(moved['stage'], 'Just bought')

    def test_validation_and_duplicates(self):
        self.people.create(dict(email='a@b.co', pathway='client'))
        for bad in [dict(email='nope', pathway='reply'), dict(name='X', pathway='fan'), dict(name='X', pathway='lead', stage='Bought'),
                    dict(name='X', pathway='lead', next_due='soon'), dict(pathway='lead')]:
            with self.assertRaises(ValueError, msg=str(bad)):
                self.people.create(bad)
        with self.assertRaises(Conflict):
            self.people.create(dict(email='A@B.co', pathway='lead'))

    def test_anouk_saves_merge_by_email_and_keep_promises(self):
        first = self.people.apply([dict(name='Sam Lee', email='sam@lee.io', pathway='lead', stage='Call done',
                                        promises=[dict(text='Send the template', due='2026-11-10'), dict(text='Check the dates')],
                                        add_history=[dict(direction='note', channel='call', summary='Call about the programme')])])
        self.assertEqual(first[0]['action'], 'added')
        again = self.people.apply([dict(email='sam@lee.io', stage='Follow-up sent', promises=[dict(text='send the template', done=True)],
                                        add_history=[dict(direction='draft', summary='Follow-up drafted')]),
                                   dict(id='missing', stage='Active')])
        self.assertEqual([d['action'] for d in again], ['updated', 'failed'])
        p = self.people.find('sam@lee.io')
        self.assertEqual(p['stage'], 'Follow-up sent')
        self.assertEqual(sorted((x['text'].lower(), x['done']) for x in p['promises']), [('check the dates', False), ('send the template', True)])
        self.assertEqual([h['direction'] for h in p['history']], ['note', 'draft'])


class AnoukSetupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = Store(self.root / 'Data')
        self.team = Team(self.store)
        self.agents = Agents(self.store, self.root, self.team)
        self.agents.people = People(self.store)
        self.anouk = next(a for a in self.team.all()['agents'] if a['id'] == 'anouk')

    def tearDown(self):
        self.temp.cleanup()

    def test_setup_mode_until_switched_on(self):
        self.assertFalse(self.agents.live('anouk'))
        self.assertTrue(self.agents.live('sam'))
        self.assertIn('You are still being set up', self.agents.desk_system_prompt(self.anouk, []))
        (self.root / 'settings.json').write_text(json.dumps({'team_live': ['anouk']}))
        self.assertTrue(self.agents.live('anouk'))
        self.assertNotIn('You are still being set up', self.agents.desk_system_prompt(self.anouk, []))

    def test_goals_playbooks_and_tracker_reach_anouk(self):
        (self.root / 'team/goals').mkdir(parents=True)
        (self.root / 'team/goals/anouk.md').write_text('Outcomes for people.', encoding='utf-8')
        (self.root / 'team/playbooks/anouk').mkdir(parents=True)
        (self.root / 'team/playbooks/anouk/nurture-flow.md').write_text('Step 1: thank them.', encoding='utf-8')
        (self.root / 'team/playbooks/anouk/README.md').write_text('Folder notes.', encoding='utf-8')
        system = self.agents.desk_system_prompt(self.anouk, [])
        self.assertIn('Outcomes for people.', system)
        self.assertIn('Step 1: thank them.', system)
        self.assertNotIn('Folder notes.', system)
        self.agents.people.create(dict(name='Priya', email='priya@x.com', pathway='reply', next_due='2020-01-01'))
        snap = self.agents.desk_snapshot('anouk', [])
        self.assertIn('Priya <priya@x.com> | reply | New reply', snap)
        self.assertIn('(OVERDUE)', snap)
        self.assertIn('Admin (clients, course buyers, Calendly calls) is not connected here.', snap)
        self.assertNotIn('priya@x.com', self.agents.desk_snapshot('sam', []))

    def test_no_outside_tools_in_setup_mode(self):
        calls = []
        self.agents.run = lambda system, prompt, schema, dirs=(), chrome=False, extra_tools=(), full=False, **k: (calls.append((chrome, list(extra_tools), schema)) or ('Noted.', {}))
        self.agents.chat(dict(agent_id='anouk', message='Hi'), [])
        chrome, tools, schema = calls[0]
        self.assertEqual((chrome, tools), (False, []))
        self.assertIn('people', schema['properties'])
        (self.root / 'settings.json').write_text(json.dumps({'team_live': ['anouk']}))
        self.agents.chat(dict(agent_id='anouk', message='Hi'), [])
        self.assertTrue(any('create_draft' in t for t in calls[1][1]))
        self.assertFalse(any('send' in t.lower() for t in calls[1][1]))


if __name__ == '__main__':
    unittest.main()
