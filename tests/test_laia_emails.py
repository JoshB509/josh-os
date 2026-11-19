"""Laia's email records (studio/agent.py apply_emails, email_answer, email_text; studio/emails.py kit_send_proof).
No Kit, no Claude, no email: a temporary store with a made-up Kit snapshot."""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from studio import agent as A
from studio.agent import Agents
from studio.emails import Emails, kit_send_proof
from studio.store import Store


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.store = Store(root / 'Data')
        self.mails = Emails(self.store, root)
        self.e = self.mails.create(dict(id='4e35953b-cb05-5782-a769-3b19990118ff', changes=dict(
            subject="You're explaining your work to the wrong people", body='Hey,\n\nFirst version.', planned_date='2026-11-13', send_time='17:00')))
        with self.store.connect() as c:
            c.execute('CREATE TABLE IF NOT EXISTS business_counters(key TEXT PRIMARY KEY,body TEXT NOT NULL)')
            c.execute('INSERT INTO business_counters VALUES(?,?)', ('kit:2026-11-13', json.dumps(dict(complete=True, taken_at='2026-11-13T02:45:00+00:00', items={
                '26927691': dict(send_at='2026-11-10T14:21:04Z', status='completed', recipients=4163)}))))
        self.a = Agents.__new__(Agents)
        self.enterContext(mock.patch('studio.kit_sent.read_broadcast', side_effect=ValueError(
            'Not marked sent. This email is not linked to a Kit broadcast and no independent Kit read is available.')))

    def tearDown(self):
        self.tmp.cleanup()

    def apply(self, *ups):
        return self.a.apply_emails(self.mails, list(ups))

    def status(self):
        return self.mails.get(self.e['id'])['status']


class Sent(Base):
    def link(self, bid, status='scheduled'):
        """Josh OS ties the email to its Kit broadcast (Kit sync), which a chat answer can never do."""
        self.mails._apply(self.e['id'], lambda cur: cur.update(kit=dict(id=bid, url=f'https://app.kit.com/campaigns/{bid}/draft', status=status)))

    def test_an_unlinked_email_is_never_marked_sent_even_with_a_real_completed_id(self):
        out = self.apply(dict(id=self.e['id'], sent=True))
        self.assertIn('not linked to a Kit broadcast', out[0]['error'])
        out = self.apply(dict(id=self.e['id'], sent=True, kit_broadcast_id='26927691'))   # Tuesday's real, completed send
        self.assertEqual(out[0]['action'], 'failed')
        out = self.apply(dict(id=self.e['id'], sent=True, kit_broadcast_id='26927691', results=dict(kit_broadcast_id='26927691')))
        self.assertEqual(out[0]['action'], 'failed')                                     # results written in chat don't link it
        self.assertEqual(self.status(), 'Draft')

    def test_a_wrong_valid_id_is_refused_when_the_email_is_linked_elsewhere(self):
        self.link(27000001)
        out = self.apply(dict(id=self.e['id'], sent=True, kit_broadcast_id='26927691'))
        self.assertIn('not the one linked', out[0]['error'])
        out = self.apply(dict(id=self.e['id'], sent=True))                              # its own broadcast hasn't gone out
        self.assertIn('Not marked sent', out[0]['error'])
        self.assertEqual(self.status(), 'Draft')

    def test_sent_only_from_its_own_completed_broadcast(self):
        self.link(26927691)
        out = self.apply(dict(id=self.e['id'], sent=True))
        self.assertEqual((out[0]['action'], out[0]['status']), ('updated', 'Sent'))
        e = self.mails.get(self.e['id'])
        self.assertEqual(e['results']['kit_broadcast_id'], '26927691')
        self.assertIn('Sent per linked Kit broadcast 26927691', e['notes'])

    def test_a_draft_josh_os_made_for_it_also_links_it(self):
        self.a.kit_drafts = SimpleNamespace(made_by_us=lambda eid: 26927691 if eid == self.e['id'] else None)
        self.assertEqual(self.apply(dict(id=self.e['id'], sent=True))[0]['status'], 'Sent')

    def test_a_proven_send_can_not_be_undone_from_chat(self):
        self.link(26927691)
        self.apply(dict(id=self.e['id'], sent=True))
        out = self.apply(dict(id=self.e['id'], sent=False))
        self.assertIn('stays Sent', out[0]['error'])
        self.assertEqual(self.status(), 'Sent')

    def test_a_chat_message_or_planned_date_never_makes_it_sent(self):
        self.apply(dict(id=self.e['id'], planned_date='2026-11-01'))           # date in the past
        self.assertEqual(self.status(), 'Draft')
        self.apply(dict(id=self.e['id'], status='Sent'))                       # status isn't writable from chat
        self.assertEqual(self.status(), 'Draft')

    def test_sent_false_undoes_a_wrong_sent_and_reports_it(self):
        self.mails.save(self.e['id'], dict(status='Sent', planned_date='2099-11-13'), self.mails.get(self.e['id'])['revision'])   # Sent before its date
        out = self.apply(dict(id=self.e['id'], sent=False))
        self.assertEqual((out[0]['action'], out[0]['status'], out[0]['fields']), ('updated', 'Draft', ['status']))
        self.assertEqual(self.status(), 'Draft')
        again = self.apply(dict(id=self.e['id'], sent=False))
        self.assertEqual(again[0]['action'], 'failed')                         # nothing to undo is never "updated"

    def test_an_older_unlinked_send_is_not_undone_from_chat(self):
        rev = self.mails.get(self.e['id'])['revision']
        self.mails.save(self.e['id'], dict(status='Sent', planned_date='2026-10-30'), rev)
        out = self.apply(dict(id=self.e['id'], sent=False))
        self.assertIn('Ask Josh', out[0]['error'])
        self.assertEqual(self.status(), 'Sent')

    def test_no_change_is_reported_as_unchanged(self):
        out = self.apply(dict(id=self.e['id'], subject="You're explaining your work to the wrong people"))
        self.assertEqual(out[0]['action'], 'unchanged')

    def test_every_version_is_kept(self):
        self.apply(dict(id=self.e['id'], body='Hey,\n\nSecond version.'))
        self.apply(dict(id=self.e['id'], body='Hey,\n\nThird version.'))
        bodies = [v['body'] for v in self.mails.history(self.e['id'])]
        self.assertEqual(bodies[-1], 'Hey,\n\nFirst version.')
        self.assertEqual(len(bodies), 3)

    def test_proof_reads_only_a_complete_snapshot(self):
        self.assertFalse(kit_send_proof(self.store, '')['sent'])
        with self.store.connect() as c:
            c.execute('INSERT INTO business_counters VALUES(?,?)', ('kit:2026-11-14', json.dumps(dict(complete=False, items={}))))
        self.assertTrue(kit_send_proof(self.store, '26927691')['sent'])        # the partial newer snapshot is skipped


