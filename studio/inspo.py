"""Creator carousels we learn from, such as Ali Abdaal and Alex Hormozi.

Each post is kept with its slides (in a folder under _Inspo on the E drive), caption,
likes, comments and the same tags our own carousels use, so posts can be compared side
by side. A breakout is a post with at least twice the median likes of that creator's
previous 20 posts. "New thing" marks a post where the creator tried something we haven't
seen from them before, with a note on whether it worked.
"""
import csv
import json
import re
import statistics
import urllib.parse
import urllib.request
import uuid
from datetime import date
from pathlib import Path
from .carousels import clean_tags, IMAGE_TYPES
from .store import Conflict, now

INSPO_ROOT = Path(r'E:\CareerOS\03_Community_Posts\_Inspo')
CREATORS = ['Ali Abdaal', 'Alex Hormozi']
FIELDS = {'creator', 'date', 'url', 'hook', 'caption', 'slides', 'likes', 'comments', 'folder', 'tags', 'look',
          'new_thing', 'new_note', 'notes', 'checked_at', 'archived'}
TEXT = {'creator': 80, 'url': 1000, 'hook': 1000, 'caption': 10000, 'folder': 500, 'look': 500, 'new_note': 1000,
        'notes': 5000, 'checked_at': 40}
# Slide images may only be fetched from YouTube's own image hosts.
IMAGE_HOSTS = ('.ggpht.com', '.googleusercontent.com', '.ytimg.com')


def likes_number(text):
    """'1.5K' -> 1500, '343' -> 343."""
    t = str(text or '').strip().upper().replace(',', '')
    m = re.fullmatch(r'([\d.]+)\s*([KM]?)', t)
    if not m:
        return None
    n = float(m.group(1)) * {'': 1, 'K': 1000, 'M': 1000000}[m.group(2)]
    return int(round(n))


