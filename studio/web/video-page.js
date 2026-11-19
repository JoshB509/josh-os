'use strict';
// The video page, stripped back. A video is a row of pieces, each ticked once it's locked in:
// title, thumbnail, script, recording, edit, animations and publish, in any order, plus one
// notes box. Clicking a piece opens just that piece underneath. The existing title, thumbnail,
// script and results views are reused inside their pieces; everything else around them goes.
const VP_PIECES=[['notes','Notes'],['title','Title'],['thumbnail','Thumbnail'],['script','Script'],['edit','Edit'],['data','Video data'],['results','All results']];
const VP_TAB={packaging:'brief',notes:'brief',title:'titles',thumbnail:'thumbnails',script:'script',data:'brief',results:'results',edit:'brief',animations:'brief'};
// The main parts of a video, along the top (16 Nov 2026). Recording is the first stage of Editing. The old release
// checklist's parts now sit where they are done: the date in the header, export, chapters and Frame.io in Editing's
// Export stage, and the publish time and Open Publishing at the foot of Packaging. Notes stays a small link.
const VP_NAV=[['packaging','Packaging'],['script','Script'],['edit','Editing'],['data','Video data']];
const VP_MORE=[['notes','Notes']];
const VP_PARENT={title:'packaging',thumbnail:'packaging',results:'data'};
// A tab set by other screens (a desk link, the Editing list) opens the matching piece.
const VP_FROM_TAB={brief:'packaging',titles:'title',thumbnails:'thumbnail',script:'script',edit:'edit',results:'results'};
let vpTabSet=null,vpNoPush=false;
let vpOpen={},vpStatus={},vpTakes=null,vpBusy=false;
if(typeof fields!=='undefined'&&!fields.includes('locks'))fields.push('locks');

function vpLocks(p){return p.locks||{}}
function vpTitle(p){return (p.titles||[]).find(t=>t.id===p.chosen_title)?.text||''}
function vpDone(p,k){
  const L=vpLocks(p),s=vpStatus[p.id]||{};
  if(k==='notes')return null;
  if(k==='recording')return !!(L.recording||(s.recording&&p.media?.video===s.recording.path));
  if(k==='publish')return !!(L.publish||p.stage==='Published');
  if(k==='edit')return vpEditStage(p)==='done';
  return !!L[k];
}
function vpSub(p,k){
  const s=vpStatus[p.id]||{},words=(p.script||'').trim().split(/\s+/).filter(Boolean).length;
  switch(k){
    case 'notes':return (p.notes||'').trim()?'Your notes':'Nothing yet';
    case 'title':return vpTitle(p)||`${(p.titles||[]).length} idea${(p.titles||[]).length===1?'':'s'}`;
    case 'thumbnail':return `${(p.thumbnails||[]).length} option${(p.thumbnails||[]).length===1?'':'s'}`;
    case 'script':return words?`${words.toLocaleString()} words`:'Not started';
    case 'recording':return s.recording?s.recording.name:s.active?'Recording now':'Not recorded';
    case 'edit':return {start:'Not started',setup:'Tariq is setting up',script:'Script ready for you',animations:'Animations',done:'Exported'}[vpEditStage(p)];
    case 'publish':return p.publish_date?new Date(p.publish_date+'T12:00').toLocaleDateString(undefined,{weekday:'short',day:'numeric',month:'short'}):'No date';
  }
}
function vpDefault(p){return 'packaging'}
// An old saved 'animations' piece opens Edit, where the animations now live.
function vpOpenKey(p){const k=vpOpen[p.id];
  if(k==='recording'){vpOpen[p.id]='edit';vpEditTab[p.id]='recording';return 'edit'}
  if(k==='publish'){vpOpen[p.id]='data';return 'data'}
  return k==='animations'?'edit':k}
function vpPiece(p){return vpOpenKey(p)||vpDefault(p)}

function vpPiecesRow(p){
  const open=vpPiece(p),top=VP_PARENT[open]||open;
  const tick=k=>k==='packaging'?vpPackDone(p):k==='edit'?vpEditDone(p):k==='data'?false:vpDone(p,k);
  return `<nav class="vp-pieces" aria-label="Parts of this video"><div class="vp-main-nav">${VP_NAV.map(([k,l])=>{const d=tick(k);
    return `<button class="vp-nav ${k===top?'on':''} ${d?'done':''}" data-vp-piece="${k}" ${k===top?'aria-current="page"':''}>${d?'<span class="vp-tick" aria-hidden="true">✓</span>':''}${l}${d?'<span class="sr-only">, done</span>':''}</button>`}).join('')}</div>
    <div class="vp-more-nav">${VP_MORE.map(([k,l])=>{const d=false;
    return `<button class="vp-more ${k===top?'on':''}" data-vp-piece="${k}" ${k===top?'aria-current="page"':''}>${d?'<span class="vp-tick" aria-hidden="true">✓</span>':''}${l}</button>`}).join('')}</div></nav>`;
}
// Packaging is done when the title and main thumbnail are locked in, and the description and pinned comment text are
// approved. Read from the release check, so it only ticks once that has loaded and agrees.
function vpPackItems(p){
  const r=vpRel[p.id];if(!r||!r.items)return null;
  const by=Object.fromEntries(r.items.map(i=>[i.key,i])),L=vpLocks(p),title=vpTitle(p);
  return {title:!!(title&&L.title),thumbnail:by.thumbnail?.state==='done'&&!!L.thumbnail&&(p.top_three||[]).length>0,
    description:by.description?.state==='done',cta:by.cta?.state==='done',by};
}
function vpPackDone(p){const y=vpRel[p.id]?.youtube;if(y&&y.privacy==='public')return true;const x=vpPackItems(p);return !!x&&x.title&&x.thumbnail&&x.description&&x.cta}
function vpEditDone(p){if(vpEf(p))return vpEditStage(p)==='done';const r=vpRel[p.id];return !!r?.items?.some(i=>i.key==='export'&&i.state==='done')}
function vpLockBar(p,k){
  // Thumbnails have no lock-in step (Josh, 16 Nov 2026). Placing a thumbnail in Main, B or C is the confirmation, and
  // studio.js keeps locks.thumbnail in step with Main whenever the three change. An older video placed before then,
  // with no lock, is not rewritten just by being opened; the readiness list treats its Main as chosen.
  if(k==='notes'||k==='thumbnail'||k==='data'||k==='results')return '';
  const L=vpLocks(p),locked=!!L[k],auto=vpDone(p,k)&&!locked;
  const label={title:'title',thumbnail:'thumbnail',script:'script',recording:'recording',edit:'edit',animations:'animations',publish:'video as published'}[k];
  if(k==='recording'||k==='animations'||k==='edit')return '';
  return `<div class="vp-lock">${locked?`<span class="vp-locked">✓ Locked in ${esc(new Date(L[k]).toLocaleDateString(undefined,{day:'numeric',month:'short'}))}</span><button class="subtle" data-vp-unlock="${k}">Unlock</button>`
    :auto?'':`<button class="primary" data-vp-lock="${k}" ${k==='title'&&!vpTitle(p)?'disabled title="Pick a title first"':''}>${k==='edit'?'Hand the edit to Tariq':k==='publish'?'Mark as published':`Lock in the ${label}`}</button>`}</div>`;
}

// Film: one press gets a video ready to record (studio/filming.py). Sam runs the same preparation from chat.
let vpFilm={},vpFilmBusy=false;
function vpFilmPanel(p){
  if(!(p.id in vpFilm)){vpFilm[p.id]=null;api(`/api/projects/${encodeURIComponent(p.id)}/film`).then(v=>{if(v&&v.steps){vpFilm[p.id]=v;if(current?.id===p.id)renderDetail()}}).catch(()=>{})}
  const r=vpFilm[p.id];if(!r||!r.steps)return r?.message?`<p class="vp-film-note">${esc(r.message)}</p>`:'';
  const mark={done:'Done',failed:'Stopped here',skipped:'Not done',waiting:'Not done',running:'Working'};
  return `<div class="vp-film" aria-live="polite"><p class="hint">Earlier filming setup. A finished take can be saved even if the teleprompter check is incomplete.</p><p class="vp-film-head ${esc(r.state)}">${esc((r.message||'').replace("Recording hasn't started.", 'This setup check did not start recording.'))}</p>
    <ol>${r.steps.map(s=>`<li class="${esc(s.state)}"><span class="dot" aria-hidden="true"></span><b>${esc(s.label)}</b><small>${esc(mark[s.state]||s.state)}${s.detail?`. ${esc(s.detail)}`:''}</small></li>`).join('')}</ol>
    ${r.manual?`<p class="vp-film-manual">${esc(r.manual)}</p>`:''}
    ${(r.state==='needs_you'||r.state==='partial')&&r.steps.some(s=>s.key==='obs_folder'&&s.state==='done')?`<div class="vp-actions">${r.prompter_text?'<button class="subtle" data-vp-film-copy>Copy script</button>':''}<button class="primary" data-vp-film-check ${vpFilmBusy?'disabled':''}>Check teleprompter</button></div>`:''}</div>`}
