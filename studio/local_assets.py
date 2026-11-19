"""Host-owned immutable local visual creation, independent of workflow records.

Integration contract:
  service = LocalAssets(data_root / 'local-assets', resolve_image=authorized_local_png)
  result = service.create(model_json, owner=authenticated_agent_id)
  png = service.read_png(result['assets'][0]['reference'], owner=authenticated_agent_id)

The host must derive owner from the authenticated dispatch, never model JSON.
resolve_image(owner, asset_id) must authorize that exact existing image for this
owner BEFORE reading it, and return AuthorizedImage; a global store.asset lookup
alone is insufficient. The callback must be local and never fetch URLs. Omitting
it denies all existing image inputs. No callback is needed for shapes/text.

Nothing here attaches assets to records, shares them with other agents, sends
files, changes approvals, calls connectors, or grants model filesystem tools.
Callers must retain current-request/proactive gates and return real failure
results. Creation is not delivery permission. No generative backend is provided.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import threading
import uuid

from PIL import Image

from . import local_asset_spec as spec

RENDER_TIMEOUT = 90
_SLOTS = threading.BoundedSemaphore(2)  # Per-process, across all service instances.
_TOKEN = re.compile(r'[0-9a-f]{32}')
_REFERENCE_FIELDS = {'batch_id', 'asset_id', 'version', 'source_hash', 'png_hash'}


@dataclass(frozen=True)
class AuthorizedImage:
    """Bytes and digest supplied by a trusted local authorization resolver."""
    png: bytes
    sha256: str


def digest(data):
    return hashlib.sha256(data).hexdigest()


def check_png(data, *, dimensions=None, max_bytes=spec.MAX_PNG_BYTES):
    if not isinstance(data, bytes) or len(data) > max_bytes or not data.startswith(b'\x89PNG\r\n\x1a\n'):
        raise ValueError('Expected a bounded PNG image.')
    try:
        with Image.open(io.BytesIO(data)) as picture:
            size = picture.size
            if picture.format != 'PNG' or size[0] * size[1] > spec.MAX_PIXELS or getattr(picture, 'n_frames', 1) != 1:
                raise ValueError('PNG exceeds the image limits.')
            if dimensions is not None and size != dimensions:
                raise ValueError('Rendered PNG dimensions do not match the scene.')
            picture.verify()
    except (OSError, SyntaxError, Image.DecompressionBombError):
        raise ValueError('PNG is corrupt or unavailable.') from None
    return size


class LocalAssets:
    def __init__(self, root, *, resolve_image=None):
        # Construction is read-only; storage is created only for a creation request.
        self.root = Path(root).resolve()
        self.resolve_image = resolve_image

    @staticmethod
    def _owner(value):
        if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,99}', value):
            raise ValueError('A host-supplied owner identity is required.')
        return value

    def _path(self, *parts):
        path = self.root.joinpath(*parts)
        if not path.is_relative_to(self.root) or path.resolve() != path:
            raise ValueError('Local asset storage must not redirect through links.')
        # On Windows also reject junctions and other reparse points, not just symlinks.
        for item in [self.root, *[self.root.joinpath(*parts[:i]) for i in range(1, len(parts) + 1)]]:
            if item.exists() or item.is_symlink():
                attrs = getattr(item.lstat(), 'st_file_attributes', 0)
                if item.is_symlink() or attrs & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400):
                    raise ValueError('Local asset storage links are not supported.')
        return path

    @staticmethod
    def _read(file, limit):
        with file.open('rb') as handle:
            data = handle.read(limit + 1)
        if len(data) > limit:
            raise ValueError('Asset file exceeds its size limit.')
        return data

    @staticmethod
    def _write(file, data):
        with file.open('xb') as handle:
            handle.write(data)

    def _sources(self, owner, scenes):
        ids = sorted({layer['asset_id'] for s in scenes for layer in s['layers'] if layer['kind'] == 'image'})
        result, total = {}, 0
        for aid in ids:
            if self.resolve_image is None:
                raise PermissionError('Existing image inputs require a host authorization resolver.')
            source = self.resolve_image(owner, aid)
            if not isinstance(source, AuthorizedImage) or not isinstance(source.sha256, str) or not spec.HASH.fullmatch(source.sha256):
                raise ValueError('The image resolver did not return an authorized PNG and digest.')
            check_png(source.png, max_bytes=spec.MAX_INPUT_BYTES)
            total += len(source.png)
            if total > spec.MAX_TOTAL_INPUT_BYTES or digest(source.png) != source.sha256:
                raise ValueError('Source image hash or total size mismatch.')
            result[aid] = source
        return result

    def _render(self, stage):
        worker = Path(__file__).with_name('local_asset_renderer.py').resolve()
        # Do not inherit credentials, PYTHONPATH, user site packages or user config.
        env = {k: v for k, v in os.environ.items() if k.upper() in {'SYSTEMROOT', 'WINDIR', 'SYSTEMDRIVE', 'TEMP', 'TMP'}}
        options = dict(cwd=str(stage), env=env, stdin=subprocess.DEVNULL,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        process = subprocess.Popen([sys.executable, '-I', '-B', str(worker), str(stage / 'job.json')], **options)
        try:
            code = process.wait(timeout=RENDER_TIMEOUT)
        except subprocess.TimeoutExpired:
            # This fixed Pillow worker never launches child processes or browsers.
            process.kill()
            process.wait(timeout=10)
            raise ValueError('Local visual rendering timed out; no assets were committed.') from None
        if code:
            raise ValueError('Local visual rendering failed; check text fit and local font availability.')

    def create(self, request, *, owner):
        owner = self._owner(owner)
        scenes = spec.validate_request(request)
        if not _SLOTS.acquire(blocking=False):
            raise ValueError('Local visual rendering is busy; retry after a current job finishes.')
        stage = None
        try:
            sources = self._sources(owner, scenes)
            batch_id = uuid.uuid4().hex
            self._path().mkdir(parents=True, exist_ok=True)
            self._path('.staging').mkdir(exist_ok=True)
            self._path('batches').mkdir(exist_ok=True)
            stage = self._path('.staging', batch_id)
            stage.mkdir(exist_ok=False)
            inputs = {}
            for index, (aid, source) in enumerate(sources.items()):
                name = f'input_{index:02d}.png'
                self._write(self._path('.staging', batch_id, name), source.png)
                inputs[aid] = dict(file=name, sha256=source.sha256)
            self._write(stage / 'job.json', spec.canonical(dict(visuals=scenes, inputs=inputs, renderer=spec.RENDERER_VERSION)))
            self._render(stage)
            render_meta = json.loads(self._read(self._path('.staging', batch_id, 'render_metadata.json'), 10_000))
            spec.fields(render_meta, {'renderer', 'fonts'}, {'renderer', 'fonts'}, 'renderer metadata')
            if render_meta['renderer'] != spec.RENDERER_VERSION or not isinstance(render_meta['fonts'], dict) or len(render_meta['fonts']) > 6:
                raise ValueError('Invalid renderer metadata.')
            if any(not isinstance(k, str) or len(k) > 100 or not isinstance(v, str) or not spec.HASH.fullmatch(v) for k, v in render_meta['fonts'].items()):
                raise ValueError('Invalid font provenance.')
            records, total = [], 0
            for index, scene in enumerate(scenes):
                image_path = self._path('.staging', batch_id, f'render_{index:02d}.png')
                png = self._read(image_path, spec.MAX_PNG_BYTES)
                check_png(png, dimensions=(scene['width'], scene['height']))
                total += len(png)
                if total > spec.MAX_TOTAL_PNG_BYTES:
                    raise ValueError('Rendered images exceed the batch byte budget.')
                source = dict(spec=scene, renderer=render_meta,
                    inputs={aid: sources[aid].sha256 for aid in sorted({x['asset_id'] for x in scene['layers'] if x['kind'] == 'image'})})
                reference = dict(batch_id=batch_id, asset_id=uuid.uuid4().hex, version=uuid.uuid4().hex,
                                 source_hash=digest(spec.canonical(source)), png_hash=digest(png))
                name = reference['asset_id'] + '-' + reference['version'] + '.png'
                self._write(self._path('.staging', batch_id, name), png)
                records.append(dict(reference=reference, source=source, width=scene['width'], height=scene['height'],
                                    title=scene['title'], alt=scene['alt']))
            manifest = dict(schema=spec.SCHEMA_VERSION, batch_id=batch_id, owner=owner,
                created_at=datetime.now(timezone.utc).isoformat(), assets=records)
            encoded = spec.canonical(manifest)
            if len(encoded) > spec.MAX_MANIFEST_BYTES:
                raise ValueError('Asset manifest exceeds its size limit.')
            self._write(self._path('.staging', batch_id, 'manifest.json'), encoded)
            # Publish only canonical PNGs + manifest. Inputs/jobs never become downloadable.
            for name in ['job.json', 'render_metadata.json', *[v['file'] for v in inputs.values()],
                         *[f'render_{i:02d}.png' for i in range(len(scenes))]]:
                self._path('.staging', batch_id, name).unlink()
            final = self._path('batches', batch_id)
            if final.exists():
                raise ValueError('Generated asset identifier collision; nothing was replaced.')
            self._path('.staging', batch_id).rename(final)
            stage = None
            return dict(batch_id=batch_id, assets=[{k: r[k] for k in ('reference', 'width', 'height', 'title', 'alt')} for r in records])
        finally:
            try:
                if stage is not None and stage.exists():
                    # Only the fresh staging directory made by this invocation can be removed.
                    if stage.parent != self.root / '.staging' or not _TOKEN.fullmatch(stage.name):
                        raise ValueError('Invalid local render cleanup target.')
                    shutil.rmtree(self._path('.staging', stage.name))
            finally:
                _SLOTS.release()

    def _record(self, reference, owner):
        owner = self._owner(owner)
        spec.fields(reference, _REFERENCE_FIELDS, _REFERENCE_FIELDS, 'asset reference')
        for key, pattern in [('batch_id', _TOKEN), ('asset_id', _TOKEN), ('version', _TOKEN),
                             ('source_hash', spec.HASH), ('png_hash', spec.HASH)]:
            if not isinstance(reference[key], str) or not pattern.fullmatch(reference[key]):
                raise ValueError('Invalid asset reference.')
        try:
            raw = self._read(self._path('batches', reference['batch_id'], 'manifest.json'), spec.MAX_MANIFEST_BYTES)
        except FileNotFoundError:
            raise KeyError('Committed local asset not found.') from None
        try:
            manifest = json.loads(raw)
            if manifest.get('owner') != owner:
                raise PermissionError('This local asset belongs to a different owner.')
            if manifest.get('schema') != spec.SCHEMA_VERSION or manifest.get('batch_id') != reference['batch_id']:
                raise ValueError('Local asset manifest identity changed.')
            record = next((r for r in manifest['assets'] if r.get('reference') == reference), None)
            if record is None or digest(spec.canonical(record['source'])) != reference['source_hash']:
                raise ValueError('Local asset source or reference changed.')
            scene = spec.visual(record['source']['spec'])
            if scene != record['source']['spec'] or (record['width'], record['height'], record['alt'], record['title']) != (scene['width'], scene['height'], scene['alt'], scene['title']):
                raise ValueError('Local asset metadata changed.')
        except (KeyError, TypeError, AttributeError, json.JSONDecodeError):
            raise ValueError('Local asset manifest is invalid.') from None
        return record

    def read_png(self, reference, *, owner):
        record = self._record(reference, owner)
        name = reference['asset_id'] + '-' + reference['version'] + '.png'
        png = self._read(self._path('batches', reference['batch_id'], name), spec.MAX_PNG_BYTES)
        if digest(png) != reference['png_hash']:
            raise ValueError('Local asset PNG changed; create a new version.')
        check_png(png, dimensions=(record['width'], record['height']))
        return png
