"""Tariq's archive of a published video to Josh's My Passport drive, plus an independent backup where one is set up.

Josh's words: "it is tariqs job to move. He just needs to know the video is confirmed public".

1. Sam's proof. Sam gives the YouTube video id. Josh OS checks it itself in the channel's public RSS feed, which lists
   only public videos. The first time Josh OS sees it there is the proof time. The upload date, the scheduled date and
   the project's publish date are never used. A claim alone is never enough.
2. Eligibility. Seven days after that proof, while the video is still public (checked again at or after that point),
   and with no open revisions (an edit job still running or blocked, or a Frame.io review that isn't finished).
3. Preparation. Every file in the video's folder, plus every outside file a Premiere project uses (found in the
   .prproj itself), and the final master. A missing file blocks. The Passport is found by its volume id and disk serial,
   never by drive letter, and its free space is checked.
4. Copy. Each file is copied to a .partial file, read back and hashed, and only then renamed. A stopped copy resumes
   where it left off. Nothing on the source changes. It pauses while OBS or ffmpeg is running.
5. Restore and relink proof. Every archived file is read back and hashed against its source. Each Premiere project is
   rewritten against the archive in a temp folder, and every media path in it must exist in the archive with the right
   hash. The final master's hash must match the approved export.
6. Cleanup review. Only once the Passport copy, the backup copy and the proof all pass. It lists what could be removed
   from the source and what that frees. Nothing is moved or deleted here. A later cleanup needs Josh's approval of that
   exact review, sends files to the Recycle Bin, and a permanent delete needs his separate, explicit confirmation.

Every step is written to Data/archive/audit.jsonl. Tariq gets no general file or PC permission from this module.

Status (12 Nov 2026, after Josh's safety review): copy-only staging is accepted. Source cleanup is hard off
(CLEANUP_ENABLED = False), and no server route or chat action can reach it. Shared files and the final master are copy
only, and only files inside the video's own folder could ever be offered. Before cleanup can be switched on, these still
have to be built and proven:
- Fresh Frame.io comments read from Frame.io itself at the eligibility check and when Josh closes a review, not only the
  comments Josh OS imported earlier.
- Ownership resolved through junctions, symlinks and paths other videos use, so a file reached through another
  project, or shared by one, is never treated as this video's own.
- A trusted Premiere restore run, opening the relinked project from the archive with the original drives unavailable
  and rendering from it, with its evidence (the render file and its check, the offline media list) recorded by Josh OS
  itself. A caller's true/false claim is not evidence, so record_premiere_restore is only a placeholder for that run.
"""
import gzip
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .store import now

WAIT = timedelta(days=7)
RECHECK = timedelta(hours=24)
VIDEO_ID = re.compile(r'[A-Za-z0-9_-]{11}')
CHANNEL_ID = re.compile(r'UC[A-Za-z0-9_-]{22}')
FEED = 'https://www.youtube.com/feeds/videos.xml?channel_id='
ARCHIVE_DIR = 'Josh OS Archive'
SKIP = re.compile(r'(^~|\.tmp$|\.partial$|^Thumbs\.db$|^desktop\.ini$|\.prproj\.lock$|^\.DS_Store$)', re.I)
OPEN_STATES = {'running', 'started', 'requested', 'waiting', 'blocked'}
COPY_CHUNK = 8 * 1024 * 1024
MARGIN = 1.02   # 2% headroom on the destination
CLEANUP_ENABLED = False   # source cleanup stays off until the whole route is proven in Premiere and reviewed


def utc(s):
    return datetime.fromisoformat(str(s).replace('Z', '+00:00'))


def sha256_file(path, chunk=COPY_CHUNK):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for b in iter(lambda: f.read(chunk), b''):
            h.update(b)
    return h.hexdigest()


