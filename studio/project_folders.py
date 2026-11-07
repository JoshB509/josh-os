"""UUID-bound video folders and bounded, local thumbnail discovery.

Construction creates database tables only. No folder is adopted or migrated on
startup. Only newly created projects and explicitly registered migrations map.
"""
import hashlib
import json
import os
import re
import threading
import time
import unicodedata
import uuid
from pathlib import Path

from .store import Conflict, now
from .lesson_audio import stable_reader

ROOT = Path('E:/CareerOS/12_YouTube')
SUBFOLDERS = ('thumbnails', 'thumbnails/sources', 'script', 'script/versions', 'video', 'Recorded/Input')
RESERVED = {'con', 'prn', 'aux', 'nul', *(f'com{i}' for i in range(1, 10)), *(f'lpt{i}' for i in range(1, 10))}


def slug(name):
    value = unicodedata.normalize('NFKD', name).encode('ascii', 'ignore').decode().lower()
    value = re.sub('[^a-z0-9]+', '-', value).strip('-')[:90].rstrip('-') or 'video'
    return 'video-' + value if value in RESERVED else value


def plain(path):
    """Reject links/reparse points before resolving; no string-prefix boundaries."""
    p = Path(path)
    if not p.is_absolute():
        raise ValueError('A project folder must be an absolute local path.')
    for part in (p, *p.parents):
        if part.is_symlink() or (part.exists() and getattr(part.stat(), 'st_file_attributes', 0) & 1024):
            raise ValueError('Linked project folders and files are not supported.')
    return p.resolve()


def signature(p):
    s = p.stat()
    return [s.st_size, s.st_mtime_ns, s.st_ctime_ns, s.st_ino]


