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
    tmp.replace(path)


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
        events = ep.apply_editorial(events, words, removals, pause_d)
        return events, dict(ran=True, reply=str(reply)[:3000], removals=len(removals), pauses=len(pause_d),
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

    def approve_script(self, pid, proposal_id, expected_digest, note=''):
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
        return self._approve(prop, dict(note=str(note)[:1000], by='josh', removals=v['summary']['removals'],
                                        meaning='Josh approved this exact script revision. Tariq may build it in Premiere.'))

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
        if cmd not in ('tq_probe', 'tq_build_cut', 'tq_master_read', 'tq_master_set', 'tq_export_frames', 'tq_save', 'tq_assemble', 'tq_export'):
            raise ValueError('Unknown Premiere command.')
        import urllib.request
        try:
            st = json.loads(urllib.request.urlopen(self.bridge + '/bridge/status', timeout=3).read())
        except OSError:
            raise BridgeError('Josh Cut Pro is not running, so Premiere can\'t be reached. Start Josh Cut Pro and open Premiere with its panel.') from None
        if not st.get('connected'):
            raise BridgeError('Premiere\'s Josh Cut Pro panel is not connected. Open Premiere and the panel.')
        import urllib.error
        req = urllib.request.Request(self.bridge + '/bridge/call', data=json.dumps(dict(cmd=cmd, args=args)).encode(),
                                     headers={'Content-Type': 'application/json'}, method='POST')
        try:
            out = json.loads(urllib.request.urlopen(req, timeout=timeout).read())
        except urllib.error.HTTPError as e:
            try:
                msg = json.loads(e.read()).get('error') or str(e)
            except ValueError:
                msg = str(e)
            raise BridgeError('Premiere said: ' + str(msg)[:400]) from None
        return out.get('result') or {}

    # ------------------------------------------------------------ stage 5: animations and final

    def visuals(self, pid, cut_id, notes=None, storyboard_digest=None, approve=False):
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
        return v

    def final(self, pid, visuals_id):
        v = self.get(pid, visuals_id)
        if not self.approved(v):
            raise Conflict('Approve the storyboard for the current cut first.')
        cut = self.get(pid, v['parent'])
        if (self.latest(pid, 'edit_cut') or {}).get('id') != cut['id'] or v['payload']['map_digest'] != cut['payload']['plan']['map_digest']:
            raise Conflict('The cut changed after the storyboard was approved, so its timing is stale.')
        plan = cut['payload']['plan']
        name = f"FINAL_ASSEMBLY_r{plan['revision']:02d}"
        try:
            base = Path(self.folders.path(pid))
        except Exception:  # noqa: BLE001
            raise ValueError('This video has no folder, so there is nowhere to save the render.') from None
        out_dir = base / 'video' / 'exports'
        out_dir.mkdir(parents=True, exist_ok=True)
        n = 1
        while (out_dir / f'{name}_v{n:02d}.mp4').exists():
            n += 1  # never overwrite an earlier render
        output = out_dir / f'{name}_v{n:02d}.mp4'
        req = self._put(pid, 'edit_final', dict(state='requested', visuals=visuals_id, cut=cut['id'], output=str(output)), parent=visuals_id)
        try:
            self.bridge_call('tq_assemble', dict(cut=plan['sequence_name'], sequence=name))
            res = self.bridge_call('tq_export', dict(sequence=name, output=str(output)), timeout=6 * 3600)
        except Exception as e:  # noqa: BLE001
            return self._put(pid, 'edit_final', dict(state='blocked', error=str(e)[:500], request=req['id']), parent=visuals_id)
        problems = verify_export(output, plan['map'])
        return self._put(pid, 'edit_final', dict(state='verified' if not problems else 'failed', result=res, output=str(output), problems=problems,
                                                  request=req['id']), parent=visuals_id)

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


def _png_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest() if Path(path).is_file() else None


def native_proof(flow, pid, param='Choke', delta=4.0):
    """Premiere proof for one verified cut: change one presenter Ultra Key value in GREEN_MASTER, export real frames
    from three distant cut pieces before and after, check every one changed and the shadow key did not, then put the
    value back and check the frames match the originals again. Writes a receipt. Needs Premiere open with the panel."""
    cut = flow.latest(pid, 'edit_cut')
    if not cut or cut['payload'].get('state') != 'verified':
        raise Conflict('Build and verify a cut in Premiere first.')
    plan = cut['payload']['plan']
    src = [p for p in plan['map']['pieces'] if p['kind'] == 'source']
    pick = [src[0], src[len(src) // 2], src[-1]] if len(src) >= 3 else src
    times = [str((p['out_start'] + p['out_end']) // 2) for p in pick]
    out = flow.folder(pid) / 'proof' / str(uuid.uuid4())
    out.mkdir(parents=True)
    master = plan['master']
    before_keys = flow.bridge_call('tq_master_read', dict(master=master, camera_path=plan['camera_path']))
    pres = [e for e in before_keys['effects'] if e['role'] == 'presenter' and 'ultra' in e['effect'].lower()]
    if not pres:
        raise ValueError('No Ultra Key on the presenter in the master.')
    value = next((p['value'] for p in pres[0]['params'] if p['name'].lower() == param.lower()), None)
    if not isinstance(value, (int, float)):
        raise ValueError(f'Ultra Key has no number called {param}.')
    shadow_before = [e for e in before_keys['effects'] if e['role'] == 'shadow']
    frames = lambda tag: flow.bridge_call('tq_export_frames', dict(sequence=plan['sequence_name'], times=times, dir=str(out), prefix=tag))
    frames('before')
    flow.bridge_call('tq_master_set', dict(master=master, camera_path=plan['camera_path'], param=param, value=float(value) + delta))
    frames('changed')
    after_keys = flow.bridge_call('tq_master_read', dict(master=master, camera_path=plan['camera_path']))
    flow.bridge_call('tq_master_set', dict(master=master, camera_path=plan['camera_path'], param=param, value=float(value)))
    frames('restored')
    rows = []
    for i, t in enumerate(times):
        h = {tag: _png_hash(out / f'{tag}-{i:03d}.png') for tag in ('before', 'changed', 'restored')}
        rows.append(dict(time_ticks=t, exported=all(h.values()), changed=h['before'] != h['changed'], restored=h['before'] == h['restored']))
    shadow_after = [e for e in after_keys['effects'] if e['role'] == 'shadow']
    receipt = dict(at=now(), cut=cut['id'], sequence=plan['sequence_name'], master=master, param=param, original=value, test_value=float(value) + delta,
                   pieces=rows, shadow_unchanged=shadow_before == shadow_after, folder=str(out),
                   passed=all(r['exported'] and r['changed'] for r in rows) and shadow_before == shadow_after,
                   limits='Byte comparison of exported PNGs at three pieces. It shows the master change reached each piece. '
                          'It does not judge key quality, motion or the full timeline. Save and reopen the project to check that too.')
    write_atomic(out / 'receipt.json', receipt)
    return receipt