document.addEventListener('click',async e=>{
  if(!current)return;const pid=current.id,r=vpFilm[pid];
  if(e.target.closest('[data-vp-film-copy]')&&r?.prompter_text){try{await navigator.clipboard.writeText(r.prompter_text);toast('Copied. Paste it into Camera Hub.')}catch(err){toast(err.message,true)}return}
  if(!e.target.closest('[data-vp-film-check]')||vpFilmBusy)return;
  vpFilmBusy=true;renderDetail();
  try{vpFilm[pid]=await api(`/api/projects/${encodeURIComponent(pid)}/film/prompter`,{});toast(vpFilm[pid].state==='ready'?'Ready to film':vpFilm[pid].message||'Not ready yet',vpFilm[pid].state!=='ready')}
  catch(err){toast(err.message,true)}finally{vpFilmBusy=false;if(current?.id===pid)renderDetail()}
});
document.addEventListener('click',async e=>{
  if(!e.target.closest('[data-vp-film]')||!current)return;const pid=current.id;if(vpFilmBusy)return;
  vpFilmBusy=true;renderDetail();
  try{await (typeof autosaveProject==='function'?autosaveProject(pid):null);vpFilm[pid]=await api(`/api/projects/${encodeURIComponent(pid)}/film`,{force:vpFilm[pid]?.state==='ready'});
    toast(vpFilm[pid].state==='ready'?'Ready to film':vpFilm[pid].message||'Film stopped',vpFilm[pid].state!=='ready')}
  catch(err){toast(err.message,true)}finally{vpFilmBusy=false;if(current?.id===pid){try{vpStatus[pid]=await api(`/api/projects/${encodeURIComponent(pid)}/pieces`)}catch{}renderDetail()}}
});

