"""Sam-only immutable single-slide overrides. No legacy SVG/path renderer.

Host supplies the selected carousel, current user attachments and actor. A model
supplies only the expected revision, slide number and bounded scene or owned PNG
ID. Originals remain untouched, including when rendering or CAS saving fails.
"""
import copy
from dataclasses import dataclass
import io
import json
from pathlib import Path
import re
import shutil
import stat
import uuid

from PIL import Image

from . import carousels as carousel_module
from .local_asset_chat import KEY, requested
from .local_asset_spec import CREATE_LOCAL_ASSETS_SCHEMA, MAX_INPUT_BYTES, MAX_PIXELS, canonical, fields
from .local_assets import AuthorizedImage, LocalAssets, check_png, digest
from .store import Conflict, now

MAX_SLIDES = 50
MAX_SET_BYTES = 128_000_000
SCHEMA = {
    'type': 'object', 'additionalProperties': False,
    'required': ['carousel_id', 'revision', 'slide_number'],
    'description': 'Replace ONE existing carousel slide locally, retaining all others exactly. '
        'Supply visual (same structured scene as create_local_assets) OR a Sam-owned local_asset_id, not both. '
        'slide_number is 1-based. Optional slide_text replaces only that slide text. No send or publication.',
    'properties': {
        'carousel_id': {'type': 'string'}, 'revision': {'type': 'integer', 'minimum': 1},
        'slide_number': {'type': 'integer', 'minimum': 1, 'maximum': MAX_SLIDES},
        'visual': copy.deepcopy(CREATE_LOCAL_ASSETS_SCHEMA['properties']['visuals']['items']),
        'local_asset_id': {'type': 'string', 'maxLength': 65},
        'slide_text': {'type': 'string', 'minLength': 1, 'maxLength': 3000},
    },
}


def wants_override(owner, message, proactive=False):
    if owner != 'sam' or proactive:
        return False
    text = str(message or '').lower()
    if not re.search(r'\b(?:slides?|carousel|cta)\b', text):
        return False
    if not re.search(r'\b(?:replace|override|swap|redesign|redo|revise|change|update|attach|last|final|first)\b|\bslide\s+\d+', text):
        return False
    text = re.sub(r'\b(?:replace|override|swap|redo|attach)\b', 'redesign', text)
    text = re.sub(r'\bcta\b', 'slide', text)
    return requested(text)


def safe_path(base, path):
    base, path = Path(base).absolute(), Path(path).absolute()
    if base.resolve() != base or path.resolve() != path or not path.is_relative_to(base):
        raise ValueError('Carousel assets must stay inside their owned storage without links.')
    for part in [base, *[base.joinpath(*path.relative_to(base).parts[:i]) for i in range(1, len(path.relative_to(base).parts) + 1)]]:
        if part.exists() or part.is_symlink():
            attrs = getattr(part.lstat(), 'st_file_attributes', 0)
            if part.is_symlink() or attrs & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400):
                raise ValueError('Linked carousel assets are not supported.')
    return path


def image_info(raw):
    if len(raw) > MAX_INPUT_BYTES:
        raise ValueError('Carousel image exceeds the byte budget.')
    try:
        with Image.open(io.BytesIO(raw)) as im:
            if im.format not in ('PNG', 'JPEG', 'WEBP') or im.width * im.height > MAX_PIXELS or getattr(im, 'n_frames', 1) != 1:
                raise ValueError('Use a bounded, single-frame PNG, JPEG or WebP image.')
            size, fmt = im.size, im.format
            im.verify()
    except (OSError, SyntaxError, Image.DecompressionBombError):
        raise ValueError('Invalid carousel source image.') from None
    return size, fmt


def as_png(raw):
    image_info(raw)
    with Image.open(io.BytesIO(raw)) as im:
        output = io.BytesIO()
        im.convert('RGBA').save(output, format='PNG')
    png = output.getvalue()
    check_png(png, max_bytes=MAX_INPUT_BYTES)
    return AuthorizedImage(png, digest(png))


def blocks(text):
    start, result = 0, []
    for separator in re.finditer(r'\n-{3,}\n?', text):
        if text[start:separator.start()].strip():
            result.append((start, separator.start()))
        start = separator.end()
    if text[start:].strip():
        result.append((start, len(text)))
    return result


