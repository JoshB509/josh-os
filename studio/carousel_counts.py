"""Daily carousel impressions from YouTube Studio (added 13 Nov 2026, as Josh confirmed).

Checked on 13 Nov 2026:
- The YouTube Data API has no resource for community posts, and the YouTube Analytics API has no post content type
  (creatorContentType is LIVE_STREAM, SHORTS, STORY, VIDEO_ON_DEMAND or UNSPECIFIED). Neither gives post figures.
- Studio's Posts list and post pages show likes and comments only.
- Studio Analytics, Advanced mode, report "Post", breakdown Post, Lifetime gives each post's running total of
  "Post impressions" (how many times the post was shown) and likes, plus a Total row for every post. The table lists
  only the top 50 posts. Studio says impressions are underreported for 8 Dec 2025 to 3 Oct 2026, and its data runs a
  day or two behind ("Aug 30, 2011 – Nov 11, 2026" on 13 Nov). YouTube calls these impressions, not views.

So once a day Josh OS reads that table in Josh's own Chrome, through a guarded Claude Code run that may open only that
Studio page, run only the exact page scripts (SCRIPTS) and close its tab. The numbers come from the script's own result, never a model's
words. Then:
- The first complete read is the baseline. It gives no daily figure and nothing is made up before it.
- A later read counts only when Studio's data-through day has moved on. One day on gives that day's impressions. More
  than one is shown as the whole gap, never as one day. The same day again gives nothing new.
- The Total row covers every post, so the channel figure doesn't depend on the top 50. Each post Josh OS tracks is
  matched to its row by its text and kept under its stable /post/ id. A post with no row, or two possible rows, is left
  out, never zero. A post new since the last read counts from zero only if it was published after that read's day.
- A total that went down is left out and flagged.
- One read per Dubai day. A complete one isn't taken again; an incomplete one is replaced by a later complete try.
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import unicodedata
from datetime import date, datetime
from pathlib import Path

from .store import now

CHANNEL = 'UC3F6JjRjqDNrK_QzvyGwppA'
STUDIO_URL = (f'https://studio.youtube.com/channel/{CHANNEL}/analytics/tab-content/period-lifetime/explore?entity_type=CHANNEL'
              f'&entity_id={CHANNEL}&time_period=lifetime&explore_type=TABLE_AND_CHART&metrics_computation_type=DELTA'
              '&metric=POST_IMPRESSIONS&granularity=DAY&t_metrics=POST_IMPRESSIONS&t_metrics=POST_LIKES&dimension=POST'
              '&o_column=POST_IMPRESSIONS&o_direction=ANALYTICS_ORDER_DIRECTION_DESC')
DEVICE = 'd791a792-0ea7-4d51-90d5-a9fbf4144b3a'   # Chrome on DESKTOP-A4TRTK5, as studio/agent.py
STARTUP_WAIT_MS = '30000'
TOOLS = tuple('mcp__claude-in-chrome__' + t for t in ('select_browser', 'tabs_context_mcp', 'tabs_create_mcp', 'navigate',
                                                       'javascript_tool', 'tabs_close_mcp'))
JS = 'mcp__claude-in-chrome__javascript_tool'
# Reads the table only, in PAGES small pieces, because the Chrome tool cuts any result past about 1,000 characters.
# Each piece waits up to 30 seconds for Studio to draw the table, changes nothing, and returns the Total row, the date
# range, how many rows there are, and up to PAGE_ROWS rows as [first 20 characters of the post, impressions, likes].
PAGES, PAGE_ROWS = 4, 13
_SCRIPT = r"""await (async () => {
  const find = () => [...document.querySelectorAll('yta-explore-table')].find(t => /Post impressions/.test(t.innerText));
  for (let i = 0; i < 60 && !(find() && find().querySelector('yta-explore-table-row')); i++) await new Promise(r => setTimeout(r, 500));
  const t = find();
  if (!t) return JSON.stringify({error: 'The Post impressions table did not appear.'});
  const head = t.innerText, at = [head.indexOf('Post impressions'), head.indexOf('Post likes')];
  const num = s => { const v = (s || '').replace(/[,\s]/g, ''); return /^\d+$/.test(v) ? Number(v) : null; };
  const rows = [...t.querySelectorAll('yta-explore-table-row')].map(r => [
    Array.from(((r.querySelector('.debug-dimension-value') || {}).textContent || '').replace(/\s+/g, ' ').trim()).slice(0, 20).join(''),
    ...[...r.querySelectorAll('.debug-metric-value')].slice(0, 2).map(v => num(v.textContent))]);
  const total = rows.filter(r => r[0] === 'Total'), posts = rows.filter(r => r[0] !== 'Total');
  const range = (document.body.innerText.match(/[A-Z][a-z]{2} \d{1,2}, \d{4}\s*\S\s*[A-Z][a-z]{2} \d{1,2}, \d{4}/) || [null])[0];
  return JSON.stringify({p: PAGE, n: posts.length, range, f: at[0] >= 0 && at[0] < at[1], top: /Showing top \d+ results/.test(head),
    total: total.length === 1 ? total[0].slice(1) : null, r: posts.slice(PAGE * ROWS, PAGE * ROWS + ROWS)});
})()"""
SCRIPTS = tuple(_SCRIPT.replace('PAGE', str(i)).replace('ROWS', str(PAGE_ROWS)) for i in range(PAGES))


def _norm(s):
    s = unicodedata.normalize('NFKC', str(s or '')).replace('…', '').rstrip('.')
    return re.sub(r'\s+', ' ', re.sub(r'[^\w\s]', ' ', s.lower())).strip()


def _end_day(rng):
    try:
        return datetime.strptime(re.split(r'\s*[–-]\s*', rng)[-1].strip(), '%b %d, %Y').date()
    except (TypeError, ValueError, IndexError):
        return None


# Reading ---------------------------------------------------------------------------------------------------------
def prompt(device=DEVICE):
    steps = [
        'Read one YouTube Studio table, then reply with the single word done. Change nothing. Use one tool per step, never '
        'browser_batch.',
        f'1. mcp__claude-in-chrome__select_browser with deviceId {device}.',
        '2. mcp__claude-in-chrome__tabs_context_mcp with createIfEmpty true, then mcp__claude-in-chrome__tabs_create_mcp.',
        f'3. mcp__claude-in-chrome__navigate that new tab to exactly this URL: {STUDIO_URL}',
    ]
    for i, script in enumerate(SCRIPTS):
        steps += [f'{4 + i}. mcp__claude-in-chrome__javascript_tool on that tab with action javascript_exec and exactly this text:', script]
    steps.append(f'{4 + len(SCRIPTS)}. mcp__claude-in-chrome__tabs_close_mcp on that tab.')
    return '\n'.join(steps)


def policy(device=DEVICE):
    """What the guard (carousel_read_guard.py) allows: these tools, this URL, these scripts and this browser only."""
    return dict(tools=list(TOOLS), url=STUDIO_URL, scripts=list(SCRIPTS), device=device)


def parse_stream(stdout):
    """{page: [the script's result, ...]} for each exact page script, from Claude Code's stream-json output."""
    uses, out = {}, {}
    for line in stdout.splitlines():
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        for c in (ev.get('message') or {}).get('content') or []:
            if not isinstance(c, dict):
                continue
            text = (c.get('input') or {}).get('text') if c.get('type') == 'tool_use' and c.get('name') == JS else None
            if text in SCRIPTS:
                uses[c['id']] = SCRIPTS.index(text)
            elif c.get('type') == 'tool_result' and c.get('tool_use_id') in uses:
                body = c.get('content')
                if isinstance(body, list):
                    body = ''.join(x.get('text', '') for x in body if isinstance(x, dict))
                body = str(body or '')
                try:
                    # The script's JSON comes first and Chrome's tab list may follow it, so only the first object is
                    # read. Control characters inside strings are allowed.
                    v, _ = json.JSONDecoder(strict=False).raw_decode(body[body.index('{'):])
                except ValueError:
                    v = None
                out.setdefault(uses[c['tool_use_id']], []).append(v if isinstance(v, dict) else None)
    return out


def join_pages(pages):
    """One read from the page results, or {'error': ...}. Every page must be there exactly once, readable, from the
    same table (same date range, row count and Total), and together they must cover every row."""
    if sorted(pages) != list(range(PAGES)) or any(len(v) != 1 or v[0] is None for v in pages.values()):
        return {'error': 'Studio was not read in full.' if pages else 'Studio was not read.'}
    got = [pages[i][0] for i in range(PAGES)]
    bad = next((g['error'] for g in got if g.get('error')), None)
    if bad:
        return {'error': bad}
    same = {(g.get('range'), g.get('n'), json.dumps(g.get('total')), g.get('f'), g.get('top')) for g in got}
    if len(same) != 1 or any(g.get('p') != i for i, g in enumerate(got)):
        return {'error': 'The Studio table changed between pages, so the read was not used.'}
    g0 = got[0]
    rows = [r for g in got for r in g.get('r') or []]
    if not isinstance(g0.get('n'), int) or g0['n'] > PAGES * PAGE_ROWS or len(rows) != g0['n']:
        return {'error': f"Studio showed {g0.get('n')} rows and {len(rows)} were read, so the read was not used."}
    return dict(range=g0.get('range'), impressions_first=bool(g0.get('f')), top_only=bool(g0.get('top')), total=g0.get('total'), rows=rows)


def fetch_studio(root, device=DEVICE, run=subprocess.run, timeout=420):
    """One guarded read of the Studio table. Returns the joined read, or {'error': ...}."""
    from .agent import find_claude
    claude = find_claude(root)
    if not claude:
        return {'error': 'Claude Code is not installed, so Studio could not be read.'}
    with tempfile.TemporaryDirectory() as tmp:
        pol = Path(tmp) / 'carousel-read.json'
        pol.write_text(json.dumps(policy(device)), encoding='utf-8')
        hook = subprocess.list2cmdline([Path(sys.executable).as_posix(), Path(__file__).with_name('carousel_read_guard.py').resolve().as_posix(),
                                        pol.as_posix()])
        cmd = [claude, '-p', prompt(device), '--output-format', 'stream-json', '--verbose', '--no-session-persistence', '--tools', '',
               '--chrome', '--allowedTools', ','.join(TOOLS), '--permission-mode', 'dontAsk',
               '--settings', json.dumps({'hooks': {'PreToolUse': [{'matcher': '*', 'hooks': [{'type': 'command', 'command': hook}]}]}})]
        try:
            out = run(cmd, capture_output=True, text=True, encoding='utf-8', timeout=timeout, cwd=str(root),
                      env=dict(os.environ, CLAUDE_CODE_MCP_STARTUP_WAIT_MS=STARTUP_WAIT_MS),
                      creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        except (OSError, subprocess.TimeoutExpired) as e:
            return {'error': f'Studio could not be read ({str(e)[:120]}).'}
    return join_pages(parse_stream(out.stdout or ''))


def snapshot(read, carousels, taken):
    """Keep the all-posts Total row (channel_impressions) and each tracked carousel's row under its stable id (items).
    Figures are Studio's post impressions, never views. One Studio row is never given to two tracked posts."""
    snap = dict(source='youtube_studio', metric='post_impressions', taken_at=taken, complete=False, reason='',
                channel_impressions=None, through=None, items={}, missing={})
    if not isinstance(read, dict) or read.get('error'):
        snap['reason'] = (read or {}).get('error') if isinstance(read, dict) else 'Studio returned nothing readable.'
        return snap
    through = _end_day(read.get('range'))
    if through is None or not read.get('impressions_first'):
        snap['reason'] = 'Studio showed a different page or column order, so the read was not used.'
        return snap
    total = (read.get('total') or [None])[0]
    rows = [r for r in read.get('rows') or [] if isinstance(r, list) and len(r) >= 2 and isinstance(r[0], str)]
    if type(total) is not int or not rows:
        snap['reason'] = 'The Studio table had no Total impressions figure or no post rows.'
        return snap
    snap.update(channel_impressions=total, through=through.isoformat(), top_only=bool(read.get('top_only')), rows=len(rows))
    keyed = [(_norm(r[0]), i) for i, r in enumerate(rows)]
    matched = {}
    for c in carousels:
        m = re.search(r'youtube\.com/post/([A-Za-z0-9_-]{10,})', str(c.get('link') or ''))
        if not m or not c.get('posted_at') or c.get('archived'):
            continue
        cap = _norm(c.get('caption'))
        hits = [i for k, i in keyed if k and cap.startswith(k)]
        if len(hits) == 1 and type(rows[hits[0]][1]) is int:
            matched[m.group(1)] = (hits[0], c.get('post_date') or None)
        else:
            snap['missing'][m.group(1)] = "not in Studio's top 50" if not hits else 'more than one Studio row matches'
    used = {}
    for pid, (i, _) in matched.items():
        used.setdefault(i, []).append(pid)
    for pid, (i, post_date) in matched.items():
        if len(used[i]) > 1:
            snap['missing'][pid] = 'one Studio row matches more than one tracked post'
            continue
        r = rows[i]
        snap['items'][pid] = dict(impressions=r[1], likes=r[2] if len(r) > 2 and type(r[2]) is int else None, post_date=post_date)
    snap['complete'] = True
    return snap


# Growth ----------------------------------------------------------------------------------------------------------
def growth(prev, cur):
    """Impressions between two complete reads, by the rules at the top. Days are Studio's own reporting days."""
    if not cur or not cur.get('complete'):
        return dict(state='incomplete', reason=(cur or {}).get('reason') or 'Studio was not read.')
    if not prev:
        return dict(state='baseline', taken_at=cur['taken_at'], through=cur['through'])
    d0, d1 = date.fromisoformat(prev['through']), date.fromisoformat(cur['through'])
    if d1 <= d0:
        return dict(state='no_new_day', through=cur['through'], since=prev['through'])
    # channel_impressions is every post on the channel (Studio's Total row). tracked is only the carousels Josh OS
    # tracks, by post id. Both are post impressions, never views.
    out = dict(state='ok', since=prev['through'], through=cur['through'], days=(d1 - d0).days, channel_impressions=None,
               tracked={}, decreased=[], no_baseline=[], missing=[k for k in prev['items'] if k not in cur['items']])
    if isinstance(prev.get('channel_impressions'), int) and cur['channel_impressions'] >= prev['channel_impressions']:
        out['channel_impressions'] = cur['channel_impressions'] - prev['channel_impressions']
    else:
        out['decreased'].append('channel')
    for pid, c in cur['items'].items():
        p = prev['items'].get(pid)
        if p is None:
            if c.get('post_date') and c['post_date'] > prev['through']:
                p = dict(impressions=0)
            else:
                out['no_baseline'].append(pid)
                continue
        if c['impressions'] < p['impressions']:
            out['decreased'].append(pid)
            continue
        out['tracked'][pid] = c['impressions'] - p['impressions']
    out['daily'] = out['days'] == 1
    return out


class CarouselCounts:
    """Studio reads kept in Josh OS's database beside the Kit snapshots, one per Dubai day."""

    def __init__(self, store, root, fetch=None, carousels=None):
        self.store, self.root = store, root
        self.fetch = fetch or (lambda: fetch_studio(root, self.device()))
        self.carousels = carousels or self._carousels
        with store.connect() as c:
            c.execute('CREATE TABLE IF NOT EXISTS business_counters(key TEXT PRIMARY KEY,body TEXT NOT NULL)')

    def device(self):
        try:
            return json.loads((Path(self.root) / 'settings.json').read_text(encoding='utf-8-sig')).get('chrome_device') or DEVICE
        except (OSError, ValueError):
            return DEVICE

    def _carousels(self):
        with self.store.connect() as c:
            return [json.loads(r[0]) for r in c.execute('SELECT body FROM carousels')]

    def get(self, key):
        with self.store.connect() as c:
            row = c.execute('SELECT body FROM business_counters WHERE key=?', (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def all(self):
        with self.store.connect() as c:
            return [json.loads(r[0]) for r in c.execute("SELECT body FROM business_counters WHERE key LIKE 'carousel:%' ORDER BY key")]

    def collect(self, day):
        key = f'carousel:{day.isoformat()}'
        have = self.get(key)
        if have and have.get('complete'):
            return have
        snap = dict(snapshot(self.fetch(), self.carousels(), now()), day=day.isoformat())
        if have and not snap.get('complete'):
            snap['tries'] = int(have.get('tries') or 1) + 1
        with self.store.connect() as c:
            c.execute('INSERT OR REPLACE INTO business_counters VALUES(?,?)', (key, json.dumps(snap)))
        return snap

    def growth(self, day):
        """Today's read against the newest complete read from an earlier Dubai day."""
        cur = self.get(f'carousel:{day.isoformat()}')
        earlier = [s for s in self.all() if s.get('complete') and s.get('day', '') < day.isoformat()]
        prev = earlier[-1] if earlier else None
        return dict(growth(prev, cur), previous_taken_at=(prev or {}).get('taken_at'),
                    taken_at=(cur or {}).get('taken_at'))

    def daily(self):
        """{Studio day: {channel, tracked, posts}} for each pair of complete reads exactly one Studio day apart. channel is
        post impressions on every post; tracked is the sum over the tracked carousels both reads had. The baseline and any
        longer gap give nothing, so they show as gaps, never as one big day."""
        done = [s for s in self.all() if s.get('complete')]
        out = {}
        for prev, cur in zip(done, done[1:]):
            g = growth(prev, cur)
            if g.get('state') == 'ok' and g['daily']:
                out[g['through']] = dict(channel=g['channel_impressions'], tracked=sum(g['tracked'].values()), posts=len(g['tracked']))
        return out

    def baseline(self):
        done = [s for s in self.all() if s.get('complete')]
        return done[0] if done else None
