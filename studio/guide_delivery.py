"""Saskia delivers approved standalone guides (tactical written resources, not lesson companions) to the CareerOS
Downloads folder in Josh's Google Drive: H:\\My Drive\\CareerOS\\Downloads on this PC, synced by Google Drive for desktop.

- Only a guide Josh approved is delivered. The approval is for one exact PDF, by its SHA-256, with his words and when.
- The file goes in as "<title> v<NN>.pdf". An older version is never overwritten or removed, and a file already there
  under that name with other contents is never replaced, so earlier links and access stay as they were.
- The copy is written next to the target first and then renamed, and read back to check it matches byte for byte.
- Then Josh OS asks Google Drive itself, through the read-only Drive search and metadata tools, whether that file is in
  the cloud at the same size. Until Drive confirms it, the delivery says "copied, waiting for Drive to sync".
- This never claims anything about the CareerOS app's Resources page. Registering a guide there is a separate step that
  isn't built here.
"""
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import uuid
from pathlib import Path

from .store import now

DOWNLOADS = Path(r'H:\My Drive\CareerOS\Downloads')
DRIVE_READ = ('mcp__claude_ai_Google_Drive__search_files', 'mcp__claude_ai_Google_Drive__get_file_metadata')
DELIVER_PROPS = {'deliver_guide': {'type': ['object', 'null'], 'description': (
    "Only when Josh's message says he approves a finished standalone guide for the CareerOS Downloads folder: the guide PDF's "
    'full path and the title it should have there. Josh OS checks the file, copies it as a new version without replacing '
    'anything, and confirms it with Google Drive. Never for lesson companion guides. Leave null otherwise.'),
    'properties': {'path': {'type': 'string'}, 'title': {'type': 'string'}}}}
APPROVE_WORDS = re.compile(r'\b(approve|approved|publish|put it in|add it to|send it to|deliver|ship it|good to go|go ahead)\b', re.I)


def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for b in iter(lambda: f.read(1 << 20), b''):
            h.update(b)
    return h.hexdigest()


def safe_title(title):
    t = re.sub(r'[\\/:*?"<>|]+', ' ', str(title or '')).strip()
    t = re.sub(r'\s+', ' ', t)
    if not t or len(t) > 120:
        raise ValueError('Give the guide a title, up to 120 characters.')
    return t


