"""Laia's Kit drafts: prepare a draft broadcast in Kit with an exact subject, content and audience that Josh approved.

What this allows, and nothing more:
- Laia proposes a draft for one email in the planner, with its audience rules (Kit tags or segments). Josh OS builds
  the exact payload from the saved email: subject, preview text, the body as HTML, the audience, public set to false.
- Josh approves that exact payload, by its hash, for that exact email revision. Any later edit to the email, or a
  different audience, makes the approval stale. Approving again is the only way back.
- Josh OS then makes one guarded Kit call through the Claude Kit connector (create_broadcast, or update_broadcast for a
  draft Josh OS itself made). kit_draft_guard.py checks the real call against the approved payload, field by field.

What it never does: set a date, schedule, send, publish to the newsletter site, touch a broadcast that is scheduled,
sending or sent, or touch one Josh OS didn't create. The connector itself has no date or send field; scheduling and
sending happen only in Kit's own app, by Josh.

Persistent writing is off until "kit_drafts_live": true. A trusted host can also pass the exact authorization from
an authenticated explicit draft request for a single approved proposal, without changing persistent settings.
"""
import hashlib
import json
import uuid
from pathlib import Path

from .kit import body_html, content_hash
from .kit_draft_guard import broadcast, private_draft, read_id
from .store import now

KIT = 'mcp__claude_ai_Kit__'
CREATE, UPDATE, GET = KIT + 'create_broadcast', KIT + 'update_broadcast', KIT + 'get_broadcast'
# Every Kit connector tool that writes. Team chats can never use these; only the guarded draft run can use the first two.
KIT_WRITES = tuple(KIT + t for t in (
    'create_broadcast', 'update_broadcast', 'delete_broadcast', 'create_sequence', 'update_sequence', 'delete_sequence',
    'create_sequence_email', 'update_sequence_email', 'delete_sequence_email', 'create_subscriber', 'update_subscriber',
    'bulk_create_subscribers', 'bulk_update_subscriber_custom_field_values', 'unsubscribe', 'tag_subscriber',
    'bulk_tag_subscribers', 'remove_tag_from_subscriber', 'bulk_remove_tags_from_subscribers', 'create_tag', 'bulk_create_tags',
    'bulk_delete_tags', 'update_tag_name', 'add_subscriber_to_form', 'bulk_add_subscribers_to_forms', 'add_subscriber_to_sequence',
    'create_custom_field', 'bulk_create_custom_fields', 'update_custom_field', 'delete_custom_field', 'create_webhook',
    'delete_webhook', 'create_landing_page', 'update_landing_page', 'create_product', 'update_product', 'create_snippet',
    'update_snippet', 'update_colors'))
PROTECTED = ('scheduled', 'sending', 'completed', 'cancelled', 'aborted')


def audience(rule):
    """Kit's subscriber_filter from Laia's audience rules: one group (all, any or none) of tag or segment ids."""
    if not isinstance(rule, dict) or len([k for k in ('all', 'any', 'none') if rule.get(k)]) != 1 or set(rule) - {'all', 'any', 'none'}:
        raise ValueError('Give the audience as one group, all, any or none, of Kit tags or segments.')
    key = next(k for k in ('all', 'any', 'none') if rule.get(k))
    entries = []
    if not isinstance(rule[key], list) or not 1 <= len(rule[key]) <= 20:
        raise ValueError('Audience must contain between one and twenty rules in one group.')
    for e in rule[key]:
        if not isinstance(e, dict) or e.get('type') not in ('tag', 'segment') or set(e) - {'type', 'ids'}:
            raise ValueError('Each audience rule is a Kit tag or segment with its ids.')
        ids = e.get('ids')
        if not isinstance(ids, list) or not ids or len(ids) > 50 or any(type(i) is not int or i <= 0 for i in ids):
            raise ValueError('Audience ids must be Kit tag or segment numbers.')
        entries.append(dict(type=e['type'], ids=ids))
    return [{key: entries}]


def payload(email, rule):
    """The exact create_broadcast input, built from the saved email. No date, no send, never public."""
    if not (email.get('subject') or '').strip():
        raise ValueError('The email needs a subject first.')
    return dict(subject=email['subject'], preview_text=email.get('preview') or '', content=body_html(email),
                subscriber_filter=audience(rule), public=False, description=f"Josh OS draft: {email['subject']}"[:200])


