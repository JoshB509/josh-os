"""Email-owned flexible static visuals. Local PNGs only; never carousel records or hosted URLs."""
import hashlib
import io
import json
import re
import subprocess
import sys
import uuid
from pathlib import Path

from .store import Conflict

from .visual_spec import MAX_VISUALS, normalize_visuals, safe_svg

LOCAL_ONLY = 'This email has local visuals. Kit draft preparation is blocked until an approved image-hosting pathway is available. Download and review the PNGs locally; nothing has been uploaded.'
TOKEN = re.compile(r'[0-9a-f]{32}')
SHA = re.compile(r'[0-9a-f]{64}')


def sha(data):
    return hashlib.sha256(data).hexdigest()


def metadata(item, body):
    if not isinstance(item.get('alt'), str) or not item['alt'].strip() or len(item['alt']) > 1000:
        raise ValueError('Each visual needs descriptive alt text, up to 1000 characters.')
    if type(item.get('after_line')) is not int or not 0 <= item['after_line'] <= len(body.split('\n')):
        raise ValueError('Visual placement must be a line number in this body (0 means before the first line).')


VISUAL_PROPS = {'type': 'object', 'additionalProperties': False,
    'required': ['email_id', 'revision', 'visuals'],
    'description': 'Only when Josh asks: replace the saved email visual set with 1-16 local PNG visuals with flexible dimensions and optional starting templates. Use the saved email id and revision. Save body edits first in a separate turn. Never uploads or changes carousel records. Kit proposals are blocked while local visuals are attached.',
    'properties': {'email_id': {'type': 'string'}, 'revision': {'type': 'integer', 'minimum': 1},
        'visuals': {'type': 'array', 'minItems': 1, 'maxItems': MAX_VISUALS, 'items': {
            'type': 'object', 'additionalProperties': False, 'required': ['alt', 'after_line'],
            'properties': {'lines': {'type': 'array', 'minItems': 0, 'maxItems': 8, 'items': {'type': 'string', 'maxLength': 300}},
                'svg': {'type': 'string', 'maxLength': 200000, 'description': 'Static SVG shapes/text with a viewBox. No style, script, image, href, events, HTML or external references. Standalone layout gives full control of the canvas.'},
                'width': {'type': 'integer', 'minimum': 320, 'maximum': 4096},
                'height': {'type': 'integer', 'minimum': 320, 'maximum': 4096},
                'aspect_ratio': {'type': 'string', 'description': 'Optional width:height ratio, e.g. 16:9. Omitted height is derived from width. Max 8M pixels per visual, 40M per set.'},
                'template': {'type': 'string', 'enum': ['paper', 'slide-card', 'plain'], 'description': 'Optional starting point only; dimensions, background and layout can override it.'},
                'layout': {'type': 'string', 'enum': ['text', 'card', 'standalone'], 'description': 'Text with optional drawing; card with an SVG slide; or standalone SVG without text lines.'},
                'footer': {'type': 'boolean', 'description': 'Defaults false for email. Explicit true adds the author tag; never adds carousel dots.'},
                'badge': {'type': 'string', 'enum': ['', 'BEFORE', 'AFTER']},
                'note': {'type': 'string', 'maxLength': 300, 'description': 'Optional one-line note below the slide. Overlong notes fail rather than crop or become unreadable.'},
                'background': {'type': 'string', 'pattern': '^#[0-9a-fA-F]{6}$'},
                'alt': {'type': 'string', 'maxLength': 1000},
                'after_line': {'type': 'integer', 'minimum': 0, 'description': 'Insert after this 1-based body line, counting blank lines; 0 inserts before the body.'}}}}}}

from .email_visual_imports import IMPORT_SCHEMA
_generated_visual_schema = VISUAL_PROPS['properties']['visuals']['items']
VISUAL_PROPS['properties']['visuals']['items'] = {'anyOf': [_generated_visual_schema, IMPORT_SCHEMA]}


