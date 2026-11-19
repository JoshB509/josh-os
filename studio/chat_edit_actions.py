"""Bounded Tariq local chat adapter. Never approves, publishes or deletes media.
Host supplies trusted role, project, current owner text and authorization policy.
"""
import hashlib
import json
import threading
import re
from pathlib import Path

ACTIONS = ('inspect','select_broll','register_sources','cancel_analysis','remove_words','restore_words','undo_words','cleanup_proposals','clean_transcript','transcript_handoff','archive_prepare','archive_copy','archive_verify')
TOOL = dict(type='function', function=dict(name='chat_edit_action', description='Bounded Tariq local edits; use the current injected revision. Owner approvals remain in review UI.', parameters=dict(type='object', additionalProperties=False, properties=dict(action=dict(type='string',enum=list(ACTIONS)), project_id=dict(type='string'), camera=dict(type='string'), mic=dict(type='string'), clip_ids=dict(type='array',items=dict(type='string'),maxItems=50), revision=dict(type='string'), words=dict(type='array',items=dict(type='string'),maxItems=500), drive=dict(type='string',enum=['passport','backup'])),required=['action','project_id'])))

def current_request_allows(message, args, context):
    """Conservative default: owner text must name the operation and exact targets.

    The host supplies an unambiguous current project context (or resolves a unique
    exact project name before calling). No model-provided approval flag is read.
    Word selections require exact IDs or a uniquely occurring quoted contiguous phrase.
    """
    text=message.casefold()
    if re.search(r"\b(don't|do not|never|avoid|without)\b",text):return False
    action=args['action']
    if action == 'select_broll':
        if not re.search(r'\b(select|use|add|import)\b.*\bb[ -]?roll\b', text, re.S): return False
        rows = context.get('broll_catalog') or []
        ids = args.get('clip_ids') or []
        lookup = {r['id']: r for r in rows}
        return bool(ids) and all(i in lookup and (i.casefold() in text or re.search(r'(?<!\w)' + re.escape(lookup[i]['title'].casefold()) + r'(?!\w)', text)) for i in ids)
    patterns={
        'register_sources':r'\b(register|use|select)\b.*\b(source|sources|camera|mic)\b',
        'cancel_analysis':r'\b(cancel|stop)\b.*\b(analysis|transcription|analysing|transcribing)\b',
        'remove_words':r'\b(remove|cut|delete)\b',
        'restore_words':r'\b(restore|keep|bring back)\b',
        'undo_words':r'\bundo\b.*\b(word|words|edit|removal|last cut)\b',
        'cleanup_proposals':r'\b(run|prepare|make|create|generate)\b.*\bcleanup\b.*\b(proposals?|review|pass)\b',
        'clean_transcript':r'\b(make|create|generate|prepare)\b.*\bclean transcript\b',
        'transcript_handoff':r'\b(show|read|get|prepare)\b.*\b(approved|final) transcript\b',
        'archive_prepare':r'\b(prepare|make|create)\b.*\barchive\b',
        'archive_copy':r'\b(copy|back up)\b.*\b(archive|video|project)\b',
        'archive_verify':r'\b(verify|check)\b.*\barchive\b',
    }
    if not re.search(patterns.get(action,r'(?!)'),text,re.S):return False
    if action in ('remove_words','restore_words'):
        words=args.get('words')
        if not isinstance(words,list) or not words or any(not isinstance(w,str) for w in words):return False
        if all(re.search(r'(?<![\w-])'+re.escape(w.casefold())+r'(?![\w-])',text) for w in words):return True
        # Quote extraction is deliberately bounded: no fuzzy text or inferred ranges.
        phrases=re.findall(r""""([^"\n]+)"|\u201c([^\u201d\n]+)\u201d|(?<!\w)'([^'\n]+)'(?!\w)""",message)
        normalize=lambda value:re.findall(r"\w+(?:['â€™]\w+)*",value.casefold())
        rows=(context.get('clean') or {}).get('words') or []
        for quoted in phrases:
            target=normalize(next(x for x in quoted if x))
            if not target:continue
            matches=[]
            for start in range(len(rows)):
                tokens=[]
                for end in range(start,min(len(rows),start+500)):
                    tokens.extend(normalize(str(rows[end].get('text',''))))
                    if tokens==target:
                        matches.append([r.get('id') for r in rows[start:end+1]])
                    if len(tokens)>=len(target):break
            if len(matches)==1 and matches[0]==words:return True
        return False
    if action=='register_sources':
        candidates=context.get('sources') or {}
        for key,pool in (('camera','videos'),('mic','audio')):
            value=args.get(key)
            if not isinstance(value,str) or value not in candidates.get(pool,[]):return False
            if value.casefold() in text:continue
            name=Path(value).name.casefold()
            all_paths=candidates.get('videos',[])+candidates.get('audio',[])
            if sum(Path(p).name.casefold()==name for p in all_paths)!=1:return False
            if not re.search(r'(?<![\w.\-])'+re.escape(name)+r'(?![\w.\-])',text):return False
        return True
    if action in ('archive_copy','archive_verify'):
        return args.get('drive') in ('passport','backup') and re.search(r'\b'+args['drive']+r'\b',text) is not None
    return True

