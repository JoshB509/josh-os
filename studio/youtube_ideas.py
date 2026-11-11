"""YouTube videos Josh saves as ideas, kept in the inspiration library beside Creator Hooks.

Sam saves one when Josh drops a link into chat (Josh OS or Discord) and asks. Each video is kept once, by its video ID, in
References/YouTubeIdeas/library.json with a copy of its thumbnail in References/YouTubeIdeas/thumbs/<id>.jpg. A saved
entry records the link Josh gave, the canonical link, the title, channel, publication date and view count as seen when it
was saved, and when that was. Anything YouTube didn't give back is stored as null and named in `unavailable`. A repeat
link never changes what's saved, so Josh's own notes and edits stay as they are.
"""
import json
import re
import threading
import urllib.parse
import urllib.request
from pathlib import Path

from .store import now

ID = re.compile(r'^[A-Za-z0-9_-]{11}$')
HOSTS = ('youtube.com', 'www.youtube.com', 'm.youtube.com', 'music.youtube.com', 'youtu.be', 'www.youtu.be')
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36'


def video_id(url):
    """The 11 character video ID from any usual YouTube link, or None."""
    u = urllib.parse.urlsplit(str(url or '').strip())
    host = (u.hostname or '').lower()
    if u.scheme not in ('http', 'https') or host not in HOSTS:
        return None
    if host.endswith('youtu.be'):
        cand = u.path.strip('/').split('/')[0]
    else:
        q = urllib.parse.parse_qs(u.query).get('v', [''])[0]
        m = re.match(r'^/(?:shorts|embed|live|v)/([^/?#]+)', u.path)
        cand = q or (m.group(1) if m else '')
    return cand if ID.match(cand or '') else None


def _get(url, timeout=20):
    req = urllib.request.Request(url, headers={'User-Agent': UA, 'Accept-Language': 'en-GB,en;q=0.8'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read(4_000_000), r.headers.get('Content-Type', '')


def fetch(vid):
    """What YouTube says about a video now. Missing pieces come back as None."""
    canonical = f'https://www.youtube.com/watch?v={vid}'
    out = dict(title=None, channel=None, published=None, views=None)
    try:
        data, _ = _get('https://www.youtube.com/oembed?format=json&url=' + urllib.parse.quote(canonical, safe=''))
        o = json.loads(data.decode('utf-8'))
        out['title'], out['channel'] = o.get('title') or None, o.get('author_name') or None
    except Exception:  # noqa: BLE001 - the watch page may still have it
        pass
    try:
        page = _get(canonical + '&hl=en')[0].decode('utf-8', 'replace')
        m = re.search(r'"viewCount":"(\d+)"', page) or re.search(r'itemprop="interactionCount" content="(\d+)"', page)
        if m:
            out['views'] = int(m.group(1))
        m = re.search(r'"publishDate":"(\d{4}-\d{2}-\d{2})', page) or re.search(r'itemprop="datePublished" content="(\d{4}-\d{2}-\d{2})', page) \
            or re.search(r'"uploadDate":"(\d{4}-\d{2}-\d{2})', page)
        if m:
            out['published'] = m.group(1)
        if not out['title']:
            m = re.search(r'<meta name="title" content="([^"]+)"', page)
            out['title'] = m.group(1) if m else None
        if not out['channel']:
            m = re.search(r'"ownerChannelName":"([^"]+)"', page)
            out['channel'] = m.group(1) if m else None
    except Exception:  # noqa: BLE001 - recorded as unavailable
        pass
    return out


class YouTubeIdeas:
    def __init__(self, root):
        self.dir = Path(root) / 'References' / 'YouTubeIdeas'
        self.file = self.dir / 'library.json'
        self.thumbs = self.dir / 'thumbs'
        self.lock = threading.Lock()

    def all(self):
        try:
            return json.loads(self.file.read_text(encoding='utf-8')).get('videos', [])
        except (OSError, ValueError):
            return []

    def _write(self, videos):
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self.file.with_suffix('.tmp')
        tmp.write_text(json.dumps(dict(videos=videos), ensure_ascii=False, indent=1), encoding='utf-8')
        tmp.replace(self.file)

    def thumb_path(self, vid):
        return self.thumbs / f'{vid}.jpg'

    def _thumbnail(self, vid):
        self.thumbs.mkdir(parents=True, exist_ok=True)
        for name in ('maxresdefault.jpg', 'sddefault.jpg', 'hqdefault.jpg'):
            try:
                data, kind = _get(f'https://i.ytimg.com/vi/{vid}/{name}')
            except Exception:  # noqa: BLE001 - try the next size
                continue
            if data and 'image' in kind and len(data) > 2000:
                self.thumb_path(vid).write_bytes(data)
                return f'thumbs/{vid}.jpg'
        return None

    def save(self, url, note='', by='sam', via=''):
        """Save a video once. Returns (entry, 'saved' or 'already saved')."""
        vid = video_id(url)
        if not vid:
            raise ValueError("That isn't a YouTube video link I can read.")
        with self.lock:
            videos = self.all()
            for v in videos:
                if v['id'] == vid:
                    return v, 'already saved'
        info = fetch(vid)
        if info['title'] is None and info['channel'] is None and info['views'] is None:
            raise ValueError("YouTube didn't return that video. It may be private, removed or mistyped, so I haven't saved it.")
        thumb = self._thumbnail(vid)
        with self.lock:
            videos = self.all()
            for v in videos:
                if v['id'] == vid:
                    return v, 'already saved'
            entry = dict(id=vid, link_given=str(url).strip(), url=f'https://www.youtube.com/watch?v={vid}',
                         title=info['title'], channel=info['channel'], published=info['published'],
                         views_at_save=info['views'], observed_at=now(), thumbnail=thumb,
                         note=str(note or '').strip()[:2000], saved_by=by, via=via or 'josh-os', tags=[])
            entry['unavailable'] = [k for k, f in (('title', 'title'), ('channel', 'channel'), ('publication date', 'published'),
                                                   ('view count', 'views_at_save'), ('thumbnail', 'thumbnail')) if entry[f] is None]
            videos.append(entry)
            self._write(videos)
            return entry, 'saved'

    @staticmethod
    def describe(entry, status):
        """The confirmation Josh sees, in chat and on Discord."""
        bits = [f"\"{entry.get('title') or 'Untitled'}\"", f"by {entry['channel']}" if entry.get('channel') else None]
        head = ' '.join(b for b in bits if b)
        if status == 'already saved':
            return f"Already in the inspiration library: {head} (video {entry['id']}), saved {entry['observed_at'][:10]}."
        facts = []
        if entry.get('published'):
            facts.append(f"published {entry['published']}")
        if entry.get('views_at_save') is not None:
            facts.append(f"{entry['views_at_save']:,} views when saved")
        line = f"Saved to the inspiration library: {head} (video {entry['id']})" + (', ' + ', '.join(facts) if facts else '') + '.'
        if entry.get('unavailable'):
            gone = entry['unavailable']
            names = gone[0] if len(gone) == 1 else ', '.join(gone[:-1]) + ' and ' + gone[-1]
            line += f" YouTube didn't give the {names}, so {'it is' if len(gone) == 1 else 'those are'} marked as unavailable."
        return line
