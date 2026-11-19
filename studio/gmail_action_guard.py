"""Fail-closed Claude PreToolUse guard for one approved Gmail action (studio/replies.py).

The runtime writes a policy file naming the single call allowed: the Gmail tool (create_draft or send_message) and the
exact input Josh approved (recipients, subject, body and the thread it belongs to). The real tool call must match it
field for field. Any other tool, any extra field (bcc, attachments, HTML, a draft id), a second call, or an unreadable
policy is denied. The tool search and structured answer tools Claude Code needs to run at all are the only others allowed.
"""
import json
import sys
from pathlib import Path

PLUMBING = {'ToolSearch', 'StructuredOutput'}


def same(a, b):
    return json.dumps(a, sort_keys=True, ensure_ascii=False) == json.dumps(b, sort_keys=True, ensure_ascii=False)


def decision(event, policy, used=False):
    """(allow, reason) for one tool call."""
    if not isinstance(event, dict) or not isinstance(policy, dict):
        return False, 'Malformed request or policy.'
    name, given = event.get('tool_name'), event.get('tool_input')
    if name in PLUMBING:
        return True, 'Claude Code plumbing.'
    write = policy.get('write') or {}
    if not name or name != write.get('tool') or not isinstance(given, dict) or not isinstance(write.get('input'), dict):
        return False, 'This run may only make the one Gmail action Josh approved.'
    if used:
        return False, 'The approved action has already been made in this run.'
    want = write['input']
    if set(given) != set(want):
        return False, 'The call has fields that were not approved: ' + ', '.join(sorted(set(given) ^ set(want)))
    for k, v in want.items():
        if not same(given[k], v):
            return False, f'"{k}" is not exactly what Josh approved.'
    return True, 'Exactly the approved action.'


def main():
    try:
        policy_file = Path(sys.argv[1])
        policy = json.loads(policy_file.read_text(encoding='utf-8'))
        event = json.load(sys.stdin)
        marker = policy_file.with_suffix('.used')
        ok, why = decision(event, policy, used=marker.exists())
        if ok and event.get('tool_name') == (policy.get('write') or {}).get('tool'):
            marker.write_text('1', encoding='utf-8')
    except Exception:
        ok, why = False, 'The guard could not read its policy.'
    print(json.dumps({'hookSpecificOutput': {'hookEventName': 'PreToolUse', 'permissionDecision': 'allow' if ok else 'deny',
                                             'permissionDecisionReason': why}}))


if __name__ == '__main__':
    main()
