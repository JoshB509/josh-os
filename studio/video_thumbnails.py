"""Sam's YouTube thumbnail jobs, started from any chat with him.

Sam never runs anything himself. When Josh asks for thumbnails, Josh OS starts a job: Sam reads the
video, his training and the approved specs (read-only tools), and answers with up to three options,
each a format and its settings. Josh OS then checks every value against the limits below, resolves
the pose by name to a cutout in the Pose Catalogue, names the output file itself inside the video's
thumbnails folder, and runs thumbnail_build.py, the one fixed builder, with an argument list (no
shell). The finished images are posted back to Sam's chat (and to Discord when Josh asked there).
"""
import json
import re
import subprocess
import sys
import tempfile
import threading
import uuid
from datetime import date
from pathlib import Path

from PIL import Image

from .thumbnail_build import FORMATS, TEMPLATE, TOOLS, check_style

POSES = TEMPLATE / 'Pose Catalogue'
MAX_OPTIONS = 3
MAX_BYTES = 2 * 1024 * 1024  # YouTube's limit for a custom thumbnail
BUILD_TIMEOUT = 180
# name: (default, low, high). Coordinates are on the 1920 by 1080 canvas.
LIMITS = {
    'middle': {'face_cx': (960, 600, 1320), 'head_top': (22, -150, 400), 'body_bottom': (1100, 900, 1500), 'desk_top': (1008, 900, 1080),
               'shade_start': (400, 0, 1080), 'baseline': (975, 850, 1060), 'bar_left': (130, 0, 600), 'bar_right': (1790, 1300, 1790)},
    'close': {'face_cx': (483, 250, 750), 'face_cy': (350, 150, 550), 'face_w': (432, 250, 650), 'shade_start': (325, 0, 1080),
              'size': (142, 60, 200)},
}


def _num(spec, name, fmt):
    default, low, high = LIMITS[fmt][name]
    v = spec.get(name, default)
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise ValueError(f'{name} must be a number.')
    if not low <= v <= high:
        raise ValueError(f'{name} must be between {low} and {high}.')
    return v


def _words(text, limit=40):
    text = ' '.join(str(text or '').split())
    if not text or len(text) > limit or not re.fullmatch(r"[\w ',.!?&%$£€:+\-]+", text):
        raise ValueError('Thumbnail words must be 1 to 40 plain characters.')
    return text


def fit_size(texts, size, widest=1120):
    """The close format's words sit right of Josh, centred on x 1297. Shrink them until the longest line fits."""
    from PIL import ImageFont
    from .thumbnail_build import FONT
    while size > 60:
        f = ImageFont.truetype(str(FONT), round(size * 1.5))
        if max(f.getbbox(t, anchor='ls')[2] - f.getbbox(t, anchor='ls')[0] for t in texts) + 54 <= widest:
            break
        size -= 2
    return round(size, 1)


def pose_path(name):
    """A pose by its file name (like seated-13-palm-present-neutral), found in the Pose Catalogue."""
    name = str(name or '').strip()
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]{2,80}', name):
        raise ValueError('Give the pose by its catalogue name, like seated-13-palm-present-neutral.')
    for group in ('Seated', 'Standing', 'Close-up'):
        f = POSES / group / f'{name}.png'
        if f.is_file():
            return f
    raise ValueError(f'There is no pose called {name} in the Pose Catalogue.')


