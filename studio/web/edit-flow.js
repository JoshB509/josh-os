'use strict';
// Tariq's clean cut: the whole transcript with what he cut and kept, a queue of things to check with
// short before-and-after clips, and the approvals that move the edit into Premiere and on to animations.
let efPid=null,efData=null,efCard=null,efCtx=2,efBusy=false,efPoll=null,efUndo=[],efRedo=[],efPrev={};

const EF_STATE={ready:'Ready',approved:'Approved',review:'Ready for you',running:'Working',waiting:'Waiting',needs_input:'Needs you',
  stale:'Out of date',failed:'Failed',blocked:'Blocked',verified:'Built',requested:'Asked',not_started:'Not started',exported:'Exported'};
const efUrl=a=>`/api/projects/${encodeURIComponent(efPid)}/edit-flow${a?'/'+a:''}`;
const efSec=(n,sr=16000)=>{const s=n/sr;return `${Math.floor(s/60)}:${String(Math.floor(s%60)).padStart(2,'0')}`};

async function openEditFlow(pid){
  if(typeof leave==='function'&&!leave())return;
  efPid=pid;efData=null;efCard=null;efUndo=[];efRedo=[];efPrev={};current=null;view='edit-flow';
  $('#workspace').innerHTML='<p class="hint">Loading the edit…</p>';
  await efLoad();scrollTo(0,0);
}
async function efLoad(){
  try{efData=await api(efUrl())}catch(e){toast(e.message,true);return}
  renderEditFlow();
  clearTimeout(efPoll);
  if(efData.progress?.state==='running')efPoll=setTimeout(()=>{if(view==='edit-flow')efLoad()},3000);
}
async function efPost(action,body,quiet){
  efBusy=true;renderEditFlow();
  try{const r=await api(efUrl(action),body);if(!quiet)toast('Saved');return r}
  catch(e){toast(e.message,true);return null}
  finally{efBusy=false}
}

function efStages(){
  return `<ol class="ef-stages">${(efData.status||[]).map(s=>`<li class="ef-stage ${esc(s.state)}" title="${esc(s.reason)}"><b>${esc(s.label)}</b><span>${esc(EF_STATE[s.state]||s.state)}</span></li>`).join('')}</ol>`;
}

function efSources(){
  const sg=efData.suggest||{},src=efData.sources,f=src?.payload?.files||{};
  const opts=(list,cur)=>list.map(v=>`<option value="${esc(v)}" ${v===cur?'selected':''}>${esc(v.split(/[\\/]/).pop())}</option>`).join('');
  const prog=efData.progress;
  return `<section class="ef-card ef-sources">
    <h2>The recording</h2>
    <p>Pick the camera file and the good mic. Tariq never edits these. Every cut is rebuilt from them.${sg.ambiguous&&(sg.videos?.length||sg.audio?.length)?' There is more than one candidate, so choose the right pair.':''}</p>
    <label>Camera<select id="ef-cam"><option value="">Choose…</option>${opts(sg.videos||[],f.camera?.path)}</select></label>
    <label>Good mic<select id="ef-mic"><option value="">Choose…</option>${opts(sg.audio||[],f.mic?.path)}</select></label>
    <p class="hint">${esc(sg.folder||'This video has no folder yet.')}</p>
    <div class="ef-row">
      <button class="subtle" data-ef-sources ${efBusy?'disabled':''}>${src?'Change the recording':'Use these files'}</button>
      ${src?`<label class="ef-check"><input type="checkbox" id="ef-editorial" checked> Tariq reads it for retakes and pauses</label>
      <button class="primary" data-ef-analyse ${efBusy||prog?.state==='running'?'disabled':''}>${efData.proposal?'Propose the cut again':'Ask Tariq to propose the cut'}</button>`:''}
    </div>
    ${prog?`<p class="ef-progress ${esc(prog.state)}">${esc(prog.message)}${prog.state==='running'?' <button class="link" data-ef-cancel>Stop</button>':''}</p>`:''}
  </section>`;
}

