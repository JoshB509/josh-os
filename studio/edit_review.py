"""Tariq's edit review: the animation deck shown as a slide show of the finished video.

Tariq makes two files for each video: the slides (an HTML deck where each animation is a
scene with numbered presses) and the shot list (a markdown table of the recorded script,
with which animation goes where and which words each press lands on). This module reads
both and turns them into review parts, in script order:

- a talking head part: the words Josh says to camera, shown on a stand-in still of him;
- an animation part: the finished scene, a picture per press as it builds, and the script
  it covers with each press's words marked.

The pictures are made by render_edit_review.py with the stand-in still placed where the
camera will go (the right-hand box for picture in picture, the right half for half and
half), so the deck can be reviewed before anything is rendered for Premiere.
"""
import hashlib
import json
import re
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path

ASSETS = Path(__file__).with_name('edit_assets')
STANDIN = ASSETS / 'standin.webp'
EDIT_SUBFOLDERS = ('backup', 'masters', 'qa', 'reproducibility', 'review', 'sources')
TYPES = {'picture in picture': 'pip', 'half and half': 'half', 'full screen': 'full'}


def _num(name, kind):
    m = re.search(rf'_{kind}_v(\d+)', name)
    return int(m.group(1)) if m else -1


def _clean(text):
    return re.sub(r'\s+', ' ', re.sub(r'<br\s*/?>', ' ', text)).strip()


def parse_scenes(html):
    """Each scene's press range and shot type, in order. Grey number cards are left out."""
    scenes = []
    for m in re.finditer(r'<(?:div|section)[^>]*class="scene([^"]*)"[^>]*>', html):
        tag, cls = m.group(0), m.group(1)
        f = re.search(r'data-from="(\d+)"', tag)
        t = re.search(r'data-to="(\d+)"', tag)
        if not f or not t or 'mark' in cls.split():
            continue
        kind = next((k for k in ('pip', 'half', 'full') if k in cls.split()), 'full')
        scenes.append(dict(start=int(f.group(1)), end=int(t.group(1)), type=kind))
    for i, s in enumerate(scenes, 1):
        s['n'] = i
    return scenes


def _find(text, phrase, start=0):
    """Where a quoted phrase starts in the script, ignoring case and punctuation differences."""
    norm = lambda s: re.sub(r'[^a-z0-9 ]', '', s.lower())
    t, p = text.lower(), phrase.lower().strip(' .,')
    i = t.find(p, start)
    if i >= 0:
        return i
    words = norm(phrase).split()
    if not words:
        return -1
    pat = r'\W+'.join(re.escape(w) for w in words[:4])
    m = re.compile(pat, re.I).search(text, start)
    return m.start() if m else -1


