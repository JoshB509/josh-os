"""Carousel tracker for the YouTube community posts.

Each carousel moves from Idea to Posted. Slides are kept as plain text, one block per
slide separated by a line with three dashes, so they are easy to write on a phone and
easy for an agent to draft. When the slides are rendered, "folder" points at the images
on the E drive so the app can show them.

Every carousel carries a short set of tags, each from a fixed list so posts can be
grouped and compared, and results at 1 day and 7 days. Sam fills both in.
"""
import hashlib
from contextlib import contextmanager
import json
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import uuid
from datetime import date
from pathlib import Path
from .store import Conflict, now
from . import carousel_jobs

STAGES = ['Idea', 'Drafting', 'Ready for review', 'Approved', 'Scheduled', 'Posted']
PLATFORMS = ['YouTube Community', 'Instagram', 'LinkedIn', 'TikTok', 'Other']
FIELDS = {'title', 'stage', 'platforms', 'post_date', 'hook', 'slides', 'caption', 'notes', 'link',
          'project_id', 'owner', 'archived', 'folder', 'tags', 'results', 'design'}
LIMITS = {'title': 240, 'hook': 1000, 'slides': 30000, 'caption': 5000, 'notes': 20000, 'link': 1000,
          'owner': 40, 'folder': 500}

# Tags. Lists are fixed so thirty posts a month can still be grouped. Text tags are free.
TAG_OPTIONS = {
    'theme': ['Promotion', 'Meetings', 'Decks and slides', 'Credit for your work', 'Pushback',
              'Executive communication', 'Performance reviews', 'Career strategy', 'Other'],
    'hook_type': ['Number list', 'Question', 'Bold claim', 'Mistake', 'How to', 'Story', 'Contrast', 'Quote', 'Other'],
    'angle': ['Pain', 'Promise'],
    'format': ['Listicle', 'Phrases', 'Framework', 'Steps', 'Mistakes', 'Story', 'Before and after', 'Other'],
    'style': ['Paper', 'Photo BG1', 'Photo BG2', 'Other'],
    'cta_type': ['None', 'Lead magnet', 'Course', 'Video', 'Comment', 'Other'],
    'cta_place': ['None', 'Caption', 'Last slide', 'QR code', 'Caption and last slide'],
    'caption_style': ['Question', 'Summary', 'Teaser', 'Instruction', 'Story', 'Other'],
    'idea_source': ['Our video', 'Ali Abdaal', 'Alex Hormozi', 'A comment', 'Josh', 'Sam', 'Other'],
}
TAG_TEXT = {'topic': 200, 'hook_text': 500, 'lead_magnet': 200, 'post_time': 5, 'testing': 500, 'tracked_link': 500}
TAG_FLAGS = {'hook_number', 'video_same_day'}
TAG_NUMBERS = {'slide_count'}
RESULT_WINDOWS = ('d1', 'd7')
RESULT_NUMBERS = ('views', 'likes', 'comments', 'clicks', 'downloads', 'new_people', 'course_clicks', 'sale_value')
SLIDE_ROOT = Path(r'E:\CareerOS\03_Community_Posts')
IMAGE_TYPES = {'.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.webp': 'image/webp'}
CUSTOM_DESIGN_ERROR = 'This carousel has custom slides. Use a single-slide override to preserve its immutable images.'


def save_receipt(results):
    """Only host results, after commit, may describe a carousel as saved."""
    lines = []
    for row in results:
        label = row.get('title') or row.get('id') or 'carousel'
        if row.get('action') == 'pending':
            lines.append('Carousel work is still in progress. Completion has not been confirmed.')
        elif row.get('action') == 'failed':
            lines.append(f"Could not save {label}: {row.get('error', 'Unknown error')} No carousel changes were saved for this request.")
        elif row.get('action') == 'added':
            lines.append(f'Added "{label}" to CareerOS.')
        elif row.get('action') == 'designed':
            lines.append(f'Saved "{label}" in CareerOS and redrew its slides.')
        else:
            lines.append(f'Saved "{label}" in CareerOS.')
    return '\n'.join(lines)


