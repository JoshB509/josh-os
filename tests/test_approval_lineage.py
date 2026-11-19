"""Which offer Josh's "send it" answers (studio/approval_lineage.py, replies.natural_from_chat). No Gmail, no Discord:
a fake runner stands in for the guarded Gmail call, and Gmail actions are switched on only in a temporary settings file."""
import json
import tempfile
import unittest
import unittest.mock
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from studio import approval_lineage as L
from studio import gmail_access
from studio import replies as R
from studio.discord_bridge import Bridge
from studio.store import Store

READ = dict(enabled=True, verified_mailbox='josh@example.com', verified_at='2026-11-11T08:00:00+00:00',
            verification_method='owner_confirmation', verified_tools=list(gmail_access.READ_TOOLS))
ACTIONS = dict(enabled=True, mailbox='josh@example.com', actions=['draft', 'send'], switched_on_at='2026-11-12T10:00:00+00:00')
CHANNEL_A, CHANNEL_B = dict(channel_id='111111111111', is_dm=False), dict(channel_id='222222222222', is_dm=False)
DM = dict(channel_id='999999999999', is_dm=True)


class FakeRun:
    def __init__(self):
        self.calls = []

    def __call__(self, system, prompt, schema, **kw):
        self.calls.append(kw['guard']['write']['input'])
        return '', dict(reply='done', id=f'gmail-{len(self.calls)}', thread_id='t', view_url='')


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / 'settings.json').write_text(json.dumps(dict(gmail_read=READ, gmail_actions=ACTIONS)), encoding='utf-8')
        self.r = R.Replies(Store(self.root / 'Data'), self.root)
        self.avi = self.add('aa', 'Avi', 'Avi <avi@example.com>', 'Hi Avi, yes.')
        self.dg = self.add('bb', '', 'dgdublin@example.ie', 'Hi, here is how refunds work.')
        self.bea = self.add('cc', 'Bea', 'bea@example.com', 'Hi Bea, use six slides.')
        self.run = FakeRun()
        self.n = 0

    def tearDown(self):
        self.tmp.cleanup()

    def add(self, k, name, email, body):
        return self.r.add(dict(thread_id=f'18{k * 4}01', message_id=f'18{k * 4}02', from_name=name, from_email=email,
                               subject='About ' + k, received_at='2026-11-12T09:00:00Z', summary='', proposed_reply=body))

    def show(self, where, *props, state='sent', owner='42'):
        """Anouk shows these replies in a message on Discord, written to owner. Returns that message's id."""
        self.n += 1
        mid = str(500000000000 + self.n)
        self.r.lineage.record('anouk', L.conversation(dict(where, author_id=owner)), [mid], [self.r._offer(self.r.get(p['id'])) for p in props],
                              state=state, owner_id=owner, channel_id=where['channel_id'])
        return mid

    def josh(self, where, text='send it', reply_to=None, mid=None, at=None, owner='42'):
        self.n += 1
        d = dict(where, author_id=owner, message_id=mid or str(900000000000 + self.n), reply_to=reply_to)
        return self.r.natural_from_chat('anouk', text, d, 'discord', self.run, at=at)

    def sent_to(self):
        return [c['to'] for c in self.run.calls]

    def notice(self, *props):
        """Anouk's mail notice, with its offers frozen when it was written (as Replies.check_now queues it)."""
        return dict(kind='mail', proposal_ids=[p['id'] for p in props], offers=[self.r._offer(self.r.get(p['id'])) for p in props])