def verified_images(tracker, item):
    """Verify a committed override manifest and bytes before serving/reusing it."""
    root = Path(carousel_module.SLIDE_ROOT).absolute()
    design = item.get('design') or {}
    version = design.get('override_version')
    if not isinstance(version, str) or not re.fullmatch(r'[0-9a-f]{32}', version):
        raise ValueError('Invalid carousel override version.')
    if str(uuid.UUID(item['id'])) != item['id']:
        raise ValueError('Invalid carousel identity.')
    folder = safe_path(root, root / '_LocalOverrides' / item['id'] / version)
    recorded = Path(item.get('folder') or '')
    if (recorded if recorded.is_absolute() else root / recorded) != folder:
        raise ValueError('Carousel override folder identity changed.')
    raw = LocalAssets._read(safe_path(root, folder / 'manifest.json'), 100_000)
    if digest(raw) != design.get('manifest_hash'):
        raise ValueError('Carousel override manifest changed.')
    manifest = json.loads(raw)
    if manifest.get('carousel_id') != item['id'] or manifest.get('owner') != item['owner'] or manifest.get('version') != version:
        raise ValueError('Carousel override ownership changed.')
    rows = manifest.get('slides')
    if not isinstance(rows, list) or not 1 <= len(rows) <= MAX_SLIDES:
        raise ValueError('Invalid carousel override image set.')
    files, total = [], 0
    for index, row in enumerate(rows):
        if not isinstance(row, dict) or row.get('slide_number') != index + 1 or row.get('file') not in [f'slide_{index + 1:02d}' + ext for ext in ('.png', '.jpg', '.jpeg', '.webp')]:
            raise ValueError('Carousel override slide identity changed.')
        path = safe_path(root, folder / row['file'])
        data = LocalAssets._read(path, MAX_INPUT_BYTES)
        total += len(data)
        if total > MAX_SET_BYTES or digest(data) != row.get('sha256'):
            raise ValueError('Carousel override image bytes changed.')
        image_info(data)
        files.append(path)
    return files


def read_slide(tracker, item, index):
    files = verified_images(tracker, item)
    if not 0 <= index < len(files):
        raise KeyError('Slide not found.')
    path = files[index]
    data = LocalAssets._read(safe_path(carousel_module.SLIDE_ROOT, path), MAX_INPUT_BYTES)
    raw = LocalAssets._read(safe_path(carousel_module.SLIDE_ROOT, path.parent / 'manifest.json'), 100_000)
    if digest(raw) != item['design']['manifest_hash']:
        raise ValueError('Carousel override manifest changed while reading.')
    manifest = json.loads(raw)
    if digest(data) != manifest['slides'][index]['sha256']:
        raise ValueError('Carousel override image changed while reading.')
    return data, carousel_module.IMAGE_TYPES[path.suffix.lower()]


@dataclass(frozen=True)
class Scope:
    carousel_id: str
    revision: int
    slide_number: int
    originals: tuple
    uploads: tuple


