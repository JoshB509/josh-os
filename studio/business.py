"""Max, the Business Manager. The daily business brief, Josh's projects and to-dos, and friendly nudges.

Numbers are never typed by a model. They come from the saved Linklr snapshot (studio/scorecard.py) and the direct
YouTube Analytics cache (studio/youtube_cache.py), each shown with its own day, time zone and freshness. Anything a
source doesn't provide is listed as not tracked, never as zero.

Projects and to-dos live in the shared Tasks list (studio/tasks.py). The morning glance excludes task nags;
Max's separate task chat and nudge workflows still use this shared list.
Max only nudges about things Josh OS can show are unfinished. That means a task past its confirmed due date, a task due
today or tomorrow, or a video with a publish date coming up that's still at an early stage. Ideas, snoozed items and
anything done are left alone, and the same nudge isn't repeated within NUDGE_GAP_DAYS.

The brief starts at 07:00 Asia/Dubai (03:00 UTC), within about a second, because the loop wakes on that boundary.
Linklr's refresh starts at 06:45, and at 07:00 the brief waits at most REFRESH_WAIT_S for a refresh still running, then
goes with the last good snapshot, dated. It is sent from Max's own Discord bot to one pinned private channel or owner DM (Data/business/
settings.json). Before anything is sent, the brief's key (brief:YYYY-MM-DD, the Dubai date), its exact text, its sources
and its send state are saved. A restart or a second trigger can't send it twice, and a send with an unclear outcome is
never repeated on its own. This only runs while this PC and Josh OS are on. A brief more than CATCH_UP_HOURS late is
recorded as missed and not sent.
"""
import json
import re
import threading
import uuid
import time as _time
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

from .store import now

UTC = timezone.utc
DUBAI = timezone(timedelta(hours=4), 'Asia/Dubai')  # Dubai has no daylight saving, so a fixed offset is exact
BRIEF_HOUR = 7
CATCH_UP_HOURS = 5          # a brief can still go out until 12:00 Dubai, then it is recorded as missed
QUIET_START, QUIET_END = 21, 7   # no nudges from 21:00 to 07:00 Dubai
NUDGE_GAP_DAYS = 2          # the same nudge isn't repeated for two days
MAX_NUDGES = 3
PREFETCH_MINUTES = 15      # Linklr's refresh starts at 06:45 Dubai
REFRESH_WAIT_S = 120       # at 07:00 the brief waits at most this long for a refresh that is still running
RETRY_MINUTES = 10         # a brief that was blocked before sending is tried again this often, up to MAX_ATTEMPTS
MAX_ATTEMPTS = 6
EARLY_STAGES = ('Idea', 'Scripting', 'Ready to record')
VIDEO_LATE_DAYS = 7
SETTINGS_TEMPLATE = {
    'note': ('Max sends the daily brief only when enabled is true and Josh has approved it. guild_id, channel_id and '
             'bot_user_id are pinned ids, never names. The brief goes nowhere else. members_intent is true only after '
             "Josh turns on Server Members Intent for Max's bot in the Discord Developer Portal."),
    'enabled': False,
    'destination': 'channel',
    'owner_user_id': '',
    'guild_id': '1569401420422058158',
    'guild_name': 'CareerOS Office',
    'channel_id': '',
    'bot_user_id': '',
    'members_intent': False,
}
CLICK_SOURCES = {'youtube_video': 'YouTube videos', 'community_post': 'community posts', 'broadcast_email': 'broadcast emails',
                 'behaviour_email': 'behaviour emails', 'lead_magnet_doc': 'lead magnet doc', 'qr_code': 'QR codes'}
# Josh's own words confirming the course goal (relayed 11 Nov 2026). Saved on the goal in Tasks & Goals as its evidence.
GOAL_CONFIRMATION = ('Josh confirmed this goal on 11 Nov 2026, in his words "Read my connected Linklr aggregate metrics and '
                     'check progress toward USD15,000 per month in course plus upsell revenue, excluding client, Udemy and '
                     'unassigned/other income."')


def load_settings(data_root, create=False):
    """Max's pinned Discord destination. Read on every use, so a change needs no restart."""
    f = Path(data_root) / 'business' / 'settings.json'
    if create and not f.is_file():
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(SETTINGS_TEMPLATE, indent=1), encoding='utf-8')
    try:
        s = json.loads(f.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return dict(SETTINGS_TEMPLATE, enabled=False, error='settings.json is missing or could not be read')
    return dict(SETTINGS_TEMPLATE, **{k: v for k, v in s.items() if k in SETTINGS_TEMPLATE})


def _ts(s):
    if not s:
        return None
    try:
        d = datetime.fromisoformat(str(s).replace('Z', '+00:00'))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=UTC)


def _day(d):
    return f"{d:%a} {d.day} {d:%b}"


def usd(v):
    return 'not reported' if v is None else ('-' if v < 0 else '') + f'${abs(v):,.2f}'


def num(v):
    return 'not reported' if v is None else f'{v:,}'


