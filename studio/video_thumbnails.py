"""Sam's YouTube thumbnail jobs, started from any chat with him.

Sam proposes prompts/settings with read-only tools. New chat requests use the GPT Image API,
with explicit references and user composition instructions; missing API access fails visibly.
An explicitly selected local provider retains the existing compositor and defaults. The host
controls asset resolution, output names, validation and result receipts. Provider failures never
fall back silently. Finished images are posted back to chat (and Discord when requested there).
"""
import json
import hashlib
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
from .thumbnail_layout import geometry, setting, text_box
from .gpt_images import GPTImages, image_input, MAX_INPUT_BYTES, MAX_REFERENCES

POSES = TEMPLATE / 'Pose Catalogue'
MAX_OPTIONS = 3
MAX_BYTES = 2 * 1024 * 1024  # YouTube's limit for a custom thumbnail
BUILD_TIMEOUT = 180
def _num(spec, name, fmt):
    return setting(spec, name, fmt)


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
    if option.get('style') is not None or any(k in option for k in ('text_cx', 'line_gap', 'text_behind', 'shade', 'scene')) or (
            fmt == 'close' and any(k in option for k in ('baseline', 'bar_left', 'bar_right'))):
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
    geometry(fmt, spec)
    return fmt, spec, words


def styled_spec(fmt, option, pose):
    """An option with a text style: chosen font, colours, size and fitting. The builder checks the style again."""
    style = check_style(option.get('style', dict(fit='fill', bar=True)))
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
    spec['scene'] = option.get('scene', spec['scene'])
    spec['text_box'] = text_box(option, fmt)
    spec['text_behind'] = option.get('text_behind', False)
    spec['shade'] = option.get('shade', True)
    geometry(fmt, spec)
    return fmt, spec, ' '.join(texts)


