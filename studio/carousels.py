"""Carousel tracker for the YouTube community posts.

Each carousel moves from Idea to Posted. Slides are kept as plain text, one block per
slide separated by a line with three dashes, so they are easy to write on a phone and
easy for an agent to draft. When the slides are rendered, "folder" points at the images
on the E drive so the app can show them.

Every carousel carries a short set of tags, each from a fixed list so posts can be
grouped and compared, and results at 1 day and 7 days. Sam fills both in.
"""
import json
import re
import uuid
from datetime import date
from pathlib import Path
from .store import Conflict, now

STAGES = ['Idea', 'Drafting', 'Ready for review', 'Approved', 'Scheduled', 'Posted']
PLATFORMS = ['YouTube Community', 'Instagram', 'LinkedIn', 'TikTok', 'Other']
FIELDS = {'title', 'stage', 'platforms', 'post_date', 'hook', 'slides', 'caption', 'notes', 'link',
          'project_id', 'owner', 'archived', 'folder', 'tags', 'results'}
LIMITS = {'title': 240, 'hook': 1000, 'slides': 30000, 'caption': 5000, 'notes': 20000, 'link': 1000,
          'owner': 40, 'folder': 500}

# Tags. Lists are fixed so thirty posts a month can still be grouped. Text tags are free.
TAG_OPTIONS = {
    'theme': ['Promotion', 'Meetings', 'Decks and slides', 'Credit for your work', 'Pushback',
              'Executive communication', 'Performance reviews', 'Career strategy', 'Other'],
    'hook_type': ['Number list', 'Question', 'Bold claim', 'Mistake', 'How to', 'Story', 'Contrast', 'Quote', 'Other'],
    'angle': ['Pain', 'Promise'],
    'format': ['Listicle', 'Phrases', 'Framework', 'Steps', 'Mistakes', 'Story', 'Before and after', 'Other'],
    'style': ['Paper', 'Photo BG1', 'Photo BG2', 'Other'],
    'cta_type': ['None', 'Lead magnet', 'Course', 'Video', 'Comment', 'Other'],
    'cta_place': ['None', 'Caption', 'Last slide', 'QR code', 'Caption and last slide'],
    'caption_style': ['Question', 'Summary', 'Teaser', 'Instruction', 'Story', 'Other'],
    'idea_source': ['Our video', 'Ali Abdaal', 'Alex Hormozi', 'A comment', 'Josh', 'Sam', 'Other'],
}
TAG_TEXT = {'topic': 200, 'hook_text': 500, 'lead_magnet': 200, 'post_time': 5, 'testing': 500, 'tracked_link': 500}
TAG_FLAGS = {'hook_number', 'video_same_day'}
TAG_NUMBERS = {'slide_count'}
RESULT_WINDOWS = ('d1', 'd7')
RESULT_NUMBERS = ('views', 'likes', 'comments', 'clicks', 'downloads', 'new_people', 'course_clicks', 'sale_value')
SLIDE_ROOT = Path(r'E:\CareerOS\03_Community_Posts')
IMAGE_TYPES = {'.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.webp': 'image/webp'}


def clean_tags(tags):
    if not isinstance(tags, dict):
        raise ValueError('Tags must be a set of named values.')
    out = {}
    for key, value in tags.items():
        if value in (None, ''):
            continue
        if key in TAG_OPTIONS:
            if value not in TAG_OPTIONS[key]:
                raise ValueError(f"{key.replace('_', ' ').capitalize()} must be one of: {', '.join(TAG_OPTIONS[key])}.")
        elif key in TAG_TEXT:
            if not isinstance(value, str) or len(value) > TAG_TEXT[key]:
                raise ValueError(f"{key.replace('_', ' ').capitalize()} must be text, up to {TAG_TEXT[key]} characters.")
            if key == 'post_time' and not re.fullmatch(r'\d{2}:\d{2}', value):
                raise ValueError('Post time must look like 21:00.')
        elif key in TAG_FLAGS:
            if not isinstance(value, bool):
                raise ValueError(f"{key.replace('_', ' ').capitalize()} must be true or false.")
        elif key in TAG_NUMBERS:
            if type(value) is not int or not 0 <= value <= 50:
                raise ValueError('Slide count must be a whole number.')
        else:
            raise ValueError(f'Unknown tag: {key}.')
        out[key] = value
    return out


