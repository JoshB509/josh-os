"""Session-only exact PNG Read admission for the supported file_upload call."""
import hashlib,json,sys
from pathlib import Path
if __package__:
    from .kit_chrome_gate import decide,record_receipt,checked_policy
else:
    from kit_chrome_gate import decide,record_receipt,checked_policy

def allowed_read(folder,event):
    if event.get('tool_name')!='Read':return False
    args=event.get('tool_input') or {}
    if not isinstance(args.get('file_path'),str):return False
    file=Path(args['file_path']);resolved=file.resolve(strict=True)
    policy=checked_policy(folder)
    if policy['tool']!='mcp__claude-in-chrome__file_upload' or str(resolved) not in policy['args'].get('paths',[]):return False
    manifest=json.loads((folder/'admitted-pngs.json').read_text(encoding='utf8'))
    expected=manifest.get(str(resolved))
    return bool(expected and file.is_absolute() and not file.is_symlink() and resolved.is_file()
                and resolved.stat().st_nlink==1 and resolved.suffix=='.png'
                and hashlib.sha256(resolved.read_bytes()).hexdigest()==expected)

def main():
    folder=Path(sys.argv[1])
    try:
        event=json.load(sys.stdin)
        if event.get('hook_event_name')=='PostToolUse':
            record_receipt(folder,event);print('{}');return
        permitted=allowed_read(folder,event) if event.get('tool_name')=='Read' else decide(folder,event)
    except Exception:permitted=False
    print(json.dumps({'hookSpecificOutput':{'hookEventName':'PreToolUse','permissionDecision':'allow' if permitted else 'deny',
        'permissionDecisionReason':'Only exact approved PNG reads during the bound upload, or its single host-bound browser call.'}}))

if __name__=='__main__':main()
