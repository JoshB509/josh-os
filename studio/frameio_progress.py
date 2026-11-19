"""Local-only Frame.io chat presentation. Never upload, authenticate or poll Frame.io."""
from datetime import datetime, timezone
import math
import re
from urllib.parse import urlsplit

STALE_SECONDS = 300
MAX_COUNT = 2 ** 53 - 1
VIEW_HOSTS = {'app.frame.io', 'next.frame.io'}
MESSAGES = {
    'queued': 'Queued for Frame.io. Waiting for the upload worker.',
    'uploading': 'Uploading to Frame.io. Counts show accepted upload parts only.',
    'processing': 'Upload sent. Waiting for verified playback in Frame.io.',
    'complete': 'Upload complete and playback verified in Frame.io.',
    'blocked': 'This review needs attention. Open the video review details before retrying.',
    'stalled': 'No recent progress update. Completion has not been confirmed.',
}


def safe_id(value):
    return isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_-]{1,80}', value) is not None


def safe_view_url(value):
    if not isinstance(value, str) or len(value) > 500 or re.search(r'[\s\\\x00-\x1f]', value):
        return ''
    try:
        url = urlsplit(value)
        if (url.scheme != 'https' or url.hostname not in VIEW_HOSTS or url.username or url.password
                or url.port not in (None, 443) or url.query or url.fragment or not url.path.startswith('/')
                or '%' in url.netloc):
            return ''
    except ValueError:
        return ''
    return value


def _count(value):
    return type(value) is int and 0 <= value <= MAX_COUNT


def _timestamp(value):
    try:
        stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return stamp.astimezone(timezone.utc) if stamp.tzinfo else None
    except (AttributeError, ValueError, TypeError):
        return None


def present_review(record, *, at=None):
    """Return an allowlisted snapshot; raw reasons, filenames and logs never leave here."""
    at = at or datetime.now(timezone.utc)
    if not safe_id(record.get('id')) or not safe_id(record.get('project_id')):
        raise ValueError('Invalid review identity.')
    raw = record.get('state')
    export = record.get('export') if isinstance(record.get('export'), dict) else {}
    remote = record.get('frameio') if isinstance(record.get('frameio'), dict) else {}
    progress = record.get('progress') if isinstance(record.get('progress'), dict) else {}
    state = {'staged': 'queued', 'checked_absent': 'queued', 'uploading': 'uploading',
             'putting': 'uploading', 'processing': 'processing'}.get(raw, 'blocked')
    if raw == 'staged' and record.get('reason'):
        state = 'blocked'
    reason = str(record.get('reason') or '')
    if raw == 'putting' and any(part in reason for part in (
            'The export file changed or is missing', 'could not be read in full', 'did not go through')):
        state = 'blocked'
    duration, wanted = remote.get('duration'), export.get('duration')
    duration_ok = duration is None or not wanted or (
        type(duration) in (int, float) and type(wanted) in (int, float)
        and math.isfinite(duration) and math.isfinite(wanted) and abs(duration - wanted) <= 1)
    if (raw == 'ready' and remote.get('upload_complete') is True and remote.get('playable') is True
            and isinstance(remote.get('file_id'), str) and remote['file_id']
            and _count(export.get('size')) and export['size'] > 0
            and type(remote.get('file_size')) is int and remote['file_size'] == export['size'] and duration_ok):
        state = 'complete'
    stamp = _timestamp(record.get('updated_at')) or _timestamp(record.get('created_at'))
    if raw == 'putting':
        stamp = _timestamp(progress.get('at')) or stamp
    stale = stamp is None or (at - stamp).total_seconds() > STALE_SECONDS or (stamp - at).total_seconds() > 30
    if stale and state in ('queued', 'uploading', 'processing'):
        state = 'stalled'
    result = dict(kind='frameio', project_id=record['project_id'], review_id=record['id'],
                  title='Frame.io review', state=state, terminal=state in ('blocked', 'complete'),
                  message=MESSAGES[state], updated_at=stamp.isoformat() if stamp else None, progress={})
    for done, total in [('parts_done', 'parts'), ('bytes_sent', 'bytes_total')]:
        if (_count(progress.get(done)) and _count(progress.get(total))
                and 0 <= progress[done] <= progress[total] and progress[total] > 0):
            if total == 'bytes_total' and (not _count(export.get('size')) or progress[total] != export['size']):
                continue
            result['progress'].update({done: progress[done], total: progress[total]})
    url = safe_view_url(remote.get('view_url')) if state == 'complete' else ''
    if url:
        result['view_url'] = url
    result['action'] = 'completed' if state == 'complete' else 'failed' if state == 'blocked' else 'started'
    return result


def send_from_chat(service, pid, approval):
    """Replace pre-action model claims with the host result for this exact review."""
    failed = dict(kind='frameio', project_id=pid if safe_id(pid) else '', review_id='', title='Frame.io review',
                  action='failed', state='blocked', terminal=True, progress={}, updated_at=None,
                  message='The Frame.io request could not be confirmed. Check the video review details before retrying.')
    if not isinstance(approval, dict) or not str(approval.get('said') or '').strip():
        return failed
    try:
        review = service.send(pid, approval)
    except Exception:  # stage/auth errors can contain local paths or remote response secrets
        return failed
    if not isinstance(review, dict) or not safe_id(review.get('id')) or review.get('project_id') != pid:
        return failed
    try:
        return service.status(pid, review['id'])
    except Exception:
        return dict(failed, review_id=review['id'], action='started', state='stalled', terminal=False,
                    message='The request was recorded. Its current upload status is unavailable; checking again shortly.')
