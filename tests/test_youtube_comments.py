"""Anouk's YouTube comment replies and Sam's lead-magnet comment (studio/youtube_comments.py). Fakes only: no YouTube."""
import random
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(r'E:\Videos\DaJoshiResolve')
sys.path.insert(0, str(ROOT))
from studio import youtube_comments as Y  # noqa: E402

UTC = timezone.utc
CH = 'UC' + 'a' * 22
T0 = datetime(2026, 11, 13, 10, 0, tzinfo=UTC)


class FakeYouTube:
    def __init__(self):
        self.threads_list, self.replies, self.comments, self.fail = [], [], [], False

    def add(self, cid, text, at, author='UCviewer' + 'x' * 15, replied=False, video='vid00000001'):
        th = dict(snippet=dict(topLevelComment=dict(id=cid, snippet=dict(textDisplay=text, publishedAt=at.isoformat(), videoId=video,
                                                                         authorDisplayName='Viewer', authorChannelId=dict(value=author)))))
        if replied:
            th['replies'] = dict(comments=[dict(snippet=dict(authorChannelId=dict(value=CH)))])
        self.threads_list.insert(0, th)   # newest first, as order=time returns them

    def threads(self, channel_id, page_token=None):
        return dict(items=self.threads_list)

    def reply(self, parent_id, text):
        if self.fail:
            raise OSError('network')
        self.replies.append((parent_id, text))
        return dict(id='r' + parent_id)

    def get_comment(self, comment_id):
        # Reads back what reply() posted, as YouTube's comments.list does.
        for parent, text in self.replies:
            if 'r' + parent == comment_id:
                return dict(id=comment_id, snippet=dict(parentId=parent, textOriginal=text, authorChannelId=dict(value=CH)))
        return None

    def comment(self, channel_id, video_id, text):
        self.comments.append((video_id, text))
        return dict(id='top1')

    def channel(self):
        return getattr(self, 'me', dict(id=CH, title='CareerOS'))


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.yt = FakeYouTube()
        self.now = T0
        self.reads = {}
        self.told = []
        self.jobs = {}
        self.c = Y.Comments(self.tmp.name, self.yt, classify=lambda t: self.reads.get(t, dict(kind='praise', sure=True)),
                            draft=lambda it: 'Thanks for asking. The short answer is to start with the conclusion.',
                            clock=lambda: self.now, rng=random.Random(4), publishing=type('P', (), {'get': lambda s, j: self.jobs.get(j)})(),
                            notify=lambda who, text, key: self.told.append((who, text, key)))
        self.c.activate(CH)

    def tearDown(self):
        self.tmp.cleanup()


class ForwardOnly(Base):
    def test_nothing_from_before_switch_on_is_ever_read(self):
        self.yt.add('old1', 'Great video!', T0 - timedelta(days=30))
        self.yt.add('old2', 'Great video, thanks!', T0 - timedelta(minutes=1))
        self.yt.add('new1', 'Great video, thank you!', T0 + timedelta(minutes=5))
        self.now = T0 + timedelta(minutes=6)
        new = self.c.scan()
        self.assertEqual([x['id'] for x in new], ['new1'])
        self.assertNotIn('old1', self.c.items())
        self.assertEqual(self.c.scan(), [])                                       # the same comment is never taken twice

    def test_off_reads_nothing(self):
        self.c.deactivate()
        self.yt.add('n1', 'Great video!', T0 + timedelta(minutes=5))
        self.assertEqual(self.c.scan(), [])