def digest(p):
    return hashlib.sha256(json.dumps(p, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()[:16]


class KitDrafts:
    def __init__(self, store, root):
        self.store, self.root = store, Path(root)
        with store.connect() as c:
            c.execute('CREATE TABLE IF NOT EXISTS kit_drafts(id TEXT PRIMARY KEY,email_id TEXT NOT NULL,body TEXT NOT NULL)')

    def live(self):
        try:
            return json.loads((self.root / 'settings.json').read_text(encoding='utf-8-sig')).get('kit_drafts_live') is True
        except (OSError, ValueError):
            return False

    def _save(self, d):
        d['updated_at'] = now()
        with self.store.connect() as c:
            c.execute('INSERT OR REPLACE INTO kit_drafts VALUES(?,?,?)', (d['id'], d['email_id'], json.dumps(d)))
        return d

    def get(self, did):
        with self.store.connect() as c:
            row = c.execute('SELECT body FROM kit_drafts WHERE id=?', (did,)).fetchone()
        if not row:
            raise KeyError('That Kit draft proposal is no longer in Josh OS.')
        return json.loads(row['body'])

    def for_email(self, eid):
        with self.store.connect() as c:
            rows = [json.loads(r['body']) for r in c.execute('SELECT body FROM kit_drafts WHERE email_id=?', (eid,))]
        return sorted(rows, key=lambda d: d['created_at'])

    def made_by_us(self, eid):
        """The broadcast Josh OS created for this email, if any."""
        done = [d for d in self.for_email(eid) if d.get('broadcast_id') and d['status'] == 'drafted']
        return done[-1]['broadcast_id'] if done else None

    def protected(self, email):
        k = email.get('kit') or {}
        return email.get('status') == 'Sent' or k.get('status') in PROTECTED

    def propose(self, email, rule, by='laia', *, baseline=None, content_only=False):
        from .kit_visual_insertions import assert_api_update_allowed
        with self.store.connect() as c:
            assert_api_update_allowed(c,email['id'],(email.get('kit') or {}).get('id') or self.made_by_us(email['id']))
        if email.get('archived'):
            raise ValueError('That email is archived.')
        if self.protected(email):
            raise ValueError('That email is already scheduled or sent in Kit, so Josh OS leaves it alone.')
        if (email.get('kit') or {}).get('id') not in (None, self.made_by_us(email['id'])):
            raise ValueError('This email is linked to a broadcast Josh OS does not own. Review it in Kit first.')
        p = payload(email, rule)
        rows = self.for_email(email['id'])
        resolved = {r.get('recovery_of') for r in rows if r['status'] == 'drafted'}
        own = self.made_by_us(email['id'])
        if content_only and (not own or not private_draft(baseline) or baseline.get('id') != own
                             or any(baseline.get(k) != p[k] for k in ('subject','preview_text','subscriber_filter'))):
            raise ValueError('A content-only edit needs an exact private-draft baseline with unchanged subject, preview and audience.')
        for old in rows:
            if old['status'] == 'writing' or (old['status'] == 'uncertain' and old['id'] not in resolved):
                raise ValueError('A prior Kit write is in progress or uncertain. Check Kit before preparing another draft.')
            if old['status'] in ('waiting', 'approved'):
                old['status'] = 'replaced'
                self._save(old)
        d = dict(id=str(uuid.uuid4()), email_id=email['id'], email_revision=email['revision'], email_content_hash=content_hash(email), payload=p, hash=digest(p),
                 broadcast_id=self.made_by_us(email['id']), status='waiting', proposed_by=by, created_at=now())
        if content_only:
            d.update(content_only=True, baseline=baseline, baseline_hash=digest(baseline))
        return self._save(d)

    def check(self, d, email):
        """Whether an approval still matches the email exactly. Any edit since makes it stale."""
        from .kit_visual_insertions import assert_api_update_allowed
        try:
            with self.store.connect() as c:assert_api_update_allowed(c,d['email_id'],d.get('broadcast_id'))
        except ValueError as exc:
            return False,str(exc)
        if email.get('id') != d['email_id']:
            return False, 'That approval belongs to a different email.'
        if digest(d['payload']) != d['hash']:
            return False, 'The approved payload changed, so it needs approving again.'
        if d.get('content_only') and digest(d.get('baseline')) != d.get('baseline_hash'):
            return False, 'The reviewed live draft baseline changed.'
        if d.get('broadcast_id') != self.made_by_us(email['id']):
            return False, 'The target broadcast is not the draft Josh OS created for this email.'
        if d.get('email_content_hash') and content_hash(email) != d['email_content_hash']:
            return False, 'The email content or visuals changed after review, so it needs approving again.'
        if email.get('revision') != d['email_revision']:
            return False, 'The email changed after it was approved, so it needs approving again.'
        try:
            again = payload(email, {k: v for k, v in d['payload']['subscriber_filter'][0].items()})
        except ValueError as e:
            return False, str(e)
        if digest(again) != d['hash']:
            return False, 'The draft no longer matches what was approved, so it needs approving again.'
        if self.protected(email):
            return False, 'That email is already scheduled or sent in Kit, so Josh OS leaves it alone.'
        return True, ''

    def approve(self, did, given_hash, email, by='josh', origin=None):
        d = self.get(did)
        if d['status'] != 'waiting':
            raise ValueError(f"This proposal is {d['status']}, so it can't be approved.")
        if given_hash != d['hash']:
            raise ValueError('That approval was for a different version of the draft. Look at it again and approve that one.')
        ok, why = self.check(d, email)
        if not ok:
            d['status'] = 'stale'
            self._save(d)
            raise ValueError(why)
        d.update(status='approved', approved_by=by, approved_at=now())
        if origin is not None:
            # Supplied only by a trusted authenticated host, never inferred from model prose or a latest draft.
            if (not isinstance(origin, dict) or set(origin) != {'channel', 'message_id', 'actor_id'}
                    or any(not isinstance(v, str) or not v.strip() for v in origin.values())):
                raise ValueError('An explicit request needs its authenticated actor, channel and message id.')
            d['authorization'] = dict(origin=origin, action='unsent_private_draft', email_id=d['email_id'],
                                      email_revision=d['email_revision'], proposal_id=d['id'], hash=d['hash'],
                                      broadcast_id=d.get('broadcast_id'))
            if d.get('content_only'):
                d['authorization'].update(action='edit_unsent_private_draft_content', baseline_hash=d['baseline_hash'])
        return self._save(d)

    def guard_policy(self, d):
        """What the guarded run may do: read the broadcast, and make exactly this one write."""
        meta = dict(kind='kit_draft', audit_id=d['id'], authorization=d.get('authorization'),
                    reference_input={'subject': 'JoshOS approved draft reference ' + uuid.uuid4().hex})
        if d.get('broadcast_id'):
            meta['reference_input']['broadcast_id'] = d['broadcast_id']
            if d.get('content_only'):
                return dict(meta, reads=[GET], expected_before=d['baseline'],
                            write=dict(tool=UPDATE, input=dict(broadcast_id=d['broadcast_id'],
                                       content=d['payload']['content'], public=False)))
            return dict(meta, reads=[GET], write=dict(tool=UPDATE, input=dict(d['payload'], broadcast_id=d['broadcast_id'])))
        return dict(meta, reads=[GET], write=dict(tool=CREATE, input=dict(d['payload'])))

    def write(self, did, email, run, request=None):
        """Make the approved draft in Kit through one guarded call. `run` is Agents.run."""
        d = self.get(did)
        if d['status'] != 'approved':
            raise ValueError('Josh has to approve this exact draft first.')
        ok, why = self.check(d, email)
        if not ok:
            d['status'] = 'stale'
            self._save(d)
            raise ValueError(why)
        if request is not None and (not d.get('authorization') or request != d['authorization']):
            raise ValueError('The explicit request does not match this exact draft, action and originating message.')
        if not self.live() and request is None:
            raise ValueError("Writing drafts to Kit isn't switched on yet, so nothing was sent to Kit. The approval is kept.")
        policy = self.guard_policy(d)
        tool = policy['write']['tool']
        system = '\n'.join([
            'You make exactly one call to save a draft email in Kit, with the exact input given. Do not change any value,',
            'and do not add any field. Never set a date, schedule, send or publish anything.',
            *(['First call get_broadcast for this broadcast id. If its status is not "draft", do not update it and say so.']
              if tool == UPDATE else []),
            'The application hook resolves the following opaque reference to the exact approved payload. '
            'Do not transcribe the email body or add fields; the hook checks the resolved payload before execution.',
            f'Then call {tool.rsplit("__", 1)[1]} once with this input, exactly:', json.dumps(policy['reference_input']),
            'After the one write, call get_broadcast for the returned broadcast id to verify its complete saved state. '
            'Never retry the write. Report the broadcast id and confirm_url the tool returned, or the exact error.'])
        schema = {'type': 'object', 'required': ['reply'], 'properties': {
            'reply': {'type': 'string'}, 'broadcast_id': {'type': ['integer', 'null']}, 'confirm_url': {'type': ['string', 'null']},
            'status': {'type': ['string', 'null']}, 'error': {'type': ['string', 'null']}}}
        # Reserve once across simultaneous requests/processes before any network call.
        with self.store.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            from .kit_visual_insertions import assert_api_update_allowed
            assert_api_update_allowed(c,d['email_id'],d.get('broadcast_id'))
            current = json.loads(c.execute('SELECT body FROM kit_drafts WHERE id=?', (did,)).fetchone()['body'])
            if current != d or current['status'] != 'approved':
                raise ValueError('This draft changed or its write has already started.')
            saved_email = c.execute('SELECT body FROM email_drafts WHERE id=?', (d['email_id'],)).fetchone()
            from .emails import normalize
            if not saved_email or normalize(json.loads(saved_email['body'])) != normalize(dict(email)):
                raise ValueError('The saved email changed. Review it again before writing to Kit.')
            d.update(status='writing', write_started_at=now(), updated_at=now())
            c.execute('UPDATE kit_drafts SET body=? WHERE id=?', (json.dumps(d), did))
        try:
            _, answer = run(system, 'Save the approved draft.', schema, extra_tools=tuple(policy['reads']) + (tool,), full=True,
                            guard=policy, timeout=180)
        except Exception as e:  # noqa: BLE001 - Kit may or may not have the draft now, so never retry on our own
            d.update(status='uncertain', error=str(e)[:300])
            self._save(d)
            raise ValueError("I'm not sure whether Kit saved the draft, so I won't try again on my own. Check Kit's drafts.") from None
        receipts = answer.get('_kit_receipts', []) if isinstance(answer, dict) else []
        writes = [r for r in receipts if r.get('tool_name') == tool and
                  {k: v for k, v in (r.get('tool_input') or {}).items() if k not in ('session_id', 'user_goal')}
                  == policy['write']['input']]
        result = broadcast(writes[0].get('tool_response')) if len(writes) == 1 else None
        bid = result.get('id') if result else None
        reads = [broadcast(r.get('tool_response')) for r in receipts[receipts.index(writes[0]) + 1:]
                 if r.get('tool_name') == GET and read_id(r.get('tool_input') or {}) == bid] if writes else []
        verified = reads[-1] if reads else None
        matches = (type(bid) is int and bid > 0 and private_draft(verified) and verified.get('id') == bid
                   and (not d.get('broadcast_id') or bid == d['broadcast_id'])
                   and all(verified.get(k) == d['payload'][k] for k in ('subject', 'preview_text', 'content'))
                   and [{k: v for k, v in g.items() if v} for g in verified.get('subscriber_filter', [])]
                       == d['payload']['subscriber_filter']
                   and (not d.get('content_only') or all(verified.get(k) == v for k,v in d['baseline'].items() if k != 'content')))
        if not matches:
            d.update(status='uncertain', error=str((answer or {}).get('error') or (answer or {}).get('reply') or 'No broadcast id came back.')[:300])
            if type(bid) is int:
                d['unverified_broadcast_id'] = bid
            self._save(d)
            raise ValueError("Kit didn't confirm the draft, so I won't try again on my own. Check Kit's drafts.")
        d.update(status='drafted', broadcast_id=bid, confirm_url=f'https://app.kit.com/campaigns/{bid}/draft',
                 drafted_at=now(), verified_at=now(), verified_status='draft', verified_send_at=None, verified_public=False,
                 verified_subscriber_filter=[{k:v for k,v in g.items() if v} for g in verified['subscriber_filter']])
        return self._save(d)