# Facts ----------------------------------------------------------------------------------------------------------
def linklr_facts(snap, now_utc, goal=None):
    """The latest completed UTC day from the saved Linklr snapshot, with its exact date and any gaps."""
    want = now_utc.date() - timedelta(days=1)
    out = dict(source='Linklr', timezone='UTC', wanted_day=want.isoformat(), notes=[])
    if not snap:
        return dict(out, available=False, reason='No Linklr snapshot has been saved yet.')
    fetched = _ts(snap.get('fetched_at'))
    out['fetched_at'] = snap.get('fetched_at')
    if not fetched:
        return dict(out, available=False, reason="The Linklr snapshot has no read time, so its days can't be dated.")
    # Only days that had ended when Linklr was read are complete.
    day = min(want, fetched.astimezone(UTC).date() - timedelta(days=1))
    rows = {r.get('date'): r for r in snap.get('daily') or [] if isinstance(r, dict)}
    row = rows.get(day.isoformat())
    if row is None:
        return dict(out, available=False, day=day.isoformat(),
                    reason=f'Linklr has no figures for the UTC day {day.isoformat()}.')
    out.update(available=True, day=day.isoformat(), stale=day < want)
    if day < want:
        out['notes'].append(f"Linklr doesn't have {_day(want)} yet.")
    st = snap.get('status') or {}
    end = datetime.combine(day + timedelta(days=1), time(0), UTC)
    for key, what in (('last_email_list_sync_at', 'Email joins and unsubscribes'), ('last_activity_at', 'Clicks and page views')):
        at = _ts(st.get(key))
        if at is None or at < end:
            out['notes'].append(f'{what} may be incomplete, since Linklr synced them before the day ended.')
    g = lambda k: row.get(k)  # noqa: E731
    parts = [g('course_usd'), g('upsell_usd'), g('clients_usd'), g('other_usd')]
    gross = round(sum(parts), 2) if all(isinstance(p, (int, float)) for p in parts) else None
    refunds, net = g('refunds_usd'), g('net_usd')
    if gross is not None and isinstance(refunds, (int, float)) and isinstance(net, (int, float)) and abs(gross - refunds - net) > 0.01:
        out['notes'].append("Linklr's net isn't gross minus refunds for this day.")
    pays = snap.get('course_payments')
    course_buys = None if pays is None else sum(1 for p in pays if str(p.get('at') or '')[:10] == day.isoformat())
    out['figures'] = dict(gross=gross, course=g('course_usd'), upsell=g('upsell_usd'), clients=g('clients_usd'), other=g('other_usd'),
                          refunds=refunds, net=net, transactions=g('purchases'), course_purchases=course_buys,
                          clicks=g('tracked_link_clicks'), new_subscribers=g('new_subscribers'), unsubscribes=g('unsubscribes'),
                          page_views=g('sales_page_views'), visitors=g('sales_page_visitors'), checkout_clicks=g('checkout_clicks'),
                          clicks_by_source=g('clicks_by_source') if isinstance(g('clicks_by_source'), dict) else None)
    # Month to date, course and upsells, on complete days only.
    first = day.replace(day=1)
    days = [first + timedelta(days=i) for i in range((day - first).days + 1)]
    mrows = [rows.get(d.isoformat()) for d in days]
    if all(isinstance(r, dict) for r in mrows):
        out['mtd'] = dict(start=first.isoformat(), end=day.isoformat(),
                          course_upsell=round(sum((r.get('course_usd') or 0) + (r.get('upsell_usd') or 0) for r in mrows), 2),
                          goal=(goal or {}).get('amount'), goal_title=(goal or {}).get('title', ''),
                          goal_confirmed=bool((goal or {}).get('confirmed')))
    return out


# The id Josh OS gives the seeded goal (uuid5 of dajoshi/tasks/course-revenue, see studio/tasks.py).
SEEDED_GOAL_ID = str(uuid.uuid5(uuid.NAMESPACE_URL, 'dajoshi/tasks/course-revenue'))


def goal_info(tasks):
    """The monthly course and upsell goal from Tasks & Goals, and whether Josh has confirmed it.

    Josh confirmed the USD 15,000 course and upsell goal in his own words on 11 Nov 2026, and Business records that on
    the goal as its evidence (GOAL_CONFIRMATION). A goal also counts as confirmed once Josh has saved any change to it.
    Only a seeded goal nobody has touched or confirmed is unconfirmed. There is no fallback figure, so a missing or
    finished goal is never invented."""
    for t in tasks:
        text = str(t.get('title') or '')
        m = re.search(r'USD\s*([\d,]+)\s*/\s*month', text)
        if m and t.get('kind') == 'goal' and 'course' in text.lower() and t.get('status') != 'Done':
            untouched = t.get('id') == SEEDED_GOAL_ID and int(t.get('revision') or 1) == 1
            said = str(t.get('evidence') or '').startswith('Josh confirmed')
            return dict(amount=int(m.group(1).replace(',', '')), title=text, id=t.get('id'), confirmed=said or not untouched,
                        evidence=t.get('evidence') or '')
    return None


def utc_window(first, last=None):
    """A UTC day (or run of days) with the same span in Dubai, where each UTC day runs 04:00 to 04:00."""
    last = last or first
    end = last + timedelta(days=1)
    return (f"{_day(first)}{'' if last == first else ' to ' + _day(last)} UTC, which is 04:00 {_day(first)} to 04:00 "
            f"{_day(end)} in Dubai")


def click_sources(by, total):
    """Linklr's tracked clicks by source for the day, largest first, as Linklr saved them. Returns (line, note)."""
    if by is None:
        return 'Clicks by source were not reported for this day.', ''
    if not by:
        return ('No clicks by source were reported for this day.' if not total else
                'Linklr saved no source for these clicks.'), ''
    parts = sorted(by.items(), key=lambda kv: (-(kv[1] or 0), kv[0]))
    line = 'Clicks by source · ' + ' · '.join(f"{CLICK_SOURCES.get(k, k.replace('_', ' '))} {num(v)}" for k, v in parts)
    added = sum(v for _, v in parts if isinstance(v, (int, float)))
    note = '' if total is None or added == total else f'The sources add up to {num(added)} of the {num(total)} tracked clicks.'
    return line, note


def youtube_facts(yt, attempt=None):
    """The newest day YouTube Analytics has reported, in Pacific Time. Missing days are not zeros."""
    out = dict(source='YouTube Analytics', timezone='America/Los_Angeles', notes=[])
    if not yt or not yt.get('daily'):
        return dict(out, available=False, reason='No YouTube Analytics report has been saved yet.')
    through = yt.get('data_through')
    row = next((r for r in yt['daily'] if r.get('date') == through), None)
    if row is None:
        return dict(out, available=False, reason='The YouTube report has no row for its own newest day.')
    out.update(available=True, day=through, fetched_at=yt.get('fetched_at'), views=row.get('views'),
               net_subscribers=row.get('net_subscribers'), subscribers_gained=row.get('subscribers_gained'),
               subscribers_lost=row.get('subscribers_lost'))
    out['missing_days'] = yt.get('missing_days')   # kept for chat; the date in the heading already shows the lag
    if (attempt or {}).get('state') == 'failed':
        out['notes'].append('The last YouTube refresh failed.')
    return out


def review_facts(rv, attempt, day):
    """Learner reviews submitted on one UTC day, from the last complete CareerOS read (studio/scorecard.py)."""
    out = dict(source='CareerOS', timezone='UTC', day=day.isoformat(), notes=[])
    if not rv:
        return dict(out, available=False, reason=(attempt or {}).get('reason') or 'CareerOS reviews have not been read yet.')
    taken = _ts(rv.get('taken_at'))
    out.update(available=True, read_at=rv.get('taken_at'), total=len(rv.get('items') or []),
               count=sum(1 for x in rv.get('items') or [] if str(x.get('submitted_at') or '')[:10] == day.isoformat()))
    if taken is None or taken < datetime.combine(day + timedelta(days=1), time(0), UTC):
        out['notes'].append(f'Reviews were last read before {_day(day)} ended, so that day may be missing some.')
    if (attempt or {}).get('state') == 'failed':
        out['notes'].append('The newest reviews read failed, so the count is from the last complete one.')
    return out


