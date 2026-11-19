"""Deliberate owner-confirmed recovery, never an automatic retry of an uncertain write.

Only a trusted host supplies authenticated user-message evidence and raw read-only
connector receipts. The original attempt is kept unchanged and linked to the new one.
"""
import json
import uuid
from datetime import datetime, timezone

from .kit_drafts import KIT
from .store import now


def _page(value):
    if isinstance(value, str):
        try:
            return _page(json.loads(value))
        except ValueError:
            return None
    if isinstance(value, list):
        return next((p for item in value if (p := _page(item))), None)
    if isinstance(value, dict) and not value.get('isError'):
        if 'broadcasts' in value and 'pagination' in value:
            return value
        for key in ('structuredContent', 'content', 'text', 'result'):
            found = _page(value.get(key))
            if found:
                return found
    return None


def _time(value):
    stamp = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    if stamp.tzinfo is None:
        raise ValueError('Recovery evidence needs a timezone-aware timestamp.')
    return stamp


def recover(drafts, did, email, confirmation, reconciliation):
    """Make one linked approved attempt after owner absence confirmation plus a fresh complete Kit check."""
    old = drafts.get(did)
    origin = (old.get('authorization') or {}).get('origin') or {}
    given = confirmation.get('origin') or {}
    if (old['status'] != 'uncertain' or old.get('broadcast_id') or old.get('unverified_broadcast_id')
            or not origin or given.get('channel') != origin.get('channel')
            or given.get('actor_id') != origin.get('actor_id')
            or not given.get('message_id') or given.get('message_id') == origin.get('message_id')
            or confirmation.get('kind') != 'owner_reported_no_draft' or not confirmation.get('text')):
        raise ValueError('Recovery needs a separate authenticated owner absence confirmation for this exact request.')
    checked = _time(reconciliation.get('checked_at'))
    if (not _time(old['write_started_at']) < _time(confirmation.get('sent_at')) <= checked
            or not 0 <= (datetime.now(timezone.utc) - checked).total_seconds() <= 300):
        raise ValueError('Recovery requires a fresh Kit check after the owner confirmation.')
    ok, why = drafts.check(old, email)
    if not ok:
        raise ValueError(why)
    pages = [r for r in reconciliation.get('receipts', []) if r.get('tool_name') == KIT + 'list_broadcasts']
    if not pages:
        raise ValueError('Recovery requires raw complete Kit broadcast-list receipts.')
    cursor = None
    seen = set()
    wanted = old['payload']['subject'].strip().casefold()
    for index, receipt in enumerate(pages):
        args = receipt.get('tool_input') or {}
        page = _page(receipt.get('tool_response'))
        if (set(args) - {'per_page', 'after', 'session_id', 'user_goal'} or args.get('after') != cursor
                or not page or not isinstance(page.get('broadcasts'), list)):
            raise ValueError('Kit reconciliation was filtered, incomplete or out of order.')
        for b in page['broadcasts']:
            bid = b.get('id')
            if type(bid) is not int or bid in seen:
                raise ValueError('Kit reconciliation contains invalid or repeated broadcast ids.')
            seen.add(bid)
            if (str(b.get('subject', '')).strip().casefold() == wanted
                    or b.get('description') == old['payload']['description']):
                raise ValueError('A matching Kit broadcast exists; recovery will not create a duplicate.')
        pg = page['pagination']
        if pg.get('has_next_page') is not (index < len(pages) - 1):
            raise ValueError('Kit reconciliation did not cover every page.')
        cursor = pg.get('end_cursor')
        if index < len(pages) - 1 and not cursor:
            raise ValueError('Kit reconciliation has no next cursor.')
    nid = str(uuid.uuid4())
    stamp = now()
    new = dict(id=nid, email_id=old['email_id'], email_revision=old['email_revision'], payload=old['payload'],
               hash=old['hash'], broadcast_id=None, status='approved', proposed_by='owner-confirmed-recovery',
               approved_by=origin['actor_id'], approved_at=stamp, created_at=stamp, updated_at=stamp,
               authorization=dict(old['authorization'], proposal_id=nid, recovery_of=old['id'], owner_confirmation=confirmation),
               recovery_of=old['id'],
               recovery_evidence=dict(owner_confirmation=confirmation, reconciliation=reconciliation,
                                      basis='Owner reported no draft and authorized continuation after a complete Kit check; '
                                            'this does not prove the original write never executed.'))
    with drafts.store.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        rows = [json.loads(r['body']) for r in c.execute('SELECT body FROM kit_drafts WHERE email_id=?', (old['email_id'],))]
        if next((r for r in rows if r['id'] == did), None) != old:
            raise ValueError('The original attempt changed during recovery.')
        if any(r.get('recovery_of') == did or (r['id'] != did and r['status'] in ('writing', 'uncertain', 'drafted')) for r in rows):
            raise ValueError('Another recovery or Kit write already exists; do not duplicate it.')
        from .emails import normalize
        saved = c.execute('SELECT body FROM email_drafts WHERE id=?', (email['id'],)).fetchone()
        if not saved or normalize(json.loads(saved['body'])) != normalize(dict(email)):
            raise ValueError('The saved email changed during recovery.')
        c.execute('INSERT INTO kit_drafts VALUES(?,?,?)', (nid, old['email_id'], json.dumps(new)))
    return new


