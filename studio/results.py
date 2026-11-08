"""How each video did, and what it taught us.

One record per video: where it sits in the funnel, the numbers that matter (views, new
subscribers, email signups and customers, so the views-to-customers chain is visible), every
round of thumbnail testing with its screenshot and winner, and the retention graph with
the dips lined up against the script. Sam fills it in from YouTube Studio and Linklr, and
Josh can add screenshots and notes himself.
"""
import json
import uuid
from datetime import date
from .store import Conflict, now

FUNNEL = ['top', 'middle', 'bottom']
METRICS = ('views', 'impressions', 'ctr', 'avg_view_duration', 'avg_percent_viewed', 'subscribers', 'emails', 'customers', 'revenue')
CHANGES = ['text', 'visual', 'font', 'colour', 'face', 'layout', 'other']
FIELDS = {'funnel', 'metrics', 'tests', 'retention', 'review', 'lessons'}


def _text(v, limit, label):
    if v is None:
        return ''
    if not isinstance(v, str) or len(v) > limit:
        raise ValueError(f'{label} must be text, up to {limit:,} characters.')
    return v.strip()


def _num(v, label):
    if v in (None, ''):
        return None
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise ValueError(f'{label} must be a number.')
    return v


class VideoResults:
    def __init__(self, store):
        self.store = store
        with store.connect() as c:
            c.execute('CREATE TABLE IF NOT EXISTS video_results(project_id TEXT PRIMARY KEY,revision INTEGER NOT NULL,body TEXT NOT NULL)')

    def empty(self, pid):
        return dict(project_id=pid, revision=0, funnel='', metrics={}, tests=[], retention={}, review='', lessons=[], updated_at=None)

    def get(self, pid):
        self.store.get(pid)
        with self.store.connect() as c:
            row = c.execute('SELECT body FROM video_results WHERE project_id=?', (pid,)).fetchone()
        return json.loads(row['body']) if row else self.empty(pid)

    def all(self):
        with self.store.connect() as c:
            return {r['project_id']: json.loads(r['body']) for r in c.execute('SELECT project_id,body FROM video_results')}

    def _asset(self, aid):
        if not aid:
            return ''
        if not isinstance(aid, str) or len(aid) != 64:
            raise ValueError('Upload the screenshot again.')
        self.store.asset(aid)
        return aid

    def validate(self, r):
        r['funnel'] = r.get('funnel') or ''
        if r['funnel'] and r['funnel'] not in FUNNEL:
            raise ValueError('Funnel stage must be top, middle or bottom.')
        m = r.get('metrics') or {}
        if not isinstance(m, dict) or set(m) - set(METRICS) - {'as_of'}:
            raise ValueError('Unknown metric.')
        r['metrics'] = {k: _num(m.get(k), k.replace('_', ' ')) for k in METRICS if m.get(k) not in (None, '')}
        if m.get('as_of'):
            date.fromisoformat(m['as_of'])
            r['metrics']['as_of'] = m['as_of']
        tests = r.get('tests') or []
        if not isinstance(tests, list) or len(tests) > 12:
            raise ValueError('Up to 12 thumbnail test rounds.')
        clean = []
        for i, t in enumerate(tests):
            if not isinstance(t, dict):
                raise ValueError('Invalid test round.')
            variants = t.get('variants') or []
            if not isinstance(variants, list) or len(variants) > 3:
                raise ValueError('YouTube tests up to 3 thumbnails per round.')
            vs = [dict(label=_text(v.get('label') or chr(65 + j), 20, 'Label'), text=_text(v.get('text'), 200, 'Thumbnail text'),
                       look=_text(v.get('look'), 300, 'Look'), share=_num(v.get('share'), 'Watch time share'),
                       asset=self._asset(v.get('asset'))) for j, v in enumerate(variants) if isinstance(v, dict)]
            changed = t.get('changed') or ''
            if changed and changed not in CHANGES:
                raise ValueError('What changed must be one of ' + ', '.join(CHANGES) + '.')
            winner = _text(t.get('winner'), 20, 'Winner')
            if winner and winner not in {v['label'] for v in vs}:
                raise ValueError('The winner must be one of the thumbnails in the round.')
            clean.append(dict(id=t.get('id') or str(uuid.uuid4()), round=i + 1, changed=changed,
                              hypothesis=_text(t.get('hypothesis'), 1000, 'Hypothesis'),
                              screenshot=self._asset(t.get('screenshot')), variants=vs, winner=winner,
                              status='done' if winner else (t.get('status') if t.get('status') in ('running', 'done') else 'running'),
                              why=_text(t.get('why'), 3000, 'Why it won')))
        r['tests'] = clean
        ret = r.get('retention') or {}
        if not isinstance(ret, dict):
            raise ValueError('Invalid retention notes.')
        dips = ret.get('dips') or []
        if not isinstance(dips, list) or len(dips) > 20:
            raise ValueError('Up to 20 retention moments.')
        r['retention'] = dict(screenshot=self._asset(ret.get('screenshot')), notes=_text(ret.get('notes'), 3000, 'Notes'),
                              summary=_text(ret.get('summary'), 3000, 'Summary'),
                              dips=[dict(at=_text(d.get('at'), 20, 'Time'), kind=d.get('kind') if d.get('kind') in ('drop', 'hold', 'spike') else 'drop',
                                         script=_text(d.get('script'), 600, 'Script line'), why=_text(d.get('why'), 1000, 'Why'))
                                    for d in dips if isinstance(d, dict)])
        r['review'] = _text(r.get('review'), 6000, 'Review')
        lessons = r.get('lessons') or []
        if not isinstance(lessons, list) or len(lessons) > 30:
            raise ValueError('Up to 30 lessons per video.')
        r['lessons'] = [x for x in (_text(l, 600, 'Lesson') for l in lessons) if x]

    def save(self, pid, changes, revision):
        if not isinstance(changes, dict) or set(changes) - FIELDS:
            raise ValueError('Unknown results field.')
        if type(revision) is not int:
            raise ValueError('A saved revision is required.')
        with self.store.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            row = c.execute('SELECT revision,body FROM video_results WHERE project_id=?', (pid,)).fetchone()
            previous = json.loads(row['body']) if row else self.empty(pid)
            if previous['revision'] != revision:
                raise Conflict('These results changed in another window. Reload before saving again.')
            r = dict(previous, **changes)
            self.validate(r)
            r.update(revision=revision + 1, updated_at=now())
            c.execute('INSERT OR REPLACE INTO video_results VALUES(?,?,?)', (pid, r['revision'], json.dumps(r)))
        return r

    def apply(self, updates):
        """Sam's saves from chat. Each update names a video and the parts he is filling in."""
        done = []
        for u in (updates or [])[:10]:
            if not isinstance(u, dict):
                continue
            pid = u.get('project_id', '')
            try:
                current = self.get(pid)
                changes = {k: v for k, v in u.items() if k in FIELDS}
                if 'metrics' in changes:
                    changes['metrics'] = dict(current.get('metrics') or {}, **(changes['metrics'] or {}))
                if 'retention' in changes:
                    changes['retention'] = dict(current.get('retention') or {}, **(changes['retention'] or {}))
                if 'lessons' in changes:
                    changes['lessons'] = list(dict.fromkeys((current.get('lessons') or []) + list(changes['lessons'] or [])))
                saved = self.save(pid, changes, current['revision'])
                done.append(dict(id=pid, title=self.store.get(pid)['name'], action='results', kind='video'))
            except Exception as e:  # noqa: BLE001 - report every failure back to Josh in the chat
                done.append(dict(id=pid, title='', action='failed', kind='video', error=str(e).strip("'\"")[:200]))
        return done
