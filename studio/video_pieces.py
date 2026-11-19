"""The pieces of a video and whether each one is there yet.

The video page shows a video as a row of pieces (title, thumbnail, script, recording, edit,
animations, publish), each ticked once it's locked in. Josh locks in title, thumbnail and
script himself. Recording uses the saved media binding; a file merely appearing in the folder
is not completion. Edit and animations are checked against the video's folder.

Recording uses the existing safe take mover (youtube_recording.py). "I'm recording this now"
sets this video as the one being recorded. "It's recorded" lists the new takes OBS saved in
the shared Input or the video's Recorded/Input today. Files already filed for this video
are validated and bound in place; shared input moves still require idle OBS and never overwrite.
"""
import json
import time
from datetime import datetime
from pathlib import Path
from .store import Conflict

PIECES = ('title', 'thumbnail', 'script', 'recording', 'edit', 'animations', 'publish')
VIDEO_TYPES = ('.mp4', '.mkv', '.mov', '.webm')
SKIP_DIRS = ('Adobe Premiere Pro Auto-Save', 'backup', 'Backups')


class VideoPieces:
    def __init__(self, store, folders, recording, edit_review):
        self.store, self.folders, self.recording, self.edit_review = store, folders, recording, edit_review

    def folder(self, pid):
        try:
            p = self.folders.path(pid)
        except (ValueError, OSError):
            return None
        return Path(p) if p else None

    def status(self, pid):
        p = self.store.get(pid)
        root = self.folder(pid)
        out = dict(folder=str(root or ''), recording=None, edit=None, deck=None, active=False)
        with self.store.connect() as c:
            a = self.recording.active(c)
        out['active'] = bool(a and a.get('project_id') == pid and p['stage'] == 'Recording')
        video = (p.get('media') or {}).get('video')
        if video and Path(video).is_file():
            out['recording'] = dict(path=video, name=Path(video).name)
        if root and (root / 'video').is_dir():
            projects = [f for f in (root / 'video').rglob('*.prproj') if not any(s in f.parts for s in SKIP_DIRS)]
            if projects:
                newest = max(projects, key=lambda f: f.stat().st_mtime)
                out['edit'] = dict(path=str(newest), name=newest.name,
                                   saved=datetime.fromtimestamp(newest.stat().st_mtime).isoformat(timespec='minutes'))
        if root:
            try:
                slides, shots = self.edit_review.deck_files(pid)
            except (KeyError, ValueError, OSError):
                slides = shots = None
            if slides:
                out['deck'] = dict(slides=slides.name, shot_list=shots.name if shots else '')
        return out

    # Recording -------------------------------------------------------------
    def _to_recording(self, pid):
        p = self.store.get(pid)
        if p['stage'] != 'Recording':
            p = self.store.save(pid, {'stage': 'Recording'}, p['revision'])
        return p

    def start(self, pid):
        """This is the video being recorded now."""
        root = self.folder(pid)
        if not root:
            raise ValueError('This video has no folder yet.')
        p = self._to_recording(pid)
        self.recording.activate(pid, p['revision'], str(root))
        return self.status(pid)

    def takes(self, pid):
        """New recordings OBS saved today, newest first. An .mp4 made from an .mkv is shown once, as the .mp4."""
        today = datetime.now().date()
        # OBS saves to E:\Videos\Input, or straight into this video's Recorded/Input after Film (studio/filming.py).
        locked = str((self.store.get(pid).get('media') or {}).get('video') or '')
        root = self.folder(pid)
        dirs = [d for d in (self.recording.input, root / 'Recorded' / 'Input' if root else None) if d and d.is_dir()]
        files = [f for d in dirs for f in d.iterdir() if f.is_file() and f.suffix.lower() in VIDEO_TYPES and str(f) != locked
                 and datetime.fromtimestamp(f.stat().st_mtime).date() == today]
        if not files:
            return []
        by_stem = {}
        for f in files:
            by_stem.setdefault((f.parent, f.stem), []).append(f)
        out = []
        for stem, group in by_stem.items():
            pick = next((f for f in group if f.suffix.lower() == '.mp4'), group[0])
            st = pick.stat()
            out.append(dict(path=str(pick), name=pick.name, size_mb=round(st.st_size / 1e6), modified=datetime.fromtimestamp(st.st_mtime).isoformat(timespec='seconds'),
                            settling=time.time() - st.st_mtime < 5, also=[f.name for f in group if f != pick]))
        return sorted(out, key=lambda t: t['modified'], reverse=True)

    def recorded(self, pid, path):
        """Complete the exact selected take, or resume the same durable request on retry."""
        if not path:
            raise ValueError('Choose the recording to move.')
        root = self.folder(pid)
        if not root:
            raise ValueError('This video has no folder yet.')
        if not self.recording.lock.acquire(False):
            raise Conflict('Already checking this recording. Wait for it to finish.')
        try:
            p = self.store.get(pid)
            with self.store.connect() as c:
                binding = self.recording.binding(c, pid)
                selection = binding.get('selection') or {}
                rows = c.execute('SELECT body FROM youtube_recording_takes WHERE project_id=? ORDER BY rowid DESC', (pid,)).fetchall()
                takes = [json.loads(row['body']) for row in rows]
                pending = next((r for r in takes if r['id'] == selection.get('id')), None)
            requested = Path(path).resolve()
            completed = next((r for r in takes if r['phase'] == 'complete' and
                              requested in (Path(r['source']['path']), Path(r['destination']))), None)
            if completed:
                take = self.recording.completed(p, completed)
            else:
                if pending and pending['phase'] != 'complete':
                    if requested != Path(pending['source']['path']):
                        raise Conflict('Recover the pending Lock Take before selecting another source.')
                    # Keep the original selection id, identity and expected revision for recovery.
                else:
                    if p.get('archived') or p['stage'] not in ('Ready to record', 'Recording'):
                        raise Conflict('Choose an unarchived video ready for recording before selecting a new take.')
                    p = self._to_recording(pid)
                    self.recording.activate(pid, p['revision'], str(root))
                    self.recording.select(pid, p['revision'], path)
                take = self.recording.lock_take(expected_pid=pid)
        finally:
            self.recording.lock.release()
        return dict(take=take, status=self.status(pid))