def carousel_lines(g):
    """Studio post impressions from two complete reads (studio/carousel_counts.py). Always impressions, never views, with
    every post on the channel kept apart from the carousels Josh OS tracks."""
    if not g:
        return []
    if g['state'] == 'baseline':
        return ["Post impressions · first Studio count taken, so daily figures start with Studio's next day"]
    if g['state'] == 'no_new_day':
        return [f"Post impressions · Studio has no day after {_day(date.fromisoformat(g['through']))} yet"]
    if g['state'] != 'ok':
        return ['Post impressions · not available today']
    d = date.fromisoformat(g['through'])
    span = _day(d) if g['daily'] else f"{_day(date.fromisoformat(g['since']) + timedelta(days=1))} to {_day(d)}, {g['days']} days"
    ch = num(g['channel_impressions']) if g['channel_impressions'] is not None else 'not counted'
    return [f"Post impressions {ch} on all posts · {num(sum(g['tracked'].values()))} on {len(g['tracked'])} tracked carousels ({span}, Studio)"]


def review_lines(r):
    if not r:
        return []
    if not r.get('available'):
        return ['Reviews · not available today']
    return [f"Reviews {num(r['count'])} · {num(r['total'])} in all"]


# Nudges ---------------------------------------------------------------------------------------------------------
def nudge_candidates(tasks, projects, today):
    """Things Josh OS can show are unfinished. Returns [(key, reason, item)] with the most pressing first."""
    out = []
    for t in tasks:
        if t.get('kind', 'task') != 'task' or t.get('status') in ('Done', 'Idea', 'Later'):
            continue
        if t.get('snoozed_until') and t['snoozed_until'] > today.isoformat():
            continue
        due = t.get('due_date')
        if not due:
            continue
        d = date.fromisoformat(due)
        if d < today:
            out.append((0, (today - d).days, f"task:{t['id']}:overdue", 'overdue', t))
        elif d <= today + timedelta(days=1):
            out.append((1, 0, f"task:{t['id']}:due", 'due', t))
    for p in projects:
        if p.get('archived') or p.get('stage') == 'Published' or not p.get('publish_date'):
            continue
        try:
            d = date.fromisoformat(p['publish_date'][:10])
        except ValueError:
            continue
        # A publish date more than VIDEO_LATE_DAYS gone is usually an old plan, not a live commitment, so it isn't nudged.
        if today - timedelta(days=VIDEO_LATE_DAYS) <= d < today and p.get('stage') not in ('Scheduled',):
            out.append((2, (today - d).days, f"video:{p['id']}:late", 'video_late', p))
        elif today <= d <= today + timedelta(days=5) and p.get('stage') in EARLY_STAGES:
            out.append((3, (d - today).days, f"video:{p['id']}:early", 'video_early', p))
    # Josh's own listed to-dos come first, then videos. Within each, the most overdue or the soonest first.
    out.sort(key=lambda x: (x[0], -x[1] if x[0] in (0, 2) else x[1]))
    return [(k, r, i) for _, _, k, r, i in out]


OPENERS = ('Bro,', 'My guy,', 'Josh,')


def nudge_line(reason, item, today, n=0):
    """One short line. It says what the list or the video board shows, never that the work wasn't done."""
    o = OPENERS[n % len(OPENERS)]
    # chosen_title is the id of the chosen title, so the text is looked up (it used to show the id itself).
    chosen = next((t.get('text') for t in item.get('titles') or [] if t.get('id') == item.get('chosen_title')), None)
    name = item.get('title') or chosen or item.get('name') or 'this'
    if reason == 'overdue':
        return (f"{o} \"{name}\" is still open, due {_day(date.fromisoformat(item['due_date']))}. Done? Tell me and I'll close it. "
                'If not, give me a new date.')
    if reason == 'due':
        return f"{o} \"{name}\" is due {'today' if item['due_date'] == today.isoformat() else 'tomorrow'} and still open."
    if reason == 'video_late':
        return (f"{o} the board still has \"{name}\" at {item.get('stage')}, with a publish date of "
                f"{_day(date.fromisoformat(item['publish_date'][:10]))}. Out of date? Tell me and I'll fix it.")
    return (f"{o} \"{name}\" goes out {_day(date.fromisoformat(item['publish_date'][:10]))} and the board still has it at "
            f"{item.get('stage')}.")


def next_wake(now_utc):
    """Seconds until the loop should look again. At most a minute, and never past 06:45 or 07:00 Dubai, so the
    prefetch and the brief start within about a second of those times."""
    local = now_utc.astimezone(DUBAI)
    marks = []
    for days in (0, 1):
        brief = datetime.combine(local.date() + timedelta(days=days), time(BRIEF_HOUR), DUBAI)
        marks += [brief - timedelta(minutes=PREFETCH_MINUTES), brief]
    soon = min((m - local).total_seconds() for m in marks if m > local)
    return max(0.5, min(60.0, soon))


def in_quiet_hours(at):
    h = at.astimezone(DUBAI).hour
    return h >= QUIET_START or h < QUIET_END


# The brief ------------------------------------------------------------------------------------------------------
def kit_lines(g):
    """The email opens line from two Kit snapshots (studio/counters.py), plus a warning only when one is needed."""
    if not g:
        return []
    if g['state'] == 'baseline':
        return ['Email opens · first count taken today, so the daily figure starts tomorrow']
    if g['state'] != 'ok':
        return ['Email opens · not available today']
    since = _ts(g['since']).astimezone(DUBAI)
    until = _ts(g['until']).astimezone(DUBAI)
    when = f"{since:%Y-%m-%d %H:%M} to {until:%Y-%m-%d %H:%M} Dubai; {g['hours']} hours of observed Kit growth"
    out = [f"Email opens {num(g['opens'])} · Email clicks {num(g['clicks'])} ({when})"]
    out.append('Opens sum unique openers per broadcast; clicks are total clicks, not CTR.')
    left = g.get('decreased', 0) + g.get('no_baseline', 0)
    if left:
        out.append(f"-# {left} email{'s' if left != 1 else ''} left out, with no matching earlier count.")
    return out


