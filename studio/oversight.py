"""Max keeps Josh moving. A daily background check of the goals, the content calendar and what's outstanding.

Josh's words: "Max has the overall calendar and goals and stuff, and keeps me moving", and "don't want crazy business data
stuff. The general goals are obviously more money more sales; we want more emails being sent, more youtube views and
subscribers, more emails collected. So to see things getting better."

So Max looks at five plain trends, each the last seven complete days against the seven before, from the source that has
it, with that source's own dates:
- sales (course plus upsell revenue) and emails collected (new subscribers) from the Linklr snapshot, by UTC day
- emails sent (Kit broadcasts and their recipients) from Josh OS's daily Kit snapshot
- YouTube views and net subscribers from the YouTube Analytics cache, by Pacific day
A trend with any day missing is shown as not available, with the reason. Nothing is filled in or estimated.

Then the calendar and what's outstanding, all from records Josh OS keeps: videos with publish dates, planned emails,
course lessons waiting on Josh's script review, Tariq's edits waiting on Josh, Anouk's saved replies waiting on his OK,
and Josh's own Tasks list. A slip is something whose date has passed and is still open. A decision is something only
Josh can unblock.

What happens with it:
- Every day from 09:00 Dubai, once (key oversight:YYYY-MM-DD), the check runs in the background whether or not any
  Discord bot is connected. Its result is saved in Data/business/oversight/ and Max posts it on his desk in Josh OS.
  Max stays off Discord.
- A slip or decision that belongs to a team member becomes a short nudge from that person, queued through the team's
  own message rules (studio/notices.py), so quiet hours, cooldowns, daily caps, mute and pause all apply. At most
  NUDGES_PER_DAY a day, the same item at most once every GAP_DAYS and never more than MAX_TIMES in all. A nudge only
  points Josh at the item. It never approves, drafts, sends or publishes anything, and Anouk's replies still need Josh's
  approval of each exact version.
- Whether a nudge went out is read back from the notice record (sent with Discord message ids, or why not). It is never
  assumed.
"""
import json
import threading
import time as _time
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

from .store import now

UTC = timezone.utc
DUBAI = timezone(timedelta(hours=4), 'Asia/Dubai')
CHECK_HOUR = 9
LAST_QUEUE_HOUR = 19          # nothing new is queued after 19:00 Dubai, so it can't wait overnight and go stale
NUDGES_PER_DAY = 2
GAP_DAYS = 3
MAX_TIMES = 3
WINDOW = 7
NAMES = {'sam': 'Sam', 'tariq': 'Tariq', 'laia': 'Laia', 'saskia': 'Saskia', 'anouk': 'Anouk', 'max': 'Max'}


def _d(s):
    return date.fromisoformat(str(s)[:10])


def _day(d):
    return f'{d:%a} {d.day} {d:%b}'


def _ts(s):
    try:
        return datetime.fromisoformat(str(s).replace('Z', '+00:00'))
    except (TypeError, ValueError):
        return None


def trend(name, rows, key, end, source, tz, fetched_at, money=False, fn=None):
    """Seven days ending on `end` against the seven before. rows: {iso date: row}. None for any missing day."""
    days = [end - timedelta(days=i) for i in range(2 * WINDOW)]
    missing = [d for d in days if d.isoformat() not in rows]
    out = dict(name=name, source=source, timezone=tz, fetched_at=fetched_at, end=end.isoformat() if end else None)
    if missing:
        return dict(out, available=False, reason=f'{len(missing)} of the 14 days are missing from {source}.')
    val = fn or (lambda r: r.get(key))
    vals = [val(rows[d.isoformat()]) for d in days]
    if any(not isinstance(v, (int, float)) for v in vals):
        return dict(out, available=False, reason=f'{source} has a day without this figure.')
    cur, prev = sum(vals[:WINDOW]), sum(vals[WINDOW:])
    return dict(out, available=True, current=round(cur, 2), previous=round(prev, 2), money=money,
                direction='up' if cur > prev else ('down' if cur < prev else 'flat'))


