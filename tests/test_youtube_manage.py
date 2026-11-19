"""Changes to existing YouTube videos (studio/youtube_manage.py) against a fake YouTube. No network, no real video."""
import copy
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from studio import youtube_manage as ym
from studio.youtube_publish_auth import PublishingAuthError

CH = ym.JOSH_CHANNEL
VID = 'abcdefghijk'


def video(**over):
    v = dict(id=VID, etag='e1', snippet=dict(publishedAt='2026-11-12T10:00:00Z', channelId=CH, title='Old title', description='Old text',
                                             thumbnails={'default': {}}, channelTitle='Josh', tags=['career', 'promotion'], categoryId='27',
                                             liveBroadcastContent='none', defaultLanguage='en', defaultAudioLanguage='en', localized={'title': 'x'}),
             status=dict(uploadStatus='processed', privacyStatus='private', license='youtube', embeddable=True, publicStatsViewable=True,
                         selfDeclaredMadeForKids=False, madeForKids=False, containsSyntheticMedia=False))
    for k, x in over.items():
        part, field = k.split('__')
        v[part][field] = x
    return v


class FakeYouTube:
    def __init__(self, channel=CH, vid=None):
        self.ch, self.v = channel, vid or video()
        self.updates, self.thumbs = [], []

    def channel(self):
        return self.ch

    def read(self, video_id):
        if video_id != VID:
            raise ym.UpdateError('no such video')
        return copy.deepcopy(self.v)

    def update(self, video_id, parts, snippet=None, status=None):
        self.updates.append(dict(parts=parts, snippet=copy.deepcopy(snippet), status=copy.deepcopy(status)))
        if snippet is not None:                      # YouTube replaces the whole snippet with what was sent
            keep = {k: self.v['snippet'][k] for k in ym.READ_ONLY_SNIPPET if k in self.v['snippet']}
            self.v['snippet'] = dict(snippet, **keep)
        if status is not None:
            keep = {k: self.v['status'][k] for k in ('uploadStatus', 'madeForKids')}
            self.v['status'] = dict(status, **keep)
        return {}

    def thumbnail(self, video_id, asset):
        self.thumbs.append(asset['sha256'])


class Auth:
    def __init__(self, connected=True, channel=CH):
        self.connected, self.ch = connected, channel

    def verified_channel(self):
        return self.ch if self.connected else None