def render_brief(day_dubai, money, yt, nudges, open_count, overdue_count, ideas_count, kit=None, reviews=None, carousels=None, learning=None):
    """Morning glance: one metric per row; precise periods/definitions live in sources."""
    def value(v, cash=False, signed=False):
        if v is None:
            return 'unavailable'
        return usd(v) if cash else (f'{v:+,}' if signed else num(v))

    def row(label, v, cash=False, signed=False, partial=False):
        return f'{label}: {value(v, cash, signed)}' + (' (partial)' if partial and v is not None else '')

    f = money.get('figures', {}) if money.get('available') else {}
    by = f.get('clicks_by_source') or {}
    yt = yt if yt.get('available') else {}
    kit, carousels, reviews = kit or {}, carousels or {}, reviews or {}
    kit_daily = kit.get('state') == 'ok' and kit.get('daily') is True
    post_daily = carousels.get('state') == 'ok' and carousels.get('daily') is True
    kit_partial = any(kit.get(k) for k in ('decreased', 'no_baseline', 'missing'))
    post_partial = any(carousels.get(k) for k in ('decreased', 'no_baseline', 'missing'))
    email_partial = any('Email joins' in n for n in money.get('notes', []))
    click_partial = any('Clicks and page views' in n for n in money.get('notes', []))
    L = [f'**Max · {_day(day_dubai)}**']
    older = []
    if money.get('stale') and money.get('day'):
        older.append('sales, email list and link clicks '+_day(date.fromisoformat(money['day'])))
    if yt.get('day') and (day_dubai - date.fromisoformat(yt['day'])).days > 7:
        older.append('YouTube '+_day(date.fromisoformat(yt['day'])))
    if post_daily and carousels.get('through') and (day_dubai-date.fromisoformat(carousels['through'])).days > 7:
        older.append('post impressions '+_day(date.fromisoformat(carousels['through'])))
    if (learning or {}).get('stale'):
        older.append('learning '+_day(date.fromisoformat(learning['requested']['start'][:10])))
    if older:
        L.append('Older data: '+'; '.join(older))
    L += ['', '**💰 Sales**', row('Total sales', f.get('transactions')), row('Revenue', f.get('gross'), cash=True),
          row('Course sales', f.get('course_purchases')), row('Course revenue', f.get('course'), cash=True),
          row('Client sales', f.get('client_purchases')), row('Client revenue', f.get('clients'), cash=True),
          row('Upsell sales', f.get('upsell_purchases')), row('Upsell revenue', f.get('upsell'), cash=True)]
    if f.get('other'):
        L.append(row('Other revenue', f['other'], cash=True))
    if f.get('refunds'):
        L += [row('Refunds', f['refunds'], cash=True), row('Net revenue', f.get('net'), cash=True)]
    L += ['', '**▶️ YouTube**', row('Views', yt.get('views')),
          row('Subscribers gained', yt.get('subscribers_gained')), row('Subscribers lost', yt.get('subscribers_lost')),
          row('Net subscribers', yt.get('net_subscribers'), signed=True),
          row('Video link clicks', by.get('youtube_video'), partial=click_partial), row('Downloads', None),
          '', '**🖼️ Carousels**',
          row('Tracked carousel impressions', sum(carousels.get('tracked', {}).values()) if post_daily else None, partial=post_partial),
          row('All-post impressions', carousels.get('channel_impressions') if post_daily else None),
          row('Community-post link clicks', by.get('community_post'), partial=click_partial), row('Downloads', None),
          '', '**📧 Emails**', row('New subscribers', f.get('new_subscribers'), partial=email_partial),
          row('Unsubscribes', f.get('unsubscribes'), partial=email_partial)]
    joins, lost = f.get('new_subscribers'), f.get('unsubscribes')
    L += [row('Net subscribers', joins-lost if joins is not None and lost is not None else None, signed=True, partial=email_partial),
          row('Opens', kit.get('opens') if kit_daily else None, partial=kit_partial),
          row('Clicks', kit.get('clicks') if kit_daily else None, partial=kit_partial)]
    metrics = ((learning or {}).get('summary') or {}).get('metrics', {}) if (learning or {}).get('available') else {}
    def activity(label, key):
        metric = metrics.get(key, {})
        v = metric.get('value') if metric.get('status') in (('available', 'partial', 'tracked') if key == 'active_learners' else ('available', 'partial')) else None
        return row(label, v, partial=metric.get('status') == 'partial')
    L += ['', '**🎓 Learning**', activity('Minutes learned','minutes_learned'), activity('Active learners','active_learners'), row('Comments',None)]
    if learning is None:
        L.append(row('Reviews', reviews.get('count') if reviews.get('available') else None,
                     partial=any('before' in n for n in reviews.get('notes', []))))
    else:
        L.append(activity('Reviews','new_reviews'))
    L.append(activity('Questions','new_questions'))
    return '\n'.join(L)


def counter_source(result, source):
    """Provenance from the exact counter pair used to render the brief; never the build time."""
    result = result or {}
    out = dict(source=source, state=result.get('state', 'unavailable'), reason=result.get('reason'),
               observation=dict(start=result.get('previous_taken_at'), end=result.get('taken_at')))
    if source == 'Kit':
        out['period'] = dict(basis='observed counter growth', start=result.get('since'),
                             end=result.get('until'), hours=result.get('hours'))
    else:
        out['period'] = dict(basis='YouTube Studio reporting days', start_exclusive=result.get('since'),
                             end_inclusive=result.get('through'), days=result.get('days'))
    out['metrics'] = {k: result[k] for k in ('opens', 'clicks', 'broadcasts', 'channel_impressions', 'tracked') if k in result}
    out['coverage'] = {k: result[k] for k in ('daily', 'decreased', 'no_baseline', 'missing') if k in result}
    return out


