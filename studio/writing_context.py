"""On-demand, bounded writing context. This module never calls a model."""
import hashlib,json,re
from pathlib import Path

JOBS={'section_open':['B1','B2','B13'],'transition':['B3','B14'],
      're_hook':['B15','B19','B21'],'story':['B4','B28'],
      'mechanics':['B8','B27'],'close':['B31']}

class Context:
    def __init__(self,root):
        self.root=Path(root);self.sources=self.root/'reference_sources';self.package=self.root/'writing_passes'
    def read(self,name):return json.loads((self.package/name).read_text(encoding='utf-8'))
    def catalog(self):
        provenance=json.loads((self.sources/'library-provenance.json').read_text(encoding='utf-8'))
        manifest=self.read('pass-manifest.json')
        return dict(status='sources_ready',pass_configuration_status='configured_local_handoff',
                    sources=[{k:r[k] for k in ('name','library_file_id','version','bytes','sha256')} for r in provenance],
                    passes=[dict(id=p['id'],writable_scope=p['writable_scope'],depends_on=p['depends_on']) for p in manifest['passes']],
                    rhetorical_jobs=list(JOBS),live_ai=False,model_quality_tested=False,
                    example_approval='Reference examples; no per-segment approval metadata was supplied',
                    provenance=self.read('integration-provenance.json'))
    def build(self,project,revision,start,end,job,pass_id=None,scope='selection'):
        if project['revision']!=revision:raise ValueError('Document revision changed; request fresh context')
        if job not in JOBS:raise ValueError('Choose a rhetorical job')
        if scope not in ('selection','full_script'):raise ValueError('Scope must be selection or full_script')
        text=project['script']
        if type(start)!=int or type(end)!=int or not 0<=start<=end<=len(text):raise ValueError('Invalid Unicode code-point selection')
        whole=scope=='full_script' or start==0 and end==len(text)
        manifest=self.read('pass-manifest.json');pass_id=pass_id or 'connective'
        specialist=next((p for p in manifest['passes'] if p['id']==pass_id),None)
        if specialist is None:raise ValueError('Unknown writing pass')
        rules=self.read('rules.json')['rules'];ids=set(specialist['rules']+manifest['global_rule_ids'])
        if pass_id=='final_flow':ids={r['id'] for r in rules}
        selected_rules=[r for r in rules if r['id'] in ids]
        packaging=dict(project.get('packaging',{}))
        packaging['selected_title']=next((t['text'] for t in project['titles'] if t['id']==project['chosen_title']),'')
        missing=[k for k in ('selected_title','curiosity_question','concrete_payoff') if not packaging.get(k,'').strip()]
        if not (packaging.get('thumbnail_concept','').strip() or packaging.get('thumbnail_reference','').strip()):missing.append('thumbnail_concept_or_reference')
        # A shortlist does not establish the selected title/thumbnail pair.
        magnet=dict(project.get('lead_magnet',{}));magnet.setdefault('decision','unknown')
        missing_magnet=[]
        if magnet['decision']=='unknown':missing_magnet=['yes_or_none_decision']
        elif magnet['decision']=='yes':missing_magnet=[k for k in ('name','result','contents') if not magnet.get(k,'').strip()]
        blockers=[]
        if whole:
            blockers += ['packaging.'+k for k in missing]
            blockers += ['lead_magnet.'+k for k in missing_magnet]
        if pass_id=='lead_magnets' and magnet['decision']!='none':
            blockers += ['lead_magnet.'+k for k in missing_magnet]
            blockers += ['lead_magnet.'+k for k in ('free_status','destination','evidence') if not magnet.get(k,'').strip()]
        source=(self.sources/'script-touch-up_SKILL.md').read_text(encoding='utf-8')
        lines=source.splitlines();examples=[]
        for code in JOBS[job][:3]:
            match=re.search(r'^#### '+code+r'\b[^\n]*\n',source,re.M)
            if not match:raise ValueError('Missing source technique '+code)
            following=re.search(r'^#{1,4} ',source[match.end():],re.M)
            stop=match.end()+following.start() if following else len(source)
            examples.append(dict(id='S1-'+code,technique=code,source='S1',lines=[source[:match.start()].count('\n')+1,source[:stop].count('\n')],
                                 text=source[match.start():stop],approval='unknown',role='source_example',
                                 factual_warning='Reference technique only; not verified Josh claims or approved wording'))
        evidence=[];seen=set()
        for rule in selected_rules:
            if rule.get('source')!='S1' or not rule.get('source_lines'):continue
            a,b=rule['source_lines'];key=(a,b)
            if key in seen:continue
            seen.add(key)
            evidence.append(dict(source='S1',lines=[a,b],rule_id=rule['id'],text='\n'.join(lines[a-1:b])))
        if pass_id=='final_flow':evidence=[];examples=[]
        prompt=next((self.package/'passes').glob('*-'+pass_id+'.md')).read_text(encoding='utf-8')
        template=self.read('document-state-template.json')
        template.update(document_id=project['id'],version=revision,scope='full_script' if whole else 'selection',lead_magnet=magnet)
        template['ledgers']['dirty_rules']=[r['id'] for r in rules]
        return dict(project_id=project['id'],project_revision=revision,document_sha256=hashlib.sha256(text.encode()).hexdigest(),
                    packaging_brief=packaging,missing_packaging=missing,lead_magnet=magnet,missing_lead_magnet=missing_magnet,
                    can_draft_full_script=not missing and not missing_magnet,preflight_blockers=sorted(set(blockers)),
                    requested_pass=pass_id,pass_configuration_status='configured_local_handoff',
                    generation_blocked=bool(blockers),live_provider_available=False,
                    pass_contract=specialist,shared_instructions=(self.package/'shared.md').read_text(encoding='utf-8'),
                    pass_instructions=prompt,selected_rules=selected_rules,exact_rule_evidence=evidence,
                    selection=dict(start=start,end=end,offset_unit='unicode_code_points',text=text[start:end]),
                    adjacent_before='\n\n'.join(text[:start].rsplit('\n\n',2)[-2:]),
                    adjacent_after='\n\n'.join(text[end:].split('\n\n',2)[:2]),rhetorical_job=job,
                    audience_summary=(self.package/'references/audience-summary.md').read_text(encoding='utf-8'),
                    selected_examples=examples,document_state=template,
                    output_contract=self.read('output-contract.json'),rerun_policy=self.read('rerun-rules.json'),
                    conflicts=self.read('conflicts.json'),readiness='not_assessed',
                    evidence_notice='Ledgers and semantic checks are unassessed. Source claims and offer facts require verification. No example is marked approved.',
                    sources=self.catalog()['sources'],external_model_called=False)
