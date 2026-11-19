"""Sam's edits to videos already on Josh's channel: title, description, thumbnail, privacy and release time.

Nothing here uploads a video or runs at import. Every change goes prepare -> Josh reviews the exact change in Josh OS ->
Josh approves that exact version -> Josh OS applies it and reads it back.

Permissions (Google's own docs, checked 12 Nov 2026):
- videos.update needs youtube, youtube.force-ssl or youtubepartner. The publishing grant (youtube.upload and
  youtube.readonly) can't update a video, so editing adds youtube.force-ssl, the narrowest of those.
- thumbnails.set works with youtube.upload (or any of the above). Up to 50 MB, JPEG or PNG.
- An update deletes any snippet or status field left out of the body (except selfDeclaredMadeForKids and
  containsSyntheticMedia, which only change when sent). So every update sends the video's current snippet and status
  with only the approved fields changed.
- status.publishAt needs privacyStatus private and a video that has never been published.

The edit grant is its own credential, Data/youtube-publishing/oauth-manage.dpapi. The upload credential (oauth.dpapi) is
never changed. The edit grant is stored only after Google says it belongs to the expected channel.

Chrome fallback: when the edit grant isn't connected, an approved change may be made by Sam in YouTube Studio in
Chrome, and only then. Josh OS checks the result with the read-only grant before it counts as done.
"""
import argparse
import hashlib
import json
import re
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode

from .youtube_publish_auth import PublishingAuth, PublishingAuthError, SCOPES as UPLOAD_SCOPES
from .youtube_publishing import API, UPLOAD, PublishingError, YouTubePublisher

FORCE_SSL = 'https://www.googleapis.com/auth/youtube.force-ssl'
MANAGE_SCOPES = frozenset(UPLOAD_SCOPES | {FORCE_SSL})
JOSH_CHANNEL = 'UC3F6JjRjqDNrK_QzvyGwppA'
FIELDS = ('title', 'description', 'thumbnail', 'privacy', 'publish_at')
PRIVACY = ('private', 'unlisted', 'public')
# Fields YouTube reports but won't take back in an update
READ_ONLY_SNIPPET = ('publishedAt', 'channelId', 'thumbnails', 'channelTitle', 'liveBroadcastContent', 'localized')
WRITABLE_STATUS = ('privacyStatus', 'publishAt', 'license', 'embeddable', 'publicStatsViewable', 'selfDeclaredMadeForKids',
                   'containsSyntheticMedia')


class UpdateError(ValueError):
    pass


def utcnow():
    return datetime.now(timezone.utc)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()


class ManageAuth(PublishingAuth):
    """The edit grant: upload, read-only and youtube.force-ssl, in its own file, for one expected channel."""
    SCOPES = MANAGE_SCOPES
    FILE = 'oauth-manage.dpapi'

    def __init__(self, data, expected_channel=JOSH_CHANNEL, channel_lookup=None):
        super().__init__(data)
        self.expected_channel = expected_channel
        self._lookup = channel_lookup or self._channel_of

    @staticmethod
    def _channel_of(access_token):
        class _Once:
            def token(self):
                return access_token
        return YouTubePublisher(_Once()).channel()

    def before_save(self, body, reply):
        try:
            channel = self._lookup(reply['access_token'])
        except Exception:
            raise PublishingAuthError('Google gave access, but the channel could not be checked, so nothing was saved.') from None
        if channel != self.expected_channel:
            raise PublishingAuthError(f'That Google account manages channel {channel}, not {self.expected_channel}. Nothing was saved. '
                                      'Sign in with the account that owns the channel and try again.')
        return dict(body, verified_channel=channel, verified_at=utcnow().isoformat())

    def verified_channel(self):
        try:
            return self._load().get('verified_channel')
        except PublishingAuthError:
            return None


