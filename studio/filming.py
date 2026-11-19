"""Ready to film: one preparation for a YouTube video, shared by the Film button on the video page and by Sam when Josh
tells him "I'm ready to record this video", in Josh OS or on Discord.

It only prepares. It never starts or stops recording or streaming, and it never runs a capture test. In order:
1. Find the video. A name that matches more than one video stops and asks which.
2. Take the approved script. The newest approved version must still match the script exactly, or nothing happens.
3. Turn it into teleprompter copy. Headings (Hook, Section 2, "Mistake 1 · ...", markdown #), bylines and stage directions
   ([On screen], (pause), "B-roll:" lines) are taken out, and every spoken word is kept. A check compares the words before
   and after and stops if any spoken word went missing.
4. Check nothing is recording, streaming or rendering, and that no lesson recording session is open.
5. Make this the video being recorded (the same step as "I'm recording this now"), so Lock Take files the take here.
6. Run Josh's own Stream Deck YouTube filming launcher (recording/youtube.vbs), the same path his button uses, and wait
   for it to report OBS ready in YouTube mode (recording/last-ready.json).
7. Point OBS's YouTube recording folder at this video's Recorded/Input, then read it back to check. Only that setting is
   changed, and only while OBS is idle on the YouTube profile. The Lessons profile is never touched.
8. The teleprompter. Camera Hub keeps each script as Texts/<GUID>.json and the list of scripts in its library as
   "applogic.prompter.libraryList" in AppSettings.json. It reads the files only when it starts (LoadFromFiles in its log)
   and writes them when a script is saved in Prompter > Scripts (StoreTextSourcesInternal), so a file written by anyone
   else while it runs is never loaded. Josh OS therefore never writes into Camera Hub. It looks for a script in the library
   whose saved words are exactly this version's copy. If there is one, it selects it on the configured Prompter, sets text
   mode and reads back the selection, mode, chapter count and device through Camera Hub's local control connection. If
   there isn't, it puts the copy on the clipboard and asks Josh to press +, paste and Save in Prompter > Scripts, then
   press Check teleprompter, which runs only this step. Content is matched word for word, so an older version with the
   same number of parts never counts.
Each step is recorded with what happened. A failure part way stops there and says what's done and what isn't.
Pressing Film twice runs it once. Pressing again after it's ready reuses the result unless the script changed.
"""
import hashlib
import json
import re
import subprocess
import threading
import time as _time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .store import Conflict, now

UTC = timezone.utc
STREAM_DECK = Path(r'C:\Users\Anigma PC\Documents\Stream Deck Studio')
LAUNCHER = STREAM_DECK / 'recording' / 'youtube.vbs'
READY_FILE = STREAM_DECK / 'recording' / 'last-ready.json'
MESSAGE_FILE = STREAM_DECK / 'recording' / 'last-message.txt'
PROMPTER_CONFIG = STREAM_DECK / 'Controls' / 'config.json'
NODE = r'C:\Users\Anigma PC\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe'
DEFAULT_RECORD_DIR = 'E:/Videos/Input'
REUSE_MINUTES = 10
LAUNCH_WAIT_S = 120
STEPS = ('video', 'script', 'checks', 'active', 'launcher', 'obs_folder', 'prompter')
LABELS = {'video': 'Video', 'script': 'Approved script', 'checks': 'Nothing recording or rendering', 'active': 'Set as the video being recorded',
          'launcher': 'YouTube filming setup (Stream Deck launcher)', 'obs_folder': "OBS saves to this video's folder",
          'prompter': 'Script on the Camera Hub teleprompter'}

# Teleprompter copy ------------------------------------------------------------------------------------------------
NUMBER = r'(\d+|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|[ivx]+)'
NAMED = re.compile(r'^(hook|intro|introduction|signpost|close|closing|outro|cta|call to action|recap|summary|conclusion|bridge|'
                   r'transition|end screen|end card|cold open|teaser|(section|part|chapter|mistake|sign|step|point|tip|reason|lesson|'
                   r'myth|rule|habit|question)\s*' + NUMBER + r'\b.*)$', re.I)
DIRECTION_LINE = re.compile(r'^\s*(b-?roll|broll|visual|visuals|on screen|on-screen|graphic|graphics|cut to|sfx|music|note|editor|'
                            r'text on screen|lower third|overlay|insert)\s*[:\-]', re.I)