class Service(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='yt-manage-'))
        self.yt = FakeYouTube()

    def svc(self, edit=True, read=True):
        return ym.UpdateService(self.tmp, manage_auth=Auth(edit), read_auth=Auth(read), editor=self.yt, read_editor=self.yt)

    def test_prepare_reads_the_video_and_refuses_the_wrong_channel(self):
        s = self.svc()
        job = s.prepare(VID, dict(title='New title'))
        self.assertEqual((job['state'], job['before']['title'], job['changes']), ('needs_review', 'Old title', dict(title='New title')))
        self.assertEqual(s.prepare(VID, dict(title='New title'))['id'], job['id'])             # the same change is prepared once
        self.yt.v['snippet']['channelId'] = 'UCsomeoneelse000000000000'
        with self.assertRaises(ym.UpdateError):
            s.prepare(VID, dict(title='Another'))
        self.yt.v['snippet']['channelId'] = CH
        self.yt.ch = 'UCsomeoneelse000000000000'
        with self.assertRaises(ym.UpdateError):
            s.prepare(VID, dict(title='Another'))
        self.assertEqual(self.yt.updates, [])

    def test_bad_or_empty_changes_are_refused(self):
        s = self.svc()
        for ch in (dict(title='Old title'), dict(title=''), dict(title='x' * 101), dict(tags=['a']), dict(privacy='secret'),
                   dict(description='a <b> c'), {}):
            with self.assertRaises(ym.UpdateError, msg=str(ch)):
                s.prepare(VID, ch)
        later = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        self.yt.v['status']['privacyStatus'] = 'public'
        with self.assertRaises(ym.UpdateError):
            s.prepare(VID, dict(publish_at=later))                                             # can't schedule a published video
        self.yt.v['status']['privacyStatus'] = 'private'
        with self.assertRaises(ym.UpdateError):
            s.prepare(VID, dict(publish_at=later, privacy='public'))
        with self.assertRaises(ym.UpdateError):
            s.prepare(VID, dict(publish_at=(datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()))

    def test_approval_is_for_the_exact_version(self):
        s = self.svc()
        job = s.prepare(VID, dict(title='New title'))
        with self.assertRaises(ym.UpdateError):
            s.approve(job['id'], 'not-this-version')
        with self.assertRaises(ym.UpdateError):
            s.apply(job['id'])                                                                 # never without approval
        self.assertEqual(self.yt.updates, [])

    def test_update_keeps_every_other_detail(self):
        s = self.svc()
        job = s.prepare(VID, dict(title='New title', description='New text'))
        s.approve(job['id'], job['digest'])
        out = s.apply(job['id'])
        self.assertEqual(out['state'], 'applied', out)
        sent = self.yt.updates[0]
        self.assertEqual(sent['parts'], ['snippet'])
        self.assertEqual(sent['snippet']['tags'], ['career', 'promotion'])
        self.assertEqual((sent['snippet']['categoryId'], sent['snippet']['defaultLanguage'], sent['snippet']['defaultAudioLanguage']), ('27', 'en', 'en'))
        for k in ym.READ_ONLY_SNIPPET:
            self.assertNotIn(k, sent['snippet'])
        self.assertEqual(self.yt.v['snippet']['title'], 'New title')
        self.assertEqual(self.yt.v['status']['privacyStatus'], 'private')                    # status untouched

    def test_schedule_keeps_disclosures_and_settings(self):
        s = self.svc()
        at = (datetime.now(timezone.utc) + timedelta(days=2)).replace(microsecond=0)
        job = s.prepare(VID, dict(publish_at=at.isoformat()))
        s.approve(job['id'], job['digest'])
        out = s.apply(job['id'])
        self.assertEqual(out['state'], 'applied', out)
        st = self.yt.updates[0]['status']
        self.assertEqual((st['privacyStatus'], st['embeddable'], st['license'], st['selfDeclaredMadeForKids'], st['containsSyntheticMedia']),
                         ('private', True, 'youtube', False, False))
        self.assertNotIn('uploadStatus', st)
        self.assertEqual(ym._iso(self.yt.v['status']['publishAt']), at)

    def test_making_public_drops_a_schedule(self):
        self.yt.v['status']['publishAt'] = '2026-12-01T10:00:00Z'
        s = self.svc()
        job = s.prepare(VID, dict(privacy='public'))
        s.approve(job['id'], job['digest'])
        self.assertEqual(s.apply(job['id'])['state'], 'applied')
        self.assertNotIn('publishAt', self.yt.updates[0]['status'])

    def test_a_video_changed_elsewhere_is_not_overwritten(self):
        s = self.svc()
        job = s.prepare(VID, dict(title='New title'))
        s.approve(job['id'], job['digest'])
        self.yt.v['snippet']['title'] = 'Josh changed it in Studio'
        out = s.apply(job['id'])
        self.assertEqual(out['state'], 'stale')
        self.assertEqual(self.yt.updates, [])

    def test_retry_never_repeats_a_change(self):
        s = self.svc()
        job = s.prepare(VID, dict(title='New title'))
        s.approve(job['id'], job['digest'])
        s.apply(job['id'])
        s.apply(job['id'])
        self.assertEqual(len(self.yt.updates), 1)
        # an attempt that sent the change but stopped before recording it: the next one finds it done
        job2 = s.prepare(VID, dict(description='Second change'))
        s.approve(job2['id'], job2['digest'])
        self.yt.v['snippet']['description'] = 'Second change'                               # landed, then the run died
        out = s.apply(job2['id'])
        self.assertEqual(out['state'], 'applied')
        self.assertEqual(len(self.yt.updates), 1)

    def test_thumbnail_must_be_the_approved_file(self):
        img = self.tmp / 't.png'
        img.write_bytes(b'\x89PNG\r\n\x1a\n' + b'0' * 100)
        s = self.svc()
        job = s.prepare(VID, dict(thumbnail=str(img)))
        s.approve(job['id'], job['digest'])
        img.write_bytes(b'\x89PNG\r\n\x1a\n' + b'1' * 100)                                  # changed after approval
        out = s.apply(job['id'])
        self.assertEqual(out['state'], 'blocked')
        self.assertEqual(self.yt.thumbs, [])
        fake = self.tmp / 'f.png'
        fake.write_bytes(b'not an image')
        with self.assertRaises(ym.UpdateError):
            s.prepare(VID, dict(thumbnail=str(fake)))

    def test_without_the_edit_permission_studio_changes_are_checked_by_reading(self):
        s = self.svc(edit=False)
        self.assertFalse(s.status()['can_update'])
        self.assertTrue(s.status()['chrome_fallback'])
        job = s.prepare(VID, dict(title='New title'))
        with self.assertRaises(ym.UpdateError):
            s.verify_chrome(job['id'])                                                         # not approved: Studio is off limits
        s.approve(job['id'], job['digest'])
        self.assertEqual(s.apply(job['id'])['state'], 'approved_waiting')
        self.assertEqual(self.yt.updates, [])
        self.assertNotEqual(s.verify_chrome(job['id'])['state'], 'applied')                   # nothing done in Studio yet
        self.yt.v['snippet']['title'] = 'New title'                                            # Sam made it in Studio
        out = s.verify_chrome(job['id'], by='sam')
        self.assertEqual((out['state'], out['applied_by']), ('applied', 'chrome:sam'))


class ManageAuthTests(unittest.TestCase):
    def test_edit_permission_is_stored_only_for_the_expected_channel(self):
        tmp = Path(tempfile.mkdtemp(prefix='yt-auth-'))
        upload = tmp / 'youtube-publishing' / 'oauth.dpapi'
        upload.parent.mkdir(parents=True)
        upload.write_bytes(b'current upload credential')
        wrong = ym.ManageAuth(tmp, channel_lookup=lambda token: 'UCsomeoneelse000000000000')
        with self.assertRaises(PublishingAuthError):
            wrong.before_save(dict(refresh_token='r'), dict(access_token='a'))
        right = ym.ManageAuth(tmp, channel_lookup=lambda token: CH)
        body = right.before_save(dict(client_id='c', client_secret='s', refresh_token='r', scope=' '.join(sorted(ym.MANAGE_SCOPES))), dict(access_token='a'))
        self.assertEqual(body['verified_channel'], CH)
        right._save(body)
        self.assertTrue(right.path.name == 'oauth-manage.dpapi' and right.path.is_file())
        self.assertEqual(right.verified_channel(), CH)
        self.assertEqual(upload.read_bytes(), b'current upload credential')                   # the upload credential is untouched
        self.assertEqual(set(right._load()['scope'].split()), ym.MANAGE_SCOPES)

    def test_scopes_add_only_force_ssl(self):
        from studio.youtube_publish_auth import SCOPES
        self.assertEqual(ym.MANAGE_SCOPES - SCOPES, {ym.FORCE_SSL})


if __name__ == '__main__':
    unittest.main()
