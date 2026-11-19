"""Anouk's quick review of email and comment replies (studio/reply_queue.py, 16 Nov 2026).

Mocks only: a fake runner stands in for the guarded Gmail calls and a fake client for YouTube. Nothing is drafted, sent
or posted anywhere."""
import threading
import unittest
from datetime import timedelta
from pathlib import Path
import sys

ROOT = Path(r'E:\Videos\DaJoshiResolve')
sys.path.insert(0, str(ROOT))
from studio.reply_queue import ReplyQueue  # noqa: E402
from studio.replies import verify_readback  # noqa: E402
from tests.test_gmail_actions import Base as MailBase, FakeRun  # noqa: E402
from tests.test_youtube_comments import Base as CommentBase, T0  # noqa: E402

SENT = dict(reply='done', id='gmail-1', thread_id='18cabc1234', view_url='https://mail.google.com/x')


def readback(**over):
    """What get_message would show for the sent reply: every field the check needs, matching the approved version."""
    m = {'found': True, 'id': 'gmail-1', 'thread_id': '18cabc1234', 'from': 'Josh <josh@example.com>', 'to': ['dana@example.com'],
         'cc': [], 'bcc': None, 'subject': 'Re: Module 2', 'label_ids': ['SENT'], 'body': 'Hi Dana,\n\nYes, start with lesson 3.\n\nJosh'}
    m.update(over)
    return m


class Email(MailBase):
    def setUp(self):
        super().setUp()
        self.run = FakeRun()
        self.q = ReplyQueue(self.r, None, self.run)

    def item(self):
        return next(x for x in self.q.view()['waiting'] if x['id'] == self.p['id'])

    def test_the_review_shows_original_destination_and_exact_reply(self):
        self.r.update(self.p['id'], dict(proposed_reply='Hi Dana,\n\nYes, start with lesson 3.\n\nJosh'))
        it = self.item()
        self.assertEqual(it['destination']['to'], ['dana@example.com'])
        self.assertEqual(it['destination']['account'], 'josh@example.com')
        self.assertEqual(it['destination']['thread_id'], '18cabc1234')
        self.assertEqual(it['reply'], 'Hi Dana,\n\nYes, start with lesson 3.\n\nJosh')
        self.assertEqual(it['original']['subject'], 'Module 2')
        self.assertTrue(it['available'] and it['digest'])

    def test_one_click_sends_that_version_and_counts_only_after_gmail_reads_it_back(self):
        it = self.item()
        self.run.answers = [SENT, readback(subject=self.r.binding(self.r.get(self.p['id']), 'send')['input']['subject'])]
        out = self.q.send('email', it['id'], it['digest'])
        self.assertEqual(out['outcome']['state'], 'replied')
        self.assertEqual(len(self.run.calls), 2)
        self.assertTrue(self.run.calls[1]['read_only'])
        self.assertEqual(self.run.calls[1]['extra_tools'], ('mcp__claude_ai_Gmail__get_message',))

    def test_a_read_back_that_does_not_match_is_shown_as_unchecked_and_never_resent(self):
        it = self.item()
        self.run.answers = [SENT, readback(body='Something else entirely')]
        out = self.q.send('email', it['id'], it['digest'])
        self.assertEqual(out['outcome']['state'], 'unverified')
        self.assertIn('body', out['outcome']['reason'])
        with self.assertRaises(ValueError):
            self.q.send('email', it['id'], it['digest'])
        self.assertEqual(len(self.run.calls), 2)

    def test_an_edit_makes_the_shown_version_stale(self):
        old = self.item()['digest']
        self.q.edit('email', self.p['id'], 'Hi Dana,\n\nStart with lesson 4.\n\nJosh')
        with self.assertRaises(ValueError):
            self.q.send('email', self.p['id'], old)
        self.assertEqual(self.run.calls, [])

    def test_two_clicks_send_once(self):
        it = self.item()
        self.run.answers = [SENT, readback(subject=self.r.binding(self.r.get(self.p['id']), 'send')['input']['subject'])]
        self.q.send('email', it['id'], it['digest'])
        with self.assertRaises(ValueError):
            self.q.send('email', it['id'], it['digest'])
        self.assertEqual(sum(1 for c in self.run.calls if not c.get('read_only')), 1)

    def test_nothing_sends_while_gmail_sending_is_off(self):
        self.settings(actions=None)
        it = self.item()
        self.assertFalse(it['available'])
        with self.assertRaises(ValueError):
            self.q.send('email', it['id'], it['digest'])
        self.assertEqual(self.run.calls, [])