CUE = re.compile(r'\(\s*(pause|beat|long pause|b-?roll|cut|on screen[^)]*|laughs?|smiles?|look[^)]*|gestures?[^)]*|points?[^)]*|'
                 r'lean[^)]*|breathe|slow(er)?|faster|emphasis|hold[^)]*|camera[^)]*)\s*\)', re.I)
BRACKET = re.compile(r'\[[^\]\n]*\]')
WORD = re.compile(r"[A-Za-z0-9\u00C0-\u024F]+(?:['\u2019][A-Za-z]+)*")


def words(text):
    return [w.lower().replace('\u2019', "'") for w in WORD.findall(text or '')]


def is_heading(line):
    s = line.strip().strip('*_').strip()
    if not s:
        return False
    if re.match(r'^\s{0,3}#{1,6}\s', line) or re.fullmatch(r'[-=*_]{3,}', s):
        return True
    if s.endswith(('.', '?', '!', '"', '\u201d', '\u2026', ',')):
        return False
    bare = s.rstrip(':').strip()
    n = len(bare.split())
    if n <= 12 and NAMED.match(bare):
        return True
    if n <= 12 and ' \u00b7 ' in bare:
        return True
    return False


def teleprompter(script):
    """Spoken copy for the prompter. Returns dict(chapters, text, removed, words) and raises ValueError if any spoken
    word would be lost."""
    kept_lines, removed = [], []
    for line in (script or '').replace('\r\n', '\n').split('\n'):
        if is_heading(line) or DIRECTION_LINE.match(line):
            if line.strip():
                removed.append(line.strip())
            kept_lines.append('')
            continue
        cleaned = line
        for pat in (BRACKET, CUE):
            for m in pat.finditer(cleaned):
                removed.append(m.group(0))
            cleaned = pat.sub(' ', cleaned)
        cleaned = re.sub(r'\*\*|__|(?<!\w)[*_](?=\w)|(?<=\w)[*_](?!\w)', '', cleaned)
        cleaned = re.sub(r'^\s*[-*\u2022]\s+', '', cleaned)
        cleaned = re.sub(r'[ \t]{2,}', ' ', cleaned).strip()
        kept_lines.append(cleaned)
    chapters, cur = [], []
    for line in kept_lines + ['']:
        if line:
            cur.append(line)
        elif cur:
            chapters.append('\n'.join(cur))
            cur = []
    out = words('\n'.join(chapters))
    original, gone = words(script), words(' '.join(removed))
    # Every word is either in the copy or in something that was taken out, in the same order.
    it = iter(original)
    if len(out) + len(gone) != len(original) or not all(any(w == o for o in it) for w in out):
        raise ValueError("The teleprompter copy didn't keep every spoken word, so it wasn't used.")
    if not out:
        raise ValueError('The approved script has no spoken words in it.')
    return dict(chapters=chapters, text='\n\n'.join(chapters), removed=removed, words=len(out))


# OBS: only the record folder, only while idle --------------------------------------------------------------------
def film_obs():
    from .lessons import OBS

    class FilmOBS(OBS):
        ALLOWED = {'GetRecordStatus', 'GetStreamStatus', 'GetVirtualCamStatus', 'GetReplayBufferStatus', 'GetProfileList',
                   'GetRecordDirectory', 'SetRecordDirectory'}

        def call(self, kind, data=None):
            if kind not in self.ALLOWED:
                raise ValueError('Unsupported OBS operation')
            rid = str(uuid.uuid4())
            self.ws.send(json.dumps({'op': 6, 'd': {'requestType': kind, 'requestId': rid, 'requestData': data or {}}}))
            while True:
                msg = json.loads(self.ws.recv())
                if msg.get('op') == 7 and msg['d']['requestId'] == rid:
                    d = msg['d']
                    if not d['requestStatus']['result']:
                        raise ValueError('OBS could not complete ' + kind + ': ' + str(d['requestStatus'].get('comment') or ''))
                    return d.get('responseData', {})
    return FilmOBS()


