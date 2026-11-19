"""Sam's carousel reviews on Discord.

A review is one Discord message per carousel showing one slide at a time, with Previous and Next buttons, the slide
position, and buttons to leave feedback on the slide on screen or on the whole carousel. The state (which slide is
showing, the message it lives in, every piece of feedback) is kept in Josh OS's database, so the buttons keep
working after Josh OS restarts or the bot reconnects. Only the owners listed in Data/discord.json can use the
buttons. Feedback is saved to the carousel's notes, where Sam reads it, except on a review marked as a test.

Tariq's shot shortlists use the same viewer (kind "shots", built by studio/shot_review.py): one finished picture
per shot, the shot list's script for it with each press's words marked, and feedback on the shot or the whole list.
Shot feedback goes to the video's animation notes, where Tariq reads it, except on a test.

This module has no Discord code, so it can be tested on its own. discord_bridge.py draws the message from view().

A one-off review can be requested by dropping a JSON file in Data/discord/review-requests/. The bridge sends each
request once, to the bot's own configured private channel, and writes the outcome next to it. It never resends a
request, including when Discord's answer was unclear.
"""
import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .store import Conflict, now

PREFIX = 'crv'
ACTIONS = ('prev', 'next', 'fb', 'fball')
FEEDBACK_LIMIT = 1000
TYPE_NAMES = {'pip': 'picture in picture', 'half': 'half and half', 'full': 'full screen'}
DESCRIPTION_LIMIT = 3800  # Discord allows 4096 characters in an embed description


def custom_id(review_id, action):
    return f'{PREFIX}:{review_id}:{action}'


def parse(cid):
    """('review id', 'action') for one of our buttons, else None."""
    m = re.fullmatch(PREFIX + r':([0-9a-f-]{36}):(' + '|'.join(ACTIONS) + ')', str(cid or ''))
    return (m.group(1), m.group(2)) if m else None


