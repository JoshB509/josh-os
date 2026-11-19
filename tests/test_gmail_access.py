import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from studio import gmail_access
from studio.agent import Agents, CAREEROS_READ
from studio.read_tools_guard import decision
from studio.store import Store
from studio.team import Team


class GmailAccessTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store = Store(self.root / 'Data')
        self.agents = Agents(self.store, self.root, Team(self.store))

    def config(self, **changes):
        value = dict(enabled=True, verified_mailbox='owner@example.com', verified_at='2026-11-11T00:00:00Z', verification_method='connector_account_ui',
                     verified_tools=list(gmail_access.READ_TOOLS))
        value.update(changes)
        (self.root / 'settings.json').write_text(json.dumps({'gmail_read': value}), encoding='utf-8')

    def agent_tools(self, agent_id='anouk', live=False):
        with patch.object(self.agents, 'live', return_value=live), patch.object(self.agents, 'run', return_value=('ok', {'reply': 'ok'})) as run:
            self.agents.chat({'agent_id': agent_id, 'message': 'Review incoming replies'}, [])
            return run.call_args.kwargs

    def test_default_is_closed_and_keeps_careeros(self):
        args = self.agent_tools()
        self.assertEqual(set(args['extra_tools']), set(CAREEROS_READ))
        self.assertTrue(args['read_only'])
        self.assertFalse(self.agents.live('anouk'))

    def test_verified_gmail_is_independent_of_general_gate(self):
        self.config()
        args = self.agent_tools()
        self.assertEqual(set(args['extra_tools']), set(CAREEROS_READ + gmail_access.READ_TOOLS))
        self.assertFalse(self.agents.live('anouk'))
        self.assertFalse(any('Calendly' in x or 'Kit' in x or 'linklr' in x for x in args['extra_tools']))

    def test_other_roles_do_not_receive_independent_gmail(self):
        self.config()
        for role in ('sam', 'laia', 'saskia', 'tariq', 'max'):
            self.assertEqual(gmail_access.tools_for(role, self.root), ())

    def test_config_rejects_missing_identity_and_unverified_tools(self):
        for values in ({'verification_method':''}, {'enabled':False}, {'enabled':'true'}, {'verified_mailbox':''}, {'verified_at':''},
                       {'verified_mailbox':'not-mail'}, {'verified_tools':[]}, {'verified_tools':['mcp__claude_ai_Gmail__send_email']},
                       {'verified_tools':['mcp__claude_ai_Gmail__*']}, {'verified_tools':'*'}):
            self.config(**values)
            self.assertEqual(gmail_access.tools_for('anouk', self.root), (), values)

    def test_malformed_config_is_closed(self):
        for content in ('{', 'null', '[]', '{"gmail_read":null}'):
            (self.root / 'settings.json').write_text(content, encoding='utf-8')
            self.assertEqual(gmail_access.tools_for('anouk', self.root), ())

    def test_no_gmail_mutations_even_with_general_gate(self):
        self.config()
        names = self.agent_tools(live=True)['extra_tools']
        gmail = [x for x in names if 'Gmail' in x]
        self.assertEqual(set(gmail), set(gmail_access.READ_TOOLS))
        self.assertFalse(any('draft' in x or 'send' in x for x in gmail))

    def test_prompt_is_honest_when_pending(self):
        text = '\n'.join(gmail_access.prompt_rules('anouk', self.root))
        self.assertIn('pending verification', text)
        self.assertIn('Do not claim live inbox tracking', text)

    def test_prompt_binds_verified_account_and_untrusted_mail(self):
        self.config()
        text = '\n'.join(gmail_access.prompt_rules('anouk', self.root))
        self.assertIn('owner@example.com', text)
        self.assertIn('different account', text)
        self.assertIn('untrusted data', text)
        self.assertIn('no profile or account-selection tool', text)
        self.assertIn('Do not create/update/delete Gmail drafts', text)

    def test_guard_exact_name_only(self):
        allowed = list(gmail_access.READ_TOOLS)
        for name in allowed:
            self.assertEqual(decision({'tool_name':name}, allowed)['hookSpecificOutput']['permissionDecision'], 'allow')
        for name in ('Bash', 'Write', 'mcp__claude_ai_Gmail__send_email', 'mcp__claude_ai_Gmail__create_draft',
                     'mcp__claude_ai_Gmail__delete_message', 'mcp__claude_ai_Gmail__new_future_tool', ''):
            self.assertEqual(decision({'tool_name':name}, allowed)['hookSpecificOutput']['permissionDecision'], 'deny')

    def test_guard_malformed_is_closed(self):
        for event, allowed in ((None, []), ({}, []), ({'tool_name':'Read'}, None), ({'tool_name':'Read'}, 'Read')):
            self.assertEqual(decision(event, allowed)['hookSpecificOutput']['permissionDecision'], 'deny')

    def test_run_installs_enforced_read_guard(self):
        captured = {}
        def fake_run(cmd, **kwargs):
            captured['cmd'] = cmd
            # Read temporary policy while the runtime still owns it.
            folder = Path(cmd[cmd.index('--append-system-prompt-file') + 1]).parent
            captured['policy'] = json.loads((folder / 'read-tools.json').read_text(encoding='utf-8'))
            return SimpleNamespace(stdout=json.dumps({'structured_output':{'reply':'ok','replacement':None}}), stderr='')
        with patch('studio.agent.find_claude', return_value='fake-claude.exe'), patch('studio.agent.subprocess.run', side_effect=fake_run):
            self.agents.run('system', 'prompt', tools='Read,Write,Bash', extra_tools=list(CAREEROS_READ)+list(gmail_access.READ_TOOLS)+['mcp__claude_ai_Gmail__send_email'], read_only=True)
        cmd = captured['cmd']
        self.assertEqual(cmd[cmd.index('--permission-mode')+1], 'dontAsk')
        settings = json.loads(cmd[cmd.index('--settings')+1])
        self.assertEqual(settings['hooks']['PreToolUse'][0]['matcher'], '*')
        self.assertEqual(len(settings['allowedMcpServers']), 2)
        self.assertIn({'serverUrl':'https://linklr.app/*'}, settings['deniedMcpServers'])
        self.assertIn('read_tools_guard.py', settings['hooks']['PreToolUse'][0]['hooks'][0]['command'])
        self.assertNotIn('mcp__claude_ai_Gmail__send_email', captured['policy'])
        self.assertIn('ToolSearch', captured['policy'])
        self.assertIn('StructuredOutput', captured['policy'])
        self.assertNotIn(chr(92), settings['hooks']['PreToolUse'][0]['hooks'][0]['command'])
        self.assertNotIn('Write', captured['policy'])
        self.assertNotIn('Bash', captured['policy'])
        self.assertEqual(cmd[cmd.index('--tools')+1], 'Read,Grep,Glob,ToolSearch')
        self.assertTrue(set(CAREEROS_READ).issubset(captured['policy']))


if __name__ == '__main__':
    unittest.main()