class Thanks(Base):
    def test_clear_praise_is_thanked_later_not_straight_away(self):
        self.yt.add('p1', 'Great video, this really helped me', T0 + timedelta(minutes=1))
        self.now = T0 + timedelta(minutes=2)
        self.c.scan()
        self.assertEqual(self.c.tick(), [])                                        # never instant
        it = self.c.items()['p1']
        self.assertEqual(it['state'], 'thanks_due')
        due = Y._ts(it['due_at'])
        self.assertTrue(timedelta(minutes=20) <= due - self.now <= timedelta(minutes=90))
        self.now = due + timedelta(seconds=1)
        self.c.tick()
        self.assertEqual(self.yt.replies, [('p1', it['reply'])])
        self.assertIn(it['reply'], Y.THANKS)
        self.c.tick()
        self.assertEqual(len(self.yt.replies), 1)                                  # once only

    def test_both_checks_must_agree(self):
        self.yt.add('q1', 'Great video but how do I use this with my boss?', T0 + timedelta(minutes=1))
        self.yt.add('p2', 'Great video!', T0 + timedelta(minutes=2))
        self.reads['Great video!'] = dict(kind='praise', sure=False)                 # Anouk unsure
        self.now = T0 + timedelta(minutes=3)
        self.c.scan()
        self.assertEqual({k: v['state'] for k, v in self.c.items().items()}, {'q1': 'needs_josh', 'p2': 'needs_josh'})

    def test_rate_limits_one_per_person_and_already_replied(self):
        for i in range(8):
            self.yt.add(f'p{i}', 'Thank you, so helpful', T0 + timedelta(minutes=1, seconds=i), author=f'UCperson{i:02d}' + 'x' * 14)
        self.yt.add('same', 'Brilliant, thanks', T0 + timedelta(minutes=2), author='UCperson00' + 'x' * 14)
        self.yt.add('done', 'Great video', T0 + timedelta(minutes=3), replied=True)
        self.now = T0 + timedelta(minutes=4)
        self.c.scan()
        self.assertEqual(self.c.items()['done']['state'], 'skipped')
        self.now = T0 + timedelta(hours=2)
        self.c.tick()
        self.assertEqual(len(self.yt.replies), Y.AUTO_PER_HOUR)
        self.now = T0 + timedelta(hours=4)
        self.c.tick()
        authors = [self.c.items()[cid]['author_channel'] for cid, _ in self.yt.replies]
        self.assertEqual(len(authors), len(set(authors)))                           # one thank-you per person a day

    def test_an_unclear_send_is_never_repeated(self):
        self.yt.add('p1', 'Great video', T0 + timedelta(minutes=1))
        self.now = T0 + timedelta(minutes=2)
        self.c.scan()
        self.yt.fail = True
        self.now = T0 + timedelta(hours=3)
        self.c.tick()
        self.assertEqual(self.c.items()['p1']['state'], 'uncertain')
        self.yt.fail = False
        self.c.tick()
        self.assertEqual(self.yt.replies, [])


class NeedsJosh(Base):
    def test_a_question_waits_for_the_exact_approved_reply(self):
        self.yt.add('q1', 'How would you handle this if your boss interrupts?', T0 + timedelta(minutes=1))
        self.now = T0 + timedelta(minutes=2)
        self.c.scan()
        it = self.c.items()['q1']
        self.assertEqual((it['state'], self.yt.replies), ('needs_josh', []))
        self.assertTrue(self.told and it['code'] in self.told[0][1])
        with self.assertRaises(ValueError):
            self.c.approve('q1', 'WRONG1')
        with self.assertRaises(ValueError):
            self.c.approve('q1', it['code'], text='A different reply')               # changing the text changes the code
        it = self.c.items()['q1']
        out = self.c.approve('q1', it['code'])
        self.assertEqual((out['state'], self.yt.replies), ('replied', [('q1', 'A different reply')]))
        with self.assertRaises(ValueError):
            self.c.approve('q1', it['code'])                                          # once only

    def test_pitches_complaints_and_sensitive_topics_never_go_on_their_own(self):
        for i, text in enumerate(('Great video, check out my channel', 'Great video but this is wrong', 'Thanks, I got fired today',
                                  'Love this, DM me for a collab')):
            self.yt.add(f'x{i}', text, T0 + timedelta(minutes=1, seconds=i))
        self.now = T0 + timedelta(minutes=2)
        self.c.scan()
        self.assertTrue(all(v['state'] == 'needs_josh' for v in self.c.items().values()))
        self.now = T0 + timedelta(hours=5)
        self.c.tick()
        self.assertEqual(self.yt.replies, [])


