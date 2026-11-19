"""Attribution guidance and compatibility checks. All subprocesses are mocked."""
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from studio.agent import Agents, LINKLR, LINK_ATTRIBUTION_RULES
from studio.store import Store
from studio.team import Team


class AttributionGuidanceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store = Store(self.root / 'Data')
        self.agents = Agents(self.store, self.root, Team(self.store))

    def test_creation_access_restored_without_expanding_other_roles(self):
        for role in ('sam', 'laia'):
            self.assertIn('create_tracked_link', LINKLR[role])
            self.assertIn('register_video', LINKLR[role])
        for role in ('max', 'anouk'):
            self.assertNotIn('create_tracked_link', LINKLR[role])
            self.assertNotIn('register_video', LINKLR[role])

    def test_shared_runner_does_not_deny_legitimate_link_creation(self):
        captured = {}
        names = ['mcp__linklr__create_tracked_link', 'mcp__claude_ai_Linklr__create_tracked_link']
        def fake(cmd, **kwargs):
            captured['cmd'] = cmd
            captured['system'] = Path(cmd[cmd.index('--append-system-prompt-file') + 1]).read_text(encoding='utf-8')
            return SimpleNamespace(stdout=json.dumps({'structured_output': {'reply': 'fixture'}}), stderr='', returncode=0)
        with patch('studio.agent.find_claude', return_value='fixture-executable'), patch('studio.agent.subprocess.run', side_effect=fake):
            self.agents.run('', '', extra_tools=names)
        args = captured['cmd']
        allowed = args[args.index('--allowedTools') + 1].split(',')
        denied = args[args.index('--disallowedTools') + 1].split(',')
        self.assertTrue(set(names) <= set(allowed))
        self.assertFalse(any('linklr' in name.lower() for name in denied))
        self.assertIn(LINK_ATTRIBUTION_RULES, captured['system'])

    def test_chat_has_no_request_ledger_or_preparation_action(self):
        with patch.object(self.agents, 'run', return_value=('Fixture reply.', {'reply': 'Fixture reply.'})) as run:
            result = self.agents.chat(dict(agent_id='sam', message='Review the link source'), [])
        self.assertNotIn('link_requests', run.call_args.args[2]['properties'])
        self.assertEqual(result['reply']['text'], 'Fixture reply.')
        with self.store.connect() as c:
            self.assertIsNone(c.execute("SELECT 1 FROM sqlite_master WHERE name='link_requests'").fetchone())

    def test_guidance_distinguishes_supported_keys_from_carousel_gap(self):
        for phrase in ('video_id', 'sequence_name and sequence_position', 'email_key when the installed schema supports it',
                       'channel-wide reuse can be intentional', 'different post label does not make a distinct source',
                       "Never silently reuse another post's URL", 'structured-association gap',
                       'never send an unsupported field', 'workflow guidance, not proof'):
            self.assertIn(phrase, LINK_ATTRIBUTION_RULES)
        prompt = self.agents.desk_system_prompt(next(a for a in self.agents.team.all()['agents'] if a['id'] == 'sam'), [])
        self.assertNotIn('Use an existing link when preview finds one', prompt)
        self.assertIn('Check the intended source before reusing a preview match', prompt)


if __name__ == '__main__':
    unittest.main()
