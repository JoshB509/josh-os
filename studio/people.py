"""Anouk's customer success tracker: one record per person she's looking after.

Each person sits on one pathway (a newsletter reply, a lead from a call, a client, or a
course buyer) at a stage on that pathway. The record keeps who they are and where they're
at, every touchpoint, anything Josh promised them, and the next check-in, so nobody drifts
by accident. Anouk keeps it up to date from her chat; Josh can edit it on the
Customer success page.
"""
import json
import re
import uuid
from datetime import date
from .store import Conflict, now

PATHWAYS = {
    'reply': ('Newsletter replies', ['New reply', 'Replied', 'Warming', 'Ready for an offer', 'Bought', 'Not now']),
    'lead': ('Leads from calls', ['Call done', 'Follow-up sent', 'Considering', 'Said yes', 'Said no']),
    'client': ('Clients', ['Onboarding', 'Active', 'Needs a check-in', 'Wrapping up', 'Alumni']),
    'buyer': ('Course buyers', ['Just bought', 'Getting started', 'Stalled', 'Progressing', 'Finished', 'Reviewed', 'Ascended']),
}
FIELDS = {'name', 'email', 'pathway', 'stage', 'source', 'summary', 'next_step', 'next_due', 'promises', 'tags', 'archived'}
EMAIL = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')
DIRECTIONS = ('in', 'out', 'note', 'draft')


def _text(v, limit, label):
    if v is None:
        return ''
    if not isinstance(v, str) or len(v) > limit:
        raise ValueError(f'{label} must be text, up to {limit:,} characters.')
    return v.strip()


def _day(v, label):
    v = _text(v, 10, label)
    if v:
        date.fromisoformat(v)
    return v