def clean_results(results):
    if not isinstance(results, dict) or set(results) - set(RESULT_WINDOWS):
        raise ValueError('Results are kept for d1 and d7 only.')
    out = {}
    for window, numbers in results.items():
        if numbers in (None, {}):
            continue
        if not isinstance(numbers, dict):
            raise ValueError('Each results window must be a set of numbers.')
        row = {}
        for key, value in numbers.items():
            if key == 'checked_at':
                if not isinstance(value, str) or len(value) > 40:
                    raise ValueError('checked_at must be a date or time.')
                row[key] = value
                continue
            if key not in RESULT_NUMBERS:
                raise ValueError(f'Unknown result: {key}.')
            if value in (None, ''):
                continue
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
                raise ValueError(f"{key.replace('_', ' ').capitalize()} must be a number.")
            row[key] = value
        if row:
            out[window] = row
    return out


class Carousels:
    def __init__(self, store):
        self.store = store
        with store.connect() as c:
            c.execute('CREATE TABLE IF NOT EXISTS carousels(id TEXT PRIMARY KEY,revision INTEGER NOT NULL,body TEXT NOT NULL)')

    def all(self):
        with self.store.connect() as c:
            items = [self.defaults(json.loads(r['body'])) for r in c.execute('SELECT body FROM carousels ORDER BY rowid DESC')]
        for item in items:
            item['images'] = len(self.images(item))
        return dict(items=items, stages=STAGES, platforms=PLATFORMS, tag_options=TAG_OPTIONS,
                    tag_text=sorted(TAG_TEXT), tag_flags=sorted(TAG_FLAGS), result_numbers=list(RESULT_NUMBERS))

    @staticmethod
    def defaults(item):
        item.setdefault('folder', '')
        item.setdefault('tags', {})
        item.setdefault('results', {})
        return item

    def get(self, cid):
        with self.store.connect() as c:
            row = c.execute('SELECT body FROM carousels WHERE id=?', (cid,)).fetchone()
        if not row:
            raise KeyError('Carousel not found.')
        return self.defaults(json.loads(row['body']))

    # Slide images ----------------------------------------------------------
    def folder(self, item):
        raw = (item.get('folder') or '').strip()
        if not raw:
            return None
        path = Path(raw)
        if not path.is_absolute():
            path = SLIDE_ROOT / path
        try:
            path = path.resolve()
            path.relative_to(SLIDE_ROOT.resolve())
        except (OSError, ValueError):
            return None
        return path if path.is_dir() else None

    def images(self, item):
        folder = self.folder(item)
        if not folder:
            return []
        files = [f for f in folder.iterdir() if f.is_file() and f.suffix.lower() in IMAGE_TYPES]
        return sorted(files, key=lambda f: [int(t) if t.isdigit() else t for t in re.split(r'(\d+)', f.name.lower())])

    def image(self, cid, index):
        files = self.images(self.get(cid))
        if not 0 <= index < len(files):
            raise KeyError('Slide not found.')
        return files[index], IMAGE_TYPES[files[index].suffix.lower()]

    # Saving ----------------------------------------------------------------
    def validate(self, item):
        for key, limit in LIMITS.items():
            if not isinstance(item[key], str) or len(item[key]) > limit:
                raise ValueError(f'{key.capitalize()} must be text, up to {limit:,} characters.')
        item['title'] = item['title'].strip()
        if not item['title']:
            raise ValueError('Give the carousel a working title.')
        if item['stage'] not in STAGES:
            raise ValueError('Choose a valid stage.')
        if not isinstance(item['platforms'], list) or set(item['platforms']) - set(PLATFORMS):
            raise ValueError('Choose platforms from the list.')
        if item['post_date']:
            date.fromisoformat(item['post_date'])
            if len(item['post_date']) != 10:
                raise ValueError('Use a full date.')
        if item['link'] and not item['link'].startswith(('https://', 'http://')):
            raise ValueError('Paste the full link to the post, starting with https://')
        if not isinstance(item['archived'], bool):
            raise ValueError('Archived must be true or false.')
        if item['project_id']:
            self.store.get(item['project_id'])
        item['tags'] = clean_tags(item['tags'])
        item['results'] = clean_results(item['results'])

    def create(self, data):
        if not isinstance(data, dict) or set(data) - FIELDS - {'id'}:
            raise ValueError('Unknown carousel field.')
        cid = data.get('id') or str(uuid.uuid4())
        uuid.UUID(cid)
        item = dict(id=cid, revision=1, title=data.get('title', ''), stage=data.get('stage', 'Idea'),
                    platforms=data.get('platforms', ['YouTube Community']), post_date=data.get('post_date', ''),
                    hook=data.get('hook', ''), slides=data.get('slides', ''), caption=data.get('caption', ''),
                    notes=data.get('notes', ''), link=data.get('link', ''), project_id=data.get('project_id', ''),
                    owner=data.get('owner', 'sam'), archived=data.get('archived', False),
                    folder=data.get('folder', ''), tags=data.get('tags', {}), results=data.get('results', {}),
                    created_at=now(), updated_at=now(), posted_at=None)
        self.validate(item)
        if item['stage'] == 'Posted':
            item['posted_at'] = now()
        with self.store.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            row = c.execute('SELECT body FROM carousels WHERE id=?', (cid,)).fetchone()
            if row:
                return self.defaults(json.loads(row['body']))
            c.execute('INSERT INTO carousels VALUES(?,?,?)', (cid, 1, json.dumps(item)))
        return item

    def save(self, cid, changes, revision):
        if not isinstance(changes, dict) or set(changes) - FIELDS:
            raise ValueError('Unknown carousel field.')
        if type(revision) is not int:
            raise ValueError('A saved revision is required.')
        with self.store.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            row = c.execute('SELECT revision,body FROM carousels WHERE id=?', (cid,)).fetchone()
            if not row:
                raise KeyError('Carousel not found.')
            if row['revision'] != revision:
                raise Conflict('This carousel changed in another window. Reload it before saving again.')
            previous = self.defaults(json.loads(row['body']))
            item = dict(previous, **changes)
            self.validate(item)
            if all(item[k] == previous[k] for k in FIELDS):
                return previous
            item.update(revision=revision + 1, updated_at=now())
            item['posted_at'] = (previous.get('posted_at') or now()) if item['stage'] == 'Posted' else None
            c.execute('UPDATE carousels SET revision=?,body=? WHERE id=?', (item['revision'], json.dumps(item), cid))
        return item

    def apply(self, owner, updates):
        """Changes a team member asked for from chat. Tags and results merge into what is there."""
        done = []
        for u in updates or []:
            if not isinstance(u, dict):
                continue
            u = {k: v for k, v in u.items() if v is not None}
            cid = u.pop('id', '') or ''
            try:
                if cid:
                    current = self.get(cid)
                    if 'tags' in u:
                        u['tags'] = dict(current['tags'], **u['tags'])
                    if 'results' in u:
                        merged = dict(current['results'])
                        for w, nums in u['results'].items():
                            merged[w] = dict(merged.get(w, {}), **(nums or {}))
                        u['results'] = merged
                    item = self.save(cid, {k: v for k, v in u.items() if k in FIELDS}, current['revision'])
                    done.append(dict(id=item['id'], title=item['title'], action='updated'))
                else:
                    u.setdefault('owner', owner)
                    item = self.create({k: v for k, v in u.items() if k in FIELDS})
                    done.append(dict(id=item['id'], title=item['title'], action='added'))
            except (ValueError, KeyError, Conflict) as e:
                done.append(dict(id=cid, title=u.get('title', ''), action='failed', error=str(e).strip("'\"")))
        return done
