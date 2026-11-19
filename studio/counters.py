"""Daily growth from running totals: Kit broadcast opens and clicks (added 11 Nov 2026).

Kit only gives running totals for each broadcast (recipients, unique openers in emails_opened, total_clicks,
unsubscribes). So once a day, at the 06:45 Dubai pre-brief run, Josh OS takes a full snapshot of every sent
broadcast and the brief shows how much each total grew since the last complete snapshot. That is growth observed
between two collection times, not opens counted by the hour they happened.

Rules, so a number is never made up:
- Only a complete snapshot counts. Every page must chain from the first (no cursor) through each end_cursor to a page
  with has_next_page false, with no repeated ids. Anything else is kept but marked incomplete and never compared.
- The first complete snapshot is the baseline. It produces no growth figure and no history is invented before it.
- A broadcast is compared only with its own earlier total. One that is new since the last snapshot counts from zero
  only if Kit says it was sent after that snapshot. One that existed before but wasn't in the last snapshot is left out.
- A broadcast missing from today's snapshot is not counted as zero. A total that went down is left out and flagged.
- A gap longer than a day is shown as the whole gap, never as one day.
- One snapshot per Dubai day. Taking it again the same day does nothing once it is complete.

Carousel views have no counter Josh OS can read automatically (YouTube offers no API for community posts), so they
are not collected here.
"""
import json
import os
import subprocess
from datetime import datetime, timedelta, timezone

from .store import now

UTC = timezone.utc
KIT_TOOL = 'mcp__claude_ai_Kit__get_stats_for_a_list_of_broadcasts'
# A one-shot `claude -p` run doesn't wait for claude.ai connectors before the first turn, so Kit was still 'pending'
# and its tools weren't there (found 11 Nov 2026 on Claude Code 2.1.319). This setting makes that one child process
# wait up to 30 seconds for its connectors at start-up. It changes no settings, credentials or permissions.
STARTUP_WAIT_MS = '30000'
SENT = ('completed', 'sending')
DAY_HOURS = (20, 28)   # a gap in this range is "since yesterday"; anything else is shown as its real length


def _ts(s):
    try:
        d = datetime.fromisoformat(str(s).replace('Z', '+00:00'))
    except (TypeError, ValueError):
        return None
    return d if d.tzinfo else d.replace(tzinfo=UTC)


# Fetching ---------------------------------------------------------------------------------------------------------
def kit_prompt():
    return ('Call the read-only tool ' + KIT_TOOL + ' with per_page 25 and no other arguments. If its '
            'pagination.has_next_page is true, call it again with the same per_page and after set to that result\'s '
            'pagination.end_cursor. Keep going, one call at a time, until has_next_page is false. Then reply with the '
            'single word done.')


def parse_stream(stdout):
    """The Kit tool calls and their raw results from Claude Code's stream-json output, in call order."""
    uses, pages = {}, []
    for line in stdout.splitlines():
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        for c in (ev.get('message') or {}).get('content') or []:
            if not isinstance(c, dict):
                continue
            if c.get('type') == 'tool_use' and c.get('name') == KIT_TOOL:
                uses[c['id']] = c.get('input') or {}
            elif c.get('type') == 'tool_result' and c.get('tool_use_id') in uses:
                body = c.get('content')
                if isinstance(body, list):
                    body = ''.join(x.get('text', '') for x in body if isinstance(x, dict))
                try:
                    result = json.loads(str(body or ''))
                except ValueError:
                    result = {'error': 'not a Kit report'}
                pages.append(dict(input=uses[c['tool_use_id']], result=result))
    return pages


def kit_server_status(stdout):
    """The Kit connector's state when Claude Code started ('connected', 'pending', ...), or None if not listed."""
    for line in stdout.splitlines():
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if ev.get('type') == 'system' and ev.get('subtype') == 'init':
            for s in ev.get('mcp_servers') or []:
                if str(s.get('name', '')).lower().endswith('kit'):
                    return s.get('status')
            return None
    return None