function efTranscript(){
  let html='';
  for(const it of efData.transcript){
    if(it.type==='word')html+=`<span class="ef-w ${it.state}${it.by_josh?' josh':''}" data-w="${it.id}">${esc(it.text)}</span> `;
    else{
      const on=efCard===it.id?' on':'';
      const tag=it.kind==='pause'?`${it.label}${it.state==='shortened'&&it.removed_ms>=50?` cut ${(it.removed_ms/1000).toFixed(1)}s`:''}`:it.label;
      html+=`<button class="ef-ev ${esc(it.kind)} ${esc(it.state)}${it.review?' flag':''}${on}" data-ef-ev="${esc(it.id)}">${esc(tag)}</button> `;
    }
  }
  return `<div class="ef-script" id="ef-script">${html}</div>`;
}

function efCardHtml(){
  const c=(efData.cards||[]).find(x=>x.event_id===efCard)||null;
  const ev=efData.transcript.find(x=>x.id===efCard);
  if(!c&&!ev)return `<div class="ef-cardview empty"><p>Pick a flagged moment from the list, or select words in the transcript to remove or restore them.</p></div>`;
  const kind=(c||ev).kind,label=(c||ev).label,act=c?c.action:ev.action;
  const pv=efPrev[efCard+':'+efCtx];
  const btn=(a,t)=>`<button class="${act===a?'primary':'subtle'}" data-ef-act="${a}">${t}</button>`;
  let actions='';
  if(kind==='pause')actions=`${btn('keep','Keep the pause')}<label class="ef-target">Shorten to <input id="ef-ms" type="number" min="0" step="50" value="${Math.min(600,ev?.duration_ms||600)}"> ms</label><button class="subtle" data-ef-act="shorten">Shorten</button>`;
  else actions=`${btn('keep','Keep')}${btn('remove','Cut it')}`;
  return `<div class="ef-cardview">
    <div class="ef-card-head"><b>${esc(label)}</b><span>${efSec((c||ev).start)}${ev?` · ${(ev.duration_ms/1000).toFixed(2)}s`:''}</span></div>
    ${c?.text?`<p class="ef-quote">“${esc(c.text)}”</p>`:''}
    <p>${esc(c?.reason||'')}</p>
    ${c?.uncertainty?.label==='unverified'?'<p class="hint">Not a verified label. Listen before deciding.</p>':''}
    <div class="ef-listen">
      ${pv?`<div><small>Original</small>${efWave(pv.original_peaks,pv.removed_in_view,pv.context_s*2+(pv.event_in_original[1]-pv.event_in_original[0]))}<audio controls preload="none" src="${efUrl('audio/'+pv.original)}"></audio></div>
      <div><small>Your cut</small>${efWave(pv.proposed_peaks,[],0)}<audio controls preload="none" src="${efUrl('audio/'+pv.proposed)}"></audio></div>
      <p class="hint">${esc(pv.label)}</p>`:`<button class="subtle" data-ef-listen>Make the clips</button>`}
      <div class="ef-ctx">Context ${[2,5,10].map(s=>`<button class="${efCtx===s?'on':''}" data-ef-ctx="${s}">${s}s</button>`).join('')}</div>
    </div>
    <div class="ef-row">${actions}<button class="link" data-ef-act="default">Back to Tariq's choice</button></div>
  </div>`;
}
function efWave(peaks,cuts,total){
  if(!peaks?.length)return '';
  const w=300,h=40,step=w/peaks.length,max=Math.max(...peaks,.001);
  const bars=peaks.map((p,i)=>`<rect x="${(i*step).toFixed(1)}" y="${(h/2-p/max*h/2).toFixed(1)}" width="${Math.max(1,step-.4).toFixed(1)}" height="${Math.max(1,p/max*h).toFixed(1)}"/>`).join('');
  const shade=total?(cuts||[]).map(([a,b])=>`<rect class="cut" x="${(a/total*w).toFixed(1)}" y="0" width="${((b-a)/total*w).toFixed(1)}" height="${h}"/>`).join(''):'';
  return `<svg class="ef-wave" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none">${shade}<g>${bars}</g></svg>`;
}