def goals(linklr, youtube, kit):
    """The five trends from the saved snapshots. Read only."""
    out = []
    if linklr and linklr.get('daily'):
        rows = {r['date']: r for r in linklr['daily']}
        today = _d(linklr.get('today_utc') or linklr.get('fetched_at'))
        end = today - timedelta(days=1)   # the newest complete UTC day
        f = linklr.get('fetched_at')
        out.append(trend('Sales (courses and upsells)', rows, None, end, 'Linklr', 'UTC', f, money=True,
                         fn=lambda r: (r.get('course_usd') or 0) + (r.get('upsell_usd') or 0)
                         if isinstance(r.get('course_usd'), (int, float)) and isinstance(r.get('upsell_usd'), (int, float)) else None))
        out.append(trend('Emails collected', rows, 'new_subscribers', end, 'Linklr', 'UTC', f))
    else:
        for n in ('Sales (courses and upsells)', 'Emails collected'):
            out.append(dict(name=n, source='Linklr', available=False, reason='No Linklr snapshot has been saved yet.'))
    out.append(emails_sent(kit))
    if youtube and youtube.get('daily') and youtube.get('data_through'):
        rows = {r['date']: r for r in youtube['daily']}
        end = _d(youtube['data_through'])
        out.append(trend('YouTube views', rows, 'views', end, 'YouTube Analytics', 'Pacific', youtube.get('fetched_at')))
        out.append(trend('YouTube subscribers (net)', rows, 'net_subscribers', end, 'YouTube Analytics', 'Pacific', youtube.get('fetched_at')))
    else:
        for n in ('YouTube views', 'YouTube subscribers (net)'):
            out.append(dict(name=n, source='YouTube Analytics', available=False, reason='No YouTube Analytics report has been saved yet.'))
    return out


def emails_sent(kit):
    """Broadcasts Kit says were sent in the seven days before the snapshot, against the seven before that."""
    base = dict(name='Emails sent', source='Kit snapshot', timezone='UTC')
    if not kit or not kit.get('complete') or not isinstance(kit.get('items'), dict):
        return dict(base, available=False, reason='No complete Kit snapshot yet.')
    taken = _ts(kit.get('taken_at'))
    if taken is None:
        return dict(base, available=False, reason='The Kit snapshot has no time.')
    # Recipients summed over broadcasts are deliveries, not unique people (one person gets every broadcast). A broadcast
    # without a recipient count makes that window's deliveries unknown, never a smaller number.
    cur, prev, cur_r, prev_r = 0, 0, 0, 0
    for b in kit['items'].values():
        at = _ts(b.get('send_at'))
        if at is None or b.get('status') != 'completed':
            continue
        r = b.get('recipients')
        r = r if isinstance(r, int) and not isinstance(r, bool) and r >= 0 else None
        age = taken - at
        if timedelta(0) <= age < timedelta(days=WINDOW):
            cur, cur_r = cur + 1, (cur_r + r if cur_r is not None and r is not None else None)
        elif timedelta(days=WINDOW) <= age < timedelta(days=2 * WINDOW):
            prev, prev_r = prev + 1, (prev_r + r if prev_r is not None and r is not None else None)
    return dict(base, available=True, fetched_at=kit['taken_at'], end=taken.date().isoformat(), current=cur, previous=prev,
                deliveries=cur_r, previous_deliveries=prev_r, direction='up' if cur > prev else ('down' if cur < prev else 'flat'))


def fmt(v, money):
    return f'${v:,.2f}' if money else f'{int(v):,}' if float(v).is_integer() else f'{v:,}'


def goal_line(g):
    if not g.get('available'):
        return f"{g['name']}: not available. {g.get('reason', '')}".strip()
    m = g.get('money')
    word = {'up': 'up from', 'down': 'down from', 'flat': 'the same as'}[g['direction']]
    extra = ''
    if g['name'] == 'Emails sent':
        n = lambda k: f"{k} broadcast" + ('' if k == 1 else 's')
        dl = lambda v: f'{v:,} deliveries' if v is not None else 'an unknown number of deliveries'
        return (f"Emails sent: {n(g['current'])} ({dl(g['deliveries'])}) in the 7 days to the Kit snapshot, {word} "
                f"{n(g['previous'])} ({dl(g['previous_deliveries'])}).")
    return (f"{g['name']}: {fmt(g['current'], m)} in the 7 days to {_day(_d(g['end']))}, {word} {fmt(g['previous'], m)} "
            f"({g['source']}, {g['timezone']} days).")