def reply_with_receipt(reply, results):
    """The team member's answer with the host's save outcome under it. Josh asked a question, so the answer stays.
    If any save failed or hasn't finished, the receipt replaces the answer, because the answer was written before the
    save and could claim something that didn't happen."""
    receipt = save_receipt(results)
    if not results:
        return reply
    if not (reply or '').strip() or any(r.get('action') in ('failed', 'pending') for r in results):
        return receipt or 'No carousel changes were saved.'
    return reply.rstrip() + '\n\n' + receipt


def date_clashes(items, results):
    """Lines warning that a carousel just saved shares its post date with another live one. The app shows every
    carousel on its post_date, so a replaced post left on the same day still shows on Today and the calendar."""
    live = [c for c in items if c.get('post_date') and not c.get('archived') and c.get('stage') != 'Posted']
    touched = {r.get('id') for r in results if r.get('action') in ('added', 'updated', 'designed')}
    seen, lines = set(), []
    for c in live:
        if c['id'] not in touched:
            continue
        others = [o for o in live if o['id'] != c['id'] and o['post_date'] == c['post_date']]
        key = frozenset([c['id'], *(o['id'] for o in others)])
        if not others or key in seen:
            continue
        seen.add(key)
        names = ', '.join(f'"{o["title"]}"' for o in others)
        lines.append(f'"{c["title"]}" and {names} are all set for {c["post_date"]}, so they all show for that day. '
                     'Move or clear the date on any that is not going out.')
    return lines


def clean_tags(tags):
    if not isinstance(tags, dict):
        raise ValueError('Tags must be a set of named values.')
    out = {}
    for key, value in tags.items():
        if value in (None, ''):
            continue
        if key in TAG_OPTIONS:
            if value not in TAG_OPTIONS[key]:
                raise ValueError(f"{key.replace('_', ' ').capitalize()} must be one of: {', '.join(TAG_OPTIONS[key])}.")
        elif key in TAG_TEXT:
            if not isinstance(value, str) or len(value) > TAG_TEXT[key]:
                raise ValueError(f"{key.replace('_', ' ').capitalize()} must be text, up to {TAG_TEXT[key]} characters.")
            if key == 'post_time' and not re.fullmatch(r'\d{2}:\d{2}', value):
                raise ValueError('Post time must look like 21:00.')
        elif key in TAG_FLAGS:
            if not isinstance(value, bool):
                raise ValueError(f"{key.replace('_', ' ').capitalize()} must be true or false.")
        elif key in TAG_NUMBERS:
            if type(value) is not int or not 0 <= value <= 50:
                raise ValueError('Slide count must be a whole number.')
        else:
            raise ValueError(f'Unknown tag: {key}.')
        out[key] = value
    return out


def clean_results(results):
    if not isinstance(results, dict) or set(results) - set(RESULT_WINDOWS):
        raise ValueError('Results are kept for d1 and d7 only.')
    out = {}
    for window, numbers in results.items():
        if numbers in (None, {}):
            continue
        if not isinstance(numbers, dict):
            raise ValueError('Each results window must be a set of numbers.')
        row = {}
        for key, value in numbers.items():
            if key == 'checked_at':
                if not isinstance(value, str) or len(value) > 40:
                    raise ValueError('checked_at must be a date or time.')
                row[key] = value
                continue
            if key not in RESULT_NUMBERS:
                raise ValueError(f'Unknown result: {key}.')
            if value in (None, ''):
                continue
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
                raise ValueError(f"{key.replace('_', ' ').capitalize()} must be a number.")
            row[key] = value
        if row:
            out[window] = row
    return out


