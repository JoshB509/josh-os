"""YouTube upload transport and durable, approval-gated publishing jobs.

No network or uploads at import/construction. No automatic background publishing.
"""
import base64
import hashlib
import json
import mimetypes
import os
import re
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, build_opener
from .youtube_analytics import _NoRedirect
from .youtube_auth import protect
from .youtube_release import prepare as prepare_packet, verify, ReleaseError

API = 'https://www.googleapis.com/youtube/v3/'
UPLOAD = 'https://www.googleapis.com/upload/youtube/v3/'
CHUNK = 8 * 1024 * 1024

class PublishingError(ValueError):
    pass


def utcnow():
    return datetime.now(timezone.utc).isoformat()


def safe_session(url):
    p = urlsplit(url)
    if p.scheme != 'https' or p.hostname != 'www.googleapis.com' or p.port not in (None,443) or p.username or p.password or not p.path.startswith('/upload/youtube/v3/videos') or p.fragment:
        raise PublishingError('Unexpected upload session destination; stopped without sending credentials.')
    return url


class YouTubePublisher:
    def __init__(self, auth, opener=None):
        self.auth = auth
        self.opener = opener or build_opener(_NoRedirect)

    def request(self, method, url, data=None, headers=None, allow_resume=False):
        req = Request(url, data=data, method=method, headers={
            'Authorization': 'Bearer ' + self.auth.token(), **(headers or {})})
        try:
            response = self.opener.open(req, timeout=120)
        except HTTPError as exc:
            if exc.code == 308 and allow_resume:
                response = exc
            else:
                code = exc.code
                exc.close()
                raise PublishingError('YouTube request failed (HTTP %s). Retry only this saved job after fixing access/quota; no new upload was created automatically.' % code) from None
        except (OSError, URLError):
            raise PublishingError('YouTube response is uncertain. Resume this saved job; do not create another upload.') from None
        with response:
            code, meta = response.code, dict(response.headers)
            raw = response.read(2 * 1024 * 1024)
        try:
            body = json.loads(raw) if raw else {}
        except ValueError:
            raise PublishingError('YouTube returned an unreadable response; keep this job for recovery.') from None
        return code, meta, body

    def channel(self):
        _, _, body = self.request('GET', API + 'channels?' + urlencode(dict(part='id,snippet', mine='true')))
        items = body.get('items') or []
        if len(items) != 1:
            raise PublishingError('Could not verify exactly one authorized YouTube channel.')
        return items[0]['id']

    def initiate(self, packet):
        # Never rely on Google's default, which can be PUBLIC.
        metadata = dict(snippet=dict(title=packet['title'], description=packet['description'],
            categoryId=packet['category_id'], defaultLanguage=packet['language']),
            status=dict(privacyStatus=packet.get('initial_privacy','private'),
                selfDeclaredMadeForKids=packet['made_for_kids'], containsSyntheticMedia=packet['contains_synthetic_media']),
            paidProductPlacementDetails=dict(hasPaidProductPlacement=packet['contains_paid_promotion']))
        if packet.get('initial_privacy') != 'unlisted':
            metadata['status']['publishAt'] = packet['schedule']['publish_at']
        _, headers, _ = self.request('POST', UPLOAD + 'videos?' + urlencode(dict(uploadType='resumable',
            part='snippet,status,paidProductPlacementDetails', notifySubscribers='true')),
            json.dumps(metadata).encode(), {'Content-Type':'application/json; charset=UTF-8',
            'X-Upload-Content-Type':'video/mp4', 'X-Upload-Content-Length':str(packet['video']['size'])})
        url = next((v for k,v in headers.items() if k.lower() == 'location'), None)
        if not url:
            raise PublishingError('Upload initialization was uncertain: no session URL. Reconcile in YouTube Studio before any new upload.')
        return safe_session(url)

    def session_status(self, session, total):
        return self.request('PUT', safe_session(session), b'',
            {'Content-Length':'0', 'Content-Range':'bytes */%d' % total}, True)

    @staticmethod
    def offset(headers):
        value = next((v for k,v in headers.items() if k.lower() == 'range'), '')
        if not value:
            return 0
        found = re.fullmatch(r'bytes=0-(\d+)', value)
        if not found:
            raise PublishingError('Unexpected resumable upload offset.')
        return int(found.group(1)) + 1

    def upload(self, session, packet):
        asset = packet['video']; total = asset['size']
        code, headers, body = self.session_status(session, total)
        if code in (200,201):
            return body['id']
        if code != 308:
            raise PublishingError('Unexpected upload recovery response.')
        offset = self.offset(headers)
        if not 0 <= offset < total:
            raise PublishingError('Upload completion is uncertain; keep the saved session for recovery.')
        # Rehash prefix and upload bytes; withhold final chunk if content changed.
        sha = hashlib.sha256()
        with Path(asset['path']).open('rb') as source:
            remaining = offset
            while remaining:
                block = source.read(min(CHUNK, remaining))
                if not block: raise PublishingError('Video changed during upload.')
                sha.update(block); remaining -= len(block)
            while offset < total:
                block = source.read(min(CHUNK, total-offset))
                if not block: raise PublishingError('Video changed during upload.')
                sha.update(block)
                end = offset + len(block)
                if end == total and packet.get('initial_privacy') != 'unlisted' and datetime.fromisoformat(packet['schedule']['publish_at'].replace('Z','+00:00')) <= datetime.now(timezone.utc) + timedelta(minutes=5):
                    raise PublishingError('Release is less than five minutes away. Final chunk withheld; inspect the saved upload before revising the schedule.')
                if end == total and (sha.hexdigest() != asset['sha256'] or source.read(1)):
                    raise PublishingError('Video bytes changed. Final chunk was not sent; prepare and review again.')
                code, headers, body = self.request('PUT', safe_session(session), block,
                    {'Content-Type':'video/mp4','Content-Length':str(len(block)),
                     'Content-Range':'bytes %d-%d/%d' % (offset,end-1,total)}, True)
                if code in (200,201):
                    if end != total or not body.get('id'):
                        raise PublishingError('Unexpected upload completion response.')
                    return body['id']
                if code != 308 or self.offset(headers) != end:
                    raise PublishingError('Upload offset is uncertain. Resume the existing saved session.')
                offset = end
        raise PublishingError('Upload completion is uncertain. Resume the existing saved session.')

    def thumbnail(self, video_id, asset):
        from PIL import Image
        with Image.open(asset['path']) as im:
            if im.format not in ('JPEG','PNG'):
                raise PublishingError('Use a JPEG or PNG primary thumbnail.')
            mime = 'image/jpeg' if im.format == 'JPEG' else 'image/png'
        raw = Path(asset['path']).read_bytes()
        if hashlib.sha256(raw).hexdigest() != asset['sha256']:
            raise PublishingError('Thumbnail changed after approval.')
        self.request('POST', UPLOAD + 'thumbnails/set?' + urlencode(dict(videoId=video_id)), raw,
                     {'Content-Type':mime})

    def inspect(self, video_id):
        _, _, body = self.request('GET', API + 'videos?' + urlencode(dict(id=video_id,
            part='snippet,status,processingDetails,paidProductPlacementDetails')))
        if len(body.get('items') or []) != 1:
            raise PublishingError('Uploaded video could not be verified. Keep its saved ID.')
        return body['items'][0]