// Pieces with their own view ---------------------------------------------------
function vpNotes(p){return `<label class="vp-notes"><span class="sr-only">Notes</span><textarea data-field="notes" rows="14" placeholder="Anything you want to jot down about this video as you go">${esc(p.notes||'')}</textarea></label>`}
function vpRecording(p){
  const s=vpStatus[p.id]||{};
  if(s.recording&&p.media?.video===s.recording.path)return `<div class="vp-card"><h3>Recorded</h3><p>The take is in the video's folder as <b>${esc(s.recording.name)}</b>.</p>${s.folder?`<p class="hint">${esc(s.folder)}\\Recorded\\Input</p>`:''}<button class="primary" data-vp-edit-tab="setup">Continue to Setup</button></div>`;
  const takes=vpTakes&&vpTakes.pid===p.id?vpTakes.list:null;
  return `<div class="vp-card">
    ${s.active?'<h3>Recording this video now</h3>':'<h3>Ready when you are</h3>'}
    <div class="vp-actions"><button class="${s.active?'subtle':'primary'}" data-vp-film ${vpBusy||vpFilmBusy?'disabled':''}>${vpFilmBusy?'Getting ready…':vpFilm[p.id]?.state==='ready'?'Film again':'Film'}</button>${s.active?'':`<button class="subtle" data-vp-rec-start ${vpBusy?'disabled':''}>I'm recording this now</button>`}<button class="${s.active?'primary':'subtle'}" data-vp-rec-find ${vpBusy?'disabled':''}>It's recorded</button></div>
    ${vpFilmPanel(p)}
    ${takes?(takes.length?`<div class="vp-takes"><p>Today's recordings in the shared Input folder and this video's Recorded/Input. Pick this video's take. Files already in this video's folder stay in place.</p>${takes.map((t,i)=>`<label class="vp-take"><input type="radio" name="vp-take" value="${esc(t.path)}" ${takes.length===1?'checked':''}><span><b>${esc(t.name)}</b><small>${esc(t.path)}</small><small>${t.size_mb.toLocaleString()} MB, saved ${esc(new Date(t.modified).toLocaleTimeString(undefined,{hour:'numeric',minute:'2-digit'}))}${t.settling?', still being written':''}${t.also.length?`. The ${esc(t.also.join(', '))} stays in Input.`:''}</small></span></label>`).join('')}
      <button class="primary" data-vp-rec-move ${vpBusy?'disabled':''}>${vpBusy?'Checking the recording.':'Use this recording'}</button></div>`:'<p class="hint">No recordings from today in either recording folder yet.</p>'):''}
  </div>`;
}
// Edit belongs to Tariq. Three tabs: he sets the edit up (steps fill in as he goes, with pictures), then the
// script with his cuts for Josh to check, then the animations. Each tab fills in once he reaches it.
let vpEditTab={},vpEditBusy=false,vpSetupDraft={},vpSetupPicking=false;
const VP_EDIT_TABS=[['recording','Recording'],['setup','Setup'],['script','Clean Cut'],['animations','Animations'],['export','Export']];
function vpEf(p){return typeof efPid!=='undefined'&&efPid===p.id?efData:null}
function vpEditStage(p){
  const d=vpEf(p);if(!d)return vpLocks(p).edit?'setup':'start';
  const st=Object.fromEntries((d.status||[]).map(x=>[x.key,x.state]));
  if(['verified','exported'].includes(st.final))return 'done';
  if(d.proposal?.approved)return 'animations';
  if(d.proposal)return 'script';
  return d.setup||vpLocks(p).edit?'setup':'start';
}
function vpEditCurrent(p){
  if(vpEditTab[p.id])return vpEditTab[p.id];
  const k=vpEditStage(p);
  if(k==='start'&&!vpDone(p,'recording'))return 'recording';
  return k==='script'?'script':k==='done'?'export':k==='animations'?'animations':'setup';
}
function vpEdit(p){
  const d=vpEf(p),open=vpEditCurrent(p),stage=vpEditStage(p),order=['start','setup','script','animations','done'];
  const reached={recording:true,setup:true,script:!!d&&(order.indexOf(stage)>=2||!!d.proposal),animations:!!d&&order.indexOf(stage)>=3,export:true};
  const pane=open==='recording'?vpRecording(p):open==='export'?vpEditExport(p):!d?'<div class="vp-card"><p class="hint">Loading the edit…</p></div>'
    :open==='setup'?vpEditSetup(p,d):open==='script'?vpEditScript(p,d):vpEditAnimations(p,d);
  return `<div class="vp-edit">
    <nav class="vp-etabs" aria-label="Edit stages">${VP_EDIT_TABS.map(([k,l],i)=>`<button class="vp-etab ${k===open?'on':''} ${reached[k]?'':'later'} ${k==='recording'&&vpDone(p,'recording')?'done':''}" data-vp-edit-tab="${k}" aria-pressed="${k===open}"><span>${k==='recording'&&vpDone(p,'recording')?'✓':i+1}</span>${l}</button>`).join('')}</nav>
    <div class="vp-epane">${pane}</div>
  </div>`;
}
// Export, the last stage: the final export's approval, the chapters made from it and the optional Frame.io review,
// read from the release check (studio/release_ready.py) with the same approve and make buttons as before.
function vpEditExport(p){
  vpRelFetch(p);
  const r=vpRel[p.id];
  if(!r)return '<div class="vp-card"><p class="hint">Checking the export…</p></div>';
  if(r.error)return `<div class="vp-card"><p class="hint">${esc(r.error)}</p></div>`;
  const rows=(r.items||[]).filter(i=>i.key==='export'||i.key==='chapters');
  return `<div class="vp-rel vp-export"><ol>${rows.map(i=>`<li class="${esc(i.state)}"><span class="dot" aria-hidden="true"></span><div><b>${esc(i.label)}</b> <small class="vp-rel-state">${esc(VP_REL_MARK[i.state]||i.state)}</small><p>${esc(i.detail)}${i.next&&i.state!=='done'?` <span class="vp-rel-next">${esc(i.next)}</span>`:''}</p>${vpRelAct(p,r,i)}</div></li>`).join('')}</ol></div>${vpFrameioBox(p)}`;
}
function vpEditSetup(p,d){
  const su=d.setup?{...d.setup,message:vpSetupMessage(d.setup.message),steps:(d.setup.steps||[]).map(s=>({...s,message:vpSetupMessage(s.message)}))}:null;
  const files=vpSetupFiles(p,d),working=su?.state==='running'||d.progress?.state==='running',busy=vpEditBusy||vpSetupPicking||working;
  const field=(key,label,kind)=>`<label>${label}<input id="vp-setup-${key}" data-vp-setup-source="${key}" value="${esc(files[key])}" placeholder="Choose ${label.toLowerCase()}" ${busy?'disabled':''}></label><button class="subtle" data-vp-setup-pick="${key}" data-kind="${kind}" ${busy?'disabled':''}>Browse ${label.toLowerCase()}</button>`;
  const sources=`<div class="vp-card vp-start"><h3>Set up the edit</h3>
    <p>Confirm the recorded video and choose the good microphone audio.</p>
    ${field('camera','Recorded video','video')}${field('mic','Good microphone audio','audio')}
    <p class="hint">AutoCut runs Silences, then Repeat. Review the result in Clean Cut.</p>
    ${d.processor==='autocut-v1'?'':'<p class="hint">CareerOS needs to reload the updated processing service before you can start.</p>'}
    <div class="vp-actions"><button class="primary" data-vp-go ${busy||d.processor!=='autocut-v1'?'disabled':''}>${vpEditBusy?'Starting processing...':working?'Processing...':'Start processing'}</button></div></div>`;
  if(!su)return sources;
  return sources+vpPhase(p,su,{running:'Tariq is setting up the edit',done:'The setup is done',failed:'Tariq stopped',blocked:'Processing is blocked',needs_you:'Tariq needs you',interrupted:'Tariq stopped'},'',
    su.state==='done'?'<div class="vp-actions"><button class="primary" data-vp-edit-tab="script">Review the clean cut</button></div>':'');
}
function vpSetupMessage(text){return (text||'').replace('Pick the camera and the good mic on the Script tab, then ask me to start again.','Pick the recorded video and good microphone audio in Setup, then press Start processing.').replaceAll('Script tab','Clean Cut tab')}
function vpSetupFiles(p,d){
  if(!vpSetupDraft[p.id]){const f=d.sources?.payload?.files||{},s=d.suggest||{};
    vpSetupDraft[p.id]={camera:f.camera?.path||p.media?.video||(s.videos?.length===1?s.videos[0]:''),mic:f.mic?.path||p.media?.audio||''};}
  return vpSetupDraft[p.id];
}
document.addEventListener('input',e=>{const key=e.target.dataset?.vpSetupSource;if(key&&current)vpSetupFiles(current,vpEf(current)||{})[key]=e.target.value});
document.addEventListener('click',async e=>{
  const b=e.target.closest('[data-vp-setup-pick]');if(!b||!current||vpSetupPicking||vpEditBusy)return;
  const p=current,pid=p.id,key=b.dataset.vpSetupPick,files=vpSetupFiles(p,vpEf(p)||{});vpSetupPicking=true;vpEditRender();
  try{const result=await api('/api/pick',{kind:b.dataset.kind});if(result.path)files[key]=result.path;}
  catch(err){toast(err.message,true)}finally{vpSetupPicking=false;if(current?.id===pid)vpEditRender()}
});
// While Tariq works, a small line under the heading changes every few seconds, like a loading screen.
const VP_FUN=['Adding a little magic','Making it worth a million views','Polishing the gold','Giving every card its moment','Keeping the reveals on the beat',
  'Checking the fonts are on brand','Leaving room for your face','Lining up each press with your words','Making sure nothing pulls focus from you',
  'Choosing the boldest word on each slide','Nudging things a pixel to the left','Making the executives look impressed','Getting the timing just right',
  'Tidying the edges','Saving the best reveal for last'];
function vpFunLine(){return VP_FUN[Math.floor(Math.random()*VP_FUN.length)]}
setInterval(()=>{document.querySelectorAll('[data-vp-fun]').forEach(el=>{el.classList.add('out');setTimeout(()=>{el.textContent=vpFunLine();el.classList.remove('out')},300)})},5000);
// One run of Tariq's steps: each waits greyed out, fills while he works on it and ticks green when done.
function vpPhase(p,su,heads,action,foot){
  const icon={waiting:'',running:'',done:'✓',problems:'!',failed:'✕',skipped:'–'};
  const img=n=>`/api/projects/${encodeURIComponent(p.id)}/edit-flow/setup/${encodeURIComponent(n)}`;
  // The phase's progress is its real steps: done ones count whole, a measured running step counts its fraction.
  const steps=su.steps||[],finished=steps.filter(x=>['done','problems','skipped'].includes(x.state)).length;
  const part=steps.reduce((n,x)=>n+(x.state==='running'&&x.fraction!=null?x.fraction:0),0),pct=steps.length?Math.round((finished+part)/steps.length*100):0;
  return `<div class="vp-card vp-setup">
    <div class="vp-setup-head"><h3>${esc(heads[su.state]||heads.running)}</h3>${action||''}</div>
    ${steps.length?`<div class="vp-phase-bar"><div class="vp-bar" role="progressbar" aria-label="Steps done" aria-valuemin="0" aria-valuemax="${steps.length}" aria-valuenow="${finished}"><i style="width:${pct}%"></i></div><span>${finished} of ${steps.length} steps done</span></div>`:''}
    ${su.state==='running'?`<p class="vp-fun" data-vp-fun>${esc(vpFunLine())}</p>`:''}
    ${su.message&&su.state!=='running'?`<p class="vp-phase-msg ${esc(su.state)}">${esc(su.message)}</p>`:''}
    <ol class="vp-steps">${su.steps.map(st=>`<li class="vp-step ${esc(st.state)}${st.fraction!=null&&st.state==='running'?' measured':''}">
      <span class="vp-step-mark" aria-hidden="true">${icon[st.state]||''}</span>
      <div class="vp-step-main"><div class="vp-step-top"><b>${esc(st.label)}</b><span class="vp-step-state">${{waiting:'Waiting',running:st.fraction!=null?Math.round(st.fraction*100)+'%':'Working',done:'Done',problems:'Check this',failed:'Stopped',blocked:'Blocked',skipped:'Not needed'}[st.state]||''}</span></div>
        <div class="vp-bar"><i ${st.fraction!=null&&st.state==='running'?`style="width:${Math.max(4,Math.round(st.fraction*100))}%"`:''}></i></div>
        ${st.message?`<p>${esc(st.message)}</p>`:''}
        ${st.images?.length?`<div class="vp-step-pics">${st.images.map(n=>`<a href="${img(n)}" target="_blank" rel="noopener" title="Open full size"><img alt="${esc(st.label)}, a frame from Tariq's run" loading="lazy" src="${img(n)}?t=${encodeURIComponent(st.at||'')}" data-vp-pic></a>`).join('')}</div>`:''}
      </div></li>`).join('')}</ol>
    ${foot||''}
  </div>`;
}
function vpEditScript(p,d){
  if(!d.proposal)return `<div class="vp-card vp-later"><h3>Clean cut review</h3><p>Choose the recorded video and microphone audio in Setup, then start processing. The transcript and proposed cuts will appear here for your review.</p><button class="primary" data-vp-edit-tab="setup">Go to Setup</button></div>`;
  const st=Object.fromEntries((d.status||[]).map(x=>[x.key,x])),bu=d.phases?.build;
  let build='';
  if(d.proposal?.approved&&bu&&(bu.state==='running'||st.cut?.state==='verified'||bu.state!=='done'))
    build=vpPhase(p,bu,{running:'Tariq is building your cut in Premiere',done:'The cut is built',failed:'The cut did not build',interrupted:'The build stopped'},
      bu.state==='running'?'':st.cut?.state==='verified'?'<button class="primary" data-vp-edit-tab="animations">Go to the animations</button>':`<button class="primary" data-vp-build ${vpEditBusy?'disabled':''}>Build it again</button>`);
  else if(d.proposal?.approved&&st.cut?.state!=='verified')
    build=`<div class="vp-card"><h3>Your clean cut is approved</h3><p>${st.cut&&!['waiting','requested','running'].includes(st.cut.state)?`The last build stopped. ${esc(st.cut.reason||'')}`:'Tariq builds it in Premiere as a new sequence, CLEAN_CUT, from his own project.'}</p>
      <div class="vp-actions"><button class="primary" data-vp-build ${vpEditBusy?'disabled':''}>Build the cut in Premiere</button></div></div>`;
  return `${build}<div id="ef-embed">${efInner(true)}</div>`;
}
let vpAnimSeen={};
function vpEditAnimations(p,d){
  const st=Object.fromEntries((d.status||[]).map(x=>[x.key,x])),s=vpStatus[p.id]||{},ph=d.phases||{};
  const cutOk=st.cut?.state==='verified',vis=st.visuals,an=ph.animate,fi=ph.final,making=an?.state==='running';
  const anim=an&&an.state!=='done'?vpPhase(p,an,{running:'Tariq is making the slides and shot list',failed:'Tariq stopped on the slides',interrupted:'Tariq stopped on the slides'},
    making?'':`<button class="subtle" data-vp-slides ${vpEditBusy?'disabled':''}>Try again</button>`):'';
  const deck=s.deck?`<div class="vp-card vp-anim-card"><h3>${vis?.state==='approved'?'Storyboard approved':'The animations are ready to review'}</h3>
      <p>${esc(s.deck.slides)} and ${esc(s.deck.shot_list||'no shot list yet')}. ${cutOk?'Approve the storyboard and Tariq puts it all together and exports it.':'You can approve the storyboard once the cut is built on the Clean Cut tab.'}</p>
      ${vpAnimStrip(p)}
      <div class="vp-actions"><button class="${vis?.state==='approved'?'subtle':'primary'}" data-er-open="${esc(p.id)}">Open the review</button>
      ${cutOk&&vis?.state!=='approved'?'<button class="subtle" data-ef-storyboard>Approve the storyboard</button>':''}
      ${making?'':`<button class="subtle" data-vp-slides ${vpEditBusy?'disabled':''}>Make them again</button>`}</div></div>`
    :making?'':`<div class="vp-card"><h3>No slides yet</h3><p>Tariq makes the slides and shot list from the script, with your notes below. You can watch each step here while he works.</p>
      <div class="vp-actions"><button class="primary" data-vp-slides ${vpEditBusy?'disabled':''}>Make the slides</button></div></div>`;
  const n=d.anim_notes||{};
  const notes=`<div class="vp-card vp-notes-card"><h3>Your notes for the animations</h3>
    <p class="hint">Tariq reads these when he makes the slides and when you chat with him. Ideas you give him in chat get added here too.</p>
    <textarea id="vp-anim-notes" rows="7" placeholder="For example, four blank cards that fill in one tip at a time, or a picture in picture on the chess line">${esc(n.text||'')}</textarea>
    <div class="vp-actions"><button class="subtle" data-vp-anim-notes>Save notes</button>${n.at?`<span class="hint">Saved ${esc(new Date(n.at).toLocaleString(undefined,{day:'numeric',month:'short',hour:'numeric',minute:'2-digit'}))}</span>`:''}</div></div>`;
  const fin=vis?.state==='approved'&&fi?vpPhase(p,fi,{running:'Tariq is putting it together and exporting it',done:'Exported and checked',failed:'The export has a problem',interrupted:'The export stopped'}):'';
  // when the slides finish, look for the new deck
  if(vpAnimSeen[p.id]==='running'&&an?.state==='done')vpLoad(p.id);
  vpAnimSeen[p.id]=an?.state;
  return anim+deck+fin+notes;
}
function vpEditRender(){
  const p=current;if(!p||vpPiece(p)!=='edit')return;
  const body=$('#tab-body');if(!body)return;
  const keep=$('#ef-script')?.scrollTop,y=scrollY,ta=$('#vp-anim-notes'),typing=ta&&document.activeElement===ta?{v:ta.value,a:ta.selectionStart,b:ta.selectionEnd}:null;
  body.innerHTML=vpEdit(p);
  if(typing){const t=$('#vp-anim-notes');if(t){t.value=typing.v;t.focus();t.setSelectionRange(typing.a,typing.b)}}
  if(keep!=null&&$('#ef-script'))$('#ef-script').scrollTop=keep;
  scrollTo(0,y);
  const row=document.querySelector('.vp-pieces');if(row)row.outerHTML=vpPiecesRow(p);
}
async function vpEditLoad(p){
  if(typeof efPid==='undefined')return;
  if(efPid!==p.id){efPid=p.id;efData=null;efCard=null;efUndo=[];efRedo=[];efPrev={}}
  await efLoad();
}

// Optional Frame.io review (studio/frameio_review.py). Josh chooses it; it never uploads, shares or publishes on its own.
let vpFio={},vpFioBusy=false;
function vpFrameioBox(p){
  if(!(p.id in vpFio)){vpFio[p.id]=null;api(`/api/projects/${encodeURIComponent(p.id)}/frameio`).then(v=>{vpFio[p.id]=v;if(current?.id===p.id)renderDetail()}).catch(e=>{vpFio[p.id]={error:e.message};if(current?.id===p.id)renderDetail()})}
  const v=vpFio[p.id];if(!v)return '';if(v.error)return `<div class="vp-rel vp-fio"><p class="hint">${esc(v.error)}</p></div>`;
  const c=v.connection||{},r=(v.reviews||[])[0],d=v.destination;
  let body='',btn='';
  if(c.state!=='connected')body=`<p>${esc(c.detail||'Frame.io is not connected.')}</p>${c.state==='needs_sign_in'?'<p class="hint">One time, on this PC: open a terminal in E:\\Videos\\DaJoshiResolve, run claude, type /mcp, choose frameio, then Authenticate and sign in with your Adobe ID in the browser that opens.</p>':''}`;
  else if(!d)body=`<p>Connected. Choose the private Frame.io project and folder for review before anything is sent.</p>${vpFioDest(v)}`;
  else body=`<p>Review folder ${esc(d.project_name)}${d.folder_name?' / '+esc(d.folder_name):''}. Private, no share links.</p>`;
  if(r){const s={staged:'Staged, not uploaded',uploading:'Uploading',processing:'Processing in Frame.io',ready:'Ready to review in Frame.io',uncertain:'Upload result unclear',failed:'Upload failed',checked_absent:'Not in Frame.io, can be sent again',blocked:'Blocked'}[r.state]||r.state;
    body+=`<p><b>${esc(r.export.name)}</b> <small class="vp-rel-state">${esc(s)}</small>${r.reason?`<br><span class="vp-rel-next">${esc(r.reason)}</span>`:''}</p>`;
    if(r.feedback?.length)body+=`<ol class="vp-fio-notes">${r.feedback.slice(0,12).map(f=>`<li><b>${esc(f.at)}</b> ${esc(f.text)}</li>`).join('')}</ol><p class="hint">Comments on this exact version, read ${esc((r.feedback_read_at||'').slice(0,16).replace('T',' '))}. Nothing is changed for them until you ask Tariq.</p>`;
    if(['ready','processing'].includes(r.state))btn='<button class="subtle" data-vp-fio="feedback">Get feedback</button>';
    if(['uncertain','failed'].includes(r.state))btn='<button class="subtle" data-vp-fio="check">Check Frame.io</button>';
    if(r.frameio?.view_url)btn+=` <a class="subtle" href="${esc(r.frameio.view_url)}" target="_blank" rel="noopener">Open in Frame.io</a>`}
  if(c.state==='connected'&&d&&(!r||['checked_absent','blocked'].includes(r.state)))btn='<button class="subtle" data-vp-fio="send">Send to Frame.io</button>';
  return `<section class="vp-rel vp-fio" aria-label="Frame.io review"><h3>Frame.io review <small>optional</small></h3>${body}${vpFioBusy?'<span class="hint">Working…</span>':btn}</section>`}
let vpFioList=null;
function vpFioDest(v){
  if(!vpFioList)return '<button class="subtle" data-vp-fio-list>Show my Frame.io projects</button>';
  if(vpFioList.error)return `<p class="hint">${esc(vpFioList.error)}</p>`;
  const opts=[];(vpFioList.workspaces||[]).forEach(w=>(w.projects||[]).forEach(pr=>{
    opts.push({label:`${w.workspace_name} / ${pr.project_name} (top of the project)`,v:{account_id:vpFioList.account_id,workspace_id:w.workspace_id,workspace_name:w.workspace_name,project_id:pr.project_id,project_name:pr.project_name,folder_id:pr.root_folder_id}});
    (pr.folders||[]).forEach(f=>opts.push({label:`${w.workspace_name} / ${pr.project_name} / ${f.name}`,v:{account_id:vpFioList.account_id,workspace_id:w.workspace_id,workspace_name:w.workspace_name,project_id:pr.project_id,project_name:pr.project_name,folder_id:f.folder_id,folder_name:f.name}}))}));
  vpFioList._opts=opts;
  return `<label class="vp-fio-pick">Review folder <select data-vp-fio-pick>${opts.map((o,i)=>`<option value="${i}">${esc(o.label)}</option>`).join('')}</select></label> <button class="subtle" data-vp-fio-use>Use this folder</button><p class="hint">Only folders that already exist. Nothing is created or shared.</p>`}
document.addEventListener('click',async e=>{
  if(!current)return;const pid=current.id;
  if(e.target.closest('[data-vp-fio-list]')){try{vpFioList=await api('/api/frameio/destinations')}catch(err){vpFioList={error:err.message}}renderDetail();return}
  if(e.target.closest('[data-vp-fio-use]')&&vpFioList?._opts){const o=vpFioList._opts[+document.querySelector('[data-vp-fio-pick]').value];if(!o||!o.v.folder_id)return toast('That project has no folder id',true);
    if(!confirm(`Use ${o.label} as the private review folder?`))return;
    try{await api('/api/frameio/destination',{destination:o.v});vpFio[pid]=await api(`/api/projects/${encodeURIComponent(pid)}/frameio`);toast('Review folder saved')}catch(err){toast(err.message,true)}renderDetail()}
});
document.addEventListener('click',async e=>{
  const b=e.target.closest('[data-vp-fio]');if(!b||!current||vpFioBusy)return;const pid=current.id,act=b.dataset.vpFio;
  if(act==='send'&&!confirm('Upload the newest checked export to your private Frame.io review folder? It is not published or shared.'))return;
  vpFioBusy=true;renderDetail();
  try{vpFio[pid]=await api(`/api/projects/${encodeURIComponent(pid)}/frameio/${act}`,{});toast(act==='send'?'Sent to Frame.io review':act==='feedback'?'Feedback read':'Checked Frame.io')}
  catch(err){toast(err.message,true)}finally{vpFioBusy=false;if(current?.id===pid)renderDetail()}
});
// Release readiness (studio/release_ready.py): the real state of each part and the one next step, read from the workflow.
let vpRel={},vpRelBusy='';
const VP_REL_MARK={done:'Done',review:'For your review',todo:'To do',blocked:'Blocked',waiting:'Waiting'};
// One fetch of the release check per visit. A refresh keeps what is on screen until the new answer comes back.
function vpRelFetch(p,refresh){
  if(p.id in vpRel&&!refresh)return;if(!(p.id in vpRel))vpRel[p.id]=null;
  const pid=p.id,n=vpRelFetch.n=(vpRelFetch.n||0)+1;
  api(`/api/projects/${encodeURIComponent(pid)}/release`).then(v=>{if(n!==vpRelFetch.n&&vpRel[pid])return;vpRel[pid]=v;if(current?.id===pid)vpRedraw()})
    .catch(e=>{if(!vpRel[pid]||vpRel[pid].error)vpRel[pid]={error:e.message};if(current?.id===pid)vpRedraw()});
}
function vpRedraw(){const p=current;if(!p)return;if(vpPiece(p)==='edit'){const row=document.querySelector('.vp-pieces');if(row)row.outerHTML=vpPiecesRow(p);if(vpEditCurrent(p)==='export')vpEditRender();return}renderDetail()}
// The approve and make buttons for one item of the release check, wherever that item is shown.
function vpRelAct(p,r,i){{if(vpRelBusy===i.key)return '<span class="hint">Working…</span>';
    if(i.action==='approve_export'&&r.export_candidate)return `<button class="subtle" data-vp-rel="approve-export" data-path="${esc(r.export_candidate)}">Approve this export</button>`;
    if(i.action==='draft_packaging')return '<button class="subtle" data-vp-rel="draft">Ask Sam to draft</button>';
    if(i.key==='chapters'&&r.chapters_text)return `<details class="vp-rel-text"><summary>${r.chapters_preview?'Read the preview':'Read the chapters and description'}</summary><pre>${esc(r.chapters_text)}</pre>${r.chapters_flags.length?`<ul>${r.chapters_flags.map(f=>`<li>${esc(f)}</li>`).join('')}</ul>`:''}<button class="subtle" data-vp-rel="chapters">Make again</button></details>`;
    if(i.action==='preview_chapters')return '<button class="subtle" data-vp-rel="chapters">Preview chapters</button>';
    if(i.action==='approve_description')return `<details class="vp-rel-text"><summary>Read Sam's draft</summary><pre>${esc(r.description)}</pre>${r.cta?`<p><b>CTA</b> ${esc(r.cta)}</p>`:''}<button class="subtle" data-vp-rel="approve-description" data-digest="${esc(r.description_sha)}">Approve this description</button></details>`;
    if(i.action==='approve_cta')return `<details class="vp-rel-text"><summary>Read the CTA</summary><p>${esc(r.cta)}</p><button class="subtle" data-vp-rel="approve-cta" data-digest="${esc(r.cta_sha)}">Approve this CTA</button></details>`;
    if(i.action==='open_publishing')return '<a class="subtle" href="/publishing" target="_blank" rel="noopener">Open Publishing</a>';
    if(i.key==='date'&&i.state!=='done')return '<span class="vp-rel-time"><input type="time" data-vp-rel-time aria-label="Publish time"><input data-vp-rel-tz value="Asia/Dubai" aria-label="Time zone" size="10"><button class="subtle" data-vp-rel="publish-time">Set time</button></span>';
    return ''}}