def kit_evidence(c, kit, today):
    """What Kit itself says about one planned email. Only an email linked to its own Kit broadcast, checked in a snapshot
    from today or yesterday (Dubai), can be sent, scheduled or none (still a draft in Kit). Everything else is unknown:
    an unlinked email may have gone out on another day or under another subject, and an old snapshot can't show what
    Kit says now. Unknown is a check for Josh, never an overdue nudge or a send suggestion."""
    if not c.get('kit_id'):
        return dict(state='unknown', detail="It isn't linked to a Kit broadcast, so Josh OS can't tell if it already went.")
    if not kit or not kit.get('complete') or not isinstance(kit.get('items'), dict):
        return dict(state='unknown', detail="There's no complete Kit snapshot to check against.")
    taken = _ts(kit.get('taken_at'))
    if taken is None or taken.astimezone(DUBAI).date() < today - timedelta(days=1):
        return dict(state='unknown', detail="The latest Kit snapshot is too old to show what Kit says now.")
    b = kit['items'].get(str(c['kit_id']))
    if b is None:
        return dict(state='unknown', detail=f"Its Kit broadcast {c['kit_id']} isn't in the latest snapshot.")
    st = b.get('status')
    if st == 'completed':
        return dict(state='sent', id=c['kit_id'], status=st, send_at=b.get('send_at'), detail=f"sent {b.get('send_at')}")
    if st == 'scheduled':
        return dict(state='scheduled', id=c['kit_id'], status=st, send_at=b.get('send_at'), detail=f"scheduled for {b.get('send_at')}")
    if st == 'draft':
        return dict(state='none', detail=f"Its Kit broadcast {c['kit_id']} is still a draft in Kit's {taken.date().isoformat()} snapshot.")
    return dict(state='unknown', detail=f"Its Kit broadcast {c['kit_id']} is {st}.")


def project_name(p):
    chosen = p.get('chosen_title')
    t = next((x.get('text') for x in p.get('titles') or [] if x.get('id') == chosen), None)
    return t or p.get('name') or 'this video'