def set_record_folder(obs_factory, folder, profile='YouTube'):
    """Point the active YouTube profile's recording folder at folder and read it back. Returns (previous, now)."""
    with obs_factory() as obs:
        obs.idle()
        current = obs.call('GetProfileList').get('currentProfileName')
        if current != profile:
            raise Conflict(f"OBS is on the {current or 'unknown'} profile, not {profile}, so its recording folder was left alone.")
        before = obs.call('GetRecordDirectory').get('recordDirectory', '')
        want = str(folder).replace('\\', '/')
        if before.replace('\\', '/').rstrip('/').lower() != want.rstrip('/').lower():
            obs.idle()
            obs.call('SetRecordDirectory', {'recordDirectory': want})
        after = obs.call('GetRecordDirectory').get('recordDirectory', '')
        if after.replace('\\', '/').rstrip('/').lower() != want.rstrip('/').lower():
            raise ValueError(f"OBS still reports {after} as its recording folder.")
        return before, after


# Camera Hub ---------------------------------------------------------------------------------------------------------
def texts_dir():
    import os
    return Path(os.environ.get('APPDATA', '')) / 'Elgato' / 'CameraHub' / 'Texts'


def prompter_device():
    try:
        return json.loads(PROMPTER_CONFIG.read_text(encoding='utf-8'))['prompter']['deviceId']
    except (OSError, ValueError, KeyError, TypeError):
        return ''


SAVE_WAIT_S = 180   # Camera Hub saves a changed script by itself, about a minute after the paste


def add_script(name, text):
    """Camera Hub's own Scripts +, name, Add and paste, driven through Windows UI Automation (camera_hub_add_script.ps1)."""
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        f = Path(tmp) / 'script.txt'
        f.write_text(text, encoding='utf-8')
        p = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File',
                            str(Path(__file__).with_name('camera_hub_add_script.ps1')), '-Name', name, '-TextFile', str(f)],
                           capture_output=True, text=True, timeout=90, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    try:
        return json.loads((p.stdout or '').strip().splitlines()[-1])
    except (ValueError, IndexError):
        return dict(ok=False, error='Camera Hub control gave no answer: ' + (p.stderr or '').strip()[-200:])


class NeedsPaste(Exception):
    """The script isn't in Camera Hub's library yet, so Josh pastes it once."""


def to_clipboard(text):
    p = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command',
                        '[Console]::InputEncoding=[Text.Encoding]::UTF8; Set-Clipboard -Value ([Console]::In.ReadToEnd())'],
                       input=text, text=True, encoding='utf-8', capture_output=True, timeout=15,
                       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if p.returncode:
        raise OSError('Clipboard not set')