def confirm_created(drafts, did, email, readback):
    """Reconcile a known create from durable hook evidence and an exact fresh read; never writes to Kit."""
    from .kit_draft_guard import broadcast, private_draft, read_id
    from .kit_drafts import CREATE, GET
    d = drafts.get(did)
    if d['status'] != 'uncertain':
        raise ValueError('Only an uncertain recorded attempt needs reconciliation.')
    ok, why = drafts.check(d, email)
    if not ok:
        raise ValueError(why)
    audit_file = drafts.root / 'Data' / 'kit-draft-audit' / (str(uuid.UUID(did)) + '.jsonl')
    try:
        events = [json.loads(line) for line in audit_file.read_text(encoding='utf-8').splitlines()]
    except (OSError, ValueError):
        raise ValueError('No durable raw create evidence exists for this attempt.') from None
    allowed = [e for e in events if e.get('event') == 'PreToolUse' and e.get('allow') is True
               and e.get('tool_name') == CREATE and e.get('tool_input') == d['payload'] and e.get('reserved') is True]
    if len(allowed) != 1:
        raise ValueError('The audit must prove exactly one approved create.')
    replies = [e for e in events if e.get('event') == 'PostToolUse' and e.get('tool_name') == CREATE
               and e.get('run_id') == allowed[0]['run_id']]
    created = broadcast(replies[0].get('tool_response')) if len(replies) == 1 else None
    bid = created.get('id') if created else None
    if type(bid) is not int or bid <= 0:
        raise ValueError('Kit did not return a raw created broadcast id.')
    checked = _time(readback.get('checked_at'))
    if not _time(allowed[0]['at']) <= checked or not 0 <= (datetime.now(timezone.utc)-checked).total_seconds() <= 300:
        raise ValueError('Read this broadcast again before reconciling it.')
    reads = [r for r in readback.get('_kit_receipts', []) if r.get('tool_name') == GET
             and read_id(r.get('tool_input') or {}) == bid]
    b = broadcast(reads[-1].get('tool_response')) if reads else None
    if (not private_draft(b) or b.get('id') != bid
            or any(b.get(k) != d['payload'][k] for k in ('subject','preview_text','content'))
            or [{k:v for k,v in group.items() if v} for group in b.get('subscriber_filter', [])]
                != d['payload']['subscriber_filter']):
        raise ValueError('The raw Kit readback is not exactly the approved private unsent draft.')
    before = dict(d)
    d.update(status='drafted', broadcast_id=bid, confirm_url=f'https://app.kit.com/campaigns/{bid}/draft',
             drafted_at=allowed[0]['at'], verified_at=now(), verified_status='draft', verified_send_at=None,
             verified_public=False, verification_evidence=readback,
             reconciliation=dict(previous_status=before['status'], previous_error=before.get('error'),
                                 create_run_id=allowed[0]['run_id'], audit_path=str(audit_file),
                                 basis='Durable create hook evidence and exact fresh UTF-8 Kit readback'))
    d.pop('error', None)
    with drafts.store.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        row = c.execute('SELECT body FROM kit_drafts WHERE id=?',(did,)).fetchone()
        if not row or json.loads(row['body']) != before:
            raise ValueError('The attempt changed during readback reconciliation.')
        d['updated_at'] = now()
        c.execute('UPDATE kit_drafts SET body=? WHERE id=?',(json.dumps(d),did))
    return d
