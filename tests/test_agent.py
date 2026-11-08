import json, os, stat, subprocess, sys, tempfile, unittest, uuid
from unittest import mock
from pathlib import Path
from studio.store import Store
from studio.team import Team
from studio.agent import Agents, AgentError, find_claude

FAKE = '''#!{python}
import json, sys
args = sys.argv[1:]
prompt = sys.stdin.read()
system = open(args[args.index('--append-system-prompt-file') + 1], encoding='utf-8').read()
open({log!r}, 'w', encoding='utf-8').write(json.dumps({{'args': args, 'prompt': prompt, 'system': system}}))
mode = {mode!r}
if mode == 'error':
    print(json.dumps({{'type': 'result', 'is_error': True, 'result': 'Not logged in'}}))
elif mode == 'text':
    print(json.dumps({{'type': 'result', 'is_error': False, 'result': 'Sure. {{"reply": "Tightened.", "replacement": "Short line."}}'}}))
else:
    print(json.dumps({{'type': 'result', 'is_error': False, 'structured_output': {{'reply': 'Here is a tighter hook.', 'replacement': 'New hook line.'}}}}))
'''


class AgentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / '.claude/skills/script-touch-up').mkdir(parents=True)
        (self.root / '.claude/skills/script-touch-up/SKILL.md').write_text('# Script touch-up\nEvery signpost is a named Ed move.', encoding='utf-8')
        self.store = Store(self.root / 'Data')
        self.team = Team(self.store)
        self.agents = Agents(self.store, self.root, self.team)
        p = self.store.create('Promotion conversations')
        self.p = self.store.save(p['id'], {'script': '# Hook\nOld hook line.\n\n# Section 1\nBody.'}, 1)
        self.log = self.root / 'call.json'
        if os.name == 'nt':
            # Windows can't start the fake's #! script directly, so run it with Python.
            # The fake keeps the plain name "claude", so the agent still takes its normal path with --json-schema.
            run = subprocess.run
            def run_fake(cmd, *a, **k):
                if cmd and str(cmd[0]).startswith(str(self.root)):
                    cmd = [sys.executable] + list(cmd)
                return run(cmd, *a, **k)
            patcher = mock.patch('studio.agent.subprocess.run', run_fake)
            patcher.start()
            self.addCleanup(patcher.stop)

    def tearDown(self):
        self.temp.cleanup()

    def fake(self, mode='json', name='claude'):
        f = self.root / name
        f.write_text(FAKE.format(python=sys.executable, log=str(self.log), mode=mode), encoding='utf-8')
        f.chmod(f.stat().st_mode | stat.S_IEXEC)
        (self.root / 'settings.json').write_text(json.dumps({'claude': str(f)}))
        return f

    def ask(self, **extra):
        start = self.p['script'].index('Old hook line.')
        sel = {'start': start, 'end': start + len('Old hook line.'), 'text': 'Old hook line.'}
        return self.agents.ask(dict(dict(agent_id='sam', project_id=self.p['id'], message='Make this punchier', selection=sel), **extra))

    def test_sam_gets_his_skill_lessons_script_and_selection(self):
        self.fake()
        self.team.learn({'agent_id': 'sam', 'lesson': 'Number-led hooks get more saves'})
        out = self.ask()
        self.assertEqual(out['reply']['replacement'], 'New hook line.')
        call = json.loads(self.log.read_text(encoding='utf-8'))
        self.assertIn('You are Sam', call['system'])
        self.assertIn('Every signpost is a named Ed move.', call['system'])
        self.assertIn('Number-led hooks get more saves', call['system'])
        self.assertIn('Old hook line.', call['prompt'])
        self.assertIn('Josh has selected this passage', call['prompt'])
        self.assertIn('--json-schema', call['args'])
        self.assertEqual(call['args'][call['args'].index('--tools') + 1], 'Read,Grep,Glob')
        thread = self.agents.thread(self.p['id'], 'sam')
        self.assertEqual([m['role'] for m in thread], ['josh', 'agent'])
        self.ask(message='And the next line?')
        self.assertIn('Earlier in this conversation', json.loads(self.log.read_text(encoding='utf-8'))['prompt'])

    def test_josh_can_send_images_in_a_team_chat(self):
        import base64, io
        from PIL import Image
        self.fake()
        buf = io.BytesIO()
        Image.new('RGB', (8, 8), 'red').save(buf, 'PNG')
        aid = self.store.import_image('shot.png', buf.getvalue())['asset']
        out = self.agents.chat(dict(agent_id='sam', message='', images=[aid]), [])
        call = json.loads(self.log.read_text(encoding='utf-8'))
        path = str(self.store.asset(aid)[0])
        self.assertIn('Josh attached these images', call['prompt'])
        self.assertIn(path, call['prompt'])
        self.assertIn(str(self.store.root / 'assets'), call['args'])
        self.assertEqual(out['messages'][-2]['images'], [aid])
        self.agents.chat(dict(agent_id='sam', message='And the second one?'), [])
        self.assertIn('[attached images, which you can open with Read: ' + path, json.loads(self.log.read_text(encoding='utf-8'))['prompt'])
        with self.assertRaises(ValueError):
            self.agents.chat(dict(agent_id='sam', message='Look', images=['0' * 64]), [])
        with self.assertRaises(ValueError):
            self.agents.chat(dict(agent_id='sam', message='', images=[]), [])

    def test_anouk_reads_her_own_section_of_the_real_personalities_file(self):
        from studio.agent import personality
        repo = Path(__file__).resolve().parent.parent
        text = personality(repo, {'id': 'anouk', 'name': 'Anouk'})
        self.assertIn("It's to get them to achieve something valuable.", text)
        self.assertIn('Shared Team Communication Principles', text)
        self.assertIn('The Overall Experience', text)
        self.assertNotIn('## 1. Sam', text)

    def test_saskia_gets_her_skills_and_training_files(self):
        self.fake()
        for name, text in [('lesson-slides', 'Beat-by-beat slides.'), ('careeros-playbook', 'Playbooks in house style.')]:
            (self.root / '.claude/skills' / name).mkdir(parents=True, exist_ok=True)
            (self.root / '.claude/skills' / name / 'SKILL.md').write_text(text, encoding='utf-8')
        training = self.root / 'training'
        training.mkdir()
        (training / '01_house-style.md').write_text('No em dashes.', encoding='utf-8')
        (training / 'saskia_courses.md').write_text('Course map.', encoding='utf-8')
        settings = json.loads((self.root / 'settings.json').read_text())
        (self.root / 'settings.json').write_text(json.dumps(dict(settings, training_dir=str(training))))
        self.agents.ask(dict(agent_id='saskia', project_id=self.p['id'], message='Turn this into a lesson outline'))
        system = json.loads(self.log.read_text(encoding='utf-8'))['system']
        self.assertIn('You are Saskia', system)
        self.assertIn('Beat-by-beat slides.', system)
        self.assertIn('Playbooks in house style.', system)
        self.assertIn('No em dashes.', system)
        text, dirs = self.agents.training('saskia')
        self.assertIn('saskia_courses.md', text)
        self.assertIn(str(training), dirs)

    def test_personality_file_gives_each_person_their_section_and_the_shared_rules(self):
        self.fake()
        (self.root / 'team').mkdir()
        (self.root / 'team/personalities.md').write_text(
            '# Profiles\n\n## Purpose\nBe colleagues.\n\n## 1. Sam\nDry humour.\n\n## 3. Laia\nPlayful.\n\n'
            '## 5. Shared Team Communication Principles\nNo "Great question!".\n\n## 6. Team Dynamics\nDisagree usefully.\n', encoding='utf-8')
        self.ask()
        system = json.loads(self.log.read_text(encoding='utf-8'))['system']
        for text in ['Be colleagues.', 'Dry humour.', 'No "Great question!".', 'Disagree usefully.']:
            self.assertIn(text, system)
        self.assertNotIn('Playful.', system)
        self.assertNotIn('then ask for a yes', system)

    def test_stale_selection_and_bad_requests_are_refused(self):
        self.fake()
        with self.assertRaises(ValueError):
            self.ask(selection={'start': 0, 'end': 5, 'text': 'nope!'})
        with self.assertRaises(ValueError):
            self.ask(message=' ')
        with self.assertRaises(KeyError):
            self.ask(agent_id='nobody')
        self.assertEqual(self.agents.thread(self.p['id'], 'sam'), [])

    def test_errors_and_plain_text_answers(self):
        self.fake('error')
        with self.assertRaises(AgentError):
            self.ask()
        self.fake('text')
        self.assertEqual(self.ask()['reply']['replacement'], 'Short line.')

    def test_missing_claude_is_a_clear_error(self):
        (self.root / 'settings.json').write_text(json.dumps({'claude': str(self.root / 'missing')}))
        # Point PATH and every place find_claude looks at the empty test folder, so a real Claude on this PC isn't found.
        empty = {k: str(self.root) for k in ('PATH', 'USERPROFILE', 'HOME', 'APPDATA', 'LOCALAPPDATA')}
        with mock.patch.dict(os.environ, empty):
            self.assertFalse(self.agents.status()['available'])
            with self.assertRaises(AgentError):
                self.ask()


if __name__ == '__main__':
    unittest.main()