def digest(v):
    return hashlib.sha256(json.dumps(v, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()


# Drives ------------------------------------------------------------------------------------------------------------
PS_VOLUMES = r'''
$out = @()
foreach ($v in Get-Volume | Where-Object { $_.DriveLetter }) {
  $p = Get-Partition -DriveLetter $v.DriveLetter -ErrorAction SilentlyContinue
  $d = if ($p) { Get-Disk -Number $p.DiskNumber -ErrorAction SilentlyContinue } else { $null }
  $out += [pscustomobject]@{ letter = [string]$v.DriveLetter; volume_id = [string]$v.UniqueId; label = [string]$v.FileSystemLabel;
    fs = [string]$v.FileSystem; size = [int64]$v.Size; free = [int64]$v.SizeRemaining; disk = [string]$d.FriendlyName;
    serial = ([string]$d.SerialNumber).Trim(); bus = [string]$d.BusType }
}
$out | ConvertTo-Json -Compress
'''


def list_volumes():
    """Every lettered volume with its disk's identity, read from Windows itself (read only)."""
    p = subprocess.run(['powershell', '-NoProfile', '-NonInteractive', '-Command', PS_VOLUMES], capture_output=True, text=True,
                       timeout=60, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    v = json.loads(p.stdout or '[]')
    return v if isinstance(v, list) else [v]


def identity(vol):
    return dict(volume_id=vol['volume_id'], serial=vol['serial'], disk=vol['disk'], label=vol['label'], size=vol['size'])


def find_volume(ident, volumes):
    """The volume with this exact volume id and disk serial, wherever its letter is now. None if it isn't plugged in."""
    if not ident:
        return None
    hits = [v for v in volumes if v.get('volume_id') == ident.get('volume_id') and v.get('serial') == ident.get('serial')]
    return hits[0] if len(hits) == 1 else None


def passport_candidates(volumes):
    return [v for v in volumes if 'my passport' in (v.get('disk') or '').lower() or 'my passport' in (v.get('label') or '').lower()]


# Public proof ------------------------------------------------------------------------------------------------------
def fetch_url(url, timeout=30):
    import urllib.request
    req = urllib.request.Request(url, headers={'User-Agent': 'JoshOS-archive/1.0'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read(2_000_000)


def check_public(video_id, channel_id, fetch=None):
    """Is this video in the channel's public feed? The feed lists public videos only, so private, unlisted and
    scheduled videos are never in it. Returns what was seen; never raises for a missing video."""
    fetch = fetch or fetch_url
    status, body = fetch(FEED + channel_id)
    if status != 200:
        return dict(public=None, reason=f'The channel feed answered {status}, so nothing was proven.')
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        return dict(public=None, reason='The channel feed could not be read, so nothing was proven.')
    ns = {'a': 'http://www.w3.org/2005/Atom', 'yt': 'http://www.youtube.com/xml/schemas/2015'}
    feed_channel = root.findtext('yt:channelId', default='', namespaces=ns)
    if feed_channel and feed_channel != channel_id:
        return dict(public=None, reason='The feed belongs to a different channel.')
    for e in root.findall('a:entry', ns):
        if e.findtext('yt:videoId', default='', namespaces=ns) == video_id:
            return dict(public=True, feed_published=e.findtext('a:published', default='', namespaces=ns),
                        title=e.findtext('a:title', default='', namespaces=ns)[:200])
    return dict(public=False, reason='The video is not in the channel\'s public feed (private, unlisted, scheduled, or older than the feed).')


# Premiere dependencies ---------------------------------------------------------------------------------------------
PATH_TAGS = re.compile(r'<(ActualMediaFilePath|FilePath|ConformedAudioPath|PeakFilePath)>([^<]+)</\1>')


def prproj_paths(path):
    """Absolute media paths a Premiere project refers to (read from its gzip XML). Peak and conform cache files are
    left out, since Premiere rebuilds them."""
    raw = Path(path).read_bytes()
    try:
        raw = gzip.decompress(raw)
    except OSError:
        pass
    text = raw.decode('utf-8', 'replace')
    out = []
    for tag, val in PATH_TAGS.findall(text):
        if tag in ('ConformedAudioPath', 'PeakFilePath'):
            continue
        val = val.strip().replace('&lt;', '<').replace('&gt;', '>').replace('&quot;', '"').replace('&apos;', "'").replace('&amp;', '&')
        if re.match(r'^[A-Za-z]:[\\/]', val) or val.startswith('\\\\'):
            out.append(val)
    return list(dict.fromkeys(out))


def xml_escape(s):
    return str(s).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def norm(p):
    return os.path.normcase(os.path.normpath(str(p)))


# The workflow ------------------------------------------------------------------------------------------------------
class Archive:
    def __init__(self, store, root, flow=None, folders=None, frameio=None, agents=None, volumes=None, fetch=None, clock=None,
                 busy=None, trash=None):
        self.store, self.root = store, Path(root)
        self.flow, self.folders, self.frameio, self.agents = flow, folders, frameio, agents
        self.volumes = volumes or list_volumes
        self.fetch = fetch
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.busy = busy or media_busy
        self.trash = trash
        self.dir = Path(store.root) / 'archive'
        self.dir.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()

    # -- records
    def _read(self, name, default):
        try:
            return json.loads((self.dir / name).read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return default

    def _write(self, name, value):
        p = self.dir / name
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + '.tmp')
        tmp.write_text(json.dumps(value, indent=1), encoding='utf-8')
        os.replace(tmp, p)

    def audit(self, what, **kw):
        with (self.dir / 'audit.jsonl').open('a', encoding='utf-8') as f:
            f.write(json.dumps(dict(at=now(), what=what, **kw), ensure_ascii=False) + '\n')

    def settings(self):
        return self._read('settings.json', {})

    def record(self, pid):
        return self._read(f'{pid}/record.json', dict(project_id=pid, state='waiting'))

    def _save(self, pid, rec):
        rec['updated_at'] = now()
        self._write(f'{pid}/record.json', rec)
        return rec

    # -- setup: Josh's drives and channel
    def set_drive(self, role, volume_id, serial, by='Josh'):
        """Josh picks the Passport (and optionally a backup) from the drives Windows reports. Stored by identity."""
        if role not in ('passport', 'backup'):
            raise ValueError('The drive role is passport or backup.')
        vol = find_volume(dict(volume_id=volume_id, serial=serial), self.volumes())
        if not vol:
            raise ValueError('That drive is not plugged in, or its id and serial do not match.')
        s = self.settings()
        other = s.get('backup' if role == 'passport' else 'passport') or {}
        if other.get('serial') == vol['serial']:
            raise ValueError('The backup has to be a different physical drive from the Passport.')
        s[role] = dict(identity(vol), chosen_by=by, at=now())
        self._write('settings.json', s)
        self.audit('drive chosen', role=role, **identity(vol))
        return s

    def set_channel(self, channel_id, by='Josh'):
        if not CHANNEL_ID.fullmatch(str(channel_id or '')):
            raise ValueError('A YouTube channel id starts with UC and has 24 characters.')
        s = self.settings()
        s['channel_id'] = dict(id=channel_id, set_by=by, at=now())
        self._write('settings.json', s)
        self.audit('channel set', channel_id=channel_id, by=by)
        return s

    def drives(self):
        vols = self.volumes()
        s = self.settings()
        out = dict(volumes=vols, passport_candidates=passport_candidates(vols))
        for role in ('passport', 'backup'):
            v = find_volume(s.get(role), vols)
            out[role] = dict(chosen=s.get(role), present=bool(v), letter=v.get('letter') if v else None,
                             free=v.get('free') if v else None, size=v.get('size') if v else None)
        return out

    # -- step 1: Sam's public proof
    def record_public(self, pid, video_id, by='sam'):
        """Sam says the video is public. Josh OS checks the public feed itself before anything counts."""
        video_id = str(video_id or '').strip()
        if not VIDEO_ID.fullmatch(video_id):
            raise ValueError('That is not a YouTube video id (11 letters, numbers, - or _).')
        ch = (self.settings().get('channel_id') or {}).get('id')
        if not ch:
            raise ValueError("Josh OS doesn't have the YouTube channel id yet, so it can't check the public feed.")
        rec = self.record(pid)
        old = rec.get('proof') or {}
        if old.get('video_id') and old['video_id'] != video_id:
            raise ValueError(f"This video already has proof for YouTube id {old['video_id']}. Ask Josh before changing it.")
        seen = check_public(video_id, ch, self.fetch)
        at = self.clock().isoformat()
        proof = dict(old, video_id=video_id, channel_id=ch, said_by=by, method='channel public RSS feed (public videos only)')
        proof.setdefault('checks', []).append(dict(at=at, public=seen.get('public'), reason=seen.get('reason', '')))
        proof['checks'] = proof['checks'][-20:]
        if seen.get('public') is True:
            proof.setdefault('first_verified_at', at)
            proof['last_verified_at'] = at
            proof['feed_published'] = seen.get('feed_published')
            proof['eligible_from'] = (utc(proof['first_verified_at']) + WAIT).isoformat()
        rec['proof'] = proof
        self._save(pid, rec)
        self.audit('public check', project_id=pid, video_id=video_id, public=seen.get('public'), by=by, reason=seen.get('reason', ''))
        if seen.get('public') is True and not old.get('first_verified_at') and self.agents is not None:
            name = (self.store.get(pid) or {}).get('name', 'this video')
            try:
                self.agents.post('tariq', f'Sam confirmed "{name}" is public on YouTube, and Josh OS checked it in the channel\'s public feed '
                                          f'at {at[:16].replace("T", " ")} UTC. You can archive it to the Passport from '
                                          f'{proof["eligible_from"][:10]}, once it is still public and nothing is still being revised.',
                                 notice=False)
            except Exception:  # noqa: BLE001 - the proof is saved either way
                pass
        return proof

    # -- step 2: eligibility
    def open_revisions(self, pid):
        out = []
        if self.flow is not None:
            for kind in ('edit_proposal', 'edit_cut', 'edit_visuals', 'edit_final', 'edit_master'):
                for a in self.flow._all(pid, kind)[:5]:
                    st = str((a.get('payload') or {}).get('state') or '')
                    if st in OPEN_STATES:
                        out.append(f'{kind.replace("edit_", "")} {a["id"][:8]} is {st}')
        if self.frameio is not None:
            for b in self.frameio.all(pid):
                # "ready" only means the upload plays in Frame.io. A review counts as finished only when Josh closed it
                # (review_closed), after the latest comments were imported, with every imported comment done.
                closed = b.get('review_closed') or {}
                fb_at = b.get('feedback_read_at') or ''
                open_notes = [c for c in b.get('feedback') or [] if not c.get('completed')]
                if not closed.get('by') == 'Josh' or not closed.get('at') or closed.get('reopened_at'):
                    out.append(f'Frame.io review {b["id"][:8]} has not been closed by Josh')
                elif not fb_at or fb_at > closed['at'] or closed.get('feedback_read_at') != fb_at:
                    out.append(f'Frame.io review {b["id"][:8]} has comments imported after it was closed, or none imported')
                if open_notes:
                    out.append(f'Frame.io review {b["id"][:8]} has {len(open_notes)} comment(s) not done')
        return out

    def eligibility(self, pid, recheck=True):
        rec = self.record(pid)
        proof = rec.get('proof') or {}
        why, t = [], self.clock()
        if not proof.get('first_verified_at'):
            why.append('No independent proof yet that the video is public. Sam gives the YouTube id and Josh OS checks the public feed.')
            return dict(eligible=False, blockers=why, eligible_from=None)
        start = utc(proof['first_verified_at']) + WAIT
        if t < start:
            why.append(f'Public since {proof["first_verified_at"][:16].replace("T", " ")} UTC, so it can be archived from '
                       f'{start.isoformat()[:16].replace("T", " ")} UTC.')
        else:
            last = utc(proof.get('last_verified_at') or proof['first_verified_at'])
            if (last < start or t - last > RECHECK) and recheck:
                proof = self.record_public(pid, proof['video_id'], by='josh os recheck')
                last = utc(proof.get('last_verified_at') or proof['first_verified_at'])
            if last < start or t - last > RECHECK or (proof.get('checks') or [{}])[-1].get('public') is not True:
                why.append('The video has to be seen in the public feed again now, seven days on, before it is archived.')
        why += self.open_revisions(pid)
        return dict(eligible=not why, blockers=why, eligible_from=start.isoformat())

    # -- step 3: preparation
    def source_dir(self, pid):
        p = self.folders.path(pid) if self.folders is not None else None
        if not p or not Path(p).is_dir():
            raise ValueError("This video's folder isn't linked in Josh OS, so there's nothing to archive yet.")
        return Path(p)

    def final_master(self, pid):
        """The approved export (release export approval, bound to its SHA-256). Without it the archive is blocked."""
        r = (self.store.get(pid) or {}).get('release') or {}
        ap = r.get('export_approval') or {}
        if ap.get('path') and ap.get('sha256'):
            return dict(path=ap['path'], sha256=ap['sha256'], size=ap.get('size'))
        return None

    def prepare(self, pid):
        """The full list of what to archive. Read only on the source."""
        src = self.source_dir(pid)
        files, blockers, external = [], [], {}
        for p in sorted(src.rglob('*')):
            if p.is_file() and not SKIP.search(p.name) and ARCHIVE_DIR not in p.parts:
                st = p.stat()
                files.append(dict(rel=p.relative_to(src).as_posix(), path=str(p), size=st.st_size, mtime_ns=str(st.st_mtime_ns), kind='project'))
        inside = {norm(f['path']) for f in files}
        projects = [f for f in files if f['rel'].lower().endswith('.prproj')]
        for f in projects:
            try:
                refs = prproj_paths(f['path'])
            except OSError as e:
                blockers.append(f'Could not read the Premiere project {f["rel"]} ({e}).')
                continue
            f['refs'] = refs
            for ref in refs:
                if norm(ref) in inside or norm(ref) in external:
                    continue
                rp = Path(ref)
                if not rp.is_file():
                    blockers.append(f'{f["rel"]} uses {ref}, which is missing. Relink or find it before archiving.')
                    continue
                st = rp.stat()
                drive = (rp.drive or 'UNC').rstrip(':').replace('\\', '') or 'UNC'
                rel = 'external/' + drive + '/' + '/'.join(rp.parts[1:])
                external[norm(ref)] = dict(rel=rel, path=str(rp), size=st.st_size, mtime_ns=str(st.st_mtime_ns), kind='dependency')
        files += list(external.values())
        fm = self.final_master(pid)
        if not fm:
            blockers.append("Josh hasn't approved the final export, so there's no final master to keep.")
        elif norm(fm['path']) not in {norm(f['path']) for f in files}:
            p = Path(fm['path'])
            if not p.is_file():
                blockers.append(f'The approved final master {p.name} is missing.')
            else:
                st = p.stat()
                files.append(dict(rel='final-master/' + p.name, path=str(p), size=st.st_size, mtime_ns=str(st.st_mtime_ns), kind='final_master'))
        root = norm(src) + os.sep
        for f in files:
            if fm and norm(f['path']) == norm(fm['path']):
                f['kind'], f['expect_sha256'] = 'final_master', fm['sha256']
            # Only a file inside this video's own folder, that no other step depends on, could ever leave the source.
            # Shared dependencies (a background, music, a mic file used elsewhere) and the final master are copy only.
            f['owned'] = norm(f['path']).startswith(root) and f['kind'] == 'project'
            f['copy_only'] = not f['owned']
        total = sum(f['size'] for f in files)
        name = re.sub(r'[<>:"/\\|?*]+', '-', src.name)
        man = dict(project_id=pid, source=str(src), folder_name=name, files=files, total_bytes=total, final_master=fm,
                   prproj=[f['rel'] for f in projects], blockers=blockers, prepared_at=now())
        man['digest'] = digest({k: v for k, v in man.items() if k not in ('prepared_at',)})
        self._write(f'{pid}/manifest.json', man)
        rec = self.record(pid)
        rec.update(manifest_digest=man['digest'], total_bytes=total, file_count=len(files),
                   state='prepared' if not blockers else 'blocked', reason=' '.join(blockers[:3]))
        self._save(pid, rec)
        self.audit('prepared', project_id=pid, files=len(files), bytes=total, blockers=blockers[:10])
        return man

    def manifest(self, pid):
        return self._read(f'{pid}/manifest.json', None)

    # -- step 4: copy
    def target(self, role):
        ident = self.settings().get(role)
        vol = find_volume(ident, self.volumes())
        if not ident:
            return None, ('The Passport drive has not been chosen yet.' if role == 'passport' else 'No independent backup drive is set up.')
        if not vol:
            return None, f'The {role} drive ({ident.get("disk")}, serial {ident.get("serial")}) is not plugged in.'
        return vol, ''

    def dest_root(self, vol, man):
        base = Path(vol['root']) if vol.get('root') else Path(f'{vol["letter"]}:\\')   # root only in tests
        return base / ARCHIVE_DIR / man['folder_name']

    def copy(self, pid, role='passport', stop=None):
        """Verified, resumable copy of the prepared manifest to one drive. Source files are only read."""
        with self.lock:
            el = self.eligibility(pid)
            if not el['eligible']:
                raise ValueError('Not ready to archive. ' + ' '.join(el['blockers'][:3]))
            man = self.manifest(pid)
            if not man or man['blockers']:
                raise ValueError('Prepare the archive first, and clear what it found. ' + ' '.join((man or {}).get('blockers', [])[:3]))
            vol, why = self.target(role)
            if not vol:
                raise ValueError(why)
            dest = self.dest_root(vol, man)
            jf = f'{pid}/{role}-copy.json'
            journal = self._read(jf, {})
            if journal.get('manifest_digest') != man['digest'] or journal.get('volume_id') != vol['volume_id']:
                journal = dict(manifest_digest=man['digest'], volume_id=vol['volume_id'], serial=vol['serial'], files={}, started_at=now())
            need = sum(f['size'] for f in man['files'] if journal['files'].get(f['rel'], {}).get('state') != 'verified')
            if need * MARGIN > int(vol.get('free') or 0):
                raise ValueError(f'The {role} drive has {int(vol.get("free") or 0) // 2**30} GB free, and this needs about {need // 2**30 + 1} GB.')
            self.audit('copy started', project_id=pid, role=role, volume_id=vol['volume_id'], serial=vol['serial'], letter=vol['letter'], bytes=need)
            rec = self.record(pid)
            for i, f in enumerate(man['files']):
                if stop and stop():
                    break
                done = journal['files'].get(f['rel'], {})
                if done.get('state') == 'verified' and (dest / f['rel']).is_file() and (dest / f['rel']).stat().st_size == f['size']:
                    continue
                b = self.busy()
                if b:
                    rec.update(state='paused', reason=f'Paused while {b} is running. It carries on from file {i + 1}.')
                    self._save(pid, rec)
                    self._write(jf, journal)
                    return self.status(pid)
                st = Path(f['path']).stat() if Path(f['path']).is_file() else None
                if not st or st.st_size != f['size'] or str(st.st_mtime_ns) != f['mtime_ns']:
                    journal['files'][f['rel']] = dict(state='changed')
                    self._write(jf, journal)
                    rec.update(state='blocked', reason=f'{f["rel"]} changed or went missing since it was prepared. Prepare again.')
                    self._save(pid, rec)
                    return self.status(pid)
                ok, src_sha = self._copy_one(Path(f['path']), dest / f['rel'])
                if f.get('expect_sha256') and src_sha != f['expect_sha256']:
                    ok = False
                journal['files'][f['rel']] = dict(state='verified' if ok else 'mismatch', sha256=src_sha, size=f['size'], at=now())
                self._write(jf, journal)
                if not ok:
                    rec.update(state='blocked', reason=f'{f["rel"]} did not read back the same after copying. Nothing on the source was touched.')
                    self._save(pid, rec)
                    self.audit('copy mismatch', project_id=pid, role=role, rel=f['rel'])
                    return self.status(pid)
                rec['copy_progress'] = dict(role=role, files_done=sum(1 for x in journal['files'].values() if x.get('state') == 'verified'),
                                            files=len(man['files']), at=now())
                self._save(pid, rec)
            complete = all(journal['files'].get(f['rel'], {}).get('state') == 'verified' for f in man['files'])
            journal['complete'] = complete
            self._write(jf, journal)
            (dest / '_josh_os').mkdir(parents=True, exist_ok=True)
            (dest / '_josh_os' / 'manifest.json').write_text(json.dumps(dict(man, copied=journal), indent=1), encoding='utf-8')
            rec.update(state=f'{role}_copied' if complete else 'copying', reason='' if complete else 'Copy stopped part way. It resumes from here.')
            self._save(pid, rec)
            self.audit('copy finished' if complete else 'copy stopped', project_id=pid, role=role, dest=str(dest))
            return self.status(pid)

    def _copy_one(self, src, dst):
        """Copy to dst.partial while hashing the source, flush to disk, read the copy back and hash it, then rename."""
        dst.parent.mkdir(parents=True, exist_ok=True)
        part = dst.with_name(dst.name + '.partial')
        h = hashlib.sha256()
        with open(src, 'rb') as a, open(part, 'wb') as b:
            for chunk in iter(lambda: a.read(COPY_CHUNK), b''):
                h.update(chunk)
                b.write(chunk)
            b.flush()
            os.fsync(b.fileno())
        src_sha = h.hexdigest()
        if sha256_file(part) != src_sha:
            part.unlink(missing_ok=True)
            return False, src_sha
        os.replace(part, dst)
        shutil.copystat(src, dst)
        return True, src_sha

    # -- step 5: restore and relink proof
    def verify(self, pid, role='passport'):
        """Read every archived file back, and prove each Premiere project relinks fully to the archive."""
        man = self.manifest(pid)
        journal = self._read(f'{pid}/{role}-copy.json', {})
        vol, why = self.target(role)
        if not man or not journal.get('complete') or journal.get('manifest_digest') != man['digest']:
            raise ValueError(f'The {role} copy is not complete for the current preparation.')
        if not vol or vol['volume_id'] != journal.get('volume_id'):
            raise ValueError(why or f'The {role} drive is a different drive from the one copied to.')
        dest = self.dest_root(vol, man)
        problems = []
        for f in man['files']:
            got = dest / f['rel']
            want = journal['files'][f['rel']]['sha256']
            if not got.is_file() or sha256_file(got) != want:
                problems.append(f'{f["rel"]} does not read back the same from the {role} drive.')
            if f.get('kind') == 'final_master' and want != (man.get('final_master') or {}).get('sha256'):
                problems.append('The archived final master is not the approved export.')
        # Each Premiere project is rewritten so every media path points inside this archive, XML-escaped as Premiere
        # writes it, and saved in the archive (_josh_os/relinked/). Each path in the saved copy must resolve to a file
        # inside the archive, so it opens with the original drives unplugged.
        by_path = {norm(f['path']): f for f in man['files']}
        relinked = []
        out_dir = dest / '_josh_os' / 'relinked'
        out_dir.mkdir(parents=True, exist_ok=True)
        inside = norm(dest) + os.sep
        for rel in man['prproj']:
            f = next(x for x in man['files'] if x['rel'] == rel)
            raw = (dest / rel).read_bytes()
            text = (gzip.decompress(raw) if raw[:2] == b'\x1f\x8b' else raw).decode('utf-8', 'replace')
            missing = []
            for ref in f.get('refs') or []:
                m = by_path.get(norm(ref))
                if not m or not (dest / m['rel']).is_file():
                    missing.append(ref)
                    continue
                target = str(dest / m['rel'])
                for form in dict.fromkeys((xml_escape(ref), ref)):
                    text = text.replace('>' + form + '<', '>' + xml_escape(target) + '<')
            out = out_dir / (Path(rel).stem + ' - relinked to archive.prproj')
            out.write_bytes(gzip.compress(text.encode('utf-8')))
            now_refs = prproj_paths(out)
            outside = [p for p in now_refs if not norm(p).startswith(inside) or not Path(p).is_file()]
            if missing or outside or len(now_refs) != len(f.get('refs') or []):
                problems.append(f'{rel} would not relink fully inside the archive ({len(missing) + len(outside)} path(s)).')
            relinked.append(dict(prproj=rel, saved=str(out.relative_to(dest)), media=len(now_refs), missing=len(missing), outside=len(outside)))
        lines = ['original,archived'] + [f'"{f["path"]}","{f["rel"]}"' for f in man['files']]
        (dest / '_josh_os' / 'relink-map.csv').write_text('\n'.join(lines), encoding='utf-8')
        res = dict(at=now(), role=role, ok=not problems, problems=problems, files=len(man['files']), relinked=relinked,
                   volume_id=vol['volume_id'], serial=vol['serial'], manifest_digest=man['digest'],
                   hashes=digest({r: v['sha256'] for r, v in journal['files'].items()}))
        rec = self.record(pid)
        rec.setdefault('verified', {})[role] = res
        rec.update(state=f'{role}_verified' if not problems else 'blocked', reason=' '.join(problems[:3]))
        self._save(pid, rec)
        self.audit('verified' if not problems else 'verify failed', project_id=pid, role=role, problems=problems[:10])
        return res

    def record_premiere_restore(self, pid, result):
        """The result of opening the relinked project from the archive in Premiere and rendering from it (run by Tariq's
        Premiere steps). Only a passing result, bound to this manifest, lets a cleanup review be ready."""
        man = self.manifest(pid)
        ok = bool(result.get('opened')) and bool(result.get('rendered')) and int(result.get('offline_media', 1)) == 0
        rec = self.record(pid)
        rec['premiere_restore'] = dict(at=now(), ok=ok, manifest_digest=(man or {}).get('digest'),
                                       **{k: result.get(k) for k in ('opened', 'rendered', 'offline_media', 'render_path', 'role')})
        self._save(pid, rec)
        self.audit('premiere restore test', project_id=pid, ok=ok)
        return rec['premiere_restore']

    # -- step 6: cleanup review (no changes)
    def cleanup_review(self, pid):
        """What could come off the source, and every reason it can't yet. Moves nothing. Both copies are checked again
        now (drive identity, every hash), never taken from an earlier result."""
        man = self.manifest(pid)
        rec = self.record(pid)
        why = []
        if not CLEANUP_ENABLED:
            why.append('Source cleanup is switched off in Josh OS until it has been proven end to end.')
        if not man:
            why.append('Not prepared yet.')
        el = self.eligibility(pid, recheck=False)
        why += el['blockers']
        fresh = {}
        for role in ('passport', 'backup'):
            ident = self.settings().get(role)
            if not ident:
                why.append('The Passport drive has not been chosen.' if role == 'passport'
                           else 'There is no independent backup drive, so nothing comes off the source.')
                continue
            try:
                v = self.verify(pid, role) if man else {}
            except ValueError as e:
                why.append(f'The {role} copy could not be checked now: {e}')
                continue
            if not v.get('ok') or v.get('serial') != ident.get('serial') or v.get('volume_id') != ident.get('volume_id'):
                why.append(f'The {role} copy did not pass a fresh check on the chosen drive.')
            fresh[role] = v.get('hashes')
        if len(fresh) == 2 and fresh['passport'] != fresh['backup']:
            why.append('The Passport and backup copies are not the same package.')
        pr = rec.get('premiere_restore') or {}
        if not pr.get('ok') or pr.get('manifest_digest') != (man or {}).get('digest'):
            why.append('Premiere has not opened and rendered the archived project yet, so the restore is not proven.')
        if man and man['blockers']:
            why += man['blockers']
        journal = self._read(f'{pid}/passport-copy.json', {})
        files = [dict(rel=f['rel'], path=f['path'], size=f['size'], sha256=(journal.get('files', {}).get(f['rel']) or {}).get('sha256'))
                 for f in (man or {}).get('files', []) if f.get('owned') is True]
        review = dict(project_id=pid, at=now(), ready=not why, blockers=why, files=files, bytes=sum(f['size'] for f in files),
                      keeps='Shared files used by other videos and the final master are copied only and never removed from the source.',
                      note='Review only. Nothing has been moved or deleted.')
        review['digest'] = digest(dict(files=files, manifest=(man or {}).get('digest'), backups=fresh))
        rec = self.record(pid)
        rec['cleanup_review'] = dict(at=review['at'], digest=review['digest'], ready=review['ready'], bytes=review['bytes'], files=len(files))
        self._save(pid, rec)
        self.audit('cleanup reviewed', project_id=pid, ready=review['ready'], files=len(files), bytes=review['bytes'])
        return review

    def approve_cleanup(self, pid, review_digest, said, trusted=False):
        """Josh's own approval of one exact cleanup review, recorded by Josh OS itself (trusted=True only from his own
        session, never from a chat or an agent). Returns its id."""
        if trusted is not True:
            raise ValueError('A cleanup can only be approved by Josh in Josh OS itself.')
        rec = self.record(pid)
        if (rec.get('cleanup_review') or {}).get('digest') != review_digest or not rec['cleanup_review'].get('ready'):
            raise ValueError('That is not the current, ready cleanup review.')
        aid = str(uuid.uuid4())
        rec['cleanup_approval'] = dict(id=aid, digest=review_digest, by='Josh', said=str(said)[:300], at=now(), used=False)
        self._save(pid, rec)
        self.audit('cleanup approved', project_id=pid, approval=aid, digest=review_digest)
        return aid

    def cleanup(self, pid, approval_id, permanent=False, confirm=''):
        """Switched off (CLEANUP_ENABLED). When on, only with Josh's recorded approval of the exact current review, once.
        Every source file is hashed again right before it is touched and must match the archived copy. Files go to the
        Recycle Bin. A permanent delete needs Josh's own words ("delete permanently")."""
        if not CLEANUP_ENABLED:
            raise ValueError('Source cleanup is switched off in Josh OS.')
        rec = self.record(pid)
        ap = rec.get('cleanup_approval') or {}
        if not approval_id or ap.get('id') != approval_id or ap.get('used'):
            raise ValueError('There is no unused approval from Josh for this cleanup.')
        if permanent and str(confirm).strip().lower() != 'delete permanently':
            raise ValueError('A permanent delete needs Josh to confirm it in his own words ("delete permanently").')
        review = self.cleanup_review(pid)
        if not review['ready'] or review['digest'] != ap['digest']:
            raise ValueError('The cleanup changed or is no longer safe since Josh approved it. Show him the new review.')
        changed = [f['rel'] for f in review['files'] if not Path(f['path']).is_file() or sha256_file(f['path']) != f['sha256']]
        if changed:
            raise ValueError(f'{len(changed)} source file(s) changed since they were archived, so nothing was removed.')
        if self.trash is None and not permanent:
            raise ValueError('No Recycle Bin mover is set up, so nothing was removed.')
        ap['used'] = True
        rec['cleanup_approval'] = ap
        self._save(pid, rec)
        done = []
        for f in review['files']:
            p = Path(f['path'])
            (p.unlink if permanent else (lambda p=p: self.trash(p)))()
            done.append(f['path'])
        self.audit('cleanup done', project_id=pid, permanent=permanent, files=len(done), approval=approval_id)
        rec = self.record(pid)
        rec.update(state='archived', reason=f'{len(done)} source file(s) {"deleted" if permanent else "sent to the Recycle Bin"}.')
        self._save(pid, rec)
        return dict(removed=len(done), permanent=permanent)

    # -- readable status
    def status(self, pid):
        rec = self.record(pid)
        man = self.manifest(pid)
        el = self.eligibility(pid, recheck=False)
        proof = rec.get('proof') or {}
        lines = []
        if proof.get('first_verified_at'):
            lines.append(f'Public on YouTube ({proof["video_id"]}), first seen in the public feed {proof["first_verified_at"][:16].replace("T", " ")} UTC.')
        else:
            lines.append('Not proven public yet.')
        lines.append(f'Archive from {el["eligible_from"][:10]}.' if el.get('eligible_from') else 'No archive date until it is proven public.')
        if el['blockers']:
            lines += ['Waiting on: ' + b for b in el['blockers'][:4]]
        if man:
            lines.append(f'Prepared {len(man["files"])} files, {man["total_bytes"] / 2**30:.1f} GB.' + (' Blocked: ' + man['blockers'][0] if man['blockers'] else ''))
        for role, v in (rec.get('verified') or {}).items():
            lines.append(f'{role.capitalize()} copy ' + ('verified, with a relinked Premiere project saved inside the archive.' if v.get('ok') else 'has problems: ' + '; '.join(v['problems'][:2])))
        pr = rec.get('premiere_restore') or {}
        lines.append('Premiere restore test passed.' if pr.get('ok') else 'Premiere has not opened and rendered the archived project yet.')
        if rec.get('cleanup_review'):
            c = rec['cleanup_review']
            lines.append(f'Cleanup review: {c["files"]} files, {c["bytes"] / 2**30:.1f} GB, ' + ('ready for Josh to approve.' if c['ready'] else 'not safe yet.'))
        return dict(project_id=pid, state=rec.get('state'), reason=rec.get('reason', ''), eligible=el['eligible'], lines=lines,
                    proof=proof, progress=rec.get('copy_progress'))

    def context(self, pid):
        """For Tariq's chat."""
        try:
            st = self.status(pid)
        except Exception:  # noqa: BLE001
            return ''
        return '\n'.join(['Archive to the Passport (your job once the video is confirmed public; you never delete, Josh approves any cleanup):']
                         + ['- ' + x for x in st['lines']])


PUBLIC_PROPS = {'youtube_public': {'type': 'object', 'description': (
    'Only when Josh (or YouTube Studio, as Josh tells you) says a video is now live and public, and you know its YouTube '
    'video id (the 11 characters after watch?v=). Josh OS checks it in the channel\'s public feed itself and tells Tariq. '
    'Never for a scheduled, unlisted or private video. Leave it out otherwise.'), 'properties': {
        'project_id': {'type': 'string'}, 'video_id': {'type': 'string'}}, 'required': ['project_id', 'video_id']}}


def describe_proof(p):
    if p.get('first_verified_at') and (p.get('checks') or [{}])[-1].get('public') is True:
        return (f"Josh OS checked it in the channel's public feed, so it's confirmed public. Tariq can archive it from "
                f"{p['eligible_from'][:10]}.")
    last = (p.get('checks') or [{}])[-1]
    return "It isn't confirmed public yet. " + (last.get('reason') or '')


def media_busy():
    """OBS or ffmpeg running means recording or rendering, so archive copies wait."""
    try:
        out = subprocess.run(['tasklist', '/FO', 'CSV', '/NH'], capture_output=True, text=True, timeout=30,
                             creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)).stdout.lower()
    except Exception:  # noqa: BLE001 - unknown means wait
        return 'an unknown process check'
    for name in ('obs64.exe', 'ffmpeg.exe'):
        if f'"{name}"' in out:
            return name.split('.')[0].upper() if name.startswith('obs') else 'ffmpeg'
    return ''