def parse_shot_list(md):
    """The shot list table as review parts in script order: talking head stretches and animation appearances."""
    parts, info = [], {}
    mode = ('talk', None)
    for line in md.splitlines():
        if not line.startswith('|') or line.startswith('|---') or line.startswith('| Script'):
            continue
        cells = [c.strip() for c in line.strip().strip('|').split('|')]
        if len(cells) < 2:
            continue
        script, shot = cells[0], cells[1].replace('**', '')
        head = re.match(r'\*\*(\d+)\s*·\s*(.+?)\*\*$', script)
        if head and not shot.strip():
            parts.append(dict(kind='section', title=head.group(2).strip(), number=head.group(1)))
            continue
        tc = re.match(r'(\d+:\d{2})\s*·\s*(.*)', script)
        time, text = (tc.group(1), _clean(tc.group(2))) if tc else ('', _clean(script))

        # Animation details and presses, with each press tied to the animation named before it.
        current = mode[1] if mode[0] == 'anim' else None
        for seg in re.split(r'(?=Insert Animation \d+)|(?=(?:Stay on|back to|Back to) Animation \d+)', shot):
            m = re.match(r'(Insert|Stay on|back to|Back to) Animation (\d+)\s*(\(([^)]*)\))?', seg)
            if m:
                current = int(m.group(2))
                a = info.setdefault(current, dict(n=current, presses=[], desc='', type='full'))
                if m.group(1) == 'Insert' and m.group(4):
                    a['type'] = TYPES.get(m.group(4).split(',')[0].strip().lower(), 'full')
                    a['desc'] = _clean(seg[m.end():].split('<br><br>')[0].replace('<br>', ' ').split('Press 1 on')[0])
            for k, words in re.findall(r'Press (\d+) on "([^"]+)"', seg):
                if current:
                    info.setdefault(current, dict(n=current, presses=[], desc='', type='full'))['presses'].append(dict(press=int(k), words=words))

        # Where the picture changes inside this row, in the order the shot cell describes.
        events = []
        if re.match(r'\s*Talking head', shot):
            events.append((0, ('talk', None)))
        elif re.match(r'\s*Stay on Animation (\d+)', shot):
            events.append((0, ('anim', int(re.match(r'\s*Stay on Animation (\d+)', shot).group(1)))))
        pending = []
        for m in re.finditer(r'cut to the animation from "([^"]+)"|Slide your camera[^"]*from "([^"]+)"|Insert Animation (\d+)|[Bb]ack to Animation (\d+)|[Tt]alking head (?:for|from) "([^"]+)"|full-frame talking head from "([^"]+)"', shot):
            if m.group(1) or m.group(2):
                pending.append(_find(text, m.group(1) or m.group(2)))
            elif m.group(3) or m.group(4):
                n = int(m.group(3) or m.group(4))
                presses = re.search(rf'(?:Insert|[Bb]ack to) Animation {n}.*?Press \d+ on "([^"]+)"', shot[m.start():])
                if pending:
                    at = pending.pop(0)
                elif not events and m.start() < 40:
                    at = 0
                else:
                    at = _find(text, presses.group(1)) if presses else -1
                events.append((max(at, 0), ('anim', n)))
            else:
                events.append((max(_find(text, m.group(5) or m.group(6)), 0), ('talk', None)))
        if not events:
            events.append((0, ('talk', None)))
        events.sort(key=lambda e: e[0])
        if events[0][0] > 0:
            events.insert(0, (0, mode))
        for i, (at, m) in enumerate(events):
            end = events[i + 1][0] if i + 1 < len(events) else len(text)
            piece = text[at:end].strip()
            if not piece:
                mode = m
                continue
            last = parts[-1] if parts else None
            if last and last['kind'] == ('talk' if m[0] == 'talk' else 'animation') and (m[0] == 'talk' or last.get('n') == m[1]):
                last['script'] += ' ' + piece
            elif m[0] == 'talk':
                parts.append(dict(kind='talk', time=time, script=piece))
            else:
                parts.append(dict(kind='animation', n=m[1], time=time, script=piece))
            mode = m
    for p in parts:
        if p['kind'] == 'animation':
            a = info.get(p['n'], dict(presses=[], desc='', type='full'))
            p.update(type=a['type'], desc=a['desc'],
                     presses=[dict(x, found=_find(p['script'], x['words']) >= 0) for x in sorted(a['presses'], key=lambda x: x['press'])])
    return parts


