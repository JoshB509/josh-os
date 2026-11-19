"""Exact read-only Linklr preview guard. No writes or local execution allowed."""
import json,sys
from pathlib import Path
PREVIEW='mcp__linklr__preview_tracked_link'
def decision(event,policy):
    if not isinstance(event,dict) or not isinstance(policy,dict):return False
    name=event.get('tool_name')
    if name=='ToolSearch':return True
    return name==PREVIEW and isinstance(policy.get('input'),dict) and bool(policy['input']) and event.get('tool_input')==policy['input']
if __name__=='__main__':
    try:ok=decision(json.load(sys.stdin),json.loads(Path(sys.argv[1]).read_text(encoding='utf-8')))
    except Exception:ok=False
    print(json.dumps({'hookSpecificOutput':{'hookEventName':'PreToolUse','permissionDecision':'allow' if ok else 'deny','permissionDecisionReason':'Exact read-only Linklr preview only.'}}))
