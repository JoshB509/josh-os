"""Local saved-file signal checks. Never opens an input device or controls OBS.

Technical signal detection is not speech recognition or a listening review.
Checks are additive, persistent, and bound to the exact saved file content.
"""
import contextlib
import ctypes
import hashlib
import json
import math
import os
import queue
import re
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from .store import Conflict

VERSION = 1
FLAGS = getattr(subprocess, 'CREATE_NO_WINDOW', 0)

def now(): return datetime.now(timezone.utc).isoformat()

def identity(path):
    p = Path(path).resolve(); s = p.stat()
    return dict(path=str(p), size=s.st_size, mtime_ns=str(s.st_mtime_ns),
                ctime_ns=str(s.st_ctime_ns), inode=str(s.st_ino))

def sha256(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''): h.update(block)
    return h.hexdigest()

@contextlib.contextmanager
def stable_reader(path):
    """Allow decoder reads, deny writers/deletion while checking on Windows."""
    if os.name != 'nt':
        with Path(path).open('rb') as f: yield f
        return
    import msvcrt
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateFileW.restype = ctypes.c_void_p
    kernel.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32,
        ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p]
    handle = kernel.CreateFileW(str(path), 0x80000000, 1, None, 3, 0x80, None)
    if handle == ctypes.c_void_p(-1).value:
        raise Conflict('File is open for writing. Wait for recording/remux to finish, then Check again.')
    try: fd = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
    except Exception:
        kernel.CloseHandle(ctypes.c_void_p(handle)); raise
    with os.fdopen(fd, 'rb') as f: yield f

def inspect_audio(path, ffmpeg):
    """Decode every audio stream to completion; astats measures all samples.

    A finite peak, rather than rounded Min/Max values, distinguishes arbitrarily
    small nonzero samples from digital zero. No leading-silence sampling shortcut.
    """
    base = [str(ffmpeg), '-hide_banner', '-nostdin']
    probe = subprocess.run(base + ['-i', str(path)], capture_output=True, text=True,
        errors='replace', timeout=60, creationflags=FLAGS)
    indices = sorted(set(int(x) for x in re.findall(r'Stream #0:(\d+)(?:\[[^\]]*\])?(?:\([^)]*\))?: Audio:', probe.stderr)))
    if not re.search(r'Input #0,', probe.stderr):
        return dict(status='failed', reason='decode_error', message='The saved file could not be opened as complete media.', streams=[])
    if not indices:
        return dict(status='failed', reason='missing_audio', message='No audio stream is present in this saved take.', streams=[])
    streams = []
    for index in indices:
        result = subprocess.run(base + ['-v', 'info', '-xerror', '-err_detect', 'explode',
            '-i', str(path), '-map', '0:'+str(index), '-vn', '-sn', '-dn',
            '-af', 'astats=metadata=0:reset=0', '-f', 'null', '-'],
            capture_output=True, text=True, errors='replace', timeout=7200, creationflags=FLAGS)
        log = result.stderr
        overall = log.rsplit('Overall', 1)[-1] if 'Overall' in log else ''
        peak = re.search(r'Peak level dB:\s*([-+\w.]+)', overall)
        rms = re.search(r'RMS level dB:\s*([-+\w.]+)', overall)
        count = re.search(r'Number of samples:\s*(\d+)', overall)
        bad = bool(result.returncode or not peak or not rms or not count or
            re.search(r'File ended prematurely|Packet corrupt|corrupt decoded|Invalid data found|Error while decoding|truncated', log, re.I))
        row = dict(index=index, decoded=not bad, samples=int(count[1]) if count else 0)
        if not bad:
            p, r = float(peak[1]), float(rms[1])
            bad = row['samples'] == 0 or math.isnan(p) or math.isnan(r) or p == math.inf or r == math.inf
            row.update(decoded=not bad, digital_zero=p == -math.inf,
                peak_dbfs=p if math.isfinite(p) else None, rms_dbfs=r if math.isfinite(r) else None)
        if bad: row['error'] = 'Audio decoding did not finish cleanly or produced no valid samples.'
        streams.append(row)
    if any(not s['decoded'] for s in streams):
        return dict(status='failed', reason='decode_error', message='At least one audio stream could not be decoded completely. The file may be corrupt or unfinished.', streams=streams)
    nonzero = [s for s in streams if not s['digital_zero']]
    if not nonzero:
        return dict(status='failed', reason='digital_silence', message='Verified digital silence: every decoded audio stream contains only zero-valued samples.', streams=streams)
    useful = [s for s in nonzero if s['peak_dbfs'] >= -40 and s['rms_dbfs'] is not None and s['rms_dbfs'] >= -60]
    if not useful:
        return dict(status='warning', reason='low_level', message='Only very low-level signal was detected; it may be noise. Play the take and confirm intelligible speech before continuing.', streams=streams)
    return dict(status='passed', reason='signal_present', message='Non-silent audio detected. Speech and audible quality are not verified; play the take before Done.', streams=streams,
                signal_streams=[s['index'] for s in useful])

