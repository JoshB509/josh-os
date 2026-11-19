"""Reconcile an unlinked local email with an independently read, completed Kit broadcast.

Only get_broadcast is permitted. Chat summaries and ids are never send evidence.
The returned patch is saved through Emails.save with its original revision.
"""
import hashlib
import html
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path

from .kit import DUBAI
from .kit_draft_guard import broadcast, read_id
from .store import now

GET = 'mcp__claude_ai_Kit__get_broadcast'


def read_broadcast(root, bid):
    """Return the actual matching tool result, never the model's final response."""
    from .agent import find_claude
    claude = find_claude(root)
    if not claude:
        raise ValueError('Kit read access is unavailable.')
    with tempfile.TemporaryDirectory() as td:
        policy = Path(td) / 'kit-sent-reads.json'
        policy.write_text(json.dumps([GET, 'ToolSearch']), encoding='utf-8')
        hook = subprocess.list2cmdline([Path(sys.executable).as_posix(),
            Path(__file__).with_name('read_tools_guard.py').as_posix(), policy.as_posix()])
        settings = {'hooks': {'PreToolUse': [{'matcher': '*', 'hooks': [{'type': 'command', 'command': hook}]}]}}
        cmd = [claude, '-p', f'Use {GET} to read broadcast id {bid}. Find that tool with ToolSearch if needed. Then say done.',
               '--output-format', 'stream-json', '--verbose', '--no-session-persistence', '--tools', 'ToolSearch',
               '--allowedTools', GET + ',ToolSearch', '--permission-mode', 'dontAsk', '--settings', json.dumps(settings)]
        try:
            result = subprocess.run(cmd, cwd=str(root), capture_output=True, text=True, encoding='utf-8', timeout=180,
                env=dict(os.environ, CLAUDE_CODE_MCP_STARTUP_WAIT_MS='30000'),
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        except (OSError, subprocess.TimeoutExpired):
            raise ValueError('Kit could not be read. No local changes were saved.') from None
    return read_receipt(result.stdout, bid)


def read_receipt(stream, bid):
    calls = set()
    found = None
    for line in stream.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        for item in (event.get('message') or {}).get('content') or []:
            if not isinstance(item, dict):
                continue
            if item.get('type') == 'tool_use' and item.get('name') == GET and str(read_id(item.get('input') or {})) == str(bid):
                calls.add(item['id'])
            if item.get('type') == 'tool_result' and item.get('tool_use_id') in calls and not item.get('is_error'):
                b = broadcast(item.get('content'))
                if b and type(b.get('id')) is int and b['id'] == bid:
                    found = b
    if found is None:
        raise ValueError('Kit returned no verified broadcast read. No local changes were saved.')
    return found


def words(value):
    value = re.sub(r'<(style|script)\b[^>]*>.*?</\1>', ' ', value, flags=re.I | re.S)
    return re.findall(r'[a-z0-9]+', html.unescape(re.sub(r'<[^>]+>', ' ', value)).casefold())


def verified_link(mails, current, bid):
    if not str(bid).isdigit() or int(bid) <= 0:
        raise ValueError('Not marked sent. This email is not linked to a Kit broadcast; supply its broadcast id for verification.')
    bid = int(bid)
    b = read_broadcast(mails.root, bid)
    if type(b.get('id')) is not int or b['id'] != bid or b.get('status') != 'completed':
        raise ValueError('Not marked sent. Kit must show that exact broadcast as completed.')
    try:
        sent = datetime.fromisoformat(b['send_at'].replace('Z', '+00:00'))
        if sent.tzinfo is None:
            raise ValueError()
    except (KeyError, TypeError, ValueError, AttributeError):
        raise ValueError('Not marked sent. Kit did not return a valid send timestamp.') from None
    subject = lambda p: str(p.get('subject') or '').strip().casefold()
    if not subject(current) or subject(current) != subject(b):
        raise ValueError('Not marked sent. The Kit subject does not match this email.')
    local, remote = words(current.get('body') or ''), words(b.get('content') or '')
    similarity = SequenceMatcher(None, local, remote, autojunk=False).ratio()
    if min(len(local), len(remote)) < 40 or similarity < .90:
        raise ValueError('Not marked sent. The Kit content does not closely match this email; review the link manually.')
    for other in mails.all():
        if other['id'] != current['id'] and (subject(other) == subject(current) or (other.get('kit') or {}).get('id') == bid):
            raise ValueError('Not marked sent. Another local email has this subject or Kit link; review the match manually.')
    existing = current.get('kit') or {}
    if existing.get('id') not in (None, bid):
        raise ValueError('Not marked sent. This email already links to a different Kit broadcast.')
    checked = now()
    kit = dict(existing, id=bid, url=f'https://app.kit.com/campaigns/{bid}/draft', status='completed',
               send_at=b['send_at'], published_at=b.get('published_at'), synced_at=checked,
               reconciliation=dict(source=GET, checked_at=checked, subject=b['subject'],
                   content_sha256=hashlib.sha256(b['content'].encode()).hexdigest(), content_similarity=round(similarity, 6),
                   preview_text=b.get('preview_text')))
    local_time = sent.astimezone(DUBAI)
    return dict(kit=kit, status='Sent', planned_date=local_time.date().isoformat(), send_time=local_time.strftime('%H:%M'))


def save_failure_reply(reply, saved):
    failures = [x for x in saved if x.get('kind') == 'email' and x.get('action') == 'failed']
    if not failures:
        return reply
    # A model's pre-save success claim must not survive a failed application action.
    lines = ['The email changes were not all saved.']
    lines += [f"{x.get('title') or 'Email'}: {x.get('error') or 'Save failed.'}" for x in failures]
    succeeded = [x for x in saved if x.get('kind') == 'email' and x.get('action') in ('updated', 'added')]
    lines += [f"Saved: {x.get('title') or 'Email'}." for x in succeeded]
    return '\n\n'.join(lines)
