"""Which offer Josh's "send it" answers (added 13 Nov 2026, after Anouk sent the wrong email; hardened the same day
after an independent review).

What went wrong: every conversation with Anouk (the Josh OS desk, her Discord DM with Josh, and any channel) shared one
desk thread, and "send it" approved the reply in the newest Anouk message there, whichever conversation Josh was in.

Now every message Anouk sends is recorded here BEFORE it goes out, as "sending", and only becomes approvable once every
part has reached Discord ("sent"). If the send stops part way, is unclear, or the record can't be finished, it stays
unusable and, being the newest message in that conversation, blocks every older offer there. If the record can't even be
started, the message isn't sent. Each record holds:
- the conversation: the Josh OS desk, one Discord channel, or one owner's DM (keyed by that owner, with the DM channel id
  checked on every answer), plus the owner it was written to;
- the exact offers shown, frozen when the text was composed: proposal id, revision, recipients, a digest of the body and
  the digest of each action. Replies.natural_match refuses any offer that no longer matches the reply as it is now.

"Send it" counts only for the exact message Josh replied to (its Discord reference) in the same conversation, from the
same owner, or otherwise the newest Anouk message in that same conversation, and only if that message showed a reply.
Anything else fails closed, with nothing approved and nothing retried.
"""
import hashlib
import json
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .store import now

STALE_HOURS = 24
SEND_WAIT_S = 120     # how long a message waits for the one before it in the same conversation to finish going out

DESK = 'joshos:desk'

REASONS = {
    'unknown': "I can't match the message you replied to with a reply I showed you, so nothing was sent.",
    'other_conversation': 'That reply was shown in a different conversation, so nothing was sent. Answer it where I showed it.',
    'other_owner': 'That reply was shown to someone else, so nothing was sent.',
    'none': "I haven't shown you a reply in this conversation yet, so nothing was sent.",
    'no_offer': "My last message here didn't show a reply to send, so nothing was sent. Tell me which one and I'll show it.",
    'partial': "That message didn't reach you in full, so nothing was sent. I'll show you the reply again.",
    'stale': "That reply was shown more than a day ago, so nothing was sent. I'll show you the current version.",
    'not_latest': 'I wrote to you after showing that reply, so nothing was sent. Tell me which one you mean.',
    'duplicate': 'I already handled that message, so nothing was done again.',
    'no_conversation': "I couldn't tell which conversation that came from, so nothing was sent.",
    'busy': 'I was still sending you something here, so nothing was sent. Say it again once my message arrives.',
    'arrived_after': ("Another message of mine reached you after you wrote, so I can't be sure which reply you meant. Nothing "
                      'was sent. Reply to the exact message, or say it again.'),
    'blocked': ("Something I sent in this conversation couldn't be recorded, so nothing here can be approved until I show "
                'you the reply again.'),
}


def conversation(discord=None):
    """The conversation key for one message. discord is the bridge's dict, or None for the Josh OS desk. A DM is keyed by
    its owner (each owner has their own DM with each bot), a channel by its id."""
    if discord is None:
        return DESK
    if discord.get('is_dm'):
        owner = str(discord.get('author_id') or discord.get('owner_id') or '')
        return f'discord:dm:{owner}' if owner.isdigit() else None
    cid = str(discord.get('channel_id') or '')
    return f'discord:channel:{cid}' if cid.isdigit() else None


def digest(text):
    return hashlib.sha256(' '.join(str(text or '').split()).encode('utf-8')).hexdigest()


def _ts(s):
    try:
        t = datetime.fromisoformat(str(s).replace('Z', '+00:00'))
    except (TypeError, ValueError):
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


