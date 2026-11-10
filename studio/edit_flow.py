"""Tariq's editing workflow in Josh OS: sources and sync, proposed cut, Josh's review, approval,
native Premiere build, animation notes and final assembly.

Stages and their gates (handover doc 05). Every artifact is an immutable snapshot in the existing
workflow_artifacts table (kinds starting edit_) and every approval is tied to one artifact's digest.
A new revision never edits an old one, so restore is always a rebuild from the uncut source.

    edit_sources   camera + good mic chosen explicitly, file identities and headers
    edit_proposal  transcript, events, decisions and Josh's changes (one per revision)
    edit_cut       the native build request and Premiere's read-back for one approved proposal
    edit_visuals   Josh's animation notes and the storyboard approval for one cut
    edit_final     assembly and local export request and its verification

Local work only. Decoding, sync, transcription (faster-whisper, local files only, CPU unless the
PC proves otherwise), measurement and previews run here. Tariq's editorial pass is the one model
call, with read-only tools and a fixed answer shape that Josh OS validates. Premiere work goes
through the Josh Cut Pro panel bridge on this PC. Nothing is uploaded or published.
"""
import hashlib
import json
import subprocess
import sys
import threading
import uuid
import wave
from pathlib import Path

import numpy as np

from dajoshi import edit_plan as ep
from .store import Conflict, now

SR = 16000
VIDEO_EXT = {'.mp4', '.mov', '.mkv', '.mxf'}
AUDIO_EXT = {'.wav', '.aif', '.aiff', '.flac', '.mp3', '.m4a'}
KINDS = ('edit_sources', 'edit_proposal', 'edit_cut', 'edit_visuals', 'edit_final')
STAGES = [('sources', 'Sources and sync'), ('proposal', 'Proposed cut'), ('script', 'Script approved'),
          ('cut', 'Premiere cut'), ('visuals', 'Animations'), ('final', 'Final video')]
MAX_CONTEXT_S = 15
BRIDGE = 'http://127.0.0.1:47820'


class BridgeError(ValueError):
    """Premiere could not be reached or refused. Shown to Josh as it is, never as success."""


def sha256_file(path, block=8 << 20):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(block), b''):
            h.update(chunk)
    return h.hexdigest()


def identity(path):
    p = Path(path).resolve()
    if not p.is_file():
        raise ValueError(f'File not found: {p}')
    st = p.stat()
    return dict(path=str(p), size=st.st_size, mtime_ns=st.st_mtime_ns)


def write_atomic(path, value):
    path = Path(path)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(value, indent=1), encoding='utf-8')
    import time
    for attempt in range(40):  # Windows refuses the swap while a reader (the screen polling progress) has the file open
        try:
            tmp.replace(path)
            return
        except PermissionError:
            if attempt == 39:
                raise
            time.sleep(0.05)