class EditReview:
    def __init__(self, store, folders):
        self.store, self.folders = store, folders

    def project_folder(self, pid):
        p = self.folders.path(pid) if self.folders else None
        if not p:
            raise KeyError('This video has no folder yet.')
        return Path(p)

    def deck_files(self, pid):
        """The newest slides and shot list in the video's folder."""
        root = self.project_folder(pid)
        slides = sorted(root.glob('*_slides_v*.html'), key=lambda f: _num(f.name, 'slides'))
        shots = sorted(root.glob('*_shot-list_v*.md'), key=lambda f: _num(f.name, 'shot-list'))
        return (slides[-1] if slides else None), (shots[-1] if shots else None)

    def edit_folder(self, pid):
        video = self.project_folder(pid) / 'video'
        found = sorted(video.glob('Animation-Edit-v*'), key=lambda f: f.name) if video.is_dir() else []
        return found[-1] if found else None

    def out_folder(self, pid):
        edit = self.edit_folder(pid)
        return (edit / 'review' / 'deck') if edit else (self.project_folder(pid) / 'video' / 'review-deck')

    def setup(self, pid):
        """Make the edit folder the way Josh lays them out, once a video is recorded."""
        root = self.project_folder(pid)
        for sub in ('Recorded/Input', 'script', 'thumbnails/ab-test', 'video'):
            (root / sub).mkdir(parents=True, exist_ok=True)
        edit = self.edit_folder(pid)
        if not edit:
            edit = root / 'video' / f"Animation-Edit-v01-{date.today().strftime('%Y%m%d')}"
        for sub in EDIT_SUBFOLDERS:
            (edit / sub).mkdir(parents=True, exist_ok=True)
        return dict(project=str(root), edit=str(edit), made=list(EDIT_SUBFOLDERS))

    def review(self, pid):
        slides, shots = self.deck_files(pid)
        out = dict(slides=slides.name if slides else '', shot_list=shots.name if shots else '', parts=[], rendered=None,
                   edit_folder=str(self.edit_folder(pid) or ''))
        if not slides or not shots:
            return out
        html = slides.read_text(encoding='utf-8')
        scenes = {s['n']: s for s in parse_scenes(html)}
        parts = parse_shot_list(shots.read_text(encoding='utf-8'))
        manifest = self.out_folder(pid) / 'manifest.json'
        made = json.loads(manifest.read_text(encoding='utf-8')) if manifest.is_file() else {}
        current = hashlib.sha1(html.encode('utf-8')).hexdigest()[:12]
        for p in parts:
            if p['kind'] != 'animation':
                continue
            s = scenes.get(p['n'])
            if s:
                p['beats'] = s['end'] - s['start'] + 1
                p['type'] = s['type']
            p['images'] = (made.get('images') or {}).get(str(p['n']), [])
        out.update(parts=parts, rendered=made.get('at'), stale=bool(made) and made.get('slides_hash') != current,
                   slides_hash=current)
        return out

    def image(self, pid, name):
        if not re.fullmatch(r'[\w.-]+\.jpg', name):
            raise KeyError('Picture not found.')
        f = self.out_folder(pid) / name
        if not f.is_file():
            raise KeyError('Picture not found.')
        return f

    def render(self, pid, only=None):
        """Take the pictures for every animation, or only the ones given."""
        slides, _ = self.deck_files(pid)
        if not slides:
            raise ValueError('There are no slides in this video\'s folder yet.')
        html = slides.read_text(encoding='utf-8')
        scenes = parse_scenes(html)
        if only:
            scenes = [s for s in scenes if s['n'] in set(only)]
        out = self.out_folder(pid)
        out.mkdir(parents=True, exist_ok=True)
        job = Path(tempfile.mkdtemp()) / 'job.json'
        job.write_text(json.dumps(dict(slides=str(slides), out=str(out), standin=str(STANDIN), scenes=scenes)), encoding='utf-8')
        run = subprocess.run([sys.executable, str(Path(__file__).with_name('render_edit_review.py')), str(job)],
                             capture_output=True, text=True, encoding='utf-8', timeout=900,
                             creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if run.returncode != 0:
            raise ValueError('The review pictures could not be made: ' + ((run.stderr or run.stdout).strip().splitlines() or ['no details'])[-1][:300])
        made = json.loads(run.stdout.strip().splitlines()[-1])
        manifest = out / 'manifest.json'
        old = json.loads(manifest.read_text(encoding='utf-8')) if manifest.is_file() else {}
        images = dict(old.get('images') or {}) if only else {}
        images.update(made['images'])
        from .store import now
        manifest.write_text(json.dumps(dict(at=now(), slides=slides.name, images=images,
                                            slides_hash=hashlib.sha1(html.encode('utf-8')).hexdigest()[:12]), indent=1), encoding='utf-8')
        return self.review(pid)

    def decks(self):
        """Every video with an animation deck in its folder, newest first, for the Editing page."""
        out = []
        for p in self.store.projects():
            if p.get('archived'):
                continue
            try:
                slides, shots = self.deck_files(p['id'])
            except (KeyError, ValueError, OSError):
                continue
            if not slides:
                continue
            manifest = self.out_folder(p['id']) / 'manifest.json'
            made = json.loads(manifest.read_text(encoding='utf-8')) if manifest.is_file() else {}
            images = made.get('images') or {}
            first = next((v[-1] for k, v in sorted(images.items(), key=lambda kv: int(kv[0])) if v), '')
            html = slides.read_text(encoding='utf-8')
            out.append(dict(id=p['id'], name=p['name'], stage=p['stage'], slides=slides.name, shot_list=shots.name if shots else '',
                            animations=len(parse_scenes(html)), rendered=made.get('at'), cover=first,
                            stale=bool(made) and made.get('slides_hash') != hashlib.sha1(html.encode('utf-8')).hexdigest()[:12],
                            modified=slides.stat().st_mtime))
        return sorted(out, key=lambda d: d['modified'], reverse=True)