class Lineage:
    """Also keeps blocks. If a message can't be recorded before it goes out (the database is failing), its conversation
    is blocked in a small file outside the database and in memory. While blocked, no "send it" there counts. The block
    lasts through a restart and is lifted only when a new message in that conversation is fully recorded, which means
    the reply has been shown again. The bridge sends its "couldn't record" note only once the file block is saved."""

    def __init__(self, store):
        self.store = store
        self.block_dir = Path(store.root) / 'replies' / 'lineage-blocks'
        self._blocks = {}
        # One lock per conversation. A message holds it from "sending" until it is finished, and "send it" holds it while
        # it works out which message it answers. So messages in one conversation are recorded in the order they reach
        # Discord, and "send it" never reads one that is half sent. (A proactive DM and a chat reply could otherwise
        # finish in the other order from the one they were recorded in.)
        self._locks, self._locks_guard, self._held = {}, threading.Lock(), {}
        with store.connect() as c:
            c.execute('CREATE TABLE IF NOT EXISTS outbound_messages(id TEXT PRIMARY KEY,agent_id TEXT NOT NULL,conversation TEXT NOT NULL,'
                      'body TEXT NOT NULL)')
            c.execute('CREATE TABLE IF NOT EXISTS outbound_message_ids(message_id TEXT NOT NULL,conversation TEXT NOT NULL,outbound_id TEXT NOT NULL,'
                      'PRIMARY KEY(conversation,message_id))')
            c.execute('CREATE TABLE IF NOT EXISTS inbound_events(key TEXT PRIMARY KEY,body TEXT NOT NULL)')

    # Blocks ---------------------------------------------------------------------------------------------------------
    def _block_file(self, agent_id, conv):
        return self.block_dir / (hashlib.sha256(f'{agent_id}|{conv}'.encode('utf-8')).hexdigest()[:32] + '.json')

    def block(self, agent_id, conv, reason):
        """Block approvals in one conversation. Returns True when the block is saved on disk (it then survives a
        restart). The in-memory block is set either way."""
        rec = dict(agent_id=agent_id, conversation=conv or 'unknown', reason=str(reason)[:300], at=now())
        self._blocks[(agent_id, conv)] = rec
        try:
            self.block_dir.mkdir(parents=True, exist_ok=True)
            self._block_file(agent_id, conv).write_text(json.dumps(rec), encoding='utf-8')
            return True
        except OSError:
            return False

    def blocked(self, agent_id, conv):
        """The block on this conversation, or None. A block file that can't be read counts as a block."""
        if (agent_id, conv) in self._blocks:
            return self._blocks[(agent_id, conv)]
        f = self._block_file(agent_id, conv)
        try:
            return json.loads(f.read_text(encoding='utf-8'))
        except FileNotFoundError:
            return None
        except (OSError, ValueError):
            return dict(agent_id=agent_id, conversation=conv, reason='The block file could not be read.')

    def _unblock(self, agent_id, conv):
        f = self._block_file(agent_id, conv)
        try:
            f.unlink()
        except FileNotFoundError:
            pass
        self._blocks.pop((agent_id, conv), None)

    # Ordering -------------------------------------------------------------------------------------------------------
    def _lock(self, agent_id, conv):
        with self._locks_guard:
            return self._locks.setdefault((agent_id, conv), threading.Lock())

    def release(self, rec):
        """Let the next message in this conversation go. Safe to call more than once."""
        lock = self._held.pop((rec or {}).get('id'), None)
        if lock is not None and lock.locked():
            lock.release()

    # Records --------------------------------------------------------------------------------------------------------
    def begin(self, agent_id, conv, offers, kind='chat', owner_id=None, channel_id=None, at=None):
        """Record a message as "sending" before it goes out, holding its conversation until finish(). It blocks older
        offers there from now on, and can't be approved until finish() says every part arrived. Raises if it can't be
        saved, or if another message there is still going out after SEND_WAIT_S, so the caller doesn't send."""
        if not conv:
            raise ValueError('No conversation for this message, so it is not sent.')
        lock = self._lock(agent_id, conv)
        if not lock.acquire(timeout=SEND_WAIT_S):
            raise TimeoutError('Another message in this conversation is still being sent, so this one was not sent.')
        rec = dict(id=str(uuid.uuid4()), agent_id=agent_id, conversation=conv, message_ids=[], offers=list(offers or []), kind=kind,
                   state='sending', usable=False, owner_id=str(owner_id or '') or None, channel_id=str(channel_id or '') or None,
                   at=at or now(), reason='')
        try:
            with self.store.connect() as c:
                c.execute('BEGIN IMMEDIATE')
                c.execute('INSERT INTO outbound_messages VALUES(?,?,?,?)', (rec['id'], agent_id, conv, json.dumps(rec)))
        except Exception:
            lock.release()
            raise
        self._held[rec['id']] = lock
        return rec

    def finish(self, rec, message_ids, state, channel_id=None, reason=''):
        """Close a "sending" record with what reached Discord, and let the next message go. Only state "sent" with
        message ids can be approved."""
        try:
            return self._finish(rec, message_ids, state, channel_id, reason)
        finally:
            self.release(rec)

    def _finish(self, rec, message_ids, state, channel_id=None, reason=''):
        ids = [str(m) for m in message_ids or [] if str(m)]
        with self.store.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            row = c.execute('SELECT body FROM outbound_messages WHERE id=?', (rec['id'],)).fetchone()
            cur = json.loads(row[0])
            cur.update(message_ids=ids, state=state, usable=state == 'sent' and bool(ids), reason=str(reason or '')[:200],
                       channel_id=str(channel_id or cur.get('channel_id') or '') or None, finished_at=now())
            c.execute('UPDATE outbound_messages SET body=? WHERE id=?', (json.dumps(cur), rec['id']))
            for m in ids:
                c.execute('INSERT OR REPLACE INTO outbound_message_ids VALUES(?,?,?)', (m, cur['conversation'], cur['id']))
        if cur['usable'] and self.blocked(cur['agent_id'], cur['conversation']):
            # A new message reached Josh in full and is recorded, so this conversation's newest message is known again.
            # Any reply he approves now has to be in it (or one he replies to that was recorded before).
            try:
                self._unblock(cur['agent_id'], cur['conversation'])
            except OSError:
                pass                                  # still blocked, which is the safe side
        return cur

    def record(self, agent_id, conv, message_ids, offers, kind='chat', state='sent', at=None, reason='', owner_id=None, channel_id=None):
        """begin and finish together, for a message already shown (the Josh OS desk)."""
        return self.finish(self.begin(agent_id, conv, offers, kind, owner_id, channel_id, at), message_ids, state, channel_id, reason)

    def _get(self, oid):
        with self.store.connect() as c:
            r = c.execute('SELECT body FROM outbound_messages WHERE id=?', (oid,)).fetchone()
        return json.loads(r[0]) if r else None

    def latest(self, agent_id, conv):
        with self.store.connect() as c:
            r = c.execute('SELECT body FROM outbound_messages WHERE agent_id=? AND conversation=? ORDER BY rowid DESC LIMIT 1',
                          (agent_id, conv)).fetchone()
        return json.loads(r[0]) if r else None

    def resolve(self, agent_id, conv, reply_to=None, at=None, latest_desk_id=None, author_id=None, channel_id=None, inbound_id=None):
        """The outgoing message Josh's "send it" answers, or (None, reason). Never looks outside conv.

        If a message is going out in this conversation when "send it" is handled, it fails closed straight away (13 Nov
        2026 review). Waiting for that send to finish would let "send it" bind the message that arrived after Josh wrote.
        On Discord it also checks arrival order with Discord's own ids: snowflakes carry the moment Discord created the
        message, so every part of the message it answers must have an id older than Josh's own message (inbound_id).
        A message that reached Discord after Josh wrote can never be the one he meant, however late his message is
        processed. Processing time is never used for this."""
        if not conv:
            return None, 'no_conversation'
        lock = self._lock(agent_id, conv)
        if not lock.acquire(blocking=False):
            return None, 'busy'
        try:
            return self._resolve(agent_id, conv, reply_to, at, latest_desk_id, author_id, channel_id, inbound_id)
        finally:
            lock.release()

    def _resolve(self, agent_id, conv, reply_to, at, latest_desk_id, author_id, channel_id, inbound_id=None):
        if self.blocked(agent_id, conv):
            return None, 'blocked'
        if conv.startswith('discord:'):
            if not str(inbound_id or '').isdigit():
                return None, 'no_conversation'               # without Josh's own Discord id, arrival order can't be checked
            later = self._delivered_after(agent_id, conv, int(inbound_id))
            if later and not reply_to:
                return None, 'arrived_after'                  # something reached Discord after Josh wrote, so "the latest" is unclear
        if reply_to:
            with self.store.connect() as c:
                rows = c.execute('SELECT conversation,outbound_id FROM outbound_message_ids WHERE message_id=?', (str(reply_to),)).fetchall()
            here = [r[1] for r in rows if r[0] == conv]
            if not here:
                return None, ('other_conversation' if rows else 'unknown')
            rec = self._get(here[0])
            if not rec or rec['agent_id'] != agent_id:
                return None, 'unknown'
        else:
            rec = self.latest(agent_id, conv)
            if rec is None:
                return None, 'none'
            # On the desk, the newest Anouk message on screen may have come from Discord. It must be this one.
            if conv == DESK and latest_desk_id is not None and latest_desk_id not in rec['message_ids']:
                return None, 'not_latest'
        if conv.startswith('discord:'):
            if any(not str(m).isdigit() or int(m) >= int(inbound_id) for m in rec['message_ids']):
                return None, 'arrived_after'                  # every part must have reached Discord before Josh's message
            if not author_id or str(rec.get('owner_id') or '') != str(author_id):
                return None, 'other_owner'
            if conv.startswith('discord:dm:') and (not channel_id or str(rec.get('channel_id') or '') != str(channel_id)):
                return None, 'other_conversation' if rec.get('usable') else 'partial'
        if not rec['offers']:
            return None, 'no_offer'
        if not rec['usable']:
            return None, 'partial'
        sent = _ts(rec['at'])
        if sent is None or (at or datetime.now(timezone.utc)) - sent > timedelta(hours=STALE_HOURS):
            return None, 'stale'
        return rec, None

    def _delivered_after(self, agent_id, conv, inbound):
        """Outgoing messages in this conversation with any part Discord created at or after Josh's message. A send still
        going out is caught by the lock (busy), and one that never finished is caught as the newest record (partial)."""
        with self.store.connect() as c:
            rows = [json.loads(r[0]) for r in c.execute('SELECT body FROM outbound_messages WHERE agent_id=? AND conversation=?', (agent_id, conv))]
        return [r for r in rows if any(not str(m).isdigit() or int(m) >= inbound for m in r.get('message_ids') or [])]

    def claim_inbound(self, key, detail=None):
        """True the first time an incoming message is seen, False if it was already handled (a repeated Discord event)."""
        with self.store.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            if c.execute('SELECT 1 FROM inbound_events WHERE key=?', (key,)).fetchone():
                return False
            c.execute('INSERT INTO inbound_events VALUES(?,?)', (key, json.dumps(dict(at=now(), **(detail or {})))))
        return True