class Narrow(unittest.TestCase):
    def test_the_client_can_only_list_reply_and_comment(self):
        public = {n for n in dir(Y.Client) if not n.startswith('_')}
        # get_comment only reads one comment back, to confirm a posted reply (16 Nov 2026).
        self.assertEqual(public, {'channel', 'threads', 'reply', 'comment', 'get_comment', 'video', 'video_threads', 'get_thread'})
        self.assertIn("self._call('GET', 'comments'", (ROOT / 'studio' / 'youtube_comments.py').read_text(encoding='utf-8'))
        src = (ROOT / 'studio' / 'youtube_comments.py').read_text(encoding='utf-8')
        for m in ('setModerationStatus', 'markAsSpam', "'DELETE'", 'rating'):
            self.assertNotIn(m, src)

    def test_thanks_follow_the_writing_rules(self):
        for t in Y.THANKS:
            low = t.lower()
            self.assertFalse(any(b in low for b in ('quiet', 'honest', '\u2014')), t)
            self.assertNotRegex(low, r'\bland(ed)?\b')
            self.assertLessEqual(len(t), 80)


class LeadMagnet(unittest.TestCase):
    def setUp(self):
        from unittest.mock import Mock
        from contextlib import nullcontext
        from studio.youtube_release import digest
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.text='Get the pack https://linklr.app/r/abc123';self.vid='vid00000001'
        self.link=dict(source_type='pinned_comment',video_id=self.vid,url='https://linklr.app/r/abc123')
        self.packet=dict(channel_id=CH,pinned_comment=self.text);self.packet['packet_digest']=digest(self.packet)
        self.job=dict(packet=self.packet,approval={'digest':self.packet['packet_digest']},video_id=self.vid,state='published')
        self.pub=Mock();self.pub._load.return_value=self.job;self.pub.locked.side_effect=lambda _:nullcontext()
        self.yt=Mock();self.yt.channel.return_value={'id':CH}
        self.yt.video.return_value=dict(id=self.vid,snippet={'channelId':CH},status={'privacyStatus':'public'})
        self.yt.video_threads.return_value={'items':[]}
        self.yt.comment.return_value={'id':'thread1','snippet':{'topLevelComment':{'id':'comment1'}}}
        self.yt.get_comment.return_value={'id':'comment1','snippet':dict(textOriginal=self.text,authorChannelId={'value':CH})}
        self.yt.get_thread.return_value={'id':'thread1','snippet':{'videoId':self.vid,'topLevelComment':self.yt.get_comment.return_value}}
        self.c=Y.Comments(self.tmp.name,self.yt,publishing=self.pub);self.c.expected_channel=CH
    def send(self,text=None):return self.c.lead_magnet_comment('job',self.vid,text or self.text,self.link)
    def test_exact_readback_and_dedupe(self):
        r=self.send();self.assertEqual(r['state'],'posted');self.assertFalse(r['pinned']);self.assertEqual(r['comment_id'],'comment1')
        self.send();self.yt.comment.assert_called_once()
    def test_scheduled_and_private_do_not_post(self):
        self.job['state']='scheduled'
        with self.assertRaises(ValueError):self.send()
        self.job['state']='published';self.yt.video.return_value['status']['privacyStatus']='private'
        with self.assertRaises(ValueError):self.send()
        self.yt.comment.assert_not_called()
    def test_wrong_channel_and_changed_text(self):
        with self.assertRaises(ValueError):self.send('Different words')
        self.yt.channel.return_value={'id':'wrong'}
        with self.assertRaises(ValueError):self.send()
        self.yt.comment.assert_not_called()
    def test_timeout_never_reposts(self):
        self.yt.comment.side_effect=TimeoutError()
        self.assertEqual(self.send()['state'],'uncertain');self.assertEqual(self.send()['state'],'uncertain')
        self.yt.comment.assert_called_once()
    def test_existing_exact_comment_reconciles_without_post(self):
        self.yt.video_threads.return_value={'items':[{'snippet':{'topLevelComment':self.yt.get_comment.return_value}}]}
        self.assertEqual(self.send()['comment_id'],'comment1');self.yt.comment.assert_not_called()
    def test_tampered_packet_rejected(self):
        self.packet['pinned_comment']='Changed'
        with self.assertRaises(ValueError):self.send()
        self.yt.comment.assert_not_called()


