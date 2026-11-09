import io, json, tempfile, unittest
from pathlib import Path
from types import SimpleNamespace
from PIL import Image
from studio.store import Store
from studio.team import Team
from studio.agent import Agents
from studio.discord_bridge import Bridge, allowed, split, clean, load_config, saved_line

CONFIG = dict(owners=['111'], bots={'sam': dict(token='x', channels=['sam']), 'tariq': dict(token='y', channels=['edits', '999'])})


def png():
    b = io.BytesIO(); Image.new('RGB', (64, 36), 'red').save(b, format='PNG'); return b.getvalue()


class RulesTests(unittest.TestCase):
    def test_only_josh_and_only_in_the_right_places(self):
        self.assertTrue(allowed(CONFIG, 'sam', 111, False, 'sam', 1, False))
        self.assertTrue(allowed(CONFIG, 'sam', '111', True, None, 1, False))
        self.assertTrue(allowed(CONFIG, 'sam', 111, False, 'production', 1, True))
        self.assertTrue(allowed(CONFIG, 'tariq', 111, False, 'whatever', 999, False))
        self.assertFalse(allowed(CONFIG, 'sam', 111, False, 'production', 1, False))
        self.assertFalse(allowed(CONFIG, 'tariq', 111, False, 'sam', 1, False))
        self.assertFalse(allowed(CONFIG, 'sam', 222, True, None, 1, True))

    def test_split_clean_and_config(self):
        parts = split(('word ' * 300 + '\n\n') * 3)
        self.assertTrue(all(len(p) <= 1900 for p in parts))
        self.assertEqual(len(' '.join(parts).split()), 900)
        self.assertEqual(clean('<@42> draft three carousels', 42), 'draft three carousels')
        with tempfile.TemporaryDirectory() as t:
            self.assertEqual(load_config(t), {})
            (Path(t) / 'discord.json').write_text(json.dumps(dict(bots={'sam': {'token': 'x'}})))
            self.assertEqual(load_config(t), {}, 'no owner means no bridge, so nobody else can drive the team')
            (Path(t) / 'discord.json').write_text(json.dumps(dict(owners=['1'], bots={'sam': {'token': 'x'}, 'tariq': {}})))
            self.assertEqual(list(load_config(t)['bots']), ['sam'])
        self.assertIn("Couldn't save 1", saved_line([dict(title='A', action='added'), dict(action='failed', error='bad date')]))


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.store = Store(self.root / 'Data')
        self.calls = []
        out = self.root / 'slide.png'
        out.write_bytes(png())

        def chat(data, items, tracker, inspo):
            self.calls.append(data)
            return dict(reply=dict(text='Done. ' + 'x' * 2000, saved=[dict(title='5 promotion myths', action='updated')], files=[str(out)]))
        server = SimpleNamespace(store=self.store, agents=SimpleNamespace(chat=chat),
                                 carousels=SimpleNamespace(all=lambda: dict(items=[])), inspo=None)
        self.bridge = Bridge(server)
        self.out = out

    def tearDown(self):
        self.temp.cleanup()

    def test_attachments_go_to_the_chat_and_replies_come_back_split_with_files(self):
        messages, files = self.bridge.handle('sam', 'Use this for the carousel', [('shot.png', 'image/png', png()), ('brief.pdf', 'application/pdf', b'%PDF-1.4 x')])
        data = self.calls[0]
        self.assertEqual((data['agent_id'], data['via'], len(data['images']), len(data['files'])), ('sam', 'discord', 1, 1))
        self.assertTrue(self.store.asset(data['images'][0])[0].is_file())
        self.assertTrue(Path(data['files'][0]).is_relative_to(self.root / 'Data' / 'discord' / 'inbox' / 'sam'))
        self.assertGreater(len(messages), 1)
        self.assertIn('Saved in Josh OS: 5 promotion myths', messages[-1])
        self.assertEqual(files, [str(self.out)])

    def test_errors_reach_josh(self):
        self.bridge.server.agents.chat = lambda *a: (_ for _ in ()).throw(ValueError('Sam is still working on your last message.'))
        messages, files = self.bridge.handle('sam', 'hi')
        self.assertEqual((messages, files), (["I couldn't do that: Sam is still working on your last message."], []))


class ChatFileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.store = Store(self.root / 'Data')
        self.agents = Agents(self.store, self.root, Team(self.store))
        self.inbox = self.root / 'Data' / 'discord' / 'inbox' / 'tariq'
        self.inbox.mkdir(parents=True)
        self.calls = []

    def tearDown(self):
        self.temp.cleanup()

    def run_chat(self, data, answer):
        def run(system, prompt, schema, dirs=(), **k):
            self.calls.append(dict(prompt=prompt, schema=schema, dirs=list(dirs)))
            return 'Here you go.', answer
        self.agents.run = run
        return self.agents.chat(dict(dict(agent_id='tariq', message='Look at this'), **data), [])

    def test_inbox_files_reach_the_agent_and_safe_files_go_back(self):
        f = self.inbox / 'notes.txt'
        f.write_text('cut the intro', encoding='utf-8')
        mine = self.root / 'deck.html'
        mine.write_text('<html></html>', encoding='utf-8')
        r = self.run_chat(dict(files=[str(f)], via='discord'), dict(send_files=[str(mine), '/etc/passwd', str(self.root / 'missing.txt')]))
        call = self.calls[0]
        self.assertIn(str(f), call['prompt'])
        self.assertIn(str(self.inbox), call['dirs'])
        self.assertIn('send_files', call['schema']['properties'])
        self.assertEqual(r['reply']['files'], [str(mine.resolve())])
        self.assertEqual(r['messages'][-2]['via'], 'discord')

    def test_files_outside_the_inbox_are_refused_and_app_chat_has_no_file_sending(self):
        with self.assertRaises(ValueError):
            self.run_chat(dict(files=[str(self.root / 'deck.html')]), {})
        r = self.run_chat({}, dict(send_files=[str(self.root)]))
        self.assertNotIn('send_files', self.calls[-1]['schema']['properties'])
        self.assertNotIn('files', r['reply'])


if __name__ == '__main__':
    unittest.main()