class ChatEditActions:
    def __init__(self, store, clean, archive, authorize=current_request_allows, flow=None, broll_catalog=None):
        self.flow=flow
        from .broll import catalog
        self.broll_catalog = broll_catalog or catalog
        self.store,self.clean,self.archive,self.authorize=store,clean,archive,authorize
        self.lock=threading.RLock()

    def context(self, project_id):
        self._project(project_id,project_id)
        project=self.store.get(project_id)
        state=self.clean._load(project_id) if self.clean._file(project_id).is_file() else None
        snapshot=dict(project_id=project_id,clean=state,evidence=self.clean.revision(state) if state else None,archive=self.archive.manifest(project_id),drives=self.archive.settings())
        if self.flow:
            progress=self.flow.progress(project_id)
            snapshot.update(sources=self.flow.latest(project_id,'edit_sources'),run={k:progress.get(k) for k in ('run','state')} if progress else None)
        try:
            snapshot['broll_catalog'] = self.broll_catalog()
            snapshot['broll_error'] = None
        except (ValueError, KeyError, OSError) as e:
            snapshot['broll_catalog'] = []
            snapshot['broll_error'] = str(e)[:300]
        snapshot['broll_selection'] = self.flow.latest(project_id, 'edit_broll_selection') if self.flow else None
        revision=hashlib.sha256(json.dumps(snapshot,sort_keys=True,default=str).encode()).hexdigest()
        return dict(project_id=project_id,project_title=project.get('name',project.get('title','')),revision=revision,clean_state_digest=hashlib.sha256(json.dumps(state,sort_keys=True,default=str).encode()).hexdigest(),clean=self.clean.view(project_id) if state else None,archive=snapshot['archive'],actions=list(ACTIONS),broll_catalog=snapshot['broll_catalog'],broll_error=snapshot['broll_error'],broll_selection=snapshot['broll_selection'],sources=self.flow.suggest(project_id) if self.flow else None,run=snapshot.get('run'))

    @staticmethod
    def _project(pid,current):
        if not isinstance(pid,str) or not pid or pid!=current or any(x in pid for x in '/\\') or pid in ('.','..'):
            raise ValueError('Use the exact current project ID from the trusted context.')

    def dispatch(self,args,*,role,project_id,current_message):
        # authorize is trusted host policy, never supplied in model arguments.
        # It may resolve the exact title from context; users need not type a UUID.
        if role!='tariq': raise ValueError('Only Tariq can use edit actions.')
        if not isinstance(args,dict) or args.get('action') not in ACTIONS: raise ValueError('Action unavailable.')
        action=args['action'];pid=args.get('project_id');self._project(pid,project_id)
        allowed={'action','project_id','revision'}
        if action in ('remove_words','restore_words'): allowed.add('words')
        if action=='select_broll':allowed.add('clip_ids')
        if action=='register_sources':allowed |= {'camera','mic'}
        if action in ('archive_copy','archive_verify'): allowed.add('drive')
        if set(args)-allowed: raise ValueError('Unknown arguments.')
        with self.lock:
            context=self.context(pid)
            if action=='inspect':return context
            if not isinstance(current_message,str) or not current_message.strip() or self.authorize(current_message,dict(args),context) is not True:
                raise ValueError('An explicit current owner request for this action is required.')
            if args.get('revision')!=context['revision']:raise ValueError('Revision changed; inspect again.')
            if action == 'select_broll':
                if self.flow is None: raise ValueError('Edit workflow unavailable.')
                from .broll import selection
                payload = selection(context['broll_catalog'], args.get('clip_ids'))
                previous = context['broll_selection']
                if previous and previous.get('payload') == payload:
                    result = previous
                else:
                    result = self.flow._put(pid, 'edit_broll_selection', payload, parent=previous['id'] if previous else None, expected_latest=previous['id'] if previous else None)
            elif action in ('register_sources','cancel_analysis'):
                if self.flow is None:raise ValueError('Edit workflow unavailable.')
                if action=='register_sources':
                    candidates=context['sources']
                    for key,pool in (('camera','videos'),('mic','audio')):
                        value=args.get(key)
                        if not isinstance(value,str) or value not in candidates[pool]:raise ValueError('Choose exact source candidates for this project.')
                        path=Path(value).resolve(strict=True)
                        if not path.is_file() or not path.is_relative_to(Path(candidates['folder']).resolve()):raise ValueError('Source must stay in project input folder.')
                    result=self.flow.register(pid,args['camera'],args['mic'])
                else:
                    run=context['run']
                    if not run or run['state']!='running' or not Path(run['run']).resolve().is_relative_to((self.flow.folder(pid)/'runs').resolve()):raise ValueError('No running analysis in this project.')
                    result=self.flow.cancel(pid)
            elif action in ('remove_words','restore_words'):
                words=args.get('words')
                if not isinstance(words,list) or not 1<=len(words)<=500 or any(not isinstance(w,str) or not w for w in words) or len(words)!=len(set(words)):raise ValueError('Choose 1-500 unique word IDs.')
                result=self.clean.edit(pid,'remove' if action=='remove_words' else 'restore',words,'tariq',expected_state_digest=context['clean_state_digest'])
            elif action=='undo_words':result=self.clean.undo(pid,expected_state_digest=context['clean_state_digest'])
            elif action=='cleanup_proposals':result=self.clean.cleanup(pid)
            elif action=='clean_transcript':result=self.clean.clean_transcript(pid)
            elif action=='transcript_handoff':result=self.clean.sam_handoff(pid)
            elif action=='archive_prepare':result=self.archive.prepare(pid)
            else:
                drive=args.get('drive')
                if drive not in ('passport','backup') or not context['archive']:raise ValueError('Prepare an archive and explicitly select a configured drive.')
                result=(self.archive.copy if action=='archive_copy' else self.archive.verify)(pid,drive)
            return dict(project_id=pid,action=action,result=result)
