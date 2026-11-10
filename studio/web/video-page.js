'use strict';
// The video page, stripped back. A video is a row of pieces, each ticked once it's locked in:
// title, thumbnail, script, recording, edit, animations and publish, in any order, plus one
// notes box. Clicking a piece opens just that piece underneath. The existing title, thumbnail,
// script and results views are reused inside their pieces; everything else around them goes.
const VP_PIECES=[['notes','Notes'],['title','Title'],['thumbnail','Thumbnail'],['script','Script'],['recording','Recording'],['edit','Edit'],['publish','Publish']];
const VP_TAB={notes:'brief',title:'titles',thumbnail:'thumbnails',script:'script',publish:'results',recording:'brief',edit:'brief',animations:'brief'};
let vpOpen={},vpStatus={},vpTakes=null,vpBusy=false;
if(typeof fields!=='undefined'&&!fields.includes('locks'))fields.push('locks');

function vpLocks(p){return p.locks||{}}
function vpTitle(p){return (p.titles||[]).find(t=>t.id===p.chosen_title)?.text||''}
function vpDone(p,k){
  const L=vpLocks(p),s=vpStatus[p.id]||{};
  if(k==='notes')return null;
  if(k==='recording')return !!(L.recording||s.recording);
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
function vpDefault(p){return VP_PIECES.map(x=>x[0]).find(k=>k!=='notes'&&!vpDone(p,k))||'publish'}
// An old saved 'animations' piece opens Edit, where the animations now live.
function vpOpenKey(p){const k=vpOpen[p.id];return k==='animations'?'edit':k}
function vpPiece(p){return vpOpenKey(p)||vpDefault(p)}

function vpPiecesRow(p){
  const open=vpPiece(p);
  return `<nav class="vp-pieces" aria-label="Pieces of this video">${VP_PIECES.map(([k,l])=>{const d=vpDone(p,k);
    return `<button class="vp-piece ${k===open?'on':''} ${d?'done':''} ${k==='notes'?'notes':''}" data-vp-piece="${k}" aria-pressed="${k===open}">
      <span class="vp-mark" aria-hidden="true">${k==='notes'?'✎':d?'✓':''}</span><span class="vp-piece-text"><b>${l}</b><small>${esc(vpSub(p,k))}</small></span></button>`}).join('')}</nav>`;
}
function vpLockBar(p,k){
  if(k==='notes')return '';
  const L=vpLocks(p),locked=!!L[k],auto=vpDone(p,k)&&!locked;
  const label={title:'title',thumbnail:'thumbnail',script:'script',recording:'recording',edit:'edit',animations:'animations',publish:'video as published'}[k];
  if(k==='recording'||k==='animations'||k==='edit')return '';
  return `<div class="vp-lock">${locked?`<span class="vp-locked">✓ Locked in ${esc(new Date(L[k]).toLocaleDateString(undefined,{day:'numeric',month:'short'}))}</span><button class="subtle" data-vp-unlock="${k}">Unlock</button>`
    :auto?'':`<button class="primary" data-vp-lock="${k}" ${k==='title'&&!vpTitle(p)?'disabled title="Pick a title first"':''}>${k==='edit'?'Hand the edit to Tariq':k==='publish'?'Mark as published':`Lock in the ${label}`}</button>`}</div>`;
}

// Pieces with their own view ---------------------------------------------------
function vpNotes(p){return `<label class="vp-notes"><span class="sr-only">Notes</span><textarea data-field="notes" rows="14" placeholder="Anything you want to jot down about this video as you go">${esc(p.notes||'')}</textarea></label>`}
function vpRecording(p){
  const s=vpStatus[p.id]||{};
  if(s.recording)return `<div class="vp-card"><h3>Recorded</h3><p>The take is in the video's folder as <b>${esc(s.recording.name)}</b>.</p>${s.folder?`<p class="hint">${esc(s.folder)}\\Recorded\\Input</p>`:''}</div>`;
  const takes=vpTakes&&vpTakes.pid===p.id?vpTakes.list:null;
  return `<div class="vp-card">
    ${s.active?`<h3>Recording this video now</h3><p>When you've stopped recording in OBS, press It's recorded. Josh OS finds today's take in E:\\Videos\\Input, moves it into this video's folder and names it after the video.</p>`
      :`<h3>Ready when you are</h3><p>Open OBS and everything else, then press I'm recording this now. When you stop, press It's recorded.</p>`}
    <div class="vp-actions">${s.active?'':`<button class="primary" data-vp-rec-start ${vpBusy?'disabled':''}>I'm recording this now</button>`}<button class="${s.active?'primary':'subtle'}" data-vp-rec-find ${vpBusy?'disabled':''}>It's recorded</button></div>
    ${takes?(takes.length?`<div class="vp-takes"><p>Today's recordings in E:\\Videos\\Input, newest first. Pick this video's take.</p>${takes.map((t,i)=>`<label class="vp-take"><input type="radio" name="vp-take" value="${esc(t.path)}" ${i===0?'checked':''}><span><b>${esc(t.name)}</b><small>${t.size_mb.toLocaleString()} MB, saved ${esc(new Date(t.modified).toLocaleTimeString(undefined,{hour:'numeric',minute:'2-digit'}))}${t.settling?', still being written':''}${t.also.length?`. The ${esc(t.also.join(', '))} stays in Input.`:''}</small></span></label>`).join('')}
      <button class="primary" data-vp-rec-move ${vpBusy?'disabled':''}>${vpBusy?'Moving it…':'Move it into this video'}</button></div>`:'<p class="hint">No recordings from today in E:\\Videos\\Input yet.</p>'):''}
  </div>`;
}
// Edit belongs to Tariq. Three tabs: he sets the edit up (steps fill in as he goes, with pictures), then the
// script with his cuts for Josh to check, then the animations. Each tab fills in once he reaches it.
let vpEditTab={},vpEditBusy=false;
const VP_EDIT_TABS=[['setup','Setting up'],['script','Script'],['animations','Animations']];
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
  const k=vpEditStage(p);return k==='script'?'script':['animations','done'].includes(k)?'animations':'setup';
}
function vpEdit(p){
  const d=vpEf(p);
  if(!d)return `<div class="vp-card"><p class="hint">Loading the edit…</p></div>`;
  const stage=vpEditStage(p),open=vpEditCurrent(p),order=['start','setup','script','animations','done'];
  const reached={setup:true,script:order.indexOf(stage)>=2||!!d.proposal,animations:order.indexOf(stage)>=3};
  return `<div class="vp-edit">
    <nav class="vp-etabs" aria-label="Edit stages">${VP_EDIT_TABS.map(([k,l],i)=>`<button class="vp-etab ${k===open?'on':''} ${reached[k]?'':'later'}" data-vp-edit-tab="${k}" aria-pressed="${k===open}"><span>${i+1}</span>${l}</button>`).join('')}</nav>
    <div class="vp-epane">${open==='setup'?vpEditSetup(p,d):open==='script'?vpEditScript(p,d):vpEditAnimations(p,d)}</div>
  </div>`;
}
function vpEditSetup(p,d){
  const su=d.setup;
  if(!su)return `<div class="vp-card vp-start"><h3>Tariq does the edit</h3>
    <p>He finds your camera and mic, sets up his own Premiere project with you keyed, masked and centred, then transcribes the recording and marks the silences, repeats and retakes. You watch each step here, then check his cuts on the Script tab.</p>
    <p class="hint">You can also ask him in the chat. Premiere opens if it isn't open, but never while OBS is.</p>
    <div class="vp-actions"><button class="primary" data-vp-go ${vpEditBusy?'disabled':''}>Start the edit</button></div></div>`;
  return vpPhase(p,su,{running:'Tariq is setting up the edit',done:'The setup is done',failed:'Tariq stopped',needs_you:'Tariq needs you',interrupted:'Tariq stopped'},
    su.state==='running'?'':`<button class="subtle" data-vp-go ${vpEditBusy?'disabled':''}>Start again</button>`,
    su.state==='done'?'<div class="vp-actions"><button class="primary" data-vp-edit-tab="script">Check the script</button></div>':'');
}
// One run of Tariq's steps: each waits greyed out, fills while he works on it and ticks green when done.
function vpPhase(p,su,heads,action,foot){
  const icon={waiting:'',running:'',done:'✓',problems:'!',failed:'✕',skipped:'–'};
  const img=n=>`/api/projects/${encodeURIComponent(p.id)}/edit-flow/setup/${encodeURIComponent(n)}`;
  return `<div class="vp-card vp-setup">
    <div class="vp-setup-head"><h3>${esc(heads[su.state]||heads.running)}</h3>${action||''}</div>
    ${su.message&&su.state!=='running'?`<p class="vp-phase-msg ${esc(su.state)}">${esc(su.message)}</p>`:''}
    <ol class="vp-steps">${su.steps.map(st=>`<li class="vp-step ${esc(st.state)}${st.fraction!=null&&st.state==='running'?' measured':''}">
      <span class="vp-step-mark" aria-hidden="true">${icon[st.state]||''}</span>
      <div class="vp-step-main"><div class="vp-step-top"><b>${esc(st.label)}</b><span class="vp-step-state">${{waiting:'Waiting',running:st.fraction!=null?Math.round(st.fraction*100)+'%':'Working',done:'Done',problems:'Check this',failed:'Stopped',skipped:'Not needed'}[st.state]||''}</span></div>
        <div class="vp-bar"><i ${st.fraction!=null&&st.state==='running'?`style="width:${Math.max(4,Math.round(st.fraction*100))}%"`:''}></i></div>
        ${st.message?`<p>${esc(st.message)}</p>`:''}
        ${st.images?.length?`<div class="vp-step-pics">${st.images.map(n=>`<a href="${img(n)}" target="_blank" rel="noopener"><img alt="${esc(st.label)}" src="${img(n)}?t=${encodeURIComponent(st.at||'')}"></a>`).join('')}</div>`:''}
      </div></li>`).join('')}</ol>
    ${foot||''}
  </div>`;
}
function vpEditScript(p,d){
  if(!d.proposal&&!(d.suggest?.ambiguous))return `<div class="vp-card vp-later"><h3>Nothing here yet</h3><p>Tariq puts the script here once he has transcribed the recording and marked his cuts. You can watch him get there on the Setting up tab.</p></div>`;
  const st=Object.fromEntries((d.status||[]).map(x=>[x.key,x])),bu=d.phases?.build;
  let build='';
  if(d.proposal?.approved&&bu&&(bu.state==='running'||st.cut?.state==='verified'||bu.state!=='done'))
    build=vpPhase(p,bu,{running:'Tariq is building your cut in Premiere',done:'The cut is built',failed:'The cut did not build',interrupted:'The build stopped'},
      bu.state==='running'?'':st.cut?.state==='verified'?'<button class="primary" data-vp-edit-tab="animations">Go to the animations</button>':`<button class="primary" data-vp-build ${vpEditBusy?'disabled':''}>Build it again</button>`);
  else if(d.proposal?.approved&&st.cut?.state!=='verified')
    build=`<div class="vp-card"><h3>Your script is approved</h3><p>${st.cut&&!['waiting','requested','running'].includes(st.cut.state)?`The last build stopped. ${esc(st.cut.reason||'')}`:'Tariq builds it in Premiere as a new sequence, CLEAN_CUT, from his own project.'}</p>
      <div class="vp-actions"><button class="primary" data-vp-build ${vpEditBusy?'disabled':''}>Build the cut in Premiere</button></div></div>`;
  return `${build}<div id="ef-embed">${efInner(true)}</div>`;
}
let vpAnimSeen={};
function vpEditAnimations(p,d){
  const st=Object.fromEntries((d.status||[]).map(x=>[x.key,x])),s=vpStatus[p.id]||{},ph=d.phases||{};
  const cutOk=st.cut?.state==='verified',vis=st.visuals,an=ph.animate,fi=ph.final,making=an?.state==='running';
  const anim=an&&an.state!=='done'?vpPhase(p,an,{running:'Tariq is making the slides and shot list',failed:'Tariq stopped on the slides',interrupted:'Tariq stopped on the slides'},
    making?'':`<button class="subtle" data-vp-slides ${vpEditBusy?'disabled':''}>Try again</button>`):'';
  const deck=s.deck?`<div class="vp-card"><h3>${vis?.state==='approved'?'Storyboard approved':'The slides are ready to review'}</h3>
      <p>${esc(s.deck.slides)} and ${esc(s.deck.shot_list||'no shot list yet')}. Review them as a slide show. ${cutOk?'Approve the storyboard and Tariq puts it all together and exports it.':'You can approve the storyboard once the cut is built on the Script tab.'}</p>
      <div class="vp-actions"><button class="${vis?.state==='approved'?'subtle':'primary'}" data-er-open="${esc(p.id)}">Review the animations</button>
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
function vpPublishTop(p){return `<label class="vp-date">Publish date<input type="date" data-field="publish_date" value="${esc(p.publish_date||'')}"></label>`}

// The page -----------------------------------------------------------------------
const vpBase=renderDetail;
renderDetail=function(){
  const p=current;if(!p)return vpBase.apply(this,arguments);
  const k=vpPiece(p);tab=VP_TAB[k]||'brief';
  const r=vpBase.apply(this,arguments);
  vpLayout(p,k);
  if(!vpStatus[p.id])vpLoad(p.id);
  return r;
};
function vpLayout(p,k){
  const ws=$('#workspace');if(!ws||current!==p)return;
  ws.querySelectorAll(':scope>.crumb,:scope>.phase-bar,:scope>.next-step,:scope>section.panel,:scope>.tabs').forEach(e=>e.remove());
  const top=ws.querySelector('.project-top');
  if(top){top.querySelectorAll('#save-project,[data-flow],[data-er-open],.subtle').forEach(e=>e.remove());top.classList.add('vp-top');
    const sel=top.querySelector('select[data-field=stage]');if(sel)sel.classList.add('vp-stage');
    const s=vpStatus[p.id];if(s?.folder&&!top.querySelector('[data-vp-folder]'))top.insertAdjacentHTML('beforeend',`<button class="link vp-folder" data-vp-folder>Open folder</button>`)}
  ws.querySelector('.vp-pieces')?.remove();ws.querySelector('.vp-head')?.remove();
  (top||ws.firstElementChild)?.insertAdjacentHTML('afterend',vpPiecesRow(p)+`<div class="vp-head ${['title','thumbnail','script'].includes(k)?'bare':''}">${['title','thumbnail','script'].includes(k)?'':`<h2>${esc(VP_PIECES.find(x=>x[0]===k)[1])}</h2>`}${vpLockBar(p,k)}</div>`);
  const body=$('#tab-body');if(!body)return;
  body.classList.add('vp-body');
  if(k==='notes')body.innerHTML=vpNotes(p);
  else if(k==='recording')body.innerHTML=vpRecording(p);
  else if(k==='edit'){body.innerHTML=vpEdit(p);if(vpEf(p)===null)vpEditLoad(p)}
  else if(k==='publish')body.insertAdjacentHTML('afterbegin',vpPublishTop(p));
}
function vpRefresh(){if(!current)return;const k=vpPiece(current);if(['notes','recording','edit'].includes(k)||!document.querySelector('.vp-pieces'))return renderDetail();vpLayout(current,k)}
async function vpLoad(pid){try{vpStatus[pid]=await api(`/api/projects/${encodeURIComponent(pid)}/pieces`)}catch{vpStatus[pid]={}}if(current?.id===pid){const row=document.querySelector('.vp-pieces');if(row){row.outerHTML=vpPiecesRow(current)}const k=vpPiece(current);if(k==='edit')vpEditRender();else if(k==='recording')renderDetail();else vpLayout(current,k)}}
function vpSetLock(k,on){const p=current;const L={...vpLocks(p)};if(on)L[k]=new Date().toISOString().slice(0,19);else delete L[k];p.locks=L;
  if(on&&k==='publish'&&p.stage!=='Published')p.stage='Published';if(on&&k==='edit'&&['Recording','Recorded','Ready to record'].includes(p.stage))p.stage='Editing';
  mark();renderDetail()}

document.addEventListener('click',async e=>{
  if(!current||view==='edit-review')return;
  const pc=e.target.closest('[data-vp-piece]');if(pc){if(typeof leave==='function'&&dirty){/* autosave keeps edits */}vpOpen[current.id]=pc.dataset.vpPiece;vpTakes=null;renderDetail();if(typeof dockRender==='function')dockRender();return}
  const et=e.target.closest('[data-vp-edit-tab]');if(et){vpEditTab[current.id]=et.dataset.vpEditTab;vpEditRender();return}
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
  if(e.target.closest('[data-vp-go]')){const pid=current.id;vpEditBusy=true;vpEditRender();
    try{await api(`/api/projects/${encodeURIComponent(pid)}/edit-flow/go`,{});if(!vpLocks(current).edit)vpSetLock('edit',true);vpEditTab[pid]='setup';toast('Tariq has started the edit')}catch(err){toast(err.message,true)}
    finally{vpEditBusy=false}if(current?.id===pid){await efLoad();vpEditRender()}return}
  const lk=e.target.closest('[data-vp-lock]');if(lk){vpSetLock(lk.dataset.vpLock,true);toast('Locked in');return}
  const ul=e.target.closest('[data-vp-unlock]');if(ul){vpSetLock(ul.dataset.vpUnlock,false);return}
  if(e.target.closest('[data-vp-folder]')){const f=vpStatus[current.id]?.folder;if(f){try{await navigator.clipboard.writeText(f);toast('Folder path copied')}catch{toast(f)}}return}
  const pid=current.id;
  if(e.target.closest('[data-vp-rec-start]')){vpBusy=true;renderDetail();try{await (typeof autosaveProject==='function'?autosaveProject(pid):null);vpStatus[pid]=await api(`/api/projects/${encodeURIComponent(pid)}/recording/start`,{});await refresh();vpReopen(pid);toast('Recording this video')}catch(err){toast(err.message,true)}finally{vpBusy=false;if(current?.id===pid)renderDetail()}return}
  if(e.target.closest('[data-vp-rec-find]')){try{vpTakes={pid,list:await api(`/api/projects/${encodeURIComponent(pid)}/recording/takes`)}}catch(err){toast(err.message,true)}renderDetail();return}
  if(e.target.closest('[data-vp-rec-move]')){const pick=document.querySelector('input[name=vp-take]:checked')?.value;if(!pick)return;vpBusy=true;renderDetail();
    try{await (typeof autosaveProject==='function'?autosaveProject(pid):null);const r=await api(`/api/projects/${encodeURIComponent(pid)}/recording/done`,{path:pick});vpStatus[pid]=r.status;vpTakes=null;await refresh();vpReopen(pid);toast('Recording moved into the video')}
    catch(err){toast(err.message,true)}finally{vpBusy=false;if(current?.id===pid)renderDetail()}return}
});
function vpReopen(pid){const fresh=state.projects.find(x=>x.id===pid);if(fresh&&current?.id===pid&&!dirty)current=structuredClone(fresh)}
// The piece row follows saves (a picked title, new thumbnails, script words) without a full redraw.
document.addEventListener('input',e=>{if(current&&e.target.dataset?.field){clearTimeout(vpRefresh.t);vpRefresh.t=setTimeout(()=>{const row=document.querySelector('.vp-pieces');if(row&&current)row.outerHTML=vpPiecesRow(current)},400)}});