class AudioChecks:
    def __init__(self, store, ffmpeg):
        self.store, self.ffmpeg = store, ffmpeg
        self.lock = threading.RLock(); self.jobs = queue.Queue(); self.queued = set()
        self.closed = threading.Event()
        with store.connect() as c:
            c.execute('CREATE TABLE IF NOT EXISTS lesson_audio_checks(take_id TEXT PRIMARY KEY, body TEXT NOT NULL)')
            c.execute('INSERT OR IGNORE INTO lesson_migrations VALUES(4,?)', (now(),))
        self.worker = threading.Thread(target=self._loop, name='lesson-audio-check', daemon=True)
        self.worker.start()

    def close(self):
        self.closed.set(); self.jobs.put(None); self.worker.join(timeout=10)

    def _read(self, tid):
        with self.store.connect() as c: row=c.execute('SELECT body FROM lesson_audio_checks WHERE take_id=?',(tid,)).fetchone()
        return json.loads(row['body']) if row else None

    def _save(self, tid, value):
        with self.store.connect() as c: c.execute('INSERT OR REPLACE INTO lesson_audio_checks VALUES(?,?)',(tid,json.dumps(value,allow_nan=False)))

    @staticmethod
    def present(value):
        value = dict(value)
        value['can_continue'] = value['status']=='passed' or (value['status']=='warning' and bool(value.get('audition')))
        value['speech_verified'] = bool(value.get('audition'))
        value['verification_kind'] = 'user_reported_listening' if value.get('audition') else 'technical_signal_only'
        return value

    def ensure(self, take, retry=False):
        with self.lock:
            try: current=identity(take['path'])
            except OSError:
                return self.present(dict(status='failed',reason='missing_file',message='Saved take is missing or inaccessible.',version=VERSION))
            old=self._read(take['id'])
            if old and old.get('identity')==current and old.get('version')==VERSION and not retry:
                if old['status'] not in {'pending','checking','waiting'} or take['id'] in self.queued:
                    return self.present(old)
            if take['id'] in self.queued:
                return self.present(dict(status='pending',reason='queued',message='Audio check in progress. Done and upload readiness are blocked.',identity=current,version=VERSION))
            value=dict(status='pending',reason='queued',message='Waiting to check every saved audio stream. Done and upload readiness are blocked.',identity=current,version=VERSION,requested_at=now())
            self._save(take['id'],value);self.queued.add(take['id']);self.jobs.put(dict(take))
            return self.present(value)

    def require(self, take):
        check=self.ensure(take)
        if not check['can_continue']: raise Conflict(take['name']+': '+check['message']+' Use Check again or choose a validated take. Previous selections are preserved.')
        # Completion/upload endpoints verify content as well as stat identity.
        with stable_reader(take['path']):
            if identity(take['path'])!=check.get('identity') or sha256(take['path'])!=check.get('sha256'):
                self.ensure(take,retry=True)
                raise Conflict(take['name']+': file changed; a fresh audio check is required.')
        return check

    def audition(self, take, data):
        with self.lock:
            value=self.ensure(take)
            if value['status'] not in {'passed','warning'}: raise Conflict('Audio check must finish successfully before a listening review can be recorded.')
            if data.get('confirmed') is not True or data.get('heard_speech') is not True:
                raise ValueError('Confirm that you played this exact take and heard intelligible speech.')
            with stable_reader(take['path']):
                if identity(take['path'])!=value.get('identity') or sha256(take['path'])!=value.get('sha256'):
                    self.ensure(take,retry=True);raise Conflict('Take changed. Check it again before a listening review.')
            value['audition']=dict(at=now(),evidence='User reports playing this exact take and hearing intelligible speech',sha256=value['sha256'])
            self._save(take['id'],value)
            return self.present(value)

    def _loop(self):
        while not self.closed.is_set():
            take=self.jobs.get()
            if take is None: self.jobs.task_done();break
            try: self._check(take)
            finally:
                with self.lock:self.queued.discard(take['id'])
                self.jobs.task_done()

    def _check(self, take):
        tid=take['id'];before=None
        try:
            before=identity(take['path'])
            settle=5-(time.time()-int(before['mtime_ns'])/1e9)
            if settle>0:
                self.closed.wait(min(settle,5))
                if before!=identity(take['path']):raise Conflict('Saved file is still changing. Waiting for writing/remux to finish.')
            with stable_reader(take['path']):
                time.sleep(.1)
                if before!=identity(take['path']):raise Conflict('File is still changing. Wait, then Check again.')
                self._save(tid,dict(status='checking',reason='decoding',message='Decoding every audio stream to the end. Done and upload readiness are blocked.',identity=before,version=VERSION))
                fingerprint=sha256(take['path'])
                result=inspect_audio(take['path'],self.ffmpeg)
                if before!=identity(take['path']):raise Conflict('File changed during validation. Check again after writing finishes.')
                result.update(sha256=fingerprint,identity=before,version=VERSION,checked_at=now(),human_listening=False)
                if take.get('sha256') and take['sha256']!=fingerprint:
                    result.update(status='failed',reason='content_changed',message='Saved take differs from its original archived hash. Review the file; no selection has changed.')
        except Conflict as e:
            result=dict(status='waiting',reason='file_busy',message=str(e),identity=before,version=VERSION)
        except Exception as e:
            result=dict(status='failed',reason='check_error',message='Audio check could not finish: '+str(e)[:250],identity=before,version=VERSION,checked_at=now())
        self._save(tid,result)
