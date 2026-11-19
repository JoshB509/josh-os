"""Sam's local release drafts. No approval, publish, upload, schedule or send."""
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from .store import Conflict

SCHEMA = {'type':'object','additionalProperties':False,'properties':{
    'action':{'type':'string','enum':['draft_packaging','chapters','publish_time']},
    'project_id':{'type':'string'},'revision':{'type':'integer'},
    'time':{'type':'string'},'timezone':{'type':'string'}},
    'required':['action','project_id','revision'],
    'description':'Local release drafts only. chapters preserves preview and export-approval gates. publish_time stores only a local planned time, never schedules YouTube.'}

class ChatReleaseActions:
    def __init__(self, release): self.release=release
    def context(self,pid):
        p=self.release.store.get(pid)
        return dict(project_id=pid,revision=p['revision'],name=p['name'],local_only=True)
    def dispatch(self,agent_id,args,*,project_id,message):
        if agent_id!='sam':raise ValueError('Only Sam handles release drafting.')
        if not isinstance(args,dict) or set(args)-set(SCHEMA['properties']):raise ValueError('Unknown release action fields.')
        if any(k not in args for k in SCHEMA['required']):raise ValueError('Exact project and revision required.')
        if not project_id or args['project_id']!=project_id:raise ValueError('Name one exact video for this release draft.')
        p=self.release.store.get(project_id)
        if type(args['revision']) is not int or args['revision']!=p['revision']:raise Conflict('The video changed. Read it again.')
        if not isinstance(message,str) or re.search(r"\b(?:do not|don't|never|cancel|stop)\b",message,re.I):raise ValueError('A current direct request is required.')
        action=args['action']
        if action=='draft_packaging':
            if not re.search(r'\b(?:draft|prepare|write|generate|regenerate|make)\b.*\b(?:description|cta|packaging)\b',message,re.I|re.S):raise ValueError('Ask to draft the description or packaging.')
            self.release.draft_packaging(project_id,expected_revision=args['revision'])
            note='Description and CTA drafts saved locally for review. Nothing uploaded or scheduled.'
        elif action=='chapters':
            if not re.search(r'\b(?:draft|prepare|write|generate|regenerate|make)\b.*\bchapters\b',message,re.I|re.S):raise ValueError('Ask to prepare the chapters.')
            prep=self.release.chapters.prepare(project_id,preview=True,expected_revision=args['revision'])
            note=('Chapter preview' if prep.get('preview') else 'Chapter draft')+' saved locally for review. Nothing approved or published.'
        elif action=='publish_time':
            tm,tz=args.get('time'),args.get('timezone')
            if not isinstance(tm,str) or not isinstance(tz,str):raise ValueError('Exact time and IANA time zone required.')
            try:ZoneInfo(tz)
            except (ValueError,ZoneInfoNotFoundError):raise ValueError('Use an IANA time zone such as Asia/Dubai.') from None
            if not re.search(r'\b(?:set|save|change)\b.*\b(?:local|planned|plan|publish time)\b',message,re.I|re.S) or tm not in message or tz not in message:raise ValueError('State the local planned time and exact time zone.')
            self.release.set_publish_time(project_id,tm,tz,by='sam (owner request)',expected_revision=args['revision'])
            note='Local planned release time saved. Nothing scheduled on YouTube.'
        else:raise ValueError('Unknown release action.')
        out=self.release.store.get(project_id)
        return dict(kind='release',id=project_id,action=action,revision=out['revision'],note=note)
