"""Curated, local B-roll discovery. Selection never changes a timeline or audio.
Manifest paths are host configuration, never model-supplied paths.
"""
import hashlib
import json
import math
import re
from pathlib import Path

DEFAULT_MANIFESTS = (
    Path(r"C:\Users\Anigma PC\Videos\CareerOS B-roll 2026-11-18\clip-manifest.json"),
    Path(r"C:\Users\Anigma PC\Videos\CareerOS B-roll 2026-11-18\V2\clip-manifest.json"),
)

def catalog(manifests=None):
    rows = []
    for manifest in DEFAULT_MANIFESTS if manifests is None else manifests:
        manifest = Path(manifest)
        if not manifest.is_file():
            continue
        root = manifest.resolve().parent
        body = json.loads(manifest.read_text(encoding='utf-8-sig'))
        for clip in body['clips']:
            name = clip['file']
            if not isinstance(name, str) or Path(name).name != name or '/' in name or '\\' in name:
                raise ValueError('B-roll filenames must be local basenames.')
            asset = (root / name).resolve(strict=True)
            if not asset.is_relative_to(root) or not asset.is_file() or asset.suffix.lower() != '.mp4':
                raise ValueError('B-roll asset must stay within its curated library.')
            duration = clip['duration_seconds']
            start = clip['source_start_seconds']
            if any(isinstance(n, bool) or not isinstance(n, (int, float)) or not math.isfinite(n) for n in (duration, start)) or duration <= 0 or start < 0:
                raise ValueError('Invalid B-roll source timing.')
            digest = hashlib.sha256(asset.read_bytes()).hexdigest()
            rows.append(dict(id=root.name + '/' + asset.stem, title=re.sub(r'^\d+-', '', asset.stem).replace('-', ' '), collection=root.name,
                             path=str(asset), sha256=digest, duration_seconds=duration,
                             source_start_seconds=start, source=body.get('source'),
                             treatment=body.get('treatment'), output=body.get('output')))
    if len({r['id'] for r in rows}) != len(rows):
        raise ValueError('Duplicate B-roll identities in the curated library.')
    return rows

def selection(rows, ids):
    if not isinstance(ids, list) or not 1 <= len(ids) <= 50 or any(not isinstance(i, str) for i in ids) or len(set(ids)) != len(ids):
        raise ValueError('Select 1-50 unique B-roll IDs from the current catalog.')
    by_id = {r['id']: r for r in rows}
    if any(i not in by_id for i in ids):
        raise ValueError('B-roll selection is not in the current catalog.')
    return dict(status='selected_not_inserted', clips=[by_id[i] for i in ids],
                timeline_modified=False, audio_modified=False,
                next_step='Place selected clips against the approved CTA in a versioned edit; timeline insertion is not implemented by this action.')
