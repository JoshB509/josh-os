"""Anouk's Gmail drafts and sends, only after Josh approves one exact version (studio/replies.py, gmail_action_guard.py).
No Gmail: a fake runner stands in for the guarded Claude run and records exactly what it was asked to do."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(r'E:\Videos\DaJoshiResolve')
sys.path.insert(0, str(ROOT))
from studio import gmail_access  # noqa: E402
from studio import gmail_action_guard as G  # noqa: E402
from studio import replies as R  # noqa: E402
from studio.store import Store  # noqa: E402
from studio.replies import fields  # noqa: E402

READ = dict(enabled=True, verified_mailbox='josh@example.com', verified_at='2026-11-11T08:00:00+00:00',
            verification_method='owner_confirmation', verified_tools=list(gmail_access.READ_TOOLS))
ACTIONS = dict(enabled=True, mailbox='josh@example.com', actions=['draft', 'send'], switched_on_at='2026-11-12T10:00:00+00:00')


class FakeRun:
    def __init__(self, *answers):
        self.answers, self.calls = list(answers), []

    def __call__(self, system, prompt, schema, **kw):
        self.calls.append(dict(system=system, **kw))
        a = self.answers.pop(0) if self.answers else dict(reply='done', id='gmail-1', thread_id='18cabc1234', view_url='https://mail.google.com/x')
        if isinstance(a, Exception):
            raise a
        return '', a


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = Store(self.root / 'Data')
        self.settings(actions=ACTIONS)
        self.r = R.Replies(self.store, self.root)
        self.p = self.r.add(dict(thread_id='18cabc1234', message_id='18cabc9999', from_name='Dana', from_email='Dana <dana@example.com>',
                                 subject='Module 2', received_at='2026-11-12T09:00:00Z', summary='Asked about module 2.',
                                 proposed_reply='Hi Dana,\n\nYes, start with lesson 3.\n\nJosh'))

    def tearDown(self):
        self.tmp.cleanup()

    def settings(self, actions=None):
        s = dict(gmail_read=READ)
        if actions is not None:
            s['gmail_actions'] = actions
        (self.root / 'settings.json').write_text(json.dumps(s), encoding='utf-8')

    def code(self, action):
        return self.r.binding(self.r.get(self.p['id']), action)['code']


class Binding(Base):
    def test_the_approval_covers_account_recipients_subject_body_thread_and_revision(self):
        b = self.r.binding(self.r.get(self.p['id']), 'send')
        self.assertEqual(b['account'], 'josh@example.com')
        self.assertEqual(b['tool'], gmail_access.SEND_MESSAGE)
        self.assertEqual(b['input'], dict(to=['dana@example.com'], subject='Re: Module 2', body=self.p['proposed_reply'], replyThreadId='18cabc1234'))
        d = self.r.binding(self.r.get(self.p['id']), 'draft')
        self.assertEqual(d['input']['replyToMessageId'], '18cabc9999')
        self.assertNotEqual(b['code'], d['code'])
        self.assertRegex(b['code'], r'^[A-Z2-7]{6}$')

    def test_any_edit_changes_the_code_and_stales_an_old_approval(self):
        old = self.code('send')
        a = self.r.approve(self.p['id'], 'send', old)
        self.r.update(self.p['id'], dict(proposed_reply='Hi Dana, different.'))
        self.assertNotEqual(self.code('send'), old)
        self.assertEqual(self.r.actions(self.p['id'])[0]['state'], 'stale')
        run = FakeRun()
        self.assertEqual(self.r.execute(a['id'], run)['state'], 'approved' if False else 'stale')
        self.assertEqual(run.calls, [])
        for change in (dict(to='someone@else.com'), dict(reply_subject='New subject'), dict(cc='boss@example.com')):
            before = self.code('send')
            self.r.update(self.p['id'], change)
            self.assertNotEqual(self.code('send'), before, change)

    def test_a_wrong_or_old_version_is_refused(self):
        with self.assertRaises(ValueError):
            self.r.approve(self.p['id'], 'send', 'ABCDEF')
        with self.assertRaises(ValueError):
            self.r.approve(self.p['id'], 'send', self.code('draft'))
        with self.assertRaises(ValueError):
            self.r.approve(self.p['id'], 'delete', self.code('send'))

    def test_nothing_happens_until_gmail_actions_are_switched_on(self):
        for actions in (None, dict(ACTIONS, enabled=False), dict(ACTIONS, mailbox='other@example.com'), dict(ACTIONS, actions=['send', 'label']),
                        dict(ACTIONS, switched_on_at='')):
            self.settings(actions=actions)
            with self.assertRaises(ValueError, msg=actions):
                self.r.approve(self.p['id'], 'send', self.code('send'))
        self.settings(actions=dict(ACTIONS, actions=['draft']))
        with self.assertRaises(ValueError):
            self.r.approve(self.p['id'], 'send', self.code('send'))
        self.assertTrue(self.r.approve(self.p['id'], 'draft', self.code('draft')))


class Doing(Base):
    def test_one_guarded_call_with_exactly_the_approved_input(self):
        a = self.r.approve(self.p['id'], 'send', self.code('send'), via='discord', said='send ' + self.code('send'))
        run = FakeRun()
        done = self.r.execute(a['id'], run)
        self.assertEqual(done['state'], 'done')
        call = run.calls[0]
        self.assertEqual(call['extra_tools'], (gmail_access.SEND_MESSAGE,))
        self.assertEqual(call['guard']['kind'], 'gmail')
        self.assertEqual(call['guard']['write'], dict(tool=gmail_access.SEND_MESSAGE, input=a['binding']['input']))
        self.assertEqual(self.r.get(self.p['id'])['state'], 'handled')
        audit = [json.loads(l)['what'] for l in (self.root / 'Data' / 'replies' / 'audit.jsonl').read_text(encoding='utf-8').splitlines()]
        self.assertEqual(audit, ['approved', 'calling', 'done'])

    def test_never_twice(self):
        code = self.code('send')
        a = self.r.approve(self.p['id'], 'send', code)
        run = FakeRun()
        self.r.execute(a['id'], run)
        self.r.execute(a['id'], run)
        self.assertEqual(len(run.calls), 1)
        with self.assertRaises(ValueError):
            self.r.approve(self.p['id'], 'send', code)

    def test_the_same_draft_version_cannot_be_approved_twice_but_a_draft_can_still_be_sent(self):
        dc = self.code('draft')
        a = self.r.approve(self.p['id'], 'draft', dc)
        self.r.execute(a['id'], FakeRun())
        self.assertEqual(self.r.get(self.p['id'])['state'], 'drafted')
        self.assertIn('not done twice', self.r.act_on_text('draft ' + dc, FakeRun()))
        self.assertIn('Sent', self.r.act_on_text('send ' + self.code('send'), FakeRun()))

    def test_an_unclear_result_is_never_retried(self):
        a = self.r.approve(self.p['id'], 'send', self.code('send'))
        run = FakeRun(TimeoutError('slow'))
        self.assertEqual(self.r.execute(a['id'], run)['state'], 'uncertain')
        self.assertEqual(self.r.execute(a['id'], run)['state'], 'uncertain')
        self.assertEqual(len(run.calls), 1)
        with self.assertRaises(ValueError):
            self.r.approve(self.p['id'], 'send', self.code('send'))
        b = self.r.approve(self.p['id'], 'draft', self.code('draft'))
        self.assertEqual(self.r.execute(b['id'], FakeRun(dict(reply='failed', error='403')))['state'], 'uncertain')

    def test_switched_off_between_approval_and_action(self):
        a = self.r.approve(self.p['id'], 'send', self.code('send'))
        self.settings(actions=None)
        run = FakeRun()
        self.assertEqual(self.r.execute(a['id'], run)['state'], 'stale')
        self.assertEqual(run.calls, [])


class ChatCodes(Base):
    def test_only_an_exact_code_counts(self):
        run = FakeRun()
        for text in ('ok send it', 'send', 'yes', 'send it now please', 'send ABC'):
            self.assertIsNone(self.r.act_on_text(text, run), text)
        self.assertIn("couldn't match", self.r.act_on_text('send ZZZZZZ', run))
        self.assertEqual(run.calls, [])
        self.assertIn('Sent your reply to Dana', self.r.act_on_text(f'OK send {self.code("send").lower()}', run))
        self.assertEqual(len(run.calls), 1)

    def test_chat_handles_the_code_itself_without_the_model(self):
        from studio.agent import Agents
        from studio.team import Team
        agents = Agents(self.store, self.root, Team(self.store))
        agents.replies = self.r
        code = self.code('draft')
        with patch.object(Agents, 'run', side_effect=lambda *a, **k: ('', dict(reply='x', id='d-1'))) as run:
            out = agents.chat(dict(agent_id='anouk', message=f'draft {code}', via='discord'), [])
        self.assertIn('Gmail draft', out['reply']['text'])
        self.assertEqual(run.call_args.kwargs['guard']['write']['tool'], gmail_access.CREATE_DRAFT)

    def test_other_team_members_never_act_on_codes(self):
        from studio.agent import Agents
        from studio.team import Team
        agents = Agents(self.store, self.root, Team(self.store))
        agents.replies = self.r
        with patch.object(Agents, 'run', return_value=('ok', dict(reply='ok'))) as run:
            agents.chat(dict(agent_id='sam', message=f'send {self.code("send")}'), [])
        self.assertNotIn('guard', run.call_args.kwargs)
        self.assertEqual(self.r.actions(self.p['id']), [])


class Natural(Base):
    def shown(self, text=None):
        # Anouk's message says who the reply is for. A body alone is never offered (studio/replies.py _identity_shown).
        text = text if text is not None else 'Here is the reply for Dana:\n\n' + self.r.get(self.p['id'])['proposed_reply'] + '\n\nOk?'
        return dict(role='agent', id='m1', at='2026-11-12T07:31:00Z', text=text, offers=self.r.offers_for(text))

    def say(self, text, shown=None, run=None):
        return self.r.act_on_natural(dict(role='josh', id='j1', text=text), shown or self.shown(), run or FakeRun(), via='discord')

    def test_send_that_approves_exactly_the_offer_shown(self):
        run = FakeRun()
        out = self.say('Yes send that to Dana', run=run)
        self.assertIn('Sent your reply to Dana', out)
        self.assertEqual(run.calls[0]['guard']['write']['input']['body'], self.p['proposed_reply'])
        audit = [json.loads(l)['what'] for l in (self.root / 'Data' / 'replies' / 'audit.jsonl').read_text(encoding='utf-8').splitlines()]
        self.assertIn('natural approval', audit)

    def test_any_change_after_it_was_shown_stops_the_send(self):
        for change in (dict(to='other@example.com'), dict(reply_subject='Different subject'), dict(cc='boss@example.com'),
                       dict(proposed_reply='Hi Dana,\n\nReworded.\n\nJosh')):
            shown = self.shown()
            self.r.update(self.p['id'], change)
            run = FakeRun()
            self.assertIn('changed after I showed it', self.say('send it', shown, run), change)
            self.assertEqual(run.calls, [])

    def test_a_message_with_no_saved_offer_never_sends(self):
        run = FakeRun()
        self.assertIn("isn't saved", self.say('send it', dict(self.shown(), offers=[]), run))
        self.assertEqual(run.calls, [])

    def test_conditions_changes_negation_and_quotes_never_send(self):
        run = FakeRun()
        for text in ('send that but change the opening', 'send that tomorrow', "don't send that", 'do not send it yet',
                     'Not sure what you want for her', '> send it', '"send it" is what she wrote', 'can you draft one for Dana?',
                     'what would you send?', 'sending you thoughts later', 'Send that to Dana but fix the typo', 'send that once she replies'):
            self.assertIsNone(self.say(text, run=run), text)
        for text in ('Send that.\nBut change the greeting first', 'Send that to Dana.\nWait until Monday', 'send it\n\nactually hold off'):
            self.assertIn('nothing was sent', self.say(text, run=run), text)
        self.assertEqual(run.calls, [])

    def test_a_condition_about_someone_else_does_not_hold_it(self):
        self.r.add(dict(thread_id='18cabc7777', message_id='18cabc8888', from_name='Daniel', from_email='daniel@example.com', subject='Hi',
                        proposed_reply='Hi Daniel,\n\nThanks.\n\nJosh'))
        out = self.say('Yes send that to Dana\n\nFor Daniel, suggest a message here do not send yet.')
        self.assertIn('Sent your reply to Dana', out)

    def test_only_josh_answering_anouk_counts(self):
        run = FakeRun()
        self.assertIsNone(self.r.act_on_natural(dict(role='agent', text='send it'), self.shown(), run))
        self.assertIsNone(self.r.act_on_natural(dict(role='josh', text='send it'), dict(self.shown(), role='josh'), run))
        self.assertEqual(run.calls, [])

    def test_two_offers_need_a_name(self):
        other = self.r.add(dict(thread_id='18cabc7777', message_id='18cabc8888', from_name='Lee', from_email='lee@example.com', subject='Hi',
                                proposed_reply='Hi Lee,\n\nThanks.\n\nJosh'))
        shown = self.shown('For Dana:\n' + self.p['proposed_reply'] + '\n\nFor Lee:\n' + other['proposed_reply'])
        self.assertIn("couldn't tell", self.say('send that', shown))
        self.assertIn('Lee', self.say('Send that to Lee.', shown))

    def test_a_reply_written_in_chat_becomes_a_proposal_and_can_be_approved(self):
        b = self.r.create_from_chat(dict(thread_id='18cdef1234', message_id='18cdef5678', to='daniel@example.com', from_name='Daniel',
                                         subject='Lesson 1.3', body='Hi Daniel,\n\nGlad it helps.\n\nJosh'))
        self.assertEqual((b['state'], fields(b)['subject'], fields(b)['to']), ('proposed', 'Re: Lesson 1.3', ['daniel@example.com']))
        with self.assertRaises(ValueError):
            self.r.create_from_chat(dict(thread_id='18cdef1234', message_id='18cdef5678', to='daniel@example.com', body='again'))
        run = FakeRun()
        out = self.say('Send that to Daniel.\n\nThe draft you typed here is the one I want you to send',
                       self.shown('Here is the reply to Daniel:\nHi Daniel,\n\nGlad it helps.\n\nJosh'), run)
        self.assertIn('Sent your reply to Daniel', out)
        self.assertEqual(run.calls[0]['guard']['write']['input']['replyThreadId'], '18cdef1234')


class Guard(unittest.TestCase):
    POLICY = dict(kind='gmail', write=dict(tool=gmail_access.SEND_MESSAGE, input=dict(to=['a@b.com'], subject='Re: x', body='hi')))

    def test_exact_call_only(self):
        ok = dict(tool_name=gmail_access.SEND_MESSAGE, tool_input=dict(to=['a@b.com'], subject='Re: x', body='hi'))
        self.assertTrue(G.decision(ok, self.POLICY)[0])
        self.assertFalse(G.decision(ok, self.POLICY, used=True)[0])
        for bad in (dict(ok, tool_input=dict(ok['tool_input'], bcc=['x@y.com'])), dict(ok, tool_input=dict(ok['tool_input'], body='hi!')),
                    dict(ok, tool_input=dict(ok['tool_input'], to=['a@b.com', 'c@d.com'])), dict(ok, tool_name=gmail_access.CREATE_DRAFT),
                    dict(tool_name='mcp__claude_ai_Gmail__reply', tool_input=dict(messageId='1', body='hi')),
                    dict(tool_name='Bash', tool_input=dict(command='curl')), dict(tool_name='Read', tool_input=dict(file_path='x'))):
            self.assertFalse(G.decision(bad, self.POLICY)[0], bad)
        self.assertTrue(G.decision(dict(tool_name='ToolSearch', tool_input={}), self.POLICY)[0])
        self.assertFalse(G.decision(ok, None)[0])

    def test_runs_disallow_every_other_gmail_write(self):
        from studio.agent import Agents
        from studio.team import Team
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        a = Agents(Store(Path(tmp.name) / 'Data'), Path(tmp.name), Team(Store(Path(tmp.name) / 'Data')))
        seen = {}

        def fake_run(cmd, **kw):
            seen['cmd'] = cmd
            return SimpleNamespace(stdout=json.dumps(dict(structured_output=dict(reply='ok'))), stderr='', returncode=0)
        with patch('studio.agent.find_claude', return_value='claude.exe'), patch('studio.agent.subprocess.run', side_effect=fake_run):
            a.run('s', 'p', R.ACTION_SCHEMA, extra_tools=(gmail_access.SEND_MESSAGE,), full=True, guard=self.POLICY)
            blocked = seen['cmd'][seen['cmd'].index('--disallowedTools') + 1].split(',')
            self.assertNotIn(gmail_access.SEND_MESSAGE, blocked)
            self.assertIn(gmail_access.CREATE_DRAFT, blocked)
            self.assertIn('gmail_action_guard.py', ' '.join(seen['cmd']))
            a.run('s', 'p', R.MAIL_SCHEMA, extra_tools=gmail_access.READ_TOOLS, full=True, read_only=True)
            blocked = seen['cmd'][seen['cmd'].index('--disallowedTools') + 1].split(',')
            self.assertTrue(set(gmail_access.ALL_WRITES) <= set(blocked))


if __name__ == '__main__':
    unittest.main()