class Inspo:
    def __init__(self, store, root=INSPO_ROOT):
        self.store, self.root = store, Path(root)
        with store.connect() as c:
            c.execute('CREATE TABLE IF NOT EXISTS inspo_posts(id TEXT PRIMARY KEY,revision INTEGER NOT NULL,body TEXT NOT NULL)')

    # Reading ---------------------------------------------------------------
    def rows(self):
        with self.store.connect() as c:
            return [json.loads(r['body']) for r in c.execute('SELECT body FROM inspo_posts')]

    def all(self):
        items = [p for p in self.rows() if not p.get('archived')]
        items.sort(key=lambda p: (p['date'], p['id']), reverse=True)
        by_creator = {}
        for p in sorted(items, key=lambda p: p['date']):
            prev = by_creator.setdefault(p['creator'], [])
            base = [x for x in prev[-20:] if x is not None]
            median = statistics.median(base) if len(base) >= 5 else None
            p['median_before'] = median
            p['breakout'] = bool(median and p.get('likes') is not None and p['likes'] >= 2 * median)
            prev.append(p.get('likes'))
        for p in items:
            p['images'] = len(self.images(p))
        creators = sorted({p['creator'] for p in items} | set(CREATORS))
        return dict(items=items, creators=creators)

    def get(self, pid):
        with self.store.connect() as c:
            row = c.execute('SELECT body FROM inspo_posts WHERE id=?', (pid,)).fetchone()
        if not row:
            raise KeyError('Post not found.')
        return json.loads(row['body'])

    def folder(self, p):
        raw = (p.get('folder') or '').strip()
        if not raw:
            return None
        path = Path(raw) if Path(raw).is_absolute() else self.root / raw
        try:
            path = path.resolve()
            path.relative_to(self.root.resolve())
        except (OSError, ValueError):
            return None
        return path if path.is_dir() else None

    def images(self, p):
        folder = self.folder(p)
        if not folder:
            return []
        files = [f for f in folder.iterdir() if f.is_file() and f.suffix.lower() in IMAGE_TYPES]
        return sorted(files, key=lambda f: [int(t) if t.isdigit() else t for t in re.split(r'(\d+)', f.name.lower())])

    def image(self, pid, index):
        files = self.images(self.get(pid))
        if not 0 <= index < len(files):
            raise KeyError('Slide not found.')
        return files[index], IMAGE_TYPES[files[index].suffix.lower()]

    # Saving ----------------------------------------------------------------
    def validate(self, p):
        for key, limit in TEXT.items():
            if not isinstance(p.get(key, ''), str) or len(p.get(key, '')) > limit:
                raise ValueError(f'{key.capitalize()} must be text, up to {limit:,} characters.')
        p['creator'] = p['creator'].strip()
        if not p['creator']:
            raise ValueError('Say whose post this is.')
        try:
            date.fromisoformat(p['date'])
        except (TypeError, ValueError):
            raise ValueError('Give the post date as YYYY-MM-DD.') from None
        for key in ('slides', 'likes', 'comments'):
            v = p.get(key)
            if v is not None and (type(v) is not int or v < 0):
                raise ValueError(f'{key.capitalize()} must be a whole number.')
        if not isinstance(p.get('new_thing', False), bool) or not isinstance(p.get('archived', False), bool):
            raise ValueError('New thing and archived must be true or false.')
        p['tags'] = clean_tags(p.get('tags') or {})

    def create(self, data):
        if not isinstance(data, dict) or set(data) - FIELDS - {'id', 'image_urls'}:
            raise ValueError('Unknown field for a creator post.')
        # The same post (by link, or by folder) is only kept once.
        for p in self.rows():
            if (data.get('url') and p.get('url') == data['url']) or (data.get('folder') and p.get('folder') == data['folder']):
                return p
        pid = data.get('id') or str(uuid.uuid4())
        uuid.UUID(pid)
        p = dict(id=pid, revision=1, creator=data.get('creator', ''), date=data.get('date', ''), url=data.get('url', ''),
                 hook=data.get('hook', ''), caption=data.get('caption', ''), slides=data.get('slides'),
                 likes=data.get('likes'), comments=data.get('comments'), folder=data.get('folder', ''),
                 tags=data.get('tags', {}), look=data.get('look', ''), new_thing=data.get('new_thing', False),
                 new_note=data.get('new_note', ''), notes=data.get('notes', ''), checked_at=data.get('checked_at', ''),
                 archived=False, created_at=now(), updated_at=now())
        self.validate(p)
        if data.get('image_urls'):
            p['folder'] = self.download(p, data['image_urls'])
            p['slides'] = p['slides'] or len(data['image_urls'])
        with self.store.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            c.execute('INSERT OR IGNORE INTO inspo_posts VALUES(?,?,?)', (pid, 1, json.dumps(p)))
        return p

    def save(self, pid, changes, revision):
        if not isinstance(changes, dict) or set(changes) - FIELDS:
            raise ValueError('Unknown field for a creator post.')
        if type(revision) is not int:
            raise ValueError('A saved revision is required.')
        with self.store.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            row = c.execute('SELECT revision,body FROM inspo_posts WHERE id=?', (pid,)).fetchone()
            if not row:
                raise KeyError('Post not found.')
            if row['revision'] != revision:
                raise Conflict('This post changed in another window. Reload it before saving again.')
            p = dict(json.loads(row['body']), **changes)
            self.validate(p)
            p.update(revision=revision + 1, updated_at=now())
            c.execute('UPDATE inspo_posts SET revision=?,body=? WHERE id=?', (p['revision'], json.dumps(p), pid))
        return p

    def download(self, p, urls):
        """Save a post's slide images into _Inspo/<Creator>-Carousels/<date>_<slug>/."""
        if not isinstance(urls, list) or not 1 <= len(urls) <= 20:
            raise ValueError('Give between 1 and 20 slide image links.')
        slug = re.sub(r'[^a-z0-9]+', '-', p['hook'].lower())[:50].strip('-') or 'post'
        folder = self.root / (re.sub(r'[^A-Za-z0-9]+', '-', p['creator']).strip('-') + '-Carousels') / f"{p['date']}_{slug}"
        for url in urls:
            parts = urllib.parse.urlsplit(str(url))
            if parts.scheme != 'https' or not (parts.hostname or '').endswith(IMAGE_HOSTS):
                raise ValueError('Slide images can only come from YouTube image links.')
        folder.mkdir(parents=True, exist_ok=True)
        for i, url in enumerate(urls, 1):
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=30) as r:
                data = r.read(15_000_000)
                kind = r.headers.get('Content-Type', '')
            ext = '.png' if 'png' in kind else '.webp' if 'webp' in kind else '.jpg'
            (folder / f'slide_{i:02d}{ext}').write_bytes(data)
        if p.get('caption'):
            (folder / 'caption.md').write_text(f"# {p['hook']}\n\n- Posted: {p['date']}\n- Link: {p.get('url', '')}\n\n## Caption\n\n{p['caption']}\n", encoding='utf-8')
        return str(folder.relative_to(self.root))

    def apply(self, updates):
        """Changes a team member asked for from chat. Tags merge into what is there."""
        done = []
        for u in updates or []:
            if not isinstance(u, dict):
                continue
            u = {k: v for k, v in u.items() if v is not None}
            pid = u.pop('id', '') or ''
            try:
                if pid:
                    current = self.get(pid)
                    if 'tags' in u:
                        u['tags'] = dict(current.get('tags') or {}, **u['tags'])
                    p = self.save(pid, {k: v for k, v in u.items() if k in FIELDS}, current['revision'])
                    done.append(dict(id=p['id'], title=p['hook'][:80], action='updated', kind='inspo'))
                else:
                    p = self.create({k: v for k, v in u.items() if k in FIELDS | {'image_urls'}})
                    done.append(dict(id=p['id'], title=p['hook'][:80], action='added', kind='inspo'))
            except (ValueError, KeyError, Conflict, OSError) as e:
                done.append(dict(id=pid, title=str(u.get('hook', ''))[:80], action='failed', kind='inspo', error=str(e).strip("'\"")))
        return done

    def import_folder(self, folder, creator, checked_at=''):
        """Bring in a collected folder that has an index.csv (date, hook, slides, likes, comments, folder, caption)."""
        base = Path(folder) if Path(folder).is_absolute() else self.root / folder
        base = base.resolve()
        base.relative_to(self.root.resolve())
        added = 0
        with open(base / 'index.csv', encoding='utf-8-sig', newline='') as f:
            for row in csv.DictReader(f):
                rel = str((base / row['folder']).relative_to(self.root))
                before = len(self.rows())
                likes = row.get('likes_approx') or ''
                self.create(dict(creator=creator, date=row['date'], hook=row.get('hook', '')[:1000],
                                 caption=(row.get('caption') or '')[:10000], folder=rel,
                                 slides=int(row['slides']) if (row.get('slides') or '').isdigit() else None,
                                 likes=int(float(likes)) if likes else likes_number(row.get('likes_shown')),
                                 comments=int(row['comments']) if (row.get('comments') or '').isdigit() else None,
                                 checked_at=checked_at))
                added += len(self.rows()) - before
        return dict(added=added)
