"""Release readiness for one video: what's really done before it goes on YouTube, and the next step for each part.

It reads the existing workflow rather than keeping a separate checklist. Title is the project's chosen_title and its
title lock, thumbnail is the thumbnail lock, the export comes from the video's exports folder, the date is the
project's publish_date, publishing health comes from the YouTube publisher's own files, and the upload comes from its
jobs. What had no home before lives on the project as "release":
- export_approval: Josh's sign-off on one exact export file (size, modified time and SHA-256). A rendered export is
  not approved until he signs it off, and a changed file loses the approval.
- title_approval and thumbnail_approval: who chose them, when and in what words, next to the existing locks.
- description and cta: Sam's drafts for review, then Josh's approval of an exact version (by its SHA-256).
- publish_time: the agreed time and time zone, set by Josh.
Nothing here uploads, schedules, publishes, creates links or sends anything. It never approves on Josh's behalf.
"""
import hashlib
import json
import os
from datetime import date, datetime, timezone
from pathlib import Path

from .store import now, Conflict

RELEASE_KEYS = {'export_approval', 'title_approval', 'thumbnail_approval', 'description', 'cta', 'publish_time', 'links', 'chapters', 'history', 'youtube'}
# What YouTube itself reported about the published video, read with videos.list. Never an approval: Josh OS shows it as
# what is live, beside whatever was drafted or approved here.
YOUTUBE_KEYS = {'video_id', 'url', 'channel', 'title', 'description', 'privacy', 'upload_status', 'published_at', 'read_at', 'source'}
PUBLISHING = Path(__file__).resolve().parents[1] / 'Data' / 'youtube-publishing'


def validate(release):
    """Checked by Store.validate for every save."""
    if not isinstance(release, dict) or set(release) - RELEASE_KEYS:
        raise ValueError('Invalid release details')
    if len(json.dumps(release)) > 60000:
        raise ValueError('Release details are too long')
    for k in ('export_approval', 'title_approval', 'thumbnail_approval', 'description', 'cta', 'publish_time', 'youtube'):
        if k in release and not isinstance(release[k], dict):
            raise ValueError('Invalid release details')
    if 'youtube' in release and (set(release['youtube']) - YOUTUBE_KEYS or not str(release['youtube'].get('video_id') or '')):
        raise ValueError('Invalid YouTube record')
    if 'links' in release and (not isinstance(release['links'], list) or any(not isinstance(l, dict) for l in release['links'])):
        raise ValueError('Invalid release links')


def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for b in iter(lambda: f.read(1 << 20), b''):
            h.update(b)
    return h.hexdigest()


def text_sha(text):
    return hashlib.sha256(str(text or '').encode('utf-8')).hexdigest()