class Conversations(Base):
    def test_dm_send_it_answers_the_dm_not_a_newer_channel_offer(self):
        self.show(DM, self.dg)                 # notice about dgdublin in the DM
        self.show(CHANNEL_A, self.avi)         # then Avi's reply shown in a channel, the newest message anywhere
        self.assertIn('Sent your reply', self.josh(DM))
        self.assertEqual(self.sent_to(), [['dgdublin@example.ie']])

    def test_dm_with_no_offer_never_falls_back_to_a_channel_offer(self):
        self.show(CHANNEL_A, self.avi)
        self.assertEqual(self.josh(DM), L.REASONS['none'])
        self.show(DM)                          # a DM message that showed no reply
        self.assertEqual(self.josh(DM), L.REASONS['no_offer'])
        self.assertEqual(self.run.calls, [])

    def test_interleaved_channels_each_answer_their_own(self):
        self.show(CHANNEL_A, self.avi)
        b = self.show(CHANNEL_B, self.bea)
        self.assertIn('Sent', self.josh(CHANNEL_A))
        self.assertEqual(self.josh(CHANNEL_A, reply_to=b), L.REASONS['other_conversation'])   # B's message, answered in A
        self.assertIn('Sent', self.josh(CHANNEL_B, reply_to=b))
        self.assertEqual(self.sent_to(), [['avi@example.com'], ['bea@example.com']])

    def test_a_reply_to_an_older_message_answers_that_one_only(self):
        first = self.show(DM, self.dg)
        self.show(DM, self.avi)
        self.assertIn('Sent', self.josh(DM, 'yes send it', reply_to=first))
        self.assertEqual(self.sent_to(), [['dgdublin@example.ie']])
        self.assertEqual(self.josh(DM, reply_to='123456789'), L.REASONS['unknown'])

    def test_a_newer_message_without_an_offer_blocks_the_older_one(self):
        self.show(DM, self.dg)
        self.show(DM)
        self.assertEqual(self.josh(DM), L.REASONS['no_offer'])
        self.assertEqual(self.run.calls, [])

    def test_several_offers_in_one_message_need_a_name(self):
        self.show(DM, self.avi, self.bea)
        self.assertIn("couldn't tell which", self.josh(DM))
        self.assertIn('Sent', self.josh(DM, 'send it to Bea'))
        self.assertEqual(self.sent_to(), [['bea@example.com']])