class Show(Base):
    def test_show_adds_the_saved_text_and_never_saves(self):
        rev = self.mails.get(self.e['id'])['revision']
        done, text = self.a.email_answer(self.mails, dict(show_email=self.e['id'], emails=[
            dict(id=self.e['id'], body='Rebuilt from the chat'), dict(id='', subject='Another new draft', body='x')]))
        self.assertIn('First version.', text)
        self.assertIn('planned 2026-11-13 17:00', text)
        self.assertIn(f'saved version {rev}', text)
        self.assertEqual(self.mails.get(self.e['id'])['body'], 'Hey,\n\nFirst version.')
        self.assertEqual(self.mails.get(self.e['id'])['revision'], rev)
        self.assertEqual(done[0]['action'], 'failed')
        self.assertEqual(done[1]['action'], 'added')                           # other emails are still handled

    def test_show_is_by_stable_id_wherever_it_is_asked(self):
        a = self.a.email_answer(self.mails, dict(show_email=self.e['id']))[1]
        b = self.a.email_answer(self.mails, dict(show_email=self.e['id']))[1]
        self.assertEqual(a, b)
        self.assertIn('no email with id', self.a.email_answer(self.mails, dict(show_email='nope'))[1])


class ShowBlocksEverything(Base):
    def test_a_show_answer_saves_nothing_at_all(self):
        """The whole chat path: the model asks to show the email and also rebuilds it, adds a new draft and a to-do."""
        answer = dict(reply='Here it is.', show_email=self.e['id'], emails=[dict(id=self.e['id'], body='Rebuilt'), dict(subject='New idless draft', body='x')],
                      kit_draft=dict(email_id=self.e['id'], audience={}), todos=[dict(title='x')], learned=[])
        kept, held = A.show_only(answer)
        self.assertEqual(held, ['emails', 'kit_draft', 'todos'])
        self.assertEqual(kept, dict(reply='Here it is.', show_email=self.e['id']))
        done, text = self.a.email_answer(self.mails, kept)
        self.assertEqual(done, [])
        self.assertIn('First version.', text)
        self.assertEqual(len(self.mails.all()), 1)                              # no new draft
        self.assertEqual(self.mails.get(self.e['id'])['revision'], 1)
        self.assertEqual(A.show_only(dict(reply='x', emails=[1]))[1], [])        # an answer that shows nothing is untouched


class Connectors(unittest.TestCase):
    def test_runs_with_connector_tools_wait_for_them(self):
        a = Agents.__new__(Agents)
        a.root = Path('.')
        a.lead_magnets = SimpleNamespace(agent_context=lambda: '')
        seen = {}

        def fake(cmd, **kw):
            seen['env'] = kw.get('env')
            return SimpleNamespace(stdout=json.dumps(dict(result='{}', structured_output=dict(reply='ok'))), stderr='', returncode=0)
        with mock.patch.object(A, 'find_claude', return_value='claude.exe'), mock.patch.object(A.subprocess, 'run', fake):
            a.run('sys', 'hi', extra_tools=('mcp__claude_ai_Kit__list_broadcasts',))
            self.assertEqual(seen['env']['CLAUDE_CODE_MCP_STARTUP_WAIT_MS'], '30000')
            a.run('sys', 'hi')
            self.assertIsNone(seen['env'])


if __name__ == '__main__':
    unittest.main()