def check(option):
    """Turn one option from Sam into a spec the approved script accepts, or refuse it."""
    if not isinstance(option, dict):
        raise ValueError('Each option must be a set of settings.')
    fmt = option.get('format')
    if fmt not in FORMATS:
        raise ValueError(f"Format must be one of {', '.join(FORMATS)}.")
    pose = str(pose_path(option.get('pose')))
    if option.get('style') is not None:
        return styled_spec(fmt, option, pose)
    if fmt == 'middle':
        spec = dict(scene='seated', shade_start=_num(option, 'shade_start', fmt),
                    pose=dict(path=pose, face_cx=_num(option, 'face_cx', fmt), head_top=_num(option, 'head_top', fmt),
                              body_bottom=_num(option, 'body_bottom', fmt), desk_top=_num(option, 'desk_top', fmt)),
                    line=dict(text=_words(option.get('words')), cx=960, baseline=_num(option, 'baseline', fmt),
                              bar_left=_num(option, 'bar_left', fmt), bar_right=_num(option, 'bar_right', fmt)))
        words = spec['line']['text']
    else:
        lines = option.get('lines') or []
        if not isinstance(lines, list) or not 1 <= len(lines) <= 2:
            raise ValueError('The close format takes one or two lines of words.')
        size = _num(option, 'size', fmt)
        texts = [_words(t, 24) for t in lines]
        size = fit_size(texts, size)
        spec = dict(scene='close', shade_start=_num(option, 'shade_start', fmt),
                    pose=dict(path=pose, face_cx=_num(option, 'face_cx', fmt), face_cy=_num(option, 'face_cy', fmt),
                              face_w=_num(option, 'face_w', fmt)),
                    lines=[dict(text=t, cx=1297, baseline=round(470 + i * size * 1.5 * 0.92), size=size) for i, t in enumerate(texts)])
        words = ' '.join(texts)
    return fmt, spec, words


def styled_spec(fmt, option, pose):
    """An option with a text style: chosen font, colours, size and fitting. The builder checks the style again."""
    style = check_style(option['style'])
    lines = option.get('lines') if option.get('lines') is not None else [option.get('words')]
    if not isinstance(lines, list) or not 1 <= len(lines) <= 2:
        raise ValueError('Give one or two lines of words.')
    texts = [_words(t) for t in lines]
    if style['case'] == 'upper':
        texts = [t.upper() for t in texts]
    if fmt == 'middle':
        spec = dict(scene='seated', shade_start=_num(option, 'shade_start', fmt), lines=texts, style=style,
                    baseline=_num(option, 'baseline', fmt), bar_left=_num(option, 'bar_left', fmt), bar_right=_num(option, 'bar_right', fmt),
                    pose=dict(path=pose, face_cx=_num(option, 'face_cx', fmt), head_top=_num(option, 'head_top', fmt),
                              body_bottom=_num(option, 'body_bottom', fmt), desk_top=_num(option, 'desk_top', fmt)))
    else:
        spec = dict(scene='close', shade_start=_num(option, 'shade_start', fmt), lines=texts, style=style,
                    pose=dict(path=pose, face_cx=_num(option, 'face_cx', fmt), face_cy=_num(option, 'face_cy', fmt),
                              face_w=_num(option, 'face_w', fmt)))
    return fmt, spec, ' '.join(texts)


