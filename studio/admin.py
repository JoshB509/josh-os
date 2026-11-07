"""Admin: clients and course customers.

Everything shown comes from three places:
- the client folders in CareerOS/01_Clients (calls, transcripts, notes, files),
- Stripe (payments, so LTV and course sales),
- Calendly (upcoming and past bookings).

Stripe and Calendly keys live in Data/admin.json on this PC and never reach the
browser. The last good pull from each service is kept in Data/admin-cache.json,
so the pages still load when a service is down or a key has not been added yet.
"""
import json
import os
import re
import shutil
import subprocess
import threading
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

DUBAI = timezone(timedelta(hours=4))
SESSION_DIRS = ('01_Onboarding', '02_Sessions')
STANDARD_DIRS = ('01_Onboarding', '02_Sessions', '03_Deliverables', '04_Admin', '05_Client-Supplied')
COURSE_WORDS = ('executive communication', 'add-on', 'templates pack', 'career pack', 'prompt and skill')
TEXT_LIMIT = 600_000
SKIP_FILES = {'desktop.ini', 'thumbs.db', '.ds_store'}


def now():
    return datetime.now(timezone.utc)


def iso(ts):
    return datetime.fromtimestamp(int(ts), timezone.utc).isoformat()


def fold(text):
    """Lowercase, accent-free name for matching 'Karl Bougué' to 'Karl Bougue'."""
    text = unicodedata.normalize('NFKD', str(text or '')).encode('ascii', 'ignore').decode()
    return re.sub(r'[^a-z ]+', ' ', text.lower()).split()


def names_match(a, b):
    """Same surname and same first initial, so 'David crossland' matches 'Dave Crossland'."""
    x, y = fold(a), fold(b)
    if not x or not y:
        return False
    if x == y:
        return True
    return x[-1] == y[-1] and x[0][0] == y[0][0]


def title_case(name):
    name = (name or '').strip()
    if name and (name.isupper() or name.islower()):
        return ' '.join(w.capitalize() for w in name.split())
    return name


def write_json(path, data):
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding='utf-8')
    tmp.replace(path)


# Stripe ---------------------------------------------------------------------

def classify(text):
    t = (text or '').lower()
    return 'course' if any(w in t for w in COURSE_WORDS) else 'client'


def normalize_stripe(charges, sessions, owner_emails):
    """Turn raw Stripe charges and checkout sessions into one payment list.

    Checkout sessions carry the product names, so they decide whether a payment
    was the course or coaching. Charges without a session (instalments raised by
    a subscription) inherit the kind from earlier payments by the same person.
    """
    owners = {e.lower() for e in owner_emails}
    by_intent = {}
    for s in sessions:
        items = [li.get('description') or '' for li in ((s.get('line_items') or {}).get('data') or [])]
        meta = s.get('metadata') or {}
        by_intent[s.get('payment_intent')] = dict(
            product=' + '.join(i for i in items if i),
            bump='templates' in (meta.get('bump') or '').lower() or any('templates' in i.lower() for i in items),
            name=(s.get('customer_details') or {}).get('name'),
            email=(s.get('customer_details') or {}).get('email'),
        )
    payments = []
    for c in sorted(charges, key=lambda c: c['created']):
        if c.get('status') != 'succeeded' or not c.get('paid', True):
            continue
        bd = c.get('billing_details') or {}
        cust = c.get('customer') if isinstance(c.get('customer'), dict) else {}
        session = by_intent.get(c.get('payment_intent')) or {}
        email = (bd.get('email') or c.get('receipt_email') or cust.get('email') or session.get('email') or '').strip().lower()
        if not email or email in owners:
            continue
        product = session.get('product') or c.get('description') or ''
        payments.append(dict(
            id=c['id'],
            created=iso(c['created']),
            amount=int(c.get('amount') or 0),
            refunded=int(c.get('amount_refunded') or 0),
            currency=(c.get('currency') or 'usd').upper(),
            email=email,
            name=title_case(bd.get('name') or cust.get('name') or session.get('name') or ''),
            country=(bd.get('address') or {}).get('country') or '',
            product=product,
            bump=bool(session.get('bump')),
            kind=classify(product) if product else '',
        ))
    # Instalments: no product name, so follow whatever that person bought before.
    seen = {}
    for p in payments:
        if p['kind']:
            seen.setdefault(p['email'], p['kind'])
        else:
            p['kind'] = seen.get(p['email'], 'client')
            p['product'] = p['product'] or 'Instalment'
    for p in payments:
        if p['product'].lower().startswith('subscription'):
            p['product'] = 'Instalment'
    return payments