class VideoEditor:
    """The YouTube calls. Reading needs any grant with youtube.readonly; updating needs the edit grant."""

    def __init__(self, auth, opener=None):
        self.api = YouTubePublisher(auth, opener)

    def channel(self):
        return self.api.channel()

    def read(self, video_id):
        _, _, body = self.api.request('GET', API + 'videos?' + urlencode(dict(id=video_id, part='snippet,status')))
        items = body.get('items') or []
        if len(items) != 1:
            raise UpdateError(f'YouTube has no video {video_id} for this account.')
        return items[0]

    def update(self, video_id, parts, snippet=None, status=None):
        body = dict(id=video_id)
        if snippet is not None:
            body['snippet'] = snippet
        if status is not None:
            body['status'] = status
        _, _, out = self.api.request('PUT', API + 'videos?' + urlencode(dict(part=','.join(parts))), json.dumps(body).encode('utf-8'),
                                     {'Content-Type': 'application/json; charset=UTF-8'})
        return out

    def thumbnail(self, video_id, asset):
        self.api.thumbnail(video_id, asset)


def visible(video):
    """The fields Josh reviews, as they are on YouTube now."""
    sn, st = video.get('snippet') or {}, video.get('status') or {}
    return dict(title=sn.get('title'), description=sn.get('description'), privacy=st.get('privacyStatus'), publish_at=st.get('publishAt'),
                channel=sn.get('channelId'), upload_status=st.get('uploadStatus'))


def _iso(t):
    return datetime.fromisoformat(str(t).replace('Z', '+00:00'))