class Release:
    def __init__(self, store, folders=None, publishing_dir=None, jobs=None, agents=None):
        self.store, self.folders, self.agents = store, folders, agents
        self.pub = Path(publishing_dir) if publishing_dir else PUBLISHING
        self.jobs = jobs   # callable returning the publisher's jobs, or None
        self.chapters = None   # studio/chapters.py, set by the server

    # Reading the workflow ----------------------------------------------------------------------------------------
    def folder(self, pid):
        try:
            f = self.folders.path(pid) if self.folders else None
        except Exception:  # noqa: BLE001
            return None
        return Path(f) if f else None

    def exports(self, pid):
        f = self.folder(pid)
        d = f / 'video' / 'exports' if f else None
        if not d or not d.is_dir():
            return []
        return sorted((x for x in d.iterdir() if x.suffix.lower() in ('.mp4', '.mov', '.m4v') and x.is_file()), key=lambda x: x.stat().st_mtime, reverse=True)

    def publisher(self):
        client = (self.pub / 'desktop-client.json').is_file()
        saved = (self.pub / 'oauth.dpapi').is_file()
        return dict(client_configured=client, connected=saved, verified=False)   # a saved file, never checked with YouTube here

    def view(self, pid, today=None):
        p = self.store.get(pid)
        r = p.get('release') or {}
        locks = p.get('locks') or {}
        today = today or date.today()
        items = []

        def item(key, label, state, detail, next_step='', action=''):
            items.append(dict(key=key, label=label, state=state, detail=detail, next=next_step, action=action))

        # Final export: rendered is not the same as approved.
        ex = self.exports(pid)
        appr = r.get('export_approval') or {}
        ok_file = None
        if appr.get('path') and Path(appr['path']).is_file():
            st = Path(appr['path']).stat()
            if st.st_size == appr.get('size') and str(st.st_mtime_ns) == str(appr.get('mtime_ns')):
                ok_file = Path(appr['path'])
        newest = ex[0] if ex else None
        if ok_file:
            extra = f' A newer export, {newest.name}, is in the folder.' if newest and newest != ok_file else ''
            item('export', 'Final export', 'done', f'{ok_file.name}, approved by {appr.get("by", "Josh")} on {appr.get("at", "")[:10]}.{extra}')
        elif appr.get('path'):
            item('export', 'Final export', 'todo', f'{Path(appr["path"]).name} changed or went missing after it was approved.',
                 'Watch the export again and approve it.', 'approve_export')
        elif newest:
            item('export', 'Final export', 'todo', f'{newest.name} is rendered, {newest.stat().st_size / 1e6:,.0f} MB. Nobody has approved it for release.',
                 'Watch it and approve it if it is the one to publish.', 'approve_export')
        else:
            item('export', 'Final export', 'todo', 'No export in the video\'s exports folder yet.', 'Tariq renders it once the edit is approved.')

        # Title.
        title = next((t['text'] for t in p.get('titles') or [] if t['id'] == p.get('chosen_title')), '')
        if title and locks.get('title'):
            ta = r.get('title_approval') or {}
            item('title', 'Title', 'done', title + (f' (chosen by {ta["by"]} on {ta.get("at", "")[:10]})' if ta.get('by') else ''))
        elif title:
            item('title', 'Title', 'todo', f'{title} is picked but not locked in.', 'Lock in the title.')
        else:
            item('title', 'Title', 'todo', f'{len(p.get("titles") or [])} ideas, none chosen.', 'Pick and lock in a title.')

        # Thumbnail.
        tha = r.get('thumbnail_approval') or {}
        th = next((t for t in p.get('thumbnails') or [] if t['asset'] == tha.get('asset')), None)
        # The chosen file must still be byte for byte what Josh chose.
        same = False
        if th and tha.get('path') and Path(tha['path']).is_file() and tha.get('sha256'):
            same = sha256(tha['path']) == tha['sha256']
        if th and locks.get('thumbnail') and same:
            item('thumbnail', 'Thumbnail', 'done', f'{th["name"]} (chosen by {tha.get("by", "Josh")} on {tha.get("at", "")[:10]})')
        elif th and tha.get('path') and Path(tha['path']).is_file() and not same:
            item('thumbnail', 'Thumbnail', 'todo', f'{th["name"]} changed after it was chosen, so that choice no longer counts.',
                 'Look at it again and choose it again.')
        elif th and not Path(tha.get('path') or th.get('source_path') or '').is_file():
            item('thumbnail', 'Thumbnail', 'todo', f'{th["name"]} was chosen, but its file is missing.', 'Find the file or choose again.')
        elif th:
            item('thumbnail', 'Thumbnail', 'todo', f'{th["name"]} is selected but not locked in, or its file is missing.', 'Lock in the thumbnail.')
        else:
            item('thumbnail', 'Thumbnail', 'todo', f'{len(p.get("thumbnails") or [])} options, none selected.', 'Pick the thumbnail.')

        # Description.
        d = r.get('description') or {}
        if d.get('text') and (d.get('approved') or {}).get('sha') == text_sha(d['text']):
            item('description', 'Description', 'done', f'Approved by {d["approved"].get("by", "Josh")} on {d["approved"].get("at", "")[:10]}.')
        elif d.get('text'):
            item('description', 'Description', 'review', f'{d.get("by", "Sam").title()} drafted it on {d.get("drafted_at", "")[:10]}. Waiting for your review.',
                 'Read it and approve it, or ask Sam for changes.', 'approve_description')
        else:
            item('description', 'Description', 'todo', 'Not written yet.', 'Ask Sam to draft it.', 'draft_packaging')

        # Chapters, made by Sam from the final export (studio/chapters.py).
        if self.chapters is not None:
            try:
                cs = self.chapters.state(pid)
            except Exception as e:  # noqa: BLE001
                cs = dict(state='error', reason=str(e)[:200])
            prep = cs.get('prep') or {}
            n = len(prep.get('chapters') or [])
            if cs['state'] == 'approved':
                item('chapters', 'Chapters', 'done', f'{n} chapters, approved.')
            elif cs['state'] == 'review':
                item('chapters', 'Chapters', 'review', f'Sam made {n} chapters from the approved export. {len(prep.get("flags") or [])} notes to check.',
                     'Check the chapters and approve them.')
            elif cs['state'] == 'preview':
                item('chapters', 'Chapters', 'todo', f'Preview only: {n} chapters from the unapproved export. They are made again once you approve it.',
                     'Approve the final export first.', 'preview_chapters')
            elif cs['state'] == 'stale':
                item('chapters', 'Chapters', 'todo', cs.get('reason', 'Out of date.'), 'Make them again.', 'preview_chapters')
            else:
                item('chapters', 'Chapters', 'todo', 'Not made yet. Sam makes them once the final export is approved.', '', 'preview_chapters')

        # CTA, lead magnet and tracked links.
        lm = p.get('lead_magnet') or {}
        cta = r.get('cta') or {}
        links = [l for l in (r.get('links') or []) if isinstance(l, dict) and str(l.get('url') or '').startswith('https://')]
        decision = lm.get('decision')
        cta_ok = bool(cta.get('text')) and (cta.get('approved') or {}).get('sha') == text_sha(cta['text'])
        bits = [{'yes': f'Lead magnet: {lm.get("name") or "yes, unnamed"}.', 'none': 'No lead magnet for this video.'}.get(decision, 'Lead magnet not decided.')]
        if cta_ok:
            bits.append('CTA approved.')
        elif cta.get('text') and cta.get('approved'):
            bits.append('The CTA changed after it was approved, so it needs approving again.')
        elif cta.get('text'):
            bits.append(f'CTA drafted by {str(cta.get("by") or "Sam").title()}, not approved.')
        else:
            bits.append('No CTA yet.')
        bits.append(f'{len(links)} tracked link{"" if len(links) == 1 else "s"} recorded.' if links else 'No tracked link recorded yet.')
        need_link = decision == 'yes'
        done_cta = decision in ('yes', 'none') and cta_ok and (links or not need_link)
        nxt = ('' if done_cta else 'Decide the lead magnet.' if decision not in ('yes', 'none') else
               'Approve the CTA.' if not cta_ok else 'Make the tracked link in Linklr and record it here.')
        item('cta', 'CTA, lead magnet and links', 'done' if done_cta else ('review' if cta.get('text') else 'todo'), ' '.join(bits), nxt,
             'approve_cta' if cta.get('text') and not cta_ok else '')

        # Publish date and time.
        pt = r.get('publish_time') or {}
        pd = p.get('publish_date') or ''
        if not pd:
            item('date', 'Publish date and time', 'todo', 'No date set.', 'Agree a date and time.')
        elif pd < today.isoformat():
            item('date', 'Publish date and time', 'todo', f'The date on the card, {pd}, has already passed.', 'Agree a new date and time.')
        elif not pt.get('time'):
            item('date', 'Publish date and time', 'todo', f'{pd}, no time agreed.', 'Agree the time and time zone.')
        else:
            item('date', 'Publish date and time', 'done', f'{pd} at {pt["time"]} {pt.get("tz", "")}'.strip())

        # Publisher authorization.
        pub = self.publisher()
        if pub['connected']:
            # A saved credential file proves nothing about whether YouTube still accepts it, and Josh OS doesn't test it
            # from here, so it never shows as done.
            item('publisher', 'YouTube publishing access', 'review', 'A saved YouTube credential is on this PC, but it has not been checked with '
                 'YouTube. It may have expired or been revoked.', 'Check it on the Publishing page before uploading.', 'open_publishing')
        elif pub['client_configured']:
            item('publisher', 'YouTube publishing access', 'blocked', 'The app is set up, but YouTube has not been authorised on this PC yet.',
                 'Connect publishing on the Publishing page.', 'open_publishing')
        else:
            item('publisher', 'YouTube publishing access', 'blocked', 'Publishing is not set up on this PC.', 'Set up publishing.', 'open_publishing')

        # Upload and processing, from the publisher's own jobs.
        job = None
        try:
            # Tied to the approved file's fingerprint, never its path, so a newer file at the same path doesn't count.
            mine = [j for j in (self.jobs() if self.jobs else []) if ok_file and appr.get('sha256')
                    and (j.get('packet') or {}).get('video', {}).get('sha256') == appr['sha256']]
            job = mine[0] if mine else None
        except Exception:  # noqa: BLE001
            job = None
        yt = r.get('youtube') or {}
        if yt.get('privacy') == 'public' and yt.get('upload_status') == 'processed':
            item('upload', 'Upload and processing', 'done', f'Public on YouTube as {yt["video_id"]} since {str(yt.get("published_at") or "")[:10]}, '
                 f'read from YouTube on {str(yt.get("read_at") or "")[:10]}.')
        elif job and job.get('video_id') and job.get('state') in ('processed', 'scheduled', 'published', 'done'):
            item('upload', 'Upload and processing', 'done', f'Uploaded as {job["video_id"]}, {job["state"]}.')
        elif job:
            item('upload', 'Upload and processing', 'todo', f'Upload job {job["state"].replace("_", " ")}.', 'Finish it on the Publishing page.', 'open_publishing')
        else:
            item('upload', 'Upload and processing', 'waiting', 'Not started. It needs everything above first.')

        ready = sum(1 for i in items if i['state'] == 'done')
        nxt = next((i for i in items if i['state'] != 'done' and i['next']), None)
        chap = (((self.chapters.state(pid) if self.chapters is not None else {}) or {}).get('prep') or {}) if self.chapters is not None else {}
        return dict(project_id=pid, items=items, done=ready, total=len(items), next=nxt, chapters_text=chap.get('description', ''),
                    chapters_flags=chap.get('flags') or [], chapters_preview=bool(chap.get('preview')),
                    description=d.get('text', ''), description_sha=text_sha(d['text']) if d.get('text') else '', cta=cta.get('text', ''), cta_sha=text_sha(cta['text']) if cta.get('text') else '', export_candidate=str(newest) if newest else '',
                    # For the Packaging page: the tracked links (address and where each goes only) and the lead magnet choice.
                    links=[dict(url=l['url'], source=str(l.get('source_type') or '')) for l in links],
                    lead_magnet=dict(decision=lm.get('decision') or '', name=lm.get('name') or ''),
                    youtube=r.get('youtube') or None)

    # Josh's own approvals (each from his click or his words, never made up) ------------------------------------------
    def _save_release(self, pid, change, extra=None, *, expected_revision=None):
        p = self.store.get(pid)
        if expected_revision is not None and (type(expected_revision) is not int or p['revision'] != expected_revision):
            raise Conflict("The video changed while this release draft was being prepared. Nothing was replaced.")
        r = dict(p.get('release') or {})
        r.update(change)
        hist = list(r.get('history') or [])[-40:]
        hist.append(dict(at=now(), what=', '.join(change)))
        r['history'] = hist
        return self.store.save(pid, dict(extra or {}, release=r), p['revision'])

    def approve_export(self, pid, path, by='Josh', said='Approved on the video page'):
        f = Path(str(path or ''))
        if f not in self.exports(pid):
            raise ValueError("That file isn't in this video's exports folder.")
        st = f.stat()
        out = self._save_release(pid, dict(export_approval=dict(path=str(f), size=st.st_size, mtime_ns=str(st.st_mtime_ns), sha256=sha256(f),
                                                                 by=by, said=str(said)[:300], at=now())))
        # Approving the final export is what starts Sam's real chapter prep, from that exact export.
        if self.chapters is not None:
            import threading
            threading.Thread(target=self._chapters_after_approval, args=(pid,), daemon=True, name='sam-chapters').start()
        return out

    def _chapters_after_approval(self, pid):
        try:
            self.chapters.prepare(pid)
        except Exception as e:  # noqa: BLE001 - shown on the checklist as not made
            try:
                (Path(self.store.root) / 'chapters-last-error.txt').write_text(f'{now()} {pid} {e}', encoding='utf-8')
            except OSError:
                pass

    def approve_description(self, pid, digest, by='Josh', said='Approved on the video page'):
        p = self.store.get(pid)
        d = dict((p.get('release') or {}).get('description') or {})
        if not d.get('text') or digest != text_sha(d['text']):
            raise ValueError('That approval was for a different version of the description. Read it again and approve the one you see.')
        d['approved'] = dict(sha=digest, by=by, said=str(said)[:300], at=now())
        return self._save_release(pid, dict(description=d))

    def approve_cta(self, pid, digest, by='Josh', said='Approved on the video page'):
        p = self.store.get(pid)
        c = dict((p.get('release') or {}).get('cta') or {})
        if not c.get('text') or digest != text_sha(c['text']):
            raise ValueError('That approval was for a different version of the CTA. Read it again and approve the one you see.')
        c['approved'] = dict(sha=digest, by=by, said=str(said)[:300], at=now())
        return self._save_release(pid, dict(cta=c))

    def record_published(self, pid, video, expect_revision, expect_channel, read_at=None):
        """Brings the local record in line with a video YouTube reports as public and processed. The video is what
        VideoEditor.read returned (videos.list, snippet and status), so nothing on YouTube changes. It only sets the stage
        to Published, the publish lock and date to when YouTube says it went public, and keeps YouTube's own title and
        description under release.youtube. Drafts, approvals, chapters and history stay as they were, and the save is
        refused if the project changed since it was read or if the video's title or channel don't match."""
        from datetime import datetime, timedelta, timezone
        p = self.store.get(pid)
        if p['revision'] != expect_revision:
            raise ValueError(f'The project is at revision {p["revision"]}, not {expect_revision}. Read it again first.')
        sn, st = video.get('snippet') or {}, video.get('status') or {}
        chosen = next((t['text'] for t in p.get('titles') or [] if t['id'] == p.get('chosen_title')), '')
        if sn.get('channelId') != expect_channel:
            raise ValueError('That video is on another channel.')
        if sn.get('title') != chosen:
            raise ValueError("YouTube's title is not the title chosen for this video.")
        if st.get('privacyStatus') != 'public' or st.get('uploadStatus') != 'processed' or not sn.get('publishedAt'):
            raise ValueError('YouTube does not report this video as public and processed.')
        at = sn['publishedAt']
        dubai = (datetime.fromisoformat(at.replace('Z', '+00:00')) + timedelta(hours=4)).date().isoformat()
        yt = dict(video_id=video['id'], url=f'https://youtu.be/{video["id"]}', channel=sn['channelId'], title=sn['title'],
                  description=sn.get('description') or '', privacy=st['privacyStatus'], upload_status=st['uploadStatus'],
                  published_at=at, read_at=read_at or datetime.now(timezone.utc).isoformat(), source='YouTube Data API videos.list, read only')
        locks = dict(p.get('locks') or {})
        locks.setdefault('publish', at)
        return self._save_release(pid, dict(youtube=yt), extra=dict(stage='Published', publish_date=dubai, locks=locks))

    def set_publish_time(self, pid, time, tz, by='Josh', *, expected_revision=None):
        import re
        if not re.fullmatch(r'([01]\d|2[0-3]):[0-5]\d', str(time or '')) or not str(tz or '').strip() or len(str(tz)) > 60:
            raise ValueError('Give a time like 16:00 and a time zone.')
        return self._save_release(pid, dict(publish_time=dict(time=time, tz=str(tz).strip(), by=by, at=now())), expected_revision=expected_revision)

    # Sam's drafts -------------------------------------------------------------------------------------------------
    def draft_packaging(self, pid, run=None, *, expected_revision=None):
        """Sam drafts the description and CTA from the script, title and lead magnet. Saved as drafts for Josh to review.
        No tools, no outside services, no Discord or email."""
        agents = self.agents
        p = self.store.get(pid)
        if expected_revision is not None and (type(expected_revision) is not int or p['revision'] != expected_revision):
            raise Conflict('The video changed. Read it again before drafting.')
        title = next((t['text'] for t in p.get('titles') or [] if t['id'] == p.get('chosen_title')), p['name'])
        lm = p.get('lead_magnet') or {}
        team = agents.team.all()
        sam = next(a for a in team['agents'] if a['id'] == 'sam')
        learnings = [l for l in team['learnings'] if l['agent_id'] in ('', 'sam')][:80]
        schema = {'type': 'object', 'required': ['description', 'cta'], 'properties': {
            'description': {'type': 'string', 'description': 'The YouTube description, ready to review.'},
            'cta': {'type': 'string', 'description': 'One or two sentences: the call to action said in the video and used in the description.'},
            'lead_magnet': {'type': ['string', 'null'], 'description': 'The lead magnet the script points to, if it names one.'},
            'notes': {'type': ['string', 'null'], 'description': 'Anything Josh must decide, such as a missing link.'}}}
        prompt = '\n'.join([
            f'Draft the YouTube description and the CTA for "{title}". Josh reviews both before anything is used.',
            'Write a placeholder like [tracked link] wherever a link goes. Never invent a URL, a number or a result.',
            'Use only the script below and what Josh OS knows. Keep Josh\'s house style and his writing rules.',
            f'Lead magnet on the card: {json.dumps(lm)}', '', '--- Script ---', (p.get('script') or '')[:60000]])
        _, answer = (run or agents.run)(agents.desk_system_prompt(sam, learnings), prompt, schema, (), full=True, tools='Read', timeout=240)
        answer = answer if isinstance(answer, dict) else {}
        text = str(answer.get('description') or '').strip()
        if not text:
            raise ValueError("Sam didn't come back with a description.")
        at = now()
        change = dict(description=dict(text=text[:5000], by='sam', drafted_at=at, notes=str(answer.get('notes') or '')[:1000]),
                      cta=dict(text=str(answer.get('cta') or '')[:1000], by='sam', drafted_at=at, lead_magnet=str(answer.get('lead_magnet') or '')[:300]))
        out = self._save_release(pid, change, expected_revision=p['revision'])
        try:
            agents.post('sam', f'I drafted the description and CTA for {title}. They are on the video\'s Publish piece for you to review. '
                               'Nothing is uploaded or linked yet.', notice=False)
        except Exception:  # noqa: BLE001
            pass
        return out