document.addEventListener('click',async e=>{
  const b=e.target.closest('[data-vp-rel]');if(!b||!current)return;const pid=current.id,act=b.dataset.vpRel;
  if(act==='approve-export'&&!confirm(`Approve ${b.dataset.path.split(/[\\/]/).pop()} as the final export for release?`))return;
  const body=act==='approve-export'?{path:b.dataset.path}:(act==='approve-description'||act==='approve-cta')?{digest:b.dataset.digest}:act==='publish-time'?{time:document.querySelector('[data-vp-rel-time]')?.value,tz:document.querySelector('[data-vp-rel-tz]')?.value}:{};
  vpRelBusy={chapters:'chapters','approve-export':'export','approve-description':'description','approve-cta':'cta','publish-time':'date',draft:'description'}[act];renderDetail();
  try{const out=await api(`/api/projects/${encodeURIComponent(pid)}/release/${act}`,body);vpRel[pid]=out.release;if(current?.id===pid&&out.project){Object.assign(current,out.project)}toast(act==='draft'?'Sam drafted it for your review':'Saved')}
  catch(err){toast(err.message,true)}finally{vpRelBusy='';if(current?.id===pid)renderDetail()}
});

// The page -----------------------------------------------------------------------
const vpBase=renderDetail;
renderDetail=function(){
  const p=current;if(!p)return vpBase.apply(this,arguments);
  if(tab!==vpTabSet&&VP_FROM_TAB[tab])vpOpen[p.id]=VP_FROM_TAB[tab];
  const k=vpPiece(p);tab=vpTabSet=VP_TAB[k]||'brief';
  vpPush(p,k);
  const r=vpBase.apply(this,arguments);
  vpLayout(p,k);
  if(!vpStatus[p.id])vpLoad(p.id);
  return r;
};
function vpLayout(p,k){
  const ws=$('#workspace');if(!ws||current!==p)return;
  ws.querySelectorAll(':scope>.crumb,:scope>.phase-bar,:scope>.next-step,:scope>section.panel,:scope>.tabs').forEach(e=>e.remove());
  const top=ws.querySelector('.project-top');
  if(top)vpHeader(p,top);
  ws.querySelector('.vp-pieces')?.remove();ws.querySelector('.vp-head')?.remove();
  const bare=['title','thumbnail','script','edit','data'].includes(k),name=(VP_PIECES.find(x=>x[0]===k)||[])[1]||'';
  const back=VP_PARENT[k]?`<button class="link vp-back" data-vp-piece="${VP_PARENT[k]}">‹ Back to ${esc((VP_NAV.find(x=>x[0]===VP_PARENT[k])||[])[1]||'')}</button>`:'';
  (top||ws.firstElementChild)?.insertAdjacentHTML('afterend',vpPiecesRow(p)+(k==='packaging'?'':`<div class="vp-head ${bare?'bare':''}">${back}${bare?'':`<h2>${esc(name)}</h2>`}${vpLockBar(p,k)}</div>`));
  const body=$('#tab-body');if(!body)return;
  body.classList.add('vp-body');
  if(k==='packaging'){body.innerHTML=vpPackaging(p);vpRelFetch(p)}
  else if(k==='title')vpTitleGuard(p);
  else if(k==='notes')body.innerHTML=vpNotes(p);
  else if(k==='edit'){body.innerHTML=vpEdit(p);if(vpEf(p)===null)vpEditLoad(p)}
  else if(k==='data')body.innerHTML=vpData(p);
}
// The header: the confirmed title (starred and locked in), or the idea's working name while there isn't one, and the
// planned publication date. The stage menu and the save note are gone; the stage still changes from the board and from
// Film, Editing and Mark as published, and a save that fails still says so.
function vpHeader(p,top){
  top.querySelectorAll('#save-project,[data-flow],[data-er-open],.subtle,select[data-field=stage],#save-note').forEach(e=>e.remove());
  top.classList.add('vp-top');
  const title=vpLocks(p).title&&vpTitle(p);
  top.querySelector('.vp-heading')?.remove();
  if(title){top.querySelector('.name')?.remove();top.insertAdjacentHTML('afterbegin',`<h1 class="vp-heading"><button class="vp-heading-btn" data-vp-piece="title" title="Open titles">${esc(title)}</button></h1>`)}
  else vpNameBox(top);
  if(!top.querySelector('.vp-when'))top.insertAdjacentHTML('beforeend',`<label class="vp-when ${p.publish_date?'':'empty'}"><span>${p.publish_date?'Publishes':'<i class="pk-dot" aria-hidden="true"></i>Choose a publish date'}</span><input type="date" data-field="publish_date" value="${esc(p.publish_date||'')}" aria-label="Planned publish date"></label>`);
  if(!top.querySelector('[data-vp-publish]')&&!vpPublished(p))top.insertAdjacentHTML('beforeend','<button class="primary" data-vp-publish>Publish</button>');
  if(!top.querySelector('[data-vp-header-time]'))top.insertAdjacentHTML('beforeend',`<label class="vp-time"><input type="time" data-vp-header-time value="${esc(p.release?.publish_time?.time||'')}" aria-label="Publish time"><span>${esc(p.release?.publish_time?.tz||'Asia/Dubai')}</span></label>`);
  const s=vpStatus[p.id];if(s?.folder&&!top.querySelector('[data-vp-folder]'))top.insertAdjacentHTML('beforeend',`<button class="link vp-folder" data-vp-folder>Open folder</button>`);
}
document.addEventListener('change',e=>{const w=e.target.closest?.('.vp-when');if(!w||!current)return;w.classList.toggle('empty',!e.target.value);
  w.querySelector('span').innerHTML=e.target.value?'Publishes':'<i class="pk-dot" aria-hidden="true"></i>Choose a publish date'});