class CarouselOverrides:
    def __init__(self, tracker, local_chat):
        self.tracker, self.local_chat = tracker, local_chat
        self.root = Path(carousel_module.SLIDE_ROOT).absolute()

    def _images(self, item):
        raw_folder = Path(item.get('folder') or '')
        folder = safe_path(self.root, raw_folder if raw_folder.is_absolute() else self.root / raw_folder)
        # Legacy records can point at shared directories. Do not treat another
        # owner's carousel directory as Sam's merely because a record names it.
        with self.tracker.store.connect() as connection:
            others = [json.loads(row['body']) for row in connection.execute('SELECT body FROM carousels')]
        for other in others:
            if other.get('owner') == 'sam' or not other.get('folder'):
                continue
            other_folder = Path(other['folder'])
            other_folder = (other_folder if other_folder.is_absolute() else self.root / other_folder).resolve()
            if folder == other_folder or folder.is_relative_to(other_folder):
                raise PermissionError('This image directory is also assigned to another carousel owner.')
        paths = self.tracker.images(item)
        if not 1 <= len(paths) <= MAX_SLIDES or len(paths) != len(blocks(item['slides'])):
            raise ValueError('Render the existing carousel first; its image and text slide counts must match.')
        words = item.get('design', {}).get('words')
        if words and words != self.tracker.words_hash(item):
            raise ValueError('The existing carousel design is stale. Review it before replacing a slide.')
        result, total = [], 0
        for path in paths:
            raw = LocalAssets._read(safe_path(self.root, path), MAX_INPUT_BYTES)
            image_info(raw)
            total += len(raw)
            if total > MAX_SET_BYTES:
                raise ValueError('Carousel images exceed the set byte budget.')
            result.append((str(path), digest(raw)))
        return tuple(result)

    def prepare(self, *, owner, message, context, items, images=(), proactive=False):
        if not wants_override(owner, message, proactive):
            return None
        mine = [x for x in items if x.get('owner') == 'sam' and not x.get('archived')]
        context = context if isinstance(context, dict) else {}
        if context.get('kind') == 'carousel':
            matches = [x for x in mine if x['id'] == context.get('id')]
        else:
            matches = [x for x in mine if x['id'] in message or x['title'].casefold() in message.casefold()]
        if len(matches) != 1:
            return None
        item = self.tracker.get(matches[0]['id'])
        if item['owner'] != owner or item.get('archived'):
            raise PermissionError('This carousel is not owned by Sam.')
        if item['stage'] in ('Scheduled', 'Posted'):
            raise ValueError('Scheduled or posted carousels cannot use the local slide override.')
        if len(images) > 16 or any(not re.fullmatch(r'[0-9a-f]{64}', x) for x in images):
            raise ValueError('Use current chat image attachment IDs.')
        originals = self._images(item)
        # Bind the slide to the user's words as well as the carousel. The model
        # cannot redirect 'the final slide' to one of the first eight slides.
        text = str(message).lower()
        numbers = {int(n) for n in re.findall(r'\b(?:slide|cta)\s*#?\s*(\d{1,2})\b', text)}
        numbers.update(int(n) for n in re.findall(r'\b(\d{1,2})(?:st|nd|rd|th)\s+slide\b', text))
        if re.search(r'\b(?:last|final)\s+(?:carousel\s+)?(?:slide|cta)\b', text):
            numbers.add(len(originals))
        if re.search(r'\bfirst\s+(?:carousel\s+)?(?:slide|cta)\b', text):
            numbers.add(1)
        if len(numbers) != 1 or not 1 <= next(iter(numbers)) <= len(originals):
            raise ValueError('Name exactly one existing slide number, or the first/last slide, to replace.')
        return Scope(item['id'], item['revision'], next(iter(numbers)), originals, tuple(images))

    def context(self, scope):
        sources = [dict(asset_id=f'slide_{i + 1}_{sha}', slide_number=i + 1) for i, (_, sha) in enumerate(scope.originals)]
        sources += [dict(asset_id='upload_' + aid) for aid in scope.uploads]
        return 'carousel_slide_override is available for ' + json.dumps(dict(
            carousel_id=scope.carousel_id, revision=scope.revision, slide_number=scope.slide_number,
            slide_count=len(scope.originals), sources=sources)) + (
            '. Replace exactly one existing slide. Supply visual OR a Sam-owned local_asset_id. '
            'Use any dimensions, layers and optional branding; match existing slide dimensions for a consistent carousel. '
            'A cover/photo and QR must be supplied as these authorized image IDs or Sam-owned LocalAssets IDs. '
            'No arbitrary files, URLs, SVG, new QR backend or generative backend. '
            'Other slides and their image bytes remain unchanged. Do not also send carousels/make_design in this reply. '
            'Keep requested explanation in reply; the host appends a verified receipt. Nothing is sent or published.')

    def _source(self, scope, owner, aid):
        if owner != 'sam':
            raise PermissionError('Only Sam can compose carousel overrides.')
        if KEY.fullmatch(aid):
            png = self.local_chat.read_png(owner, aid)
            return AuthorizedImage(png, digest(png))
        if aid.startswith('upload_') and aid[7:] in scope.uploads:
            asset = aid[7:]
            with self.tracker.store.connect() as connection:
                row = connection.execute('SELECT filename FROM assets WHERE id=?', (asset,)).fetchone()
            if not row or row['filename'] not in [asset + ext for ext in ('.png', '.jpg', '.webp')]:
                raise ValueError('Attachment source identity changed.')
            base = self.tracker.store.root / 'assets'
            raw = LocalAssets._read(safe_path(base, base / row['filename']), MAX_INPUT_BYTES)
            if digest(raw) != asset:
                raise ValueError('Attachment bytes changed.')
            return as_png(raw)
        for index, (path, sha) in enumerate(scope.originals):
            if aid == f'slide_{index + 1}_{sha}':
                raw = LocalAssets._read(safe_path(self.root, Path(path)), MAX_INPUT_BYTES)
                if digest(raw) != sha:
                    raise ValueError('Carousel source bytes changed.')
                return as_png(raw)
        raise PermissionError('Image ID is not authorized for this carousel request.')

    def _current(self, scope):
        item = self.tracker.get(scope.carousel_id)
        if item['owner'] != 'sam' or item.get('archived'):
            raise PermissionError('This carousel is no longer owned by Sam.')
        if item['stage'] in ('Scheduled', 'Posted'):
            raise ValueError('Scheduled or posted carousels cannot use the local slide override.')
        if item['revision'] != scope.revision:
            raise Conflict('The carousel changed; reload before replacing a slide.')
        if self._images(item) != scope.originals:
            raise Conflict('The carousel image set changed; reload before replacing a slide.')
        return item

    def apply(self, request, *, scope, owner, message, proactive=False):
        if not wants_override(owner, message, proactive) or not isinstance(scope, Scope):
            raise PermissionError('Ask Sam to replace a slide in the selected or explicitly named carousel.')
        fields(request, {'carousel_id', 'revision', 'slide_number', 'visual', 'local_asset_id', 'slide_text'},
               {'carousel_id', 'revision', 'slide_number'}, 'carousel slide override')
        if request['carousel_id'] != scope.carousel_id or type(request['revision']) is not int or request['revision'] != scope.revision:
            raise Conflict('The requested carousel/revision does not match the current user target.')
        number = request['slide_number']
        if type(number) is not int or not 1 <= number <= len(scope.originals):
            raise ValueError('Choose one existing slide number, starting at 1.')
        if number != scope.slide_number:
            raise PermissionError('The slide number does not match the current user request.')
        if ('visual' in request) == ('local_asset_id' in request):
            raise ValueError('Supply exactly one visual or local_asset_id.')
        item = self._current(scope)
        text = request.get('slide_text')
        if text is not None and (not isinstance(text, str) or not text.strip() or len(text) > 3000 or '<' in text or re.search(r'\n-{3,}|\[(?:image|picture|photo):', text, re.I)):
            raise ValueError('slide_text must be plain text for this slide only.')
        reference = None
        if 'visual' in request:
            renderer = LocalAssets(self.local_chat.assets.root, resolve_image=lambda actor, aid: self._source(scope, actor, aid))
            made = renderer.create({'visuals': [request['visual']]}, owner=owner)
            reference = made['assets'][0]['reference']
            png = renderer.read_png(reference, owner=owner)
        else:
            aid = request['local_asset_id']
            if not isinstance(aid, str) or not KEY.fullmatch(aid):
                raise ValueError('Use a generated Sam-owned LocalAssets ID.')
            reference = self.local_chat._reference(owner, aid)
            png = self.local_chat.read_png(owner, aid)
        check_png(png, max_bytes=MAX_INPUT_BYTES)
        item = self._current(scope)  # Recheck after the bounded renderer returns.
        version = uuid.uuid4().hex
        base = safe_path(self.root, self.root / '_LocalOverrides' / scope.carousel_id)
        base.mkdir(parents=True, exist_ok=True)
        stage = safe_path(self.root, base / ('.staging-' + version))
        final = safe_path(self.root, base / version)
        stage.mkdir(exist_ok=False)
        try:
            rows, total = [], 0
            for index, (path, sha) in enumerate(scope.originals):
                raw = LocalAssets._read(safe_path(self.root, Path(path)), MAX_INPUT_BYTES)
                if digest(raw) != sha:
                    raise Conflict('An original carousel image changed during composition.')
                out = png if index == number - 1 else raw
                total += len(out)
                if total > MAX_SET_BYTES:
                    raise ValueError('Carousel override exceeds the set byte budget.')
                ext = '.png' if index == number - 1 else Path(path).suffix.lower()
                filename = f'slide_{index + 1:02d}' + ext
                LocalAssets._write(safe_path(self.root, stage / filename), out)
                rows.append(dict(slide_number=index + 1, file=filename, sha256=digest(out), original_sha256=sha))
            manifest = canonical(dict(version=version, carousel_id=scope.carousel_id, owner=owner,
                previous_revision=scope.revision, replaced_slide=number, local_asset=reference, slides=rows))
            LocalAssets._write(safe_path(self.root, stage / 'manifest.json'), manifest)
            self._current(scope)
            if final.exists():
                raise ValueError('Generated carousel version collision.')
            stage.rename(final)
            stage = None
            slides = item['slides']
            if text is not None:
                start, end = blocks(slides)[number - 1]
                slides = slides[:start] + text.strip() + slides[end:]
            changes = dict(folder=str(final.relative_to(self.root)), slides=slides, stage='Ready for review',
                design=dict(style='Custom', words=self.tracker.words_hash(dict(slides=slides)), at=now(),
                            override_version=version, manifest_hash=digest(manifest)))
            # Existing optimistic DB save is the final ownership/revision fence.
            # A conflict leaves only an unreferenced immutable version, never a
            # half-updated carousel or overwritten previous slide images.
            saved = self.tracker.save(scope.carousel_id, changes, scope.revision)
            return dict(id=saved['id'], revision=saved['revision'], title=saved['title'], action='updated',
                        slide_number=number, override_version=version)
        finally:
            if stage is not None and stage.exists():
                if stage.parent != base or stage.name != '.staging-' + version:
                    raise ValueError('Invalid override staging cleanup target.')
                shutil.rmtree(safe_path(self.root, stage))