class StrictReadBack(MailBase):
    """Gmail's read-back must match the approved sender, To, Cc, subject, thread and body exactly. Only line endings and
    address display names or case may differ. A missing field fails. (16 Nov 2026 review.)"""
    def setUp(self):
        super().setUp()
        self.r.update(self.p['id'], dict(cc='Sam <sam@example.com>'))
        self.b = self.r.binding(self.r.get(self.p['id']), 'send')
        self.good = readback(subject=self.b['input']['subject'], cc=['sam@example.com'], body=self.b['input']['body'])

    def check(self, **over):
        got = dict(self.good)
        for k, v in over.items():
            if v is ...:
                got.pop(k, None)
            else:
                got[k] = v
        return verify_readback(self.b, 'gmail-1', got)

    def assertFails(self, check, **over):
        ok, checks, why = self.check(**over)
        self.assertFalse(ok, over)
        self.assertTrue(why)
        if check:
            self.assertFalse(checks.get(check, True), (check, checks))

    def test_an_exact_copy_passes(self):
        self.assertEqual(self.check()[0], True)

    def test_canonical_equivalents_pass(self):
        body = self.b['input']['body'].replace('\n', '\r\n') + '\r\n'
        ok, checks, why = self.check(**{'from': '"Josh Brindley" <JOSH@Example.com>'}, to=['Dana <DANA@example.com>'],
                                     cc=['SAM@EXAMPLE.COM'], body=body, bcc=[])
        self.assertTrue(ok, (checks, why))

    def test_the_wrong_sender_fails(self):
        self.assertFails('sender', **{'from': 'Someone <someone@example.com>'})
        self.assertFails('sender', **{'from': 'josh@example.com, other@example.com'})

    def test_extra_or_missing_to_fails(self):
        self.assertFails('to', to=['dana@example.com', 'extra@example.com'])
        self.assertFails('to', to=[])

    def test_extra_or_missing_cc_fails(self):
        self.assertFails('cc', cc=['sam@example.com', 'extra@example.com'])
        self.assertFails('cc', cc=[])

    def test_any_bcc_fails_and_no_bcc_header_is_fine(self):
        self.assertFails('no_bcc', bcc=['hidden@example.com'])
        self.assertTrue(self.check(bcc=None)[0])

    def test_changed_or_appended_body_fails(self):
        body = self.b['input']['body']
        self.assertFails('body', body=body + '\n\nP.S. something Josh never approved')
        self.assertFails('body', body='Before. ' + body)
        self.assertFails('body', body=body.replace('lesson 3', 'lesson 4'))
        self.assertFails('body', body=body.replace('\n\n', '\n', 1))       # a removed blank line is a change, not a line ending
        self.assertFails('body', body=body.replace(' ', '  ', 1))          # so is a doubled space

    def test_changed_subject_fails(self):
        self.assertFails('subject', subject=self.b['input']['subject'] + ' ')
        self.assertFails('subject', subject='Something else')

    def test_a_mismatched_thread_fails(self):
        self.assertFails('thread', thread_id='18cdifferent')

    def test_absent_fields_fail_without_inventing_evidence(self):
        for k in ('from', 'to', 'cc', 'subject', 'body', 'thread_id', 'label_ids', 'id'):
            ok, checks, why = self.check(**{k: ...})
            self.assertFalse(ok, k)
            self.assertIn(k, why)
        self.assertFails('to', to=None)
        self.assertFails('in_sent', label_ids=['DRAFT'])
        self.assertFalse(self.check(found=False)[0])

    def test_verify_marks_a_mismatch_unverified_through_the_real_flow(self):
        q = ReplyQueue(self.r, None, FakeRun(SENT, dict(self.good, cc=[])))
        out = q.send('email', self.p['id'], self.b['digest'])
        self.assertEqual(out['outcome']['state'], 'unverified')
        self.assertIn('cc', out['outcome']['reason'])

    def test_edit_away_and_back_needs_a_new_approval(self):
        old = self.b['digest']
        text = self.r.get(self.p['id'])['proposed_reply']
        self.r.update(self.p['id'], dict(proposed_reply=text + ' Changed.'))
        self.r.update(self.p['id'], dict(proposed_reply=text))
        self.assertNotEqual(self.r.binding(self.r.get(self.p['id']), 'send')['digest'], old)
        with self.assertRaises(ValueError):
            ReplyQueue(self.r, None, FakeRun()).send('email', self.p['id'], old)