class EditFlow:
    def __init__(self, store, root, agents=None, folders=None, edit_review=None):
        self.store, self.root, self.agents, self.folders, self.edit_review = store, Path(root), agents, folders, edit_review
        with store.connect() as c:  # the same tables studio/workflow.py uses, so there is one record of reviews
            c.executescript('''CREATE TABLE IF NOT EXISTS workflow_artifacts
                (id TEXT PRIMARY KEY,project_id TEXT NOT NULL,kind TEXT NOT NULL,body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS workflow_approvals (artifact_id TEXT PRIMARY KEY,body TEXT NOT NULL);''')
        self.data = Path(store.root) / 'edit_flow'
        self.data.mkdir(parents=True, exist_ok=True)
        self.jobs, self.lock = {}, threading.Lock()
        self.bridge = BRIDGE
        settings = {}
        try:
            settings = json.loads((self.root / 'settings.json').read_text(encoding='utf-8-sig'))
        except (OSError, ValueError):
            pass
        self.model_dir = settings.get('model', '')
        self.ffmpeg = str(self.root / 'tools' / 'ffmpeg.exe')

    # ------------------------------------------------------------ storage

    def _all(self, pid, kind):
        with self.store.connect() as c:
            rows = c.execute('SELECT body FROM workflow_artifacts WHERE project_id=? AND kind=? ORDER BY rowid DESC', (pid, kind)).fetchall()
        return [json.loads(r['body']) for r in rows]

    def latest(self, pid, kind):
        rows = self._all(pid, kind)
        return rows[0] if rows else None

    def get(self, pid, aid):
        with self.store.connect() as c:
            r = c.execute('SELECT body FROM workflow_artifacts WHERE id=? AND project_id=?', (aid, pid)).fetchone()
        if not r:
            raise KeyError('That revision is not in this project.')
        return json.loads(r['body'])

    def _put(self, pid, kind, payload, parent=None):
        body = dict(id=str(uuid.uuid4()), project_id=pid, kind=kind, created_at=now(), parent=parent, payload=payload)
        body['digest'] = ep.digest({k: v for k, v in body.items() if k != 'digest'})
        with self.store.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            c.execute('INSERT INTO workflow_artifacts VALUES(?,?,?,?)', (body['id'], pid, kind, json.dumps(body)))
        return body

    def approval(self, aid):
        with self.store.connect() as c:
            r = c.execute('SELECT body FROM workflow_approvals WHERE artifact_id=?', (aid,)).fetchone()
        return json.loads(r['body']) if r else None

    def _approve(self, a, extra):
        rec = dict(artifact_id=a['id'], digest=a['digest'], approved_at=now(), **extra)
        with self.store.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            latest = c.execute('SELECT id FROM workflow_artifacts WHERE project_id=? AND kind=? ORDER BY rowid DESC LIMIT 1',
                               (a['project_id'], a['kind'])).fetchone()
            if not latest or latest['id'] != a['id']:
                raise Conflict('A newer revision exists. Review that one.')
            c.execute('INSERT OR REPLACE INTO workflow_approvals VALUES(?,?)', (a['id'], json.dumps(rec)))
        return rec

    def approved(self, a):
        r = a and self.approval(a['id'])
        return bool(r and r['digest'] == a['digest'] and (self.latest(a['project_id'], a['kind']) or {}).get('id') == a['id'])

    def folder(self, pid):
        f = self.data / pid
        f.mkdir(parents=True, exist_ok=True)
        return f

    # ------------------------------------------------------------ stage 0: sources

    def suggest(self, pid):
        """Camera and mic candidates from the video's own Recorded\\Input folder. Ambiguity is shown, never resolved by guessing."""
        out = dict(videos=[], audio=[], folder=None)
        try:
            base = Path(self.folders.path(pid)) if self.folders and self.folders.path(pid) else None
        except Exception:  # noqa: BLE001
            base = None
        if base:
            inp = base / 'Recorded' / 'Input'
            out['folder'] = str(inp)
            if inp.is_dir():
                for f in sorted(inp.iterdir()):
                    if f.suffix.lower() in VIDEO_EXT:
                        out['videos'].append(str(f))
                    elif f.suffix.lower() in AUDIO_EXT:
                        out['audio'].append(str(f))
        # an mkv beside its remuxed mp4 is the same recording; offer the mp4 Premiere uses
        mp4 = {Path(v).with_suffix('').as_posix().lower() for v in out['videos'] if v.lower().endswith('.mp4')}
        out['videos'] = [v for v in out['videos'] if not (v.lower().endswith('.mkv') and Path(v).with_suffix('').as_posix().lower() in mp4)]
        out['ambiguous'] = len(out['videos']) != 1 or len(out['audio']) != 1
        return out

    def register(self, pid, camera, mic, note=''):
        self.store.get(pid)
        cam, m = Path(str(camera).strip().strip('"')), Path(str(mic).strip().strip('"'))
        if cam.suffix.lower() not in VIDEO_EXT:
            raise ValueError('Choose the camera video file.')
        if m.suffix.lower() not in AUDIO_EXT:
            raise ValueError('Choose the good mic audio file.')
        from dajoshi.media import probe
        files = dict(camera=identity(cam), mic=identity(m))
        headers = dict(camera=probe(cam), mic=probe(m))
        if not headers['camera'].get('fps'):
            raise ValueError('The camera file has no video stream.')
        if not headers['mic'].get('sample_rate'):
            raise ValueError('The mic file has no audio stream.')
        return self._put(pid, 'edit_sources', dict(files=files, headers=headers, note=str(note)[:500],
                                                     baseline='Originals are read only. Every cut is rebuilt from them.'))

    def sources_ok(self, src):
        for k, ident in src['payload']['files'].items():
            if identity(ident['path']) != ident:
                return f'The {k} file changed on disk since it was chosen.'
        return None

    # ------------------------------------------------------------ stage 1/3: analysis and proposal

    def analyse(self, pid, sources_id, editorial=True):
        src = self.get(pid, sources_id)
        if src['kind'] != 'edit_sources' or (self.latest(pid, 'edit_sources') or {}).get('id') != sources_id:
            raise Conflict('Use the latest chosen sources.')
        err = self.sources_ok(src)
        if err:
            raise Conflict(err)
        with self.lock:
            t = self.jobs.get(pid)
            if t and t.is_alive():
                raise Conflict('Tariq is already working on this video.')
            run = self.folder(pid) / 'runs' / str(uuid.uuid4())
            run.mkdir(parents=True)
            t = threading.Thread(target=self._analyse, args=(pid, src, run, editorial), daemon=True)
            self.jobs[pid] = t
            t.start()
        return dict(run=run.name, state='started')

    def progress(self, pid):
        runs = sorted((self.folder(pid) / 'runs').glob('*/progress.json'), key=lambda p: p.stat().st_mtime) if (self.folder(pid) / 'runs').exists() else []
        if not runs:
            return None
        try:
            p = json.loads(runs[-1].read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return None
        t = self.jobs.get(pid)
        if p.get('state') == 'running' and not (t and t.is_alive()):
            p['state'] = 'interrupted'
            p['message'] = 'The work stopped when Josh OS closed. Start it again. Finished steps are cached.'
        return p

    def cancel(self, pid):
        p = self.progress(pid)
        if p and p.get('state') == 'running':
            (Path(p['run']) / 'CANCEL').write_text('cancel', encoding='utf-8')
        return dict(ok=True)

    def _step(self, run, state, step, message, **extra):
        write_atomic(run / 'progress.json', dict(state=state, step=step, message=message, run=str(run), at=now(), **extra))

    def _cache(self):
        c = self.data / 'cache'
        c.mkdir(exist_ok=True)
        return c

    def _hash(self, ident):
        key = ep.digest(ident)[:24]
        f = self._cache() / f'sha-{key}.txt'
        if f.exists():
            return f.read_text().strip()
        h = sha256_file(ident['path'])
        f.write_text(h)
        return h

    def _decode(self, path, sha, run):
        from dajoshi.media import decode
        out = self._cache() / f'pcm16k-{sha[:24]}.f32'
        if out.exists():
            return np.fromfile(out, dtype='<f4')
        return decode(path, out, run, self.ffmpeg)

    def _transcribe(self, mic, sha, run):
        if not self.model_dir or not (Path(self.model_dir) / 'model.bin').is_file():
            raise ValueError('The local Whisper model is missing, so transcription stopped. Josh OS will not use a cloud service instead.')
        cfg = dict(model=Path(self.model_dir).name, model_dir=self.model_dir, device='cpu', compute_type='int8', beam=3, vad=True, lang='en')
        out = self._cache() / f"asr-{sha[:24]}-{ep.digest(cfg)[:12]}.json"
        if out.exists():
            return json.loads(out.read_text(encoding='utf-8')), cfg
        code = ('import json,sys;from dajoshi.media import transcribe;'
                'json.dump(transcribe(sys.argv[1],sys.argv[2],sys.argv[3]),open(sys.argv[4],"w",encoding="utf-8"))')
        tmp = run / 'transcript.json'
        from dajoshi.media import run as run_proc
        # A child process, because faster-whisper has crashed natively before. Fixed command, no shell.
        run_proc([sys.executable, '-c', code, str(mic), self.model_dir, str(run), str(tmp)], run, 'Transcribe locally', timeout=4 * 3600)
        segs = json.loads(tmp.read_text(encoding='utf-8'))
        write_atomic(out, segs)
        return segs, cfg

    def _analyse(self, pid, src, run, editorial):
        files, headers = src['payload']['files'], src['payload']['headers']
        try:
            self._step(run, 'running', 'hash', 'Fingerprinting the original files')
            sha = dict(camera=self._hash(files['camera']), mic=self._hash(files['mic']))
            self._step(run, 'running', 'decode', 'Decoding the mic and camera audio on this PC')
            mic = self._decode(files['mic']['path'], sha['mic'], run)
            cam = self._decode(files['camera']['path'], sha['camera'], run)
            self._step(run, 'running', 'sync', 'Checking camera and mic sync near the start, middle and end')
            from dajoshi.media import sync
            try:
                sync_report = sync(cam, mic)
            except ValueError as e:
                sync_report = dict(verified=False, error=str(e))
            self._step(run, 'running', 'transcribe', 'Transcribing the mic on this PC (CPU, local model)')
            segs, asr_cfg = self._transcribe(files['mic']['path'], sha['mic'], run)
            self._step(run, 'running', 'measure', 'Measuring pauses and sounds')
            words = ep.words_from_segments(segs, SR)
            events, meta = ep.discover(words, mic, SR)
            ed = dict(ran=False, note='Tariq\'s editorial pass was not run.')
            if editorial and self.agents is not None:
                self._step(run, 'running', 'editorial', 'Tariq is reading the transcript for retakes and pauses')
                try:
                    events, ed = self._editorial(pid, words, events)
                except Exception as e:  # noqa: BLE001 - the proposal still works without it, and says so
                    ed = dict(ran=False, error=str(e)[:400], note='Tariq\'s editorial pass failed, so retakes are only flagged, not removed.')
            payload = dict(sources_id=src['id'], sources_digest=src['digest'], hashes=sha, headers=headers, sync=sync_report,
                           asr=dict(asr_cfg, words=len(words)), measurements=meta, words=words, events=events,
                           decisions={}, word_actions={}, notes=[], editorial=ed, revision=1, inserts=[],
                           pcm=str(self._cache() / f"pcm16k-{sha['mic'][:24]}.f32"))
            prop = self._put(pid, 'edit_proposal', payload, parent=src['id'])
            self._step(run, 'complete', 'done', 'The proposed cut is ready for review', proposal=prop['id'])
        except Exception as e:  # noqa: BLE001
            from dajoshi.media import Cancelled
            self._step(run, 'cancelled' if isinstance(e, Cancelled) else 'failed', 'error', str(e)[:500])

    def _editorial(self, pid, words, events):
        """Tariq reads the transcript and answers in a fixed shape. Josh OS checks every reference."""
        p = self.store.get(pid)
        pauses = {e['event_id']: e for e in events if e['kind'] == 'pause' and not e.get('outer') and e.get('review')}
        lines, at = [], 0
        marks = sorted([(e['start'], f"<{e['event_id']} {e['measurements']['duration_ms'] / 1000:.1f}s pause>") for e in pauses.values()])
        mi = 0
        for w in words:
            while mi < len(marks) and marks[mi][0] <= w['start']:
                lines.append(marks[mi][1])
                mi += 1
            lines.append(f"[{w['id']}]{w['text']}")
        cands = [dict(start_word=e['word_ids'][0], end_word=e['word_ids'][-1], text=e['text'][:200])
                 for e in events if e['kind'] == 'take_group']
        reply, answer = self.agents.edit_proposal_job(p, ' '.join(lines), cands)
        ids = {w['id'] for w in words}
        removals, pause_d = [], []
        for r in (answer or {}).get('removals') or []:
            if r.get('start_word') in ids and r.get('end_word') in ids:
                removals.append(r)
        for d in (answer or {}).get('pauses') or []:
            if d.get('event_id') in pauses and d.get('action') in ('keep', 'shorten'):
                if d['action'] == 'shorten':
                    t = d.get('target_ms')
                    if not isinstance(t, (int, float)) or not 150 <= t < pauses[d['event_id']]['measurements']['duration_ms']:
                        continue
                pause_d.append(d)
        checks = [c for c in (answer or {}).get('checks') or [] if c.get('start_word') in ids and c.get('end_word') in ids]
        events = ep.apply_editorial(events, words, removals, pause_d, checks=checks)
        return events, dict(ran=True, reply=str(reply)[:3000], removals=len(removals), pauses=len(pause_d), checks=len(checks),
                            rejected=len((answer or {}).get('removals') or []) - len(removals))

    # ------------------------------------------------------------ the view Josh reviews

    def computed(self, prop):
        pl = prop['payload']
        total = pl['measurements']['samples']
        rem, issues = ep.plan(pl['events'], pl['words'], pl['decisions'], total, SR, word_actions=pl['word_actions'])
        return rem, issues

    def view(self, pid, proposal_id=None):
        prop = self.get(pid, proposal_id) if proposal_id else self.latest(pid, 'edit_proposal')
        if not prop:
            return dict(proposal=None, progress=self.progress(pid), status=self.status(pid))
        pl = prop['payload']
        rem, issues = self.computed(prop)
        total = pl['measurements']['samples']
        missing = [k for k, f in self.get(pid, pl['sources_id'])['payload']['files'].items() if not Path(f['path']).is_file()]
        errors = ep.hard_errors(pl['sync'], issues, missing)
        kept = sum(b - a for a, b in ep.keeps_from(rem, total))
        return dict(
            proposal=dict(id=prop['id'], digest=prop['digest'], revision=pl['revision'], created_at=prop['created_at'],
                          latest=(self.latest(pid, 'edit_proposal') or {}).get('id') == prop['id'], approved=self.approved(prop)),
            transcript=ep.transcript(pl['words'], pl['events'], pl['decisions'], rem, SR, pl['word_actions']),
            cards=ep.queue(pl['events'], pl['decisions'], issues), issues=issues, errors=errors,
            sync=pl['sync'], editorial=pl['editorial'], asr=pl['asr'], notes=pl['notes'],
            summary=dict(source_s=round(total / SR, 2), kept_s=round(kept / SR, 2), removed_s=round((total - kept) / SR, 2),
                         removals=len(rem), cards=len(ep.queue(pl['events'], pl['decisions'], issues))),
            config=ep.CONFIG, progress=self.progress(pid), status=self.status(pid))

    def revise(self, pid, proposal_id, expected_digest, changes=(), words=(), note=''):
        """Josh's (or Tariq's) changes make a new revision. The old one is never edited."""
        prop = self.get(pid, proposal_id)
        if prop['kind'] != 'edit_proposal' or prop['digest'] != expected_digest:
            raise Conflict('This revision changed. Reload it before changing it.')
        if (self.latest(pid, 'edit_proposal') or {}).get('id') != proposal_id:
            raise Conflict('A newer revision exists. Change that one.')
        pl = json.loads(json.dumps(prop['payload']))
        ids = {e['event_id']: e for e in pl['events']}
        wid = {w['id'] for w in pl['words']}
        total = pl['measurements']['samples']
        for c in changes or []:
            e = ids.get(c.get('event_id'))
            if not e:
                raise ValueError('That event is not in this revision.')
            act = c.get('action')
            if act == 'restore':
                act = 'keep'
            if act not in ('keep', 'remove', 'shorten', 'default'):
                raise ValueError('Choose keep, remove or shorten.')
            if act == 'default':
                pl['decisions'].pop(e['event_id'], None)
                continue
            d = dict(action=act, by='josh', at=now())
            if act == 'shorten':
                if e['kind'] != 'pause':
                    raise ValueError('Only pauses can be shortened.')
                t = c.get('target_ms')
                if not isinstance(t, (int, float)) or not 0 <= t <= e['measurements']['duration_ms']:
                    raise ValueError('The shorter pause must be between 0 and its current length.')
                d['target_ms'] = int(t)
            for k in ('start', 'end'):
                if c.get(k) is not None:
                    v = int(c[k])
                    if not 0 <= v <= total:
                        raise ValueError('That boundary is outside the recording.')
                    d[k] = v
            if 'start' in d and 'end' in d and d['end'] <= d['start']:
                raise ValueError('The end of a cut must come after its start.')
            pl['decisions'][e['event_id']] = d
        for w in words or []:
            sel = [x for x in w.get('ids', []) if x in wid]
            if len(sel) != len(w.get('ids', [])) or w.get('action') not in ('remove', 'keep', 'default'):
                raise ValueError('Those words are not in this revision.')
            for x in sel:
                if w['action'] == 'default':
                    pl['word_actions'].pop(x, None)
                else:
                    pl['word_actions'][x] = w['action']
        if note and str(note).strip():
            pl['notes'].append(dict(text=str(note).strip()[:2000], at=now(), by='josh'))
        pl['revision'] += 1
        return self.view(pid, self._put(pid, 'edit_proposal', pl, parent=prop['id'])['id'])

    def approve_script(self, pid, proposal_id, expected_digest, note='', auto=False):
        prop = self.get(pid, proposal_id)
        if prop['kind'] != 'edit_proposal' or prop['digest'] != expected_digest:
            raise Conflict('This revision changed. Review the current one before approving.')
        src = self.get(pid, prop['payload']['sources_id'])
        if (self.latest(pid, 'edit_sources') or {}).get('id') != src['id']:
            raise Conflict('The sources were changed after this proposal. Run the proposal again.')
        err = self.sources_ok(src)
        if err:
            raise Conflict(err)
        v = self.view(pid, proposal_id)
        if v['errors']:
            raise ValueError('Fix these first: ' + ' '.join(v['errors']))
        a = self._approve(prop, dict(note=str(note)[:1000], by='josh', removals=v['summary']['removals'],
                                     meaning='Josh approved this exact script revision. Tariq may build it in Premiere.'))
        if auto:
            threading.Thread(target=self._after_script, args=(pid, prop), daemon=True).start()
        return a

    # ------------------------------------------------------------ previews

    def preview(self, pid, proposal_id, event_id, context_s=2.0):
        """Two short WAVs from the good mic: the original moment and the proposed join, with hard joins exactly as
        the native cut will have them. Audio only, so the picture and the key are not shown."""
        prop = self.get(pid, proposal_id)
        pl = prop['payload']
        ev = next((e for e in pl['events'] if e['event_id'] == event_id), None)
        if ev is None:
            raise KeyError('That event is not in this revision.')
        ctx = max(0.5, min(MAX_CONTEXT_S, float(context_s)))
        rem, _ = self.computed(prop)
        key = ep.digest(dict(mic=pl['hashes']['mic'], ev=event_id, rem=rem, ctx=ctx, v=1))[:24]
        out = self.folder(pid) / 'previews'
        out.mkdir(exist_ok=True)
        a_path, b_path = out / f'{key}-original.wav', out / f'{key}-proposed.wav'
        meta_path = out / f'{key}.json'
        if meta_path.exists():
            return json.loads(meta_path.read_text(encoding='utf-8'))
        pcm = np.fromfile(pl['pcm'], dtype='<f4')
        n = int(ctx * SR)
        lo, hi = max(0, ev['start'] - n), min(len(pcm), ev['end'] + n)
        orig = pcm[lo:hi]
        keeps = ep.keeps_from(rem, len(pcm))
        # the edited output around the same moment: kept source in order, cut at the same places
        out_audio, mark = [], None
        for a, b in keeps:
            if b <= lo - n or a >= hi + n:
                continue
            x, y = max(a, lo - n), min(b, hi + n)
            if mark is None and y > ev['start'] - 1:
                mark = sum(len(s) for s in out_audio) + max(0, min(ev['start'], y) - x)
            out_audio.append(pcm[x:y])
        prop_audio = np.concatenate(out_audio) if out_audio else np.zeros(1, dtype='<f4')
        if mark is not None:
            prop_audio = prop_audio[max(0, mark - n):mark + n + (ev['end'] - ev['start'] if not any(a <= ev['start'] and ev['end'] <= b for a, b in rem) else 0)]
        for path, data in ((a_path, orig), (b_path, prop_audio)):
            with wave.open(str(path), 'wb') as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(SR)
                w.writeframes((np.clip(data, -1, 1) * 32767).astype('<i2').tobytes())

        def peaks(x, bins=300):
            if len(x) < bins:
                return [round(float(abs(v)), 4) for v in x]
            return [round(float(np.abs(c).max()), 4) for c in np.array_split(x, bins)]
        meta = dict(event_id=event_id, context_s=ctx, original=a_path.name, proposed=b_path.name,
                    original_peaks=peaks(orig), proposed_peaks=peaks(prop_audio),
                    event_in_original=[(ev['start'] - lo) / SR, (ev['end'] - lo) / SR],
                    removed_in_view=[[(max(a, lo) - lo) / SR, (min(b, hi) - lo) / SR] for a, b in rem if b > lo and a < hi],
                    label='Good mic audio. Hard joins, exactly as the Premiere cut will have them. The picture and key are not shown.')
        write_atomic(meta_path, meta)
        return meta

    def preview_file(self, pid, name):
        if not name.endswith('.wav') or '/' in name or '\\' in name or '..' in name:
            raise KeyError('Not a preview.')
        f = self.folder(pid) / 'previews' / name
        if not f.is_file():
            raise KeyError('Not a preview.')
        return f

    # ------------------------------------------------------------ stage 4: native cut

    def cut_plan(self, pid, proposal_id, expected_digest, frame_ticks, master=None):
        """The exact native build for one approved revision. Checked again right before Premiere is asked."""
        prop = self.get(pid, proposal_id)
        if prop['digest'] != expected_digest or not self.approved(prop):
            raise Conflict('Only the approved, current script revision can be built.')
        pl = prop['payload']
        src = self.get(pid, pl['sources_id'])
        err = self.sources_ok(src)
        if err:
            raise Conflict(err)
        if not pl['sync'].get('verified'):
            raise Conflict('Sync is not verified.')
        rem, _ = self.computed(prop)
        keeps = ep.keeps_from(rem, pl['measurements']['samples'])
        mapping = ep.output_map(keeps, SR, pl['sync']['offset_seconds'], int(frame_ticks), pl.get('inserts') or [])
        files = src['payload']['files']
        return dict(project_id=pid, proposal_id=prop['id'], proposal_digest=prop['digest'], revision=pl['revision'],
                    camera_path=files['camera']['path'], mic_path=files['mic']['path'], master=master or 'GREEN_MASTER',
                    sequence_name=f"CLEAN_CUT_r{pl['revision']:02d}", map=mapping, map_digest=ep.digest(mapping))

    def build_cut(self, pid, proposal_id, expected_digest, frame_ticks, master=None):
        plan = self.cut_plan(pid, proposal_id, expected_digest, frame_ticks, master)
        prior = [c for c in self._all(pid, 'edit_cut') if c['payload'].get('plan_digest') == ep.digest(plan) and c['payload'].get('state') == 'verified']
        if prior:
            return prior[0]  # idempotent: the same approved plan is never built twice
        req = self._put(pid, 'edit_cut', dict(state='requested', plan=plan, plan_digest=ep.digest(plan)), parent=proposal_id)
        try:
            result = self.bridge_call('tq_build_cut', dict(plan=plan, request_id=req['id']))
        except Exception as e:  # noqa: BLE001
            return self._put(pid, 'edit_cut', dict(state='blocked', plan=plan, plan_digest=ep.digest(plan), error=str(e)[:500],
                                                    request=req['id']), parent=proposal_id)
        problems = verify_readback(plan, result)
        return self._put(pid, 'edit_cut', dict(state='verified' if not problems else 'failed', plan=plan, plan_digest=ep.digest(plan),
                                                result=result, problems=problems, request=req['id']), parent=proposal_id)

    def bridge_call(self, cmd, args, timeout=1800):
        """Ask the Josh Cut Pro panel in Premiere, through the helper's local bridge. Fixed command names only."""
        if cmd not in ('tq_probe', 'tq_build_cut', 'tq_master_read', 'tq_master_set', 'tq_export_frames', 'tq_save', 'tq_assemble', 'tq_export',
                       'tq_make_master', 'tq_import', 'tq_open_project', 'tq_close_project', 'tq_verify_project', 'tq_place_layers', 'tq_ops'):
            raise ValueError('Unknown Premiere command.')
        import urllib.request
        import time as _t
        import urllib.error
        # Premiere reloads the panel when a project opens, so it drops off the bridge for a moment. Wait it out.
        for attempt in range(3):
            st = {}
            for _ in range(90):
                try:
                    st = json.loads(urllib.request.urlopen(self.bridge + '/bridge/status', timeout=3).read())
                except OSError:
                    st = None
                if st and st.get('connected'):
                    break
                _t.sleep(1)
            if st is None:
                raise BridgeError('Josh Cut Pro is not running, so Premiere can\'t be reached. Start Josh Cut Pro and open Premiere with its panel.')
            if not st.get('connected'):
                raise BridgeError('Premiere\'s Josh Cut Pro panel is not connected. Open Premiere and the panel.')
            req = urllib.request.Request(self.bridge + '/bridge/call', data=json.dumps(dict(cmd=cmd, args=args)).encode(),
                                         headers={'Content-Type': 'application/json'}, method='POST')
            try:
                out = json.loads(urllib.request.urlopen(req, timeout=timeout).read())
            except urllib.error.HTTPError as e:
                try:
                    msg = json.loads(e.read()).get('error') or str(e)
                except ValueError:
                    msg = str(e)
                if 'not connected' in str(msg) and attempt < 2:
                    continue  # the panel dropped before it picked the command up, so it never ran
                raise BridgeError('Premiere said: ' + str(msg)[:400]) from None
            return out.get('result') or {}

    # ------------------------------------------------------------ stage 5: animations and final

    def visuals(self, pid, cut_id, notes=None, storyboard_digest=None, approve=False, auto=False):
        cut = self.get(pid, cut_id)
        if cut['kind'] != 'edit_cut' or cut['payload'].get('state') != 'verified':
            raise Conflict('Animations follow a verified Premiere cut.')
        prev = next((v for v in self._all(pid, 'edit_visuals') if v['parent'] == cut_id), None)
        payload = dict(notes=(prev or {}).get('payload', {}).get('notes', []), storyboard_digest=(prev or {}).get('payload', {}).get('storyboard_digest'),
                       map_digest=cut['payload']['plan']['map_digest'])
        if notes is not None and str(notes).strip():
            payload['notes'] = payload['notes'] + [dict(text=str(notes).strip()[:4000], at=now())]
        if storyboard_digest:
            payload['storyboard_digest'] = str(storyboard_digest)
        v = self._put(pid, 'edit_visuals', payload, parent=cut_id)
        if approve:
            if not payload['storyboard_digest']:
                raise ValueError('Review the storyboard before approving it.')
            self._approve(v, dict(meaning='Josh approved this storyboard for this cut. Tariq may assemble and render it locally.'))
            if auto:
                self.final(pid, v['id'])  # Josh's approval is the go-ahead to compile and export
        return v

    def final(self, pid, visuals_id):
        """Assemble, render and place Tariq's approved animations, export locally and check the file. Runs as a job."""
        v = self.get(pid, visuals_id)
        if not self.approved(v):
            raise Conflict('Approve the storyboard for the current cut first.')
        cut = self.get(pid, v['parent'])
        if (self.latest(pid, 'edit_cut') or {}).get('id') != cut['id'] or v['payload']['map_digest'] != cut['payload']['plan']['map_digest']:
            raise Conflict('The cut changed after the storyboard was approved, so its timing is stale.')
        if self.edit_review is not None:
            er = self.edit_review.review(pid)
            if v['payload'].get('storyboard_digest') != f"{er.get('slides')}|{er.get('rendered')}":
                raise Conflict('The slides changed after the storyboard was approved. Review and approve it again.')
        try:
            base = Path(self.folders.path(pid))
        except Exception:  # noqa: BLE001
            raise ValueError('This video has no folder, so there is nowhere to save the render.') from None
        with self.lock:
            t = self.jobs.get(('final', pid))
            if t and t.is_alive():
                raise Conflict('The final render is already running.')
            t = threading.Thread(target=self._final, args=(pid, v, cut, base), daemon=True)
            self.jobs[('final', pid)] = t
            t.start()
        return dict(state='started')

    def _final(self, pid, v, cut, base):
        plan = cut['payload']['plan']
        name = f"FINAL_ASSEMBLY_r{plan['revision']:02d}"
        out_dir = base / 'video' / 'exports'
        out_dir.mkdir(parents=True, exist_ok=True)
        n = 1
        while (out_dir / f'{name}_v{n:02d}.mp4').exists():
            n += 1  # never overwrite an earlier render
        output = out_dir / f'{name}_v{n:02d}.mp4'
        job = self.folder(pid) / 'final' / f'{name}_v{n:02d}'
        job.mkdir(parents=True, exist_ok=True)
        say = lambda m: write_atomic(job / 'progress.json', dict(state='running', message=m, at=now()))
        req = self._put(pid, 'edit_final', dict(state='running', visuals=v['id'], cut=cut['id'], output=str(output)), parent=v['id'])
        title = self.store.get(pid).get('name') or 'this video'
        try:
            say('Checking Premiere')
            from . import edit_master as em
            em.open_project(self, pid)  # Tariq's own project, when he built one
            check = self.bridge_call('tq_probe', dict(master=plan['master']))
            frame = check.get('frame') or [1920, 1080]
            say('Copying the approved cut to ' + name)
            self.bridge_call('tq_assemble', dict(cut=plan['sequence_name'], sequence=name))
            layers = self.animation_layers(pid, cut, frame, job, say)
            if layers['result']['layers']:
                say('Placing the animations on ' + name)
                placed = self.bridge_call('tq_place_layers', dict(sequence=name, result=layers['result'], nest=plan['master']), timeout=6 * 3600)
            else:
                placed = dict(note='No animations to place.')
            say('Rendering the video on this PC')
            res = self.bridge_call('tq_export', dict(sequence=name, output=str(output), preset=export_preset()), timeout=6 * 3600)
            say('Saving the project')
            self.bridge_call('tq_save', {})
        except Exception as e:  # noqa: BLE001
            write_atomic(job / 'progress.json', dict(state='failed', message=str(e)[:500], at=now()))
            self._tell(pid, f'I stopped putting {title} together: {str(e)[:300]}')
            return self._put(pid, 'edit_final', dict(state='blocked', error=str(e)[:500], request=req['id']), parent=v['id'])
        problems = verify_export(output, plan['map'])
        windows = verify_windows(output, layers['result'], self.ffmpeg, job / 'windows') if layers['result']['layers'] else dict(problems=[])
        problems += windows['problems']
        self._tell(pid, (f'{title} is exported to {output}. I checked its length, picture, sound and that you show in every PiP window.'
                         if not problems else f'{title} exported to {output}, but these checks failed: ' + ' '.join(problems)))
        write_atomic(job / 'progress.json', dict(state='done', message='; '.join(problems) or 'Exported and checked', at=now()))
        return self._put(pid, 'edit_final', dict(state='verified' if not problems else 'failed', result=res, output=str(output), problems=problems,
                                                  animations=dict(placed=placed, left_out=layers['problems'], presses=len(layers['shots'])),
                                                  request=req['id']), parent=v['id'])

    def animation_layers(self, pid, cut, frame, job, say=print, render_limit=None):
        """Tariq's approved deck as rendered layers for this cut: presses found in the approved transcript, mapped through the cut."""
        from . import edit_animations as ea
        from .edit_review import parse_scenes, parse_shot_list
        plan = cut['payload']['plan']
        prop = self.get(pid, cut['parent'])
        slides, shots_md = self.edit_review.deck_files(pid)
        if not slides or not shots_md:
            return dict(result=dict(layers=[], fps_num=1, fps_den=1), shots=[], problems=['There are no slides or shot list for this video.'])
        rem, _ = self.computed(prop)
        scenes = {x['n']: x for x in parse_scenes(slides.read_text(encoding='utf-8'))}
        parts = parse_shot_list(shots_md.read_text(encoding='utf-8'))
        shots, problems = ea.plan_shots(prop['payload']['words'], rem, plan['map']['pieces'], parts, scenes, plan['map']['frame_ticks'], SR)
        if render_limit is not None:
            shots = shots[:render_limit]
        scale = max(1, round(frame[0] / 1920.0, 3))
        out = Path(self.folders.path(pid)) / 'video' / 'tariq-animations'
        ea.render_shots(shots, slides, out, plan['map']['frame_ticks'], scale, self.ffmpeg, job, log=say)
        cam = self.get(pid, prop['payload']['sources_id'])['payload']
        size = (cam['headers']['camera']['width'], cam['headers']['camera']['height'])
        return dict(result=ea.layer_result(shots, plan['map']['frame_ticks'], cam['files']['camera']['path'], size, frame), shots=shots, problems=problems)

    def make_master(self, pid, source_sequence, reference=None, add_shadow=False):
        """GREEN_MASTER from a sequence Josh keyed himself. His sequence is copied, never changed."""
        if reference not in (None, '', 'data', 'exec'):
            raise ValueError('Reference key must be data or exec.')
        res = self.bridge_call('tq_make_master', dict(source=str(source_sequence), reference=reference or None, add_shadow=bool(add_shadow)))
        self._put(pid, 'edit_master', dict(result=res, source=str(source_sequence), reference=reference, add_shadow=bool(add_shadow)))
        return res

    # ------------------------------------------------------------ "Tariq, go ahead with the edit"

    def _tell(self, pid, text, via=''):
        """Tariq tells Josh where the edit is, on his desk (and wherever Josh asked from)."""
        log = self.folder(pid) / 'go-ahead.jsonl'
        with open(log, 'a', encoding='utf-8') as f:
            f.write(json.dumps(dict(at=now(), text=text)) + '\n')
        if self.agents is not None:
            try:
                self.agents.post('tariq', text, via=via)
            except Exception:  # noqa: BLE001 - the log still has it
                pass

    def go_ahead(self, pid, via=''):
        """Josh said go ahead. Tariq gathers the files, builds his Premiere project and GREEN_MASTER, transcribes and
        proposes the cut, then hands it to Josh to review. Approving the script and the storyboard carry it on."""
        self.store.get(pid)
        with self.lock:
            t = self.jobs.get(('go', pid))
            if t and t.is_alive():
                raise Conflict('Tariq is already on this edit.')
            t = threading.Thread(target=self._go, args=(pid, via), daemon=True)
            self.jobs[('go', pid)] = t
            t.start()
        return dict(state='started')

    def _go(self, pid, via):
        from . import edit_master as em
        import time as _t
        title = self.store.get(pid).get('name') or 'this video'
        try:
            src = self.latest(pid, 'edit_sources')
            if not src or self.sources_ok(src):
                found = self.suggest(pid)
                if found['ambiguous']:
                    self._tell(pid, f"I need you to pick the files for {title}. In {found['folder'] or 'the video folder'} I found "
                                    f"{len(found['videos'])} camera files and {len(found['audio'])} audio files. Choose the camera and the good mic "
                                    'on the clean cut screen, then tell me to go ahead again.', via)
                    return
                src = self.register(pid, found['videos'][0], found['audio'][0])
            files = src['payload']['files']
            premiere = None
            try:
                if not (self.data / 'look.json').is_file():
                    em.read_look(self)
                premiere = em.build_master(self, pid, files['camera']['path'], files['mic']['path'])
                self._put(pid, 'edit_master', dict(result=premiere, built_from='raw files'))
            except Exception as e:  # noqa: BLE001 - the review can go ahead without Premiere
                premiere = dict(state='blocked', error=str(e)[:500])
                self._put(pid, 'edit_master', dict(result=premiere, built_from='raw files'))
            prop = self.latest(pid, 'edit_proposal')
            if not prop or prop['payload'].get('sources_id') != src['id']:
                self.analyse(pid, src['id'], editorial=True)
                _t.sleep(1)
                while (self.progress(pid) or {}).get('state') == 'running':
                    _t.sleep(2)
                p = self.progress(pid) or {}
                if p.get('state') != 'complete':
                    self._tell(pid, f"I stopped on {title}. {p.get('message') or 'The transcript did not finish.'}", via)
                    return
                prop = self.latest(pid, 'edit_proposal')
            v = self.view(pid, prop['id'])
            s = v.get('summary') or {}
            check = s.get('cards', 0)
            if premiere.get('state') in ('built', 'exists'):
                where = f"Your timeline is set up in {Path(premiere['project']).name}, with you keyed on V3, the shadow on V2 and the background on V1."
                if premiere.get('problems'):
                    where += ' Check these in Premiere: ' + ' '.join(premiere['problems'])
            else:
                where = 'I could not set up Premiere: ' + str(premiere.get('error') or ' '.join(premiere.get('problems') or []))
            self._tell(pid, f"{title} is ready for you to review. {where} I have marked {s.get('removals', 0)} cuts in red and "
                            f"{check} moments I want you to listen to. Approve the script when you are happy and I will build the cut.", via)
        except Exception as e:  # noqa: BLE001
            self._tell(pid, f'I stopped on {title}: {str(e)[:300]}', via)

    def _after_script(self, pid, prop, via=''):
        """Josh approved the script: build the cut in Tariq's project, then hand him the slides to review."""
        from . import edit_master as em
        title = self.store.get(pid).get('name') or 'this video'
        try:
            if not em.open_project(self, pid):
                self._tell(pid, f"I can't build {title} yet, because my Premiere project for it isn't made. Tell me to go ahead with the edit first.", via)
                return
            check = self.bridge_call('tq_probe', dict(master=em.MASTER))
            if not check.get('frame_ticks'):
                raise ValueError(' '.join(check.get('notes') or ['GREEN_MASTER is missing.']))
            cut = self.build_cut(pid, prop['id'], prop['digest'], int(check['frame_ticks']))
            state = cut['payload'].get('state')
            if state != 'verified':
                self._tell(pid, f"The cut for {title} did not build cleanly: {cut['payload'].get('error') or ' '.join(cut['payload'].get('problems') or [])}", via)
                return
            self.bridge_call('tq_save', {})
            self.visuals(pid, cut['id'])
            self._tell(pid, f"The cut for {title} is built as {cut['payload']['plan']['sequence_name']}. The animations are next. "
                            'Review the slides, and when you approve the storyboard I will put it all together and export it.', via)
        except Exception as e:  # noqa: BLE001
            self._tell(pid, f'I stopped building {title}: {str(e)[:300]}', via)

    # ------------------------------------------------------------ status

    def status(self, pid):
        src, prop, cut = self.latest(pid, 'edit_sources'), self.latest(pid, 'edit_proposal'), self.latest(pid, 'edit_cut')
        vis, fin = self.latest(pid, 'edit_visuals'), self.latest(pid, 'edit_final')
        def st(key):
            if key == 'sources':
                if not src:
                    return 'needs_input', 'Choose the camera file and the good mic.'
                e = self.sources_ok(src)
                return ('stale', e) if e else ('ready', 'Chosen. Sync is checked during the proposal.')
            if key == 'proposal':
                p = self.progress(pid)
                if p and p.get('state') == 'running':
                    return 'running', p['message']
                if not prop:
                    return ('failed', p['message']) if p and p.get('state') == 'failed' else ('not_started', 'Ask Tariq to propose a cut.')
                if prop['payload']['sources_id'] != (src or {}).get('id'):
                    return 'stale', 'The sources changed after this proposal.'
                return ('approved' if self.approved(prop) else 'review'), f"Revision {prop['payload']['revision']} {'is approved' if self.approved(prop) else 'is ready for you'}."
            if key == 'script':
                return ('approved', 'Approved.') if prop and self.approved(prop) else ('waiting', 'Approve the script when it reads right.')
            if key == 'cut':
                if not cut:
                    return 'waiting', 'Not built in Premiere yet.'
                s = cut['payload']['state']
                stale = prop and cut['parent'] != prop['id']
                return ('stale', 'The script changed after this cut.') if stale else (s, cut['payload'].get('error') or ', '.join(cut['payload'].get('problems') or []) or s)
            if key == 'visuals':
                if not vis:
                    return 'waiting', 'Add notes and review the storyboard after the cut.'
                return ('approved', 'Storyboard approved.') if self.approved(vis) else ('review', 'Notes saved. Approve the storyboard when ready.')
            if key == 'final':
                return (fin['payload']['state'], fin['payload'].get('error', '')) if fin else ('waiting', 'Rendered locally after both approvals.')
        return [dict(key=k, label=l, state=st(k)[0], reason=st(k)[1]) for k, l in STAGES]


def verify_readback(plan, result):
    """Premiere's read-back of the new sequence against the plan: every piece where it should be, nothing extra."""
    problems = []
    f = int(plan['map']['frame_ticks'])
    want = [p for p in plan['map']['pieces'] if p['kind'] == 'source']
    got_v = sorted(result.get('video') or [], key=lambda x: int(x['start']))
    got_a = sorted(result.get('audio') or [], key=lambda x: int(x['start']))
    if len(got_v) != len(want):
        problems.append(f'Expected {len(want)} picture pieces, Premiere has {len(got_v)}.')
    if len([a for a in got_a if a.get('role') == 'mic']) != len(want):
        problems.append(f'Expected {len(want)} mic pieces, Premiere has {len([a for a in got_a if a.get("role") == "mic"])}.')
    for p, v in zip(want, got_v):
        if abs(int(v['start']) - p['out_start']) > f // 2 or abs(int(v['end']) - p['out_end']) > f // 2:
            problems.append(f"A picture piece sits at {int(v['start']) / ep.TICKS_PER_SECOND:.2f}s instead of {p['out_start'] / ep.TICKS_PER_SECOND:.2f}s.")
            break
    mics = [a for a in got_a if a.get('role') == 'mic']
    for p, a in zip(want, mics):
        if abs(int(a['inp']) - p['mic_in']) > f // 2 or abs(int(a['start']) - p['out_start']) > f // 2:
            problems.append(f"The mic is out of sync at {int(a['start']) / ep.TICKS_PER_SECOND:.2f}s.")
            break
    if any(a.get('role') == 'camera' for a in got_a):
        problems.append('Camera audio is on the timeline.')
    if result.get('overlaps'):
        problems.append('Pieces overlap on the timeline.')
    if result.get('gaps'):
        problems.append('There are gaps between pieces.')
    return problems


def verify_export(path, mapping):
    """The rendered file itself: it exists, has picture and sound, and runs as long as the approved cut."""
    p = Path(path)
    if not p.is_file() or p.stat().st_size == 0:
        return ['Premiere reported the export, but there is no file.']
    from dajoshi.media import probe
    try:
        h = probe(p)
    except Exception as e:  # noqa: BLE001
        return [f'The file does not open: {str(e)[:200]}']
    problems = []
    want = mapping['duration_ticks'] / mapping['ticks_per_second']
    if abs(h['duration'] - want) > 0.5:
        problems.append(f"The file runs {h['duration']:.2f}s, the approved cut {want:.2f}s.")
    if not h.get('channels'):
        problems.append('The file has no sound.')
    if not h.get('fps'):
        problems.append('The file has no picture.')
    return problems


def verify_windows(path, result, ffmpeg, folder):
    """In the exported file, the middle of every PiP and half and half shot must show a picture in its camera
    window, not the flat colour of a slide painted over it. Saves each frame it looked at."""
    from PIL import Image, ImageStat
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    fps = result['fps_num'] / result['fps_den']
    checked, problems = [], []
    for n, l in enumerate(x for x in result['layers'] if x['kind'] == 'camera_copy' and x.get('hole')):
        t = (l['start_f'] + (l['out_f'] - l['in_f']) / 2) / fps
        jpg = folder / f'window-{n:02d}.png'
        subprocess.run([ffmpeg, '-v', 'error', '-nostdin', '-y', '-ss', f'{t:.3f}', '-i', str(path), '-frames:v', '1', str(jpg)],
                       capture_output=True, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), timeout=300)
        if not jpg.is_file():
            problems.append(f'No frame at {t:.1f}s to check.')
            continue
        im = Image.open(jpg).convert('L')
        k = im.size[0] / 1920.0
        x, y, w, h = l['hole']
        box = im.crop((int((x + w * 0.1) * k), int((y + h * 0.1) * k), int((x + w * 0.9) * k), int((y + h * 0.9) * k)))
        spread = ImageStat.Stat(box).stddev[0]
        checked.append(dict(at_s=round(t, 2), name=l.get('name'), spread=round(spread, 1), frame=str(jpg)))
        if spread < 6:
            problems.append(f"{l.get('name')} at {t:.1f}s shows a flat colour in the camera window, so Josh is not in it.")
    return dict(checked=checked, problems=problems)