class Business:
    def __init__(self, store, data_root, scorecard=None, tasks=None, bridge=None, root=None, counters=None, carousel_counts=None, learning_activity=None):
        self.store, self.scorecard, self.tasks, self.bridge = store, scorecard, tasks, bridge
        from .counters import Counters
        # Kit broadcast snapshots (studio/counters.py). Off when there is no repo root to run Claude Code from.
        self.counters = counters or (Counters(store, root) if root else None)
        # Studio post impressions (studio/carousel_counts.py). Off without a repo root, like Kit.
        from .carousel_counts import CarouselCounts
        self.carousel_counts = carousel_counts if carousel_counts is not None else (CarouselCounts(store, root) if root else None)
        self._kit_job = self._carousel_job = None
        from .learning_activity import LearningActivity
        self.learning_activity = learning_activity or (LearningActivity(root, data_root) if root else None)
        self._learning_job = None
        self.dir = Path(data_root) / 'business'
        self.dir.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self._sleep, self._prefetched = _time.sleep, None
        with store.connect() as c:
            c.execute('CREATE TABLE IF NOT EXISTS business_outbox(key TEXT PRIMARY KEY,body TEXT NOT NULL)')
            c.execute('CREATE TABLE IF NOT EXISTS business_nudges(key TEXT PRIMARY KEY,body TEXT NOT NULL)')
            # A send that was running when Josh OS stopped may or may not have reached Discord.
            for r in c.execute("SELECT key,body FROM business_outbox").fetchall():
                b = json.loads(r['body'])
                if b.get('state') == 'sending':
                    b.update(state='uncertain', reason='Josh OS stopped while this was sending, so it is not sent again.', updated_at=now())
                    c.execute('UPDATE business_outbox SET body=? WHERE key=?', (json.dumps(b), r['key']))
        self._record_goal_confirmation()

    def _record_goal_confirmation(self):
        """Save Josh's confirmation of the course goal on the goal itself, once. Only while the goal is still exactly as
        Josh OS seeded it, so a goal Josh has since edited, finished or deleted is left as he left it, and a deleted
        goal is never brought back."""
        if not self.tasks:
            return None
        t = next((x for x in self.tasks.all()['items'] if x['id'] == SEEDED_GOAL_ID), None)
        if not t or int(t.get('revision') or 1) != 1 or t.get('evidence') or t.get('status') == 'Done':
            return None
        try:
            return self.tasks.save(t['id'], {'evidence': GOAL_CONFIRMATION, 'owner': 'Josh'}, 1)
        except Exception:  # noqa: BLE001 - changed in another window at the same moment; Josh's change wins
            return None

    # Settings -------------------------------------------------------------------------------------------------
    def settings(self):
        return load_settings(self.dir.parent, create=True)

    # Reading ---------------------------------------------------------------------------------------------------
    def _tasks(self):
        return self.tasks.all()['items'] if self.tasks else []

    def _goal(self):
        return goal_info(self._tasks())

    def facts(self, now_utc=None):
        now_utc = now_utc or datetime.now(UTC)
        sc = self.scorecard
        snap = sc.snapshot() if sc else None
        yt = sc.youtube.snapshot() if sc else None
        yt_attempt = sc.youtube.attempt() if sc else None
        money = linklr_facts(snap, now_utc, self._goal())
        reviews = None
        if sc is not None and hasattr(sc, 'reviews'):
            day = date.fromisoformat(money.get('day') or money['wanted_day'])
            reviews = review_facts(sc.reviews(), sc.reviews_attempt(), day)
        return dict(money=money, youtube=youtube_facts(yt, yt_attempt), reviews=reviews,
                    learning=self.learning_activity.facts(now_utc) if getattr(self, 'learning_activity', None) else None,
                    linklr_attempt=sc.attempt() if sc else None)

    def nudge_state(self):
        with self.store.connect() as c:
            return {r['key']: json.loads(r['body']) for r in c.execute('SELECT key,body FROM business_nudges')}

    def pick_nudges(self, now_utc):
        """The nudges for this moment, after snoozes, quiet hours and the repeat gap. Returns [(key, line)]."""
        if in_quiet_hours(now_utc):
            return []
        today = now_utc.astimezone(DUBAI).date()
        state = self.nudge_state()
        picked = []
        for key, reason, item in nudge_candidates(self._tasks(), self.store.projects(), today):
            s = state.get(key) or {}
            if s.get('snoozed_until') and s['snoozed_until'] > today.isoformat():
                continue
            last = s.get('last')
            if last and (today - date.fromisoformat(last)).days < NUDGE_GAP_DAYS:
                continue
            picked.append((key, nudge_line(reason, item, today, len(picked) + int(today.toordinal()))))
            if len(picked) >= MAX_NUDGES:
                break
        return picked

    def mark_nudged(self, keys, day):
        with self.store.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            for k in keys:
                row = c.execute('SELECT body FROM business_nudges WHERE key=?', (k,)).fetchone()
                b = json.loads(row['body']) if row else {}
                b.update(last=day.isoformat(), count=int(b.get('count') or 0) + 1)
                c.execute('INSERT OR REPLACE INTO business_nudges VALUES(?,?)', (k, json.dumps(b)))

    def snooze(self, key, until):
        date.fromisoformat(until)
        with self.store.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            row = c.execute('SELECT body FROM business_nudges WHERE key=?', (key,)).fetchone()
            b = dict(json.loads(row['body']) if row else {}, snoozed_until=until)
            c.execute('INSERT OR REPLACE INTO business_nudges VALUES(?,?)', (key, json.dumps(b)))
        return b

    def build(self, now_utc=None):
        """The brief for this moment, with everything needed to explain and send it. Sends nothing."""
        now_utc = now_utc or datetime.now(UTC)
        today = now_utc.astimezone(DUBAI).date()
        f = self.facts(now_utc)
        tasks = self._tasks()
        open_tasks = [t for t in tasks if t.get('kind', 'task') == 'task' and t.get('status') not in ('Done', 'Idea')]
        overdue = [t for t in open_tasks if t.get('due_date') and t['due_date'] < today.isoformat()]
        ideas = [t for t in tasks if t.get('status') == 'Idea']
        nudges = []  # Josh's daily metrics glance excludes task nags; separate nudge workflows are unchanged.
        kit = self.counters.growth(today) if self.counters else None
        carousels = self.carousel_counts.growth(today) if self.carousel_counts else None
        text = render_brief(today, f['money'], f['youtube'], [n for _, n in nudges], len(open_tasks), len(overdue), len(ideas), kit,
                            f.get('reviews'), carousels, f.get('learning'))
        from .discord_bridge import split
        return dict(key=f'brief:{today.isoformat()}', kind='brief', day_dubai=today.isoformat(), built_at=now_utc.isoformat(),
                    text=text, parts=split(text), nudge_keys=[k for k, _ in nudges],
                    sources=dict(linklr=f['money'], youtube=f['youtube'], careeros=f.get('reviews') or dict(source='CareerOS', available=False, reason='No reviews source is configured'),
                                 kit=counter_source(kit, 'Kit'), carousel=counter_source(carousels, 'YouTube Studio'), learning=f.get('learning'),
                                 definitions=dict(currency='USD', linklr_period='Complete UTC calendar day; day names start inclusive 00:00 and end exclusive next day 00:00 UTC',
                                     youtube_period='Complete Pacific calendar day; gross gains and losses only when present in the direct Analytics row',
                                     total_sales='All-stream Linklr purchases; revenue is gross across reported streams, not all off-platform revenue',
                                     course_sales='Count of dated course payment records; client/upsell counts unavailable without category transaction evidence',
                                     youtube_clicks='Linklr youtube_video tracked-link clicks; not views or downloads',
                                     carousel_clicks='Linklr community_post tracked-link clicks across community posts; not carousel-only attribution',
                                     carousel_impressions='Tracked carousel delta; all-post impressions is the separate channel total',
                                     email_opens='Unique openers per broadcast, summed across broadcasts; not distinct people across broadcasts',
                                     email_clicks='Kit total click counter growth; not unique people or CTR',
                                     daily_counter_policy='Only daily observation deltas render; multi-day totals are retained in provenance and withheld from the daily glance',
                                     active_learners='CareerOS distinct learners with recorded lesson activity, download starts, questions or reviews; source definition and partial/lower-bound caveat are preserved in learning.summary',
                                     learning_minutes='Visible-tab video playback wall-clock minutes; retain unavailable/null until the endpoint has tracking', comments='Unavailable: no complete daily aggregate',
                                     questions='CareerOS questions first created in the requested Dubai window; public/private counts remain in learning.summary',
                                     reviews='CareerOS reviews first submitted in the requested Dubai window when the aggregate is configured; later edits excluded',
                                     learning_period='Last completed Asia/Dubai calendar day; window completeness does not establish metric tracking completeness',
                                     downloads='Unavailable: resource requests do not establish completed downloads')))


    # The outbox -----------------------------------------------------------------------------------------------
    def record(self, key):
        with self.store.connect() as c:
            row = c.execute('SELECT body FROM business_outbox WHERE key=?', (key,)).fetchone()
        return json.loads(row['body']) if row else None

    def outbox(self, limit=20):
        with self.store.connect() as c:
            rows = [json.loads(r['body']) for r in c.execute('SELECT body FROM business_outbox ORDER BY rowid DESC LIMIT ?', (limit,))]
        return rows

    def _put(self, body):
        body['updated_at'] = now()
        with self.store.connect() as c:
            c.execute('INSERT OR REPLACE INTO business_outbox VALUES(?,?)', (body['key'], json.dumps(body)))
        return body

    def claim(self, key, prepare, now_utc):
        """Save the brief before sending and mark it sending, in one step. Returns the record to send, or None when
        it must not be sent (already sent, unclear, missed, out of attempts, or tried too recently)."""
        with self.store.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            row = c.execute('SELECT body FROM business_outbox WHERE key=?', (key,)).fetchone()
            body = json.loads(row['body']) if row else None
            if body is None:
                body = dict(prepare(), state='prepared', attempts=0, created_at=now(), message_ids=[], history=[])
            elif body.get('state') not in ('prepared', 'blocked', 'failed'):
                return None
            elif 'parts' not in body:
                # Recorded while briefs were switched off, so nothing was prepared yet.
                body = dict(prepare(), **{k: v for k, v in body.items() if k in ('created_at', 'history', 'attempts', 'last_try')},
                            state=body['state'], message_ids=[])
            elif body.get('attempts', 0) >= MAX_ATTEMPTS:
                return None
            elif body.get('last_try') and now_utc - _ts(body['last_try']) < timedelta(minutes=RETRY_MINUTES):
                return None
            body.update(state='sending', attempts=body.get('attempts', 0) + 1, last_try=now_utc.isoformat(), updated_at=now())
            c.execute('INSERT OR REPLACE INTO business_outbox VALUES(?,?)', (key, json.dumps(body)))
        return body

    def finish(self, body, result):
        """Record what Discord said. 'blocked' and 'failed' mean nothing reached Discord, so a later try is allowed."""
        state = result.get('state')
        if state not in ('sent', 'blocked', 'failed', 'uncertain', 'partial'):
            state = 'uncertain'
        body.update(state=state, reason=result.get('reason', ''), message_ids=body.get('message_ids', []) + list(result.get('message_ids') or []),
                    channel_id=result.get('channel_id', body.get('channel_id')), guild_id=result.get('guild_id', body.get('guild_id')),
                    audience=result.get('audience'))
        for field in ('confirmed', 'confirmed_at', 'confirm_error'):
            if field in result:
                body[field] = result[field]
        body.setdefault('history', []).append(dict(at=now(), state=state, reason=result.get('reason', '')[:300],
                                                   message_ids=list(result.get('message_ids') or [])))
        if state in ('sent', 'partial', 'uncertain') and body.get('nudge_keys'):
            # Counted as nudged even when unclear, so Josh never gets the same nudge twice in a row.
            self.mark_nudged(body['nudge_keys'], date.fromisoformat(body['day_dubai']))
        return self._put(body)

    # Scheduling -----------------------------------------------------------------------------------------------
    def due(self, now_utc):
        """The Dubai day whose brief should go out now, or None. Also records a brief that was missed entirely."""
        local = now_utc.astimezone(DUBAI)
        start = datetime.combine(local.date(), time(BRIEF_HOUR), DUBAI)
        if local < start:
            return None
        key = f'brief:{local.date().isoformat()}'
        if local >= start + timedelta(hours=CATCH_UP_HOURS):
            if self.record(key) is None:
                self._put(dict(key=key, kind='brief', day_dubai=local.date().isoformat(), state='missed', attempts=0, message_ids=[],
                               reason=f'Josh OS was not running between {BRIEF_HOUR:02d}:00 and {BRIEF_HOUR + CATCH_UP_HOURS:02d}:00 Dubai, so this brief was not sent late.'))
            return None
        return local.date()

    def tick(self, now_utc=None, refresh=True):
        """One scheduler step. Safe to call any number of times."""
        if getattr(self,'maintenance_hold_dispatch',False):return dict(state='maintenance_hold')
        now_utc = now_utc or datetime.now(UTC)
        day = self.due(now_utc)
        if day is None:
            return None
        key = f'brief:{day.isoformat()}'
        rec = self.record(key)
        if rec and rec.get('state') not in ('prepared', 'blocked', 'failed'):
            return rec
        s = self.settings()
        if not s.get('enabled'):
            if rec is None:
                self._put(dict(key=key, kind='brief', day_dubai=day.isoformat(), state='blocked', attempts=0, message_ids=[],
                               reason='Daily briefs are not switched on yet (Data/business/settings.json enabled is false).'))
            return self.record(key)
        if not self.lock.acquire(blocking=False):
            return None
        try:
            if refresh and (rec is None or 'parts' not in rec):
                started = _time.monotonic()
                self.start_kit(now_utc)
                self.start_carousels(now_utc)
                self.start_learning(now_utc)
                if self.scorecard is not None:
                    self.refresh_sources(now_utc, wait_s=REFRESH_WAIT_S)
                self.wait_kit(REFRESH_WAIT_S - (_time.monotonic() - started))
            body = self.claim(key, lambda: self.build(now_utc), now_utc)
            if body is None:
                return self.record(key)
            if self.bridge is None:
                return self.finish(body, dict(state='blocked', reason='The Discord bridge is not running.'))
            try:
                result = self.bridge.send_fixed('max', s, body['parts'])
            except Exception as e:  # noqa: BLE001 - we can't tell whether anything was posted
                result = dict(state='uncertain', reason=f'The send stopped unexpectedly: {str(e)[:200]}')
            return self.finish(body, result)
        finally:
            self.lock.release()

    def refresh_sources(self, now_utc, wait_s=0):
        """Start a Linklr refresh (the YouTube cache comes with it) if the snapshot was read before today's UTC day began,
        then wait up to wait_s seconds for it. The brief never waits longer. It goes out with the last good snapshot,
        dated, and says what is missing."""
        snap = self.scorecard.snapshot()
        fetched = _ts((snap or {}).get('fetched_at'))
        if fetched is not None and fetched.astimezone(UTC).date() >= now_utc.astimezone(UTC).date():
            return 'fresh'
        refreshing = getattr(self.scorecard, 'refreshing', lambda: False)
        try:
            if not refreshing():
                self.scorecard.refresh(wait=False)
        except Exception:  # noqa: BLE001 - the brief says what's missing instead
            return 'failed'
        waited = 0
        while waited < wait_s and refreshing():
            self._sleep(2)
            waited += 2
        return 'running' if refreshing() else 'done'

    def prefetch(self, now_utc):
        """From 06:45 Dubai (02:45 UTC, after the UTC day has ended) start Linklr's refresh once, so the 07:00 brief rarely waits."""
        local = now_utc.astimezone(DUBAI)
        start = datetime.combine(local.date(), time(BRIEF_HOUR), DUBAI) - timedelta(minutes=PREFETCH_MINUTES)
        if self._prefetched == local.date() or not (start <= local < start + timedelta(minutes=PREFETCH_MINUTES)):
            return None
        self._prefetched = local.date()
        self.start_kit(now_utc)
        self.start_carousels(now_utc)
        self.start_learning(now_utc)
        return self.refresh_sources(now_utc, wait_s=0) if self.scorecard is not None else None

    def start_learning(self, now_utc):
        """Collect only the new aggregate, independently of Linklr refresh freshness."""
        if self.learning_activity is None or (self._learning_job and self._learning_job.is_alive()):
            return None
        have = self.learning_activity.facts(now_utc)
        if have.get('available') and not have.get('stale'):
            return 'have'
        self._learning_job = threading.Thread(target=self.learning_activity.collect, args=(now_utc,), daemon=True, name='max-learning-aggregate')
        self._learning_job.start()
        return 'started'

    def start_kit(self, now_utc):
        """Take today's Kit snapshot in the background, unless today's is already complete or one is running."""
        if self.counters is None or (self._kit_job and self._kit_job.is_alive()):
            return None
        day = now_utc.astimezone(DUBAI).date()
        have = self.counters.get(f'kit:{day.isoformat()}')
        if have and have.get('complete'):
            return 'have'
        self._kit_job = threading.Thread(target=self._collect_kit, args=(day,), daemon=True, name='max-kit-snapshot')
        self._kit_job.start()
        return 'started'

    def start_carousels(self, now_utc):
        """Take today's Studio post impressions read in the background, unless today's is complete or one is running."""
        if self.carousel_counts is None or (self._carousel_job and self._carousel_job.is_alive()):
            return None
        day = now_utc.astimezone(DUBAI).date()
        have = self.carousel_counts.get(f'carousel:{day.isoformat()}')
        if have and have.get('complete'):
            return 'have'
        self._carousel_job = threading.Thread(target=self._collect_carousels, args=(day,), daemon=True, name='max-studio-read')
        self._carousel_job.start()
        return 'started'

    def _collect_carousels(self, day):
        try:
            self.carousel_counts.collect(day)
        except Exception as e:  # noqa: BLE001 - the brief says carousel impressions are not available today
            try:
                (self.dir / 'last-error.txt').write_text(f'{now()} studio read: {e}', encoding='utf-8')
            except OSError:
                pass

    def _collect_kit(self, day):
        try:
            self.counters.collect(day)
        except Exception as e:  # noqa: BLE001 - the brief says email opens are not available today
            try:
                (self.dir / 'last-error.txt').write_text(f'{now()} kit snapshot: {e}', encoding='utf-8')
            except OSError:
                pass

    def wait_kit(self, seconds):
        """Waits for the Kit snapshot and the Studio read together, never longer than seconds in all."""
        end = _time.monotonic() + max(0.0, seconds)
        for job in (self._kit_job, self._carousel_job, self._learning_job):
            if job and job.is_alive():
                job.join(max(0.0, end - _time.monotonic()))

    def loop(self):
        while True:
            at = datetime.now(UTC)
            try:
                self.prefetch(at)
                self.tick(at)
            except Exception as e:  # noqa: BLE001 - keep the loop alive, note the error
                try:
                    (self.dir / 'last-error.txt').write_text(f'{now()} {e}', encoding='utf-8')
                except OSError:
                    pass
            self._sleep(next_wake(datetime.now(UTC)))

    # Chat --------------------------------------------------------------------------------------------------------
    def context(self, now_utc=None):
        """What Max sees on every message, with sources and dates, so answers quote real figures."""
        now_utc = now_utc or datetime.now(UTC)
        today = now_utc.astimezone(DUBAI).date()
        b = self.build(now_utc)
        lines = [f'Today in Dubai is {today.isoformat()} ({now_utc.astimezone(DUBAI):%H:%M}). The brief as it would be built right now:',
                 '--- brief ---', b['text'], '--- end of brief ---', '',
                 'Learning aggregate provenance (source period, metric tracking status and definitions; not delivery proof):',
                 json.dumps(b['sources'].get('learning'), ensure_ascii=False), '',
                 'Projects and to-dos in the shared Tasks list. id | kind | status | title | owner | due | snoozed until | next step | evidence:']
        for t in self._tasks():
            lines.append(f"- {t['id']} | {t.get('kind')} | {t.get('status')} | {t.get('title')} | {t.get('owner') or '-'} | "
                         f"{t.get('due_date') or 'no date'} | {t.get('snoozed_until') or '-'} | {(t.get('next_step') or '')[:160]} | {(t.get('evidence') or '')[:160]}")
        recent = [o for o in self.outbox(5)]
        if recent:
            lines += ['', 'Recent briefs. key | state | message ids | reason:']
            lines += [f"- {o['key']} | {o.get('state')} | {','.join(o.get('message_ids') or []) or '-'} | {(o.get('reason') or '')[:120]}" for o in recent]
        return '\n'.join(lines)

    def apply_todos(self, changes, by='max'):
        """Create, update, complete or snooze items from chat. Returns (saved entries, lines showing each changed record)."""
        saved, lines = [], []
        if not self.tasks:
            return saved, lines
        current = {t['id']: t for t in self._tasks()}
        for ch in (changes or [])[:15]:
            if not isinstance(ch, dict):
                continue
            action = ch.get('action')
            fields = {k: str(ch[k]) for k in ('title', 'kind', 'status', 'next_step', 'due_date', 'owner', 'evidence', 'snoozed_until', 'link')
                      if k in ch and ch[k] is not None}
            try:
                if action == 'create':
                    fields.setdefault('owner', 'Josh')
                    t = self.tasks.create(fields)
                elif action in ('update', 'complete', 'snooze', 'reopen'):
                    t = current.get(str(ch.get('id') or ''))
                    if t is None:
                        raise KeyError('That item is not in the list.')
                    if action == 'complete':
                        fields['status'] = 'Done'
                    elif action == 'reopen':
                        fields.setdefault('status', 'Next')
                    elif action == 'snooze' and not fields.get('snoozed_until'):
                        raise ValueError('Say until when.')
                    t = self.tasks.save(t['id'], fields, t['revision'])
                elif action == 'snooze_video':
                    self.snooze(f"video:{ch.get('id')}:early", str(ch.get('snoozed_until')))
                    self.snooze(f"video:{ch.get('id')}:late", str(ch.get('snoozed_until')))
                    saved.append(dict(id=str(ch.get('id')), title='video nudge', action='snoozed', kind='todo'))
                    lines.append(f"Snoozed nudges about that video until {ch.get('snoozed_until')}.")
                    continue
                else:
                    continue
                current[t['id']] = t
                saved.append(dict(id=t['id'], title=t['title'], action='created' if action == 'create' else action + 'd', kind='todo'))
                lines.append(f"{t['title']} · {t['status']} · owner {t.get('owner') or '-'} · due {t.get('due_date') or 'no date'}"
                             + (f" · snoozed until {t['snoozed_until']}" if t.get('snoozed_until') else '')
                             + (f" · next step {t['next_step']}" if t.get('next_step') else ''))
            except (KeyError, ValueError) as e:
                saved.append(dict(id=str(ch.get('id') or ''), title=str(ch.get('title') or 'to-do')[:80], action='failed', kind='todo',
                                  error=str(e).strip("'\"")[:200]))
            except Exception as e:  # noqa: BLE001 - a Conflict means it changed elsewhere
                saved.append(dict(id=str(ch.get('id') or ''), title=str(ch.get('title') or 'to-do')[:80], action='failed', kind='todo',
                                  error=str(e)[:200]))
        return saved, lines