function efQueue(){
  const cards=efData.cards||[];
  if(!cards.length)return '<p class="hint">Nothing flagged. You can still change anything in the transcript.</p>';
  return `<ol class="ef-queue">${cards.map(c=>`<li><button class="ef-qi ${efCard===c.event_id?'on':''} ${c.decided?'done':''}" data-ef-ev="${esc(c.event_id)}">
    <b>${esc(c.label)}</b><span>${efSec(c.start)} · ${c.action==='remove'?'cut':c.action==='shorten'?'shortened':'kept'}${c.decided?' · you decided':''}</span></button></li>`).join('')}</ol>`;
}

function efNext(){
  const st=Object.fromEntries((efData.status||[]).map(s=>[s.key,s])),p=efData.proposal;
  if(!p)return '';
  if(!p.approved){
    return `<section class="ef-card ef-approve">
      ${efData.errors?.length?`<p class="ef-err">${efData.errors.map(esc).join(' ')}</p>`:''}
      <p>Approving revision ${p.revision} lets Tariq build exactly this cut in Premiere, as a new sequence. Your original sequence and files stay as they are.</p>
      <button class="primary" data-ef-approve ${efData.errors?.length||efBusy||!p.latest?'disabled':''}>Approve this script</button>
    </section>`;
  }
  const cut=st.cut,vis=st.visuals,fin=st.final;
  return `<section class="ef-card ef-approve">
    <p>Revision ${p.revision} is approved. ${esc(cut?.reason||'')}</p>
    <div class="ef-row">
      <button class="subtle" data-ef-premiere>Check Premiere</button>
      <button class="${['verified'].includes(cut?.state)?'subtle':'primary'}" data-ef-build ${efBusy?'disabled':''}>Build the cut in Premiere</button>
    </div>
    ${cut?.state==='verified'?`<div class="ef-row"><button class="subtle" data-ef-proof>Prove a key change reaches every piece</button></div>
      <h3>Animations</h3>
      <textarea id="ef-notes" rows="3" placeholder="Anything you have in mind for the animations"></textarea>
      <div class="ef-row"><button class="subtle" data-ef-notes>Save notes</button><button class="subtle" data-er-open="${esc(efPid)}">Review the storyboard</button>
      <button class="${vis?.state==='approved'?'subtle':'primary'}" data-ef-storyboard>Approve the storyboard</button></div>
      ${vis?.state==='approved'?`<button class="primary" data-ef-final>Assemble and render locally</button><p class="hint">${esc(fin?.reason||'')}</p>`:''}`:''}
  </section>`;
}

function renderEditFlow(){
  const project=state.projects.find(p=>p.id===efPid);
  page('Clean cut','');subPage(project?.name||'Video','Clean cut','data-ef-back');
  if(!efData){$('#workspace').innerHTML='<p class="hint">Loading…</p>';return}
  const s=efData.summary,p=efData.proposal,ed=efData.editorial;
  const keep=$('#ef-script')?.scrollTop;
  $('#workspace').innerHTML=`<div class="ef">
    <header class="ef-head"><div><h1>${esc(project?.name||'Clean cut')}</h1>
      ${p?`<p>Revision ${p.revision}. ${s.removed_s}s cut from ${s.source_s}s, leaving ${s.kept_s}s. ${efData.sync?.verified?`Mic and camera in sync (mic ${Number(efData.sync.offset_seconds).toFixed(3)}s ahead).`:'Sync is not verified.'}
        ${ed?.ran?`Tariq marked ${ed.removals} retakes and ${ed.pauses} pauses.`:esc(ed?.note||'')}</p>`:'<p>Tariq proposes the cut, you check it here, and nothing reaches Premiere until you approve it.</p>'}</div>
      ${p?`<div class="ef-row"><button class="subtle" data-ef-undo ${efUndo.length?'':'disabled'}>Undo</button><button class="subtle" data-ef-redo ${efRedo.length?'':'disabled'}>Redo</button></div>`:''}
    </header>
    ${efStages()}
    ${!p||efData.progress?.state==='running'||efData.status?.[0]?.state!=='ready'?efSources():''}
    ${p?`<div class="ef-body">
      <div class="ef-main">
        <div class="ef-legend"><span class="ef-w removed">cut</span> <span class="ef-w josh">your change</span> Select words to cut or restore them. Click a tag to check it.</div>
        ${efTranscript()}
        <div class="ef-selbar" id="ef-selbar" hidden><button class="subtle" data-ef-sel="remove">Cut these words</button><button class="subtle" data-ef-sel="keep">Keep these words</button><button class="link" data-ef-sel="default">Back to Tariq's choice</button></div>
        <div class="ef-notes-box"><textarea id="ef-note" rows="2" placeholder="A note for Tariq on this revision"></textarea><button class="subtle" data-ef-note>Add note</button>
          ${(efData.notes||[]).map(n=>`<p class="hint">${esc(n.text)}</p>`).join('')}</div>
        ${efNext()}
      </div>
      <aside class="ef-rail"><h2>To check (${(efData.cards||[]).length})</h2>${efCardHtml()}${efQueue()}</aside>
    </div>`:''}
  </div>`;
  if(keep!=null&&$('#ef-script'))$('#ef-script').scrollTop=keep;
}

