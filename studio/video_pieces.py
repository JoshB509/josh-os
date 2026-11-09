"""The pieces of a video and whether each one is there yet.

The video page shows a video as a row of pieces (title, thumbnail, script, recording, edit,
animations, publish), each ticked once it's locked in. Josh locks in title, thumbnail and
script himself. Recording, edit and animations are also checked against the video's folder:
the take in Recorded/Input, the Premiere project Josh hands over in video/, and Tariq's deck.

Recording uses the existing safe take mover (youtube_recording.py). "I'm recording this now"
sets this video as the one being recorded. "It's recorded" lists the new takes OBS saved in
E:\\Videos\\Input today, and moving one checks OBS has stopped, that the file has finished
writing, and never overwrites anything.
"""
import time
from datetime import datetime
from pathlib import Path

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
        out['active'] = bool(a and a.get('project_id') == pid)
        video = (p.get('media') or {}).get('video')
        if video and Path(video).is_file():
            out['recording'] = dict(path=video, name=Path(video).name)
        elif root and (root / 'Recorded' / 'Input').is_dir():
            takes = sorted((f for f in (root / 'Recorded' / 'Input').iterdir() if f.suffix.lower() in VIDEO_TYPES), key=lambda f: f.stat().st_mtime)
            if takes:
                out['recording'] = dict(path=str(takes[-1]), name=takes[-1].name)
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
        src = self.recording.input
        if not src.is_dir():
            return []
        today = datetime.now().date()
        files = [f for f in src.iterdir() if f.is_file() and f.suffix.lower() in VIDEO_TYPES
                 and datetime.fromtimestamp(f.stat().st_mtime).date() == today]
        by_stem = {}
        for f in files:
            by_stem.setdefault(f.stem, []).append(f)
        out = []
        for stem, group in by_stem.items():
            pick = next((f for f in group if f.suffix.lower() == '.mp4'), group[0])
            st = pick.stat()
            out.append(dict(path=str(pick), name=pick.name, size_mb=round(st.st_size / 1e6), modified=datetime.fromtimestamp(st.st_mtime).isoformat(timespec='seconds'),
                            settling=time.time() - st.st_mtime < 5, also=[f.name for f in group if f != pick]))
        return sorted(out, key=lambda t: t['modified'], reverse=True)

    def recorded(self, pid, path):
        """Move the chosen take into the video's Recorded/Input, renamed after the video."""
        if not path:
            raise ValueError('Choose the recording to move.')
        root = self.folder(pid)
        if not root:
            raise ValueError('This video has no folder yet.')
        p = self._to_recording(pid)
        self.recording.activate(pid, p['revision'], str(root))
        p = self.store.get(pid)
        self.recording.select(pid, p['revision'], path)
        take = self.recording.lock_take()
        p = self.store.get(pid)
        locks = dict(p.get('locks') or {}, recording=take.get('completed_at') or datetime.now().isoformat(timespec='seconds'))
        self.store.save(pid, {'locks': locks, 'stage': 'Recorded'}, p['revision'])
        return dict(take=take, status=self.status(pid))
