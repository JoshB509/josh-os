"""Persisted, local carousel operation receipts. No renderer or work scheduling here."""
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
import hashlib
import json
import uuid

from .store import Conflict, now

WORKER = uuid.uuid4().hex
ACTIVE = ContextVar('carousel_progress_job', default=None)
MESSAGES = {'queued': 'Waiting to prepare this carousel request.', 'preparing': 'Checking the carousel and requested changes.',
            'rendering': 'Rendering slides. The renderer does not report incremental progress.',
            'verifying': 'Checking the ordered slide images and immutable manifest.',
            'saving': 'Saving the verified carousel and rendered slides.',
            'complete': 'Carousel changes committed.', 'failed': 'The carousel was not saved. Review the request before retrying.',
            'context': 'This carousel needs saved separate caption and QR link context. No Photo draft was saved.',
            'binding': 'Photo promotion needs a verified owner-approved post/offer binding. No draft was saved.',
            'conflict': 'The carousel changed during this request. Reload it before retrying.',
            'custom': 'Custom slides were preserved. Use a single-slide override for this change.',
            'interrupted': 'The app restarted before completion was confirmed. Check the carousel before retrying.',
            'stalled': 'No recent stage update. Completion has not been confirmed.'}


def request_key(value=None):
    if not value:
        return str(uuid.uuid4())
    try:
        return str(uuid.UUID(str(value)))
    except ValueError:
        return str(uuid.uuid5(uuid.NAMESPACE_URL, 'carousel-request:' + str(value)))


def identity(value):
    try:
        return isinstance(value, str) and str(uuid.UUID(value)) == value
    except (ValueError, TypeError, AttributeError):
        return False


def mark(state):
    job = ACTIVE.get()
    if job:
        job.mark(state)


def committed(connection, item, changed=True):
    job = ACTIVE.get()
    if job:
        job.commit(connection, item, changed)


class CarouselJobs:
    def __init__(self, store):
        self.store = store
        with store.connect() as c:
            c.execute('CREATE TABLE IF NOT EXISTS carousel_jobs(id TEXT PRIMARY KEY,request_id TEXT NOT NULL,carousel_id TEXT NOT NULL,body TEXT NOT NULL)')
            c.execute('CREATE INDEX IF NOT EXISTS carousel_jobs_request ON carousel_jobs(request_id)')
            c.execute('CREATE INDEX IF NOT EXISTS carousel_jobs_carousel ON carousel_jobs(carousel_id)')
            c.execute('CREATE TABLE IF NOT EXISTS carousel_requests(id TEXT PRIMARY KEY,digest TEXT NOT NULL)')

    def batch(self, request_id, updates, revisions):
        rid = request_key(request_id)
        updates = [u for u in updates if isinstance(u, dict)]
        digest = hashlib.sha256(json.dumps(updates, sort_keys=True).encode()).hexdigest()
        pending = []
        with self.store.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            existing = c.execute('SELECT digest FROM carousel_requests WHERE id=?', (rid,)).fetchone()
            if existing and existing['digest'] != digest:
                raise Conflict('This request already names different carousel changes. Start a new request.')
            if not existing:
                c.execute('INSERT INTO carousel_requests VALUES(?,?)', (rid,digest))
            for ordinal, update in enumerate(updates):
                jid = str(uuid.uuid5(uuid.UUID(rid), str(ordinal)))
                row = c.execute('SELECT body FROM carousel_jobs WHERE id=?', (jid,)).fetchone()
                if row:
                    pending.append((update, Job(self,json.loads(row['body'])), False))
                    continue
                cid = update.get('id') or str(uuid.uuid5(uuid.UUID(jid), 'carousel'))
                record = dict(id=jid, request_id=rid, carousel_id=cid, worker=WORKER, owner='sam',
                              state='queued', updated_at=now(), created_at=now(), expected_revision=(revisions or {}).get(cid),
                              new=not bool(update.get('id')), result=None)
                c.execute('INSERT INTO carousel_jobs VALUES(?,?,?,?)', (jid,rid,cid,json.dumps(record)))
                pending.append((update, Job(self,record), True))
        return pending

    def get(self, jid):
        if not identity(jid):
            raise KeyError('Carousel job not found.')
        with self.store.connect() as c:
            row = c.execute('SELECT body FROM carousel_jobs WHERE id=?', (jid,)).fetchone()
        if not row:
            raise KeyError('Carousel job not found.')
        return json.loads(row['body'])

    def present(self, record):
        state = record['state']
        terminal = state in ('complete','failed','conflict','custom','binding','context')
        if not terminal:
            if record['worker'] != WORKER:
                state,terminal = 'interrupted',True
            else:
                try:
                    age = (datetime.now(timezone.utc)-datetime.fromisoformat(record['updated_at'])).total_seconds()
                except (ValueError,TypeError):
                    age = 301
                if age > 300 or age < -30:
                    state = 'stalled'
        out = dict(kind='carousel_job',job_id=record['id'],request_id=record['request_id'],
                   carousel_id=record['carousel_id'] if identity(record['carousel_id']) else '',
                   state=state,terminal=terminal,message=MESSAGES[state],updated_at=record['updated_at'])
        if state=='complete':
            out.update(revision=record['result']['revision'],changed=record['changed'])
            if not record['changed']:
                out['message']='No changes were needed. The current revision was verified.'
        return out

    def status(self, jid):
        return self.present(self.get(jid))

    def scoped(self, *, request_id=None, carousel_id=None):
        value = request_id or carousel_id
        if not identity(value) or bool(request_id)==bool(carousel_id):
            raise ValueError('Choose one exact carousel or request.')
        column = 'request_id' if request_id else 'carousel_id'
        with self.store.connect() as c:
            limit = '' if request_id else ' LIMIT 50'
            rows = c.execute(f'SELECT body FROM carousel_jobs WHERE {column}=? ORDER BY rowid DESC'+limit,(value,)).fetchall()
        return dict(jobs=[self.present(json.loads(row['body'])) for row in rows])