def drive_check(name, size, root):
    """Ask Google Drive whether a file with this name and size exists, using only the read-only search and metadata tools."""
    from .agent import find_claude
    claude = find_claude(Path(root))
    if not claude:
        return dict(state='unknown', reason='Claude Code is not set up on this PC, so Drive could not be asked.')
    with tempfile.TemporaryDirectory() as tmp:
        pol = Path(tmp) / 'tools.json'
        pol.write_text(json.dumps(['ToolSearch', 'StructuredOutput', *DRIVE_READ]), encoding='utf-8')
        hook = subprocess.list2cmdline([Path(sys.executable).as_posix(), Path(__file__).with_name('read_tools_guard.py').resolve().as_posix(), pol.as_posix()])
        settings = {'hooks': {'PreToolUse': [{'matcher': '*', 'hooks': [{'type': 'command', 'command': hook}]}]}}
        schema = {'type': 'object', 'required': ['found'], 'properties': {
            'found': {'type': 'boolean'}, 'file_id': {'type': ['string', 'null']}, 'size': {'type': ['integer', 'null']},
            'parent_folder_name': {'type': ['string', 'null']}, 'modified': {'type': ['string', 'null']}, 'note': {'type': ['string', 'null']}}}
        cmd = [claude, '-p', '--output-format', 'json', '--no-session-persistence', '--tools', 'ToolSearch',
               '--allowedTools', ','.join(('ToolSearch',) + DRIVE_READ), '--permission-mode', 'dontAsk',
               '--settings', json.dumps(settings), '--json-schema', json.dumps(schema)]
        prompt = (f'Read only. In Google Drive, search for a file named exactly "{name}" (search_files), then read its metadata '
                  '(get_file_metadata). Report whether it exists, its file id, its size in bytes, the name of its parent folder and when '
                  'it was last modified. Do not create, change, share, copy or trash anything.')
        try:
            out = subprocess.run(cmd, input=prompt, capture_output=True, text=True, encoding='utf-8', timeout=240,
                                 creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            r = json.loads(out.stdout)
        except Exception as e:  # noqa: BLE001
            return dict(state='unknown', reason=f'Drive could not be asked ({str(e)[:150]}).')
    a = r.get('structured_output') or {}
    if not a.get('found'):
        return dict(state='not_yet', reason='Google Drive does not show the file yet. Drive for desktop may still be uploading it.')
    if a.get('size') != size:
        return dict(state='not_yet', reason=f"Google Drive shows {a.get('size')} bytes, not {size}. It may still be uploading.", drive=a)
    return dict(state='confirmed', drive=a)


class GuideDelivery:
    def __init__(self, store, root, downloads=None, checker=None):
        self.store, self.root = store, Path(root)
        self.downloads = Path(downloads) if downloads else DOWNLOADS
        self.checker = checker or (lambda name, size: drive_check(name, size, self.root))
        self.lock = threading.Lock()
        with store.connect() as c:
            c.execute('CREATE TABLE IF NOT EXISTS guide_deliveries(id TEXT PRIMARY KEY,sha TEXT NOT NULL,body TEXT NOT NULL)')

    def all(self, limit=50):
        with self.store.connect() as c:
            return [json.loads(r['body']) for r in c.execute('SELECT body FROM guide_deliveries ORDER BY rowid DESC LIMIT ?', (limit,))]

    def _save(self, d):
        d['updated_at'] = now()
        with self.store.connect() as c:
            c.execute('INSERT OR REPLACE INTO guide_deliveries VALUES(?,?,?)', (d['id'], d['sha'], json.dumps(d)))
        return d

    def deliver(self, path, title, approval):
        """Copy one approved guide PDF into Downloads as a new version. approval is dict(by, said, at) from Josh."""
        src = Path(str(path or '').strip().strip('"'))
        if src.suffix.lower() != '.pdf' or not src.is_file():
            raise ValueError('Choose the finished guide PDF.')
        if not isinstance(approval, dict) or not str(approval.get('said') or '').strip():
            raise ValueError("Josh has to approve this guide first. Nothing was copied.")
        if not self.downloads.is_dir():
            raise ValueError(f"The Drive Downloads folder ({self.downloads}) isn't on this PC, so nothing was copied.")
        title = safe_title(title)
        sha, size = sha256(src), src.stat().st_size
        with self.lock:
            # Already there, under any name: nothing is copied again.
            for f in self.downloads.glob('*.pdf'):
                if f.stat().st_size == size and sha256(f) == sha:
                    d = dict(id=str(uuid.uuid4()), sha=sha, source=str(src), title=title, file=str(f), size=size, state='already_there',
                             approval=approval, at=now())
                    d['drive'] = self.checker(f.name, size)
                    return self._save(d)
            stem = re.escape(title)
            taken = [int(m.group(1)) for f in self.downloads.iterdir() if (m := re.fullmatch(stem + r' v(\d+)\.pdf', f.name, re.I))]
            m = re.search(r'_v(\d+)_', src.name)
            n = int(m.group(1)) if m else (max(taken) + 1 if taken else 1)
            if n in taken:
                n = max(taken) + 1
            dest = self.downloads / f'{title} v{n:02d}.pdf'
            if dest.exists():
                raise ValueError(f'{dest.name} is already in Downloads with other contents, so it was left alone.')
            part = dest.with_name(dest.name + '.partial')
            shutil.copyfile(src, part)
            if sha256(part) != sha:
                part.unlink(missing_ok=True)
                raise ValueError('The copy did not match the guide, so it was removed and nothing was delivered.')
            part.replace(dest)
            if sha256(dest) != sha:
                raise ValueError(f'{dest.name} does not match the guide after copying. Check it before sharing.')
            d = dict(id=str(uuid.uuid4()), sha=sha, source=str(src), title=title, file=str(dest), size=size, state='copied',
                     approval=approval, at=now())
        d['drive'] = self.checker(dest.name, size)
        if d['drive'].get('state') == 'confirmed':
            d['state'] = 'synced'
        return self._save(d)

    def recheck(self, did):
        d = next((x for x in self.all(500) if x['id'] == did), None)
        if not d:
            raise KeyError('That delivery is not in Josh OS.')
        d['drive'] = self.checker(Path(d['file']).name, d['size'])
        if d['drive'].get('state') == 'confirmed' and d['state'] == 'copied':
            d['state'] = 'synced'
        return self._save(d)

    @staticmethod
    def describe(d):
        name = Path(d['file']).name
        drive = d.get('drive') or {}
        if d['state'] == 'already_there':
            return f"That exact guide is already in Downloads as {name}, so nothing was copied." + (
                ' Google Drive shows it.' if drive.get('state') == 'confirmed' else f" {drive.get('reason', '')}")
        if d['state'] == 'synced':
            return f"Saved to CareerOS Downloads as {name}, and Google Drive shows it at the same size. Nothing else there was changed."
        return f"Copied to CareerOS Downloads as {name} and checked byte for byte. {drive.get('reason', 'Drive has not confirmed it yet.')}"