class Oversight:
    def __init__(self, store, data_root, business=None, notices=None, agents=None, flow=None, emails=None, courses=None,
                 replies=None, clock=None):
        self.store, self.business, self.notices, self.agents = store, business, notices, agents
        self.flow, self.emails, self.courses, self.replies = flow, emails, courses, replies
        self.clock = clock or (lambda: datetime.now(UTC))
        self.dir = Path(data_root) / 'business' / 'oversight'
        self.dir.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self._sleep = _time.sleep

    # -- state
    def _read(self, name, default):
        try:
            return json.loads((self.dir / name).read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return default

    def _write(self, name, value):
        p = self.dir / name
        tmp = p.with_suffix('.tmp')
        tmp.write_text(json.dumps(value, indent=1, default=str), encoding='utf-8')
        tmp.replace(p)

    # -- reading
    def sources(self):
        sc = getattr(self.business, 'scorecard', None)
        linklr = sc.snapshot() if sc else None
        yt = sc.youtube.snapshot() if sc else None
        kit = None
        counters = getattr(self.business, 'counters', None)
        if counters is not None:
            with self.store.connect() as c:
                r = c.execute("SELECT body FROM business_counters WHERE key LIKE 'kit:%' ORDER BY key DESC LIMIT 1").fetchone()
            kit = json.loads(r['body']) if r else None
        return linklr, yt, kit

    def calendar(self, today):
        out = []
        for p in self.store.projects():
            if p.get('archived') or not p.get('publish_date'):
                continue
            try:
                d = _d(p['publish_date'])
            except ValueError:
                continue
            if today - timedelta(days=7) <= d <= today + timedelta(days=14):
                out.append(dict(kind='video', id=p['id'], title=project_name(p), date=d.isoformat(), stage=p.get('stage')))
        for e in (self.emails.all() if self.emails is not None else []):
            if e.get('archived') or not e.get('planned_date'):
                continue
            try:
                d = _d(e['planned_date'])
            except ValueError:
                continue
            if today - timedelta(days=7) <= d <= today + timedelta(days=14):
                link = e.get('kit') if isinstance(e.get('kit'), dict) else {}
                out.append(dict(kind='email', id=e['id'], title=e.get('subject') or 'an email', date=d.isoformat(), stage=e.get('status'),
                                kit_id=str(link.get('id') or '') or None))
        return sorted(out, key=lambda x: x['date'])

    def findings(self, today, kit=None):
        """Slips and decisions, each with its owner, the record it comes from and a stable key."""
        out = []
        for c in self.calendar(today):
            d = _d(c['date'])
            if c['kind'] == 'video' and c['stage'] != 'Published':
                if d < today:
                    out.append(dict(key=f"video:{c['id']}:slip:{c['date']}", agent='sam', kind='slip',
                                    text=f"\"{c['title']}\" was due out {_day(d)} and the board still has it at {c['stage']}. Want a new date?",
                                    evidence=f"Video board: {c['stage']}, publish date {c['date']}"))
                elif d <= today + timedelta(days=3) and c['stage'] in ('Idea', 'Scripting', 'Ready to record', 'Recorded'):
                    out.append(dict(key=f"video:{c['id']}:close:{c['date']}", agent='sam', kind='slip',
                                    text=f"\"{c['title']}\" goes out {_day(d)} and it's still at {c['stage']}.",
                                    evidence=f"Video board: {c['stage']}, publish date {c['date']}"))
            if c['kind'] == 'email' and c['stage'] not in ('Scheduled', 'Published', 'Sent'):
                if d < today:
                    # Kit's own record beats the local plan. Max never suggests sending an email.
                    seen = kit_evidence(c, kit, today)
                    if seen['state'] in ('sent', 'scheduled'):
                        out.append(dict(key=f"email:{c['id']}:reconcile", agent='max', kind='reconcile',
                                        text=(f"Kit shows the email \"{c['title']}\" {seen['state']} ({seen['detail']}), but Josh OS still has "
                                              f"it at {c['stage']}. The plan needs updating to match."),
                                        evidence=f"Kit broadcast {seen['id']}: {seen['status']}, {seen['send_at']}"))
                    elif seen['state'] == 'none':
                        out.append(dict(key=f"email:{c['id']}:slip:{c['date']}", agent='laia', kind='slip',
                                        text=(f"The email \"{c['title']}\" was planned for {_day(d)} and Kit still has it as a draft. "
                                              'Want a new date, or should it come off the plan?'),
                                        evidence=f"Email plan: {c['stage']}, planned {c['date']}. {seen['detail']}"))
                    else:
                        out.append(dict(key=f"email:{c['id']}:reconcile", agent='max', kind='reconcile',
                                        text=(f"The email \"{c['title']}\" was planned for {_day(d)} and Josh OS still has it at {c['stage']}. "
                                              f"{seen['detail']} Check Kit before anything else happens with it."),
                                        evidence=f"Email plan: {c['stage']}, planned {c['date']}"))
                elif d <= today + timedelta(days=2) and c['stage'] == 'Ready':
                    out.append(dict(key=f"email:{c['id']}:ready:{c['date']}", agent='laia', kind='decision',
                                    text=f"The email \"{c['title']}\" for {_day(d)} is ready for your OK.",
                                    evidence=f"Email plan: Ready, planned {c['date']}"))
        if self.flow is not None:
            for p in self.store.projects():
                if p.get('archived'):
                    continue
                try:
                    stages = self.flow.status(p['id'])
                except Exception:  # noqa: BLE001 - no edit for this video
                    continue
                for s in stages:
                    if s.get('state') == 'review':
                        out.append(dict(key=f"edit:{p['id']}:{s.get('key')}", agent='tariq', kind='decision',
                                        text=f"{s.get('label') or s.get('key')} for \"{project_name(p)}\" is waiting on your review.",
                                        evidence=f"Edit workflow: {s.get('key')} in review"))
        if self.courses is not None:
            waiting = [l for l in self.courses._all() if l.get('stage') == 'Script review']
            if waiting:
                import hashlib
                first = waiting[0].get('title') or waiting[0].get('number')
                text = (f"The script for lesson \"{first}\" is waiting on your review." if len(waiting) == 1 else
                        f"{len(waiting)} lesson scripts are waiting on your review, starting with \"{first}\".")
                out.append(dict(key='lessons:script:' + hashlib.sha256(','.join(sorted(str(l['id']) for l in waiting)).encode()).hexdigest()[:12],
                                agent='saskia', kind='decision', text=text, evidence=f'Course lessons: {len(waiting)} at Script review'))
        if self.replies is not None:
            from .replies import OPEN
            waiting = [b for b in self.replies.all(OPEN) if b.get('state') == 'proposed']
            if waiting:
                out.append(dict(key='replies:' + ','.join(sorted(b['id'][:8] for b in waiting)), agent='anouk', kind='decision',
                                text=(f"{len(waiting)} customer {'reply is' if len(waiting) == 1 else 'replies are'} saved and waiting for your OK in "
                                      'Josh OS. Nothing goes out until you approve each one.'),
                                evidence='Replies: ' + ', '.join(b['id'][:8] for b in waiting[:5])))
        tasks = self.business._tasks() if self.business is not None else []
        for t in tasks:
            if t.get('kind', 'task') != 'task' or t.get('status') in ('Done', 'Idea', 'Later') or not t.get('due_date'):
                continue
            if t.get('snoozed_until') and t['snoozed_until'] > today.isoformat():
                continue
            d = _d(t['due_date'])
            if d < today:
                owner = str(t.get('owner') or '').lower()
                out.append(dict(key=f"task:{t['id']}:slip:{t['due_date']}", agent=owner if owner in NAMES and owner != 'max' else 'max',
                                kind='slip', text=f"\"{t['title']}\" was due {_day(d)} and is still open. Done, or a new date?",
                                evidence=f"Tasks: {t.get('status')}, due {t['due_date']}"))
        return out

    def assess(self, now_utc=None):
        now_utc = now_utc or self.clock()
        today = now_utc.astimezone(DUBAI).date()
        linklr, yt, kit = self.sources()
        return dict(built_at=now_utc.isoformat(), day_dubai=today.isoformat(), goals=goals(linklr, yt, kit),
                    calendar=self.calendar(today), findings=self.findings(today, kit))

    # -- nudges
    def pick(self, findings, today):
        """Which findings become nudges today, after the gap, the lifetime cap and the daily cap."""
        state = self._read('nudges.json', {})
        sent_today = sum(1 for v in state.values() if v.get('last') == today.isoformat())
        sup = self._read('suppressed.json', {})
        ns = self.notices.settings() if self.notices is not None else {}
        muted = set(ns.get('muted') or [])
        if not ns.get('enabled', True) or (self.notices is not None and self.notices.paused(datetime.now(UTC), ns)):
            return []
        out = []
        for f in findings:
            if any(f['key'].startswith(k + ':') or f['key'] == k for k in sup):
                continue   # Josh OS learned this one was wrong or settled; never nudged again
            if f['kind'] == 'reconcile' or f['agent'] not in ('sam', 'tariq', 'laia', 'saskia', 'anouk') or f['agent'] in muted:
                continue   # a muted person's items stay in Max's summary and don't use up the day's nudges
            s = state.get(f['key']) or {}
            if int(s.get('times') or 0) >= MAX_TIMES:
                continue
            if s.get('last') and (today - _d(s['last'])).days < GAP_DAYS:
                continue
            if sent_today + len(out) >= NUDGES_PER_DAY:
                break
            out.append(f)
        return out

    def plan(self, findings, now_utc):
        """The day's nudges with their notice keys, worked out once and saved in the day record before anything is
        queued, so a restart part way through queues the same keys (which notices.py never sends twice)."""
        today = now_utc.astimezone(DUBAI).date()
        state = self._read('nudges.json', {})
        out = []
        for f in self.pick(findings, today):
            n = int((state.get(f['key']) or {}).get('times') or 0) + 1
            out.append(dict(key=f['key'], agent=f['agent'], text=f['text'], footer=f"From Max's daily check. {f['evidence']}.",
                            notice_key=f"max:{f['key']}:{n}", times=n, state='planned', reason=''))
        return out

    def queue_planned(self, rec, now_utc):
        """Queue each planned nudge (a key already saved is left as it is), then record it against the item."""
        if self.notices is None:
            return rec
        state = self._read('nudges.json', {})
        for q in rec['nudges']:
            if q['state'] == 'planned':
                r = self.notices.queue(q['agent'], 'work', q['text'], q['notice_key'], now_utc, footer=q['footer'])
                got = r or self.notices.record(q['notice_key']) or {}
                q.update(state=got.get('state', 'queued'), reason=got.get('reason', ''))
            state[q['key']] = dict(last=rec['day_dubai'], times=q['times'], notice_key=q['notice_key'], agent=q['agent'])
        self._write('nudges.json', state)
        return rec

    def outcomes(self, day_rec):
        """Read each nudge's real outcome back from the notice record."""
        for q in day_rec.get('nudges') or []:
            r = self.notices.record(q['notice_key']) if self.notices is not None else None
            if r:
                q.update(state=r.get('state'), message_ids=r.get('message_ids') or [], reason=r.get('reason') or '')
        return day_rec

    # -- the daily run
    def summary(self, a, queued):
        lines = [f"Here's where things stand, {_day(_d(a['day_dubai']))}.", '', 'Goals, the last 7 days against the 7 before:']
        lines += ['- ' + goal_line(g) for g in a['goals']]
        cal = [c for c in a['calendar'] if c['date'] >= a['day_dubai']][:6]
        if cal:
            lines += ['', 'Coming up:'] + [f"- {_day(_d(c['date']))}: {c['kind']} \"{c['title']}\" ({c['stage']})" for c in cal]
        slips = [f for f in a['findings'] if f['kind'] == 'slip']
        dec = [f for f in a['findings'] if f['kind'] == 'decision']
        rec_ = [f for f in a['findings'] if f['kind'] == 'reconcile']
        if slips:
            lines += ['', 'Slipped:'] + ['- ' + f['text'] for f in slips[:6]]
        if dec:
            lines += ['', 'Waiting on you:'] + ['- ' + f['text'] for f in dec[:6]]
        if rec_:
            lines += ['', 'Needs checking (Josh OS and Kit disagree):'] + ['- ' + f['text'] for f in rec_[:6]]
        if not slips and not dec:
            lines += ['', 'Nothing has slipped and nothing is waiting on you.']
        if queued:
            lines += ['', 'I asked ' + ' and '.join(sorted({NAMES[q['agent']] for q in queued})) + ' to nudge you about '
                      + ('that.' if len(queued) == 1 else 'those.')]
        return '\n'.join(lines)

    def _already_posted(self, marker):
        from .agent import DESK
        try:
            return any(marker in (m.get('text') or '') for m in self.agents.thread(DESK, 'max')[-30:] if m.get('role') == 'agent')
        except Exception:  # noqa: BLE001 - unknown counts as posted, so it is never posted twice
            return True

    def tick(self, now_utc=None):
        """One background step, safe to call often and safe after a crash. Each day's check runs once from 09:00 Dubai,
        in saved steps: assess and plan (saved), queue the planned nudges (same keys every time), post on Max's desk
        (checked against his desk first), then read back how each nudge went."""
        now_utc = now_utc or self.clock()
        local = now_utc.astimezone(DUBAI)
        key = f'oversight-{local.date().isoformat()}.json'
        if not self.lock.acquire(blocking=False):
            return None
        try:
            rec = self._read(key, None)
            if rec is None:
                if local.time() < time(CHECK_HOUR):
                    return None
                a = self.assess(now_utc)
                rec = dict(a, nudges=self.plan(a['findings'], now_utc) if local.time() < time(LAST_QUEUE_HOUR) else [],
                           queued=False, posted=False)
                rec['text'] = self.summary(a, rec['nudges'])
                rec['marker'] = f"(Max's check {a['day_dubai']})"
                self._write(key, rec)              # the plan is saved before anything leaves Josh OS
            if not rec.get('queued'):
                rec = self.queue_planned(rec, now_utc)
                rec['queued'] = True
                self._write(key, rec)
            if not rec.get('posted') and self.agents is not None:
                if not self._already_posted(rec['marker']):
                    self.agents.post('max', rec['text'] + '\n\n' + rec['marker'], notice=False)   # his desk; Max stays off Discord
                rec['posted'] = True
                self._write(key, rec)
            if rec.get('nudges'):
                rec = self.outcomes(rec)
                self._write(key, rec)
            return rec
        finally:
            self.lock.release()

    def loop(self, every=300):
        while True:
            try:
                self.tick()
            except Exception as e:  # noqa: BLE001 - keep running, note why
                try:
                    (self.dir / 'last-error.txt').write_text(f'{now()} {e}', encoding='utf-8')
                except OSError:
                    pass
            self._sleep(every)

    def context(self):
        """For Max's chat: today's saved check, if there is one."""
        rec = self._read(f'oversight-{self.clock().astimezone(DUBAI).date().isoformat()}.json', None)
        if not rec:
            return ''
        nudges = [f"- {NAMES.get(q['agent'], q['agent'])}: {q['key']} is {q.get('state')}" for q in rec.get('nudges') or []]
        return '\n'.join(["Today's oversight check (saved by Josh OS):", rec.get('text', '')] + (['Nudges and their real outcome:'] + nudges if nudges else []))
