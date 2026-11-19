"""Durable, fail-closed contract for a narrow native-transcription UI worker.

This module does not supply a desktop controller or dispatch endpoint. A supported
worker must be connected explicitly. Worker reports are never completion evidence:
CareerOS verifies the saved exact-microphone transcript and unchanged timeline.
"""
import hashlib
import json
import os
import time
import uuid
from contextlib import contextmanager
from pathlib import Path


class WorkerBlocked(RuntimeError):
    pass


def file_digest(path):
    h = hashlib.sha256()
    with open(path, 'rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def timeline(info):
    # Include every track/item, not merely the expected A1/V1 piece count.
    return dict(name=info.get('name'), frame_ticks=info.get('frame_ticks'),
                items=sorted(info['items'], key=lambda x: json.dumps(x, sort_keys=True)))


class RequestStore:
    def __init__(self, path, clock=time.time):
        self.path, self.clock = Path(path), clock

    def exists(self):
        return self.path.is_file()

    @contextmanager
    def locked(self):
        lock = self.path.with_suffix('.lock')
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as exc:
            raise WorkerBlocked('Transcript request is locked; verify the previous worker before retrying.') from exc
        try:
            yield
        finally:
            os.close(fd)
            lock.unlink()

    def read(self):
        return json.loads(self.path.read_text())

    def write(self, value):
        tmp = self.path.with_suffix('.tmp')
        with tmp.open('w') as stream:
            json.dump(value, stream, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        tmp.replace(self.path)
        return value

    def _check(self, request, identity, info):
        if request['identity'] != identity:
            raise WorkerBlocked('Transcript request project, run, or microphone identity changed; no dispatch is allowed.')
        if request['timeline_digest'] != digest(timeline(info)):
            raise WorkerBlocked('The cut changed after the transcription request. Preserve the edit and reconcile before resuming.')

    def prepare(self, identity, info):
        with self.locked():
            if self.exists():
                request = self.read()
                self._check(request, identity, info)
                return request
            if not all(identity.get(k) for k in ('project', 'mic', 'mic_sha256', 'sequence', 'run')):
                raise WorkerBlocked('Incomplete source identity; no transcription request was created.')
            return self.write(dict(version=1, request_id=str(uuid.uuid4()), identity=identity,
                timeline_digest=digest(timeline(info)), state='pending', created=self.clock(),
                worker=None, dispatch_available=False,
                scope='Transcribe this source microphone in this open project, save, then return. Do not cut, reopen, import, replace, approve, or publish.'))

    def check(self, identity, info):
        with self.locked():
            request = self.read()
            self._check(request, identity, info)
            return request

    def observe_background(self, identity, info):
        with self.locked():
            request = self.read()
            self._check(request, identity, info)
            if request['state'] not in ('pending', 'observing'):
                raise WorkerBlocked('A transcription worker already attempted this request; reconcile its outcome before background observation.')
            request.update(state='observing', route='premiere-background',
                           message='Premiere owns automatic transcription. CareerOS observes saved native words; no UI action was dispatched.')
            return self.write(request)

    def claim(self, worker, identity, info, ttl=900):
        if not worker or not 1 <= ttl <= 900:
            raise WorkerBlocked('A named worker and a bounded lease are required.')
        with self.locked():
            request = self.read()
            self._check(request, identity, info)
            if request['state'] != 'pending':
                raise WorkerBlocked('Request already claimed or attempted. Reconcile its outcome; never submit it again.')
            request.update(state='claimed', worker=worker, claim_id=str(uuid.uuid4()), deadline=self.clock()+ttl)
            return self.write(request)

    def report(self, claim_id, state, message=''):
        if state not in ('started', 'awaiting_verification', 'uncertain'):
            raise WorkerBlocked('Worker cannot declare completion or reset a request.')
        with self.locked():
            request = self.read()
            if request.get('claim_id') != claim_id:
                raise WorkerBlocked('Worker claim mismatch.')
            if self.clock() > request['deadline']:
                request.update(state='uncertain', message='Worker lease expired; verify before any further action.')
                self.write(request)
                raise WorkerBlocked(request['message'])
            allowed = {'claimed': {'started', 'uncertain'}, 'started': {'awaiting_verification', 'uncertain'}}
            if state not in allowed.get(request['state'], set()):
                raise WorkerBlocked('Invalid worker transition; an attempted action must not repeat.')
            request.update(state=state, message=str(message)[:1000])
            return self.write(request)

    def verify(self, identity, info, selected, saved):
        from .premiere_transcript import media_key
        with self.locked():
            request = self.read()
            self._check(request, identity, info)
            if (not saved or not selected or media_key(selected.get('media')) != identity['mic']
                    or not any(w.get('word', '').strip() for w in selected.get('words', []))):
                raise WorkerBlocked('Saved native microphone transcript is not verified.')
            # Only CareerOS calls this after save + decoding, never on worker assertion.
            request.update(state='verified', verified=self.clock(), transcript_digest=digest(selected))
            return self.write(request)