function vpRefresh(){if(!current)return;const k=vpPiece(current);if(['notes','edit','packaging','data'].includes(k)||!document.querySelector('.vp-pieces'))return renderDetail();vpLayout(current,k)}
async function vpLoad(pid){try{vpStatus[pid]=await api(`/api/projects/${encodeURIComponent(pid)}/pieces`)}catch{vpStatus[pid]={}}if(current?.id===pid){const row=document.querySelector('.vp-pieces');if(row){row.outerHTML=vpPiecesRow(current)}const k=vpPiece(current);if(k==='edit')vpEditRender();else vpLayout(current,k)}}
function vpSetLock(k,on){const p=current;const L={...vpLocks(p)};if(on)L[k]=new Date().toISOString().slice(0,19);else delete L[k];p.locks=L;
  if(on&&k==='publish'&&p.stage!=='Published')p.stage='Published';if(on&&k==='edit'&&['Recording','Recorded','Ready to record'].includes(p.stage))p.stage='Editing';
  mark();renderDetail()}

document.addEventListener('click',async e=>{
  if(!current||view==='edit-review')return;
  const pc=e.target.closest('[data-vp-piece]');if(pc){const k=pc.dataset.vpPiece;vpOpen[current.id]=k;vpTakes=null;
    if(k==='packaging'||k==='data')vpRelFetch(current,true);renderDetail();scrollTo(0,0);if(typeof dockRender==='function')dockRender();return}
  const et=e.target.closest('[data-vp-edit-tab]');if(et){const pid=current.id;vpEditTab[pid]=et.dataset.vpEditTab;vpTakes=null;if(et.dataset.vpEditTab==='export')vpRelFetch(current,true);if(et.dataset.vpEditTab==='script'&&typeof ccLoad==='function'){await ccLoad(pid);if(current?.id!==pid)return}vpPush(current,'edit');vpEditRender();return}
  if(e.target.closest('[data-vp-slides]')){const pid=current.id;vpEditBusy=true;vpEditRender();
    try{const t=$('#vp-anim-notes')?.value;if(t!=null)await api(`/api/projects/${encodeURIComponent(pid)}/edit-flow/anim_notes`,{text:t});
      await api(`/api/projects/${encodeURIComponent(pid)}/edit-flow/slides`,{});toast('Tariq is making the slides')}catch(err){toast(err.message,true)}
    finally{vpEditBusy=false}if(current?.id===pid){await efLoad();vpEditRender()}return}
  if(e.target.closest('[data-vp-anim-notes]')){const pid=current.id;
    try{await api(`/api/projects/${encodeURIComponent(pid)}/edit-flow/anim_notes`,{text:$('#vp-anim-notes').value});toast('Notes saved')}catch(err){toast(err.message,true)}
    if(current?.id===pid){await efLoad();vpEditRender()}return}
  if(e.target.closest('[data-vp-build]')){const pid=current.id;vpEditBusy=true;vpEditRender();
    try{await api(`/api/projects/${encodeURIComponent(pid)}/edit-flow/build_again`,{});toast('Tariq is building the cut again')}catch(err){toast(err.message,true)}
    finally{vpEditBusy=false}if(current?.id===pid){await efLoad();vpEditRender()}return}
  if(e.target.closest('[data-vp-go]')){if(vpEditBusy||vpSetupPicking)return;const p=current,pid=p.id,d=vpEf(p)||{},files={...vpSetupFiles(p,d)};
    if(d.processor!=='autocut-v1'){toast('The updated processing service is not loaded yet.',true);return}
    if(d.setup?.state==='running'||d.progress?.state==='running')return;
    if(!files.camera.trim()||!files.mic.trim()){toast('Choose the recorded video and the good microphone audio in Setup',true);return}
    vpEditBusy=true;vpEditRender();
    try{await (typeof autosaveProject==='function'?autosaveProject(pid):null);
      const saved=d.sources?.payload?.files||{};
      if(saved.camera?.path!==files.camera||saved.mic?.path!==files.mic)await api(`/api/projects/${encodeURIComponent(pid)}/edit-flow/sources`,{camera:files.camera,mic:files.mic});
      await api(`/api/projects/${encodeURIComponent(pid)}/edit-flow/go`,{});vpEditTab[pid]='setup';toast('Processing started. Review the result in Clean Cut.')}catch(err){toast(err.message,true)}
    finally{vpEditBusy=false}if(current?.id===pid){await efLoad();vpEditRender()}return}
  const lk=e.target.closest('[data-vp-lock]');if(lk){vpSetLock(lk.dataset.vpLock,true);toast('Locked in');return}
  const ul=e.target.closest('[data-vp-unlock]');if(ul){vpSetLock(ul.dataset.vpUnlock,false);return}
  if(e.target.closest('[data-vp-folder]')){const f=vpStatus[current.id]?.folder;if(f){try{await navigator.clipboard.writeText(f);toast('Folder path copied')}catch{toast(f)}}return}
  const pid=current.id;
  if(e.target.closest('[data-vp-rec-start]')){vpBusy=true;renderDetail();try{await (typeof autosaveProject==='function'?autosaveProject(pid):null);vpStatus[pid]=await api(`/api/projects/${encodeURIComponent(pid)}/recording/start`,{});await refresh();vpReopen(pid);toast('Recording this video')}catch(err){toast(err.message,true)}finally{vpBusy=false;if(current?.id===pid)renderDetail()}return}
  if(e.target.closest('[data-vp-rec-find]')){try{vpTakes={pid,list:await api(`/api/projects/${encodeURIComponent(pid)}/recording/takes`)}}catch(err){toast(err.message,true)}renderDetail();return}
  if(e.target.closest('[data-vp-rec-move]')){if(vpBusy)return;const pick=document.querySelector('input[name=vp-take]:checked')?.value;if(!pick)return;vpBusy=true;renderDetail();
    try{await (typeof autosaveProject==='function'?autosaveProject(pid):null);const r=await api(`/api/projects/${encodeURIComponent(pid)}/recording/done`,{path:pick});vpStatus[pid]=r.status;vpTakes=null;await refresh();vpReopen(pid);vpEditTab[pid]='setup';toast('Recording saved for this video')}
    catch(err){toast(err.message,true)}finally{vpBusy=false;if(current?.id===pid)renderDetail()}return}
});
// Recording checks stay on Recording. A successful recording save advances to Setup above.
document.addEventListener('click',e=>{if(current&&e.target.closest('[data-vp-film],[data-vp-film-check],[data-vp-rec-start],[data-vp-rec-find],[data-vp-rec-move]'))vpEditTab[current.id]='recording'},true);
function vpReopen(pid){const fresh=state.projects.find(x=>x.id===pid);if(fresh&&current?.id===pid&&!dirty)current=structuredClone(fresh)}
// The piece row follows saves (a picked title, new thumbnails, script words) without a full redraw.
document.addEventListener('input',e=>{if(current&&e.target.dataset?.field){clearTimeout(vpRefresh.t);vpRefresh.t=setTimeout(()=>{const row=document.querySelector('.vp-pieces');if(row&&current)row.outerHTML=vpPiecesRow(current)},400)}});

