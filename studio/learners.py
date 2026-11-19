"""Course progress and sign-in history for one person at a time, read from CareerOS for the Customer center.

Josh OS has no direct CareerOS connection. Claude Code holds the CareerOS sign-in, and the only approved use is
Anouk's seven named read tools (agent.CAREEROS_READ) behind the exact-name read guard. So a lookup is a short
Claude run with exactly those tools and read_only=True, the same path Anouk's chat uses. Nothing new is granted,
and nothing in CareerOS can change.

Limits, stated on the page too:
- One person per lookup, started by Josh. There is no roster or bulk progress read.
- The counts are copied from the tool results by Claude, then checked here for shape and sense. Josh OS works out each
  course's completion itself, as completed lessons the learner can still open divided by the published lessons they can
  open today (CareerOS's lessons_available_to_learner). CareerOS's own percent_complete isn't used, and without both
  counts there's no percentage. Locked lessons aren't counted by CareerOS, so they're outside the denominator.
- Sign-in and activity history only goes back as far as CareerOS tracked it. The tracking start dates come with each
  result so the page can show the gap instead of an empty history.
- Results are cached for CACHE_HOURS and always shown with their fetch time. Failures aren't cached.
"""
import json
import re
import threading
from datetime import datetime, timedelta, timezone

from .agent import AgentError, CAREEROS_READ
from .store import now

CACHE_HOURS = 12
EMAIL = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')
STATUSES = ('found', 'not_found', 'ambiguous', 'unavailable')
TEXT = {'type': ['string', 'null']}
COUNT = {'type': ['integer', 'null']}
SCHEMA = {
    'type': 'object',
    'properties': {
        'reply': {'type': 'string', 'description': 'One plain sentence saying what you found.'},
        'status': {'type': 'string', 'enum': list(STATUSES)},
        'learner': {'type': 'object', 'properties': {
            'name': TEXT, 'email': TEXT, 'signed_up_at': TEXT, 'last_sign_in_at': TEXT, 'last_app_activity_at': TEXT,
            'recorded_sign_ins': COUNT, 'sign_in_tracking_began': TEXT}},
        'access': {'type': 'array', 'items': {'type': 'object', 'properties': {
            'item': TEXT, 'status': TEXT, 'reason': TEXT, 'granted_at': TEXT}}},
        'courses': {'type': 'array', 'description': 'One per entry of progress.courses, counts copied exactly.', 'items': {'type': 'object', 'properties': {
            'course': TEXT, 'lessons_available_to_learner': COUNT, 'lessons_completed': COUNT,
            'completed_without_current_access': COUNT, 'lessons_started_not_completed': COUNT,
            'hidden_from_learners': {'type': ['boolean', 'null']}}}},
        'events': {'type': 'array', 'description': 'Newest first, at most 25, from timeline.events. Date and kind only.', 'items': {
            'type': 'object', 'properties': {'at': TEXT, 'kind': TEXT}}},
        'tracking': {'type': 'array', 'description': 'Copied from timeline.tracking_began.', 'items': {'type': 'object', 'properties': {
            'kind': TEXT, 'live_from': TEXT, 'earliest_backfilled': TEXT}}},
        'tools_used': {'type': 'array', 'items': {'type': 'string'}},
        'problem': TEXT,
    },
    'required': ['reply', 'status', 'tools_used'],
}
SYSTEM = '\n'.join([
    "You look up one learner in CareerOS for Josh's Customer center. Use only the mcp__careeros__ read tools. Never change",
    'learners, access, questions, reviews or messages, and never send anything.',
    'Steps: call find_learners with the email given. If there is no match, status is not_found. If more than one learner',
    'could match, status is ambiguous and you stop. With exactly one match, call get_learner, get_learner_progress and',
    'get_learner_timeline for that learner.',
    'learner comes from get_learner (account, profile.full_name and sign_in_and_activity). access lists learner.access with',
    'item, status, reason and granted_at. courses copies every entry of progress.courses with its counts exactly as returned.',
    'events lists timeline.events newest first, at most 25, with only "at" and "kind". tracking copies timeline.tracking_began.',
    'Copy values exactly. Use null for anything a tool did not return. Never estimate, round, add up or work out a number,',
    'and never copy percent_complete. Leave out question text, review text, notes and any personal detail beyond name and email.',
    'If a tool fails or asks for authentication, status is unavailable and problem says what happened. Never ask for or repeat',
    'tokens. List every tool you called in tools_used.',
])
EVENT_KINDS = {'signed_in', 'lesson_completed', 'lesson_uncompleted', 'access_granted', 'access_revoked', 'access_expiry_changed',
               'file_requested', 'question_posted', 'reply_posted', 'reply_notification_queued', 'review_submitted',
               'review_status_changed', 'email_handed_to_provider'}


def _text(v, limit=200):
    return v.strip()[:limit] if isinstance(v, str) and v.strip() else None


def _count(v):
    return v if type(v) is int and 0 <= v <= 100000 else None


def course(x):
    """One course's completion over the lessons this learner can open today. Only completed lessons they can still
    open count, so the share can't pass 100%. With a missing or zero count there's no percentage."""
    avail, done, lost = _count(x.get('lessons_available_to_learner')), _count(x.get('lessons_completed')), _count(x.get('completed_without_current_access'))
    eligible = None if done is None else max(0, done - (lost or 0))
    ok = avail is not None and avail > 0 and eligible is not None and eligible <= avail
    return dict(course=_text(x.get('course'), 120), available=avail, completed=eligible if ok else None,
                started=_count(x.get('lessons_started_not_completed')), percent=round(100 * eligible / avail) if ok else None,
                hidden=x.get('hidden_from_learners') is True)


