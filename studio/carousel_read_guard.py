"""Fail-closed PreToolUse guard for the daily Studio carousel read (studio/carousel_counts.py).

The run may use only the named Chrome tools, may open only the one Studio address, may run only the exact page scripts,
and may use only Josh's own Chrome. Anything else, or anything it can't check, is denied.
"""
import json
import sys


def allowed(event, policy):
    if not isinstance(event, dict) or not isinstance(policy, dict):
        return False
    name, args = event.get('tool_name'), event.get('tool_input') or {}
    if name not in policy.get('tools', []):
        return False
    short = name.rsplit('__', 1)[-1]
    if short == 'navigate':
        return args.get('url') == policy['url']
    if short == 'javascript_tool':
        return args.get('text') in policy['scripts'] and args.get('action', 'javascript_exec') == 'javascript_exec'
    if short == 'select_browser':
        return args.get('deviceId') == policy['device']
    return short in ('tabs_context_mcp', 'tabs_create_mcp', 'tabs_close_mcp')


def decision(event, policy):
    ok = allowed(event, policy)
    return {'hookSpecificOutput': {'hookEventName': 'PreToolUse', 'permissionDecision': 'allow' if ok else 'deny',
                                   'permissionDecisionReason': 'The Studio carousel read.' if ok else 'This read may not do that.'}}


def main():
    try:
        with open(sys.argv[1], encoding='utf-8') as f:
            policy = json.load(f)
        result = decision(json.load(sys.stdin), policy)
    except Exception:  # noqa: BLE001 - anything unexpected is denied
        result = decision({}, {})
    print(json.dumps(result))


if __name__ == '__main__':
    main()