// Changes always make a new revision; undo sends the opposite change as another new revision.
async function efRevise(changes,words,inverse,note){
  const p=efData.proposal;
  const v=await efPost('revise',{proposal_id:p.id,digest:p.digest,changes:changes||[],words:words||[],note:note||''},true);
  if(!v)return false;
  if(inverse){efUndo.push(inverse);efRedo=[]}
  Object.assign(efData,v);efPrev={};renderEditFlow();return true;
}
function efInverseEvent(id){
  const c=(efData.cards||[]).find(x=>x.event_id===id);
  return {changes:[{event_id:id,action:c?.decided?c.action:'default'}],words:[]};
}
function efInverseWords(ids){
  const by={};for(const it of efData.transcript)if(it.type==='word')by[it.id]=it;
  const groups={};for(const id of ids){const w=by[id];const a=!w?.by_josh?'default':w.state==='removed'?'remove':'keep';(groups[a]=groups[a]||[]).push(id)}
  return {changes:[],words:Object.entries(groups).map(([action,ids])=>({ids,action}))};
}
function efSelected(){
  const sel=getSelection();if(!sel||sel.isCollapsed)return [];
  return [...document.querySelectorAll('#ef-script .ef-w')].filter(el=>sel.containsNode(el,true)).map(el=>el.dataset.w);
}

