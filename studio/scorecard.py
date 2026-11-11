"""The business scorecard: six cards, the last 30 completed UTC days against the 30 before, plus month to date
against the revenue goal.

Business numbers come from Linklr through Claude Code's read-only Linklr tools. Josh OS asks Claude Code to call them and
keeps the raw tool results, so no model ever retypes a number. Buyer emails and names in the course payment report are
dropped as it's read, and only dates and amounts are kept. The rest comes from Josh OS itself (published videos, posted
carousels and their checks).

The last good snapshot is kept on disk. A failed refresh never replaces it, it's recorded beside it. Anything a source
doesn't provide is shown as unavailable with the reason, never as zero, and no daily figures are made up from totals.
"""
import json
import os
import re
import subprocess
import threading
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from .store import now

TRACKING_STARTED = date(2026, 10, 1)
COURSE_LAUNCH = date(2026, 11, 1)
WINDOW = 30
READ_TOOLS = ('get_data_status', 'get_daily_metrics', 'get_acquisition_by_source', 'get_business_summary', 'get_course_buyer_journeys')
CAROUSEL_SOURCES = ('community_post', 'qr_code')


def _d(s):
    return date.fromisoformat(str(s)[:10])


class Scorecard:
    def __init__(self, store, root, data):
        self.store, self.root = store, Path(root)
        self.dir = Path(data) / 'scorecard'
        self.dir.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self.job = None

    # Fetching -------------------------------------------------------------------------------------
    def snapshot(self):
        try:
            return json.loads((self.dir / 'snapshot.json').read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return None

    def attempt(self):
        try:
            return json.loads((self.dir / 'last-attempt.json').read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return None

    def refresh(self, wait=False):
        with self.lock:
            if self.job and self.job.is_alive():
                return dict(state='running')
            self.job = threading.Thread(target=self._refresh, daemon=True)
            self.job.start()
        if wait:
            self.job.join(600)
        return dict(state='started')

    def refreshing(self):
        return bool(self.job and self.job.is_alive())

    def _calls(self, today):
        calls = [('get_data_status', {}), ('get_daily_metrics', {'days': 62}),
                 ('get_acquisition_by_source', {'days': 1}), ('get_acquisition_by_source', {'days': 31}),
                 ('get_acquisition_by_source', {'days': 61}), ('get_business_summary', {'period': 'mtd'})]
        # Linklr's course payment report times out over long windows, so it is read in 10 day pieces, and only from the
        # day tracking started, because earlier days are shown as untracked anyway.
        start, end = max(today - timedelta(days=61), TRACKING_STARTED), today - timedelta(days=1)
        while start <= end:
            stop = min(start + timedelta(days=9), end)
            calls.append(('get_course_buyer_journeys', {'start_date': start.isoformat(), 'end_date': stop.isoformat()}))
            start = stop + timedelta(days=1)
        return calls

    def _raw(self, today):
        """Every Linklr read in one Claude Code run. Returns {(tool, json args): parsed result}."""
        from .agent import find_claude
        claude = find_claude(self.root)
        if not claude:
            raise RuntimeError('Claude Code is not installed on this PC, so Linklr cannot be read.')
        calls = self._calls(today)
        steps = '\n'.join(f'{i + 1}. mcp__linklr__{name} with arguments {json.dumps(args)}' for i, (name, args) in enumerate(calls))
        prompt = f'Call each of these read-only tools exactly once, with exactly these arguments, then reply with the single word done.\n{steps}'
        cmd = [claude, '-p', prompt, '--output-format', 'stream-json', '--verbose', '--no-session-persistence', '--tools', '',
               '--allowedTools', ','.join(f'mcp__linklr__{t}' for t in READ_TOOLS)]
        # The course payment report for 61 days is longer than Claude Code's usual cap on a tool result, so the cap is
        # raised for this run only. Nothing else about Claude Code changes.
        env = dict(os.environ, MAX_MCP_OUTPUT_TOKENS='400000')
        run = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', timeout=540, cwd=str(self.root), env=env,
                             creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        uses, results = {}, {}
        for line in run.stdout.splitlines():
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            for c in (ev.get('message') or {}).get('content') or []:
                if not isinstance(c, dict):
                    continue
                if c.get('type') == 'tool_use' and str(c.get('name', '')).startswith('mcp__linklr__'):
                    uses[c['id']] = (c['name'].split('__')[-1], json.dumps(c.get('input') or {}, sort_keys=True))
                elif c.get('type') == 'tool_result' and c.get('tool_use_id') in uses:
                    body = c.get('content')
                    if isinstance(body, list):
                        body = ''.join(x.get('text', '') for x in body if isinstance(x, dict))
                    body = self._persisted(str(body or ''))
                    try:
                        results[uses[c['tool_use_id']]] = json.loads(body)
                    except (TypeError, ValueError):
                        # never keep the text itself, it can hold buyer details
                        results[uses[c['tool_use_id']]] = {'error': 'Linklr returned something that was not a report: ' + ('a timeout' if 'timeout' in body else 'unreadable')}
        want = [(n, json.dumps(a, sort_keys=True)) for n, a in calls]
        missing = [f'{n} {a}' for n, a in want if n and (n, a) not in results]
        if not results:
            raise RuntimeError('Linklr returned nothing. ' + (run.stderr.strip().splitlines() or ['Is Linklr signed in to Claude Code?'])[-1][:200])
        return {k: v for k, v in results.items()}, want, missing

    @staticmethod
    def _persisted(body):
        """Claude Code saves a long tool result to a file and passes back a note with its path. Read the file and remove
        it, because a course payment report holds buyer details Josh OS doesn't keep."""
        if not body.lstrip().startswith('<persisted-output>'):
            return body
        m = re.search(r'Full output saved to:\s*(.+?\.(?:txt|json))\s*$', body, re.M)
        if not m:
            return body
        path = Path(m.group(1).strip())
        try:
            text = path.read_text(encoding='utf-8')
        except OSError:
            return body
        try:
            path.unlink()
        except OSError:
            pass
        try:
            blocks = json.loads(text)
            if isinstance(blocks, list):
                return ''.join(x.get('text', '') for x in blocks if isinstance(x, dict))
        except ValueError:
            pass
        return text

    def _refresh(self):
        started = time.time()
        today = datetime.now(timezone.utc).date()
        attempt = dict(at=now(), state='running')
        try:
            raw, want, missing = self._raw(today)
            get = lambda i: raw.get(want[i])  # noqa: E731
            bad = [want[i][0] for i in range(len(want)) if isinstance(get(i), dict) and get(i).get('error')]
            daily = (get(1) or {}).get('daily')
            if not daily:
                raise RuntimeError('Linklr did not return the daily figures' + (f": {get(1).get('error')}" if isinstance(get(1), dict) and get(1).get('error') else '.'))

            def acq(i):
                r = get(i)
                if not isinstance(r, dict) or 'sources' not in r:
                    return None
                out = {}
                for s in r['sources']:
                    out[s.get('source_type') or 'unknown'] = out.get(s.get('source_type') or 'unknown', 0) + int(s.get('new_subscribers') or 0)
                return dict(window=r.get('window'), by_type=out)

            pieces = [get(i) for i in range(6, len(want))]
            course_payments = None
            if pieces and all(isinstance(b, dict) and 'rows' in b for b in pieces):
                # Only the date and money of each course payment are kept. Names and emails are dropped here.
                course_payments = [dict(at=r.get('purchase_at'), cents=int(r.get('usd_amount') or 0),
                                        refunded_cents=sum(int(x.get('amount_cents') or x.get('usd_cents') or 0) for x in r.get('refunds') or []))
                                   for b in pieces for r in b['rows'] if r.get('purchase_at')]
            summary = get(5) if isinstance(get(5), dict) else {}
            snap = dict(fetched_at=now(), response_seconds=round(time.time() - started, 1), today_utc=today.isoformat(),
                        status=get(0) if isinstance(get(0), dict) else {}, daily=daily,
                        acq1=acq(2), acq31=acq(3), acq61=acq(4),
                        mtd_summary={k: (summary.get('current') or {}).get(k) for k in ('udemy_income', 'youtube_income', 'other_pay', 'revenue', 'course', 'clients')},
                        mtd_window=(summary.get('current') or {}).get('start'),
                        course_payments=course_payments, missing=missing + bad)
            (self.dir / 'snapshot.json').write_text(json.dumps(snap, indent=1), encoding='utf-8')
            attempt.update(state='ok', seconds=snap['response_seconds'], missing=snap['missing'])
        except Exception as e:  # noqa: BLE001 - the last good snapshot stays; the failure is shown beside it
            attempt.update(state='failed', error=str(e)[:400], seconds=round(time.time() - started, 1))
        (self.dir / 'last-attempt.json').write_text(json.dumps(attempt, indent=1), encoding='utf-8')

    # Building the cards -----------------------------------------------------------------------------
    def goal(self):
        """The revenue goal as Josh OS has it set, with its basis, from Tasks & Goals."""
        try:
            with self.store.connect() as c:
                rows = [json.loads(r[0]) for r in c.execute('SELECT body FROM task_items')]
        except Exception:  # noqa: BLE001
            rows = []
        for t in rows:
            text = ' '.join(str(t.get(k) or '') for k in ('title', 'text', 'name'))
            m = re.search(r'USD\s*([\d,]+)\s*/\s*month', text)
            if m and t.get('kind', 'goal') == 'goal' and 'course' in text.lower():
                return dict(amount=int(m.group(1).replace(',', '')), title=text.strip(), basis='course_upsell',
                            note=str(t.get('next_step') or t.get('note') or ''))
        return dict(amount=15000, title='Reach USD 15,000/month from courses + upsells', basis='course_upsell',
                    note='Excludes client work, Udemy and other revenue.', assumed=True)

    def view(self):
        snap, attempt = self.snapshot(), self.attempt()
        today = _d(snap['today_utc']) if snap else datetime.now(timezone.utc).date()
        cur = [today - timedelta(days=WINDOW - i) for i in range(WINDOW)]
        prev = [d - timedelta(days=WINDOW) for d in cur]
        out = dict(windows=dict(current=dict(start=cur[0].isoformat(), end=cur[-1].isoformat()),
                                previous=dict(start=prev[0].isoformat(), end=prev[-1].isoformat()), timezone='UTC'),
                   refreshing=self.refreshing(), attempt=attempt, snapshot=bool(snap), goal=self.goal())
        if snap:
            st = snap.get('status') or {}
            out['freshness'] = dict(fetched_at=snap['fetched_at'], response_seconds=snap.get('response_seconds'),
                                    payments_through=st.get('last_payment_at'), activity_through=st.get('last_activity_at'),
                                    email_list_synced=st.get('last_email_list_sync_at'), youtube_data_through=st.get('youtube_data_through'),
                                    warnings=st.get('warnings') or [], missing=snap.get('missing') or [])
        partial_prev = prev[0] < TRACKING_STARTED
        by_day = {r['date']: r for r in (snap or {}).get('daily') or []}

        def series(field, days, fn=None):
            vals = []
            for d in days:
                r = by_day.get(d.isoformat())
                if d < TRACKING_STARTED or r is None:
                    vals.append(None)
                else:
                    vals.append(fn(r) if fn else r.get(field))
            return vals

        def total(vals):
            got = [v for v in vals if v is not None]
            return round(sum(got), 2) if got else None

        def metric(label, unit, cur_vals, prev_vals, definition, chart=True, source='', note='', gap=None):
            c, p = total(cur_vals), total(prev_vals)
            untracked = sum(1 for v in prev_vals if v is None)
            m = dict(label=label, unit=unit, value=c, previous=p, definition=definition, source=source, note=note,
                     available=c is not None, previous_partial=untracked > 0,
                     delta=(round(c - p, 2) if c is not None and p is not None and untracked == 0 and all(v is not None for v in cur_vals) else None))
            if untracked and p is not None:
                m['previous_note'] = gap or f'{untracked} of the earlier 30 days were before tracking started on 1 October, so there is no fair change figure.'
            if any(v is None for v in cur_vals) and c is not None:
                m['current_partial'] = True
            if chart:
                m['daily'] = dict(current=cur_vals, previous=prev_vals)
            return m

        def gone(label, unit, reason, definition):
            return dict(label=label, unit=unit, value=None, previous=None, delta=None, available=False, reason=reason, definition=definition)

        acq = lambda key, types: (sum(((snap or {}).get(key) or {}).get('by_type', {}).get(t, 0) for t in types) if (snap or {}).get(key) else None)  # noqa: E731

        def acq_metric(label, types, definition):
            a1, a31, a61 = acq('acq1', types), acq('acq31', types), acq('acq61', types)
            if a1 is None or a31 is None:
                return gone(label, 'people', 'Linklr did not return the acquisition report.', definition)
            c = a31 - a1
            p = (a61 - a31) if a61 is not None else None
            m = dict(label=label, unit='people', value=c, previous=p, definition=definition, available=True,
                     previous_partial=partial_prev, delta=(c - p if p is not None and not partial_prev else None),
                     source='Linklr acquisition by source (31 day minus 1 day, so today is left out)')
            if partial_prev:
                m['previous_note'] = 'The earlier 30 days start before tracking began on 1 October, so there is no fair change figure.'
            return m

        cards = []
        # 1. YouTube
        projects = self.store.projects()
        pub = {}
        for p in projects:
            if p.get('stage') == 'Published' and p.get('publish_date'):
                pub[p['publish_date']] = pub.get(p['publish_date'], 0) + 1
        yt_through = (snap or {}).get('status', {}).get('youtube_data_through') if snap else None
        # Josh OS only has the videos and carousels entered since it started tracking them, so earlier days are unrecorded, not zero
        first_pub = min(pub) if pub else None
        pub_vals = lambda days: [None if first_pub is None or d.isoformat() < first_pub else pub.get(d.isoformat(), 0) for d in days]  # noqa: E731
        pub_gap = f'Josh OS has published videos recorded from {first_pub} only, so earlier days are not counted and there is no fair change figure.'
        cards.append(dict(key='youtube', title='YouTube',
                          headline=gone('Video views', 'views', f"Linklr's YouTube figures stop at {yt_through or 'an unknown date'} and it has no daily views, so this window can't be counted yet.",
                                        'Views of every video on the channel on each day, from YouTube Analytics.'),
                          supporting=[metric('Videos published', 'videos', pub_vals(cur), pub_vals(prev),
                                             'Videos marked Published in Josh OS, by their publish date.', chart=False, source='Josh OS videos', gap=pub_gap),
                                      gone('Subscribers gained', 'subscribers', "Linklr's YouTube figures are behind and have no daily subscriber counts.", 'Net channel subscribers gained, from YouTube Analytics.'),
                                      acq_metric('Emails from YouTube', ('youtube_video',),
                                                 'New email subscribers whose first tracked click was a YouTube video link. A subset of the email list growth, not extra people.')]))
        # 2. Carousels
        posts = []
        for r in self._carousels():
            if r.get('posted_at') and not r.get('archived'):
                checks = [v for v in (r.get('results') or {}).values() if isinstance(v, dict)]
                last = max(checks, key=lambda v: str(v.get('checked_at') or '')) if checks else None
                posts.append(dict(day=str(r['posted_at'])[:10], views=(last or {}).get('views'), likes=(last or {}).get('likes')))

        first_post = min((p['day'] for p in posts), default=None)
        post_gap = f'Josh OS has carousel posts recorded from {first_post} only, so earlier days are not counted and there is no fair change figure.'

        def post_vals(days, key):
            vals = []
            for d in days:
                ps = [p for p in posts if p['day'] == d.isoformat()]
                if first_post is None or d.isoformat() < first_post:
                    vals.append(None)
                elif not ps:
                    vals.append(0)
                elif any(p[key] is None for p in ps):
                    vals.append(None if all(p[key] is None for p in ps) else sum(p[key] or 0 for p in ps))
                else:
                    vals.append(sum(p[key] for p in ps))
            return vals
        in_cur = [p for p in posts if cur[0].isoformat() <= p['day'] <= cur[-1].isoformat()]
        unchecked = sum(1 for p in in_cur if p['views'] is None)
        head = metric('Views on posts published', 'views', post_vals(cur, 'views'), post_vals(prev, 'views'),
                      'Views on carousels posted in the window, as at each post\'s latest check in Josh OS, shown by post date. It is not views per day.',
                      source='Josh OS carousel checks', gap=post_gap,
                      note=(f'{unchecked} of {len(in_cur)} posts in the window have no views check yet. ' if unchecked else '')
                      + (f'Posts before {first_post} are not recorded in Josh OS.' if first_post and first_post > cur[0].isoformat() else ''))
        head['chart_basis'] = 'By post date'
        cards.append(dict(key='carousels', title='Carousels', headline=head,
                          supporting=[metric('Posts published', 'posts', *[[None if first_post is None or d.isoformat() < first_post else sum(1 for p in posts if p['day'] == d.isoformat()) for d in w] for w in (cur, prev)],
                                             'Carousels marked posted in Josh OS, by post date.', chart=False, source='Josh OS', gap=post_gap),
                                      metric('Likes on posts published', 'likes', post_vals(cur, 'likes'), post_vals(prev, 'likes'),
                                             "Likes on carousels posted in the window, as at each post's latest check.", chart=False, source='Josh OS carousel checks', gap=post_gap),
                                      acq_metric('Emails from carousel links', CAROUSEL_SOURCES,
                                                 'New email subscribers whose first tracked click was a community post link or a QR code on a carousel. A subset of list growth.')]))
        # 3. Email list
        new = series('new_subscribers', cur), series('new_subscribers', prev)
        uns = series('unsubscribes', cur), series('unsubscribes', prev)
        net = [None if a is None or b is None else a - b for a, b in zip(*[new[0], uns[0]])], [None if a is None or b is None else a - b for a, b in zip(new[1], uns[1])]
        cards.append(dict(key='email', title='Email list',
                          headline=metric('Net subscribers added', 'people', net[0], net[1], 'New subscribers minus unsubscribes, each day.', source='Linklr (email list sync)'),
                          supporting=[metric('New subscribers', 'people', *new, 'People who joined the list. Attributed emails on the other cards are part of this, not extra.', chart=False, source='Linklr'),
                                      metric('Unsubscribes', 'people', *uns, 'People who left the list.', chart=False, source='Linklr'),
                                      gone('Downloads', 'downloads', 'No download events reach Josh OS or Linklr. Lead magnet clicks are clicks, not downloads.', 'Lead magnet downloads, counted as events, not unique people.')]))
        # 4. Course sales
        pay = (snap or {}).get('course_payments')
        counts = {}
        for p in pay or []:
            counts[p['at'][:10]] = counts.get(p['at'][:10], 0) + 1
        buys = ([None if d < TRACKING_STARTED else counts.get(d.isoformat(), 0) for d in cur],
                [None if d < TRACKING_STARTED else counts.get(d.isoformat(), 0) for d in prev]) if pay is not None else None
        course = series('course_usd', cur), series('course_usd', prev)
        upsell = series('upsell_usd', cur), series('upsell_usd', prev)
        both = [None if a is None else a + (b or 0) for a, b in zip(course[0], upsell[0])], [None if a is None else a + (b or 0) for a, b in zip(course[1], upsell[1])]
        launch = 'The course launched on 1 November, so the earlier window is before launch and not a like-for-like comparison.'
        cards.append(dict(key='course', title='Course sales',
                          headline=(metric('Paid course purchases', 'purchases', *buys, 'Course payments, by purchase date. Client payments and upsells are not counted.', source='Linklr course payments', note=launch)
                                    if buys else gone('Paid course purchases', 'purchases', 'Linklr did not return the course payment report.', 'Course payments by purchase date.')),
                          supporting=[metric('Course revenue', 'usd', *course, 'Course payments in US dollars, before refunds.', chart=False, source='Linklr'),
                                      metric('Upsell revenue', 'usd', *upsell, 'Upsell payments in US dollars.', chart=False, source='Linklr'),
                                      metric('Course and upsell revenue', 'usd', *both, 'Course plus upsells. Excludes client work, Udemy and unassigned payments.', chart=False, source='Linklr')]))
        # 5. Business revenue
        gross = lambda r: round((r.get('course_usd') or 0) + (r.get('upsell_usd') or 0) + (r.get('clients_usd') or 0) + (r.get('other_usd') or 0), 2)  # noqa: E731
        other = series('other_usd', cur)
        mtd = (snap or {}).get('mtd_summary') or {}
        rev_note = ('Udemy and YouTube income come as payouts, not daily payments, so they are not in this total. Month to date Linklr shows '
                    f"Udemy {self._usd_cents(mtd.get('udemy_income'))} and YouTube {self._usd_cents(mtd.get('youtube_income'))}.") if snap else ''
        if total(other):
            rev_note += f" {self._usd(total(other))} is unassigned payments counted as other."
        cards.append(dict(key='revenue', title='Business revenue',
                          headline=metric('Gross payments', 'usd', series(None, cur, gross), series(None, prev, gross),
                                          'Course, upsell, client and unassigned payments each day, before refunds, in US dollars.', source='Linklr payments', note=rev_note.strip()),
                          supporting=[metric('Course and upsells', 'usd', *both, 'Course plus upsell payments.', chart=False, source='Linklr'),
                                      metric('Services', 'usd', series('clients_usd', cur), series('clients_usd', prev), 'Client work payments.', chart=False, source='Linklr'),
                                      metric('Transactions', 'payments', series('purchases', cur), series('purchases', prev), 'Every payment of any kind.', chart=False, source='Linklr')]))
        # 6. Customer learning
        why = 'No learner activity source is connected to Josh OS yet (the course app does not send lesson activity).'
        cards.append(dict(key='learning', title='Customer learning',
                          headline=gone('Distinct active learners', 'learners', why, 'Different people who opened a lesson in the window. Daily active counts are not added up.'),
                          supporting=[gone('Started within 7 days', 'buyers', why, 'Buyers who started a lesson within 7 days of buying, counting only buyers whose 7 days have fully passed.'),
                                      gone('Lessons completed', 'lessons', why, 'Lesson completions in the window.'),
                                      gone('Course completions', 'learners', why, 'Learners who finished the course in the window.')]))
        out['cards'] = cards
        out['mtd'] = self._mtd(by_day, today, out['goal']) if snap else None
        return out

    def _carousels(self):
        try:
            with self.store.connect() as c:
                return [json.loads(r[0]) for r in c.execute('SELECT body FROM carousels')]
        except Exception:  # noqa: BLE001
            return []

    @staticmethod
    def _usd(v):
        return 'unavailable' if v is None else f'${v:,.2f}'

    @staticmethod
    def _usd_cents(v):
        return 'unavailable' if v is None else f'${int(v) / 100:,.2f}'

    def _mtd(self, by_day, today, goal):
        """Month to date on completed days, against the same completed days last month."""
        first = today.replace(day=1)
        n = (today - first).days
        last_first = (first - timedelta(days=1)).replace(day=1)
        days = [first + timedelta(days=i) for i in range(n)]
        then = [last_first + timedelta(days=i) for i in range(n)]
        cu = lambda r: (r.get('course_usd') or 0) + (r.get('upsell_usd') or 0)  # noqa: E731
        gr = lambda r: cu(r) + (r.get('clients_usd') or 0) + (r.get('other_usd') or 0)  # noqa: E731

        def s(ds, f):
            rows = [by_day.get(d.isoformat()) for d in ds]
            if any(r is None or d < TRACKING_STARTED for r, d in zip(rows, ds)):
                return None
            return round(sum(f(r) for r in rows), 2)
        month_days = ((first + timedelta(days=32)).replace(day=1) - first).days
        cur_cu = s(days, cu)
        return dict(month=first.strftime('%B %Y'), completed_days=n, days_in_month=month_days,
                    through=(today - timedelta(days=1)).isoformat() if n else None,
                    compare=dict(start=then[0].isoformat() if then else None, end=then[-1].isoformat() if then else None, month=last_first.strftime('%B')),
                    course_upsell=cur_cu, course_upsell_prev=s(then, cu), gross=s(days, gr), gross_prev=s(then, gr),
                    target=goal['amount'], basis=goal['basis'],
                    needed_per_day=(round((goal['amount'] - cur_cu) / (month_days - n), 2) if cur_cu is not None and month_days > n else None))