class ProjectFolders:
    def __init__(self, store, root=ROOT, settle_seconds=2, clock=time.time):
        self.store, self.root = store, plain(root)
        self.settle_seconds, self.clock = settle_seconds, clock
        self.lock = threading.RLock()
        self.observed = {}
        self.errors = {}
        self.stop = threading.Event()
        with store.connect() as c:
            c.executescript('''
                CREATE TABLE IF NOT EXISTS project_folders(project_id TEXT PRIMARY KEY, folder TEXT NOT NULL UNIQUE COLLATE NOCASE);
                CREATE TABLE IF NOT EXISTS project_folder_files(project_id TEXT NOT NULL, name TEXT NOT NULL COLLATE NOCASE, signature TEXT NOT NULL, asset TEXT NOT NULL, PRIMARY KEY(project_id,name));
                CREATE TABLE IF NOT EXISTS project_folder_dismissals(project_id TEXT NOT NULL, asset TEXT NOT NULL, PRIMARY KEY(project_id,asset));
                CREATE TABLE IF NOT EXISTS project_discovery_revisions(project_id TEXT NOT NULL, revision INTEGER NOT NULL, additions TEXT NOT NULL, PRIMARY KEY(project_id,revision));
            ''')
        store.folders = self

    def _path(self, folder):
        if not re.fullmatch('[a-z0-9][a-z0-9-]{0,149}', folder) or folder in RESERVED:
            raise ValueError('Invalid saved project folder name.')
        p = plain(self.root / folder)
        if p.parent != plain(self.root):
            raise ValueError('Project folder is outside the YouTube root.')
        return p

    def _check_marker(self, p, pid):
        marker = plain(p / '.joshos' / 'project.json')
        if not marker.is_relative_to(p) or json.loads(marker.read_text(encoding='utf-8')) != {'schema': 1, 'project_id': pid}:
            raise ValueError('Project folder identity does not match JoshOS.')

    def provision(self, c, project):
        """Called inside the new-project transaction. Files are never overwritten."""
        pid = str(uuid.UUID(project['id']))
        existing = c.execute('SELECT folder FROM project_folders WHERE project_id=?', (pid,)).fetchone()
        if existing:
            p = self._path(existing['folder']); self._check_marker(p, pid)
            return p
        if not plain(self.root).is_dir():
            raise ValueError('The CareerOS YouTube root is unavailable. No project was created.')
        stem = slug(project['name'])
        for name in (stem, stem + '-' + pid[:8], stem + '-' + pid):
            p = self._path(name)
            if c.execute('SELECT 1 FROM project_folders WHERE folder=?', (name,)).fetchone():
                continue
            try:
                p.mkdir()
            except FileExistsError:
                try: self._check_marker(p, pid)
                except (OSError, ValueError): continue
            else:
                meta = plain(p / '.joshos'); meta.mkdir()
                with (meta / 'project.json').open('x', encoding='utf-8') as f:
                    json.dump({'schema': 1, 'project_id': pid}, f)
            for sub in SUBFOLDERS:
                child = plain(p / sub)
                if not child.is_relative_to(p): raise ValueError('Invalid project child folder.')
                child.mkdir(parents=True, exist_ok=True)
            c.execute('INSERT INTO project_folders VALUES(?,?)', (pid, name))
            return p
        raise Conflict('All safe folder names are occupied. No existing files were overwritten.')

    def path(self, pid, c=None):
        if c is None:
            with self.store.connect() as conn: return self.path(pid, conn)
        row = c.execute('SELECT folder FROM project_folders WHERE project_id=?', (pid,)).fetchone()
        if not row: return None
        p = self._path(row['folder']); self._check_marker(p, pid)
        return p

    def present(self, project):
        """Extra API metadata, never editable project fields."""
        p = dict(project)
        try:
            folder = self.path(p['id'])
            if folder:
                p['folder_info'] = {'project': str(folder), 'thumbnails': str(folder / 'thumbnails'),
                                    'scan_error': self.errors.get(p['id'], '')}
        except (OSError, ValueError) as e:
            p['folder_info'] = {'scan_error': str(e)}
        with self.store.connect() as c:
            revisions = {r['revision'] for r in c.execute('SELECT revision FROM project_discovery_revisions WHERE project_id=? AND revision<=?', (p['id'], p['revision']))}
        floor = p['revision']
        while floor in revisions: floor -= 1
        p['discovery_base_revision'] = floor
        return p

    def rebase_save(self, c, pid, changes, revision, previous):
        """Only discovery-only intervening revisions can be merged automatically."""
        if revision == previous['revision']: return changes
        if not isinstance(revision, int) or revision < 1 or revision > previous['revision']:
            raise Conflict('This project changed in another window. Reload before saving.')
        rows = c.execute('SELECT revision,additions FROM project_discovery_revisions WHERE project_id=? AND revision>? AND revision<=? ORDER BY revision', (pid, revision, previous['revision'])).fetchall()
        if [r['revision'] for r in rows] != list(range(revision + 1, previous['revision'] + 1)):
            raise Conflict('This project changed in another window. Your unsaved edits are preserved.')
        changes = dict(changes)
        if 'thumbnails' in changes:
            thumbs = list(changes['thumbnails']); seen = {t['asset'] for t in thumbs}
            for r in rows:
                for t in json.loads(r['additions']):
                    if t['asset'] not in seen: thumbs.append(t); seen.add(t['asset'])
            changes['thumbnails'] = thumbs
        return changes

    def track_dismissals(self, c, pid, previous, project):
        before = {t['asset'] for t in previous['thumbnails']}
        after = {t['asset'] for t in project['thumbnails']}
        for aid in before - after:
            c.execute('INSERT OR IGNORE INTO project_folder_dismissals VALUES(?,?)', (pid, aid))
        for aid in after - before:
            c.execute('DELETE FROM project_folder_dismissals WHERE project_id=? AND asset=?', (pid, aid))

    def scan(self, pid):
        with self.lock:
            try:
                result = self._scan(pid); self.errors.pop(pid, None); return result
            except (OSError, ValueError) as e:
                self.errors[pid] = str(e); return {'added': 0, 'error': str(e)}

    def _scan(self, pid):
        project = self.store.get(pid)
        folder = self.path(pid)
        if not folder or project['archived']: return {'added': 0}
        thumbdir = plain(folder / 'thumbnails')
        if thumbdir.parent != folder: raise ValueError('Thumbnail folder is outside the project.')
        candidates, seen_keys = [], set()
        for raw in sorted(thumbdir.iterdir(), key=lambda x: x.name.casefold()):
            if raw.suffix.lower() not in ('.png', '.jpg', '.jpeg', '.webp'): continue
            try:
                file = plain(raw)
                if file.parent != thumbdir or not file.is_file(): continue
                sig = signature(file); key = (pid, file.name.casefold()); seen_keys.add(key)
                if sig[0] > 20 * 1024 * 1024 or sig[0] == 0: continue
                prior = self.observed.get(key)
                if not prior or prior[0] != sig:
                    self.observed[key] = (sig, self.clock()); continue
                if self.clock() - prior[1] < self.settle_seconds or self.clock() - file.stat().st_mtime < self.settle_seconds: continue
                with self.store.connect() as c:
                    cached = c.execute('SELECT signature FROM project_folder_files WHERE project_id=? AND name=?', (pid, file.name)).fetchone()
                if cached and json.loads(cached['signature']) == sig: continue
                with stable_reader(file) as reader:
                    data = reader.read(20 * 1024 * 1024 + 1)
                    if len(data) != sig[0] or signature(file) != sig: continue
                    # Revalidate boundaries after opening; deny writer/delete sharing on Windows.
                    if plain(file).parent != plain(folder / 'thumbnails'): continue
                    t = self.store.import_image(file.name, data)
                t.update(source_path=str(file), source_kind='project_thumbnail_folder', candidate_status='candidate_unapproved')
                candidates.append((file.name, sig, t))
            except (OSError, ValueError):
                # Partial/corrupt/locked files are retried without harming existing assets.
                continue
        self.observed = {k: v for k, v in self.observed.items() if k[0] != pid or k in seen_keys}
        if not candidates: return {'added': 0}
        with self.store.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            p = json.loads(c.execute('SELECT body FROM projects WHERE id=?', (pid,)).fetchone()['body'])
            if p['archived'] or self.path(pid, c) != folder: return {'added': 0}
            ids = {t['asset'] for t in p['thumbnails']}; additions = []
            for name, sig, t in candidates:
                c.execute('INSERT OR REPLACE INTO project_folder_files VALUES(?,?,?,?)', (pid, name, json.dumps(sig), t['asset']))
                dismissed = c.execute('SELECT 1 FROM project_folder_dismissals WHERE project_id=? AND asset=?', (pid, t['asset'])).fetchone()
                if t['asset'] not in ids and not dismissed:
                    additions.append(t); ids.add(t['asset'])
            if additions:
                p['thumbnails'].extend(additions); p['revision'] += 1; p['updated_at'] = now()
                c.execute('UPDATE projects SET revision=?,body=? WHERE id=?', (p['revision'], json.dumps(p), pid))
                c.execute('INSERT INTO project_discovery_revisions VALUES(?,?,?)', (pid, p['revision'], json.dumps(additions)))
        return {'added': len(additions)}

    def loop(self):
        while not self.stop.is_set():
            try:
                with self.store.connect() as c: ids = [r['project_id'] for r in c.execute('SELECT project_id FROM project_folders')]
                for pid in ids:
                    if self.stop.is_set(): break
                    self.scan(pid)
            except Exception:
                # The next bounded cycle retries; never stop the existing application.
                pass
            self.stop.wait(3)
