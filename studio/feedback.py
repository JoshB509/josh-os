"""What Josh changes in the team's work, so they can learn from it.

When Josh edits a carousel, email or script that a team member wrote, the change is kept
here: the field, what it said before and what it says now. Edits made while he types are
folded into one change per field. The team member sees the open changes in their chat,
asks about the ones they can't work out, and saves what they learn as a lesson. Once
they've raised a change it is marked so it isn't asked about twice.
"""
import difflib
import json
import uuid
from datetime import datetime, timedelta, timezone
from .store import now

# Which fields count as the team's writing, and who wrote them by default.
TRACK = {
    'carousel': ('title', 'hook', 'slides', 'caption'),
    'email': ('subject', 'preview', 'body'),
    'project': ('script',),
}
OWNER = {'email': 'laia', 'project': 'sam'}
WORKFLOW = {'carousel': 'carousels', 'email': 'email', 'project': 'youtube'}
FOLD_MINUTES = 45


def summarise(before, after, limit=1500):
    """A short view of what changed: the whole thing when short, the changed lines when long."""
    before, after = str(before or ''), str(after or '')
    if len(before) + len(after) <= 600:
        return f'Before: {before}\nAfter: {after}'
    a = before.replace('. ', '.\n').splitlines()
    b = after.replace('. ', '.\n').splitlines()
    out = []
    for line in difflib.unified_diff(a, b, lineterm='', n=1):
        if line.startswith(('---', '+++', '@@')):
            continue
        out.append(line)
    text = '\n'.join(out)
    return text[:limit] + ('\n…' if len(text) > limit else '')


class Feedback:
    def __init__(self, store):
        self.store = store
        with store.connect() as c:
            c.execute('CREATE TABLE IF NOT EXISTS edits(id TEXT PRIMARY KEY, body TEXT NOT NULL)')

    def rows(self):
        with self.store.connect() as c:
            return [json.loads(r['body']) for r in c.execute('SELECT body FROM edits')]

    def _put(self, c, e):
        c.execute('INSERT OR REPLACE INTO edits VALUES(?,?)', (e['id'], json.dumps(e, ensure_ascii=False)))

    def record(self, kind, before, after):
        """Compare a record before and after Josh saved it, and keep what changed in the team's writing."""
        if kind not in TRACK or not isinstance(before, dict) or not isinstance(after, dict):
            return []
        agent = before.get('owner') or OWNER.get(kind)
        if not agent or agent == 'josh':
            return []
        title = after.get('title') or after.get('subject') or after.get('name') or ''
        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=FOLD_MINUTES)).isoformat()
        changed = []
        with self.store.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            open_ = {(e['kind'], e['item_id'], e['field']): e for e in
                     (json.loads(r['body']) for r in c.execute('SELECT body FROM edits'))
                     if e['status'] == 'new' and e['updated_at'] >= cutoff}
            for field in TRACK[kind]:
                old, new = before.get(field), after.get(field)
                if old == new or not isinstance(new, str):
                    continue
                e = open_.get((kind, after['id'], field))
                if e:
                    e.update(after=new, title=title, updated_at=now())
                    if (e['before'] or '') == new:
                        c.execute('DELETE FROM edits WHERE id=?', (e['id'],))
                        continue
                else:
                    e = dict(id=str(uuid.uuid4()), kind=kind, item_id=after['id'], title=title, field=field,
                             agent=agent, before=old or '', after=new, status='new', created_at=now(), updated_at=now())
                self._put(c, e)
                changed.append(e)
        return changed

    def pending(self, agent, limit=8):
        items = [e for e in self.rows() if e['agent'] == agent and e['status'] == 'new']
        items.sort(key=lambda e: e['updated_at'], reverse=True)
        return items[:limit]

    def mark(self, ids, status='raised'):
        ids = set(ids or [])
        if not ids:
            return
        with self.store.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            for r in c.execute('SELECT body FROM edits').fetchall():
                e = json.loads(r['body'])
                if e['id'] in ids:
                    e.update(status=status, updated_at=now())
                    self._put(c, e)

    def describe(self, edits):
        lines = []
        for e in edits:
            lines.append(f"- edit {e['id']} | {e['kind']} \"{e['title'][:80]}\" | {e['field']} | {e['updated_at'][:16]}\n"
                         + '\n'.join('    ' + l for l in summarise(e['before'], e['after']).splitlines()))
        return '\n'.join(lines)