class SamReleaseEntry(LeadMagnet):
    def test_sam_entry_uses_approved_copy(self):
        result = self.c.post_approved_release_comment('a' * 32)
        self.assertEqual(result['state'], 'posted')
        self.assertFalse(result['pinned'])
        self.yt.comment.assert_called_once_with(CH, self.vid, self.text)

    def test_sam_entry_refuses_unknown_id(self):
        with self.assertRaises(ValueError): self.c.post_approved_release_comment('../job')
        self.yt.comment.assert_not_called()

    def test_sam_entry_refuses_ambiguous_links(self):
        self.packet['pinned_comment'] = self.text + ' https://linklr.app/r/second'
        with self.assertRaises(ValueError): self.c.post_approved_release_comment('a' * 32)
        self.yt.comment.assert_not_called()

    def test_sam_entry_does_not_approve(self):
        self.job['approval'] = {}
        with self.assertRaises(ValueError): self.c.post_approved_release_comment('a' * 32)
        self.yt.comment.assert_not_called()


class Controls(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.now = T0

    def tearDown(self):
        self.tmp.cleanup()

    def test_on_needs_the_connection_and_the_channel_it_reports(self):
        off = Y.Comments(self.tmp.name, None, clock=lambda: self.now)
        with self.assertRaises(ValueError):
            off.enable()
        self.assertIn("isn't set up", off.status_line())
        yt = FakeYouTube()
        yt.me = None
        c = Y.Comments(self.tmp.name, yt, clock=lambda: self.now)
        c.expected_channel = CH
        with self.assertRaises(ValueError):
            c.enable()                                                               # no channel, stays off
        yt.me = dict(id='UC' + 'b' * 22, title='Someone else')
        with self.assertRaises(ValueError):
            c.enable()                                                               # another channel, stays off
        self.assertFalse(c.settings().get('enabled'))
        yt.me = dict(id=CH, title='CareerOS')
        s = c.enable()
        self.assertEqual((s['channel_id'], s['activated_at']), (CH, T0.isoformat()))  # the cutoff is the moment it was switched on
        self.assertIn('on for CareerOS', c.status_line())
        c.deactivate()
        self.assertIn('off', c.status_line())

    def test_the_pin_only_counts_when_seen(self):
        shot = Path(self.tmp.name) / 'pin.png'
        with self.assertRaises(ValueError):
            Y.record_studio_step(self.tmp.name, 'vid00000001', 'pin_comment', dict(url='https://www.youtube.com/watch?v=vid00000001',
                                                                                    observed='comment posted', screenshot=str(shot)))
        shot.write_bytes(b'png')
        with self.assertRaises(ValueError):
            Y.record_studio_step(self.tmp.name, 'vid00000001', 'pin_comment', dict(url='https://studio.youtube.com/x', observed='Pinned by CareerOS',
                                                                                    screenshot=str(shot)))     # not the public page
        rec = Y.record_studio_step(self.tmp.name, 'vid00000001', 'pin_comment', dict(url='https://www.youtube.com/watch?v=vid00000001',
                                                                                      observed='Pinned by @CareerOS', screenshot=str(shot)))
        self.assertIn('pin_comment', rec)
        task = Y.studio_task('vid00000001', 'top1')
        self.assertTrue(any('Pinned by' in x for x in task['steps']) and any('Do not reply to, delete, heart' in x for x in task['steps']))

    def test_consent_never_overwrites_an_existing_connection(self):
        from studio.youtube_comments_auth import CommentsAuth, CommentsAuthError, SCOPES
        a = CommentsAuth(self.tmp.name)
        self.assertEqual(SCOPES, {'https://www.googleapis.com/auth/youtube.force-ssl'})
        self.assertEqual(a.client_file(), Path(self.tmp.name) / 'youtube-publishing' / 'desktop-client.json')
        a.path.parent.mkdir(parents=True)
        a.path.write_bytes(b'existing')
        with self.assertRaises(CommentsAuthError):
            a.consent()
        with self.assertRaises(CommentsAuthError):
            a.connect('x.json')
        self.assertEqual(a.path.read_bytes(), b'existing')


if __name__ == '__main__':
    unittest.main()


class SharedConnection(unittest.TestCase):
    """The edit grant lends Anouk a token only while it holds youtube.force-ssl and names Josh's channel."""

    def grant(self, scope, channel, connected=True):
        from studio.youtube_comments_auth import SharedCommentsAuth
        calls = []
        m = type('M', (), {'connected': connected, '_load': lambda s: dict(scope=scope), 'verified_channel': lambda s: channel,
                           'token': lambda s: calls.append(1) or 'tok'})()
        return SharedCommentsAuth(None, expected_channel=CH, manage=m), calls

    def test_used_only_with_force_ssl_on_joshs_channel(self):
        from studio.youtube_comments_auth import CommentsAuthError
        FS = 'https://www.googleapis.com/auth/youtube.force-ssl'
        ok, calls = self.grant(FS + ' https://www.googleapis.com/auth/youtube.upload', CH)
        self.assertTrue(ok.connected)
        self.assertEqual(ok.granted(), {FS})
        self.assertEqual(ok.token(), 'tok')
        for scope, ch, conn in ((' https://www.googleapis.com/auth/youtube.upload', CH, True), (FS, 'UC' + 'b' * 22, True), (FS, CH, False)):
            bad, calls = self.grant(scope, ch, conn)
            self.assertFalse(bad.connected)
            with self.assertRaises(CommentsAuthError):
                bad.token()
            self.assertEqual(calls, [])                                              # no token is ever fetched

    def test_a_dedicated_comments_grant_wins(self):
        from studio import youtube_comments_auth as A
        with tempfile.TemporaryDirectory() as d:
            self.assertIsInstance(A.comments_auth(d), A.SharedCommentsAuth)


class ScanRecord(Base):
    """Each scan leaves last-scan.json: when it ran, what it read, where it stopped at the switch-on, and what it found."""

    def test_the_record_shows_the_cutoff_was_kept(self):
        self.yt.add('old2', 'Loved it', T0 - timedelta(days=3))
        self.yt.add('old1', 'Great video, thanks!', T0 - timedelta(minutes=5))   # the fake lists the last added first
        self.now = T0 + timedelta(minutes=10)
        self.c.scan()
        r = self.c.last_scan()
        self.assertEqual((r['result'], r['pages'], r['threads_read'], r['new'], r['queued_thanks']), ('ok', 1, 1, 0, 0))
        self.assertEqual(r['stopped_at'], (T0 - timedelta(minutes=5)).isoformat())     # stopped at the first old thread
        self.assertEqual(self.c.items(), {})                                            # nothing old was queued
        self.yt.add('new1', 'Great video, really helped', T0 + timedelta(minutes=8))
        self.c.scan()
        r = self.c.last_scan()
        self.assertEqual((r['new'], r['queued_thanks']), (1, 1))
        self.assertEqual(self.c.status()['last_scan']['new'], 1)
        self.assertEqual(self.yt.replies, [])                                           # queued for later, never sent at once
