"""Messages the team sends Josh on Discord without being asked, with one set of limits for every bot.

There are three kinds.
- work: a team member's own update on their desk (studio/agent.py post), such as Tariq saying a cut is ready, plus
  Tariq's reminder when a script or storyboard has waited on Josh for a day.
- mail: Anouk's read-only inbox check. She saves a proposed reply in Josh OS for each email that needs one
  (studio/replies.py) and tells Josh. Nothing is drafted or sent in Gmail.
- banter: made-up office chat (studio/banter.py), about 1 or 2 a week across the whole team.

Every message goes to Josh alone, as a direct message from that team member's own bot. Max never sends any (he stays off
Discord). The shared rules, all in Data/notices/settings.json and read on every use:
- Dedupe. Each message has a key saved before it is sent, so a restart or a second check never sends it twice. A send
  with an unclear result is recorded as uncertain and never repeated on its own.
- Quiet hours, 21:00 to 08:00 Dubai. Work waits until morning, then goes out as one message per person.
- Cooldowns. A gap between any two messages from the team, a longer gap per person, and a daily cap for each.
- No floods. Anything still waiting is combined into one message per person, at most batch_limit items with a count of
  the rest, and anything older than max_age_hours is kept in Josh OS without being sent.
- Mute and pause. Josh can pause everything until a time, mute one person, or switch banter off, from Josh OS or with
  !pause, !resume, !mute, !unmute, !banter off and !banter on on Discord. Messages made while paused or muted are kept
  in Josh OS and never sent later.
- Busy. Nothing is sent and no inbox check starts while OBS is open with an active or unknown output, or while Josh OS
  is transcribing, building, rendering or exporting. Work waits for that to finish.
"""
import json
import random
import re
import subprocess
import threading
import uuid
import time as _time
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

from . import banter as banter_lines
from .store import now

UTC = timezone.utc
DUBAI = timezone(timedelta(hours=4), 'Asia/Dubai')  # no daylight saving in Dubai, so a fixed offset is exact
TEAM = ('sam', 'tariq', 'laia', 'saskia', 'anouk')
NEVER = frozenset({'max'})  # Max may answer owner chat; no proactive notices or banter
NAMES = {'sam': 'Sam', 'tariq': 'Tariq', 'laia': 'Laia', 'saskia': 'Saskia', 'anouk': 'Anouk', 'max': 'Max'}
DEFAULTS = {
    'enabled': True,
    'paused_until': '',
    'muted': [],
    'banter': True,
    'mail_checks': True,
    'quiet_start': 21, 'quiet_end': 8,
    'team_gap_minutes': 20, 'bot_gap_minutes': 120,
    'team_daily_cap': 8, 'bot_daily_cap': 3,
    'max_age_hours': 18, 'batch_limit': 5,
    'mail_every_minutes': 120, 'mail_hours': [8, 20],
    'banter_per_week': [1, 2], 'banter_min_gap_days': 2, 'banter_hours': [11, 17], 'banter_days': [0, 1, 2, 3, 4],
    'banter_after_work_minutes': 120,
    'remind_after_hours': 20,
}
EDITABLE = {'enabled': bool, 'paused_until': str, 'muted': list, 'banter': bool, 'mail_checks': bool}
DONE = ('sent', 'uncertain', 'partial', 'stale', 'skipped', 'no_bot', 'failed_final')
MAX_ATTEMPTS = 3
RETRY_MINUTES = 15


def _ts(s):
    try:
        t = datetime.fromisoformat(str(s).replace('Z', '+00:00'))
    except (TypeError, ValueError):
        return None
    return t if t.tzinfo else t.replace(tzinfo=UTC)


def dubai(at):
    return at.astimezone(DUBAI)


def in_quiet_hours(at, s):
    h = dubai(at).hour
    a, b = int(s['quiet_start']), int(s['quiet_end'])
    return (h >= a or h < b) if a > b else (a <= h < b)


