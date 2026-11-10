'use strict';
// The video page, stripped back. A video is a row of pieces, each ticked once it's locked in:
// title, thumbnail, script, recording, edit, animations and publish, in any order, plus one
// notes box. Clicking a piece opens just that piece underneath. The existing title, thumbnail,
// script and results views are reused inside their pieces; everything else around them goes.
const VP_PIECES=[['notes','Notes'],['title','Title'],['thumbnail','Thumbnail'],['script','Script'],['recording','Recording'],['edit','Edit'],['animations','Animations'],['publish','Publish']];
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
    case 'edit':return vpLocks(p).edit?'With Tariq':s.edit?'Premiere file found':'Waiting for your edit';
    case 'animations':return vpLocks(p).animations?'Approved':s.deck?'Deck ready to review':'Not made yet';
    case 'publish':return p.publish_date?new Date(p.publish_date+'T12:00').toLocaleDateString(undefined,{weekday:'short',day:'numeric',month:'short'}):'No date';
  }
}
function vpDefault(p){return VP_PIECES.map(x=>x[0]).find(k=>k!=='notes'&&!vpDone(p,k))||'publish'}
function vpPiece(p){return vpOpen[p.id]||vpDefault(p)}

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
  if(k==='recording'||k==='animations')return '';
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
function vpEdit(p){
  const s=vpStatus[p.id]||{};
  return `<div class="vp-card"><h3>${vpLocks(p).edit?'The edit is with Tariq':'Your edit'}</h3>
    ${s.edit?`<p>Josh OS found your Premiere project <b>${esc(s.edit.name)}</b>, saved ${esc(new Date(s.edit.saved).toLocaleString(undefined,{day:'numeric',month:'short',hour:'numeric',minute:'2-digit'}))}.</p>`
      :`<p>Do the cuts, sound and green screen, then save your Premiere project in this video's <b>video</b> folder. It shows here when it's there.</p>${s.folder?`<p class="hint">${esc(s.folder)}\\video</p>`:''}`}
    ${vpLocks(p).edit?`<p>Tariq makes the animations from here. You review them in Animations.</p>`:''}
    <div class="vp-actions"><button class="subtle" data-ef-open="${esc(p.id)}">Tariq's clean cut</button></div></div>`;
}
function vpAnimations(p){
  const s=vpStatus[p.id]||{};
  return `<div class="vp-card"><h3>${vpLocks(p).animations?'Animations approved':s.deck?'The animation deck is ready':'No animation deck yet'}</h3>
    ${s.deck?`<p>${esc(s.deck.slides)} and ${esc(s.deck.shot_list||'no shot list yet')}.</p><div class="vp-actions"><button class="primary" data-er-open="${esc(p.id)}">Review the animations</button></div>`
      :`<p>Once your edit is handed over, Tariq makes the slides and shot list. You can review them here as a slide show.</p><div class="vp-actions"><button class="subtle" data-dock-ask="Make the animation slides and shot list for this video.">Ask Tariq to make them</button></div>`}</div>`;
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
  else if(k==='edit')body.innerHTML=vpEdit(p);
  else if(k==='animations')body.innerHTML=vpAnimations(p);
  else if(k==='publish')body.insertAdjacentHTML('afterbegin',vpPublishTop(p));
}
function vpRefresh(){if(!current)return;const k=vpPiece(current);if(['notes','recording','edit','animations'].includes(k)||!document.querySelector('.vp-pieces'))return renderDetail();vpLayout(current,k)}
async function vpLoad(pid){try{vpStatus[pid]=await api(`/api/projects/${encodeURIComponent(pid)}/pieces`)}catch{vpStatus[pid]={}}if(current?.id===pid){const row=document.querySelector('.vp-pieces');if(row){row.outerHTML=vpPiecesRow(current)}const k=vpPiece(current);if(['recording','edit','animations'].includes(k))renderDetail();else vpLayout(current,k)}}
function vpSetLock(k,on){const p=current;const L={...vpLocks(p)};if(on)L[k]=new Date().toISOString().slice(0,19);else delete L[k];p.locks=L;
  if(on&&k==='publish'&&p.stage!=='Published')p.stage='Published';if(on&&k==='edit'&&['Recording','Recorded','Ready to record'].includes(p.stage))p.stage='Editing';
  mark();renderDetail()}

document.addEventListener('click',async e=>{
  if(!current||view==='edit-review')return;
  const pc=e.target.closest('[data-vp-piece]');if(pc){if(typeof leave==='function'&&dirty){/* autosave keeps edits */}vpOpen[current.id]=pc.dataset.vpPiece;vpTakes=null;renderDetail();return}
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