class FailClosed(Base):
    def test_partial_or_unclear_notice_never_approves_and_blocks_older(self):
        self.show(DM, self.avi)
        rec = self.r.begin_notice('anouk', [self.notice(self.dg)], '42')
        rec = self.r.finish_notice(rec, dict(state='partial', message_ids=['700000000001'], channel_id=DM['channel_id'], reason='1 of 2'))
        self.assertFalse(rec['usable'])
        self.assertEqual(self.josh(DM), L.REASONS['partial'])
        self.assertEqual(self.josh(DM, reply_to='700000000001'), L.REASONS['partial'])
        self.assertEqual(self.run.calls, [])

    def test_a_record_that_never_finished_blocks_the_older_offer(self):
        self.show(DM, self.avi)
        self.r.begin_notice('anouk', [self.notice(self.dg)], '42')   # sent, then finishing the record never happened
        self.assertEqual(self.josh(DM), L.REASONS['busy'])              # still held in this run: refused at once
        self.r = R.Replies(Store(self.root / 'Data'), self.root)      # after a restart the record is still "sending"
        self.assertEqual(self.josh(DM), L.REASONS['partial'])
        self.assertEqual(self.run.calls, [])

    def test_a_full_notice_records_the_offer_as_written(self):
        rec = self.r.finish_notice(self.r.begin_notice('anouk', [self.notice(self.dg)], '42'),
                                   dict(state='sent', message_ids=['700000000002'], channel_id=DM['channel_id']))
        o = rec['offers'][0]
        self.assertEqual((rec['conversation'], rec['owner_id'], o['to'], o['revision'], o['body_digest']),
                         ('discord:dm:42', '42', ['dgdublin@example.ie'], 1, L.digest('Hi, here is how refunds work.')))
        self.assertIn('Sent', self.josh(DM, reply_to='700000000002'))

    def test_a_reply_revised_after_the_notice_was_written_is_refused(self):
        queued = self.notice(self.dg)                               # written at the mail check
        self.r.update(self.dg['id'], dict(proposed_reply='Hi, a revised answer the notice never showed.'))
        self.r.finish_notice(self.r.begin_notice('anouk', [queued], '42'),
                             dict(state='sent', message_ids=['700000000003'], channel_id=DM['channel_id']))
        self.assertIn('changed after I showed it', self.josh(DM))
        self.assertEqual(self.run.calls, [])

    def test_a_recipient_changed_after_the_notice_was_written_is_refused(self):
        queued = self.notice(self.dg)
        self.r.update(self.dg['id'], dict(to='someone@else.com'))
        self.r.finish_notice(self.r.begin_notice('anouk', [queued], '42'),
                             dict(state='sent', message_ids=['700000000004'], channel_id=DM['channel_id']))
        self.assertIn('changed after I showed it', self.josh(DM))
        self.assertEqual(self.run.calls, [])

    def test_a_batch_of_notices_shows_no_full_reply_so_offers_nothing(self):
        self.assertEqual(self.r.begin_notice('anouk', [self.notice(self.dg), self.notice(self.avi)], '42')['offers'], [])

    def test_no_owner_means_no_record_and_so_no_send(self):
        with self.assertRaises(ValueError):
            self.r.begin_notice('anouk', [self.notice(self.dg)], None)

    def test_an_edit_after_it_was_shown_stops_the_send(self):
        self.show(DM, self.dg)
        self.r.update(self.dg['id'], dict(proposed_reply='Hi, a different answer.'))
        self.assertIn('changed after I showed it', self.josh(DM))
        self.assertEqual(self.run.calls, [])

    def test_a_repeated_discord_event_is_handled_once(self):
        self.show(DM, self.dg)
        self.assertIn('Sent', self.josh(DM, mid='800000000001'))
        self.assertEqual(self.josh(DM, mid='800000000001'), L.REASONS['duplicate'])
        self.assertEqual(len(self.run.calls), 1)

    def test_a_second_send_it_never_reuses_the_offer(self):
        self.show(DM, self.dg)
        self.josh(DM)
        self.assertNotIn('Sent', self.josh(DM))
        self.assertEqual(len(self.run.calls), 1)

    def test_conditions_and_changes_hold_the_send(self):
        self.show(DM, self.dg)
        for text in ('send it but change the greeting', 'send it tomorrow', "send it, don't mention the price"):
            said = self.josh(DM, text)                                  # not read as an approval; goes to Anouk instead
            self.assertTrue(said is None or 'condition' in said, said)
        for text in ('send it\nbut change the greeting first', "send it\ndon't mention the price"):
            self.assertIn('condition', self.josh(DM, text))
        self.assertEqual(self.run.calls, [])

    def test_stale_offer_and_missing_conversation(self):
        self.show(DM, self.dg)
        later = datetime.now(timezone.utc) + timedelta(hours=L.STALE_HOURS + 1)
        self.assertEqual(self.josh(DM, at=later), L.REASONS['stale'])
        self.assertEqual(self.r.natural_from_chat('anouk', 'send it', None, 'discord', self.run), L.REASONS['no_conversation'])
        self.assertEqual(self.run.calls, [])

    def test_gmail_switched_off_sends_nothing(self):
        (self.root / 'settings.json').write_text(json.dumps(dict(gmail_read=READ)), encoding='utf-8')   # writes off, as now
        self.show(DM, self.dg)
        self.assertIn("isn't switched on", self.josh(DM))
        self.assertEqual(self.run.calls, [])


class Owners(Base):
    def test_another_owner_can_never_approve_an_offer_shown_to_josh(self):
        mid = self.show(DM, self.dg, owner='42')
        other_dm = dict(DM, channel_id='888888888888')
        self.assertEqual(self.josh(other_dm, owner='43'), L.REASONS['none'])
        self.assertEqual(self.josh(other_dm, owner='43', reply_to=mid), L.REASONS['other_conversation'])
        a = self.show(CHANNEL_A, self.avi, owner='42')
        self.assertEqual(self.josh(CHANNEL_A, owner='43'), L.REASONS['other_owner'])
        self.assertEqual(self.josh(CHANNEL_A, owner='43', reply_to=a), L.REASONS['other_owner'])
        self.assertEqual(self.run.calls, [])

    def test_the_same_owner_in_a_different_dm_channel_is_refused(self):
        self.show(DM, self.dg)
        self.assertEqual(self.josh(dict(DM, channel_id='777777777777')), L.REASONS['other_conversation'])
        self.assertEqual(self.run.calls, [])


