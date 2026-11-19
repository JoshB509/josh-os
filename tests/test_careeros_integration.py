import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from studio.agent import Agents, CAREEROS, CAREEROS_READ
from studio.store import Store
from studio.team import Team


class CareerOSIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = Store(self.root / 'Data')
        self.team = Team(self.store)
        self.agents = Agents(self.store, self.root, self.team)

    def tools_for(self, agent_id, live):
        with patch.object(self.agents, 'live', return_value=live), patch.object(self.agents, 'run', return_value=('ok', {'reply': 'ok'})) as run:
            self.agents.chat({'agent_id': agent_id, 'message': 'Check learner progress'}, [])
            return run.call_args.kwargs['extra_tools']

    def test_exact_read_only_allowlist(self):
        self.assertEqual(set(CAREEROS), {'anouk'})
        self.assertEqual(set(CAREEROS_READ), {'mcp__careeros__' + name for name in (
            'find_learners', 'get_learner', 'get_learner_admin_history', 'get_learner_progress',
            'get_learner_timeline', 'list_questions', 'list_reviews')})

    def test_setup_gate_allows_only_explicit_careeros_reads(self):
        self.assertFalse(self.agents.live('anouk'))
        self.assertEqual(set(self.tools_for('anouk', False)), set(CAREEROS_READ))

    def test_setup_anouk_prompt_explains_read_only_exception(self):
        anouk = next(a for a in self.team.all()['agents'] if a['id'] == 'anouk')
        prompt = self.agents.desk_system_prompt(anouk, [])
        self.assertIn('CareerOS learner support is available', prompt)
        self.assertIn('no general access to Calendly, Kit, Linklr or Chrome', prompt)
        self.assertIn('Never change learners, access, questions, reviews or messages', prompt)

    def test_live_anouk_gets_only_named_careeros_reads(self):
        allowed = self.tools_for('anouk', True)
        self.assertEqual({t for t in allowed if t.startswith('mcp__careeros__')}, set(CAREEROS_READ))

    def test_other_roles_do_not_get_careeros(self):
        for agent_id in ('sam', 'laia', 'max', 'saskia', 'tariq'):
            self.assertFalse(any(t.startswith('mcp__careeros__') for t in self.tools_for(agent_id, True)))


if __name__ == '__main__':
    unittest.main()
