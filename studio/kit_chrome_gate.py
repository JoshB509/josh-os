"""Immutable single-call permits and raw receipts for the dedicated Chrome worker.

Publishing, claiming, retiring and receipt completion each create a new file. No
shared policy-file replacement is needed. Actual tool_use_id binds each receipt to
its claim, so a late response cannot satisfy a later identical call.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time


def fingerprint(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()


def write_once(path,value):
    with path.open('x',encoding='utf-8') as stream:
        json.dump(value,stream,ensure_ascii=False);stream.flush();os.fsync(stream.fileno())


def valid_id(value):return isinstance(value,str) and bool(re.fullmatch(r'[A-Za-z0-9_-]{1,80}',value))


def active_policy(folder):
    candidates=[]
    for marker in folder.glob('*.ready'):
        cid=marker.name.removesuffix('.ready')
        if not valid_id(cid) or (folder/(cid+'.retired')).exists():continue
        policy=json.loads((folder/(cid+'.policy.json')).read_text(encoding='utf-8'))
        if policy.get('id')!=cid:raise ValueError('Permit identity mismatch.')
        if policy['expires']>=time.time():candidates.append(policy)
    if len(candidates)!=1:raise ValueError('Exactly one active browser permit is required.')
    return candidates[0]


def publish_policy(folder,policy):
    cid=policy.get('id')
    if not valid_id(cid):raise ValueError('Invalid permit identity.')
    if any(not (folder/(p.name.removesuffix('.ready')+'.retired')).exists() for p in folder.glob('*.ready')):
        raise ValueError('Retire the preceding browser permit before publishing another.')
    write_once(folder/(cid+'.policy.json'),policy)
    write_once(folder/(cid+'.ready'),{'policy_sha256':fingerprint(policy)})


def retire_policy(folder,cid):
    if not valid_id(cid):raise ValueError('Invalid permit identity.')
    try:write_once(folder/(cid+'.retired'),{'retired_at':time.time()})
    except FileExistsError:pass


def checked_policy(folder):
    policy=active_policy(folder);marker=json.loads((folder/(policy['id']+'.ready')).read_text(encoding='utf-8'))
    if marker.get('policy_sha256')!=fingerprint(policy):raise ValueError('A published permit changed.')
    return policy


def decide(folder,event):
    policy=checked_policy(folder);cid=policy['id'];name=event.get('tool_name');args=event.get('tool_input')
    if name=='ToolSearch':return args=={'query':'select:'+policy['tool'],'max_results':1}
    use_id=event.get('tool_use_id')
    if (name!=policy['tool'] or args!=policy['args'] or not valid_id(use_id)
            or (folder/(cid+'.retired')).exists()):return False
    try:write_once(folder/(cid+'.used'),dict(tool=name,args=args,tool_use_id=use_id,policy_sha256=fingerprint(policy)))
    except FileExistsError:return False
    return not (folder/(cid+'.retired')).exists()


def record_receipt(folder,event):
    use_id=event.get('tool_use_id')
    if not valid_id(use_id):return False
    matches=[]
    for used in folder.glob('*.used'):
        claim=json.loads(used.read_text(encoding='utf-8'))
        if claim.get('tool_use_id')==use_id:matches.append((used,claim))
    if len(matches)!=1:return False
    used,claim=matches[0];cid=used.name.removesuffix('.used')
    policy=json.loads((folder/(cid+'.policy.json')).read_text(encoding='utf-8'))
    if (claim.get('policy_sha256')!=fingerprint(policy) or event.get('tool_name')!=claim['tool']
            or event.get('tool_input')!=claim['args']):return False
    try:
        write_once(folder/(cid+'.receipt.json'),dict(policy_id=cid,tool_use_id=use_id,tool=claim['tool'],args=claim['args'],response=event.get('tool_response')))
        write_once(folder/(cid+'.receipt-complete'),{'tool_use_id':use_id})
    except FileExistsError:return False
    return True


def main():
    folder=Path(sys.argv[1])
    try:event=json.loads(sys.stdin.buffer.read().decode('utf-8'))
    except (ValueError,UnicodeError):event={}
    if not isinstance(event,dict):event={}
    if event.get('hook_event_name')=='PostToolUse':
        try:record_receipt(folder,event)
        except (ValueError,OSError,KeyError,TypeError):pass
        print('{}');return
    try:ok=decide(folder,event)
    except (ValueError,OSError,KeyError,TypeError):ok=False
    print(json.dumps({'hookSpecificOutput':{'hookEventName':'PreToolUse','permissionDecision':'allow' if ok else 'deny',
        'permissionDecisionReason':'Only the single immutable host-bound browser permit is authorized. No general Chrome access.'}}))


if __name__=='__main__':main()