class Identity(Base):
    def test_identical_body_binds_only_the_recipient_named_in_the_message(self):
        same = 'Thanks, I will look at this and come back to you.'
        a = self.add('dd', 'Avi', 'avi2@example.com', same)
        b = self.add('ee', 'Bea', 'bea2@example.com', same)
        self.assertEqual([o['proposal_id'] for o in self.r.offers_for(f'Here is a reply for Bea:\n>>> {same}')], [b['id']])
        self.assertEqual(self.r.offers_for(f'Here is a reply:\n>>> {same}'), [])          # nobody named, nothing offered
        both = self.r.offers_for(f'For Avi and for Bea:\n>>> {same}')
        self.assertEqual(sorted(o['proposal_id'] for o in both), sorted([a['id'], b['id']]))   # both named: "send it" must say who

    def test_a_name_inside_another_word_does_not_count(self):
        self.assertEqual(self.r.offers_for('Bravia says:\n>>> Hi Avi, yes.'), [])


class Desk(Base):
    def test_desk_answers_only_its_own_newest_message(self):
        rec = self.r.lineage.record('anouk', L.DESK, ['desk-1'], [self.r._offer(self.r.get(self.dg['id']))])
        self.assertIn('I wrote to you after', self.r.natural_from_chat('anouk', 'send it', None, '', self.run, desk_last='from-discord'))
        self.assertIn('Sent', self.r.natural_from_chat('anouk', 'send it', None, '', self.run, desk_last=rec['message_ids'][0]))
        self.assertEqual(self.sent_to(), [['dgdublin@example.ie']])


