"""Bounded local scenes -> ordered immutable carousel draft, with host receipts."""
import copy
import re
import shutil
import uuid
from pathlib import Path
from . import carousels, carousel_jobs
from .store import Conflict
from .local_asset_chat import requested as visual_requested
from .local_asset_spec import CREATE_LOCAL_ASSETS_SCHEMA, canonical, fields
from .local_assets import LocalAssets, digest, check_png
from .carousel_overrides import safe_path, verified_images, MAX_SET_BYTES, MAX_INPUT_BYTES

SCHEMA={'type':'object','additionalProperties':False,'required':['title','caption','slides'],'properties':{
 'title':{'type':'string','minLength':1,'maxLength':240},'caption':{'type':'string','maxLength':5000},
 'slides':{'type':'array','minItems':2,'maxItems':16,'items':{'type':'object','additionalProperties':False,'required':['text','visual'],'properties':{'text':{'type':'string','minLength':1,'maxLength':3000},'visual':copy.deepcopy(CREATE_LOCAL_ASSETS_SCHEMA['properties']['visuals']['items'])}}}}}


def requested(owner, message, proactive=False):
    return (owner == 'sam' and not proactive
            and bool(re.search(r'\bcarousels?\b', message, re.I))
            and bool(re.search(r'\b(?:original|geometric|illustrated|dot|diagram)\b', message, re.I))
            and visual_requested(re.sub(r'\bcarousels?\b', 'slides', message, flags=re.I)))


class OriginalCarousels:
    def __init__(self, tracker, local_chat, progress=None):
        self.tracker, self.local_chat = tracker, local_chat
        self.progress = progress or (lambda stage: None)

    def stage(self, state):
        carousel_jobs.mark(state)
        self.progress(state)

    def apply(self, request, *, owner, message, proactive=False, request_id=None):
        if owner != 'sam':
            raise ValueError('Only Sam can create Original carousel drafts.')
        # The same nonce replays its exact receipt. Identical content with a new
        # nonce is a new draft, not an implicit edit of an earlier idea/card.
        _, job, fresh = self.tracker.jobs.batch(request_id, [{'original_carousel': request}], None)[0]
        if fresh:
            try:
                with job.active():
                    job.mark('preparing')
                    self._apply(request, owner=owner, message=message, proactive=proactive,
                                target_id=job.record['carousel_id'])
            except Exception as exc:
                job.fail(exc)
        return job.receipt()

    def _apply(self, request, *, owner, message, proactive=False, target_id):
        if not requested(owner, message, proactive):
            raise PermissionError('Ask Sam to create an original carousel in this message.')
        if self.local_chat is None:
            raise ValueError('Local scene renderer is unavailable.')
        fields(request, {'title', 'caption', 'slides'}, {'title', 'caption', 'slides'}, 'original carousel')
        for key, limit in [('title', 240), ('caption', 5000)]:
            if not isinstance(request[key], str) or len(request[key]) > limit:
                raise ValueError('Invalid ' + key)
        if not request['title'].strip() or not isinstance(request['slides'], list) or not 2 <= len(request['slides']) <= 16:
            raise ValueError('Use a title and 2-16 slides.')
        texts, visuals = [], []
        for slide in request['slides']:
            fields(slide, {'text', 'visual'}, {'text', 'visual'}, 'slide')
            text = slide['text']
            if not isinstance(text, str) or not text.strip() or len(text) > 3000 or re.search(r'\n-{3}|<|\[(?:image|photo):', text):
                raise ValueError('Plain slide text required.')
            texts.append(text)
            visuals.append(slide['visual'])
        key = digest(canonical(request))
        cid = target_id
        try:
            existing = self.tracker.get(cid)
        except KeyError:
            existing = None
        if existing:
            raise Conflict('This request target already exists.')

        self.stage('rendering')
        made = self.local_chat.assets.create({'visuals': visuals}, owner='sam')
        if len(made['assets']) != len(visuals):
            raise ValueError('Incomplete scene batch.')
        version = uuid.uuid4().hex
        root = Path(carousels.SLIDE_ROOT).absolute()
        folder = safe_path(root, root / '_LocalOverrides' / cid / version)
        folder.mkdir(parents=True, exist_ok=False)
        committed = False
        try:
            rows, total, dimensions = [], 0, set()
            for index, asset in enumerate(made['assets']):
                png = self.local_chat.assets.read_png(asset['reference'], owner='sam')
                check_png(png, max_bytes=MAX_INPUT_BYTES)
                dimensions.add((asset['width'], asset['height']))
                total += len(png)
                if total > MAX_SET_BYTES or len(dimensions) > 1:
                    raise ValueError('Slides must share dimensions within the image budget.')
                name = f'slide_{index + 1:02d}.png'
                LocalAssets._write(safe_path(root, folder / name), png)
                rows.append(dict(slide_number=index + 1, file=name, sha256=digest(png)))
            manifest = canonical(dict(version=version, carousel_id=cid, owner='sam', slides=rows, request_hash=key))
            LocalAssets._write(safe_path(root, folder / 'manifest.json'), manifest)
            self.stage('verifying')
            words = '\n---\n'.join(texts)
            data = dict(id=cid, title=request['title'], owner='sam', caption=request['caption'], slides=words,
                        stage='Drafting', folder=str(folder),
                        notes='Original structured scenes. Draft only; no upload, scheduling or publication.',
                        design=dict(style='Original', words=self.tracker.words_hash({'slides': words}),
                                    override_version=version, manifest_hash=digest(manifest)))
            verified_images(self.tracker, data)
            self.stage('saving')
            row = self.tracker.create(data, commit_check=lambda connection: verified_images(self.tracker, data))
            committed = True
            return self.receipt(row)
        finally:
            if not committed:
                # Only this fresh version is owned by this attempt. Existing
                # carousel files and immutable local-asset batches stay intact.
                owned = safe_path(root, root / '_LocalOverrides' / cid / version)
                if folder == owned and folder.is_dir():
                    shutil.rmtree(owned)

    def receipt(self, row):
        files = verified_images(self.tracker, row)
        return dict(item=row, previews=[dict(slide_number=i + 1, carousel_id=row['id'], index=i,
                                            sha256=digest(path.read_bytes())) for i, path in enumerate(files)])
