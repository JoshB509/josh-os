"""Fail-closed Claude PreToolUse guard for read-only agent runs.

The runtime writes the exact permitted tool names to a temporary JSON file. This
hook checks the actual tool name, not model text or a guessed list of write tools.
"""
import json
import sys


def decision(event, allowed):
    permitted = isinstance(event, dict) and isinstance(allowed, list) and event.get('tool_name') in allowed
    return {'hookSpecificOutput': {'hookEventName': 'PreToolUse',
        'permissionDecision': 'allow' if permitted else 'deny',
        'permissionDecisionReason': 'Named read-only capability.' if permitted else 'This read-only run does not permit that tool.'}}


def main():
    try:
        with open(sys.argv[1], encoding='utf-8') as file:
            allowed = json.load(file)
        result = decision(json.load(sys.stdin), allowed)
    except Exception:
        result = decision({}, [])
    print(json.dumps(result))


if __name__ == '__main__':
    main()