def clean(answer, email):
    """Keep only well-formed values. Anything odd becomes null or turns the result into 'unavailable'."""
    a = answer if isinstance(answer, dict) else {}
    status = a.get('status') if a.get('status') in STATUSES else 'unavailable'
    used = [t if t.startswith('mcp__careeros__') else 'mcp__careeros__' + t
            for t in (a.get('tools_used') or []) if isinstance(t, str)]
    used = [t for t in dict.fromkeys(used) if t in CAREEROS_READ]
    problem = _text(a.get('problem'), 400)
    if status != 'unavailable' and 'mcp__careeros__find_learners' not in used:
        status, problem = 'unavailable', 'The lookup did not report a CareerOS search, so nothing is shown.'
    lr = a.get('learner') if isinstance(a.get('learner'), dict) else {}
    courses = [course(x) for x in (a.get('courses') or [])[:12] if isinstance(x, dict) and _text(x.get('course'))]
    courses = [c for c in courses if not c['hidden']]
    # The headline is the course with the most lessons open to them. Every course is listed when the row opens.
    main = max((c for c in courses if c['percent'] is not None), key=lambda c: c['available'], default=None)
    out = dict(email=email, status=status, summary=_text(a.get('reply'), 400) or '', tools_used=used, problem=problem,
               learner=dict(name=_text(lr.get('name')), email=_text(lr.get('email')), signed_up_at=_text(lr.get('signed_up_at'), 40),
                            last_sign_in_at=_text(lr.get('last_sign_in_at'), 40), last_app_activity_at=_text(lr.get('last_app_activity_at'), 40),
                            recorded_sign_ins=_count(lr.get('recorded_sign_ins')), sign_in_tracking_began=_text(lr.get('sign_in_tracking_began'), 40)),
               access=[dict(item=_text(x.get('item')), status=_text(x.get('status'), 40), reason=_text(x.get('reason'), 80),
                            granted_at=_text(x.get('granted_at'), 40))
                       for x in (a.get('access') or [])[:12] if isinstance(x, dict) and _text(x.get('item'))],
               courses=courses, main=main,
               events=[dict(at=_text(x.get('at'), 40), kind=x.get('kind')) for x in (a.get('events') or [])[:25]
                       if isinstance(x, dict) and _text(x.get('at'), 40) and x.get('kind') in EVENT_KINDS],
               tracking=[dict(kind=_text(x.get('kind'), 60), live_from=_text(x.get('live_from'), 40),
                              earliest_backfilled=_text(x.get('earliest_backfilled'), 40))
                         for x in (a.get('tracking') or [])[:20] if isinstance(x, dict) and _text(x.get('live_from'), 40)])
    if status != 'found':
        out.update(access=[], courses=[], main=None, events=[], tracking=[])
    return out


class Learners:
    def __init__(self, store, agents):
        self.store, self.agents = store, agents
        self.running, self.failed, self.lock = {}, {}, threading.Lock()
        with store.connect() as c:
            c.execute('CREATE TABLE IF NOT EXISTS learner_lookups(email TEXT PRIMARY KEY,body TEXT NOT NULL)')

    def all(self):
        with self.store.connect() as c:
            items = {r['email']: json.loads(r['body']) for r in c.execute('SELECT email,body FROM learner_lookups')}
        with self.lock:
            return dict(items=items, running=dict(self.running), failed=dict(self.failed), cache_hours=CACHE_HOURS,
                        source='CareerOS, read one person at a time through Anouk\'s read-only tools')

    def fresh(self, item):
        try:
            return datetime.now(timezone.utc) - datetime.fromisoformat(item['fetched_at']) < timedelta(hours=CACHE_HOURS)
        except (KeyError, TypeError, ValueError):
            return False

    def lookup(self, data):
        """Start a lookup in the background, unless a fresh one is cached or one is already running."""
        email = str((data or {}).get('email') or '').strip().lower()
        if not EMAIL.match(email) or len(email) > 200:
            raise ValueError('Give an email address to look up.')
        with self.store.connect() as c:
            row = c.execute('SELECT body FROM learner_lookups WHERE email=?', (email,)).fetchone()
        cached = json.loads(row['body']) if row else None
        with self.lock:
            if email in self.running:
                return dict(state='running', started_at=self.running[email])
            if cached and self.fresh(cached) and not (data or {}).get('force'):
                return dict(state='cached', item=cached)
            if len(self.running) >= 2:
                raise ValueError('Two lookups are already running. Try again in a minute.')
            started = self.running[email] = now()
            self.failed.pop(email, None)
        threading.Thread(target=self._run, args=(email,), daemon=True).start()
        return dict(state='running', started_at=started)

    def _run(self, email):
        try:
            _, answer = self.agents.run(SYSTEM, f'Look up this learner by email: {email}', SCHEMA,
                                        extra_tools=CAREEROS_READ, full=True, read_only=True, timeout=240)
            item = dict(clean(answer, email), fetched_at=now())
            if item['status'] == 'unavailable':
                raise AgentError(item['problem'] or 'CareerOS could not be read.')
            with self.store.connect() as c:
                c.execute('INSERT OR REPLACE INTO learner_lookups VALUES(?,?)', (email, json.dumps(item)))
        except Exception as e:  # noqa: BLE001 - the page shows the reason, and nothing is cached
            with self.lock:
                self.failed[email] = dict(at=now(), error=str(e)[:300])
        finally:
            with self.lock:
                self.running.pop(email, None)