class VideoThumbnails:
    def __init__(self, store, folders, agents):
        self.store, self.folders, self.agents = store, folders, agents
        self.jobs, self.lock = {}, threading.Lock()
        self.notify = None  # set by the server when the Discord bridge is running

    def folder(self, pid):
        p = self.folders.path(pid)
        if not p:
            raise ValueError('This video has no folder yet.')
        return Path(p)

    def running(self, pid):
        t = self.jobs.get(pid)
        return bool(t and t.is_alive())

    def start(self, pid, note='', via=''):
        p = self.store.get(pid)
        self.folder(pid)
        with self.lock:
            if self.running(pid):
                raise ValueError(f"Sam is already making thumbnails for {p['name']}.")
            t = threading.Thread(target=self._work, args=(pid, str(note or '')[:2000], via), daemon=True)
            self.jobs[pid] = t
            t.start()
        return dict(id=pid, title=p['name'], action='started', kind='thumbnail')

    def _work(self, pid, note, via):
        p = self.store.get(pid)
        try:
            reply, answer = self.agents.thumbnail_job(p, note, self.folder(pid))
            made, problems = self.build_all(pid, p, (answer or {}).get('options') or [])
            text = (reply or '').strip()
            if problems:
                text += '\n\n' + '\n'.join(problems)
            if not made:
                text += "\n\nNo thumbnail came out of this round, so nothing was saved."
        except Exception as e:  # noqa: BLE001 - the failure goes back to Josh in the chat
            made, text = [], f"I couldn't make the thumbnails for {p['name']}: {str(e)[:300]}"
        self.post(pid, p, text, made, via)

    def build_all(self, pid, p, options):
        made, problems = [], []
        thumbs = self.folder(pid) / 'thumbnails'
        thumbs.mkdir(exist_ok=True)
        slug = re.sub(r'[^a-z0-9]+', '-', p['name'].lower()).strip('-')[:40].strip('-') or 'video'
        for i, option in enumerate(options[:MAX_OPTIONS], 1):
            try:
                fmt, spec, words = check(option)
                word_slug = re.sub(r'[^a-z0-9]+', '-', words.lower()).strip('-')[:40].strip('-')
                n = 1
                while True:
                    out = thumbs / f"{date.today().isoformat()}_{slug}_thumb-{fmt}-{word_slug}_v{n:02d}.png"
                    if not out.exists() and not out.with_suffix('.jpg').exists():
                        break
                    n += 1
                result = self.build(fmt, spec, out)
                final = self.under_limit(out)
                asked, size = result.get('asked'), result.get('size')
                if asked and size and size < asked:
                    problems.append(f"Option {i}'s words were shrunk from {asked:g} to {size:g} px so they fit.")
                made.append(dict(path=str(final), label=str(option.get('label') or f'Option {i}')[:60], format=fmt, words=words))
            except (ValueError, OSError, subprocess.SubprocessError) as e:
                problems.append(f"Option {i} wasn't built: {str(e)[:200]}")
        return made, problems

    def build(self, fmt, spec, out):
        with tempfile.TemporaryDirectory() as tmp:
            job = Path(tmp) / 'job.json'
            job.write_text(json.dumps(dict(format=fmt, spec=spec, out=str(out))), encoding='utf-8')
            run = subprocess.run([sys.executable, str(Path(__file__).with_name('thumbnail_build.py')), str(job)],
                                 capture_output=True, text=True, encoding='utf-8', timeout=BUILD_TIMEOUT, cwd=tmp,
                                 creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if run.returncode != 0 or not out.is_file():
            raise ValueError(((run.stderr or run.stdout).strip().splitlines() or ['the builder stopped'])[-1][:300])
        try:
            return json.loads(run.stdout.strip().splitlines()[-1]).get('result') or {}
        except (ValueError, IndexError, AttributeError):
            return {}

    @staticmethod
    def under_limit(png):
        """YouTube refuses thumbnails over 2 MB, so a large PNG gets a high quality JPEG beside it instead."""
        if png.stat().st_size <= MAX_BYTES:
            return png
        jpg = png.with_suffix('.jpg')
        with Image.open(png) as im:
            for q in (92, 88, 84, 80):
                im.convert('RGB').save(jpg, 'JPEG', quality=q, optimize=True)
                if jpg.stat().st_size <= MAX_BYTES:
                    break
        png.unlink()
        return jpg

    def post(self, pid, p, text, made, via):
        images = []
        for m in made:
            try:
                images.append(self.store.import_image(Path(m['path']).name, Path(m['path']).read_bytes())['asset'])
            except (OSError, ValueError):
                pass
        if made:
            text += '\n\nSaved in the video\'s thumbnails folder:\n' + '\n'.join(f"- {m['label']}: {Path(m['path']).name}" for m in made)
        saved = [dict(id=pid, title=p['name'], action='made', kind='thumbnail', count=len(made))] if made else []
        self.agents.post('sam', text.strip(), images=images, saved=saved, via=via, files=[m['path'] for m in made])
        if via == 'discord' and self.notify:
            try:
                self.notify('sam', text.strip(), [m['path'] for m in made])
            except Exception:  # noqa: BLE001 - the message is in Josh OS either way
                pass