class EmailVisuals:
    def __init__(self, emails):
        self.emails = emails
        self.root = Path(emails.store.root) / 'email-visuals'

    def folder(self, eid, version):
        if not isinstance(eid, str) or str(uuid.UUID(eid)) != eid or not isinstance(version, str) or not TOKEN.fullmatch(version):
            raise ValueError('Invalid email visual identifier.')
        root = self.root.resolve()
        folder = (root / eid / version).resolve()
        if not folder.is_relative_to(root):
            raise ValueError('Invalid email visual location.')
        return folder

    def manifest(self, eid, version):
        file = self.folder(eid, version) / 'manifest.json'
        if file.resolve().parent != file.parent:
            raise ValueError('Invalid visual manifest location.')
        try:
            manifest = json.loads(file.read_text(encoding='utf-8'))
            if manifest.get('email_id') != eid:
                raise ValueError('Visual belongs to a different email.')
            return manifest
        except (OSError, ValueError):
            raise ValueError('This visual version is unavailable. Generate it again.') from None

    def validate(self, p):
        images = p.get('images', [])
        if not isinstance(images, list) or len(images) > MAX_VISUALS:
            raise ValueError('Use at most sixteen email visuals.')
        seen = set()
        for item in images:
            if not isinstance(item, dict) or set(item) != {'version', 'index', 'source_hash', 'png_hash', 'alt', 'after_line'}:
                raise ValueError('Images must reference generated email visuals, with alt text and placement.')
            metadata(item, p['body'])
            if type(item['index']) is not int or not 1 <= item['index'] <= MAX_VISUALS:
                raise ValueError('Invalid visual index.')
            if any(not isinstance(item[k], str) or not SHA.fullmatch(item[k]) for k in ('source_hash', 'png_hash')):
                raise ValueError('Invalid visual hash.')
            key = (item['version'], item['index'])
            if key in seen:
                raise ValueError('Duplicate email visual.')
            seen.add(key)
            manifest = self.manifest(p['id'], item['version'])
            try:
                saved = manifest['images'][item['index'] - 1]
            except (KeyError, IndexError):
                raise ValueError('This visual is unavailable.') from None
            if any(item[k] != saved[k] for k in ('version', 'index', 'source_hash', 'png_hash')):
                raise ValueError('The visual source or PNG hash does not match its immutable version.')

    def render(self, slides, folder):
        job = folder / 'job.json'
        job.write_text(json.dumps(dict(slides=slides, out=str(folder), local_only=True)), encoding='utf-8')
        try:
            # No PIPE: a browser descendant retaining a pipe must not defeat the timeout.
            process = subprocess.Popen([sys.executable, str(Path(__file__).with_name('render_carousel.py')), str(job)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            try:
                code = process.wait(timeout=120)
            except subprocess.TimeoutExpired:
                if sys.platform == 'win32':
                    subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL, timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)
                else:
                    process.kill()
                process.wait(timeout=10)
                raise ValueError('The visual render timed out. The saved visuals are unchanged.') from None
        except (OSError, subprocess.TimeoutExpired):
            raise ValueError('The visual renderer could not finish. The saved visuals are unchanged.') from None
        finally:
            job.unlink(missing_ok=True)
            (folder / '_render.html').unlink(missing_ok=True)
        if code:
            raise ValueError('The visual renderer failed. The saved visuals are unchanged.')

    def generate(self, data):
        if not isinstance(data, dict) or set(data) != {'email_id', 'revision', 'visuals'}:
            raise ValueError('Provide email_id, revision and visuals only.')
        p = self.emails.get(data['email_id'])
        if type(data['revision']) is not int or data['revision'] != p['revision']:
            raise Conflict('This email changed. Reload its saved revision before generating visuals.')
        if p.get('archived') or p['status'] not in ('Draft', 'Ready') or (p.get('kit') or {}).get('status') in ('scheduled', 'sending', 'completed'):
            raise ValueError('Visuals can only be generated for an active unsent draft.')
        visuals = data['visuals']
        if isinstance(visuals, list) and any(isinstance(v, dict) and 'import_id' in v for v in visuals):
            from .email_visual_imports import attach_pngs
            return attach_pngs(self, p, visuals)
        slides = normalize_visuals(visuals)
        for item in visuals:
            metadata(item, p['body'])
        version = uuid.uuid4().hex
        folder = self.folder(p['id'], version)
        folder.mkdir(parents=True, exist_ok=False)
        # Email rendering requires a fresh destination and never clears earlier output.
        self.render(slides, folder)
        images = []
        for i, (slide, item) in enumerate(zip(slides, visuals), 1):
            file = folder / f'slide_{i:02d}.png'
            if file.resolve().parent != folder:
                raise ValueError('Invalid rendered image location.')
            try:
                png = file.read_bytes()
            except OSError:
                raise ValueError('The render is incomplete. The saved visuals are unchanged.') from None
            if len(png) > 10000000 or not png.startswith(b'\x89PNG\r\n\x1a\n'):
                raise ValueError('The renderer did not produce a valid PNG.')
            from PIL import Image
            try:
                with Image.open(io.BytesIO(png)) as picture:
                    if picture.format != 'PNG' or picture.size != (slide['width'], slide['height']):
                        raise ValueError('The renderer did not produce the requested PNG dimensions.')
                    picture.verify()
            except OSError:
                raise ValueError('The rendered PNG is incomplete.') from None
            images.append(dict(version=version, index=i, source_hash=sha(json.dumps(slide, sort_keys=True, ensure_ascii=False).encode()),
                               png_hash=sha(png), alt=item['alt'], after_line=item['after_line']))
        (folder / 'manifest.json').write_text(json.dumps(dict(email_id=p['id'], base_revision=p['revision'], slides=slides, images=images), ensure_ascii=False), encoding='utf-8')
        # Optimistic commit after rendering: any intervening edit wins, retaining the last good set.
        return self.emails.save(p['id'], {'images': images}, p['revision'], sync_remote=False)

    def read_png(self, eid, version, index):
        if not isinstance(index, str) or not re.fullmatch(r'[1-9][0-9]?', index) or not 1 <= int(index) <= MAX_VISUALS:
            raise ValueError('Invalid visual index.')
        self.folder(eid, version)
        # Only committed versions (including history) are served, never orphan renders.
        image = next((v for row in self.emails.history(eid) for v in row.get('images', [])
                      if v['version'] == version and v['index'] == int(index)), None)
        if image is None:
            raise KeyError('Email visual not found.')
        file = self.folder(eid, version) / f'slide_{int(index):02d}.png'
        if file.resolve().parent != file.parent:
            raise ValueError('Invalid visual image location.')
        try:
            png = file.read_bytes()
        except OSError:
            raise ValueError('The visual PNG is unavailable. Generate a new version.') from None
        if sha(png) != image['png_hash']:
            raise ValueError('The visual file changed. Generate a new version before using it.')
        return png