TODO_PROPS = {
    'todos': {
        'type': 'array',
        'description': ('Changes to Josh\'s projects and to-dos in the shared Tasks list, only when Josh asks for them in this '
                        'message. action is create, update, complete, reopen, snooze (with snoozed_until YYYY-MM-DD) or '
                        'snooze_video (id is the video project id). Use the item id from your context for anything but create. '
                        'Set due_date only to a date Josh gave you. Your own improvement ideas go in with status Idea, '
                        'never as a commitment. Leave empty otherwise.'),
        'items': {'type': 'object', 'properties': {
            'action': {'type': 'string', 'enum': ['create', 'update', 'complete', 'reopen', 'snooze', 'snooze_video']},
            'id': {'type': 'string'}, 'title': {'type': 'string'}, 'kind': {'type': 'string', 'enum': ['task', 'goal']},
            'status': {'type': 'string', 'enum': ['Next', 'In progress', 'Waiting', 'Later', 'Idea', 'Done']},
            'next_step': {'type': 'string'}, 'due_date': {'type': 'string'}, 'owner': {'type': 'string'},
            'evidence': {'type': 'string', 'description': 'Where this came from, like "Josh in Discord, 12 Nov".'},
            'snoozed_until': {'type': 'string'}, 'link': {'type': 'string', 'description': 'Relevant task source link supplied or verified for this task.'}},
            'required': ['action']},
    },
}

