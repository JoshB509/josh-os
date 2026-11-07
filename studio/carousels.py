"""Carousel tracker for the YouTube and social workflow.

Each carousel moves from Idea to Posted. Slides are kept as plain text, one block per
slide separated by a line with three dashes, so they are easy to write on a phone and
easy for an agent to draft. Posting happens outside the app; the tracker records it.
"""
import json
import uuid
from datetime import date
from .store import Conflict, now

STAGES = ['Idea', 'Drafting', 'Ready for review', 'Approved', 'Scheduled', 'Posted']
PLATFORMS = ['Instagram', 'LinkedIn', 'TikTok', 'YouTube Community', 'Other']
FIELDS = {'title', 'stage', 'platforms', 'post_date', 'hook', 'slides', 'caption', 'notes', 'link',
          'project_id', 'owner', 'archived'}
LIMITS = {'title': 240, 'hook': 1000, 'slides': 30000, 'caption': 5000, 'notes': 20000, 'link': 1000,
          'owner': 40}


class Carousels:
    def __init__(self, store):
        self.store = store
        with store.connect() as c:
            c.execute('CREATE TABLE IF NOT EXISTS carousels(id TEXT PRIMARY KEY,revision INTEGER NOT NULL,body TEXT NOT NULL)')

    def all(self):
        with self.store.connect() as c:
            items = [json.loads(r['body']) for r in c.execute('SELECT body FROM carousels ORDER BY rowid DESC')]
        return dict(items=items, stages=STAGES, platforms=PLATFORMS)

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

    def create(self, data):
        if not isinstance(data, dict) or set(data) - FIELDS - {'id'}:
            raise ValueError('Unknown carousel field.')
        cid = data.get('id') or str(uuid.uuid4())
        uuid.UUID(cid)
        item = dict(id=cid, revision=1, title=data.get('title', ''), stage=data.get('stage', 'Idea'),
                    platforms=data.get('platforms', ['Instagram', 'LinkedIn']), post_date=data.get('post_date', ''),
                    hook=data.get('hook', ''), slides=data.get('slides', ''), caption=data.get('caption', ''),
                    notes=data.get('notes', ''), link=data.get('link', ''), project_id=data.get('project_id', ''),
                    owner=data.get('owner', 'sam'), archived=data.get('archived', False),
                    created_at=now(), updated_at=now(), posted_at=None)
        self.validate(item)
        if item['stage'] == 'Posted':
            item['posted_at'] = now()
        with self.store.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            row = c.execute('SELECT body FROM carousels WHERE id=?', (cid,)).fetchone()
            if row:
                return json.loads(row['body'])
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
            previous = json.loads(row['body'])
            item = dict(previous, **changes)
            self.validate(item)
            if all(item[k] == previous[k] for k in FIELDS):
                return previous
            item.update(revision=revision + 1, updated_at=now())
            item['posted_at'] = (previous.get('posted_at') or now()) if item['stage'] == 'Posted' else None
            c.execute('UPDATE carousels SET revision=?,body=? WHERE id=?', (item['revision'], json.dumps(item), cid))
        return item
