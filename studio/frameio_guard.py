"""Fail-closed Claude PreToolUse guard for Frame.io runs (studio/frameio_review.py).

The runtime writes a policy naming the read tools allowed, plus at most one write given as the exact tool input: every
field name and every value, built from Frame.io's own inspected tool schema (Data/frameio/schema.json). The real call
must have exactly those fields with exactly those values. A value in the wrong field, any extra field (a share,
publish or notify option), a missing field, a second write, or an unreadable policy is denied. Without an exact input
there is no write at all.

The one write is reserved by creating a marker file atomically (O_CREAT | O_EXCL), so two calls racing each other
can't both get through.
"""
import json
import os
import sys
from pathlib import Path

PLUMBING = {'ToolSearch', 'StructuredOutput'}


def same(a, b):
    return json.dumps(a, sort_keys=True, ensure_ascii=False) == json.dumps(b, sort_keys=True, ensure_ascii=False)


def decision(event, policy):
    """(allow, reason, is_the_write) for one tool call. The caller reserves the write."""
    if not isinstance(event, dict) or not isinstance(policy, dict):
        return False, 'Malformed request or policy.', False
    name, given = event.get('tool_name'), event.get('tool_input')
    if name in PLUMBING or name in (policy.get('reads') or []):
        return True, 'Named read.', False
    write = policy.get('write') or {}
    want = write.get('input')
    if not name or name != write.get('tool') or not isinstance(given, dict) or not isinstance(want, dict) or not want:
        return False, 'This Frame.io run may only make the one action Josh approved.', False
    if set(given) != set(want):
        return False, 'The call has fields that were not approved: ' + ', '.join(sorted(set(given) ^ set(want))), False
    for k, v in want.items():
        if not same(given[k], v):
            return False, f'"{k}" is not exactly what Josh approved.', False
    return True, 'Exactly the approved Frame.io action.', True


def reserve(marker):
    """True for the first caller only, even when two calls race."""
    try:
        fd = os.open(str(marker), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return False
    os.close(fd)
    return True


def main():
    try:
        f = Path(sys.argv[1])
        policy = json.loads(f.read_text(encoding='utf-8'))
        event = json.load(sys.stdin)
        ok, why, is_write = decision(event, policy)
        if ok and is_write and not reserve(f.with_suffix('.used')):
            ok, why = False, 'The approved Frame.io action has already been made in this run.'
    except Exception:
        ok, why = False, 'The guard could not read its policy.'
    print(json.dumps({'hookSpecificOutput': {'hookEventName': 'PreToolUse', 'permissionDecision': 'allow' if ok else 'deny',
                                             'permissionDecisionReason': why}}))


if __name__ == '__main__':
    main()
