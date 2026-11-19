"""Fail-closed Claude PreToolUse guard for one approved Kit draft write.

The runtime writes a policy file naming the read tools allowed and the single write allowed: the tool name and the
exact input Josh approved (subject, preview text, content, audience, public set to false, and for an update the
broadcast id). This hook compares the real tool call against it. Anything else, any extra field (such as a date or
send time), a second write, or an unreadable policy is denied.
"""
import json
import os
import sys
from pathlib import Path
from datetime import datetime, timezone

HARMLESS = {'session_id', 'user_goal'}  # analytics fields the Kit connector accepts and ignores
GET = 'mcp__claude_ai_Kit__get_broadcast'


def broadcast(response):
    """Decode the raw MCP response, never a model's summary of it."""
    if isinstance(response, str):
        try:
            return broadcast(json.loads(response))
        except ValueError:
            return None
    if isinstance(response, list):
        for item in response:
            found = broadcast(item)
            if found:
                return found
    if isinstance(response, dict) and not response.get('isError'):
        if isinstance(response.get('broadcast'), dict):
            return response['broadcast']
        if type(response.get('id')) is int and 'status' in response:
            return response
        for key in ('structuredContent', 'content', 'text', 'result'):
            found = broadcast(response.get(key))
            if found:
                return found
    return None


def read_id(given):
    return given.get('id', given.get('broadcast_id'))


def private_draft(b):
    return (isinstance(b, dict) and b.get('status') == 'draft' and b.get('public') is False
            and 'send_at' in b and b['send_at'] is None and not b.get('published_at') and not b.get('public_url'))


def same(a, b):
    return json.dumps(a, sort_keys=True, ensure_ascii=False) == json.dumps(b, sort_keys=True, ensure_ascii=False)


def expand_reference(event, policy):
    """Only the run's exact opaque reference can load its approved input, without model transcription."""
    reference = policy.get('reference_input')
    write = policy.get('write') or {}
    if (policy.get('kind') == 'kit_draft' and isinstance(reference, dict) and reference
            and event.get('tool_name') == write.get('tool') and same(event.get('tool_input'), reference)):
        return dict(event, tool_input=write.get('input')), write.get('input')
    return event, None


def audit(policy, record):
    """Persist actual hook evidence before allowing a write; failure to log fails closed."""
    if not policy.get('audit_path'):
        return
    entry = dict(record, run_id=policy['run_id'], at=datetime.now(timezone.utc).isoformat())
    data = (json.dumps(entry) + '\n').encode('utf-8')
    fd = os.open(policy['audit_path'], os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        if os.write(fd, data) != len(data):
            raise OSError('Incomplete Kit audit write')
        os.fsync(fd)
    finally:
        os.close(fd)


def decision(event, policy, used=False, receipts=()):
    """(allow, reason) for one tool call."""
    if not isinstance(event, dict) or not isinstance(policy, dict):
        return False, 'Malformed request or policy.'
    name, given = event.get('tool_name'), event.get('tool_input')
    if name in (policy.get('reads') or []):
        return True, 'Named read.'
    write = policy.get('write') or {}
    if name != write.get('tool') or not isinstance(given, dict) or not isinstance(write.get('input'), dict):
        return False, 'This run may only make the one approved Kit draft write.'
    if used:
        return False, 'The approved write has already been made in this run.'
    want = write['input']
    # Even a malformed policy must never grant scheduling or publication.
    if set(want) - {'subject', 'preview_text', 'content', 'subscriber_filter', 'public', 'description', 'broadcast_id'}:
        return False, 'Only unsent draft fields are allowed.'
    if name.endswith('__update_broadcast'):
        prior = [broadcast(r.get('tool_response')) for r in receipts if r.get('tool_name') == GET
                 and read_id(r.get('tool_input') or {}) == want.get('broadcast_id')]
        if not prior or not private_draft(prior[-1]) or prior[-1].get('id') != want.get('broadcast_id'):
            return False, 'Read back this private unsent draft before updating it.'
        baseline = policy.get('expected_before')
        if baseline is not None and (not isinstance(baseline, dict) or any(prior[-1].get(k) != v for k,v in baseline.items())):
            return False, 'The live draft changed after review; do not overwrite it.'
    if set(given) - HARMLESS != set(want):
        return False, 'The call has fields that were not approved: ' + ', '.join(sorted(set(given) ^ set(want) - HARMLESS))
    for k, v in want.items():
        if not same(given[k], v):
            return False, f'"{k}" is not exactly what Josh approved.'
    if given.get('public') is not False:
        return False, 'A draft must not be published to the newsletter site.'
    return True, 'Exactly the approved draft.'


def main():
    updated_input = None
    try:
        policy_file = Path(sys.argv[1])
        policy = json.loads(policy_file.read_text(encoding='utf-8'))
        # Claude emits UTF-8 JSON; Windows pipe defaults to cp1252 otherwise.
        sys.stdin.reconfigure(encoding='utf-8')
        event = json.load(sys.stdin)
        marker = policy_file.with_suffix('.used')
        receipt_file = policy_file.with_suffix('.receipts.json')
        receipts = json.loads(receipt_file.read_text(encoding='utf-8')) if receipt_file.exists() else []
        if event.get('hook_event_name') == 'PostToolUse':
            if event.get('tool_name') in tuple(policy.get('reads') or []) + (GET, (policy.get('write') or {}).get('tool')):
                receipts.append({k: event.get(k) for k in ('tool_name', 'tool_input', 'tool_response')})
                receipt_file.write_text(json.dumps(receipts), encoding='utf-8')
                audit(policy, dict(event='PostToolUse', tool_name=event.get('tool_name'),
                                   tool_input=event.get('tool_input'), tool_response=event.get('tool_response')))
            return
        received = event
        event, updated_input = expand_reference(event, policy)
        already_reserved = marker.exists()
        ok, why = decision(event, policy, used=already_reserved, receipts=receipts)
        if ok and event.get('tool_name') == (policy.get('write') or {}).get('tool'):
            try:
                fd = os.open(str(marker), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.close(fd)
            except FileExistsError:
                ok, why = False, 'The approved write has already been reserved.'
        if event.get('tool_name') == (policy.get('write') or {}).get('tool'):
            audit(policy, dict(event='PreToolUse', tool_name=event.get('tool_name'),
                               received_input=received.get('tool_input'), tool_input=event.get('tool_input'),
                               allow=ok, reason=why, already_reserved=already_reserved, reserved=marker.exists()))
    except Exception:
        ok, why = False, 'The guard could not read its policy.'
    result = {'hookEventName': 'PreToolUse', 'permissionDecision': 'allow' if ok else 'deny', 'permissionDecisionReason': why}
    if ok and updated_input is not None:
        result['updatedInput'] = updated_input
    print(json.dumps({'hookSpecificOutput': result}))


if __name__ == '__main__':
    main()