class Reviews:
    def __init__(self, store, carousels, shot_notes=None, card_root=None):
        self.store, self.carousels = store, carousels
        self.card_root = Path(card_root) if card_root else Path(store.root) / 'discord' / 'cards'
        self.shot_notes = shot_notes  # (project id, note) -> saves to the video's animation notes for Tariq
        with store.connect() as c:
            c.execute('CREATE TABLE IF NOT EXISTS discord_reviews(id TEXT PRIMARY KEY,carousel_id TEXT NOT NULL,body TEXT NOT NULL)')

    def create(self, carousel_id, agent_id='sam', test=False):
        item = self.carousels.get(carousel_id)
        slides = [str(p) for p in self.carousels.images(item)]
        if not slides:
            raise ValueError(f"{item['title']} has no slide images yet, so there's nothing to review.")
        r = dict(id=str(uuid.uuid4()), carousel_id=carousel_id, title=item['title'], agent_id=agent_id, slides=slides[:30],
                 position=0, test=bool(test), channel_id='', message_id='', jump_url='', feedback=[], created_at=now(), updated_at=now())
        with self.store.connect() as c:
            c.execute('INSERT INTO discord_reviews VALUES(?,?,?)', (r['id'], carousel_id, json.dumps(r)))
        return r

    def create_shots(self, shortlist, agent_id='tariq', test=False, cards=True):
        """A review of Tariq's shot shortlist (studio/shot_review.build), pinned to its exact source version."""
        items = shortlist['items'][:60]
        r = dict(id=str(uuid.uuid4()), kind='shots', carousel_id='', project_id=shortlist['source']['project_id'], title=shortlist['name'],
                 agent_id=agent_id, source=shortlist['source'], items=items, slides=[i['picture'] for i in items], position=0,
                 test=bool(test), cards=bool(cards), channel_id='', message_id='', jump_url='', feedback=[], created_at=now(), updated_at=now())
        with self.store.connect() as c:
            c.execute('INSERT INTO discord_reviews VALUES(?,?,?)', (r['id'], 'shots:' + r['project_id'], json.dumps(r)))
        return r

    def recent(self, project_id, source, minutes=10):
        """A shortlist of this exact version already sent in the last few minutes, so a repeated ask doesn't send it twice."""
        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()
        with self.store.connect() as c:
            rows = [json.loads(x['body']) for x in c.execute('SELECT body FROM discord_reviews WHERE carousel_id=?', ('shots:' + project_id,))]
        key = lambda src: (src.get('slides_hash'), src.get('shot_list_hash'))
        hits = [r for r in rows if r.get('message_id') and r['created_at'] >= cutoff and key(r.get('source') or {}) == key(source)]
        return max(hits, key=lambda r: r['created_at']) if hits else None

    def get(self, rid):
        with self.store.connect() as c:
            row = c.execute('SELECT body FROM discord_reviews WHERE id=?', (rid,)).fetchone()
        if not row:
            raise KeyError('That review is no longer in Josh OS.')
        return json.loads(row['body'])

    def save(self, r):
        r['updated_at'] = now()
        with self.store.connect() as c:
            c.execute('UPDATE discord_reviews SET body=? WHERE id=?', (json.dumps(r), r['id']))
        return r

    def sent(self, rid, channel_id, message_id, jump_url=''):
        r = self.get(rid)
        r.update(channel_id=str(channel_id), message_id=str(message_id), jump_url=jump_url or '')
        return self.save(r)

    def move(self, rid, action):
        r = self.get(rid)
        step = -1 if action == 'prev' else 1
        r['position'] = max(0, min(len(r['slides']) - 1, r['position'] + step))
        return self.save(r)

    def add_feedback(self, rid, text, user_id, slide=None):
        """Save feedback on one slide (slide is its index) or the whole carousel (slide is None)."""
        text = (text or '').strip()
        if not text:
            raise ValueError('Write some feedback first.')
        r = self.get(rid)
        if slide is not None and not 0 <= slide < len(r['slides']):
            raise ValueError('That slide is no longer in this review.')
        entry = dict(slide=slide, text=text[:FEEDBACK_LIMIT], user_id=str(user_id), at=now())
        r['feedback'].append(entry)
        self.save(r)
        if not r['test']:
            if r.get('kind') == 'shots':
                if self.shot_notes:
                    item = r['items'][slide] if slide is not None else None
                    where = f"animation {item['n']} (shot {slide + 1})" if item else 'the whole shortlist'
                    self.shot_notes(r['project_id'], f"Discord shortlist review, {entry['at'][:10]}, on {where}: {entry['text']}")
            else:
                self._to_notes(r, entry)
        return r

    def _to_notes(self, r, entry):
        where = f"slide {entry['slide'] + 1}" if entry['slide'] is not None else 'the whole carousel'
        line = f"Discord review, {entry['at'][:10]}, on {where}: {entry['text']}"
        for _ in range(3):
            item = self.carousels.get(r['carousel_id'])
            notes = ((item.get('notes') or '').rstrip() + '\n' + line).strip()[-20000:]
            try:
                self.carousels.save(item['id'], dict(notes=notes), item['revision'])
                return
            except Conflict:
                continue

    def view(self, r):
        """What the message shows: text, the slide file, and the buttons (label, action, disabled)."""
        if r.get('kind') == 'shots':
            return self.shot_view(r)
        n, i = len(r['slides']), r['position']
        label = 'Test review. ' if r['test'] else ''
        count = len(r['feedback'])
        text = (f"**{label}{r['title']}**\nSlide {i + 1} of {n}."
                + (f" {count} piece{'' if count == 1 else 's'} of feedback saved." if count else ''))
        buttons = [('Previous', 'prev', i == 0), ('Next', 'next', i >= n - 1),
                   ('Feedback on this slide', 'fb', False), ('Feedback on the whole carousel', 'fball', False)]
        return dict(text=text, file=r['slides'][i], filename=f'slide-{i + 1:02d}{Path(r["slides"][i]).suffix.lower()}', buttons=buttons)

    def shot_view(self, r):
        """One shot: its finished picture, the shot list's script with each press's words marked, and what's missing."""
        n, i = len(r['items']), r['position']
        it = r['items'][i]
        src = r.get('source') or {}
        label = 'Test review. ' if r['test'] else ''
        what = 'Talking head' if it.get('kind') == 'talk' else f"Animation {it['n']}, {TYPE_NAMES.get(it['type'], it['type'])}"
        head = f"**{label}{r['title']}**\nShot {i + 1} of {n}. {what}" + (f", at {it['time']}" if it.get('time') else '') + '.'
        lost = [p for p in it['presses'] if not p['found']]
        tail = []
        if lost:
            tail.append('Not found in the script, so not marked: ' + '; '.join(f"press {p['press']} \"{p['words']}\"" for p in lost) + '.')
        if it.get('missing'):
            tail.append(it['missing'])
        count = len(r['feedback'])
        if count:
            tail.append(f"{count} piece{'' if count == 1 else 's'} of feedback saved.")
        tail.append(f"From {src.get('shot_list', '?')} and {src.get('slides', '?')}.")
        body = it['marked']
        room = DESCRIPTION_LIMIT - len(head) - sum(len(t) + 2 for t in tail) - 8
        if len(body) > room:
            body = body[:max(0, room - 1)].rsplit(' ', 1)[0] + '…'
            if body.count('**') % 2:
                body += '**'
        text = head + '\n\n' + body + ''.join('\n\n-# ' + t for t in tail)
        file = it.get('picture') or None
        if r.get('cards'):
            # One storyboard card per segment: the finished shot, its build pictures and the marked script in one image.
            from . import storyboard
            file = storyboard.card(it, i, n, r['title'], src, self.card_root / r['id'] / f'shot-{i + 1:02d}.png', test=r['test'])
        buttons = [('Previous', 'prev', i == 0), ('Next', 'next', i >= n - 1),
                   ('Feedback on this shot', 'fb', False), ('Feedback on the whole shortlist', 'fball', False)]
        return dict(text=text, file=file, filename=f"shot-{i + 1:02d}{Path(file).suffix.lower()}" if file else '', buttons=buttons)


# Requests for a one-off review, picked up by the bridge once the bot is connected ------------------------------
def requests_dir(data_root):
    return Path(data_root) / 'discord' / 'review-requests'


def pending(data_root):
    d = requests_dir(data_root)
    if not d.is_dir():
        return []
    out = []
    for f in sorted(d.glob('*.json')):
        if f.with_suffix('.result.json').exists() or f.name.endswith('.result.json'):
            continue
        try:
            req = json.loads(f.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            continue
        if isinstance(req, dict) and req.get('carousel_id'):
            out.append((f, req))
    return out


def record(request_file, outcome):
    Path(request_file).with_suffix('.result.json').write_text(json.dumps(dict(outcome, at=now()), indent=1), encoding='utf-8')
