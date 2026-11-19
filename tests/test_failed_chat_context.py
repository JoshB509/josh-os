"""Failed Anouk reads remain context, never approval. All runs are mocked; no external calls."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from studio.agent import Agents, DESK
from studio.store import Store
from studio.team import Team
from studio.approval_lineage import Lineage

class FailedReadContext(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store = Store(self.root / 'Data')
        self.agents = Agents(self.store, self.root, Team(self.store))
        self.where = dict(author_id='42', channel_id='111', message_id='900', is_dm=True)

    def fail(self, **extra):
        data = dict(agent_id='anouk', message='Show Danielle email and the suggested response', via='discord', discord=self.where)
        data.update(extra)
        with patch.object(self.agents, 'run', side_effect=AttributeError("WindowsPath object has no attribute get")) as run:
            with self.assertRaises(AttributeError):
                self.agents.chat(data, [])
            self.assertEqual(run.call_count, 1)
            self.assertTrue(run.call_args.kwargs['read_only'])
        return self.agents.thread(DESK, 'anouk')

    def test_failed_turn_and_error_are_preserved_without_an_offer(self):
        messages = self.fail()
        self.assertEqual(len(messages), 2)
        self.assertIn('Danielle', messages[0]['text'])
        self.assertIn('WindowsPath', messages[1]['text'])
        for m in messages:
            self.assertTrue(m['failed_read'])
            self.assertEqual(m['conversation'], 'discord:dm:42')
            self.assertNotIn('offers', m)
        self.assertFalse(self.agents.locks[('anouk', DESK)].locked())

    def test_retry_after_restart_has_failed_topic_in_shared_history(self):
        self.agents._append(DESK, 'anouk', dict(id='old', role='agent', text='Previously discussing dgdublin', at='2026-11-12T00:00:00Z'))
        self.fail()
        restarted = Agents(self.store, self.root, Team(self.store))
        with patch.object(restarted, 'run', return_value=('Here is Danielle', {})) as run:
            restarted.chat(dict(agent_id='anouk', message='Ok try again'), [])
        prompt = run.call_args.args[1]
        self.assertIn('Show Danielle email and the suggested response', prompt)
        self.assertIn('Retain this topic for follow-up', prompt)
        self.assertIn('never approval to draft, send, or retry a write', prompt)
        self.assertGreater(prompt.index('Show Danielle'), prompt.index('Previously discussing'))
        self.assertTrue(run.call_args.kwargs['read_only'])

    def test_failed_desk_read_blocks_an_older_approval(self):
        lineage = Lineage(self.store)
        lineage.record('anouk', 'joshos:desk', ['old'], [], kind='chat', state='sent')
        self.agents._append(DESK, 'anouk', dict(id='old', role='agent', text='Old offer', at='2026-11-12T00:00:00Z'))
        messages = self.fail(via='', discord=None)
        found, why = lineage.resolve('anouk', 'joshos:desk', latest_desk_id=messages[-1]['id'])
        self.assertIsNone(found)
        self.assertEqual(why, 'not_latest')

    def test_other_agent_failures_not_changed_by_this_patch(self):
        with patch.object(self.agents, 'run', side_effect=RuntimeError('test')):
            with self.assertRaises(RuntimeError):
                self.agents.chat(dict(agent_id='laia', message='Read only please'), [])
        self.assertEqual(self.agents.thread(DESK, 'laia'), [])

if __name__ == '__main__':
    unittest.main()
