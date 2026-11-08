"""Course lessons, from the first idea to the course app.

Every lesson in Heard at the Top, the Career Track, the Practical Career Track and the data
storytelling course has one record here, and Josh OS is the record for them (the Notion build
trackers were imported once, on 8 Nov 2026, and aren't read any more).

A lesson moves through STAGES. Saskia prepares the lesson and drafts the script. Josh edits it
and approves it, and slides can only be built once the approved script is the current one.
Saskia builds the space-bar slides and Josh OS checks every beat. Josh records on the Lessons
recording page, which this module feeds. The lesson guide and thumbnail are added, then Publish
copies the video, the lesson guide PDF and the thumbnail into the Google Drive folder that the
Lovable course app picks lessons up from.

The files themselves stay on the E drive, named the way the course folders already name them
(number, date, version, slug). Josh OS finds the newest version of each and writes a new script
version there whenever Josh approves a changed script.
"""
import hashlib
import json
import re
import shutil
import threading
import unicodedata
import uuid
from datetime import date
from pathlib import Path

from .store import Conflict, now

STAGES = ['Planned', 'Preparing', 'Drafting', 'Script review', 'Approved', 'Slides', 'Ready to record',
          'Recorded', 'Guide and thumbnail', 'Ready to publish', 'Published']
AT = {s: i for i, s in enumerate(STAGES)}

CONTENT_DIR = r'E:\CareerOS\04_Programme\Course_Content'
COURSES = {
    'hatt': dict(name='Heard at the Top', dir='Heard_at_the_Top', track='Communication', track_key='communication',
                 publish='2 · Heard at the Top scripts'),
    'career': dict(name='Career Track', dir='Career_Track', track='Career', track_key='career', publish='5 · Career Track'),
    'practical': dict(name='Practical Career Track', dir='Practical_Career_Track', track='Practical career',
                      track_key='practical', publish='6 · Practical Career Track'),
    'data': dict(name='Data storytelling', dir='Data_Storytelling', track='Data storytelling', track_key='data',
                 publish='7 · Data storytelling'),
}
# The kinds of file each lesson has, the folder they live in and how the file name ends.
KINDS = {
    'script': ('Scripts', re.compile(r'^(?P<n>[0-9A-Z]+\.\d+)_(?P<d>\d{4}-\d{2}-\d{2})_v(?P<v>\d+)_(?P<slug>.+)\.md$')),
    'slides': ('Slides', re.compile(r'^(?P<n>[0-9A-Z]+\.\d+)_(?P<d>\d{4}-\d{2}-\d{2})_v(?P<v>\d+)_(?P<slug>.+)_slides\.html$')),
    'guide': ('Lesson_Guides', re.compile(r'^(?P<n>[0-9A-Z]+\.\d+)_(?P<d>\d{4}-\d{2}-\d{2})_v(?P<v>\d+)_(?P<slug>.+)_lesson-guide\.pdf$')),
    'thumbnail': ('Thumbnails', re.compile(r'^(?P<n>[0-9A-Z]+\.\d+)_(?P<d>\d{4}-\d{2}-\d{2})_v(?P<v>\d+)_(?P<slug>.+)_thumb\.(jpg|jpeg|png)$')),
}
TEXT = {'script': 300_000, 'prep': 40_000, 'notes': 6_000, 'title': 200}
VIDEO_TYPES = ('.mp4', '.mov', '.m4v')
# What each stage is waiting on, said the way Josh would see it on the card.
NEXT = {
    'Planned': 'Ask Saskia to prepare it',
    'Preparing': 'Saskia is gathering what the lesson needs',
    'Drafting': 'Ask Saskia to draft the script',
    'Script review': 'Read the script and approve it',
    'Approved': 'Ask Saskia to build the slides',
    'Slides': 'Slides are being built and checked',
    'Ready to record': 'Record it',
    'Recorded': 'Add the lesson guide and thumbnail',
    'Guide and thumbnail': 'Add the lesson guide and thumbnail',
    'Ready to publish': 'Publish it to Google Drive',
    'Published': 'In the course app folder',
}


def digest(text):
    return hashlib.sha256((text or '').encode('utf-8')).hexdigest()


def slugify(text):
    t = unicodedata.normalize('NFKD', text or '').encode('ascii', 'ignore').decode().lower()
    return re.sub(r'[^a-z0-9]+', '-', t.replace("'", '')).strip('-')[:60] or 'lesson'


