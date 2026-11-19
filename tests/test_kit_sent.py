import copy
import json
import unittest
from unittest import mock

from tests.test_laia_emails import Base
from studio.kit_sent import GET, read_receipt, save_failure_reply
from studio.store import Conflict


class Reconcile(Base):
    def setUp(self):
        super().setUp()
        self.text = ' '.join('work%d' % i for i in range(100))
        self.e = self.mails.save(self.e['id'], dict(body=self.text, preview='Keep local preview', notes='Keep notes'), self.e['revision'])
        self.b = dict(id=27018277, subject=self.e['subject'], status='completed',
                      send_at='2026-11-13T15:45:14Z', published_at='2026-11-13T15:45:22Z',
                      preview_text='', content='<p>' + self.text + '</p>')
        self.read = self.enterContext(mock.patch('studio.kit_sent.read_broadcast', return_value=self.b))

    def mark(self, **extra):
        return self.apply(dict(id=self.e['id'], sent=True, kit_broadcast_id='27018277', **extra))[0]

    def test_unlinked_completed_email_is_verified_saved_and_read_back(self):
        before = self.mails.get(self.e['id'])
        result = self.mark()
        self.assertEqual(result['action'], 'updated')
        after = self.mails.get(self.e['id'])
        self.assertEqual(after['status'], 'Sent')
        self.assertEqual(after['kit']['id'], 27018277)
        self.assertEqual(after['kit']['send_at'], self.b['send_at'])
        self.assertEqual(after['send_time'], '19:45')
        self.assertEqual(after['planned_date'], '2026-11-13')
        self.assertEqual(after['revision'], before['revision'] + 1)
        for key in ('body', 'preview', 'subject', 'tags', 'source_ids', 'provenance'):
            self.assertEqual(after[key], before[key])
        self.assertTrue(after['notes'].startswith('Keep notes'))
        self.assertEqual(self.mails.history(self.e['id'])[1], before)

    def test_wrong_identity_status_or_timestamp_never_saves(self):
        baseline = self.mails.get(self.e['id'])
        for changes in [dict(id=999), dict(subject='Other email'), dict(content='unrelated'),
                        dict(status='scheduled'), dict(send_at=None), dict(send_at='2026-11-13T15:45:14')]:
            with self.subTest(changes=changes):
                self.read.return_value = dict(self.b, **changes)
                self.assertEqual(self.mark()['action'], 'failed')
                self.assertEqual(self.mails.get(self.e['id']), baseline)

    def test_duplicate_local_subject_or_link_is_rejected(self):
        self.mails.create(dict(changes=dict(subject=self.e['subject'], body=self.text)))
        self.assertIn('Another local email', self.mark()['error'])
        self.assertEqual(self.status(), 'Draft')

    def test_unavailable_read_does_not_trust_chat_results(self):
        self.read.side_effect = ValueError('Kit unavailable')
        self.assertEqual(self.mark(results=dict(kit_broadcast_id='27018277', recipients=4147))['action'], 'failed')
        self.assertEqual(self.status(), 'Draft')

    def test_concurrent_edit_wins_and_reconciliation_rolls_back(self):
        def edit(*args):
            cur = self.mails.get(self.e['id'])
            self.mails.save(cur['id'], dict(notes='Concurrent note'), cur['revision'])
            return self.b
        self.read.side_effect = edit
        self.assertIn('changed in another window', self.mark()['error'])
        after = self.mails.get(self.e['id'])
        self.assertEqual(after['notes'], 'Concurrent note')
        self.assertEqual(after['status'], 'Draft')
        self.assertIsNone(after['kit'])

    def test_invalid_additional_change_leaves_link_and_status_untouched(self):
        before = self.mails.get(self.e['id'])
        self.assertEqual(self.mark(results=dict(not_a_result=1))['action'], 'failed')
        self.assertEqual(self.mails.get(self.e['id']), before)

    def test_durable_readback_failure_is_not_reported_as_success(self):
        real_get = self.mails.get
        reads = 0
        def get(eid):
            nonlocal reads
            reads += 1
            if reads > 1:raise OSError('Readback unavailable')
            return real_get(eid)
        with mock.patch.object(self.mails, 'get', side_effect=get):
            result = self.mark()
        self.assertEqual(result['action'], 'failed')
        self.assertIn('Readback unavailable', result['error'])


class Receipts(unittest.TestCase):
    def stream(self, **kw):
        call = dict(type='tool_use', id='call', name=GET, input=dict(id=7))
        result = dict(type='tool_result', tool_use_id='call', content=json.dumps(dict(broadcast=dict(id=7, status='completed'))))
        result.update(kw)
        return '\n'.join(json.dumps(dict(message=dict(content=[x]))) for x in (call, result))

    def test_raw_matching_receipt_only(self):
        self.assertEqual(read_receipt(self.stream(), 7)['id'], 7)
        for stream, bid in [(self.stream(), 8), (self.stream(is_error=True), 7),
                            (self.stream(tool_use_id='other'), 7), (json.dumps(dict(result='Broadcast 7 completed')), 7)]:
            with self.assertRaises(ValueError):read_receipt(stream, bid)

    def test_failed_save_replaces_pre_save_success_claim(self):
        reply = save_failure_reply('Done, marked sent!', [dict(kind='email', action='failed', title='Subject', error='Missing proof')])
        self.assertNotIn('Done', reply)
        self.assertIn('Missing proof', reply)
        self.assertEqual(save_failure_reply('Saved.', []), 'Saved.')


if __name__ == '__main__':
    unittest.main()