class People:
    def __init__(self, store):
        self.store = store
        with store.connect() as c:
            c.execute('CREATE TABLE IF NOT EXISTS people(id TEXT PRIMARY KEY,email TEXT NOT NULL,revision INTEGER NOT NULL,body TEXT NOT NULL)')

    def all(self):
        with self.store.connect() as c:
            items = [json.loads(r['body']) for r in c.execute('SELECT body FROM people ORDER BY rowid DESC')]
        return dict(items=items, pathways={k: dict(label=v[0], stages=v[1]) for k, v in PATHWAYS.items()})

    def get(self, pid):
        with self.store.connect() as c:
            row = c.execute('SELECT body FROM people WHERE id=?', (pid,)).fetchone()
        if not row:
            raise KeyError('Person not found.')
        return json.loads(row['body'])

    def find(self, email):
        email = (email or '').strip().lower()
        if not email:
            return None
        with self.store.connect() as c:
            row = c.execute('SELECT body FROM people WHERE email=?', (email,)).fetchone()
        return json.loads(row['body']) if row else None

    def validate(self, p):
        p['name'] = _text(p.get('name'), 120, 'Name')
        p['email'] = _text(p.get('email'), 200, 'Email').lower()
        if not p['name'] and not p['email']:
            raise ValueError('Give the person a name or an email address.')
        if p['email'] and not EMAIL.match(p['email']):
            raise ValueError(f"{p['email']} is not an email address.")
        if p.get('pathway') not in PATHWAYS:
            raise ValueError('Pathway must be one of ' + ', '.join(PATHWAYS) + '.')
        stages = PATHWAYS[p['pathway']][1]
        if not p.get('stage'):
            p['stage'] = stages[0]
        elif p['stage'] not in stages:
            raise ValueError(f"Stage must be one of: {', '.join(stages)}.")
        for k, limit in (('source', 300), ('summary', 4000), ('next_step', 600)):
            p[k] = _text(p.get(k), limit, k.replace('_', ' ').capitalize())
        p['next_due'] = _day(p.get('next_due'), 'Next check-in date')
        promises = p.get('promises') or []
        if not isinstance(promises, list) or len(promises) > 30:
            raise ValueError('Up to 30 promises per person.')
        p['promises'] = [dict(id=x.get('id') or str(uuid.uuid4()), text=_text(x.get('text'), 300, 'Promise'),
                              due=_day(x.get('due'), 'Promise date'), done=bool(x.get('done')))
                         for x in promises if isinstance(x, dict) and _text(x.get('text'), 300, 'Promise')]
        tags = p.get('tags') or []
        if not isinstance(tags, list) or len(tags) > 12:
            raise ValueError('Up to 12 tags.')
        p['tags'] = [t for t in (_text(t, 40, 'Tag') for t in tags) if t]
        p['archived'] = bool(p.get('archived'))

    def _entry(self, e):
        if not isinstance(e, dict):
            raise ValueError('Invalid history entry.')
        direction = e.get('direction') if e.get('direction') in DIRECTIONS else 'note'
        return dict(id=e.get('id') or str(uuid.uuid4()), at=_day(e.get('at'), 'History date') or date.today().isoformat(),
                    direction=direction, channel=_text(e.get('channel'), 40, 'Channel') or 'email',
                    summary=_text(e.get('summary'), 2000, 'Summary'), link=_text(e.get('link'), 500, 'Link'))

    def create(self, data, history=()):
        if not isinstance(data, dict) or set(data) - FIELDS - {'id'}:
            raise ValueError('Unknown field.')
        pid = data.get('id') or str(uuid.uuid4())
        uuid.UUID(pid)
        p = dict({k: data.get(k) for k in FIELDS}, id=pid, revision=1, history=[self._entry(e) for e in history][:200],
                 created_at=now(), updated_at=now())
        self.validate(p)
        with self.store.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            if p['email'] and c.execute('SELECT 1 FROM people WHERE email=? AND id<>?', (p['email'], pid)).fetchone():
                raise Conflict(f"{p['email']} is already in the tracker.")
            row = c.execute('SELECT body FROM people WHERE id=?', (pid,)).fetchone()
            if row:
                return json.loads(row['body'])
            c.execute('INSERT INTO people VALUES(?,?,?,?)', (pid, p['email'], 1, json.dumps(p)))
        return p

    def save(self, pid, changes, revision, history=()):
        if not isinstance(changes, dict) or set(changes) - FIELDS:
            raise ValueError('Unknown field.')
        if type(revision) is not int:
            raise ValueError('A saved revision is required.')
        with self.store.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            row = c.execute('SELECT revision,body FROM people WHERE id=?', (pid,)).fetchone()
            if not row:
                raise KeyError('Person not found.')
            if row['revision'] != revision:
                raise Conflict('This person changed in another window. Reload before saving again.')
            p = dict(json.loads(row['body']), **changes)
            if 'pathway' in changes and 'stage' not in changes:
                p['stage'] = ''
            self.validate(p)
            p['history'] = (p.get('history') or []) + [self._entry(e) for e in history]
            p['history'] = p['history'][-200:]
            if p['email'] and c.execute('SELECT 1 FROM people WHERE email=? AND id<>?', (p['email'], pid)).fetchone():
                raise Conflict(f"{p['email']} is already in the tracker.")
            p.update(revision=revision + 1, updated_at=now())
            c.execute('UPDATE people SET email=?,revision=?,body=? WHERE id=?', (p['email'], p['revision'], json.dumps(p), pid))
        return p

    def apply(self, updates):
        """Anouk's saves from chat: add people, move them on, log touchpoints and promises."""
        done = []
        for u in (updates or [])[:25]:
            if not isinstance(u, dict):
                continue
            history = u.get('add_history') if isinstance(u.get('add_history'), list) else []
            fields = {k: v for k, v in u.items() if k in FIELDS}
            try:
                current = self.get(u['id']) if u.get('id') else self.find(fields.get('email'))
                if current:
                    if 'promises' in fields:
                        # Merge by text so a save that only ticks one promise off doesn't drop the others.
                        merged = {x['text'].lower(): x for x in current.get('promises', [])}
                        for x in fields['promises'] or []:
                            if isinstance(x, dict) and x.get('text'):
                                merged[x['text'].strip().lower()] = dict(merged.get(x['text'].strip().lower(), {}), **x)
                        fields['promises'] = list(merged.values())
                    p = self.save(current['id'], fields, current['revision'], history)
                    done.append(dict(id=p['id'], title=p['name'] or p['email'], action='updated', kind='person'))
                else:
                    p = self.create(fields, history)
                    done.append(dict(id=p['id'], title=p['name'] or p['email'], action='added', kind='person'))
            except Exception as e:  # noqa: BLE001 - report every failure back to Josh in the chat
                done.append(dict(id=u.get('id', ''), title=u.get('name') or u.get('email') or '', action='failed', kind='person',
                                 error=str(e).strip("'\"")[:200]))
        return done

    def describe(self, limit=120):
        """Compact list for Anouk's snapshot."""
        today = date.today().isoformat()
        rows = [p for p in self.all()['items'] if not p.get('archived')][:limit]
        lines = []
        for p in sorted(rows, key=lambda p: (p.get('next_due') or '9999')):
            open_promises = [x for x in p.get('promises', []) if not x['done']]
            last = (p.get('history') or [{}])[-1]
            lines.append(f"- {p['id']} | {p['name'] or '?'} <{p['email'] or 'no email'}> | {p['pathway']} | {p['stage']}"
                         + f" | next: {p.get('next_step') or 'none'}{' by ' + p['next_due'] if p.get('next_due') else ''}"
                         + (' (OVERDUE)' if p.get('next_due') and p['next_due'] < today else '')
                         + (f" | promised: {'; '.join(x['text'] + (' by ' + x['due'] if x['due'] else '') for x in open_promises)}" if open_promises else '')
                         + (f" | last: {last.get('at')} {last.get('direction')} {(last.get('summary') or '')[:120]}" if last else '')
                         + (f" | {p['summary'][:200]}" if p.get('summary') else ''))
        return lines