def safe_name(text):
    """A name Windows and Google Drive both accept, keeping it readable for buyers."""
    t = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '', text or '').strip().rstrip('.')
    return re.sub(r'\s+', ' ', t)[:120] or 'Lesson'


def sort_key(number):
    part, _, n = (number or '').partition('.')
    return (0 if part.isdigit() else 1, int(part) if part.isdigit() else ord(part[:1] or 'Z'), int(n) if n.isdigit() else 0)


def _text(v, limit, label):
    if v is None:
        return ''
    if not isinstance(v, str) or len(v) > limit:
        raise ValueError(f'{label} must be text, up to {limit:,} characters.')
    return v


class CourseLessons:
    def __init__(self, store, root, recording=None):
        self.store, self.root, self.recording = store, Path(root), recording
        self.lock = threading.Lock()
        self.jobs = {}  # lesson id -> thread, for Saskia's work and publishing
        self.agents = None  # set by the server once the team is ready
        with store.connect() as c:
            c.execute('CREATE TABLE IF NOT EXISTS course_lessons(id TEXT PRIMARY KEY,course TEXT NOT NULL,body TEXT NOT NULL)')
            empty = c.execute('SELECT COUNT(*) FROM course_lessons').fetchone()[0] == 0
        if empty:
            self.import_trackers()
        self.link_recording()
        # A job that was running when Josh OS closed can't finish, so say so instead of showing it as busy.
        for l in self._all():
            for key in ('job', 'publish'):
                if (l.get(key) or {}).get('status') in ('running', 'copying'):
                    l[key] = dict(l[key], status='failed', message='Josh OS restarted before this finished. Start it again.')
                    self._put(l)

    # Settings ---------------------------------------------------------------
    def settings(self):
        try:
            return json.loads((self.root / 'settings.json').read_text(encoding='utf-8-sig'))
        except (OSError, ValueError):
            return {}

    def content_dir(self):
        return Path(self.settings().get('course_content_dir') or CONTENT_DIR)

    def publish_root(self):
        """The Google Drive for desktop folder on this PC that holds the course folders Lovable reads."""
        value = self.settings().get('lesson_publish_root') or ''
        return Path(value) if value else None

    def set_publish_root(self, value):
        value = (value or '').strip().strip('"')
        if value:
            p = Path(value)
            if not p.is_dir():
                raise ValueError("That folder isn't on this PC. Install Google Drive for desktop, sign in, then paste the folder's path.")
            if not (p / COURSES['hatt']['publish']).is_dir():
                raise ValueError(f'Pick the Drive folder that has "{COURSES["hatt"]["publish"]}" inside it.')
            value = str(p)
        path = self.root / 'settings.json'
        s = self.settings()
        s['lesson_publish_root'] = value
        path.write_text(json.dumps(s, indent=2), encoding='utf-8')
        return dict(publish_root=value)

    # Storage ----------------------------------------------------------------
    def _all(self):
        with self.store.connect() as c:
            return [json.loads(r['body']) for r in c.execute('SELECT body FROM course_lessons')]

    def _get(self, lid):
        with self.store.connect() as c:
            row = c.execute('SELECT body FROM course_lessons WHERE id=?', (lid,)).fetchone()
        if not row:
            raise KeyError('Lesson not found')
        return json.loads(row['body'])

    def _put(self, l, expected=None):
        with self.store.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            row = c.execute('SELECT body FROM course_lessons WHERE id=?', (l['id'],)).fetchone()
            if expected is not None and row and json.loads(row['body'])['revision'] != expected:
                raise Conflict('This lesson changed in another window. Reload before saving again.')
            l['revision'] = (json.loads(row['body'])['revision'] if row else 0) + 1
            l['updated_at'] = now()
            c.execute('INSERT OR REPLACE INTO course_lessons VALUES(?,?,?)', (l['id'], l['course'], json.dumps(l)))
        return l

    def _log(self, l, stage, why=''):
        if l.get('stage') != stage:
            l['history'] = (l.get('history') or [])[-40:] + [dict(at=now(), stage=stage, why=why)]
            l['stage'] = stage

    # Files on the E drive -----------------------------------------------------
    def scan(self, course):
        """The newest version of every script, slide file, lesson guide and thumbnail, by lesson number."""
        base = self.content_dir() / COURSES[course]['dir']
        found = {}
        for kind, (folder, pattern) in KINDS.items():
            d = base / folder
            if not d.is_dir():
                continue
            for f in d.iterdir():
                m = pattern.match(f.name) if f.is_file() else None
                if not m:
                    continue
                key = (int(m['v']), m['d'])
                best = found.setdefault(m['n'], {}).get(kind)
                if not best or key > best['key']:
                    found[m['n']][kind] = dict(path=str(f), version=int(m['v']), date=m['d'], slug=m['slug'], key=key)
        for files in found.values():
            for f in files.values():
                f.pop('key', None)
        return found

    def files(self, l, scanned=None):
        f = dict((scanned if scanned is not None else self.scan(l['course'])).get(l['number']) or {})
        video = self.video(l)
        if video:
            f['video'] = video
        return f

    def video(self, l):
        """The recording to publish: the take chosen on the recording page, or a file Josh picked by hand."""
        if l.get('video') and Path(l['video']).is_file():
            return dict(path=l['video'], source='chosen by hand')
        rec = self.recording_state(l)
        if rec and rec.get('take') and Path(rec['take']['path']).is_file():
            return dict(path=rec['take']['path'], name=rec['take']['name'], source='recording page')
        return None

    # The recording page -------------------------------------------------------
    def link_recording(self):
        """Give every lesson an entry on the recording page, so lessons from any course can be recorded."""
        if self.recording is None:
            return
        scans = {k: self.scan(k) for k in COURSES}
        for l in self._all():
            if l['id'] in self.recording.entries:
                continue
            files = scans[l['course']].get(l['number']) or {}
            c = COURSES[l['course']]
            self.recording.entries[l['id']] = dict(
                id=l['id'], source_id=l['id'], code=l['number'], title=l['title'], track=c['track'], track_key=c['track_key'],
                chapter=f"{l['part']} · {l['part_name']}", slug=(files.get('script') or {}).get('slug') or slugify(l['title']),
                script=(files.get('script') or {}).get('path', ''), source_url=l.get('source_url', ''),
                materials=[dict(kind='slides', path=files['slides']['path'])] if files.get('slides') else [],
                estimated_minutes=l.get('minutes'))

    def recording_state(self, l):
        if self.recording is None or l['id'] not in self.recording.entries:
            return None
        # Read straight from the recording tables, so listing every lesson doesn't re-run the audio checks.
        with self.store.connect() as c:
            row = c.execute('SELECT body FROM lesson_state WHERE id=?', (l['id'],)).fetchone()
            takes = [json.loads(r['body']) for r in c.execute('SELECT body FROM lesson_takes WHERE lesson_id=? ORDER BY rowid', (l['id'],))]
        s = json.loads(row['body']) if row else {}
        take = next((t for t in takes if t['id'] == s.get('preferred_take')), None)
        done = s.get('recorded_completion') or {}
        return dict(takes=len(takes), recorded=done.get('status') == 'recorded',
                    take=dict(path=take['path'], name=take['name'], duration=take.get('duration')) if take else None)

    # Import -----------------------------------------------------------------
    def import_trackers(self, path=None):
        """One-time import of the Notion trackers and the Udemy lesson plan, with stages read from the E drive."""
        path = Path(path) if path else self.root / 'Data' / 'course-import.json'
        if not path.is_file():
            return 0
        seed = json.loads(path.read_text(encoding='utf-8'))
        count = 0
        for course, data in seed['courses'].items():
            scanned = self.scan(course)
            for order, (number, title, kind, minutes, notion_stage, page) in enumerate(data['lessons']):
                part = number.split('.')[0]
                lid = page or f'{course}-{number}'
                files = scanned.get(number) or {}
                script = ''
                if files.get('script'):
                    try:
                        script = Path(files['script']['path']).read_text(encoding='utf-8-sig')
                    except OSError:
                        script = ''
                l = dict(id=lid, course=course, number=number, part=part, part_name=data['parts'].get(part, ''), title=title,
                         type=kind, minutes=minutes, order=order, stage='Planned', notion_stage=notion_stage,
                         source_url=f'https://app.notion.com/p/{page}' if page else '', script=script,
                         script_hash=digest(script) if script else '', approved=None, prep='', notes='', video='',
                         history=[], job=None, publish=None, slides_check=None, created_at=now())
                l['stage'] = self.imported_stage(l, files)
                if AT[l['stage']] >= AT['Approved'] and script:
                    l['approved'] = dict(at=now(), hash=l['script_hash'], by='import')
                l['history'] = [dict(at=now(), stage=l['stage'], why=f'Imported. Notion said {notion_stage}.')]
                self._put(l)
                count += 1
        return count

    def imported_stage(self, l, files):
        if not files.get('script'):
            return 'Planned'
        recorded = bool(self.video(l))
        if recorded:
            return 'Ready to publish' if files.get('guide') and files.get('thumbnail') else 'Guide and thumbnail'
        if files.get('slides'):
            return 'Ready to record'
        if l['notion_stage'] in ('Script ready', 'Slides', 'Filming', 'Editing', 'Uploaded', 'Done'):
            return 'Approved'
        return 'Script review'

    # Reading ----------------------------------------------------------------
    def summary(self, l, scanned):
        f = self.files(l, scanned)
        return dict({k: l.get(k) for k in ('id', 'course', 'number', 'part', 'part_name', 'title', 'type', 'minutes', 'stage', 'order', 'updated_at')},
                    next=NEXT[l['stage']], has={k: k in f for k in ('script', 'slides', 'guide', 'thumbnail', 'video')},
                    job=l.get('job'), publish=l.get('publish'), stale=self.approval_stale(l))

    def all(self):
        scans = {k: self.scan(k) for k in COURSES}
        items = sorted(self._all(), key=lambda l: (list(COURSES).index(l['course']), sort_key(l['number'])))
        root = self.publish_root()
        return dict(items=[self.summary(l, scans[l['course']]) for l in items], stages=STAGES,
                    courses={k: dict(name=v['name'], publish=v['publish']) for k, v in COURSES.items()},
                    publish_root=str(root) if root else '', publish_ready=bool(root and root.is_dir()))

    def get(self, lid):
        l = self._get(lid)
        f = self.files(l)
        rec = self.recording_state(l)
        return dict(l, files=f, recording=rec, next=NEXT[l['stage']], stale=self.approval_stale(l),
                    cues=bool(self.cues(lid)), blockers=self.blockers(l, f), destination=self.destination(l, quiet=True),
                    neighbours=self.neighbours(l))

    def neighbours(self, l):
        same = sorted((x for x in self._all() if x['course'] == l['course']), key=lambda x: sort_key(x['number']))
        i = next(i for i, x in enumerate(same) if x['id'] == l['id'])
        pick = lambda x: dict(id=x['id'], number=x['number'], title=x['title']) if x else None
        return dict(previous=pick(same[i - 1] if i else None), next=pick(same[i + 1] if i + 1 < len(same) else None))

    def approval_stale(self, l):
        a = l.get('approved')
        return bool(a and a.get('hash') != digest(l.get('script')))

    # Changing a lesson ----------------------------------------------------------
    def save(self, lid, changes, revision):
        if not isinstance(changes, dict) or set(changes) - {'script', 'prep', 'notes', 'title', 'minutes', 'video'}:
            raise ValueError('Unknown lesson field.')
        if type(revision) is not int:
            raise ValueError('A saved revision is required.')
        with self.lock:
            l = self._get(lid)
            for k in ('script', 'prep', 'notes', 'title'):
                if k in changes:
                    l[k] = _text(changes[k], TEXT[k], k.capitalize())
            if 'minutes' in changes:
                m = changes['minutes']
                if m not in (None, '') and (isinstance(m, bool) or not isinstance(m, (int, float)) or not 0 < m < 120):
                    raise ValueError('Minutes must be a number between 1 and 120.')
                l['minutes'] = m or None
            if 'video' in changes:
                v = (changes['video'] or '').strip().strip('"')
                if v and (not Path(v).is_file() or Path(v).suffix.lower() not in VIDEO_TYPES):
                    raise ValueError('Choose an MP4 or MOV file that is on this PC.')
                l['video'] = v
            if 'script' in changes:
                l['script_hash'] = digest(l['script'])
                # Slides are only built from the script Josh approved, so a changed script goes back to him.
                if self.approval_stale(l) and AT['Approved'] <= AT[l['stage']] <= AT['Ready to record']:
                    self._log(l, 'Script review', 'The script changed after it was approved.')
                if AT[l['stage']] < AT['Script review'] and l['script'].strip():
                    self._log(l, 'Script review', 'A script was added.')
            return self._put(l, revision)

    def blockers(self, l, f=None):
        """What stops this lesson moving to each later stage, so the page can say it plainly."""
        f = f if f is not None else self.files(l)
        out = {}
        if not (l.get('script') or '').strip():
            out['Approved'] = 'There is no script yet.'
        if not l.get('approved') or self.approval_stale(l):
            out['Slides'] = 'Approve the current script first.'
        if 'slides' not in f:
            out['Ready to record'] = 'There is no slide file yet.'
        if 'video' not in f:
            out['Recorded'] = 'Choose the take on the recording page, or pick the video file.'
        missing = [n for k, n in (('video', 'the video'), ('guide', 'the lesson guide PDF'), ('thumbnail', 'the thumbnail')) if k not in f]
        if missing:
            out['Ready to publish'] = 'Still needs ' + ', '.join(missing[:-1]) + (' and ' if len(missing) > 1 else '') + missing[-1] + '.'
        return out

    def move(self, lid, stage, revision):
        if stage not in STAGES:
            raise ValueError('Unknown stage.')
        if stage == 'Published':
            raise ValueError('Use Publish, which copies the files to Google Drive first.')
        with self.lock:
            l = self._get(lid)
            if AT[stage] > AT[l['stage']]:
                gates = self.blockers(l)
                for gate in ('Approved', 'Slides', 'Ready to record', 'Recorded', 'Ready to publish'):
                    if AT[stage] >= AT[gate] and gate in gates:
                        raise ValueError(gates[gate])
                if AT[stage] >= AT['Approved'] and (not l.get('approved') or self.approval_stale(l)):
                    raise ValueError('Approve the script to move it on.')
            self._log(l, stage, 'Moved by Josh.')
            return self._put(l, revision)

    def approve(self, lid, revision):
        with self.lock:
            l = self._get(lid)
            if not (l.get('script') or '').strip():
                raise ValueError('There is no script to approve yet.')
            path = self.write_script(l)
            l['approved'] = dict(at=now(), hash=digest(l['script']), by='Josh', file=path)
            if AT[l['stage']] < AT['Approved']:
                self._log(l, 'Approved', 'Josh approved the script.')
            return self._put(l, revision)

    def write_script(self, l):
        """Keep the E drive copy in step. A changed script becomes the next version, and the old one moves to Archive."""
        base = self.content_dir() / COURSES[l['course']]['dir']
        current = (self.scan(l['course']).get(l['number']) or {}).get('script')
        if current:
            try:
                if Path(current['path']).read_text(encoding='utf-8-sig') == l['script']:
                    return current['path']
            except OSError:
                pass
        version = current['version'] + 1 if current else 1
        slug = current['slug'] if current else slugify(l['title'])
        target = base / 'Scripts' / f"{l['number']}_{date.today().isoformat()}_v{version:02d}_{slug}.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(l['script'], encoding='utf-8')
        if current and Path(current['path']).is_file():
            archive = base / 'Archive' / 'Scripts'
            archive.mkdir(parents=True, exist_ok=True)
            old = Path(current['path'])
            dest = archive / old.name
            if not dest.exists():
                shutil.move(str(old), str(dest))
        if self.recording is not None and l['id'] in self.recording.entries:
            self.recording.entries[l['id']]['script'] = str(target)
        return str(target)

    # The cued script the recording page shows ----------------------------------
    def cues_path(self, lid):
        return self.root / 'Data' / 'lesson-cues' / (lid + '.md')

    def cues(self, lid):
        p = self.cues_path(lid)
        return p.read_text(encoding='utf-8-sig') if p.is_file() else ''

    def save_cues(self, lid, text):
        self._get(lid)
        text = _text(text, TEXT['script'], 'Cued script')
        p = self.cues_path(lid)
        p.parent.mkdir(parents=True, exist_ok=True)
        if text.strip():
            p.write_text(text, encoding='utf-8')
        elif p.exists():
            p.unlink()
        return dict(cues=bool(text.strip()))

    # Checking the slides --------------------------------------------------------
    def check_slides(self, lid, path=None):
        """Step through every beat in Chrome, look for errors and anything off the stage, and keep contact sheets."""
        l = self._get(lid)
        path = path or (self.files(l).get('slides') or {}).get('path')
        if not path or not Path(path).is_file():
            raise ValueError('There is no slide file to check yet.')
        from playwright.sync_api import sync_playwright
        from PIL import Image
        import io
        errors, problems, shots = [], [], []
        with sync_playwright() as p:
            browser = p.chromium.launch(channel='chrome')
            try:
                page = browser.new_page(viewport={'width': 1920, 'height': 1080})
                page.on('pageerror', lambda e: errors.append(str(e)[:300]))
                page.goto(Path(path).resolve().as_uri())
                # Each beat fades in, so turn the motion off and capture every beat as it settles.
                page.add_style_tag(content='*,*::before,*::after{transition:none!important;animation:none!important}')
                page.wait_for_timeout(400)
                total = page.evaluate('typeof TOTAL==="number"?TOTAL:null')
                if not isinstance(total, int) or total < 1:
                    raise ValueError("The slide file doesn't set TOTAL, so its beats can't be checked.")
                last = page.evaluate('Math.max(...[...document.querySelectorAll(".scene")].map(s=>+s.dataset.to||0))')
                if last != total:
                    problems.append(f'The last scene ends on beat {last}, and TOTAL says {total}.')
                for b in range(total + 1):
                    page.evaluate(f'go({b})')
                    page.wait_for_timeout(120)
                    off = page.evaluate('''(()=>{const st=document.getElementById('stage');if(!st)return [];const r0=st.getBoundingClientRect(),k=r0.width/1920;
                        const s=document.querySelector('.scene.on');if(!s)return ['no scene'];const out=[];
                        s.querySelectorAll('*').forEach(el=>{
                          // Only words, pictures and whole diagrams count. Glows and other decoration may bleed off the edge.
                          const cls=(el.getAttribute('class')||'');if(/glow|bg|deco/.test(cls)||el.closest('[aria-hidden="true"]'))return;
                          if(el.ownerSVGElement)return;
                          const own=[...el.childNodes].some(n=>n.nodeType===3&&n.textContent.trim());if(!own&&!/^(IMG|svg|CANVAS)$/.test(el.tagName))return;
                          const cs=getComputedStyle(el);if(cs.display==='none'||cs.visibility==='hidden'||+cs.opacity===0)return;
                          const r=el.getBoundingClientRect();if(!r.width||!r.height)return;
                          const x=(r.right-r0.left)/k,y=(r.bottom-r0.top)/k,l=(r.left-r0.left)/k,t=(r.top-r0.top)/k;
                          if(x>1925||y>1085||l<-5||t<-5)out.push(cls||el.tagName);});return out.slice(0,3)})()''')
                    if off:
                        problems.append(f'Beat {b}: ' + ('no scene is showing' if off == ['no scene'] else 'something sits off the slide (' + ', '.join(map(str, off)) + ')'))
                    shots.append(Image.open(io.BytesIO(page.screenshot(type='jpeg', quality=70))).convert('RGB').resize((384, 216)))
            finally:
                browser.close()
        sheets = []
        for i in range(0, len(shots), 24):
            group = shots[i:i + 24]
            sheet = Image.new('RGB', (6 * 392 + 8, ((len(group) + 5) // 6) * 224 + 8), (20, 22, 27))
            for j, im in enumerate(group):
                sheet.paste(im, (8 + (j % 6) * 392, 8 + (j // 6) * 224))
            buf = io.BytesIO()
            sheet.save(buf, format='JPEG', quality=80)
            sheets.append(dict(asset=self.store.import_image(f'beats-{i}.jpg', buf.getvalue())['asset'], first=i, last=i + len(group) - 1))
        result = dict(path=str(path), beats=total, errors=errors[:10], problems=problems[:20], sheets=sheets,
                      ok=not errors and not problems, checked_at=now())
        with self.lock:
            l = self._get(lid)
            l['slides_check'] = result
            self._put(l)
        return result

    # Saskia's work ------------------------------------------------------------
    def start(self, lid, task, note=''):
        if task not in TASKS:
            raise ValueError('Unknown task.')
        if self.agents is None:
            raise ValueError("Saskia isn't available yet.")
        with self.lock:
            l = self._get(lid)
            t = self.jobs.get(lid)
            if t and t.is_alive():
                raise ValueError('Saskia is already working on this lesson.')
            if task == 'slides' and (not l.get('approved') or self.approval_stale(l)):
                raise ValueError('Approve the current script before Saskia builds the slides.')
            if task == 'draft' and not (l.get('prep') or '').strip() and not note.strip():
                raise ValueError('Ask Saskia to prepare the lesson first, or tell her what it should cover.')
            l['job'] = dict(id=str(uuid.uuid4()), task=task, status='running', started_at=now(), note=_text(note, 2000, 'Note'))
            if TASKS[task]['stage']:
                self._log(l, TASKS[task]['stage'], f"Saskia started: {TASKS[task]['label'].lower()}.")
            l = self._put(l)
        thread = threading.Thread(target=self._work, args=(lid, task, note), daemon=True)
        self.jobs[lid] = thread
        thread.start()
        return l

    def _work(self, lid, task, note):
        try:
            l = self.get(lid)
            reply, answer = self.agents.course_job(l, task, note, self.content_dir())
            message = self._apply(lid, task, answer)
            self._finish(lid, 'done', (reply or '') + (('\n\n' + message) if message else ''))
        except Exception as e:  # noqa: BLE001 - every failure is shown on the lesson
            self._finish(lid, 'failed', str(e)[:600])

    def _finish(self, lid, status, message):
        with self.lock:
            l = self._get(lid)
            l['job'] = dict(l.get('job') or {}, status=status, finished_at=now(), message=message)
            if status == 'failed' and l['stage'] in ('Preparing', 'Drafting', 'Slides'):
                back = {'Preparing': 'Planned', 'Drafting': 'Drafting', 'Slides': 'Approved'}[l['stage']]
                self._log(l, back, 'Saskia stopped: ' + message[:120])
            self._put(l)

    def _apply(self, lid, task, answer):
        answer = answer if isinstance(answer, dict) else {}
        if task == 'prepare':
            prep = _text(answer.get('prep') or '', TEXT['prep'], 'Preparation notes').strip()
            if not prep:
                raise ValueError("Saskia didn't send any preparation notes.")
            with self.lock:
                l = self._get(lid)
                l['prep'] = prep
                self._log(l, 'Drafting', 'Preparation notes are ready.')
                self._put(l)
            return ''
        if task == 'draft':
            script = _text(answer.get('script') or '', TEXT['script'], 'Script').strip()
            if len(script) < 400:
                raise ValueError("Saskia didn't send a full script.")
            with self.lock:
                l = self._get(lid)
                l['script'] = script + '\n'
                l['script_hash'] = digest(l['script'])
                self._log(l, 'Script review', 'Saskia drafted the script.')
                self._put(l)
            return ''
        if task == 'slides':
            path = self._own_file(lid, answer.get('slides_path'), 'slides', '.html')
            cues = _text(answer.get('cued_script') or '', TEXT['script'], 'Cued script')
            if cues.strip():
                self.save_cues(lid, cues)
            check = self.check_slides(lid, path)
            with self.lock:
                l = self._get(lid)
                if self.recording is not None and lid in self.recording.entries:
                    self.recording.entries[lid]['materials'] = [dict(kind='slides', path=path)]
                if check['ok'] and cues.strip():
                    self._log(l, 'Ready to record', f"Slides built and checked, {check['beats']} beats.")
                self._put(l)
            if not check['ok']:
                return 'The beat check found problems, so the lesson stays in Slides until they are fixed.'
            return '' if cues.strip() else 'The cued script is missing, so the recording page has nothing to show.'
        if task in ('guide', 'thumbnail'):
            self._own_file(lid, answer.get('guide_pdf' if task == 'guide' else 'thumbnail'), task,
                           '.pdf' if task == 'guide' else ('.jpg', '.jpeg', '.png'))
            with self.lock:
                l = self._get(lid)
                if 'Ready to publish' not in self.blockers(l) and AT[l['stage']] < AT['Ready to publish']:
                    self._log(l, 'Ready to publish', 'The guide, thumbnail and video are all there.')
                elif AT[l['stage']] < AT['Guide and thumbnail'] and 'video' in self.files(l):
                    self._log(l, 'Guide and thumbnail', '')
                self._put(l)
            return ''
        return ''

    def _own_file(self, lid, path, kind, ext):
        """The file Saskia says she made has to exist, sit in this course's folder and be named like the others."""
        l = self._get(lid)
        if not path or not isinstance(path, str):
            raise ValueError(f"Saskia didn't say where she saved the {kind}.")
        p = Path(path.strip().strip('"'))
        folder = (self.content_dir() / COURSES[l['course']]['dir'] / KINDS[kind][0]).resolve()
        if not p.is_file() or p.resolve().parent != folder or not p.name.lower().endswith(ext):
            raise ValueError(f'The {kind} file should be in {folder}, and {p} is not.')
        m = KINDS[kind][1].match(p.name)
        if not m or m['n'] != l['number']:
            raise ValueError(f'{p.name} is not named like the other {kind} files for lesson {l["number"]}.')
        return str(p)

    # Publishing to the course app --------------------------------------------------
    def destination(self, l, quiet=False):
        root = self.publish_root()
        if not root:
            if quiet:
                return None
            raise ValueError('Set the Google Drive folder first. Install Google Drive for desktop, then paste the folder path.')
        part = l['part_name'] if l['part_name'].startswith('Bonus') else f"Part {l['part']} · {l['part_name']}"
        return str(root / COURSES[l['course']]['publish'] / safe_name(part) / safe_name(f"{l['number']} {l['title']}"))

    def publish(self, lid):
        with self.lock:
            l = self._get(lid)
            root = self.publish_root()
            if not root or not root.is_dir():
                raise ValueError("The Google Drive folder isn't set up on this PC yet. Install Google Drive for desktop and choose the folder.")
            gates = self.blockers(l)
            if 'Ready to publish' in gates:
                raise ValueError(gates['Ready to publish'])
            t = self.jobs.get(lid)
            if t and t.is_alive():
                raise ValueError('This lesson is still busy. Wait for it to finish.')
            dest = Path(self.destination(l))
            l['publish'] = dict(status='copying', folder=str(dest), started_at=now())
            l = self._put(l)
        thread = threading.Thread(target=self._copy, args=(lid,), daemon=True)
        self.jobs[lid] = thread
        thread.start()
        return l

    def _copy(self, lid):
        try:
            l = self._get(lid)
            f = self.files(l)
            dest = Path(self.destination(l))
            dest.mkdir(parents=True, exist_ok=True)
            name = safe_name(f"{l['number']} {l['title']}")
            plan = [(f['video']['path'], name + Path(f['video']['path']).suffix.lower()),
                    (f['guide']['path'], name + ' - Lesson guide.pdf'),
                    (f['thumbnail']['path'], name + ' - Thumbnail' + Path(f['thumbnail']['path']).suffix.lower())]
            for src, target in plan:
                part = dest / (target + '.partial')
                shutil.copy2(src, part)
                part.replace(dest / target)
            details = dict(course=COURSES[l['course']]['name'], course_key=l['course'], part=l['part'], part_name=l['part_name'],
                           number=l['number'], title=l['title'], type=l['type'], minutes=l.get('minutes'), order=l.get('order'),
                           video=plan[0][1], written_lesson=plan[1][1], thumbnail=plan[2][1], lesson_id=l['id'], published_at=now())
            (dest / 'lesson.json').write_text(json.dumps(details, ensure_ascii=False, indent=2), encoding='utf-8')
            with self.lock:
                l = self._get(lid)
                l['publish'] = dict(l['publish'], status='done', finished_at=now(), files=[t for _, t in plan] + ['lesson.json'])
                self._log(l, 'Published', 'Copied to the Google Drive folder for the course app.')
                self._put(l)
        except Exception as e:  # noqa: BLE001 - shown on the lesson
            with self.lock:
                l = self._get(lid)
                l['publish'] = dict(l.get('publish') or {}, status='failed', finished_at=now(), message=str(e)[:400])
                self._put(l)

    def open_path(self, lid, kind):
        import os
        l = self._get(lid)
        f = self.files(l)
        if kind == 'folder':
            p = self.content_dir() / COURSES[l['course']]['dir']
        elif kind == 'published':
            p = Path((l.get('publish') or {}).get('folder') or '')
        elif kind in f:
            p = Path(f[kind]['path'])
        else:
            raise ValueError('That file is not there yet.')
        if not p.exists():
            raise ValueError('That file is not there yet.')
        os.startfile(str(p))
        return dict(opened=str(p))


# The work Saskia can be asked to do on a lesson, the stage the lesson sits in while she does it,
# and the training file she follows.
TASKS = {
    'prepare': dict(label='Prepare the lesson', stage='Preparing', training='08_lessons_writing.md'),
    'draft': dict(label='Draft the script', stage='Drafting', training='08_lessons_writing.md'),
    'slides': dict(label='Build the slides', stage='Slides', training='09_lessons_slides.md'),
    'guide': dict(label='Make the lesson guide', stage='', training='10_lessons_guides-thumbnails-upload.md'),
    'thumbnail': dict(label='Make the thumbnail', stage='', training='10_lessons_guides-thumbnails-upload.md'),
}