# Client folders -------------------------------------------------------------

def parse_index(path):
    """Rows from _client-index.md, keyed by folder name."""
    rows = {}
    try:
        text = path.read_text(encoding='utf-8-sig')
    except OSError:
        return rows
    header = None
    for line in text.splitlines():
        if not line.strip().startswith('|'):
            continue
        cells = [c.strip() for c in line.strip().strip('|').split('|')]
        if set(''.join(cells)) <= set('-: '):
            continue
        if header is None:
            header = [c.lower() for c in cells]
            continue
        row = dict(zip(header, cells))
        folder = (row.get('folder') or '').strip('`').rstrip('/\\').strip()
        if folder:
            rows[folder] = row
    return rows


def call_date(name):
    m = re.match(r'(\d{4}-\d{2}-\d{2})[_ -]*(.*)', name)
    if not m:
        return '', name
    title = re.sub(r'[-_]+', ' ', m.group(2)).strip()
    return m.group(1), title


def find_urls(text):
    return re.findall(r'https?://[^\s)>\]]+', text or '')


class Admin:
    def __init__(self, data_root, app_root):
        self.data = Path(data_root)
        self.app_root = Path(app_root)
        self.config_path = self.data / 'admin.json'
        self.cache_path = self.data / 'admin-cache.json'
        self.lock = threading.Lock()
        self.sync_lock = threading.Lock()
        self.status = dict(running=False, last_error='', finished_at='')

    # config and cache ---------------------------------------------------------
    def config(self):
        try:
            c = json.loads(self.config_path.read_text(encoding='utf-8-sig'))
        except (OSError, ValueError):
            c = {}
        c.setdefault('clients_root', os.environ.get('DJ_CLIENTS_ROOT') or r'E:\CareerOS\01_Clients')
        c.setdefault('owner_emails', ['joshua.brindley@gmail.com', 'josh@datawithjosh.com'])
        c.setdefault('stripe_key', '')
        c.setdefault('calendly_token', '')
        c.setdefault('clients', {})
        return c

    def save_config(self, c):
        with self.lock:
            write_json(self.config_path, c)

    def cache(self):
        try:
            return json.loads(self.cache_path.read_text(encoding='utf-8-sig'))
        except (OSError, ValueError):
            return {}

    def save_cache(self, cache):
        with self.lock:
            write_json(self.cache_path, cache)

    @property
    def root(self):
        return Path(self.config()['clients_root'])

    # connections ----------------------------------------------------------------
    def connections(self):
        c, cache = self.config(), self.cache()
        def one(key, part):
            got = cache.get(part) or {}
            return dict(connected=bool(c.get(key)), key_end=(c.get(key) or '')[-4:],
                        synced_at=got.get('synced_at', ''), source=got.get('source', ''),
                        error=got.get('error', ''))
        return dict(stripe=one('stripe_key', 'stripe'), calendly=one('calendly_token', 'calendly'),
                    running=self.status['running'], clients_root=str(self.root),
                    clients_root_found=self.root.is_dir())

    def connect(self, data):
        c = self.config()
        stripe = (data.get('stripe_key') or '').strip()
        calendly = (data.get('calendly_token') or '').strip()
        if stripe:
            if not re.fullmatch(r'(rk|sk)_(live|test)_[A-Za-z0-9]{10,200}', stripe):
                raise ValueError('That does not look like a Stripe key. Use a restricted key that starts rk_live_')
            self.stripe_get('/v1/charges', [('limit', 1)], key=stripe)
            c['stripe_key'] = stripe
        if calendly:
            if len(calendly) < 20:
                raise ValueError('That does not look like a Calendly personal access token')
            self.calendly_get('https://api.calendly.com/users/me', token=calendly)
            c['calendly_token'] = calendly
        if data.get('disconnect') in ('stripe', 'calendly'):
            c['stripe_key' if data['disconnect'] == 'stripe' else 'calendly_token'] = ''
        self.save_config(c)
        threading.Thread(target=self.sync, daemon=True).start()
        return self.connections()

    # HTTP -------------------------------------------------------------------------
    def _get(self, url, headers):
        req = urllib.request.Request(url, headers=dict(headers, **{'User-Agent': 'JoshOS-Admin', 'Accept': 'application/json'}))
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode('utf-8'))
        except urllib.error.HTTPError as e:
            body = e.read().decode('utf-8', 'replace')[:300]
            if e.code in (401, 403):
                raise ValueError('The service rejected the key (%s). Check it has read access.' % e.code)
            raise ValueError('Request failed (%s): %s' % (e.code, body))

    def stripe_get(self, path, params=None, key=None):
        key = key or self.config()['stripe_key']
        query = urllib.parse.urlencode(params or [], doseq=True)
        return self._get('https://api.stripe.com' + path + ('?' + query if query else ''), {'Authorization': 'Bearer ' + key})

    def calendly_get(self, url, params=None, token=None):
        token = token or self.config()['calendly_token']
        if params:
            url += ('&' if '?' in url else '?') + urllib.parse.urlencode(params)
        return self._get(url, {'Authorization': 'Bearer ' + token})

    def stripe_all(self, path, params):
        out, after = [], None
        for _ in range(50):
            p = list(params) + ([('starting_after', after)] if after else [])
            page = self.stripe_get(path, p)
            out += page.get('data', [])
            if not page.get('has_more') or not page.get('data'):
                break
            after = page['data'][-1]['id']
        return out

    # sync -------------------------------------------------------------------------
    def sync(self):
        if not self.sync_lock.acquire(False):
            return self.connections()
        self.status['running'] = True
        try:
            c, cache = self.config(), self.cache()
            if c.get('stripe_key'):
                try:
                    charges = self.stripe_all('/v1/charges', [('limit', 100), ('expand[]', 'data.customer')])
                    sessions = self.stripe_all('/v1/checkout/sessions', [('limit', 100), ('status', 'complete'), ('expand[]', 'data.line_items')])
                    cache['stripe'] = dict(synced_at=now().isoformat(), source='live', error='',
                                           payments=normalize_stripe(charges, sessions, c['owner_emails']))
                except Exception as e:
                    cache.setdefault('stripe', {})['error'] = str(e)[:300]
            if c.get('calendly_token'):
                try:
                    cache['calendly'] = self.calendly_pull(cache.get('calendly') or {})
                except Exception as e:
                    cache.setdefault('calendly', {})['error'] = str(e)[:300]
            self.save_cache(cache)
        finally:
            self.status.update(running=False, finished_at=now().isoformat())
            self.sync_lock.release()
        return self.connections()

    def calendly_pull(self, previous):
        me = self.calendly_get('https://api.calendly.com/users/me')['resource']
        start = (now() - timedelta(days=240)).strftime('%Y-%m-%dT%H:%M:%SZ')
        url = 'https://api.calendly.com/scheduled_events'
        params = dict(user=me['uri'], min_start_time=start, status='active', count=100, sort='start_time:asc')
        events = []
        for _ in range(20):
            page = self.calendly_get(url, params)
            events += page.get('collection', [])
            url = (page.get('pagination') or {}).get('next_page')
            params = None
            if not url:
                break
        known = previous.get('invitees') or {}
        invitees = {}
        for e in events:
            uri = e['uri']
            if uri in known:
                invitees[uri] = known[uri]
                continue
            got = self.calendly_get(uri + '/invitees', dict(count=5)).get('collection') or []
            first = next((i for i in got if i.get('status') == 'active'), got[0] if got else {})
            invitees[uri] = dict(name=first.get('name') or '', email=(first.get('email') or '').lower(),
                                 reschedule_url=first.get('reschedule_url') or '')
        rows = []
        for e in events:
            inv = invitees.get(e['uri']) or {}
            loc = e.get('location') or {}
            rows.append(dict(uri=e['uri'], event=e.get('name') or '', start=e['start_time'], end=e['end_time'],
                             join_url=loc.get('join_url') or '', name=inv.get('name', ''), email=inv.get('email', ''),
                             reschedule_url=inv.get('reschedule_url', '')))
        return dict(synced_at=now().isoformat(), source='live', error='', events=rows, invitees=invitees)

    def loop(self, every=1800):
        import time
        time.sleep(30)
        while True:
            try:
                c = self.config()
                if c.get('stripe_key') or c.get('calendly_token'):
                    self.sync()
            except Exception:
                pass
            time.sleep(every)

    # roster -------------------------------------------------------------------------
    def payments(self, kind=None):
        rows = (self.cache().get('stripe') or {}).get('payments') or []
        return [p for p in rows if kind is None or p['kind'] == kind]

    def events(self):
        return (self.cache().get('calendly') or {}).get('events') or []

    def folders(self):
        root = self.root
        if not root.is_dir():
            return []
        return sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith(('_', '.')))

    def roster(self):
        """Every client: one per folder, plus anyone who paid for coaching without a folder yet."""
        c = self.config()
        index = parse_index(self.root / '_client-index.md')
        meta = c.get('clients') or {}
        people = []
        for folder in self.folders():
            row = index.get(folder.name) or {}
            m = meta.get(folder.name) or {}
            people.append(dict(
                id=folder.name, name=title_case(m.get('name') or row.get('client') or folder.name), folder=folder.name,
                emails=[e.lower() for e in m.get('emails', [])], programme=m.get('programme') or row.get('programme') or '',
                status=m.get('status') or row.get('status') or '', joined=m.get('joined') or row.get('started') or '',
                testimonial=row.get('testimonial') or '', company=m.get('company') or ''))
        client_pay = self.payments('client')
        # Folderless clients saved under one email can list their other addresses too.
        alias = {e.lower(): k for k, m in meta.items() if '@' in k for e in m.get('emails', [])}
        for p in client_pay:
            key = alias.get(p['email'], p['email'])
            hit = next((x for x in people if p['email'] in x['emails'] or x['id'] == key), None)
            if not hit:
                hit = next((x for x in people if not x['emails'] and names_match(x['name'], p['name'])), None)
                if hit:
                    hit['emails'].append(p['email'])
            if not hit:
                m = meta.get(key) or {}
                emails = [key] + [e.lower() for e in m.get('emails', []) if e.lower() != key]
                hit = dict(id=key, name=m.get('name') or p['name'] or p['email'], folder='', emails=emails,
                           programme=m.get('programme') or '', status=m.get('status') or '', joined=m.get('joined') or '',
                           testimonial='', company=m.get('company') or '')
                people.append(hit)
            hit.setdefault('payments', []).append(p)
        events = self.events()
        today = now()
        for x in people:
            pays = x.setdefault('payments', [])
            x['ltv'] = sum(p['amount'] - p['refunded'] for p in pays)
            x['currency'] = pays[0]['currency'] if pays else 'USD'
            if not x['joined'] and pays:
                x['joined'] = pays[0]['created'][:10]
            if not x['programme'] and pays:
                x['programme'] = pays[0]['product']
            mine = [e for e in events if e['email'] and e['email'] in x['emails']]
            upcoming = sorted((e for e in mine if e['start'] > today.isoformat()), key=lambda e: e['start'])
            x['next_call'] = upcoming[0] if upcoming else None
            x['events'] = mine
            x['calls'] = self.folder_calls(x['folder']) if x['folder'] else []
            past_folder_days = {k['date'] for k in x['calls']}
            past_bookings = [e for e in mine if e['start'] <= today.isoformat()]
            extra = [e for e in past_bookings if self.dubai_day(e['start']) not in past_folder_days]
            x['call_count'] = len(x['calls']) + len(extra)
            last = max([k['date'] for k in x['calls']] + [self.dubai_day(e['start']) for e in past_bookings] or [''])
            x['last_call'] = last
            if not x['joined']:
                x['joined'] = min([k['date'] for k in x['calls'] if k['date']] + [self.dubai_day(e['start']) for e in mine] or [''])
            if not x['status']:
                x['status'] = 'New' if x['joined'] and x['joined'] >= (today - timedelta(days=45)).strftime('%Y-%m-%d') else 'Active'
        people.sort(key=lambda x: (x['joined'] or '0000'), reverse=True)
        return people

    @staticmethod
    def dubai_day(stamp):
        try:
            return datetime.fromisoformat(stamp.replace('Z', '+00:00')).astimezone(DUBAI).strftime('%Y-%m-%d')
        except ValueError:
            return stamp[:10]

    def folder_path(self, folder):
        root = self.root.resolve()
        p = (root / folder).resolve()
        if p.parent != root or not p.is_dir():
            raise KeyError('Client folder not found')
        return p

    def folder_calls(self, folder):
        base = self.folder_path(folder)
        calls = []
        for section in SESSION_DIRS:
            d = base / section
            if not d.is_dir():
                continue
            for item in sorted(d.iterdir()):
                if not item.is_dir():
                    continue
                date, title = call_date(item.name)
                files = [f for f in item.iterdir() if f.is_file() and f.name.lower() not in SKIP_FILES]
                parts, links, local_video = {}, [], ''
                for f in files:
                    n = f.name.lower()
                    if 'transcript' in n:
                        parts['transcript'] = f.name
                    elif 'ai-notes' in n or 'summary' in n:
                        parts['summary'] = f.name
                    elif n.endswith('.md') and ('notes' in n or 'review' in n):
                        parts['notes'] = f.name
                    if n.endswith(('.mp4', '.mkv', '.mov', '.m4a', '.webm')):
                        local_video = f.name
                    if n.startswith('recording') and n.endswith(('.md', '.txt')):
                        try:
                            text = f.read_text(encoding='utf-8-sig', errors='replace')
                        except OSError:
                            text = ''
                        for line in text.splitlines():
                            for u in find_urls(line):
                                label = 'Meet chat' if 'chat' in line.lower() else 'Drive folder' if '/folders/' in u else 'Recording'
                                links.append(dict(label=label, url=u))
                        parts.setdefault('notes', f.name)
                calls.append(dict(id=section + '/' + item.name, date=date, title=title or item.name,
                                  section='Onboarding' if section.startswith('01') else 'Session',
                                  parts=parts, links=links, local_video=local_video,
                                  files=len(files)))
        calls.sort(key=lambda k: k['date'], reverse=True)
        return calls

    def tree(self, folder):
        base = self.folder_path(folder)
        def walk(p, depth):
            out = []
            try:
                items = sorted(p.iterdir(), key=lambda i: (i.is_file(), i.name.lower()))
            except OSError:
                return out
            for i in items:
                if i.name.lower() in SKIP_FILES or i.name.startswith('.'):
                    continue
                rel = i.relative_to(base).as_posix()
                if i.is_dir():
                    out.append(dict(name=i.name, path=rel, dir=True, children=walk(i, depth + 1) if depth < 6 else []))
                else:
                    st = i.stat()
                    out.append(dict(name=i.name, path=rel, dir=False, size=st.st_size,
                                    modified=datetime.fromtimestamp(st.st_mtime, timezone.utc).isoformat()))
            return out
        return walk(base, 0)

    # pages ----------------------------------------------------------------------------
    def clients_page(self):
        people = self.roster()
        today = now()
        week = (today + timedelta(days=7)).isoformat()
        upcoming = sorted((x['next_call'] | dict(client=x['name'], id=x['id']) for x in people
                           if x['next_call'] and x['next_call']['start'] <= week), key=lambda e: e['start'])
        month = today.astimezone(DUBAI).strftime('%Y-%m')
        rows = [dict(id=x['id'], name=x['name'], email=(x['emails'] or [''])[0], folder=x['folder'], programme=x['programme'],
                     status=x['status'], joined=x['joined'], ltv=x['ltv'], currency=x['currency'],
                     next_call=x['next_call'], last_call=x['last_call'], call_count=x['call_count'],
                     payments=len(x['payments'])) for x in people]
        revenue = sum(x['ltv'] for x in people)
        paying = [x for x in people if x['ltv'] > 0]
        stats = dict(
            active=sum(1 for x in people if x['status'].lower() in ('active', 'new')),
            total=len(people),
            revenue=revenue,
            revenue_month=sum(p['amount'] - p['refunded'] for x in people for p in x['payments'] if p['created'][:7] == month),
            avg_ltv=round(revenue / len(paying)) if paying else 0,
            calls_week=len(upcoming),
            next=upcoming[0] if upcoming else None,
            no_folder=sum(1 for x in people if not x['folder']),
        )
        return dict(stats=stats, clients=rows, connections=self.connections())

    def client(self, cid):
        people = self.roster()
        x = next((p for p in people if p['id'] == cid), None)
        if not x:
            raise KeyError('Client not found')
        about = ''
        if x['folder']:
            readme = self.folder_path(x['folder']) / 'README.md'
            try:
                lines = [l.strip() for l in readme.read_text(encoding='utf-8-sig').splitlines()]
                body = [l for l in lines if l and not l.startswith('#')]
                about = ' '.join(body[:1]) if body else ''
                if len(body) > 1 and not body[1].startswith(('-', '`')):
                    about += '\n\n' + body[1]
            except OSError:
                pass
        today = now().isoformat()
        calls = [dict(k, source='folder') for k in x['calls']]
        by_day = {k['date']: k for k in calls}
        for e in x['events']:
            day = self.dubai_day(e['start'])
            if e['start'] > today:
                continue
            if day in by_day:
                k = by_day[day]
                k.setdefault('booking', e)
                if not k['title'] or fold(k['title'])[:1] == fold(x['name'])[:1]:
                    k['title'] = e['event']
            else:
                k = dict(id='calendly:' + e['uri'].rsplit('/', 1)[-1], date=day, title=e['event'], section='Booking',
                         parts={}, links=[], local_video='', files=0, source='calendly', booking=e)
                calls.append(k)
                by_day[day] = k
        for k in calls:
            if not k['title'] or fold(k['title'])[:1] == fold(x['name'])[:1]:
                k['title'] = 'Onboarding call' if k['section'] == 'Onboarding' else 'Session'
        calls.sort(key=lambda k: k['date'], reverse=True)
        return dict(
            id=x['id'], name=x['name'], emails=x['emails'], folder=x['folder'], programme=x['programme'],
            status=x['status'], joined=x['joined'], ltv=x['ltv'], currency=x['currency'], testimonial=x['testimonial'], company=x.get('company', ''),
            next_call=x['next_call'], about=about, calls=calls,
            upcoming=[e for e in x['events'] if e['start'] > today],
            payments=list(reversed(x['payments'])),
            tree=self.tree(x['folder']) if x['folder'] else [],
            folder_path=str(self.root / x['folder']) if x['folder'] else '',
        )

    def text(self, cid, rel):
        base = self.folder_path(cid)
        p = (base / rel).resolve()
        if not p.is_relative_to(base) or not p.is_file():
            raise KeyError('File not found')
        if p.stat().st_size > TEXT_LIMIT * 4:
            raise ValueError('That file is too large to show here. Open it instead.')
        return dict(name=p.name, text=p.read_text(encoding='utf-8-sig', errors='replace')[:TEXT_LIMIT])

    def open(self, cid, rel=''):
        base = self.folder_path(cid)
        p = (base / rel).resolve() if rel else base
        if not p.is_relative_to(base) or not p.exists():
            raise KeyError('File not found')
        if os.name == 'nt':
            if p.is_dir():
                subprocess.Popen(['explorer.exe', str(p)])
            else:
                os.startfile(str(p))
        return dict(ok=True, path=str(p))

    def update(self, cid, data):
        c = self.config()
        meta = c['clients'].setdefault(cid, {})
        if 'emails' in data:
            emails = data['emails'] if isinstance(data['emails'], list) else re.split(r'[\s,;]+', data['emails'])
            emails = [e.strip().lower() for e in emails if e.strip()]
            for e in emails:
                if not re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+', e):
                    raise ValueError(e + ' is not an email address')
            meta['emails'] = emails
        for k in ('programme', 'status', 'joined', 'company'):
            if k in data:
                meta[k] = str(data[k]).strip()[:120]
        self.save_config(c)
        return self.client(cid)

    def create_folder(self, cid):
        person = next((p for p in self.roster() if p['id'] == cid), None)
        if not person:
            raise KeyError('Client not found')
        if person['folder']:
            raise ValueError('This client already has a folder')
        name = re.sub(r'[<>:"/\\|?*]+', '', person['name']).strip()
        if not name:
            raise ValueError('This client needs a name before a folder can be made')
        dest = self.root / name
        if dest.exists():
            raise ValueError('A folder called %s already exists' % name)
        template = self.root / '_new-client-template'
        if template.is_dir():
            shutil.copytree(template, dest, ignore=shutil.ignore_patterns('README.md'))
        for d in STANDARD_DIRS:
            (dest / d).mkdir(parents=True, exist_ok=True)
        (dest / 'README.md').write_text('# %s\n\n%s\n' % (name, person['programme'] or ''), encoding='utf-8')
        c = self.config()
        old = c['clients'].pop(cid, {})
        old['emails'] = sorted(set(old.get('emails', []) + person['emails']))
        old['name'] = person['name']
        c['clients'][name] = old
        self.save_config(c)
        index = self.root / '_client-index.md'
        try:
            lines = index.read_text(encoding='utf-8-sig').splitlines()
            last = max(i for i, l in enumerate(lines) if l.strip().startswith('|'))
            row = '| %s | `%s/` | %s | %s | Active | Not yet |' % (person['name'], name, person['programme'] or 'Unrecorded', person['joined'] or now().strftime('%Y-%m-%d'))
            lines.insert(last + 1, row)
            index.write_text('\n'.join(lines) + '\n', encoding='utf-8')
        except (OSError, ValueError):
            pass
        return self.client(name)

    # customers ------------------------------------------------------------------------
    def customers_page(self):
        pays = self.payments('course')
        people = {}
        for p in pays:
            x = people.setdefault(p['email'], dict(email=p['email'], name=p['name'], country=p['country'], first=p['created'],
                                                   last=p['created'], total=0, orders=0, items=[], bump=False, currency=p['currency']))
            x['total'] += p['amount'] - p['refunded']
            x['orders'] += 1
            x['last'] = max(x['last'], p['created'])
            x['first'] = min(x['first'], p['created'])
            x['bump'] = x['bump'] or p['bump']
            x['name'] = x['name'] or p['name']
            x['country'] = x['country'] or p['country']
            x['items'].append(dict(product=p['product'], amount=p['amount'], refunded=p['refunded'], created=p['created'], bump=p['bump']))
        rows = sorted(people.values(), key=lambda x: x['first'], reverse=True)
        rows = [x for x in rows if x['total'] > 0] + [dict(x, refunded=True) for x in rows if x['total'] <= 0]
        today = now().astimezone(DUBAI)
        this_month = today.strftime('%Y-%m')
        last = (today.replace(day=1) - timedelta(days=1))
        last_month = last.strftime('%Y-%m')
        cutoff_day = today.day
        def month_sum(m, upto=None):
            sel = [p for p in pays if self.dubai_day(p['created'])[:7] == m and (upto is None or int(self.dubai_day(p['created'])[8:10]) <= upto)]
            return len(sel), sum(p['amount'] - p['refunded'] for p in sel)
        n_this, r_this = month_sum(this_month)
        n_last, r_last = month_sum(last_month, cutoff_day)
        paid = [x for x in rows if x['total'] > 0]
        revenue = sum(x['total'] for x in paid)
        daily = {}
        for p in pays:
            d = self.dubai_day(p['created'])
            daily[d] = daily.get(d, 0) + 1
        countries = {}
        for x in paid:
            if x['country']:
                countries[x['country']] = countries.get(x['country'], 0) + 1
        def window(lo, hi):
            sel = [p for p in pays if (now() - timedelta(days=hi)).isoformat() < p['created'] <= (now() - timedelta(days=lo)).isoformat()]
            return len(sel), sum(p['amount'] - p['refunded'] for p in sel)
        n_week, r_week = window(0, 7)
        n_prev, _ = window(7, 14)
        stats = dict(customers=len(paid), revenue=revenue, week_sales=n_week, week_revenue=r_week, prev_week_sales=n_prev,
                     month_sales=n_this, month_revenue=r_this, last_month_sales=n_last, last_month_revenue=r_last,
                     month_label=today.strftime('%B'), last_label=last.strftime('%B'), day=cutoff_day,
                     aov=round(revenue / len(paid)) if paid else 0,
                     bump_rate=round(100 * sum(1 for x in paid if x['bump']) / len(paid)) if paid else 0,
                     countries=len(countries), top_countries=sorted(countries.items(), key=lambda kv: -kv[1])[:5],
                     daily=daily, first_sale=min((p['created'] for p in pays), default=''))
        return dict(stats=stats, customers=rows, connections=self.connections())