class UpdateService:
    def __init__(self, data, manage_auth=None, read_auth=None, editor=None, read_editor=None, expected_channel=JOSH_CHANNEL):
        self.root = Path(data) / 'youtube-publishing'
        self.root.mkdir(parents=True, exist_ok=True)
        self.db = self.root / 'updates.sqlite'
        self.expected = expected_channel
        self.manage_auth = manage_auth if manage_auth is not None else ManageAuth(data, expected_channel)
        self.read_auth = read_auth if read_auth is not None else PublishingAuth(data)
        self._editor, self._read_editor = editor, read_editor
        self.lock = threading.RLock()
        with self.connect() as con:
            con.execute('CREATE TABLE IF NOT EXISTS updates (id TEXT PRIMARY KEY, digest TEXT UNIQUE NOT NULL, body TEXT NOT NULL)')

    # ------------------------------------------------------------------ storage and grants
    @contextmanager
    def connect(self):
        con = sqlite3.connect(self.db, timeout=15)
        try:
            with con:
                yield con
        finally:
            con.close()

    def _load(self, jid):
        if not re.fullmatch(r'[a-f0-9]{32}', str(jid)):
            raise KeyError('Unknown update')
        with self.connect() as con:
            row = con.execute('SELECT body FROM updates WHERE id=?', (jid,)).fetchone()
        if not row:
            raise KeyError('Unknown update')
        return json.loads(row[0])

    def _save(self, job):
        job['updated_at'] = utcnow().isoformat()
        with self.connect() as con:
            con.execute('UPDATE updates SET body=? WHERE id=?', (json.dumps(job), job['id']))

    def get(self, jid):
        return {k: v for k, v in self._load(jid).items() if k != 'approval_secret'}

    def can_update(self):
        return self.manage_auth.connected and self.manage_auth.verified_channel() == self.expected

    def editor(self):
        if self._editor is None:
            self._editor = VideoEditor(self.manage_auth)
        return self._editor

    def reader(self):
        """Reading uses the edit grant when it is connected, else the read-only publishing grant."""
        if self.can_update():
            return self.editor()
        if self._read_editor is None:
            if not self.read_auth.connected:
                raise UpdateError('No YouTube connection can read the channel yet.')
            self._read_editor = VideoEditor(self.read_auth)
        return self._read_editor

    def status(self):
        with self.connect() as con:
            ids = [r[0] for r in con.execute('SELECT id FROM updates ORDER BY rowid DESC LIMIT 50')]
        live = None
        f = self.root / 'manage-live-check.json'
        if f.is_file():
            try:
                live = json.loads(f.read_text(encoding='utf-8'))
            except ValueError:
                live = None
        return dict(expected_channel=self.expected, read_connected=self.read_auth.connected, edit_connected=self.manage_auth.connected,
                    edit_channel=self.manage_auth.verified_channel(), can_update=self.can_update(),
                    chrome_fallback=self.read_auth.connected or self.manage_auth.connected,
                    edit_scopes=sorted(s.rsplit('/', 1)[-1] for s in MANAGE_SCOPES), live_read_check=live,
                    updates=[self.get(i) for i in ids])

    def live_check(self, video_id=None):
        """Read only: which channel the connection reaches and, if given, one video's current details."""
        r = self.reader()
        channel = r.channel()
        out = dict(at=utcnow().isoformat(), channel=channel, expected=self.expected, channel_ok=channel == self.expected,
                   using='edit grant' if self.can_update() else 'read-only publishing grant')
        if video_id:
            v = r.read(video_id)
            out['video'] = dict(id=video_id, **{k: v2 for k, v2 in visible(v).items() if k != 'description'},
                                description_chars=len((v.get('snippet') or {}).get('description') or ''))
        (self.root / 'manage-live-check.json').write_text(json.dumps(out, indent=1), encoding='utf-8')
        return out

    # ------------------------------------------------------------------ prepare and review
    def _check_changes(self, changes, now_video):
        if not isinstance(changes, dict) or not changes:
            raise UpdateError('Say what to change.')
        bad = set(changes) - set(FIELDS)
        if bad:
            raise UpdateError(f"Only {', '.join(FIELDS)} can be changed here, not {', '.join(sorted(bad))}.")
        out = {}
        if 'title' in changes:
            t = str(changes['title']).strip()
            if not t or len(t) > 100 or re.search(r'[<>]', t):
                raise UpdateError('A title is 1 to 100 characters, without < or >.')
            out['title'] = t
        if 'description' in changes:
            d = str(changes['description'])
            if len(d.encode('utf-8')) > 5000 or re.search(r'[<>]', d):
                raise UpdateError('A description is up to 5000 bytes, without < or >.')
            out['description'] = d
        if 'privacy' in changes:
            if changes['privacy'] not in PRIVACY:
                raise UpdateError('Privacy is private, unlisted or public.')
            out['privacy'] = changes['privacy']
        if 'publish_at' in changes and changes['publish_at'] is not None:
            try:
                at = _iso(changes['publish_at'])
                if at.tzinfo is None:
                    raise ValueError
            except ValueError:
                raise UpdateError('Give the release time with its time zone, like 2026-11-14T16:00:00+04:00.') from None
            if at < utcnow() + timedelta(minutes=30):
                raise UpdateError('A scheduled release must be at least 30 minutes away.')
            if out.get('privacy', now_video['status'].get('privacyStatus')) != 'private':
                raise UpdateError('YouTube only schedules a private video. Set privacy to private with the release time.')
            if now_video['status'].get('privacyStatus') != 'private':
                raise UpdateError('YouTube can only schedule a video that has never been published, and this one is not private now.')
            out['publish_at'] = at.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
        elif 'publish_at' in changes:
            out['publish_at'] = None                                   # clear a schedule
        if 'thumbnail' in changes:
            p = Path(str(changes['thumbnail']))
            if not p.is_file() or p.suffix.lower() not in ('.jpg', '.jpeg', '.png'):
                raise UpdateError('The thumbnail must be an existing JPEG or PNG file.')
            raw = p.read_bytes()
            if len(raw) > 50 * 1024 * 1024:
                raise UpdateError('The thumbnail is over the 50 MB limit.')
            if not (raw[:3] == b'\xff\xd8\xff' or raw[:8] == b'\x89PNG\r\n\x1a\n'):
                raise UpdateError('The thumbnail file is not really a JPEG or PNG.')
            out['thumbnail'] = dict(path=str(p), size=len(raw), sha256=hashlib.sha256(raw).hexdigest())
        return out

    def prepare(self, video_id, changes, by='sam', note=''):
        """A change for Josh to review. Reads the video now and keeps exactly what it was, so a later edit elsewhere
        makes this one stale. Prepares the same change only once."""
        if not re.fullmatch(r'[A-Za-z0-9_-]{11}', str(video_id or '')):
            raise UpdateError('That is not a YouTube video id.')
        r = self.reader()
        if r.channel() != self.expected:
            raise UpdateError(f'The YouTube connection reaches a different channel, not {self.expected}. Nothing was prepared.')
        video = r.read(video_id)
        before = visible(video)
        if before['channel'] != self.expected:
            raise UpdateError(f'Video {video_id} belongs to another channel, so it is not changed here.')
        want = self._check_changes(changes, video)
        if all(before.get(k) == v for k, v in want.items() if k != 'thumbnail') and 'thumbnail' not in want:
            raise UpdateError('YouTube already has those details, so there is nothing to change.')
        body = dict(video_id=video_id, channel=self.expected, before=before, etag=video.get('etag'), changes=want)
        d = digest(body)
        job = dict(id=uuid.uuid4().hex, digest=d, state='needs_review', created_at=utcnow().isoformat(), prepared_by=str(by)[:40],
                   note=str(note)[:500], applied_by=None, thumbnail_set=False, attempts=0, error=None, **body)
        with self.connect() as con:
            con.execute('INSERT OR IGNORE INTO updates VALUES(?,?,?)', (job['id'], d, json.dumps(job)))
            jid = con.execute('SELECT id FROM updates WHERE digest=?', (d,)).fetchone()[0]
        return self.get(jid)

    def approve(self, jid, version):
        """Josh's approval of this exact version. The server calls this only from the review page (cookie, same-origin,
        one-time review code). A model, script or chat message never counts."""
        with self.lock:
            job = self._load(jid)
            if job['digest'] != version:
                raise UpdateError('This change was edited after you opened it. Open it again and review what you see.')
            if job['state'] != 'needs_review':
                return self.get(jid)
            job['approval'] = dict(digest=version, at=utcnow().isoformat(), source='review_page')
            job['state'] = 'approved'
            self._save(job)
        return self.get(jid)

    # ------------------------------------------------------------------ apply
    def _desired(self, job, current):
        """The full snippet and status to send: the video as it is now, with only the approved fields changed."""
        sn = {k: v for k, v in (current.get('snippet') or {}).items() if k not in READ_ONLY_SNIPPET}
        st = {k: v for k, v in (current.get('status') or {}).items() if k in WRITABLE_STATUS}
        ch = job['changes']
        parts = []
        if 'title' in ch or 'description' in ch:
            if 'title' in ch:
                sn['title'] = ch['title']
            if 'description' in ch:
                sn['description'] = ch['description']
            if not sn.get('title') or not sn.get('categoryId'):
                raise UpdateError('YouTube did not return the video\'s title and category, so an update could erase details. Stopped.')
            parts.append('snippet')
        if 'privacy' in ch or 'publish_at' in ch:
            if 'privacy' in ch:
                st['privacyStatus'] = ch['privacy']
            if 'publish_at' in ch:
                if ch['publish_at'] is None:
                    st.pop('publishAt', None)
                else:
                    st['publishAt'] = ch['publish_at']
            if st.get('privacyStatus') != 'private':
                st.pop('publishAt', None)                               # a schedule only exists on a private video
            # these two only change when sent, so send them as they are to keep them
            for k in ('selfDeclaredMadeForKids', 'containsSyntheticMedia'):
                if k in (current.get('status') or {}):
                    st[k] = current['status'][k]
            parts.append('status')
        return parts, (sn if 'snippet' in parts else None), (st if 'status' in parts else None)

    @staticmethod
    def _matches(job, now):
        """YouTube shows every approved field as approved. A video made non-private keeps no release time."""
        ch = job['changes']
        if any(now.get(k) != v for k, v in ch.items() if k not in ('thumbnail', 'publish_at')):
            return False
        if 'publish_at' in ch and (now.get('publish_at') and _iso(now['publish_at'])) != (ch['publish_at'] and _iso(ch['publish_at'])):
            return False
        if ch.get('privacy') in ('unlisted', 'public') and now.get('publish_at'):
            return False
        return True

    def apply(self, jid):
        """Make the approved change through the API, once. Safe to call again: it reads YouTube first, does nothing if the
        change is already there, and refuses if the video changed since Josh reviewed it."""
        with self.lock:
            job = self._load(jid)
            if not job.get('approval') or job['approval']['digest'] != job['digest']:
                raise UpdateError('Josh has to approve this exact change first.')
            if job['state'] == 'applied':
                return self.get(jid)
            if not self.can_update():
                job['state'] = 'approved_waiting'
                job['error'] = ('The edit permission is not connected yet. Connect it, or make this exact change in YouTube Studio '
                                'and have Josh OS check it.')
                self._save(job)
                return self.get(jid)
            ed = self.editor()
            job['attempts'] += 1
            try:
                if ed.channel() != self.expected:
                    raise UpdateError('The edit permission reaches a different channel now. Nothing was changed.')
                current = ed.read(job['video_id'])
                now = visible(current)
                if now['channel'] != self.expected:
                    raise UpdateError('The video is on a different channel. Nothing was changed.')
                text_done = self._matches(job, now)
                if not text_done:
                    if any(now.get(k) != job['before'].get(k) for k in ('title', 'description', 'privacy', 'publish_at')):
                        job['state'] = 'stale'
                        raise UpdateError('The video changed on YouTube after Josh reviewed this change, so it was not applied. '
                                          'Prepare it again from what is there now.')
                    parts, sn, st = self._desired(job, current)
                    job['state'] = 'applying'
                    self._save(job)
                    ed.update(job['video_id'], parts, sn, st)
                if 'thumbnail' in job['changes'] and not job['thumbnail_set']:
                    t = job['changes']['thumbnail']
                    raw = Path(t['path']).read_bytes()
                    if hashlib.sha256(raw).hexdigest() != t['sha256']:
                        raise UpdateError('The thumbnail file changed after Josh approved it. Nothing more was sent.')
                    ed.thumbnail(job['video_id'], t)
                    job['thumbnail_set'] = True
                    self._save(job)
                after = visible(ed.read(job['video_id']))
                if not self._matches(job, after):
                    raise UpdateError('YouTube did not show the approved details after the update. Check the video in Studio.')
                job.update(state='applied', applied_by='api', after=after, applied_at=utcnow().isoformat(), error=None)
            except (UpdateError, PublishingError, PublishingAuthError) as e:
                if job['state'] not in ('stale',):
                    job['state'] = 'blocked'
                job['error'] = str(e)[:400]
            self._save(job)
        return self.get(jid)

    def verify_chrome(self, jid, by='sam'):
        """After Sam made an approved change in YouTube Studio: Josh OS reads the video and marks it done only if YouTube
        shows exactly the approved details. A thumbnail can't be compared this way, so it stays for Josh to confirm."""
        with self.lock:
            job = self._load(jid)
            if not job.get('approval') or job['approval']['digest'] != job['digest']:
                raise UpdateError('Only an approved change can be made in Studio.')
            if job['state'] == 'applied':
                return self.get(jid)
            r = self.reader()
            if r.channel() != self.expected:
                raise UpdateError('The connection reaches a different channel, so the change could not be checked.')
            now = visible(r.read(job['video_id']))
            if not self._matches(job, now):
                diff = {k: dict(approved=v, youtube=now.get(k)) for k, v in job['changes'].items() if k != 'thumbnail' and now.get(k) != v}
                job['error'] = 'YouTube does not show the approved change yet: ' + json.dumps(diff)[:300]
                self._save(job)
                return self.get(jid)
            job.update(state='applied', applied_by='chrome:' + str(by)[:30], after=now, applied_at=utcnow().isoformat(), error=None,
                       thumbnail_check='Josh confirms the thumbnail in Studio' if 'thumbnail' in job['changes'] else None)
            self._save(job)
        return self.get(jid)


def main():
    p = argparse.ArgumentParser(description='Connect the YouTube edit permission (adds youtube.force-ssl) in its own credential. '
                                            'The upload credential is not changed. Does not change any video.')
    p.add_argument('--connect', required=True, help='The downloaded Google Desktop OAuth client JSON')
    p.add_argument('--expect-channel', default=JOSH_CHANNEL)
    a = p.parse_args()
    try:
        ManageAuth(Path(__file__).resolve().parents[1] / 'Data', a.expect_channel).connect(a.connect)
        print(f'YouTube edit permission connected for channel {a.expect_channel}. No video was changed.')
    except PublishingAuthError as e:
        p.exit(1, str(e) + '\n')


if __name__ == '__main__':
    main()