def _png_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest() if Path(path).is_file() else None


def native_proof(flow, pid, param='Choke', delta=4.0):
    """Premiere proof for the latest verified cut. See _proof. Writes a receipt."""
    cut = flow.latest(pid, 'edit_cut')
    if not cut or cut['payload'].get('state') != 'verified':
        raise Conflict('Build and verify a cut in Premiere first.')
    out = flow.folder(pid) / 'proof' / str(uuid.uuid4())
    r = _proof(flow, cut['payload']['plan'], out, param, delta)
    r.update(at=now(), cut=cut['id'], limits='Byte comparison of exported PNGs at three pieces. It shows the master change reached each piece. '
                                              'It does not judge key quality, motion or the full timeline.')
    write_atomic(out / 'receipt.json', r)
    return r


def _proof(flow, plan, out, param='Choke', delta=4.0):
    """Change one presenter Ultra Key value in the master, export real frames from three distant cut pieces before and
    after, check every one changed and the shadow key did not, then put the value back and export again."""
    out.mkdir(parents=True, exist_ok=True)
    src = [p for p in plan['map']['pieces'] if p['kind'] == 'source']
    pick = [src[0], src[len(src) // 2], src[-1]] if len(src) >= 3 else src
    times = [str((p['out_start'] + p['out_end']) // 2) for p in pick]
    master = plan['master']
    before_keys = flow.bridge_call('tq_master_read', dict(master=master, camera_path=plan['camera_path']))
    pres = [e for e in before_keys['effects'] if e['role'] == 'presenter' and 'ultra' in e['effect'].lower()]
    if not pres:
        raise ValueError('No Ultra Key on the presenter in the master.')
    value = next((p['value'] for p in pres[0]['params'] if p['name'].lower() == param.lower()), None)
    if not isinstance(value, (int, float)):
        raise ValueError(f'Ultra Key has no number called {param}.')
    shadow_before = [e for e in before_keys['effects'] if e['role'] == 'shadow']

    def frames(tag):
        # Premiere sometimes drops one (seen on the first frame after a build), so ask again for a set with a gap
        import time as _t
        names = [f'{tag}-{i:03d}.png' for i in range(len(times))]
        there = lambda: all((out / n).exists() or (out / (n + '.png')).exists() for n in names)
        for _ in range(3):
            r = flow.bridge_call('tq_export_frames', dict(sequence=plan['sequence_name'], times=times, dir=str(out), prefix=tag))
            t0 = _t.time()
            while _t.time() - t0 < 30 and not there():
                _t.sleep(0.5)
            if there():
                break
        return r
    frames('before')
    flow.bridge_call('tq_master_set', dict(master=master, camera_path=plan['camera_path'], param=param, value=float(value) + delta))
    frames('changed')
    after_keys = flow.bridge_call('tq_master_read', dict(master=master, camera_path=plan['camera_path']))
    flow.bridge_call('tq_master_set', dict(master=master, camera_path=plan['camera_path'], param=param, value=float(value)))
    frames('restored')
    rows = []
    for i, t in enumerate(times):
        h = {tag: _png_hash(_frame_file(out, f'{tag}-{i:03d}.png')) for tag in ('before', 'changed', 'restored')}
        rows.append(dict(time_ticks=t, exported=all(h.values()), changed=h['before'] != h['changed'], restored=h['before'] == h['restored']))
    shadow_after = [e for e in after_keys['effects'] if e['role'] == 'shadow']
    return dict(sequence=plan['sequence_name'], master=master, param=param, original=value, test_value=float(value) + delta, pieces=rows,
                shadow_unchanged=shadow_before == shadow_after, folder=str(out),
                passed=all(r['exported'] and r['changed'] for r in rows) and shadow_before == shadow_after)


def _frame_file(folder, name):
    """Premiere adds the format's extension itself, so before-000.png arrives as before-000.png.png."""
    import time as _t
    # Premiere writes the frame after the call returns, so wait until it is there, readable and no longer growing
    last, missing = -1, 0
    for _ in range(120):
        if not any(c.exists() for c in (folder / name, folder / (name + '.png'))):
            missing += 1
            if missing > 20:  # ten seconds and nothing at all: Premiere did not write it
                break
        for cand in (folder / name, folder / (name + '.png')):
            try:
                size = cand.stat().st_size
                with open(cand, 'rb'):
                    pass
            except OSError:
                continue
            if size and size == last:
                return cand
            last = size
            break
        _t.sleep(0.5)
    return folder / name


def export_preset():
    """Adobe's own H.264 Match Source preset. Premiere's exportSequence rejects an empty preset (25.6.6)."""
    for pat in ('Adobe Media Encoder */MediaIO/systempresets/*/H264 Match Source - High bitrate.epr',
                'Adobe Premiere Pro */MediaIO/systempresets/*/H264 Match Source - High bitrate.epr'):
        found = sorted(Path(r'C:\Program Files\Adobe').glob(pat))
        if found:
            return str(found[-1])
    return ''


def _layers_summary(x):
    return dict(layers=x['result'], presses=len(x['shots']), left_out=x['problems'])


HELPER = Path(r'E:\Videos\DaJoshiCut\helper\dajoshi_cut.py')


def _premiere_exe():
    found = sorted(Path(r'C:\Program Files\Adobe').glob('Adobe Premiere Pro */Adobe Premiere Pro.exe'))
    return found[-1] if found else None


def _premiere_running():
    p = subprocess.run(['tasklist', '/FI', 'IMAGENAME eq Adobe Premiere Pro.exe', '/NH'], capture_output=True, text=True,
                       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    return 'premiere' in p.stdout.lower()


def wake_premiere(flow, wait=300, say=None):
    """Get Premiere and the Josh Cut Pro panel ready without Josh: start the helper with no window if it isn't up,
    launch Premiere if it isn't running (never while OBS is open), then wait for the panel to link up. Premiere loads
    the panel when it starts. Fixed programs only, nothing from a request. Returns what it had to start."""
    import time as _t
    import urllib.request
    say = say or (lambda m: None)
    started = []

    def status():
        try:
            return json.loads(urllib.request.urlopen(flow.bridge + '/bridge/status', timeout=3).read())
        except OSError:
            return None
    st = status()
    if st and st.get('connected'):
        return dict(started=started)
    if st is None:
        if flow.bridge.rstrip('/') != BRIDGE.rstrip('/'):
            raise BridgeError('The Josh Cut Pro helper on ' + flow.bridge + ' is not running.')
        pyw = Path(sys.executable).with_name('pythonw.exe')
        say('Starting the Josh Cut Pro helper')
        subprocess.Popen([str(pyw if pyw.exists() else sys.executable), str(HELPER), '--no-window'], cwd=str(HELPER.parent),
                         creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0) | getattr(subprocess, 'DETACHED_PROCESS', 0))
        started.append('helper')
        for _ in range(40):
            if status() is not None:
                break
            _t.sleep(0.5)
        if status() is None:
            raise BridgeError('The Josh Cut Pro helper would not start. Its log is E:\\Videos\\DaJoshiCut\\Projects\\helper.log.')
    if not _premiere_running():
        try:
            from worker import guard_recording
            guard_recording()
        except RuntimeError as e:
            raise BridgeError(str(e)) from None
        exe = _premiere_exe()
        if not exe:
            raise BridgeError('Premiere Pro is not installed where Josh OS looks for it (C:\\Program Files\\Adobe).')
        say('Opening Premiere')
        subprocess.Popen([str(exe)], cwd=str(exe.parent))
        started.append('premiere')
    say('Waiting for the Josh Cut Pro panel in Premiere')
    t0 = _t.time()
    while _t.time() - t0 < wait:
        st = status()
        if st and st.get('connected'):
            return dict(started=started, seconds=round(_t.time() - t0))
        _t.sleep(2)
    raise BridgeError('Premiere is open but the Josh Cut Pro panel did not connect within %d seconds. '
                      'Premiere may be showing a message that needs Josh.' % wait)


def _close_copy(flow, copy):
    """Close the test copy. After a save Premiere reports its path with a long-path prefix, which panel 0.8.8
    compares exactly, so pass the path the way Premiere reports it, once it is checked to be the same file."""
    def norm(p):
        return str(p or '').replace('/', '\\').lower().removeprefix('\\\\?\\')
    now_open = (flow.bridge_call('tq_probe', dict(master='GREEN_MASTER')) or {}).get('project') or ''
    if norm(now_open) != norm(copy):
        raise ValueError('The open project is not the test copy, so nothing was closed.')
    return flow.bridge_call('tq_close_project', dict(path=now_open))


def native_test(flow, pid, project_path, source_sequence, reference=None, add_shadow=False, render_limit=3):
    """The whole native route on a COPY of a Premiere project, as a background job with a receipt.

    Copies the .prproj into Josh OS's own test folder (the original is only read), opens the copy alongside
    whatever Josh has open, makes GREEN_MASTER from his keyed sequence, imports the good mic, builds the approved
    cut, proves a key change reaches three pieces, assembles with a few of Tariq's animations, exports, saves,
    closes, reopens and checks the sequences, the nest and the media links. Only the copy is ever changed."""
    import shutil as _sh
    src = Path(str(project_path).strip().strip('"'))
    if src.suffix.lower() != '.prproj' or not src.is_file():
        raise ValueError('Choose the Premiere project (.prproj) to copy.')
    prop = flow.latest(pid, 'edit_proposal')
    if not prop or not flow.approved(prop):
        raise Conflict('Approve the script first. The test builds that exact revision.')
    test = flow.folder(pid) / 'native-test' / now().replace(':', '').replace('-', '').replace('.', '')[:15]
    test.mkdir(parents=True)
    copy = test / (src.stem + ' - Tariq test copy.prproj')
    before = sha256_file(src)
    _sh.copy2(src, copy)

    def run():
        steps = []
        receipt = dict(original=str(src), original_sha256=before, copy=str(copy), started=now(), steps=steps)

        def step(name, fn):
            write_atomic(test / 'progress.json', dict(state='running', message=name, at=now()))
            try:
                r = fn()
                steps.append(dict(step=name, ok=True, result=r))
                return r
            except Exception as e:  # noqa: BLE001
                steps.append(dict(step=name, ok=False, error=str(e)[:500]))
                raise
        try:
            step('Get Premiere ready', lambda: wake_premiere(flow))
            step('Open the test copy', lambda: flow.bridge_call('tq_open_project', dict(path=str(copy))))
            step('Make GREEN_MASTER', lambda: flow.bridge_call('tq_make_master', dict(source=source_sequence, reference=reference, add_shadow=add_shadow)))
            check = step('Check the master', lambda: flow.bridge_call('tq_probe', dict(master='GREEN_MASTER')))
            if not check.get('ok'):
                raise ValueError('The master is not ready: ' + ' '.join(check.get('notes') or []))
            plan = flow.cut_plan(pid, prop['id'], prop['digest'], int(check['frame_ticks']))
            step('Import the good mic', lambda: flow.bridge_call('tq_import', dict(paths=[plan['mic_path']])))
            built = step('Build the cut', lambda: flow.bridge_call('tq_build_cut', dict(plan=plan, request_id='native-test'), timeout=6 * 3600))
            problems = verify_readback(plan, built)
            steps.append(dict(step='Read back the cut', ok=not problems, result=problems))
            proof = step('Prove a key change reaches every piece', lambda: _proof(flow, plan, test / 'proof'))
            steps.append(dict(step='Key change reached every piece, shadow untouched', ok=bool(proof.get('passed')), result=proof))
            fake_cut = dict(payload=dict(plan=plan), parent=prop['id'])
            layers = step('Render a few of Tariq\'s animations', lambda: _layers_summary(
                flow.animation_layers(pid, fake_cut, check.get('frame') or [1920, 1080], test, render_limit=render_limit)))
            final = f"FINAL_ASSEMBLY_r{plan['revision']:02d}"
            step('Copy the cut to the assembly', lambda: flow.bridge_call('tq_assemble', dict(cut=plan['sequence_name'], sequence=final)))
            if layers['layers']['layers']:
                step('Place the animations', lambda: flow.bridge_call('tq_place_layers', dict(sequence=final, result=layers['layers'], nest='GREEN_MASTER'),
                                                                      timeout=6 * 3600))
            out = test / f'{final}_test.mp4'
            step('Export on this PC', lambda: flow.bridge_call('tq_export', dict(sequence=final, output=str(out), preset=export_preset()), timeout=6 * 3600))
            bad = verify_export(out, plan['map'])
            steps.append(dict(step='Check the exported file', ok=not bad, result=bad))
            windows = verify_windows(out, layers['layers'], flow.ffmpeg, test / 'windows')
            steps.append(dict(step='Josh shows in every camera window', ok=not windows['problems'], result=windows))
            step('Save the test copy', lambda: flow.bridge_call('tq_save', {}))
            step('Close the test copy', lambda: _close_copy(flow, copy))
            step('Reopen the test copy', lambda: flow.bridge_call('tq_open_project', dict(path=str(copy))))
            again = step('Check it after reopening', lambda: flow.bridge_call('tq_verify_project', dict(
                expect=['GREEN_MASTER', plan['sequence_name'], final], cut=plan['sequence_name'], master='GREEN_MASTER')))
            steps.append(dict(step='Sequences, nest and media links after reopening',
                              ok=not again.get('missing') and not again.get('offline') and again.get('cut_is_master_nests') is True, result=again))
            step('Close the test copy again', lambda: _close_copy(flow, copy))
        except Exception as e:  # noqa: BLE001
            receipt['stopped'] = str(e)[:500]
        receipt['original_unchanged'] = sha256_file(src) == before
        receipt['passed'] = all(s['ok'] for s in steps) and 'stopped' not in receipt and receipt['original_unchanged']
        receipt['finished'] = now()
        receipt['limits'] = ('Native Premiere on a copy of one project. Frame comparisons prove the master change reached three pieces, '
                             'not key quality across the whole video. No listening pass.')
        write_atomic(test / 'receipt.json', receipt)
        write_atomic(test / 'progress.json', dict(state='done', at=now(),
                                                  message='Passed' if receipt['passed'] else 'Stopped: ' + receipt.get('stopped', 'a check failed')))
        flow._put(pid, 'edit_native_test', receipt)

    threading.Thread(target=run, daemon=True).start()
    return dict(state='started', folder=str(test), copy=str(copy))