def check_remote(packet, video, allow_published=False):
    snippet, status = video.get('snippet',{}), video.get('status',{})
    for key, expected in dict(channelId=packet['channel_id'], title=packet['title'],
            description=packet['description'],categoryId=packet['category_id'],defaultLanguage=packet['language']).items():
        if snippet.get(key) != expected: raise PublishingError('YouTube metadata differs from the approved packet: ' + key)
    if status.get('uploadStatus') in ('failed','rejected','deleted'):
        raise PublishingError('YouTube reports upload/processing failure. Inspect the saved video in Studio.')
    unlisted = packet.get('initial_privacy') == 'unlisted'
    if unlisted and (status.get('privacyStatus') != 'unlisted' or status.get('publishAt')):
        raise PublishingError('Unlisted visibility could not be verified.')
    published = not unlisted and allow_published and status.get('privacyStatus')=='public'
    if not unlisted and status.get('privacyStatus') != 'private' and not published:
        raise PublishingError('Video is no longer private. Inspect Studio; do not upload again.')
    if not unlisted:
        try:
            actual = datetime.fromisoformat(status.get('publishAt', packet['schedule']['publish_at'] if published else '').replace('Z','+00:00'))
            expected = datetime.fromisoformat(packet['schedule']['publish_at'].replace('Z','+00:00'))
            if actual != expected: raise ValueError()
        except (KeyError,ValueError):
            raise PublishingError('Scheduled release time could not be verified.') from None
    if status.get('selfDeclaredMadeForKids') is not packet['made_for_kids'] or status.get('containsSyntheticMedia', False) is not packet['contains_synthetic_media']:
        raise PublishingError('Audience or synthetic-content disclosure could not be verified.')
    if video.get('paidProductPlacementDetails',{}).get('hasPaidProductPlacement',False) is not packet['contains_paid_promotion']:
        raise PublishingError('Paid-promotion disclosure could not be verified.')
    processing = video.get('processingDetails',{}).get('processingStatus', 'unknown')
    if processing in ('failed','terminated'):
        raise PublishingError('YouTube processing failed. Inspect the saved video in Studio.')
    return dict(schedule_verified=not published and not unlisted, published=published, processing=processing,
                ready=processing=='succeeded' or status.get('uploadStatus')=='processed')