def parse_duration(text):
    """'30m', '2h', '3 hours', '2d' as a timedelta, or None."""
    m = re.fullmatch(r'\s*(\d{1,3})\s*(m|min|mins|minutes?|h|hr|hrs|hours?|d|days?)\s*', text or '', re.I)
    if not m:
        return None
    n, unit = int(m.group(1)), m.group(2)[0].lower()
    return timedelta(minutes=n) if unit == 'm' else timedelta(hours=n) if unit == 'h' else timedelta(days=n)


def next_morning(at, s):
    local = dubai(at)
    day = local.date() + (timedelta(days=1) if local.hour >= int(s['quiet_end']) else timedelta())
    return datetime.combine(day, time(int(s['quiet_end'])), DUBAI)


def week_key(at):
    y, w, _ = dubai(at).isocalendar()
    return f'{y}-W{w:02d}'


def obs_busy():
    """Why OBS needs leaving alone, or ''. OBS closed is fine. OBS open is fine only when it reports every output idle."""
    import os
    if os.name != 'nt':
        return ''
    try:
        p = subprocess.run(['tasklist', '/FI', 'IMAGENAME eq obs64.exe', '/NH', '/FO', 'CSV'], capture_output=True, text=True,
                           timeout=10, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    except (OSError, subprocess.SubprocessError):
        return 'OBS state could not be checked'
    if 'obs64.exe' not in (p.stdout or '').lower():
        return ''
    try:
        from .lessons import OBS
        with OBS() as obs:
            obs.idle()  # read-only status calls; raises when any output is active or unknown
        return ''
    except Exception:  # noqa: BLE001 - unknown counts as recording
        return 'OBS is open and might be recording'


class Notices:
    def __init__(self, store, data_root, bridge=None, busy=None, rng=None, maintenance_hold_dispatch=False):
        self.maintenance_hold_dispatch=bool(maintenance_hold_dispatch)
        self.store, self.bridge = store, bridge
        self.busy = busy or (lambda: '')
        self.rng = rng or random.Random()
        self.dir = Path(data_root) / 'notices'
        self.dir.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self._sleep = _time.sleep
        self.checks = []  # functions(now_utc) that queue work, such as Tariq's reminders and Anouk's inbox check
        with store.connect() as c:
            c.execute('CREATE TABLE IF NOT EXISTS notices(key TEXT PRIMARY KEY,body TEXT NOT NULL)')
            c.execute('CREATE TABLE IF NOT EXISTS notice_state(key TEXT PRIMARY KEY,body TEXT NOT NULL)')
            for r in c.execute("SELECT key,body FROM notices").fetchall():
                b = json.loads(r['body'])
                if b.get('state') == 'sending' and not self.maintenance_hold_dispatch:
                    b.update(state='uncertain', reason='Josh OS stopped while this was sending, so it is not sent again.', updated_at=now())
                    c.execute('UPDATE notices SET body=? WHERE key=?', (json.dumps(b), r['key']))

    # Settings --------------------------------------------------------------------------------------------------
    def settings(self):
        f = self.dir / 'settings.json'
        try:
            s = json.loads(f.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            s = {}
        return dict(DEFAULTS, **{k: v for k, v in s.items() if k in DEFAULTS})

    def set(self, changes, by='josh'):
        """Change the switches Josh controls. Returns the new settings."""
        if not isinstance(changes, dict):
            raise ValueError('Invalid change.')
        with self.lock:
            f = self.dir / 'settings.json'
            try:
                saved = json.loads(f.read_text(encoding='utf-8'))
            except (OSError, ValueError):
                saved = {}
            for k, v in changes.items():
                if k not in EDITABLE or not isinstance(v, EDITABLE[k]):
                    raise ValueError(f'{k} cannot be changed here.')
                if k == 'muted':
                    v = sorted({str(a) for a in v if str(a) in TEAM})
                if k == 'paused_until' and v and _ts(v) is None:
                    raise ValueError('paused_until must be a date and time.')
                saved[k] = v
            saved['changed_at'], saved['changed_by'] = now(), by
            tmp = f.with_suffix('.tmp')
            tmp.write_text(json.dumps(saved, indent=1), encoding='utf-8')
            tmp.replace(f)
        return self.settings()

    def paused(self, at, s=None):
        s = s or self.settings()
        until = _ts(s.get('paused_until'))
        return until if until and until > at else None

    def gate(self, at, s=None):
        """Why nothing can be sent right now, or ''."""
        s = s or self.settings()
        if not s.get('enabled'):
            return 'Team messages are switched off.'
        until = self.paused(at, s)
        if until:
            return f"Team messages are paused until {dubai(until):%H:%M} Dubai on {dubai(until):%d %b}."
        if in_quiet_hours(at, s):
            return f"It's quiet hours in Dubai ({int(s['quiet_start']):02d}:00 to {int(s['quiet_end']):02d}:00)."
        return ''

    # Records ---------------------------------------------------------------------------------------------------
    def record(self, key):
        with self.store.connect() as c:
            r = c.execute('SELECT body FROM notices WHERE key=?', (key,)).fetchone()
        return json.loads(r['body']) if r else None

    def all(self, limit=40):
        with self.store.connect() as c:
            return [json.loads(r['body']) for r in c.execute('SELECT body FROM notices ORDER BY rowid DESC LIMIT ?', (limit,))]

    def _put(self, b, c=None):
        b['updated_at'] = now()
        if c is not None:
            c.execute('INSERT OR REPLACE INTO notices VALUES(?,?)', (b['key'], json.dumps(b)))
            return b
        with self.store.connect() as c2:
            c2.execute('INSERT OR REPLACE INTO notices VALUES(?,?)', (b['key'], json.dumps(b)))
        return b

    def state(self, key, default=None):
        with self.store.connect() as c:
            r = c.execute('SELECT body FROM notice_state WHERE key=?', (key,)).fetchone()
        return json.loads(r['body']) if r else default

    def put_state(self, key, value):
        with self.store.connect() as c:
            c.execute('INSERT OR REPLACE INTO notice_state VALUES(?,?)', (key, json.dumps(value)))

    def queue(self, agent_id, kind, text, key, at=None, footer='', extra=None):
        """Save a message to send. The key is the dedupe: a key already saved is never queued again, whatever its state.
        Returns the new record, or None when the key was already there."""
        at = at or datetime.now(UTC)
        s = self.settings()
        body = dict(key=key, id=str(uuid.uuid4()), agent_id=agent_id, kind=kind, text=str(text).strip(), footer=footer,
                    created_at=at.isoformat(), state='queued', attempts=0, message_ids=[], reason='', history=[], **(extra or {}))
        if agent_id in NEVER:
            body.update(state='skipped', reason=f'{NAMES.get(agent_id, agent_id)} stays off Discord.')
        elif agent_id not in TEAM:
            body.update(state='skipped', reason='Not a team member who sends messages.')
        elif not s.get('enabled'):
            body.update(state='skipped', reason='Team messages were switched off.')
        elif self.paused(at, s):
            body.update(state='skipped', reason='Team messages were paused.')
        elif agent_id in (s.get('muted') or []):
            body.update(state='skipped', reason=f'{NAMES[agent_id]} was muted.')
        elif kind == 'banter' and not s.get('banter'):
            body.update(state='skipped', reason='Banter is switched off.')
        with self.store.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            if c.execute('SELECT 1 FROM notices WHERE key=?', (key,)).fetchone():
                return None
            self._put(body, c)
        return body

    def _sent_since(self, since, agent_id=None, kind=None):
        """Messages sent since then, one per Discord message (items combined into one message count once)."""
        out, seen = [], set()
        for b in self.all(400):
            if b.get('state') not in ('sent', 'uncertain', 'partial', 'sending'):
                continue
            t = _ts(b.get('sent_at') or b.get('last_try'))
            if t and t >= since and (agent_id is None or b['agent_id'] == agent_id) and (kind is None or b['kind'] == kind):
                if b.get('batch') and b['batch'] in seen:
                    continue
                seen.add(b.get('batch'))
                out.append(b)
        return out

    # Sending ---------------------------------------------------------------------------------------------------
    def compose(self, items, s):
        """One message for one person's waiting items. Banter always goes on its own."""
        if len(items) == 1:
            b = items[0]
            return '\n'.join(x for x in (b['text'], b.get('footer')) if x)
        limit = int(s['batch_limit'])
        lines = ['A few things for you.' if any(b['kind'] != 'banter' for b in items) else '']
        lines += ['- ' + b['text'].replace('\n', ' ')[:400] for b in items[:limit]]
        if len(items) > limit:
            lines.append(f'- And {len(items) - limit} more on my desk in Josh OS.')
        foot = next((b.get('footer') for b in items if b.get('footer')), '')
        return '\n'.join(x for x in lines + [foot] if x)

    def tick(self, at=None):
        """One scheduler step. Safe to call any number of times. Returns what happened, for tests and the status line."""
        if self.maintenance_hold_dispatch:
            return 'Maintenance hold: notification rows and settings are unchanged.'
        at = at or datetime.now(UTC)
        if not self.lock.acquire(blocking=False):
            return 'busy'
        try:
            return self._tick(at)
        finally:
            self.lock.release()

    def _tick(self, at):
        s = self.settings()
        waiting = [b for b in self.all(400) if b.get('state') in ('queued', 'failed')]
        old = at - timedelta(hours=float(s['max_age_hours']))
        for b in waiting:
            if (_ts(b['created_at']) or at) < old:
                b.update(state='stale', reason='Waited too long to send, so it stays in Josh OS only.')
                self._put(b)
            elif b['agent_id'] in (s.get('muted') or []):
                b.update(state='skipped', reason=f"{NAMES[b['agent_id']]} was muted.")
                self._put(b)
            elif self.paused(at, s) or not s.get('enabled'):
                b.update(state='skipped', reason='Team messages were paused.' if s.get('enabled') else 'Team messages were switched off.')
                self._put(b)
            elif b['kind'] == 'banter' and not s.get('banter'):
                b.update(state='skipped', reason='Banter was switched off.')
                self._put(b)
        why = self.gate(at, s)
        if why:
            return why
        busy = self.busy()
        if busy:
            return f'Held. {busy}.'
        for check in list(self.checks):
            try:
                check(at)
            except Exception as e:  # noqa: BLE001 - one broken check never stops the others
                self.put_state('last_check_error', dict(at=now(), error=str(e)[:300]))
        self.plan_banter(at, s)
        self.banter_due(at, s)
        waiting = [b for b in self.all(400) if b.get('state') in ('queued', 'failed')
                   and not (b.get('state') == 'failed' and (b.get('attempts', 0) >= MAX_ATTEMPTS
                            or at - (_ts(b.get('last_try')) or at) < timedelta(minutes=RETRY_MINUTES)))]
        if not waiting:
            return 'Nothing to send.'
        if self._sent_since(at - timedelta(minutes=float(s['team_gap_minutes']))):
            return 'Waiting for the gap between team messages.'
        today = datetime.combine(dubai(at).date(), time(0), DUBAI)
        if len(self._sent_since(today)) >= int(s['team_daily_cap']):
            return "Today's team limit is reached."
        by = {}
        for b in sorted(reversed(waiting), key=lambda b: b['created_at']):  # oldest first, then in the order saved
            by.setdefault(b['agent_id'], []).append(b)
        for agent_id, items in by.items():
            if self._sent_since(at - timedelta(minutes=float(s['bot_gap_minutes'])), agent_id):
                continue
            if len(self._sent_since(today, agent_id)) >= int(s['bot_daily_cap']):
                continue
            ok, why = self.can_send(agent_id)
            if not ok and 'not connected right now' in why:
                continue   # a bot still connecting (just after a restart) waits; max_age_hours still applies
            if not ok:
                for b in items:
                    b.update(state='no_bot', reason=why)
                    self._put(b)
                continue
            work = [b for b in items if b['kind'] != 'banter']
            return self.send(agent_id, work or items[:1], s, at)
        return 'Waiting for cooldowns.'

    def can_send(self, agent_id):
        if agent_id in NEVER:
            return False, f'{NAMES[agent_id]} does not send proactive notices.'
        if self.bridge is None or not hasattr(self.bridge, 'dm_ready'):
            return False, 'The Discord bridge is not running.'
        return self.bridge.dm_ready(agent_id)

    def send(self, agent_id, items, s, at):
        if self.maintenance_hold_dispatch:
            return 'Maintenance hold: nothing claimed or sent.'
        batch = str(uuid.uuid4())
        with self.store.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            claimed = []
            for b in items:
                r = c.execute('SELECT body FROM notices WHERE key=?', (b['key'],)).fetchone()
                cur = json.loads(r['body']) if r else None
                if not cur or cur.get('state') not in ('queued', 'failed'):
                    continue
                cur.update(state='sending', attempts=cur.get('attempts', 0) + 1, last_try=at.isoformat(), batch=batch)
                self._put(cur, c)
                claimed.append(cur)
        if not claimed:
            return 'Nothing to send.'
        text = self.compose(claimed, s)
        # Record what this message shows before it goes, so it blocks older offers even if the send or the record after it
        # fails (studio/approval_lineage.py). If that record can't be saved, the message isn't sent.
        pending = None
        if self.on_sending is not None:
            try:
                owner = self.bridge.dm_owner(agent_id) if hasattr(self.bridge, 'dm_owner') else None
                pending = self.on_sending(agent_id, claimed, owner, at.isoformat())
            except Exception as e:  # noqa: BLE001 - not sent, tried again later like any failed send
                result = dict(state='failed', reason=f'Josh OS could not record this message before sending it ({str(e)[:150]}). Nothing was sent.')
                for b in claimed:
                    b.update(state='failed' if b['attempts'] < MAX_ATTEMPTS else 'failed_final', reason=result['reason'])
                    b['history'].append(dict(at=now(), state=b['state'], reason=result['reason']))
                    self._put(b)
                return f'{NAMES[agent_id]}: failed'
        try:
            result = self.bridge.send_dm(agent_id, text)
        except Exception as e:  # noqa: BLE001 - we can't tell whether it posted
            result = dict(state='uncertain', reason=f'The send stopped unexpectedly: {str(e)[:200]}')
        state = result.get('state') if result.get('state') in ('sent', 'failed', 'uncertain', 'partial', 'blocked') else 'uncertain'
        for b in claimed:
            st = state
            if st in ('failed', 'blocked'):
                st = 'failed' if b['attempts'] < MAX_ATTEMPTS else 'failed_final'
            b.update(state=st, reason=result.get('reason', ''), message_ids=list(result.get('message_ids') or []), sent_text=text)
            if st in ('sent', 'uncertain', 'partial'):
                b['sent_at'] = at.isoformat()
            b['history'].append(dict(at=now(), state=st, reason=str(result.get('reason', ''))[:300]))
            self._put(b)
        if self.on_sent is not None and pending is not None:
            try:
                self.on_sent(pending, result)
            except Exception:  # noqa: BLE001 - the record stays "sending", which blocks every offer in that DM
                pass
            finally:
                if self.on_release is not None:
                    try:
                        self.on_release(pending)      # let the next message in that DM go, whatever happened above
                    except Exception:  # noqa: BLE001
                        pass
        if state == 'sent' and any(b['kind'] == 'banter' for b in claimed) and self.on_banter:
            try:
                self.on_banter(agent_id, text)
            except Exception:  # noqa: BLE001 - the message went; the desk copy is extra
                pass
        return f'{NAMES[agent_id]}: {state}'

    on_banter = None  # set by the server: copies sent banter onto that person's desk chat, marked as made up
    on_sending = None  # set by the server: records a message as "sending" before it goes (studio/approval_lineage.py)
    on_sent = None     # set by the server: finishes that record with what reached Discord
    on_release = None  # set by the server: frees that conversation for the next message

    # Banter ----------------------------------------------------------------------------------------------------
    def plan_banter(self, at, s):
        """Pick this week's banter times once, at random, spaced at least banter_min_gap_days apart. Weeks that start
        part way through (Josh OS turned on mid-week) get only the days that are left."""
        wk = week_key(at)
        if self.state(f'banter_plan:{wk}') is not None:
            return self.state(f'banter_plan:{wk}')
        lo, hi = (s['banter_per_week'] + [1])[:2] if isinstance(s['banter_per_week'], list) else (1, 2)
        n = self.rng.randint(int(lo), int(hi))
        local = dubai(at)
        monday = local.date() - timedelta(days=local.weekday())
        h0, h1 = int(s['banter_hours'][0]), int(s['banter_hours'][1])
        days = [monday + timedelta(days=d) for d in s['banter_days']]
        days = [d for d in days if datetime.combine(d, time(h1), DUBAI) > local]
        self.rng.shuffle(days)
        picked = []
        for d in days:
            if len(picked) >= n:
                break
            if all(abs((d - p).days) >= int(s['banter_min_gap_days']) for p in picked):
                picked.append(d)
        slots = []
        for d in sorted(picked):
            start = max(datetime.combine(d, time(h0), DUBAI), local + timedelta(minutes=5))
            end = datetime.combine(d, time(h1), DUBAI)
            if end > start:
                slots.append((start + timedelta(seconds=self.rng.uniform(0, (end - start).total_seconds()))).astimezone(UTC).isoformat())
        plan = dict(week=wk, slots=slots, done=[], made_at=at.isoformat())
        self.put_state(f'banter_plan:{wk}', plan)
        return plan

    def banter_due(self, at, s):
        if not s.get('banter'):
            return None
        wk = week_key(at)
        plan = self.state(f'banter_plan:{wk}')
        if not plan:
            return None
        for i, slot in enumerate(plan['slots']):
            t = _ts(slot)
            if i in plan['done'] or t is None or at < t:
                continue
            plan['done'].append(i)
            self.put_state(f'banter_plan:{wk}', plan)
            local = dubai(at)
            if at - t > timedelta(hours=3) or not (int(s['banter_hours'][0]) <= local.hour < int(s['banter_hours'][1])):
                return None  # missed while Josh OS was off; no catching up
            hi = int((s['banter_per_week'] + [2])[1]) if isinstance(s['banter_per_week'], list) else 2
            if len(self._sent_since(at - timedelta(days=7), kind='banter')) >= hi:
                return None
            if self._sent_since(at - timedelta(days=int(s['banter_min_gap_days'])), kind='banter'):
                return None
            if self._sent_since(at - timedelta(minutes=float(s['banter_after_work_minutes']))):
                return None  # the team has just been in touch about work
            return self.make_banter(at, s, f'banter:{wk}:{i}')
        return None

    def make_banter(self, at, s, key):
        last = self.state('banter_last', {}) or {}
        used = set(last.get('used') or [])
        who = [a for a in TEAM if a not in (s.get('muted') or []) and a != last.get('agent') and self.can_send(a)[0]]
        if not who:
            return None
        agent_id = self.rng.choice(who)
        pool = [l for l in banter_lines.LINES[agent_id] if l not in used] or banter_lines.LINES[agent_id]
        line = self.rng.choice(pool)
        used = (list(last.get('used') or []) + [line])[-20:]
        self.put_state('banter_last', dict(agent=agent_id, used=used, at=at.isoformat()))
        # Sent as it is, with no label, so it reads like the team talking (Josh, 12 Nov 2026). It's still marked as
        # banter inside Josh OS, so no team member ever mistakes it for real work.
        return self.queue(agent_id, 'banter', line, key, at)

    # Josh's controls -------------------------------------------------------------------------------------------
    def command(self, agent_id, text, at=None):
        """A control message Josh typed on Discord, like !pause 2h. Returns the reply, or None when it isn't one."""
        at = at or datetime.now(UTC)
        t = (text or '').strip()
        if not t.startswith('!'):
            return None
        word, _, rest = t[1:].partition(' ')
        word, rest = word.lower(), rest.strip().lower()
        s = self.settings()
        name = NAMES.get(agent_id, agent_id)
        if word == 'pause':
            if rest in ('today', 'tonight'):
                until = next_morning(at, s)
            else:
                d = parse_duration(rest) if rest else timedelta(hours=2)
                if d is None or d > timedelta(days=14):
                    return 'Try !pause 2h, !pause 30m, !pause 3d or !pause today.'
                until = at + d
            self.set(dict(paused_until=until.astimezone(UTC).isoformat()))
            return (f"Paused. Nobody on the team will message you until {dubai(until):%H:%M} Dubai on {dubai(until):%a %d %b}. "
                    'Send !resume to undo it.')
        if word == 'resume':
            self.set(dict(paused_until='', enabled=True))
            return "Back on. Anything made while you were paused stays in Josh OS and won't be sent now."
        if word in ('mute', 'unmute'):
            if agent_id not in TEAM:
                return None
            muted = set(s.get('muted') or [])
            (muted.add if word == 'mute' else muted.discard)(agent_id)
            self.set(dict(muted=sorted(muted)))
            return (f"Muted. I won't message you on my own until you send !unmute. You can still talk to me here."
                    if word == 'mute' else "Unmuted. I'll message you again when something needs you.")
        if word == 'banter' and rest in ('off', 'on'):
            self.set(dict(banter=rest == 'on'))
            return 'Banter is off for the whole team.' if rest == 'off' else 'Banter is back on. Expect one or two a week at most.'
        if word in ('notices', 'messages', 'status'):
            return self.status_line(at)
        if word == 'help':
            return ('!pause 2h, !pause today, !resume, !mute, !unmute, !banter off, !banter on and !notices. '
                    '!mute and !unmute only change the person you send them to.')
        return None

    def status_line(self, at=None):
        at = at or datetime.now(UTC)
        s = self.settings()
        bits = ['Maintenance hold: notification dispatch is suspended; queued rows are preserved.' if self.maintenance_hold_dispatch else (self.gate(at, s) or 'Team messages are on.')]
        muted = [NAMES[a] for a in s.get('muted') or [] if a in NAMES]
        if muted:
            bits.append(' and '.join(muted) + (' is' if len(muted) == 1 else ' are') + ' muted.')
        bits.append('Banter is on.' if s.get('banter') else 'Banter is off.')
        bits.append('Max does not send proactive notices.')
        return ' '.join(bits)

    def view(self, at=None):
        at = at or datetime.now(UTC)
        s = self.settings()
        bots = []
        for a in TEAM:
            ok, why = self.can_send(a)
            bots.append(dict(id=a, name=NAMES[a], muted=a in (s.get('muted') or []), ready=ok, why='' if ok else why))
        return dict(settings={k: s[k] for k in EDITABLE}, status=self.status_line(at), paused_until=s.get('paused_until') or '',
                    bots=bots, recent=[{k: b.get(k) for k in ('key', 'agent_id', 'kind', 'text', 'state', 'reason', 'created_at', 'sent_at')}
                                       for b in self.all(30)])

    # Loop ------------------------------------------------------------------------------------------------------
    def loop(self, every=60):
        while True:
            try:
                self.tick()
            except Exception as e:  # noqa: BLE001 - keep the loop alive
                try:
                    (self.dir / 'last-error.txt').write_text(f'{now()} {e}', encoding='utf-8')
                except OSError:
                    pass
            self._sleep(every)


# Checks ------------------------------------------------------------------------------------------------------------
def tariq_reminders(notices, flow, store, s_getter=None):
    """A check: a script or storyboard that has waited on Josh for remind_after_hours gets one reminder, ever.
    Only revisions from the last three days, so turning this on never reminds him about old work."""
    def check(at):
        s = notices.settings()
        wait = timedelta(hours=float(s['remind_after_hours']))
        with store.connect() as c:
            pids = [r['project_id'] for r in c.execute(
                "SELECT DISTINCT project_id FROM workflow_artifacts WHERE kind IN ('edit_proposal','edit_visuals')").fetchall()]
        for pid in pids:
            try:
                name = store.get(pid).get('name') or 'this video'
                stages = {x['key']: x for x in flow.status(pid)}
            except Exception:  # noqa: BLE001 - a project that went missing
                continue
            for kind, stage, text in (('edit_proposal', 'proposal', 'The script for {name} has been waiting on your review since {when}.'),
                                      ('edit_visuals', 'visuals', 'The storyboard for {name} has been waiting on your OK since {when}.')):
                art = flow.latest(pid, kind)
                if not art or stages.get(stage, {}).get('state') != 'review':
                    continue
                made = _ts(art.get('created_at'))
                if made is None or at - made < wait or at - made > timedelta(days=3):
                    continue
                notices.queue('tariq', 'work', text.format(name=name, when=f'{dubai(made):%a %H:%M}'), f'remind:{art["id"]}', at)
    return check


# Wiring ------------------------------------------------------------------------------------------------------------
JOSH_ACTIVE_MINUTES = 15  # a desk update isn't sent to Discord when Josh wrote to that person this recently


def busy_reasons(server):
    """Why the team should wait, or ''. Recording, and any transcribing, building, rendering or exporting."""
    flow = getattr(server, 'edit_flow', None)
    if flow is not None and any(t.is_alive() for t in list(getattr(flow, 'jobs', {}).values())):
        return 'Tariq is transcribing, building, rendering or exporting'
    jobs = getattr(server, 'jobs', None)
    if jobs is not None:
        try:
            if any((j.get('status') or {}).get('state') in ('running', 'starting') for j in jobs.all()):
                return 'A render is running'
        except Exception:  # noqa: BLE001 - unknown counts as busy
            return 'Render state could not be checked'
    return obs_busy()


def wire(server):
    """Set up the team's messages inside Josh OS (called once by studio/server.py)."""
    from . import gmail_access
    from .agent import DESK
    from .replies import Replies
    agents = server.agents
    replies = Replies(server.store, server.root)
    agents.replies = replies
    n = Notices(server.store, server.store.root, getattr(server, 'discord', None), lambda: busy_reasons(server), maintenance_hold_dispatch=getattr(server,'maintenance_hold_dispatch',False))
    if getattr(server, 'discord', None) is not None:
        server.discord.notices = n

    def on_post(agent_id, m):
        if agent_id not in TEAM:
            return None
        cutoff = datetime.now(UTC) - timedelta(minutes=JOSH_ACTIVE_MINUTES)
        recent = [x for x in agents.thread(DESK, agent_id)[-6:] if x.get('role') == 'josh' and (_ts(x.get('at')) or cutoff) > cutoff]
        if recent:
            return None  # Josh is talking to them in Josh OS right now and will see it there
        return n.queue(agent_id, 'work', m['text'], f"post:{m['id']}")
    agents.on_post = on_post
    n.on_banter = lambda agent_id, text: agents.post(agent_id, text, notice=False, banter=True)
    n.on_sending = replies.begin_notice
    n.on_sent = replies.finish_notice
    n.on_release = replies.lineage.release
    flow = getattr(server, 'edit_flow', None)
    if flow is not None:
        n.checks.append(tariq_reminders(n, flow, server.store))

    def run(prompt, schema):
        team = agents.team.all()
        agent = next(x for x in team['agents'] if x['id'] == 'anouk')
        learnings = [l for l in team['learnings'] if l['agent_id'] in ('', 'anouk')][:80]
        tools = list(gmail_access.tools_for('anouk', agents.root))
        if not tools or any(t not in gmail_access.READ_TOOLS for t in tools):
            raise RuntimeError('Gmail read access is not set up for Anouk.')
        _, answer = agents.run(agents.desk_system_prompt(agent, learnings), prompt, schema, (), extra_tools=tools,
                               full=True, read_only=True, timeout=600)
        return answer
    n.mail_run = run
    n.checks.append(replies.checker(n, run))
    if not n.maintenance_hold_dispatch:threading.Thread(target=n.loop, daemon=True, name='team-notices').start()
    return n, replies