def fetch_kit(root, run=subprocess.run, timeout=420, tries=2, wait=15, sleep=None):
    """Every sent broadcast's totals, through Claude Code's read-only Kit connector. Returns a snapshot body.
    The child waits for its connectors at start-up (STARTUP_WAIT_MS). If Kit is still not connected, that is tried once
    more after a short wait, then recorded as the reason."""
    import time as _t
    from .agent import find_claude
    sleep = sleep or _t.sleep
    claude = find_claude(root)
    if not claude:
        return dict(source='kit', taken_at=now(), complete=False, reason='Claude Code is not installed, so Kit could not be read.', items={})
    cmd = [claude, '-p', kit_prompt(), '--output-format', 'stream-json', '--verbose', '--no-session-persistence', '--tools', '',
           '--allowedTools', KIT_TOOL]
    snap = None
    for attempt in range(tries):
        taken = now()
        try:
            out = run(cmd, capture_output=True, text=True, encoding='utf-8', timeout=timeout, cwd=str(root),
                      env=dict(os.environ, CLAUDE_CODE_MCP_STARTUP_WAIT_MS=STARTUP_WAIT_MS),
                      creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        except (OSError, subprocess.TimeoutExpired) as e:
            return dict(source='kit', taken_at=taken, complete=False, reason=f'Kit could not be read ({str(e)[:120]}).', items={})
        stdout = out.stdout or ''
        snap = snapshot_from_pages(parse_stream(stdout), taken)
        status = kit_server_status(stdout)
        snap['connector'] = status
        if snap['complete'] or snap.get('pages'):
            return snap
        if status != 'connected':
            snap['reason'] = (f"The Kit connector was {status or 'not listed'} when Claude Code started, so Kit was not read."
                              + (' Tried twice.' if attempt else ''))
        if attempt + 1 < tries:
            sleep(wait)
    return snap


def snapshot_from_pages(pages, taken):
    """Check the pages form one unbroken chain, then keep only each sent broadcast's id, send time and totals."""
    snap = dict(source='kit', taken_at=taken, complete=False, reason='', items={}, pages=len(pages))
    if not pages:
        snap['reason'] = 'Kit returned no pages.'
        return snap
    prev_end = None
    for i, p in enumerate(pages):
        r, after = p['result'], (p['input'] or {}).get('after')
        if not isinstance(r, dict) or not isinstance(r.get('broadcasts'), list) or not isinstance(r.get('pagination'), dict):
            snap['reason'] = f'Page {i + 1} was not a Kit stats report.'
            return snap
        if (i == 0 and after) or (i > 0 and after != prev_end):
            snap['reason'] = f'Page {i + 1} does not follow the page before it, so pages may be missing.'
            return snap
        for b in r['broadcasts']:
            st = b.get('stats') or {}
            if str(b.get('id')) in snap['items']:
                snap['reason'] = 'Kit returned the same broadcast twice.'
                return snap
            if st.get('status') not in SENT:
                continue
            snap['items'][str(b['id'])] = dict(send_at=b.get('send_at'), status=st.get('status'), recipients=st.get('recipients'),
                                               opened=st.get('emails_opened'), clicks=st.get('total_clicks'),
                                               unsubscribes=st.get('unsubscribes'),
                                               tracking_off=bool(st.get('open_tracking_disabled')))
        prev_end = r['pagination'].get('end_cursor')
        last = not r['pagination'].get('has_next_page')
        if last and i != len(pages) - 1:
            snap['reason'] = 'Kit said there were no more pages, but more were read.'
            return snap
    if not last:
        snap['reason'] = 'The last page read said there were more, so the snapshot is partial.'
        return snap
    snap['complete'] = True
    return snap


# Growth -----------------------------------------------------------------------------------------------------------
def kit_growth(prev, cur):
    """How much the totals grew between two complete snapshots, by the rules at the top."""
    if not cur or not cur.get('complete'):
        return dict(state='incomplete', reason=(cur or {}).get('reason') or 'No Kit snapshot was taken.')
    if not prev:
        return dict(state='baseline', taken_at=cur['taken_at'])
    t0, t1 = _ts(prev['taken_at']), _ts(cur['taken_at'])
    out = dict(state='ok', since=prev['taken_at'], until=cur['taken_at'], hours=round((t1 - t0).total_seconds() / 3600, 1),
               opens=0, clicks=0, broadcasts=0, new=0, decreased=0, no_baseline=0, missing=0)
    for bid, c in cur['items'].items():
        p = prev['items'].get(bid)
        if p is None:
            sent = _ts(c.get('send_at'))
            if sent is None or sent <= t0:
                out['no_baseline'] += 1
                continue
            p, out['new'] = dict(opened=0, clicks=0), out['new'] + 1
        if not all(isinstance(x, int) for x in (c.get('opened'), c.get('clicks'), p.get('opened'), p.get('clicks'))):
            out['no_baseline'] += 1
            continue
        if c['opened'] < p['opened'] or c['clicks'] < p['clicks']:
            out['decreased'] += 1
            continue
        out['opens'] += c['opened'] - p['opened']
        out['clicks'] += c['clicks'] - p['clicks']
        out['broadcasts'] += 1
    out['missing'] = sum(1 for bid in prev['items'] if bid not in cur['items'])
    out['daily'] = DAY_HOURS[0] <= out['hours'] <= DAY_HOURS[1]
    return out


class Counters:
    """Snapshots kept in Josh OS's database, one per source per Dubai day."""

    def __init__(self, store, root, fetch=None):
        self.store, self.root = store, root
        self.fetch = fetch or (lambda: fetch_kit(root))
        with store.connect() as c:
            c.execute('CREATE TABLE IF NOT EXISTS business_counters(key TEXT PRIMARY KEY,body TEXT NOT NULL)')

    def get(self, key):
        with self.store.connect() as c:
            row = c.execute('SELECT body FROM business_counters WHERE key=?', (key,)).fetchone()
        return json.loads(row['body']) if row else None

    def collect(self, day):
        """Take today's Kit snapshot unless a complete one exists. A partial one is replaced by a later complete try."""
        key = f'kit:{day.isoformat()}'
        have = self.get(key)
        if have and have.get('complete'):
            return have
        snap = dict(self.fetch(), day=day.isoformat())
        if have and not snap.get('complete'):
            snap['tries'] = int(have.get('tries') or 1) + 1
        with self.store.connect() as c:
            c.execute('INSERT OR REPLACE INTO business_counters VALUES(?,?)', (key, json.dumps(snap)))
        return snap

    def growth(self, day):
        """Today's snapshot against the newest complete snapshot from an earlier day."""
        cur = self.get(f'kit:{day.isoformat()}')
        with self.store.connect() as c:
            rows = [json.loads(r['body']) for r in c.execute(
                "SELECT body FROM business_counters WHERE key LIKE 'kit:%' AND key<? ORDER BY key DESC", (f'kit:{day.isoformat()}',))]
        prev = next((r for r in rows if r.get('complete')), None)
        return dict(kit_growth(prev, cur), previous_taken_at=(prev or {}).get('taken_at'),
                    taken_at=(cur or {}).get('taken_at'))