class VideoThumbnails:
    def __init__(self, store, folders, agents, image_provider=None):
        self.store, self.folders, self.agents = store, folders, agents
        self.jobs, self.lock = {}, threading.Lock()
        self.notify = None  # set by the server when the Discord bridge is running
        self.image_provider = image_provider or GPTImages()

    def folder(self, pid):
        p = self.folders.path(pid)
        if not p:
            raise ValueError('This video has no folder yet.')
        return Path(p)

    def running(self, pid):
        t = self.jobs.get(pid)
        return bool(t and t.is_alive())

    def start(self, pid, note='', via='', provider='gpt-image', reference_assets=(), user_note=''):
        p = self.store.get(pid)
        self.folder(pid)
        if provider not in ('gpt-image', 'local'):
            raise ValueError('Choose gpt-image or local for the thumbnail provider.')
        if provider == 'gpt-image':
            self.image_provider.require_ready()
        refs = self.reference_ids(reference_assets)
        for aid in refs:
            self.store.asset(aid)
        with self.lock:
            if self.running(pid):
                raise ValueError(f"Sam is already making thumbnails for {p['name']}.")
            t = threading.Thread(target=self._work, args=(pid, str(note or '')[:8000], via, provider, refs,
                                                          str(user_note or note or '')[:8000]), daemon=True)
            self.jobs[pid] = t
            t.start()
        return dict(id=pid, title=p['name'], action='started', kind='thumbnail', provider=provider)

    def _work(self, pid, note, via, provider='local', reference_assets=(), user_note=''):
        p = self.store.get(pid)
        try:
            if provider == 'gpt-image':
                reply, answer = self.agents.thumbnail_job(p, note, self.folder(pid), provider=provider,
                                                         reference_assets=reference_assets, user_note=user_note)
            else:
                reply, answer = self.agents.thumbnail_job(p, note, self.folder(pid))
            made, problems = self.build_all(pid, p, (answer or {}).get('options') or [], provider=provider,
                                            reference_assets=reference_assets, user_note=user_note or note)
            # A planner's prose is not evidence of generation. Only host receipts claim GPT success.
            text = (f"Created {len(made)} thumbnail(s) with GPT Image. Review the text and likeness before using them."
                    if made else 'No GPT image was created or saved. No local fallback was run.') if provider == 'gpt-image' else (reply or '').strip()
            if problems:
                text += '\n\n' + '\n'.join(problems)
            if not made:
                text += "\n\nNo thumbnail came out of this round, so nothing was saved."
        except Exception as e:  # noqa: BLE001 - the failure goes back to Josh in the chat
            made, text = [], f"I couldn't make the thumbnails for {p['name']}: {str(e)[:300]}"
        self.post(pid, p, text, made, via)

    def build_all(self, pid, p, options, provider='local', reference_assets=(), user_note=''):
        made, problems = [], []
        thumbs = self.folder(pid) / 'thumbnails'
        thumbs.mkdir(exist_ok=True)
        slug = re.sub(r'[^a-z0-9]+', '-', p['name'].lower()).strip('-')[:40].strip('-') or 'video'
        for i, option in enumerate(options[:MAX_OPTIONS], 1):
            try:
                if provider == 'gpt-image':
                    if not isinstance(option, dict):
                        raise ValueError('Each option must be a set of settings.')
                    fmt, spec, words = 'gpt-image', option, str(option.get('words') or 'thumbnail')[:100]
                elif provider == 'local':
                    fmt, spec, words = check(option)
                else:
                    raise ValueError('Unsupported thumbnail provider; no fallback was run.')
                word_slug = re.sub(r'[^a-z0-9]+', '-', words.lower()).strip('-')[:40].strip('-')
                n = 1
                while True:
                    out = thumbs / f"{date.today().isoformat()}_{slug}_thumb-{fmt}-{word_slug}_v{n:02d}.png"
                    if not out.exists() and not out.with_suffix('.jpg').exists():
                        break
                    n += 1
                if provider == 'gpt-image':
                    final, result = self.build_gpt(pid, spec, out, reference_assets, user_note)
                else:
                    result = self.build(fmt, spec, out)
                    final = self.under_limit(out)
                asked, size = result.get('asked'), result.get('size')
                if asked and size and size < asked:
                    problems.append(f"Option {i}'s words were shrunk from {asked:g} to {size:g} px so they fit.")
                made.append(dict(path=str(final), label=str(option.get('label') or f'Option {i}')[:60], format=fmt,
                                 words=words, provider=provider, model=result.get('model', '')))
            except (ValueError, OSError, subprocess.SubprocessError) as e:
                problems.append(f"Option {i} wasn't built: {str(e)[:200]}")
        return made, problems

    @staticmethod
    def reference_ids(assets):
        if not isinstance(assets, (list, tuple)) or len(assets) > MAX_REFERENCES or not all(
                isinstance(aid, str) and re.fullmatch(r'[0-9a-f]{64}', aid) for aid in assets):
            raise ValueError('Use at most six existing image asset IDs as references, never paths or URLs.')
        return list(dict.fromkeys(assets))

    def build_gpt(self, pid, option, out, reference_assets, user_note):
        from .thumbnail_build import BACKGROUNDS
        self.image_provider.require_ready()
        prompt = option.get('prompt')
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 16000:
            raise ValueError('Give GPT Image a composition prompt of 1 to 16000 characters.')
        refs, identities = [], []
        def add(path, role, expected_hash=None):
            with Path(path).open('rb') as file:
                raw = file.read(MAX_INPUT_BYTES + 1)
            digest = hashlib.sha256(raw).hexdigest()
            if expected_hash and digest != expected_hash:
                raise ValueError('A selected reference image changed. Reattach it before generating.')
            refs.append(image_input(raw, role))
            identities.append(dict(role=role, sha256=digest))
        for index, aid in enumerate(self.reference_ids(reference_assets), 1):
            path, _ = self.store.asset(aid)
            add(path, f'User-selected reference {index}; follow the user instructions for its role', aid)
        if option.get('pose'):
            path = pose_path(option['pose']).resolve()
            if not path.is_relative_to(POSES.resolve()):
                raise ValueError('The pose must be inside the Pose Catalogue.')
            add(path, 'Josh subject/identity reference; preserve his likeness')
        if option.get('scene'):
            if option['scene'] not in BACKGROUNDS:
                raise ValueError('Choose an approved background scene.')
            add(BACKGROUNDS[option['scene']], 'Josh background reference')
        roles = '\n'.join(f'Image {i}: {ref["role"]}' for i, ref in enumerate(refs, 1))
        full_prompt = ('Create a 16:9 YouTube thumbnail. The user composition instructions take precedence over every '
                       'house-style default and the proposed design. Do not impose template geometry or baseline limits. '
                       'Preserve supplied subject identity and background unless the user requests a change. '
                       'Only defaults when unspecified: clear readable text, strong contrast, clean composition.\n'
                       + roles + '\nProposed design (subordinate to user instructions):\n' + prompt
                       + '\nProposed headline: ' + str(option.get('words') or '')[:200]
                       + '\nUser composition instructions (highest priority):\n' + str(user_note))
        raw, metadata = self.image_provider.render(full_prompt, refs)
        # Stage size conversion locally, then exclusively create a new output/receipt.
        with tempfile.TemporaryDirectory() as tmp:
            staged = Path(tmp) / 'generated.png'
            staged.write_bytes(raw)
            staged = self.under_limit(staged)
            data = staged.read_bytes()
            if len(data) > MAX_BYTES:
                raise ValueError('The GPT image could not fit the thumbnail file-size limit; nothing was saved.')
            final = out.with_suffix(staged.suffix)
        expected = (self.folder(pid) / 'thumbnails').resolve()
        if out.parent.is_symlink() or final.parent.resolve() != expected:
            raise ValueError('Thumbnail destination changed; nothing was saved.')
        receipt = final.with_suffix('.json')
        if final.exists() or receipt.exists():
            raise ValueError('Thumbnail output already exists; it was not overwritten.')
        metadata = dict(metadata, project_id=pid, sha256=hashlib.sha256(data).hexdigest(), references=identities,
                        prompt_sha256=hashlib.sha256(full_prompt.encode('utf-8')).hexdigest())
        created, receipt_created = False, False
        try:
            file = final.open('xb')
            created = True
            with file:
                file.write(data)
            file = receipt.open('x', encoding='utf-8')
            receipt_created = True
            with file:
                json.dump(metadata, file, indent=2)
        except (OSError, ValueError, TypeError):
            # Only files this attempt created; never delete pre-existing outputs.
            if created:
                final.unlink(missing_ok=True)
            if receipt_created:
                receipt.unlink(missing_ok=True)
            raise
        return final, metadata

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
        self.agents.post('sam', text.strip(), images=images, saved=saved, via=via, files=[m['path'] for m in made],
                         notice=not (via == 'discord' and self.notify))
        if via == 'discord' and self.notify:
            try:
                self.notify('sam', text.strip(), [m['path'] for m in made])
            except Exception:  # noqa: BLE001 - the message is in Josh OS either way
                pass