class Carousels:
    def __init__(self, store):
        self.store = store
        self.jobs = carousel_jobs.CarouselJobs(store)
        with store.connect() as c:
            c.execute('CREATE TABLE IF NOT EXISTS carousels(id TEXT PRIMARY KEY,revision INTEGER NOT NULL,body TEXT NOT NULL)')

    def all(self):
        with self.store.connect() as c:
            items = [self.defaults(json.loads(r['body'])) for r in c.execute('SELECT body FROM carousels ORDER BY rowid DESC')]
        for item in items:
            item['images'] = len(self.images(item))
            words = (item.get('design') or {}).get('words')
            item['design_stale'] = bool(words) and words != self.words_hash(item)
        return dict(items=items, stages=STAGES, platforms=PLATFORMS, tag_options=TAG_OPTIONS,
                    tag_text=sorted(TAG_TEXT), tag_flags=sorted(TAG_FLAGS), result_numbers=list(RESULT_NUMBERS))

    @staticmethod
    def defaults(item):
        item.setdefault('folder', '')
        item.setdefault('tags', {})
        item.setdefault('results', {})
        item.setdefault('design', {})
        return item

    def get(self, cid):
        with self.store.connect() as c:
            row = c.execute('SELECT body FROM carousels WHERE id=?', (cid,)).fetchone()
        if not row:
            raise KeyError('Carousel not found.')
        return self.defaults(json.loads(row['body']))

    # Slide images ----------------------------------------------------------
    def folder(self, item):
        raw = (item.get('folder') or '').strip()
        if not raw:
            return None
        path = Path(raw)
        if not path.is_absolute():
            path = SLIDE_ROOT / path
        try:
            path = path.resolve()
            path.relative_to(SLIDE_ROOT.resolve())
        except (OSError, ValueError):
            return None
        return path if path.is_dir() else None

    def images(self, item):
        if (item.get('design') or {}).get('override_version'):
            from .carousel_overrides import verified_images
            return verified_images(self, item)
        folder = self.folder(item)
        if not folder:
            return []
        files = [f for f in folder.iterdir() if f.is_file() and f.suffix.lower() in IMAGE_TYPES]
        return sorted(files, key=lambda f: [int(t) if t.isdigit() else t for t in re.split(r'(\d+)', f.name.lower())])

    def image(self, cid, index):
        files = self.images(self.get(cid))
        if not 0 <= index < len(files):
            raise KeyError('Slide not found.')
        return files[index], IMAGE_TYPES[files[index].suffix.lower()]

    # Saving ----------------------------------------------------------------
    def validate(self, item):
        for key, limit in LIMITS.items():
            if not isinstance(item[key], str) or len(item[key]) > limit:
                raise ValueError(f'{key.capitalize()} must be text, up to {limit:,} characters.')
        item['title'] = item['title'].strip()
        if not item['title']:
            raise ValueError('Give the carousel a working title.')
        if item['stage'] not in STAGES:
            raise ValueError('Choose a valid stage.')
        if not isinstance(item['platforms'], list) or set(item['platforms']) - set(PLATFORMS):
            raise ValueError('Choose platforms from the list.')
        if item['post_date']:
            date.fromisoformat(item['post_date'])
            if len(item['post_date']) != 10:
                raise ValueError('Use a full date.')
        if item['link'] and not item['link'].startswith(('https://', 'http://')):
            raise ValueError('Paste the full link to the post, starting with https://')
        if not isinstance(item['archived'], bool):
            raise ValueError('Archived must be true or false.')
        if item['project_id']:
            self.store.get(item['project_id'])
        item['tags'] = clean_tags(item['tags'])
        item['results'] = clean_results(item['results'])
        if not isinstance(item.get('design', {}), dict) or len(json.dumps(item.get('design', {}))) > 500:
            raise ValueError('Design details must be a small set of values.')

    def create(self, data, *, make_design=False, commit_check=None):
        if not isinstance(data, dict) or set(data) - FIELDS - {'id'}:
            raise ValueError('Unknown carousel field.')
        cid = data.get('id') or str(uuid.uuid4())
        uuid.UUID(cid)
        item = dict(id=cid, revision=1, title=data.get('title', ''), stage=data.get('stage', 'Idea'),
                    platforms=data.get('platforms', ['YouTube Community']), post_date=data.get('post_date', ''),
                    hook=data.get('hook', ''), slides=data.get('slides', ''), caption=data.get('caption', ''),
                    notes=data.get('notes', ''), link=data.get('link', ''), project_id=data.get('project_id', ''),
                    owner=data.get('owner', 'sam'), archived=data.get('archived', False),
                    folder=data.get('folder', ''), tags=data.get('tags', {}), results=data.get('results', {}),
                    design=data.get('design', {}),
                    created_at=now(), updated_at=now(), posted_at=None)
        self.validate(item)
        if item['stage'] == 'Posted':
            item['posted_at'] = now()
        # Resolve retries before rendering. Recheck under the insert transaction.
        with self.store.connect() as c:
            row = c.execute('SELECT body FROM carousels WHERE id=?', (cid,)).fetchone()
        if row:
            if carousel_jobs.ACTIVE.get():
                raise Conflict('This carousel was created in another window. Reload it before saving again.')
            return self.defaults(json.loads(row['body']))
        with self._prepared_design(item, make_design) as item:
            self.validate(item)
            with self.store.connect() as c:
                c.execute('BEGIN IMMEDIATE')
                row = c.execute('SELECT body FROM carousels WHERE id=?', (cid,)).fetchone()
                if row:
                    # Do not publish a rendered version for an intervening create.
                    raise Conflict('This carousel was created in another window. Reload it before saving again.')
                if commit_check is not None:commit_check(c)
                c.execute('INSERT INTO carousels VALUES(?,?,?)', (cid, 1, json.dumps(item)))
                carousel_jobs.committed(c, item)
        return item

    def save(self, cid, changes, revision, *, make_design=False):
        if not isinstance(changes, dict) or set(changes) - FIELDS:
            raise ValueError('Unknown carousel field.')
        if type(revision) is not int:
            raise ValueError('A saved revision is required.')
        previous = self.get(cid)
        if previous['revision'] != revision:
            raise Conflict('This carousel changed in another window. Reload it before saving again.')
        item = dict(previous, **changes)
        self.validate(item)
        if previous['design'].get('photo_version'):
            if make_design or any(item[k] != previous[k] for k in ('slides', 'folder', 'design')):
                raise ValueError('Photo draft images are immutable. Create a new Photo draft to revise the copy.')
        if previous['design'].get('override_version'):
            if make_design:
                raise ValueError(CUSTOM_DESIGN_ERROR)
            if any(item[k] != previous[k] for k in ('slides', 'folder', 'design')):
                # The override service supplies a complete, verified new version.
                # A regular text edit must never leave immutable images stale.
                if (not item['design'].get('override_version') or
                        item['design']['override_version'] == previous['design']['override_version'] or
                        item['design'].get('words') != self.words_hash(item)):
                    raise ValueError(CUSTOM_DESIGN_ERROR)
                from .carousel_overrides import verified_images
                verified_images(self, item)
        with self._prepared_design(item, make_design) as item:
            self.validate(item)
            with self.store.connect() as c:
                c.execute('BEGIN IMMEDIATE')
                row = c.execute('SELECT revision FROM carousels WHERE id=?', (cid,)).fetchone()
                if not row:
                    raise KeyError('Carousel not found.')
                if row['revision'] != revision:
                    raise Conflict('This carousel changed in another window. Reload it before saving again.')
                if all(item[k] == previous[k] for k in FIELDS):
                    carousel_jobs.committed(c, previous, False)
                    return previous
                item.update(revision=revision + 1, updated_at=now())
                item['posted_at'] = (previous.get('posted_at') or now()) if item['stage'] == 'Posted' else None
                c.execute('UPDATE carousels SET revision=?,body=? WHERE id=?', (item['revision'], json.dumps(item), cid))
                carousel_jobs.committed(c, item)
        return item

    def apply(self, owner, updates, revisions=None, request_id=None):
        if owner != 'sam' or request_id is None:
            return self._apply(owner, updates, revisions)
        # Register the complete immutable batch in one transaction before rendering.
        # Repeated delivery can replay receipts, but cannot add or change operations.
        try:
            pending = self.jobs.batch(request_id, updates or [], revisions)
        except (ValueError, Conflict) as exc:
            return [dict(id='', title='', kind='carousel', action='failed', error=str(exc))]
        done = []
        for update, job, fresh in pending:
            if fresh:
                try:
                    with job.active():
                        job.mark('preparing')
                        self._apply(owner, [update], revisions,
                            new_id=job.record['carousel_id'] if job.record['new'] else None)
                except Exception as exc:
                    job.fail(exc)
            done.append(job.receipt())
        return done

    def _apply(self, owner, updates, revisions=None, new_id=None):
        """Changes a team member asked for from chat. Tags and results merge into what is there."""
        done = []
        for u in updates or []:
            if not isinstance(u, dict):
                continue
            u = {k: v for k, v in u.items() if v is not None}
            cid = u.pop('id', '') or ''
            redo = u.pop('make_design', False) is True
            revision = u.pop('revision', None)
            try:
                if cid:
                    current = self.get(cid)
                    expected = revisions.get(cid) if revisions is not None else (
                        revision if revision is not None else current['revision'])
                    if expected != current['revision']:
                        raise Conflict('This carousel changed since the chat snapshot. Reload it before saving again.')
                    if 'tags' in u:
                        u['tags'] = dict(current['tags'], **u['tags'])
                    if 'results' in u:
                        merged = dict(current['results'])
                        for w, nums in u['results'].items():
                            merged[w] = dict(merged.get(w, {}), **(nums or {}))
                        u['results'] = merged
                    changes = {k: v for k, v in u.items() if k in FIELDS}
                    proposed = dict(current, **changes)
                    # New words on a post Josh OS already designed get a new design, so Josh never sees the old slides.
                    if not redo and (current.get('design') or {}).get('words') and self.words_hash(proposed) != current['design']['words']:
                        redo = True
                    item = self.save(cid, changes, expected, make_design=redo)
                    done.append(dict(id=item['id'], revision=item['revision'], kind='carousel', title=item['title'], action='designed' if redo else 'updated'))
                else:
                    u.setdefault('owner', owner)
                    data = {k: v for k, v in u.items() if k in FIELDS}
                    if new_id:
                        data['id'] = new_id
                    item = self.create(data, make_design=redo)
                    done.append(dict(id=item['id'], revision=item['revision'], kind='carousel', title=item['title'], action='added'))
            except (ValueError, KeyError, Conflict, OSError, sqlite3.Error) as e:
                if carousel_jobs.ACTIVE.get():
                    raise
                done.append(dict(id=cid, kind='carousel', title=u.get('title', ''), action='failed', error=str(e).strip("'\"")))
        return done

    # Design ----------------------------------------------------------------
    @staticmethod
    def words_hash(item):
        return hashlib.sha1((item.get('slides') or '').strip().encode('utf-8')).hexdigest()[:12]

    def slide_parts(self, block, n):
        """One slide's words, plus a drawing (inline <svg>) or a picture ([image: asset id or path]) if it has one."""
        out = {}
        svg = re.search(r'<svg[\s\S]*?</svg>', block, re.I)
        if svg:
            out['svg'] = re.sub(r'<script[\s\S]*?</script>|\son\w+="[^"]*"', '', svg.group(0), flags=re.I)
            block = block.replace(svg.group(0), '')
        lines = []
        for line in block.strip().splitlines():
            m = re.fullmatch(r'\[(?:image|picture|photo):\s*(.+?)\]', line.strip(), re.I)
            if m:
                src = m.group(1).strip().strip('"')
                try:
                    path = Path(self.store.asset(src)[0]) if re.fullmatch(r'[a-f0-9]{64}', src) else Path(src)
                except (ValueError, KeyError):
                    path = None
                if not path or not path.is_file() or path.suffix.lower() not in IMAGE_TYPES:
                    raise ValueError(f"Slide {n} uses a picture Josh OS can't find: {src}")
                out['image'] = str(path)
            elif line.strip():
                lines.append(line.strip())
        if not lines:
            raise ValueError(f'Slide {n} needs a headline line as well as its picture or drawing.')
        out['lines'] = lines
        return out

    def make_design(self, cid):
        """Render a fresh version, then atomically point the card at it."""
        item = self.get(cid)
        saved = self.save(cid, {}, item['revision'], make_design=True)
        return dict(saved, images=saved['tags']['slide_count'])

    @contextmanager
    def _prepared_design(self, item, render):
        """Keep the old record and files intact until a complete render commits."""
        if not render:
            carousel_jobs.mark('saving')
            yield item
            return
        if (item.get('design') or {}).get('override_version'):
            raise ValueError(CUSTOM_DESIGN_ERROR)
        blocks = [b for b in re.split(r'\n-{3,}\n?', item.get('slides') or '') if b.strip()]
        slides = [self.slide_parts(b, i + 1) for i, b in enumerate(blocks)]
        if not slides:
            raise ValueError('Write the slides first, then make the design.')
        # The renderer deletes old slide_*.png files. It must only see scratch
        # output, never a current or historical carousel directory.
        with tempfile.TemporaryDirectory(prefix='carousel-render-', ignore_cleanup_errors=True) as scratch:
            stage = Path(scratch) / 'images'
            job = Path(scratch) / 'job.json'
            job.write_text(json.dumps({'slides': slides, 'out': str(stage)}), encoding='utf-8')
            carousel_jobs.mark('rendering')
            try:
                run = subprocess.run([sys.executable, str(Path(__file__).with_name('render_carousel.py')), str(job)],
                                     capture_output=True, text=True, encoding='utf-8', timeout=240,
                                     creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            except subprocess.TimeoutExpired:
                raise ValueError('Making the design took too long, so it stopped. Try again.') from None
            if run.returncode != 0:
                raise ValueError('The design could not be made: ' + ((run.stderr or run.stdout).strip().splitlines() or ['no details'])[-1][:300])
            from PIL import Image
            expected = {f'slide_{i + 1:02d}.png' for i in range(len(slides))}
            if not stage.is_dir() or {p.name for p in stage.iterdir()} != expected:
                raise ValueError('The design did not produce the complete slide image set.')
            try:
                for name in expected:
                    with Image.open(stage / name) as image:
                        if image.format != 'PNG':
                            raise ValueError('The design must produce PNG slides.')
                        image.verify()
            except (OSError, SyntaxError) as exc:
                raise ValueError('The design produced an invalid slide image.') from exc
            # A unique folder prevents same-title posts and previous versions
            # from overwriting one another. Old versions remain recoverable.
            root = SLIDE_ROOT.resolve()
            out = root / 'Carousel' / f"{item['id']}_{uuid.uuid4().hex}"
            if out.resolve().parent != root / 'Carousel':
                raise ValueError('Invalid carousel render destination.')
            committed = owned = False
            try:
                out.mkdir(parents=True, exist_ok=False)
                owned = True
                shutil.copytree(stage, out, dirs_exist_ok=True)
                tags = dict(item['tags'], slide_count=len(slides))
                tags.setdefault('style', 'Paper')
                carousel_jobs.mark('saving')
                yield dict(item, folder=str(out.relative_to(root)), tags=tags,
                           design=dict(style='Paper', words=self.words_hash(item), at=now()))
                committed = True
            finally:
                if owned and not committed and out.exists():
                    if out.resolve().parent != root / 'Carousel':
                        raise ValueError('Invalid carousel render cleanup target.')
                    shutil.rmtree(out)