RULES = [
    'Business numbers: quote only the figures in the brief above, its saved CareerOS aggregate provenance, or what your read-only Linklr and Kit tools return in '
    'this answer. Name the source and the exact day with its time zone (Linklr days are UTC calendar days, YouTube days '
    'are Pacific). Never call a UTC day "the last 24 hours" or a Dubai day. Keep paid course purchases apart from client '
    'payments and from all transactions. Say gross, refunds and net in US dollars. If a figure is missing or old, say so. '
    'Never guess, estimate or fill a gap with zero.',
    'What the brief leaves out, for when Josh asks. Carousel views have no counter Josh OS can read automatically '
    '(YouTube has no API for community posts, and the saved carousel checks are typed in by hand). Email opens in the '
    'brief are growth between two complete daily Kit snapshots (unique openers per broadcast, added across broadcasts), '
    'not opens counted by the hour they happened, and they only show once two complete snapshots exist. Learning reviews '
    'and questions follow the CareerOS aggregate period described below. Post impressions in the '
    "brief come from two complete daily reads of YouTube Studio's lifetime Post impressions table, by Studio's own "
    'reporting day. They are impressions (times a post was shown), never views. "All posts" is every post on the '
    'channel, and "tracked carousels" is only the posts Josh OS tracks. The first read was a baseline on 13 Nov 2026 and '
    'is never a daily figure. No API gives post figures; Studio is the only source. Saved carousel checks are '
    "each post's running total at its newest check, by the date it was published. "
    'A resource request or download start is not a finished download. Clicks are not downloads. Linklr has upsell '
    'revenue but no upsell count, so never give one.',
    'CareerOS learning: use the validated aggregate from get_learning_activity_summary and its exact saved window, '
    'normally the last completed Asia/Dubai calendar day (start-inclusive, end-exclusive). Never relabel an older '
    'last-good period as today, or substitute the legacy UTC reviews cache into the Dubai aggregate. New questions '
    'are first creations and new reviews are first submissions; later review edits are excluded. Admins, support '
    'staff, reporting viewers and test accounts are excluded as stated by the source. Active learners is distinct '
    'learners with the lesson activity, download starts, questions or reviews named in the source definition; it is '
    'not a count of lesson opens alone. Preserve each metric status, null value and tracked_since. A partial count '
    'is a lower bound because historical opens/playback and earlier progress saves can be missing. window_complete '
    'does not mean tracking is complete. Minutes learned measures visible-tab video playback wall-clock time when '
    'the source has tracked it; if status is unavailable or value is null, say unavailable, never zero. Do not infer '
    'minutes from completions or video length. Comments and completed downloads remain unavailable without their '
    'own verified aggregates. A failed collection preserves last-good data; explain its age/status when relevant. '
    'Keep the daily glance compact; exact periods, definitions and caveats belong in provenance or an answer to a '
    'specific question, not repeated footnotes.',
    'The course goal is USD 15,000 a month in course plus upsell revenue, which Josh confirmed. Client, Udemy and '
    'unassigned payments do not count toward it.',
    'To-dos: only nudge Josh about what the list shows is unfinished, and check its status first. Never say he is behind '
    'on something or that a video has not started unless the list or the video board shows it. Keep nudges short, warm '
    'and direct, like a mate who has his back ("Bro", "my guy" are fine), with the next step. Never rude.',
    'Your ideas for the business are proposals. Add them with status Idea and let Josh decide.',
    'You never send emails, message clients or learners, publish anything, spend money or change any account.',
]
