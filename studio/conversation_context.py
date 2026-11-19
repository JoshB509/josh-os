"""Read-only conversational context. Never used for action approval or delivery proof."""
import json
import sqlite3

MAX_MESSAGES = 40
MAX_CHARS = 32000


def origin(data):
    if data.get('via') != 'discord':
        return {'conversation': 'joshos:desk'}
    d = data.get('discord') or {}
    from .approval_lineage import conversation
    return dict(conversation=conversation(d) or 'discord:unknown',
                request_message_id=str(d.get('message_id') or ''),
                reply_to=str(d.get('reply_to') or ''),
                author_id=str(d.get('author_id') or ''),
                channel_id=str(d.get('channel_id') or ''))


def recent(store, agent_id, history, discord=None):
    # The shared desk remains the source of chat rows. Do not borrow another agent's context.
    rows = [dict(m) for m in history[-MAX_MESSAGES:]]
    known_text = {m.get('text') for m in rows if m.get('role') == 'agent'}
    batches = set()
    notices = []
    try:
        with store.connect() as c:
            raw = c.execute('SELECT body FROM notices ORDER BY rowid DESC LIMIT 400').fetchall()
        for row in raw:
            try:
                n = json.loads(row['body'])
            except (TypeError, ValueError):
                continue
            if n.get('agent_id') != agent_id or n.get('state') != 'sent':
                continue
            text = n.get('sent_text') or n.get('text') or ''
            ids = tuple(str(x) for x in n.get('message_ids') or [])
            key = n.get('batch') or ids or n.get('key')
            if not text or key in batches or text in known_text:
                continue
            batches.add(key)
            notices.append(dict(id='notice:' + str(n.get('key')), role='agent', text=text,
                                at=n.get('sent_at') or n.get('updated_at') or '',
                                via='discord', conversation='discord:proactive-dm',
                                context_notice=True, discord_message_ids=list(ids),
                                banter=n.get('kind') == 'banter'))
            if len(notices) >= 12:
                break
    except sqlite3.OperationalError as exc:
        if 'no such table' not in str(exc):
            raise
    # Never manufacture delivery/approval from context. A referenced notice can be retained
    # for discussion even if newer conversation rows consume the ordinary budget.
    ref = str((discord or {}).get('reply_to') or '')
    pointed = [m for m in notices if ref and ref in m.get('discord_message_ids', [])]
    rows += notices
    rows.sort(key=lambda m: str(m.get('at') or ''))
    chosen, size = [], 0
    for m in reversed(rows):
        m['text'] = str(m.get('text') or '')
        if len(m['text']) > 6000:
            m['text'] = m['text'][:6000] + ' [context excerpt truncated; retrieve original before relying on exact wording]'
        if size + len(m['text']) > MAX_CHARS:
            continue
        chosen.append(m); size += len(m['text'])
    chosen.reverse()
    if pointed and not any(m.get('id') == pointed[0]['id'] for m in chosen):
        m = dict(pointed[0]); m['text'] = m['text'][:6000]
        chosen.insert(0, m)
        while sum(len(x['text']) for x in chosen) > MAX_CHARS and len(chosen) > 1:
            chosen.pop(1)
    return chosen