class BridgeRecords(unittest.TestCase):
    def test_bridge_records_before_sending_and_only_a_full_send_counts(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        r = R.Replies(Store(Path(tmp.name) / 'Data'), Path(tmp.name))
        b = Bridge.__new__(Bridge)
        b.server = SimpleNamespace(replies=r)
        b.config = dict(owners=['42'])
        dm = b.begin_sent('anouk', dict(DM, author_id='42', message_id='1'), [dict(proposal_id='p')])
        self.assertEqual((dm['conversation'], dm['state'], dm['usable']), ('discord:dm:42', 'sending', False))
        dm = b.finish_sent(dm, ['10'], True)
        ch = b.finish_sent(b.begin_sent('anouk', dict(CHANNEL_A, author_id='42', message_id='2'), [dict(proposal_id='q')]), ['20'], False, 'error')
        self.assertEqual((dm['usable'], dm['channel_id']), (True, DM['channel_id']))
        self.assertEqual((ch['conversation'], ch['state'], ch['usable']), ('discord:channel:111111111111', 'partial', False))
        self.assertIsNone(b.begin_sent('sam', dict(DM, author_id='42'), []))
        self.assertEqual(b.dm_owner('anouk'), '42')
        b.server = SimpleNamespace()
        with self.assertRaises(RuntimeError):
            b.begin_sent('anouk', dict(DM, author_id='42'), [])


class RecordFailure(Base):
    """The database fails while an Anouk reply is being recorded before it goes out."""

    def bridge(self):
        b = Bridge.__new__(Bridge)
        b.server = SimpleNamespace(replies=self.r)
        b.config = dict(owners=['42'])
        b.status = dict(error='')
        return b

    def test_an_unrecorded_reply_blocks_older_offers_through_a_restart_until_reoffered(self):
        self.show(DM, self.dg)                                         # an older offer, recorded and approvable
        note = self.bridge().unrecorded_note('anouk', dict(DM, author_id='42'), RuntimeError('database is locked'))
        self.assertIn("couldn't record my reply", note)                # the note goes out only because the block is saved
        self.assertEqual(self.josh(DM), L.REASONS['blocked'])
        self.assertEqual(self.josh(DM, reply_to='500000000001'), L.REASONS['blocked'])
        restarted = R.Replies(Store(self.root / 'Data'), self.root)     # Josh OS restarts
        self.r = restarted
        self.assertEqual(self.josh(DM), L.REASONS['blocked'])
        self.assertEqual(self.run.calls, [])
        self.show(DM, self.dg)                                         # Anouk shows the reply again, fully recorded
        self.assertIn('Sent', self.josh(DM))
        self.assertEqual(self.sent_to(), [['dgdublin@example.ie']])

    def test_a_partial_reoffer_does_not_lift_the_block(self):
        self.show(DM, self.dg)
        self.bridge().unrecorded_note('anouk', dict(DM, author_id='42'), RuntimeError('locked'))
        self.show(DM, self.dg, state='partial')
        self.assertEqual(self.josh(DM), L.REASONS['blocked'])
        self.assertEqual(self.run.calls, [])

    def test_when_the_block_cannot_be_saved_nothing_is_posted_and_this_run_stays_blocked(self):
        self.show(DM, self.dg)
        with unittest.mock.patch('pathlib.Path.write_text', side_effect=OSError('disk full')):
            note = self.bridge().unrecorded_note('anouk', dict(DM, author_id='42'), RuntimeError('locked'))
        self.assertIsNone(note)                                        # silent: nothing unrecorded reaches Josh
        self.assertEqual(self.josh(DM), L.REASONS['blocked'])          # and approvals here are blocked in memory
        self.assertEqual(self.run.calls, [])

    def test_an_unreadable_block_file_still_blocks(self):
        self.r.lineage.block('anouk', 'discord:dm:42', 'x')
        self.r.lineage._blocks.clear()
        self.r.lineage._block_file('anouk', 'discord:dm:42').write_text('not json', encoding='utf-8')
        self.show(DM, self.dg, state='partial')
        self.assertEqual(self.josh(DM), L.REASONS['blocked'])

    def test_other_conversations_are_not_blocked(self):
        self.show(CHANNEL_A, self.avi)
        self.bridge().unrecorded_note('anouk', dict(DM, author_id='42'), RuntimeError('locked'))
        self.assertIn('Sent', self.josh(CHANNEL_A))


class Ordering(Base):
    """A chat reply and a proactive notice in one DM, and Josh's "send it" handled late. Discord ids carry the moment
    Discord made each message, so they, not processing time, say what Josh had seen when he wrote."""

    def offer(self, p):
        return [self.r._offer(self.r.get(p['id']))]

    def say(self, mid, text='send it', reply_to=None):
        return self.josh(DM, text, reply_to=reply_to, mid=str(mid))

    def test_a_second_message_waits_so_records_follow_delivery_order(self):
        import threading
        chat = self.r.lineage.begin('anouk', 'discord:dm:42', self.offer(self.avi), owner_id='42', channel_id=DM['channel_id'])
        order = []

        def notice():
            rec = self.r.begin_notice('anouk', [dict(kind='mail', offers=self.offer(self.dg))], '42')
            order.append('notice begun')
            self.r.finish_notice(rec, dict(state='sent', message_ids=['600000000002'], channel_id=DM['channel_id']))
        t = threading.Thread(target=notice)
        t.start()
        t.join(0.3)
        self.assertTrue(t.is_alive())                                   # waits while the chat reply is going out
        order.append('chat finished')
        self.r.lineage.finish(chat, ['600000000001'], 'sent')
        t.join(5)
        self.assertEqual(order, ['chat finished', 'notice begun'])
        self.assertIn('Sent', self.say(600000000010))                   # Josh wrote after both arrived: the notice is the latest
        self.assertEqual(self.sent_to(), [['dgdublin@example.ie']])

    def test_send_it_while_b_is_going_out_is_refused_at_once_and_never_binds_b(self):
        """A is shown, B starts going out, Josh says "send it" meaning A. It must not wait for B and then send B."""
        a = self.r.lineage.record('anouk', 'discord:dm:42', ['600000000001'], self.offer(self.avi), owner_id='42', channel_id=DM['channel_id'])
        b = self.r.begin_notice('anouk', [dict(kind='mail', offers=self.offer(self.dg))], '42')
        import time
        t0 = time.monotonic()
        self.assertEqual(self.say(600000000005), L.REASONS['busy'])
        self.assertLess(time.monotonic() - t0, 1.0)                     # no waiting for B
        self.r.finish_notice(b, dict(state='sent', message_ids=['600000000009'], channel_id=DM['channel_id']))
        self.assertEqual(self.run.calls, [])
        # The same "send it" event processed again later still binds nothing (B arrived after it, and it was claimed)
        self.assertEqual(self.say(600000000005), L.REASONS['duplicate'])
        self.assertEqual(self.run.calls, [])
        self.assertTrue(a['usable'])

    def test_a_late_processed_send_it_never_binds_a_message_that_arrived_after_it(self):
        """A shown, Josh writes "send it" (id 5), B arrives (id 9), and only then is Josh's message handled."""
        self.r.lineage.record('anouk', 'discord:dm:42', ['600000000001'], self.offer(self.avi), owner_id='42', channel_id=DM['channel_id'])
        self.r.lineage.record('anouk', 'discord:dm:42', ['600000000009'], self.offer(self.dg), owner_id='42', channel_id=DM['channel_id'])
        self.assertEqual(self.say(600000000005), L.REASONS['arrived_after'])
        self.assertEqual(self.run.calls, [])
        self.assertIn('Sent', self.say(600000000006, reply_to='600000000001'))   # replying to A itself is exact
        self.assertEqual(self.sent_to(), [['avi@example.com']])

    def test_every_part_of_a_multipart_message_must_come_before_josh(self):
        self.r.lineage.record('anouk', 'discord:dm:42', ['600000000001', '600000000004', '600000000008'], self.offer(self.dg),
                              owner_id='42', channel_id=DM['channel_id'])
        self.assertEqual(self.say(600000000006), L.REASONS['arrived_after'])          # its last part came after Josh wrote
        self.assertEqual(self.say(600000000007, reply_to='600000000001'), L.REASONS['arrived_after'])
        self.assertIn('Sent', self.say(600000000010))                                 # all parts before: fine
        self.assertEqual(self.sent_to(), [['dgdublin@example.ie']])

    def test_without_josh_s_own_discord_id_nothing_binds(self):
        self.show(DM, self.dg)
        d = dict(DM, author_id='42', message_id='', reply_to=None)
        self.assertEqual(self.r.natural_from_chat('anouk', 'send it', d, 'discord', self.run), L.REASONS['no_conversation'])
        self.assertEqual(self.run.calls, [])

    def test_owner_and_conversation_still_apply_with_ordering(self):
        self.r.lineage.record('anouk', 'discord:dm:42', ['600000000001'], self.offer(self.dg), owner_id='42', channel_id=DM['channel_id'])
        self.assertEqual(self.josh(dict(DM, channel_id='888888888888'), owner='43', mid='600000000005'), L.REASONS['none'])
        self.assertEqual(self.josh(dict(DM, channel_id='777777777777'), mid='600000000006'), L.REASONS['other_conversation'])
        self.assertEqual(self.run.calls, [])

    def test_a_failed_finish_still_lets_the_next_message_go(self):
        rec = self.r.lineage.begin('anouk', 'discord:dm:42', [], owner_id='42')
        with unittest.mock.patch.object(self.r.lineage, '_finish', side_effect=RuntimeError('db')):
            with self.assertRaises(RuntimeError):
                self.r.lineage.finish(rec, ['1'], 'sent')
        old = L.SEND_WAIT_S
        L.SEND_WAIT_S = 0.5
        try:
            nxt = self.r.lineage.begin('anouk', 'discord:dm:42', [], owner_id='42')   # not stuck behind the failed one
        finally:
            L.SEND_WAIT_S = old
        self.r.lineage.finish(nxt, ['2'], 'sent')


class NoticesSend(unittest.TestCase):
    def test_a_message_that_cannot_be_recorded_is_not_sent(self):
        from studio.notices import Notices
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        store = Store(Path(tmp.name) / 'Data')
        sent = []
        bridge = SimpleNamespace(dm_ready=lambda a: (True, ''), dm_owner=lambda a: '42',
                                 send_dm=lambda a, text: sent.append(text) or dict(state='sent', message_ids=['1'], channel_id='9'))
        n = Notices(store, store.root, bridge, lambda: '')
        n.on_sending = lambda *a: (_ for _ in ()).throw(RuntimeError('database locked'))
        n.queue('anouk', 'mail', 'A reply for Avi', 'k1', datetime.now(timezone.utc))
        item = n.all(10)[0]
        self.assertEqual(n.send('anouk', [item], n.settings(), datetime.now(timezone.utc)), 'Anouk: failed')
        self.assertEqual(sent, [])
        self.assertIn('could not record', n.all(10)[0]['reason'])


if __name__ == '__main__':
    unittest.main()