class PublishingService:
    def __init__(self, data, auth=None, client=None):
        self.root = Path(data) / 'youtube-publishing'
        self.root.mkdir(parents=True, exist_ok=True)
        self.db = self.root / 'jobs.sqlite'
        if auth is None:
            from .youtube_publish_auth import PublishingAuth
            auth = PublishingAuth(data)
        self.auth = auth; self.client = client or YouTubePublisher(auth)
        with self.connect() as con:
            con.execute('CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, digest TEXT UNIQUE NOT NULL, body TEXT NOT NULL)')

    @contextmanager
    def connect(self):
        con = sqlite3.connect(self.db, timeout=15)
        try:
            with con:
                yield con
        finally:
            con.close()

    def _load(self, jid):
        with self.connect() as con:
            row = con.execute('SELECT body FROM jobs WHERE id=?',(jid,)).fetchone()
        if not row: raise KeyError('Publishing job not found')
        return json.loads(row[0])

    def _save(self, job):
        job['updated_at'] = utcnow()
        with self.connect() as con:
            con.execute('UPDATE jobs SET body=? WHERE id=?',(json.dumps(job),job['id']))

    def get(self, jid):
        job = self._load(jid)
        return {k:v for k,v in job.items() if k not in ('session','approval')}

    def status(self):
        with self.connect() as con:
            ids = [r[0] for r in con.execute('SELECT id FROM jobs ORDER BY rowid DESC LIMIT 50')]
        return dict(connected=self.auth.connected,jobs=[self.get(i) for i in ids],
                    scopes=['youtube.upload','youtube.readonly'],live_tested=False)

    def prepare(self, source):
        packet = prepare_packet(source)
        # Real upload preflight beyond the generic offline review format.
        if Path(packet['video']['path']).suffix.lower() not in ('.mp4','.mov','.m4v'):
            raise ReleaseError('This first uploader supports MP4/MOV/M4V exports only.')
        from PIL import Image
        for asset in packet['thumbnail_variants']:
            if asset['size'] > 50 * 1024 * 1024: raise ReleaseError('Thumbnail exceeds the current 50 MB API limit.')
            with Image.open(asset['path']) as im:
                if im.format not in ('PNG','JPEG'): raise ReleaseError('Use PNG/JPEG thumbnails.')
                im.verify()
        packet['capability']='api_upload_with_explicit_review'
        from .youtube_release import digest
        packet['packet_digest']=digest({k:v for k,v in packet.items() if k!='packet_digest'})
        job = dict(id=uuid.uuid4().hex,packet=packet,state='needs_review',created_at=utcnow(),
                   video_id=None,thumbnail_set=False,ab_state='pending_publication_and_Chrome_setup',error=None)
        with self.connect() as con:
            con.execute('BEGIN IMMEDIATE')
            for (raw,) in con.execute('SELECT body FROM jobs'):
                old = json.loads(raw)
                if old.get('approval') and old['packet']['channel_id']==packet['channel_id'] and old['packet']['video']['sha256']==packet['video']['sha256'] and old['packet']['packet_digest']!=packet['packet_digest']:
                    raise PublishingError('This video already has an approved upload job: '+old['id']+'. Recover or inspect that job instead of creating a duplicate.')
            con.execute('INSERT OR IGNORE INTO jobs VALUES(?,?,?)',(job['id'],packet['packet_digest'],json.dumps(job)))
            jid = con.execute('SELECT id FROM jobs WHERE digest=?',(packet['packet_digest'],)).fetchone()[0]
        return self.get(jid)

    @contextmanager
    def locked(self,jid):
        if not re.fullmatch('[a-f0-9]{32}',jid): raise KeyError('Invalid job ID')
        handle = (self.root / (jid+'.lock')).open('a+b')
        try:
            if os.name=='nt':
                import msvcrt
                handle.seek(0); handle.write(b'0'); handle.flush(); handle.seek(0)
                try: msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
                except OSError: raise PublishingError('This publishing job is already running.') from None
            else:
                import fcntl
                try: fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
                except OSError: raise PublishingError('This publishing job is already running.') from None
            yield
        finally:
            handle.close()

    def approve_and_start(self,jid,packet_digest):
        with self.locked(jid):
            job=self._load(jid)
            if job['packet']['packet_digest']!=packet_digest: raise ReleaseError('Review packet changed.')
            if job['state']!='needs_review': return self.get(jid)
            verify(job['packet'])
            if not self.auth.connected: raise PublishingError('Connect separate publishing permissions first.')
            with self.connect() as con:
                con.execute('BEGIN IMMEDIATE')
                for (raw,) in con.execute('SELECT body FROM jobs WHERE id<>?',(jid,)):
                    old=json.loads(raw)
                    if old.get('approval') and old['packet']['channel_id']==job['packet']['channel_id'] and old['packet']['video']['sha256']==job['packet']['video']['sha256']:
                        raise PublishingError('This file already has an approved job: '+old['id']+'. Recover that job instead.')
                job['approval']=dict(digest=packet_digest,at=utcnow(),source='explicit_review_control')
                job['state']='approved'; job['updated_at']=utcnow()
                con.execute('UPDATE jobs SET body=? WHERE id=?',(json.dumps(job),jid))
        return self.resume(jid)

    def resume(self,jid):
        job=self._load(jid)
        if not job.get('approval') or job['approval']['digest']!=job['packet']['packet_digest']:
            raise PublishingError('Approve this exact release packet in the review page first.')
        if job['state'] in ('published','unlisted','initialization_uncertain'):
            return self.get(jid)
        threading.Thread(target=self.run,args=(jid,),daemon=True).start()
        return self.get(jid)

    def run(self,jid):
        try:
            with self.locked(jid):
                job=self._load(jid)
                try:
                    if not job.get('approval') or job['approval']['digest']!=job['packet']['packet_digest']:
                        raise PublishingError('Exact release approval is required.')
                    due=bool(job['packet'].get('schedule')) and datetime.fromisoformat(job['packet']['schedule']['publish_at'].replace('Z','+00:00'))<=datetime.now(timezone.utc)
                    if job.get('video_id') and due:
                        from .youtube_release import digest
                        packet=job['packet']
                        if digest({k:v for k,v in packet.items() if k!='packet_digest'})!=packet['packet_digest']:
                            raise ReleaseError('Packet changed after approval.')
                        if self.client.channel()!=packet['channel_id']:
                            raise PublishingError('Connected channel differs from the approved channel.')
                        result=check_remote(packet,self.client.inspect(job['video_id']),allow_published=True)
                        job['verification']=result; job['state']='published' if result['published'] else 'processing'
                        if result['published']: job['ab_state']='ready_for_approved_Chrome_test_if_eligible'
                        job['error']=None; self._save(job); return
                    if job['state'] in ('scheduled','unlisted'): return
                    verify(job['packet'])
                    if self.client.channel()!=job['packet']['channel_id']:
                        raise PublishingError('Connected YouTube channel differs from the approved channel.')
                    if not job.get('video_id'):
                        if not job.get('session'):
                            if job['state'] in ('initializing','initialization_uncertain'):
                                job['state']='initialization_uncertain'; self._save(job)
                                raise PublishingError('Upload initialization is uncertain. Inspect Studio before any new operation.')
                            job['state']='initializing'; self._save(job)
                            try: session=self.client.initiate(job['packet'])
                            except Exception:
                                job['state']='initialization_uncertain'; self._save(job); raise
                            job['session']=base64.b64encode(protect(session.encode())).decode()
                            job['state']='uploading'; self._save(job)
                        session=protect(base64.b64decode(job['session']),decrypt=True).decode()
                        job['video_id']=self.client.upload(session,job['packet'])
                        if not isinstance(job['video_id'],str) or not re.fullmatch(r'[A-Za-z0-9_-]{11}',job['video_id']):
                            job['video_id']=None
                            raise PublishingError('Unexpected video ID; recover the saved upload session.')
                        job['state']='uploaded'; self._save(job)
                    if not job['thumbnail_set']:
                        self.client.thumbnail(job['video_id'],job['packet']['primary_thumbnail'])
                        job['thumbnail_set']=True; self._save(job)
                    result=check_remote(job['packet'],self.client.inspect(job['video_id']))
                    job['verification']=result
                    job['state']=('unlisted' if job['packet'].get('initial_privacy') == 'unlisted' else 'scheduled') if result['ready'] else 'processing'
                    job['url']='https://www.youtube.com/watch?v='+job['video_id']
                    job['studio_url']='https://studio.youtube.com/video/'+job['video_id']+'/edit'
                    job['error']=None; self._save(job)
                except Exception as exc:
                    job['error']=str(exc) if isinstance(exc,(PublishingError,ReleaseError)) else 'Publishing stopped unexpectedly. Resume the saved job after inspecting its status.'
                    if job['state']=='initializing' and not job.get('session'): job['state']='initialization_uncertain'
                    elif job['state']!='initialization_uncertain': job['state']='blocked'
                    self._save(job)
        except PublishingError:
            return  # Another worker owns the same job; never duplicate its work.