class Comment(CommentBase):
    def setUp(self):
        super().setUp()
        self.yt.add('q1', 'How would you handle this if your boss interrupts?', T0 + timedelta(minutes=1), video='vidAAAAAAAA')
        self.now = T0 + timedelta(minutes=2)
        self.c.scan()
        self.q = ReplyQueue(type('NoMail', (), {'all': lambda s: [], 'action_config': lambda s: None})(), self.c)

    def item(self):
        return next(x for x in self.q.view()['waiting'] if x['id'] == 'q1')

    def test_the_review_shows_the_comment_and_its_public_destination(self):
        it = self.item()
        self.assertEqual(it['original']['text'], 'How would you handle this if your boss interrupts?')
        self.assertEqual(it['destination']['parent_id'], 'q1')
        self.assertEqual(it['destination']['video_id'], 'vidAAAAAAAA')
        self.assertTrue(it['destination']['public'])
        self.assertIn('lc=q1', it['link'])

    def test_one_click_posts_that_text_and_checks_it_on_youtube(self):
        it = self.item()
        out = self.q.send('comment', 'q1', it['digest'])
        self.assertEqual(out['outcome']['state'], 'replied')
        self.assertEqual(self.yt.replies, [('q1', it['reply'])])
        self.assertTrue(self.c.items()['q1'].get('verified_at'))

    def test_an_edit_needs_a_new_click(self):
        old = self.item()['digest']
        self.q.edit('comment', 'q1', 'Start with the answer, then the reason.')
        with self.assertRaises(ValueError):
            self.q.send('comment', 'q1', old)
        self.assertEqual(self.yt.replies, [])
        new = self.item()
        self.assertEqual((new['reply'], new['version']), ('Start with the answer, then the reason.', 2))
        self.q.send('comment', 'q1', new['digest'])
        self.assertEqual(self.yt.replies, [('q1', 'Start with the answer, then the reason.')])

    def test_edit_away_and_back_needs_a_new_approval(self):
        first = self.item()
        text, code = first['reply'], self.c.items()['q1']['code']
        self.q.edit('comment', 'q1', 'Something else for a moment.')
        self.q.edit('comment', 'q1', text)
        back = self.item()
        self.assertEqual(back['reply'], text)
        self.assertEqual(back['version'], 3)
        self.assertNotEqual(back['digest'], first['digest'])
        self.assertNotEqual(self.c.items()['q1']['code'], code)
        with self.assertRaises(ValueError):
            self.q.send('comment', 'q1', first['digest'])
        with self.assertRaises(ValueError):
            self.c.approve('q1', code)                     # the old chat code no longer matches either
        self.assertEqual(self.yt.replies, [])
        self.q.send('comment', 'q1', back['digest'])
        self.assertEqual(self.yt.replies, [('q1', text)])

    def test_double_click_posts_once(self):
        it = self.item()
        gate, errors = threading.Event(), []
        real = self.yt.reply

        def slow(parent, text):
            gate.wait(2)
            return real(parent, text)
        self.yt.reply = slow

        def click():
            try:
                self.q.send('comment', 'q1', it['digest'])
            except ValueError as e:
                errors.append(str(e))
        a, b = threading.Thread(target=click), threading.Thread(target=click)
        a.start(); b.start()
        gate.set(); a.join(); b.join()
        self.assertEqual(len(self.yt.replies), 1)
        self.assertEqual(len(errors), 1)

    def test_a_reply_youtube_cannot_read_back_is_not_called_sent(self):
        self.yt.get_comment = lambda cid: None
        it = self.item()
        out = self.q.send('comment', 'q1', it['digest'])
        self.assertEqual(out['outcome']['state'], 'unverified')
        with self.assertRaises(ValueError):
            self.q.send('comment', 'q1', it['digest'])
        self.assertEqual(len(self.yt.replies), 1)

    def test_a_failed_call_is_unclear_and_never_retried(self):
        self.yt.fail = True
        it = self.item()
        out = self.q.send('comment', 'q1', it['digest'])
        self.assertEqual(out['outcome']['state'], 'uncertain')
        self.yt.fail = False
        with self.assertRaises(ValueError):
            self.q.send('comment', 'q1', it['digest'])
        self.assertEqual(self.yt.replies, [])


if __name__ == '__main__':
    unittest.main()