class Job:
    def __init__(self, jobs, record):
        self.jobs,self.record,self.rendered=jobs,record,False

    @contextmanager
    def active(self):
        token=ACTIVE.set(self)
        try:
            yield self
        finally:
            ACTIVE.reset(token)

    def mark(self,state):
        self.rendered |= state=='rendering'
        record=dict(self.record,state=state,updated_at=now())
        with self.jobs.store.connect() as c:
            c.execute('UPDATE carousel_jobs SET body=? WHERE id=?',(json.dumps(record),record['id']))
        self.record=record

    def commit(self,c,item,changed):
        if item['id'] != self.record['carousel_id']:
            raise Conflict('Carousel job target changed.')
        action='added' if self.record['new'] else 'designed' if self.rendered else 'updated'
        result=dict(id=item['id'],revision=item['revision'],kind='carousel',title=item['title'],action=action)
        record=dict(self.record,state='complete',updated_at=now(),changed=changed,result=result)
        # This row and the carousel revision commit or roll back together.
        c.execute('UPDATE carousel_jobs SET body=? WHERE id=?',(json.dumps(record),record['id']))

    def fail(self,exc):
        # Never turn a committed success into a failure if subsequent reporting fails.
        if self.jobs.get(self.record['id'])['state']=='complete':
            return
        state='conflict' if isinstance(exc,Conflict) else 'custom' if 'custom slides' in str(exc) else 'failed'
        if any(text in str(exc) for text in ('verified post/offer binding','verified Photo binding','adapter not connected')):
            state='binding'
        if 'no saved separate caption/QR link context' in str(exc):
            state='context'
        self.mark(state)

    def receipt(self):
        record=self.jobs.get(self.record['id']);status=self.jobs.present(record)
        result=record.get('result') or dict(id=status['carousel_id'],title='',kind='carousel',
            action='failed' if status['terminal'] else 'pending',error=status['message'])
        return dict(result,job=status)