// Packaging ------------------------------------------------------------------------
// The video as a viewer meets it: the main thumbnail with the two test thumbnails beside it, the title, the
// description and the pinned comment. Only what is missing and can be done now is marked, in gold.
function vpThumb(p,i){
  const aid=(p.top_three||[])[i],t=aid&&(p.thumbnails||[]).find(x=>x.asset===aid),main=i===0,letter=i===1?'B':'C';
  if(!t)return `<button class="pk-thumb empty ${main?'main':''}" data-pk-thumb-new>
    ${main?'<span class="pk-need"><i class="pk-dot" aria-hidden="true"></i>Add the main thumbnail</span>':`<span>Add test thumbnail ${letter}</span>`}</button>`;
  const locked=false;  // no lock-in label for thumbnails: being in Main, B or C is the confirmation (16 Nov 2026)
  return `<button class="pk-thumb ${main?'main':''}" data-vp-piece="thumbnail" aria-label="${main?'Main thumbnail':`Test thumbnail ${letter}`}, ${esc(t.name)}. Open thumbnails">
    <img src="/assets/${encodeURIComponent(aid)}?w=${main?1280:640}" alt="" loading="lazy">${main?'':`<span class="pk-tag">${letter}</span>`}${locked?'<span class="pk-locked">✓ Locked in</span>':''}</button>`;
}
function vpPackaging(p){
  if(vpPublished(p))return vpPackagingBody(p).replace(/<i class="pk-dot" aria-hidden="true"><\/i>/g,'');
  return vpPackagingBody(p);
}
function vpPackagingBody(p){
  if(vpRel[p.id]?.youtube)return vpPackagingLive(p,vpRel[p.id]);
  const r=vpRel[p.id],x=vpPackItems(p),title=vpTitle(p),L=vpLocks(p),err=r?.error,item=k=>(r?.items||[]).find(i=>i.key===k)||{};
  let desc,com='';
  if(!r)desc='<p class="pk-muted">Loading the description…</p>';
  else if(err)desc=`<p class="pk-muted">${esc(err)}</p>`;
  else if(r.description)desc=`<p class="pk-text">${esc(r.description)}</p>${x?.description?'':`<div class="pk-needs"><p class="pk-need"><i class="pk-dot" aria-hidden="true"></i>Sam's draft is waiting for your approval.</p>
      <button class="subtle" data-vp-rel="approve-description" data-digest="${esc(r.description_sha)}" ${vpRelBusy?'disabled':''}>Approve the description</button></div>`}`;
  else desc=`<div class="pk-needs"><p class="pk-need"><i class="pk-dot" aria-hidden="true"></i>There's no description yet.</p><button class="subtle" data-vp-rel="draft" ${vpRelBusy?'disabled':''}>${vpRelBusy==='description'?'Sam is drafting…':'Ask Sam to draft it'}</button></div>`;
  // The pinned comment is the CTA with the video's own tracked link.
  if(!r)com='<p class="pk-muted">Loading the pinned comment…</p>';
  else if(!err){const link=(r.links||[]).find(l=>l.source==='pinned_comment'),lm=r.lead_magnet||{},c=item('cta');
    const lmLine=lm.decision==='yes'?`Lead magnet: ${esc(lm.name||'yes, not named yet')}.`:lm.decision==='none'?'No lead magnet for this video.':'';
    if(r.cta)com=`<p class="pk-text">${esc(r.cta)}${link?`\n\n${esc(link.url)}`:''}</p>`;
    if(!x?.cta)com+=`<div class="pk-needs">${c.next?`<p class="pk-need"><i class="pk-dot" aria-hidden="true"></i>${esc(r.cta?c.next:"No comment yet")}</p>`:''}
      ${c.action==='approve_cta'?`<button class="subtle" data-vp-rel="approve-cta" data-digest="${esc(r.cta_sha)}" ${vpRelBusy?'disabled':''}>Approve the comment</button>`:''}</div>`;
    com+=lmLine?`<p class="pk-muted">${esc(lmLine)}</p>`:''}
  return `<article class="pk" aria-label="Packaging">
    <div class="pk-thumbs">${vpThumb(p,0)}<div class="pk-ab">${vpThumb(p,1)}${vpThumb(p,2)}</div></div>
    ${L.title&&title?'':`<div class="pk-title-row">${!title&&vpPublished(p)?`<button class="pk-title" data-vp-piece="title" aria-label="${esc(p.name)}. Open titles">${esc(p.name)}</button>`:title?`<button class="pk-title" data-vp-piece="title" aria-label="${esc(title)}. Open titles">${esc(title)}</button>${L.title?'<span class="pk-locked inline">✓ Locked in</span>':''}`
      :'<button class="pk-title empty" data-vp-piece="title"><i class="pk-dot" aria-hidden="true"></i>Choose a title</button>'}</div>`}
    ${title&&!L.title&&!vpPublished(p)?'<p class="pk-need small"><i class="pk-dot" aria-hidden="true"></i>Starred but not locked in yet. Open the titles to lock it in.</p>':''}
    <section class="pk-block" id="pk-desc" aria-labelledby="pk-desc-h"><h3 id="pk-desc-h">Description</h3>${desc}</section>
    <section class="pk-block" id="pk-comment" aria-labelledby="pk-com-h"><h3 id="pk-com-h">Pinned comment</h3>${com}</section>
    ${vpPublishRow(p)}
  </article>`;
}
// What is left before it can go to the Publishing page, read from the release check. Upload is the Publishing page's
// own step and the saved YouTube credential is checked there, so neither counts here.
function vpPublished(p){return p.stage==='Published'||!!vpLocks(p).publish}
// A published video read back from YouTube: the title and description are YouTube's own, not the drafts.
function vpPackagingLive(p,r){
  const y=r.youtube,day=d=>d?new Date(d).toLocaleDateString(undefined,{day:'numeric',month:'short',year:'numeric'}):'';
  return `<article class="pk" aria-label="Packaging">
    <div class="pk-thumbs">${vpThumb(p,0)}<div class="pk-ab">${vpThumb(p,1)}${vpThumb(p,2)}</div></div>
    <div class="pk-title-row"><button class="pk-title" data-vp-piece="title" aria-label="${esc(y.title)}. Open titles">${esc(y.title)}</button></div>
    <section class="pk-block" id="pk-desc" aria-labelledby="pk-desc-h"><h3 id="pk-desc-h">Description</h3><p class="pk-text">${esc(y.description||'')}</p>
      <p class="pk-muted">From YouTube, ${esc(day(y.read_at))}.</p></section>
    <div class="pk-pub done"><span>Published ${esc(day(y.published_at))}</span><a class="button subtle" href="${esc(y.url)}" target="_blank" rel="noopener">Watch on YouTube</a></div>
  </article>`;
}
// The foot of Packaging: the publish time and the way to Publishing. Open Publishing stays disabled until the release
// check (studio/release_ready.py) has nothing open, exactly as before. What is still open shows as short links to the
// place it is done. Upload, scheduling and the YouTube approval stay on the Publishing page.
function vpPublishOpen(p){
  const r=vpRel[p.id];if(!r||r.error||!r.items)return null;
  const x=vpPackItems(p);
  return r.items.filter(i=>i.key!=='upload'&&!(i.key==='publisher'&&i.state==='review')).filter(i=>i.key==='title'?!x.title:i.key==='thumbnail'?!x.thumbnail:i.state!=='done');
}
function vpPublishRow(p){return ''}