def run_prompter(args, timeout=30):
    p = subprocess.run([NODE, str(Path(__file__).with_name('film_prompter.mjs')), *args], capture_output=True, text=True, timeout=timeout,
                       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    try:
        out = json.loads((p.stdout or '').strip().splitlines()[-1])
    except (ValueError, IndexError):
        raise ValueError('Camera Hub did not answer: ' + ((p.stderr or p.stdout or '').strip()[-200:] or 'no output')) from None
    return out


class Filming:
    def __init__(self, store, data_root, folders=None, pieces=None, recording=None, busy=None, launcher=None, obs=None, prompter=None,
                 texts=None, ready_file=READY_FILE, message_file=MESSAGE_FILE, device=None, app_settings=None, clip=None, adder=None):
        self.store, self.folders, self.pieces, self.recording = store, folders, pieces, recording
        self.busy = busy or (lambda: '')
        self.launcher = launcher or (lambda: subprocess.Popen(['wscript.exe', str(LAUNCHER)], creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)))
        self.obs = obs or film_obs
        self.prompter = prompter or run_prompter
        self.texts = texts or texts_dir
        self.app_settings = app_settings or (lambda: Path(self.texts()).parent / 'AppSettings.json')
        self.clip = clip or to_clipboard
        self.adder = adder or add_script
        self.save_wait_s = SAVE_WAIT_S
        self.ready_file, self.message_file = Path(ready_file), Path(message_file)
        self.device = device if device is not None else prompter_device()
        self.dir = Path(data_root) / 'filming'
        self.dir.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self._sleep = _time.sleep
        with store.connect() as c:
            c.execute('CREATE TABLE IF NOT EXISTS film_preps(id TEXT PRIMARY KEY,project_id TEXT NOT NULL,body TEXT NOT NULL)')
            for r in c.execute('SELECT id,body FROM film_preps').fetchall():
                b = json.loads(r['body'])
                if b.get('state') == 'running':
                    b.update(state='interrupted', message='Josh OS stopped while this was getting ready. Press Film again.')
                    c.execute('UPDATE film_preps SET body=? WHERE id=?', (json.dumps(b), r['id']))

    # Records --------------------------------------------------------------------------------------------------------
    def _save(self, b):
        b['updated_at'] = now()
        with self.store.connect() as c:
            c.execute('INSERT OR REPLACE INTO film_preps VALUES(?,?,?)', (b['id'], b['project_id'], json.dumps(b)))
        return b

    def latest(self, pid):
        with self.store.connect() as c:
            r = c.execute('SELECT body FROM film_preps WHERE project_id=? ORDER BY rowid DESC LIMIT 1', (pid,)).fetchone()
        return json.loads(r['body']) if r else None

    # Finding the video and its script ----------------------------------------------------------------------------
    def resolve(self, pid=None, query=None):
        """The project for an id, or for a name Josh gave. Raises LookupError with the choices when it isn't clear."""
        if pid:
            p = self.store.get(pid)
            if p.get('archived'):
                raise LookupError(f"{p['name']} is archived.")
            return p
        q = re.sub(r'\s+', ' ', str(query or '')).strip().lower()
        if not q:
            raise LookupError('Which video? Give me its name.')
        live = [p for p in self.store.projects() if not p.get('archived')]
        exact = [p for p in live if p['name'].strip().lower() == q]
        if len(exact) == 1:
            return exact[0]
        want = set(words(q)) - {'the', 'a', 'an', 'video', 'this', 'that', 'one', 'and', 'you', 'to', 'it'}
        scored = []
        for p in live:
            have = set(words(p['name']))
            hit = len(want & have)
            if want and (q in p['name'].lower() or hit >= max(1, (len(want) + 1) // 2)):
                scored.append((hit + (2 if q in p['name'].lower() else 0), p))
        if not scored:
            raise LookupError(f'I couldn\'t find a video called "{query}".')
        scored.sort(key=lambda x: -x[0])
        best = [p for s, p in scored if s == scored[0][0]]
        if len(best) > 1:
            raise LookupError('That could be ' + ' or '.join(f'"{p["name"]}"' for p in best[:5]) + '. Which one?')
        return best[0]

    def approved_script(self, p):
        """The approved version that still matches the script exactly. Raises ValueError otherwise."""
        script = p.get('script') or ''
        sha = hashlib.sha256(script.encode('utf-8')).hexdigest()
        with self.store.connect() as c:
            exists = c.execute("SELECT 1 FROM sqlite_master WHERE name='script_versions'").fetchone()
            rows = c.execute('SELECT body FROM script_versions WHERE project_id=? ORDER BY rowid DESC', (p['id'],)).fetchall() if exists else []
        approved = [json.loads(r['body']) for r in rows]
        approved = [v for v in approved if v.get('approval') == 'approved']
        if not approved:
            raise ValueError(f"{p['name']} has no approved script yet. Approve it on the Script tab first.")
        match = [v for v in approved if v.get('sha256') == sha]
        if not match or not script.strip():
            raise ValueError(f"The script for {p['name']} changed after it was approved. Approve the version you'll read first.")
        return match[0], script

    # The preparation --------------------------------------------------------------------------------------------------
    def prepare(self, pid=None, query=None, via='joshos', force=False):
        """Get everything ready to film one video. Returns the preparation record. Never records."""
        try:
            p = self.resolve(pid, query)
        except LookupError as e:
            return dict(state='ambiguous' if 'Which one' in str(e) or 'Which video' in str(e) else 'not_found', message=str(e), steps=[])
        last = self.latest(p['id'])
        if (not force and last and last.get('state') == 'needs_you' and last.get('finished_at')
                and datetime.now(UTC) - datetime.fromisoformat(last['finished_at']) < timedelta(hours=2)):
            return self.check_prompter(p['id'])  # everything else is done; only the teleprompter is left
        if not self.lock.acquire(blocking=False):
            return dict(state='busy', project_id=p['id'], message='Already getting a video ready to film. Give it a moment.', steps=[])
        try:
            return self._prepare(p, via, force)
        finally:
            self.lock.release()

    def _prepare(self, p, via, force):
        last = self.latest(p['id'])  # before this run saves anything
        rec = dict(id=str(uuid.uuid4()), project_id=p['id'], name=p['name'], via=via, state='running', started_at=now(),
                   steps=[dict(key=k, label=LABELS[k], state='waiting', detail='') for k in STEPS])
        step = lambda k: next(s for s in rec['steps'] if s['key'] == k)  # noqa: E731

        def done(k, detail='', state='done'):
            step(k).update(state=state, detail=detail)
            self._save(rec)

        def fail(k, message, manual=''):
            step(k).update(state='failed', detail=message)
            for s in rec['steps']:
                if s['state'] == 'waiting':
                    s['state'] = 'skipped'
            finished = [s['label'] for s in rec['steps'] if s['state'] == 'done' and s['key'] not in ('video', 'script')]
            rec.update(state='failed' if not finished else 'partial', message=message, manual=manual, finished_at=now(),
                       done=finished)
            return self._save(rec)

        done('video', p['name'])
        try:
            version, script = self.approved_script(p)
            copy = teleprompter(script)
        except ValueError as e:
            return fail('script', str(e))
        rec.update(script_version=version['id'], script_sha=version['sha256'], prompter_words=copy['words'], prompter_text=copy['text'], removed=copy['removed'][:80],
                   chapters=len(copy['chapters']))
        done('script', f"Approved version, {copy['words']} spoken words in {len(copy['chapters'])} parts. "
                       f"{len(copy['removed'])} headings or directions left off the prompter.")
        if (not force and last and last.get('state') == 'ready' and last.get('script_sha') == version['sha256']
                and datetime.now(UTC) - datetime.fromisoformat(last['finished_at']) < timedelta(minutes=REUSE_MINUTES)):
            with self.store.connect() as c:  # this press changed nothing, so it leaves no record of its own
                c.execute('DELETE FROM film_preps WHERE id=?', (rec['id'],))
            return dict(last, reused=True, message=f"{p['name']} was already set up a few minutes ago, so nothing was run again.")
        why = self.busy()
        if why:
            return fail('checks', f'{why}. Nothing was changed. Try again when it has finished.')
        try:
            if self.recording is not None:
                with self.store.connect() as c:
                    self.recording.no_lesson(c)
        except Conflict as e:
            return fail('checks', str(e))
        done('checks', 'OBS is idle and nothing is rendering.')
        folder = None
        try:
            root = self.folders.path(p['id']) if self.folders else None
            if not root:
                raise ValueError('This video has no folder yet, so there is nowhere to save the recording.')
            folder = Path(root) / 'Recorded' / 'Input'
            folder.mkdir(parents=True, exist_ok=True)
            (Path(root) / 'script').mkdir(exist_ok=True)
            (Path(root) / 'script' / 'teleprompter.txt').write_text(copy['text'], encoding='utf-8')
            if self.pieces is not None:
                self.pieces.start(p['id'])
        except (ValueError, OSError, Conflict, KeyError) as e:
            return fail('active', str(e))
        rec['folder'] = str(folder)
        done('active', f'Lock Take will file the take in {folder}.')
        started = datetime.now(UTC)
        try:
            self.launcher()
        except OSError as e:
            return fail('launcher', f"The Stream Deck YouTube launcher couldn't start: {e}")
        ready, message = self.wait_ready(started)
        if not ready:
            return fail('launcher', message or "The YouTube filming setup didn't report back in time.",
                        manual='Check OBS, then press Film again. Nothing was recorded.')
        done('launcher', f"OBS is open in YouTube mode ({ready.get('scene') or ready.get('profile') or 'YouTube'}).")
        try:
            before, after = set_record_folder(self.obs, folder)
        except Exception as e:  # noqa: BLE001 - anything OBS says goes back to Josh
            return fail('obs_folder', f"OBS's recording folder wasn't changed: {str(e)[:300]}",
                        manual=f'Set the recording path in OBS to {folder} yourself, or press Film again.')
        rec['previous_record_dir'] = before
        done('obs_folder', f'Recordings now save to {after}.')
        return self._prompter_step(rec, p, copy)

    def _prompter_step(self, rec, p, copy):
        step = next(s for s in rec['steps'] if s['key'] == 'prompter')
        try:
            loaded = self.load_prompter(p, copy)
        except NeedsPaste as e:
            step.update(state='needs_you', detail=str(e))
            rec.update(state='needs_you', finished_at=now(), manual=str(e),
                       message=f"Everything else for {p['name']} is ready. The script needs pasting into Camera Hub once. Recording hasn't started.")
            return self._save(rec)
        except Exception as e:  # noqa: BLE001
            step.update(state='failed', detail=f"The teleprompter wasn't set: {str(e)[:300]}")
            rec.update(state='partial', finished_at=now(), message=step['detail'],
                       manual='OBS is ready. Fix the teleprompter, then press Check teleprompter.')
            return self._save(rec)
        step.update(state='done', detail=(f"\"{loaded['name']}\" is selected on the Prompter in text mode. Its saved words match this "
                                          f"version exactly, {loaded['chapters']} parts, checked through Camera Hub."))
        rec.update(state='ready', finished_at=now(), manual='', camera_hub_text=loaded['guid'],
                   message=f"{p['name']} is ready to film. Recording hasn't started. Press Start when you're set.")
        return self._save(rec)

    def check_prompter(self, pid):
        """Run only the teleprompter step again, after Josh pasted and saved the script in Camera Hub."""
        rec = self.latest(pid)
        if not rec or rec.get('state') not in ('needs_you', 'partial', 'ready'):
            return dict(state='failed', message='Press Film first.', steps=[])
        if any(s['key'] == 'obs_folder' and s['state'] != 'done' for s in rec['steps']):
            return dict(rec, message='OBS isn\'t set up for this video yet, so press Film again.')
        if not self.lock.acquire(blocking=False):
            return dict(rec, state='busy', message='Already getting a video ready to film. Give it a moment.')
        try:
            p = self.store.get(pid)
            try:
                version, script = self.approved_script(p)
                copy = teleprompter(script)
            except ValueError as e:
                return dict(rec, state='failed', message=str(e))
            if version['sha256'] != rec.get('script_sha'):
                return dict(rec, state='failed', message='The approved script changed since Film ran, so press Film again.')
            why = self.busy()
            if why:
                return dict(rec, message=f'{why}. The teleprompter was left as it is.')
            return self._prompter_step(rec, p, copy)
        finally:
            self.lock.release()

    def wait_ready(self, started):
        deadline = _time.monotonic() + LAUNCH_WAIT_S
        while _time.monotonic() < deadline:
            r = self._ready_after(started)
            if r:
                return r, ''
            msg = self._message_after(started)
            if msg:
                return None, f'The YouTube filming setup stopped: {msg}'
            self._sleep(1)
        return None, ''

    def _ready_after(self, started):
        try:
            r = json.loads(self.ready_file.read_text(encoding='utf-8'))
            at = datetime.fromisoformat(str(r.get('at')).replace('Z', '+00:00'))
        except (OSError, ValueError, TypeError):
            return None
        if at >= started - timedelta(seconds=1) and r.get('mode') == 'youtube' and r.get('recording') is False and r.get('streaming') is False:
            return r
        return None

    def _message_after(self, started):
        try:
            st = self.message_file.stat()
            if datetime.fromtimestamp(st.st_mtime, UTC) >= started - timedelta(seconds=1):
                return self.message_file.read_text(encoding='utf-8', errors='replace').strip()[:300]
        except OSError:
            return ''
        return ''

    # Camera Hub -------------------------------------------------------------------------------------------------------
    @staticmethod
    def text_name(p, sha=''):
        return (f"{p['name']} · Josh OS {sha[:6]}" if sha else p['name'])[:80]

    def library(self):
        """Camera Hub's own scripts library, read only: [(guid, friendlyName, words, mtime)] for scripts in libraryList."""
        try:
            settings = json.loads(Path(self.app_settings()).read_text(encoding='utf-8-sig'))
        except (OSError, ValueError) as e:
            raise ValueError(f"Camera Hub's settings couldn't be read ({type(e).__name__}).") from None
        ids = settings.get('applogic.prompter.libraryList')
        if not isinstance(ids, list):
            raise ValueError("Camera Hub's scripts library wasn't found in its settings.")
        out = []
        for g in ids:
            f = Path(self.texts()) / f'{g}.json'
            try:
                d = json.loads(f.read_text(encoding='utf-8-sig'))
            except (OSError, ValueError):
                continue
            chapters = [c for c in d.get('chapters') or [] if isinstance(c, str)]
            out.append(dict(guid=str(g), name=str(d.get('friendlyName') or '').rstrip('\x00'), words=words(' '.join(chapters)), chapters=len(chapters),
                            text=' '.join(' '.join(chapters).split()), mtime=f.stat().st_mtime))
        return out

    def add_to_camera_hub(self, p, copy, want):
        """Add the script through Camera Hub's own Scripts +, then wait for Camera Hub to save it and check every word."""
        before = {s['guid'] for s in self.library()}
        name = self.text_name(p, hashlib.sha256(copy['text'].encode('utf-8')).hexdigest())
        out = self.adder(name, copy['text'])
        if not out.get('ok'):
            try:
                self.clip(copy['text'])
                where = 'The script is on your clipboard.'
            except Exception:  # noqa: BLE001
                where = 'Copy it from the Film panel.'
            raise NeedsPaste(f"Josh OS couldn't add it in Camera Hub ({out.get('error') or 'no answer'}). {where} In Camera Hub, "
                             f"open Prompter > Scripts, press +, name it \"{name}\", paste it, then press Check teleprompter.")
        deadline = _time.monotonic() + self.save_wait_s
        new = []
        while True:
            lib = self.library()
            new = [s for s in lib if s['guid'] not in before and s['name'] == name]
            hit = [s for s in new if s['words'] == want]
            if hit:
                return hit
            if _time.monotonic() >= deadline:
                break
            self._sleep(5)
        if not new:
            raise ValueError("Camera Hub didn't add the new script to its library.")
        raise ValueError(f"Camera Hub saved \"{name}\", but its words don't match this version of the script, so it wasn't used.")

    def load_prompter(self, p, copy):
        if not self.device:
            raise ValueError("The Prompter's device id isn't in the Stream Deck controls config, so the right Prompter can't be checked.")
        want, exact = words(copy['text']), ' '.join(copy['text'].split())
        match = sorted((s for s in self.library() if s['words'] == want), key=lambda s: (s['text'] == exact, s['mtime']), reverse=True)
        if not match:
            match = self.add_to_camera_hub(p, copy, want)
        s = match[0]
        out = self.prompter(['select', s['guid'], str(s['chapters']), self.device])
        if not out.get('ok'):
            raise ValueError(out.get('error') or 'Camera Hub did not confirm the script.')
        return dict(guid=s['guid'], name=s['name'] or 'the matching script', chapters=out.get('chapters'), mode=out.get('mode'),
                    exact=s['text'] == exact)

    # After a take is locked ----------------------------------------------------------------------------------------
    def restore_default(self):
        """Put the YouTube profile's recording folder back to E:\\Videos\\Input after a take is locked, so a later recording
        that skipped Film doesn't land in this video's folder. Only when OBS is idle on the YouTube profile."""
        try:
            return set_record_folder(self.obs, DEFAULT_RECORD_DIR)[1]
        except Exception:  # noqa: BLE001 - best effort; Film sets it again next time
            return None


def describe(rec):
    """What Sam says back after a preparation, in plain words."""
    if rec.get('state') in ('ambiguous', 'not_found', 'busy'):
        return rec.get('message', '')
    if rec.get('reused'):
        return rec['message']
    lines = [rec.get('message', '')]
    for s in rec.get('steps', []):
        mark = {'done': 'Done', 'failed': 'Stopped', 'skipped': 'Not done', 'waiting': 'Not done', 'needs_you': 'Needs you'}.get(s['state'], s['state'])
        lines.append(f"- {s['label']}. {mark}. {s.get('detail') or ''}".rstrip())
    if rec.get('manual'):
        lines.append(rec['manual'])
    return '\n'.join(x for x in lines if x)


FILM_PROPS = {'prepare_filming': {'type': ['object', 'null'], 'description': (
    "Only when Josh's message says he's ready to record or film a video now: the video's project_id if you know it, else "
    "video_name as he said it. Josh OS gets everything ready (OBS in YouTube mode saving to that video's folder, the approved "
    "script on the Camera Hub teleprompter) and tells you what happened. It never starts recording. Leave null otherwise."),
    'properties': {'project_id': {'type': 'string'}, 'video_name': {'type': 'string'}}}}
FILM_WORDS = re.compile(r'\b(record|recording|film|filming|shoot|shooting)\b', re.I)