document.addEventListener('click',async e=>{
  if(view!=='edit-flow')return;
  if(e.target.closest('[data-ef-back]')){const pid=efPid;clearTimeout(efPoll);view='board';efPid=null;efData=null;openProject(pid);return}
  const ev=e.target.closest('[data-ef-ev]');if(ev){efCard=ev.dataset.efEv;renderEditFlow();return}
  if(e.target.closest('[data-ef-sources]')){
    const cam=$('#ef-cam').value,mic=$('#ef-mic').value;if(!cam||!mic){toast('Choose the camera and the mic',true);return}
    if(await efPost('sources',{camera:cam,mic})) await efLoad();return}
  if(e.target.closest('[data-ef-analyse]')){
    const r=await efPost('analyse',{sources_id:efData.sources.id,editorial:$('#ef-editorial')?.checked!==false},true);if(r)toast('Tariq has started');await efLoad();return}
  if(e.target.closest('[data-ef-cancel]')){await efPost('cancel',{},true);await efLoad();return}
  const act=e.target.closest('[data-ef-act]');if(act&&efCard){
    const a=act.dataset.efAct,ch={event_id:efCard,action:a};if(a==='shorten')ch.target_ms=+($('#ef-ms')?.value||0);
    const inv=efInverseEvent(efCard);await efRevise([ch],[],inv);return}
  const sel=e.target.closest('[data-ef-sel]');if(sel){
    const ids=efSelected();if(!ids.length)return;const inv=efInverseWords(ids);
    getSelection().removeAllRanges();$('#ef-selbar').hidden=true;await efRevise([],[{ids,action:sel.dataset.efSel}],inv);return}
  if(e.target.closest('[data-ef-undo]')&&efUndo.length){const u=efUndo.pop();
    const redo=u.words.length?efInverseWords(u.words.flatMap(w=>w.ids)):efInverseEvent(u.changes[0].event_id);
    if(await efRevise(u.changes,u.words,null)){efRedo.push(redo);renderEditFlow()}return}
  if(e.target.closest('[data-ef-redo]')&&efRedo.length){const r=efRedo.pop();
    const inv=r.words.length?efInverseWords(r.words.flatMap(w=>w.ids)):efInverseEvent(r.changes[0].event_id);
    if(await efRevise(r.changes,r.words,null)){efUndo.push(inv);renderEditFlow()}return}
  if(e.target.closest('[data-ef-note]')){const t=$('#ef-note').value.trim();if(t)await efRevise([],[],null,t);return}
  const ctx=e.target.closest('[data-ef-ctx]');if(ctx){efCtx=+ctx.dataset.efCtx;renderEditFlow();if(efCard)efListen();return}
  if(e.target.closest('[data-ef-listen]')){efListen();return}
  if(e.target.closest('[data-ef-approve]')){const p=efData.proposal;
    if(await efPost('approve',{proposal_id:p.id,digest:p.digest},true)){toast('Script approved');await efLoad()}return}
  if(e.target.closest('[data-ef-premiere]')){const r=await efPost('premiere',{},true);if(r)toast(r.ok?'Premiere is ready':'Premiere check: '+(r.notes||[]).join(' '),!r.ok);renderEditFlow();return}
  if(e.target.closest('[data-ef-proof]')){const r=await efPost('proof',{},true);if(r)toast(r.passed?'The key change reached every piece, and the shadow kept its own key':'The proof did not pass. The receipt is in '+r.folder,!r.passed);renderEditFlow();return}
  if(e.target.closest('[data-ef-build]')){const p=efData.proposal;const r=await efPost('build',{proposal_id:p.id,digest:p.digest},true);
    if(r)toast(r.payload?.state==='verified'?'The cut is built and checked':r.payload?.error||(r.payload?.problems||[]).join(' ')||'Not built',r.payload?.state!=='verified');await efLoad();return}
  if(e.target.closest('[data-ef-notes]')||e.target.closest('[data-ef-storyboard]')){
    const cut=await efLatestCut();if(!cut)return;let board=null;
    if(e.target.closest('[data-ef-storyboard]')){try{const er=await api(`/api/projects/${encodeURIComponent(efPid)}/edit-review`);board=er.rendered?`${er.slides}|${er.rendered}`:null}catch{}
      if(!board){toast('Take the storyboard pictures in the animation review first',true);return}}
    if(await efPost('visuals',{cut_id:cut,notes:$('#ef-notes')?.value||null,storyboard_digest:board,approve:!!board},true)){toast(board?'Storyboard approved':'Notes saved');await efLoad()}return}
  if(e.target.closest('[data-ef-final]')){const v=await efLatest('visuals');if(!v)return;const r=await efPost('final',{visuals_id:v},true);if(r)toast(r.payload?.state==='exported'?'Exported. Check the file.':r.payload?.error||'Not exported',r.payload?.state!=='exported');await efLoad();return}
});
async function efLatestCut(){return efLatest('cut')}
async function efLatest(kind){const id=efData[kind+'_id'];if(!id)toast('Nothing to work from yet',true);return id||null}
async function efListen(){
  if(!efCard)return;const p=efData.proposal;
  const r=await efPost('preview',{proposal_id:p.id,event_id:efCard,context_s:efCtx},true);
  if(r){efPrev[efCard+':'+efCtx]=r;renderEditFlow()}
}
document.addEventListener('mouseup',()=>{
  if(view!=='edit-flow')return;const bar=$('#ef-selbar');if(!bar)return;bar.hidden=!efSelected().length;
});

// Routing, and a way in from the video page's Edit piece.
const efBaseRender=render;
render=function(){if(view==='edit-flow'&&!current)return renderEditFlow();return efBaseRender.apply(this,arguments)};
document.addEventListener('click',e=>{const b=e.target.closest('[data-ef-open]');if(b){e.preventDefault();openEditFlow(b.dataset.efOpen)}});