document.addEventListener('click',e=>{const g=e.target.closest('[data-vp-edit-go]');if(!g||!current)return;
  vpOpen[current.id]='edit';vpEditTab[current.id]=g.dataset.vpEditGo;vpRelFetch(current,true);renderDetail();scrollTo(0,0)});
document.addEventListener('click',e=>{
  if(!current)return;
  const sc=e.target.closest('[data-pk-scroll]');if(sc){const t=document.querySelector(sc.dataset.pkScroll);if(t){t.scrollIntoView({behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'auto':'smooth',block:'start'});(t.querySelector('button')||t).focus?.({preventScroll:true})}return}
  // Opens the thumbnail library and its three positions. There is no separate creator step any more (16 Nov 2026).
  if(e.target.closest('[data-pk-thumb-new]')){vpOpen[current.id]='thumbnail';renderDetail();scrollTo(0,0);return}
});
// A locked title can't be changed by a star, an edit or a removal (the server refuses it too). Unlocking is how Josh
// changes his mind. New ideas can still be added.
function vpTitleGuard(p){
  if(!vpLocks(p).title)return;
  const why='The title is locked in. Unlock it to change it.';
  document.querySelectorAll('[data-choose-title],[data-remove-title]').forEach(b=>{b.disabled=true;b.title=why});
  const t=document.querySelector(`[data-title-text="${CSS.escape(p.chosen_title||'')}"]`);if(t){t.readOnly=true;t.title=why}
  const list=document.querySelector('.title-list');if(list&&!document.querySelector('.vp-title-locked'))list.insertAdjacentHTML('beforebegin','<p class="vp-title-locked">The starred title is locked in. Unlock it above to choose another one or change its words. You can still add new ideas.</p>');
}
document.addEventListener('click',e=>{
  if(!current||!vpLocks(current).title)return;
  if(e.target.closest('[data-choose-title],[data-remove-title]')){e.stopImmediatePropagation();e.preventDefault();toast('The title is locked in. Unlock it to change it.',true)}
},true);

// Back and Forward ------------------------------------------------------------------
// Each piece and edit tab is a history entry, so the browser's Back and Forward move between them.
function vpPush(p,k){
  if(vpNoPush)return;
  const st={vp:p.id,k,et:k==='edit'?(vpEditTab[p.id]||null):null},h=history.state;
  if(h&&h.vp===st.vp&&h.k===st.k&&h.et===st.et)return;
  history.pushState(st,'');
}
function vpLeaveReview(){if(view==='edit-review'){view='board';if(typeof erPid!=='undefined'){erPid=null;erData=null}}}
addEventListener('popstate',e=>{
  const s=e.state;
  if(s&&s.vp&&state.projects.some(x=>x.id===s.vp)){
    vpNoPush=true;
    try{vpLeaveReview();vpOpen[s.vp]=s.k||'packaging';if(s.et)vpEditTab[s.vp]=s.et;
      if(current?.id!==s.vp){openProject(s.vp);if(current?.id===s.vp){vpOpen[s.vp]=s.k||'packaging';tab=vpTabSet;renderDetail()}}else{tab=vpTabSet;renderDetail()}}
    finally{vpNoPush=false}
    scrollTo(0,0);return}
  if(s&&s.er&&typeof openEditReview==='function'){vpNoPush=true;try{openEditReview(s.er,s.at||0)}finally{vpNoPush=false}return}
  if(current||view==='edit-review'){vpLeaveReview();current=null;render();scrollTo(0,0)}
});
// Every time a video opens from the board, calendar or a desk it starts on Packaging (a desk link to a tab still
// wins, because it sets the tab after opening).
const vpOpenBase=openProject;
openProject=function(id){if(!vpNoPush)vpOpen[id]='packaging';return vpOpenBase.apply(this,arguments)};
// The app's own Back to board button is a history entry too, so Forward can return to the video.
document.addEventListener('click',e=>{if(e.target.closest('[data-action=back]')&&current)setTimeout(()=>{if(!current)history.pushState({board:true},'')},0)},true);

// The animation pictures from the review, read only. A part without a picture yet still opens the review.
let vpAnimDeck={};
function vpAnimStrip(p){
  if(!(p.id in vpAnimDeck)){vpAnimDeck[p.id]=null;api(`/api/projects/${encodeURIComponent(p.id)}/edit-review`).then(v=>{vpAnimDeck[p.id]=v;if(current?.id===p.id&&vpPiece(current)==='edit')vpEditRender()}).catch(e=>{vpAnimDeck[p.id]={error:e.message};if(current?.id===p.id&&vpPiece(current)==='edit')vpEditRender()})}
  const d=vpAnimDeck[p.id];if(!d)return '<p class="hint">Loading the animations…</p>';if(d.error)return `<p class="hint">${esc(d.error)}</p>`;
  const parts=(d.parts||[]).filter(x=>x.kind!=='section'),img=n=>`/api/projects/${encodeURIComponent(p.id)}/edit-review/img/${encodeURIComponent(n)}?v=${encodeURIComponent(d.rendered||'')}`;
  const anims=parts.map((x,i)=>({x,i})).filter(o=>o.x.kind==='animation');
  if(!anims.length)return '';
  return `<div class="vp-anims">${anims.map(({x,i})=>`<button class="vp-anim" data-vp-anim-go="${i}"><img src="${x.images?.length?img(x.images.at(-1)):'/edit-standin.webp'}" alt="" loading="lazy"><b>Animation ${esc(x.n)}</b><small>${esc((x.script||'').slice(0,60))}${(x.script||'').length>60?'…':''}</small></button>`).join('')}</div>
    ${d.stale||anims.some(o=>!(o.x.images||[]).length)?'<p class="hint">Some pictures are missing or out of date. The review takes them again when it opens.</p>':''}`;
}
document.addEventListener('click',e=>{const b=e.target.closest('[data-vp-anim-go]');if(!b||!current||typeof openEditReview!=='function')return;openEditReview(current.id,+b.dataset.vpAnimGo)});
// A picture that can't load says so instead of showing a broken image.
document.addEventListener('error',e=>{const im=e.target;if(!(im instanceof HTMLImageElement)||!im.hasAttribute('data-vp-pic'))return;
  const a=im.closest('a')||im;a.outerHTML='<span class="vp-pic-missing">This picture is missing from the edit folder</span>'},true);

// The video's name wraps onto more lines on a narrow screen instead of being cut off at the edge. It stays one line
// of text: Enter finishes editing and pasted line breaks become spaces.
function vpNameBox(top){
  const nm=top.querySelector('input.name[data-field=name]');if(!nm)return;
  const ta=document.createElement('textarea');ta.className='name vp-name';ta.dataset.field='name';ta.rows=1;ta.value=nm.value;ta.spellcheck=nm.spellcheck;
  ta.setAttribute('aria-label',nm.getAttribute('aria-label')||'Video name');if(nm.maxLength>0)ta.maxLength=nm.maxLength;
  nm.replaceWith(ta);vpGrow(ta);requestAnimationFrame(()=>vpGrow(ta));
}
function vpGrow(t){t.style.height='auto';t.style.height=t.scrollHeight+'px'}
document.addEventListener('keydown',e=>{if(e.key==='Enter'&&e.target.classList?.contains('vp-name')){e.preventDefault();e.target.blur()}});
document.addEventListener('input',e=>{const t=e.target;if(!t.classList?.contains('vp-name'))return;if(/[\r\n]/.test(t.value)){const at=t.selectionStart;t.value=t.value.replace(/[\r\n]+/g,' ');t.setSelectionRange(at,at)}vpGrow(t)},true);
addEventListener('resize',()=>{const t=document.querySelector('.vp-name');if(t)vpGrow(t)});

// Video data ---------------------------------------------------------------------
// What is saved for this video, read only: the numbers Sam saved with the date they are from, the thumbnail test
// screenshots and the retention graph. Nothing here is fetched live or refreshed on a timer, because no per-video feed
// exists yet; each figure shows the date it was saved for. The full results editor (funnel, notes, Sam's review) is
// one link away and unchanged.
let vpRes={};
const VP_NUMS=[['views','Views'],['impressions','Impressions'],['ctr','Click-through rate','%'],['avg_percent_viewed','Average viewed','%'],
  ['avg_view_duration','Average view','s'],['subscribers','Subscribers'],['emails','Email signups'],['customers','Customers'],['revenue','Revenue','$']];
function vpResLoad(p){
  if(p.id in vpRes)return;vpRes[p.id]=null;const pid=p.id;
  api('/api/results/'+encodeURIComponent(pid)).then(v=>{vpRes[pid]=v;if(current?.id===pid&&vpPiece(current)==='data')renderDetail()})
    .catch(e=>{vpRes[pid]={error:e.message};if(current?.id===pid&&vpPiece(current)==='data')renderDetail()});
}
function vpNum(v,unit){if(unit==='%')return `${Number(v).toLocaleString(undefined,{maximumFractionDigits:1})}%`;
  if(unit==='s'){const s=Math.round(v);return `${Math.floor(s/60)}:${String(s%60).padStart(2,'0')}`}
  if(unit==='$')return `$${Number(v).toLocaleString()}`;return Number(v).toLocaleString()}
function vpDay(d){return new Date(d+'T12:00').toLocaleDateString(undefined,{day:'numeric',month:'short',year:'numeric'})}
function vpData(p){
  vpResLoad(p);const r=vpRes[p.id];
  if(!r)return '<div class="vd"><p class="hint">Loading this video\'s data…</p></div>';
  if(r.error)return `<div class="vd"><p class="hint">${esc(r.error)}</p></div>`;
  const m=r.metrics||{},nums=VP_NUMS.filter(([k])=>m[k]!=null&&m[k]!=='');
  const shots=(r.tests||[]).filter(t=>t.screenshot),ret=r.retention||{};
  const due=p.publish_date?new Date(new Date(p.publish_date+'T12:00').getTime()+5*864e5).toISOString().slice(0,10):'';
  const today=new Date().toISOString().slice(0,10);
  return `<div class="vd">
    <section class="vd-block" aria-labelledby="vd-n"><header><h3 id="vd-n">Numbers</h3>${nums.length?`<span class="vd-when">${m.as_of?`As of ${esc(vpDay(m.as_of))}`:'Date not saved'}</span>`:''}</header>
      ${nums.length?`<dl class="vd-nums">${nums.map(([k,l,u])=>`<div><dt>${esc(l)}</dt><dd>${esc(vpNum(m[k],u))}</dd></div>`).join('')}</dl>`:'<p class="vd-empty">No numbers saved for this video yet.</p>'}</section>
    <section class="vd-block" aria-labelledby="vd-t"><header><h3 id="vd-t">Thumbnail tests</h3></header>
      ${shots.length?`<div class="vd-shots">${shots.map(t=>`<figure><a href="/assets/${encodeURIComponent(t.screenshot)}" target="_blank" rel="noopener"><img src="/assets/${encodeURIComponent(t.screenshot)}?w=960" alt="Thumbnail test round ${esc(t.round)}" loading="lazy"></a><figcaption>Round ${esc(t.round)}${t.winner?`, ${esc(t.winner)} won`:''}</figcaption></figure>`).join('')}</div>`:'<p class="vd-empty">No thumbnail test screenshots yet.</p>'}</section>
    <section class="vd-block" aria-labelledby="vd-r"><header><h3 id="vd-r">Retention</h3></header>
      ${ret.screenshot?`<figure class="vd-ret"><a href="/assets/${encodeURIComponent(ret.screenshot)}" target="_blank" rel="noopener"><img src="/assets/${encodeURIComponent(ret.screenshot)}?w=1400" alt="Retention graph" loading="lazy"></a>${ret.summary?`<figcaption>${esc(ret.summary)}</figcaption>`:''}</figure>`
        :`<p class="vd-empty">${due&&due>today?`The retention graph is for ${esc(vpDay(due))}, five days after publishing.`:'No retention graph yet.'}</p>`}</section>
    <p class="vd-more"><button class="link" data-vp-piece="results">Open all results</button></p>
  </div>`;
}
// Opening Video data or All results reads the saved results again, so what Sam saved since shows.
document.addEventListener('click',e=>{const b=e.target.closest('[data-vp-piece="data"],[data-vp-piece="results"]');if(b&&current)Reflect.deleteProperty(vpRes,current.id)},true);

/* Focused release review. Opening/closing never prepares, approves, or uploads. */
let vpPublishDialog=null,vpPublishGeneration=0,vpPublishBusy=false;
function vpPublishClose(){
  if(vpPublishBusy)return;
  ++vpPublishGeneration;
  if(vpPublishDialog){vpPublishDialog.close();vpPublishDialog.remove();vpPublishDialog=null}
  document.querySelector('[data-vp-publish]')?.focus();
}
function vpPublishMissing(value){return value?'':' missing'}
function vpPublishControls(){
  const d=vpPublishDialog;if(!d)return;
  const v=d._preview,answers=[...d.querySelectorAll('[data-disclosure]')];
  const answered=answers.every(s=>s.value==='true'||s.value==='false');
  for(const b of d.querySelectorAll('[data-vp-publish-mode]')){
    const gaps=v?.blockers?.[b.dataset.vpPublishMode];
    b.disabled=vpPublishBusy||!v||!v.completed_edit||!answered||!Array.isArray(gaps)||gaps.length>0;
  }
  d.querySelector('[data-vp-publish-close]').disabled=vpPublishBusy;
  answers.forEach(s=>s.disabled=vpPublishBusy);
}
function vpPublishRender(d,v,p){
  d._preview=v;
  const missing=k=>vpPublishMissing(v[k]);
  const commentLabel=v.comment_delivery==='manual'?'Pinned comment · manual':'Pinned comment';
  const block=!v.completed_edit?'There isn’t a completed edit yet.':v.blockers?.unlisted?.[0]||'';
  d.innerHTML=`<header class="vp-publish-head"><h2>Publish</h2><button class="subtle" data-vp-publish-close aria-label="Close publish panel">×</button></header>
    <div class="vp-publish-scroll"><div class="vp-publish-image${missing('thumbnail_url')}">${v.thumbnail_url?`<img src="${esc(v.thumbnail_url)}" alt="Selected thumbnail">`:'<span>No thumbnail yet</span>'}</div>
    <h3 class="vp-publish-title${missing('title')}">${esc(v.title||'No title yet')}</h3>
    <div class="vp-publish-channel"><span class="vp-publish-avatar">J</span><b>Josh</b></div>
    <section class="vp-publish-description${missing('description')}" aria-label="Description">${esc(v.description||'No description yet')}</section>
    <section class="vp-publish-comment${missing('pinned_comment')}" aria-label="Pinned comment"><small>${commentLabel}</small><p>${esc(v.pinned_comment||'No comment yet')}</p></section>
    <div class="vp-publish-date${vpPublishMissing(v.publish_date&&v.publish_time)}"><span>${esc(v.publish_date||'No date selected')}</span><span>${esc(v.publish_time||'No time selected')} ${esc(v.timezone||'')}</span></div>
    <div class="vp-publish-disclosures">${[['made_for_kids','Made for kids'],['contains_paid_promotion','Paid promotion'],['contains_synthetic_media','Altered / synthetic content']].map(([key,label])=>`<label>${label}<select data-disclosure="${key}" aria-label="${label}"><option value="">Select</option><option value="false" ${v.disclosures?.[key]===false?'selected':''}>No</option><option value="true" ${v.disclosures?.[key]===true?'selected':''}>Yes</option></select></label>`).join('')}</div></div>
    <footer class="vp-publish-footer"><p data-vp-publish-notice role="status">${esc(block)}</p><div><button class="subtle" data-vp-publish-mode="unlisted" disabled>Upload unlisted</button><button class="primary" data-vp-publish-mode="schedule" disabled>Schedule</button></div></footer>`;
  d.querySelector('[data-vp-publish-close]').onclick=vpPublishClose;
  d.querySelectorAll('[data-disclosure]').forEach(s=>s.onchange=vpPublishControls);
  d.querySelectorAll('[data-vp-publish-mode]').forEach(b=>b.onclick=()=>vpPublishAction(b.dataset.vpPublishMode));
  vpPublishControls();
}
async function vpPublishShow(){
  if(!current||vpPublishDialog)return;
  const p=current,pid=p.id,generation=++vpPublishGeneration;
  const d=document.createElement('dialog');d.className='vp-publish-dialog';d.setAttribute('aria-label','Publish video');vpPublishDialog=d;
  d.innerHTML='<header class="vp-publish-head"><h2>Publish</h2><button class="subtle" data-vp-publish-close aria-label="Close publish panel">×</button></header><p class="vp-publish-loading">Loading…</p>';
  d.querySelector('[data-vp-publish-close]').onclick=vpPublishClose;
  d.addEventListener('cancel',e=>{e.preventDefault();vpPublishClose()});
  d.addEventListener('click',e=>{if(e.target===d){const r=d.getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)vpPublishClose()}});
  document.body.append(d);d.showModal();d._pid=pid;
  try{
    if(typeof autosaveProject==='function')await autosaveProject(pid);
    const v=await api('/api/projects/'+encodeURIComponent(pid)+'/publish-preview');
    if(generation!==vpPublishGeneration||current?.id!==pid||vpPublishDialog!==d)return;
    if(!v||!Number.isInteger(v.revision)||!v.blockers)throw new Error('The publish preview is unavailable. Nothing was uploaded.');
    vpPublishRender(d,v,p);
  }catch(e){if(vpPublishDialog===d)d.querySelector('.vp-publish-loading').textContent=e.message}
}
async function vpPublishAction(mode){
  const d=vpPublishDialog,v=d?._preview;if(!d||!v||vpPublishBusy)return;
  const button=d.querySelector('[data-vp-publish-mode="'+mode+'"]');if(!button||button.disabled)return;
  const payload={mode,revision:v.revision,preview_digest:v.preview_digest};
  for(const s of d.querySelectorAll('[data-disclosure]'))payload[s.dataset.disclosure]=s.value==='true';
  vpPublishBusy=true;vpPublishControls();
  const note=d.querySelector('[data-vp-publish-notice]');note.textContent='Preparing…';
  try{
    const job=await api('/api/projects/'+encodeURIComponent(d._pid)+'/publish-preview',payload);
    if(job.packet?.title!==v.title||job.packet?.description!==v.description||job.packet?.pinned_comment!==v.pinned_comment)throw new Error('The video changed. Close and review it again.');
    if(!job.review_nonce)throw new Error('The review could not be authorised. Nothing was uploaded.');
    const result=await api('/api/publishing/approve',{job_id:job.id,packet_digest:job.packet.packet_digest,review_nonce:job.review_nonce});
    note.textContent=result.error||'Upload requested.';
    d._preview.blockers={unlisted:['Upload already requested.'],schedule:['Upload already requested.']};
  }catch(e){note.textContent=e.message}finally{vpPublishBusy=false;vpPublishControls()}
}
document.addEventListener('click',e=>{if(e.target.closest('[data-vp-publish]'))vpPublishShow()});
addEventListener('popstate',vpPublishClose);


document.addEventListener('change',async e=>{
  if(!e.target.matches('[data-vp-header-time]')||!current||!e.target.value)return;
  const p=current;
  try{await autosaveProject(p.id);const out=await api('/api/projects/'+encodeURIComponent(p.id)+'/release/publish-time',{time:e.target.value,tz:p.release?.publish_time?.tz||'Asia/Dubai'});
    if(current?.id===p.id&&out.project)Object.assign(current,out.project);vpRel[p.id]=out.release;
  }catch(err){toast(err.message,true)}
});
