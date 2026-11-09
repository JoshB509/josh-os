'use strict';
// The video page: a phase bar, a brief instead of a form, thumbnails you drag into place,
// and a script studio where Sam works beside the document.
const PHASES=[['Idea',['Idea']],['Preparing',['Scripting']],['Recording',['Ready to record','Recording','Recorded']],['Editing',['Editing','Review']],['Scheduled',['Scheduled']],['Published',['Published']]];
const PACKAGING=[['concrete_payoff','The promise','What the viewer walks away with, in their world.'],['curiosity_question','The curiosity question','The question the title and thumbnail make them need answered.'],['thumbnail_concept','Thumbnail concept','The picture and the 2 to 4 words on it.']];

function phaseBar(p){
  const at=PHASES.findIndex(([,s])=>s.includes(p.stage));
  return `<nav class="phase-bar" aria-label="Where this video is">${PHASES.map(([name,stages],i)=>`<button class="phase ${i<at?'done':''} ${i===at?'now':''}" data-phase="${esc(stages[0])}" aria-current="${i===at?'step':'false'}"><i>${i<at?'✓':i+1}</i><span>${esc(name)}${i===at&&stages.length>1?`<small>${esc(YT_SUB[p.stage]||p.stage)}</small>`:''}</span></button>`).join('')}</nav>`;
}

// Overview: the brief
brief=function(){
  const p=current,thumb=p.top_three[0]||p.thumbnails[0]?.asset,title=p.titles.find(t=>t.id===p.chosen_title)?.text;
  const pk=p.packaging||{};
  return `<div class="brief">
    <section class="brief-main">
      <div class="brief-block"><h3>The angle</h3><p class="hint">Who this is for, the pain it starts from and what they'll get.</p>
        <textarea class="doc-field" data-field="notes" rows="6" placeholder="e.g. For the expert who keeps getting sent away for more detail. They'll leave with the two sentences that get an executive to approve their idea.">${esc(p.notes)}</textarea></div>
      <div class="brief-cards">${PACKAGING.map(([k,l,h])=>`<label class="brief-card"><b>${l}</b><small>${h}</small><textarea rows="3" data-packaging="${k}">${esc(pk[k]||'')}</textarea></label>`).join('')}</div>
    </section>
    <aside class="glance">
      <button class="glance-thumb" data-tab="thumbnails">${thumb?`<img src="/assets/${esc(thumb)}?w=640" alt="Top thumbnail">`:'<span>Add a thumbnail</span>'}</button>
      <div class="glance-title"><small>Title</small>${title?`<b>${esc(title)}</b>`:`<b class="muted">${esc(p.name)}</b>`}<button class="subtle small" data-tab="titles">${title?'Change':'Choose a title'}</button></div>
      <label class="glance-date">Publish date<input type="date" data-field="publish_date" value="${esc(p.publish_date)}"></label>
      <ul class="glance-checks">${ytChecks(p).map(([k,ok,t])=>`<li class="${ok?'ok':''}"><button data-tab="${k==='Script'?'script':k==='Title'?'titles':'thumbnails'}"><i>${ok?'✓':''}</i><b>${k}</b><span>${esc(t)}</span></button></li>`).join('')}</ul>
    </aside>
  </div>
  <details class="more-actions"><summary>More actions</summary><div class="danger-zone row"><button class="subtle" data-action="export">Export project JSON</button><button class="subtle ${p.archived?'':'danger'}" data-action="archive">${p.archived?'Restore to board':'Archive project'}</button><span class="hint">Archiving keeps the script, titles, thumbnails and runs.</span></div></details>`;
};

// Script studio: the document on the left, Sam on the right.
let samThread=[],samStatus=null,samSel=null,samBusy=false;
script=function(){
  setTimeout(()=>{loadWriting(current?.id);samLoad()},0);
  return `<div class="studio"><div class="studio-doc">${writingMarkup(current)}</div>
    <aside class="sam-panel" id="sam-panel" aria-label="Work on the script with Sam">${samPanel()}</aside></div>`;
};
function samAgent(){return team?.agents?.find(a=>a.id==='sam')}
function samPanel(){
  const a=samAgent()||{name:'Sam',color:'#2383e2',role:'YouTube Channel Manager',photo:''};
  const off=samStatus&&!samStatus.available;
  const quick=samSel?['Tighten this','Make this a stronger signpost','Turn this into a Parker line','Rewrite in Ed\'s rhythm']:['Review my hook','Check every signpost','Where does it drag?','Write the close'];
  return `<header class="sam-head" style="--agent:${esc(a.color)}">${teamAvatar(a,'sm')}<div><b>${esc(a.name)}</b><small>${off?'Offline on this PC':samBusy?'Working on it…':'Using your script-touch-up skill'}</small></div></header>
    <div class="sam-thread" id="sam-thread">${samThread.length?samThread.map(samMessage).join(''):`<div class="sam-empty"><p>Highlight any part of the script and tell me what you want. I'll rewrite it in your house style and you choose whether to use it. You can also paste screenshots here, and ask me for anything else.</p></div>`}
      ${samBusy?'<div class="sam-msg agent typing"><span></span><span></span><span></span></div>':''}</div>
    ${off?`<p class="sam-off">${esc(samStatus.help)}</p>`:''}
    <div class="sam-compose">
      ${samSel?`<div class="sam-sel"><span>Working on “${esc(samSel.text.slice(0,70))}${samSel.text.length>70?'…':''}”</span><button class="subtle small" data-sam-clear aria-label="Clear selection">✕</button></div>`:'<p class="sam-tip">Tip: select text in the script to work on just that part.</p>'}
      <div class="sam-quick">${quick.map(q=>`<button class="chip-btn" data-sam-quick="${esc(q)}" ${off||samBusy?'disabled':''}>${esc(q)}</button>`).join('')}</div>
      ${chatAttachStrip('sam')}
      <div class="sam-input">${chatAttachButton('sam',off)}<textarea id="sam-input" rows="2" placeholder="${samSel?'What should Sam do with this part?':'Ask Sam about the script'}" ${off?'disabled':''}></textarea><button class="primary" data-sam-send ${off||samBusy?'disabled':''} aria-label="Send to Sam">Send</button></div>
    </div>`;
}
function samMessage(m){
  if(m.role==='josh')return `<div class="sam-msg josh">${m.selection?`<q>${esc(m.selection.text.slice(0,120))}${m.selection.text.length>120?'…':''}</q>`:''}${chatImagesHtml(m.images)}${m.text?`<p>${esc(m.text)}</p>`:''}</div>`;
  return `<div class="sam-msg agent"><p>${esc(m.text)}</p>${m.saved?.length&&typeof roomSaved==='function'?roomSaved(m.saved):''}${m.replacement!=null?`<div class="sam-suggestion"><small>${m.selection?'Suggested rewrite':'Suggested script'}</small><pre>${esc(m.replacement)}</pre>
    <div class="row"><button class="small primary" data-sam-apply="${esc(m.id)}">${m.selection?'Replace selection':'Replace whole script'}</button><button class="small subtle" data-sam-copy="${esc(m.id)}">Copy</button></div></div>`:''}${m.learned?.length?m.learned.map(l=>`<div class="room-learned">Added to lessons: ${esc(l)}</div>`).join(''):''}</div>`;
}
function samRender(){const box=$('#sam-panel');if(!box)return;box.innerHTML=samPanel();const t=$('#sam-thread');if(t)t.scrollTop=t.scrollHeight}
async function samLoad(){
  if(!current||tab!=='script')return;const id=current.id;
  try{
    if(!team)team=await api('/api/team');
    [samThread,samStatus]=await Promise.all([api('/api/agents/thread?project=desk&agent=sam'),samStatus?Promise.resolve(samStatus):api('/api/agents/status')]);
    if(current?.id===id&&tab==='script')samRender();
  }catch(e){toast(e.message,true)}
}
async function samSend(message){
  message=(message||'').trim();if(samBusy||!current)return;
  if(chatUploading.sam){toast('Wait a moment for the images to finish adding.',true);return}
  if(!message&&!chatAttach.sam.length)return;
  const doc=$('#script-document');
  if(dirty){clearTimeout(autoTimer);await autosaveProject(current.id);while(autoBusy)await autoBusy}
  if(samSel&&doc&&doc.value.slice(samSel.start,samSel.end)!==samSel.text){samSel=null;toast('The script changed, so select the passage again.',true);samRender();return}
  const images=chatTake('sam');
  const body={agent_id:'sam',project_id:current.id,message,selection:samSel,images};
  samThread=[...samThread,{id:'pending',role:'josh',text:message,selection:samSel,images}];samBusy=true;samRender();
  try{const r=await api('/api/agents/ask',body);samThread=r.messages;if(typeof roomThread!=='undefined')roomThread.sam=r.messages;
    if(r.reply?.saved?.length&&typeof dockRefresh==='function')await dockRefresh(r.reply.saved)}
  catch(e){samThread=samThread.filter(m=>m.id!=='pending');chatAttach.sam=images;toast(e.message,true)}
  finally{samBusy=false;samRender()}
}
function samApply(id){
  const m=samThread.find(x=>x.id===id),doc=$('#script-document');if(!m||m.replacement==null||!doc)return;
  if(m.selection){const {start,end,text}=m.selection;
    if(doc.value.slice(start,end)!==text){const at=doc.value.indexOf(text);if(at<0){toast('That passage has changed since you asked, so it can\'t be replaced automatically. Copy the suggestion instead.',true);return}doc.setRangeText(m.replacement,at,at+text.length,'select')}
    else doc.setRangeText(m.replacement,start,end,'select');}
  else{if(!confirm('Replace the whole script with Sam\'s version? The current one stays in Previous versions.'))return;doc.value=m.replacement}
  doc.dispatchEvent(new Event('input',{bubbles:true}));samSel=null;samRender();doc.focus();toast('Replaced. Saving as a new draft.');
}
function samTrack(){const d=$('#script-document');if(!d)return;const s=d.selectionStart,e=d.selectionEnd;
  const next=e>s&&d.value.slice(s,e).trim()?{start:s,end:e,text:d.value.slice(s,e)}:null;
  if(JSON.stringify(next)!==JSON.stringify(samSel)&&(next||document.activeElement===d)){samSel=next;samRender()}}
['mouseup','keyup','select'].forEach(ev=>document.addEventListener(ev,e=>{if(e.target.id==='script-document')samTrack()}));
document.addEventListener('click',async e=>{
  if(!current||tab!=='script')return;
  const q=e.target.closest('[data-sam-quick]');if(q){samSend(q.dataset.samQuick);return}
  if(e.target.closest('[data-sam-send]')){const i=$('#sam-input');const v=i.value;i.value='';samSend(v);return}
  if(e.target.closest('[data-sam-clear]')){samSel=null;samRender();return}
  const a=e.target.closest('[data-sam-apply]');if(a){samApply(a.dataset.samApply);return}
  const c=e.target.closest('[data-sam-copy]');if(c){const m=samThread.find(x=>x.id===c.dataset.samCopy);try{await navigator.clipboard.writeText(m.replacement);toast('Copied')}catch{toast('Copy failed, select the text instead',true)}}
});
document.addEventListener('keydown',e=>{if(e.target.id==='sam-input'&&e.key==='Enter'&&!e.shiftKey){e.preventDefault();const v=e.target.value;e.target.value='';samSend(v)}});

// Thumbnails: drag files or library images straight into slots 1, 2 and 3.
function thumbsToSlot(asset,i){const arr=current.top_three.filter(a=>a!==asset);arr.splice(Math.min(i,arr.length),0,asset);current.top_three=arr.slice(0,3)}
async function thumbsImport(files){
  const added=[];
  for(const f of files){if(!/^image\/(png|jpeg|webp)$/.test(f.type)){toast(`${f.name} isn't a PNG, JPEG or WebP`,true);continue}
    const data=await new Promise((ok,bad)=>{const r=new FileReader();r.onload=()=>ok(r.result.split(',')[1]);r.onerror=bad;r.readAsDataURL(f)});
    const t=await api('/api/images',{name:f.name,data});if(!current.thumbnails.some(x=>x.asset===t.asset))current.thumbnails.push(t);added.push(t.asset)}
  return added;
}
function thumbsWire(){
  if(tab!=='thumbnails')return;
  document.querySelectorAll('.thumbnail-grid article').forEach(card=>{const id=card.querySelector('[data-remove-thumb]')?.dataset.removeThumb;if(id){card.draggable=true;card.dataset.thumbAsset=id}});
  const tabBody=document.querySelector('.top-three');if(tabBody&&!document.querySelector('.drop-hint'))tabBody.insertAdjacentHTML('beforebegin','<p class="drop-hint">Drag images from your computer or from the library below onto 1, 2 or 3.</p>');
}
document.addEventListener('dragstart',e=>{const c=e.target.closest?.('[data-thumb-asset]');if(c){e.dataTransfer.setData('application/x-thumb',c.dataset.thumbAsset);e.dataTransfer.effectAllowed='copy'}});
document.addEventListener('dragover',e=>{if(!current||tab!=='thumbnails')return;const slot=e.target.closest('.thumb-preview');if(slot||e.dataTransfer.types.includes('Files')){e.preventDefault();document.querySelectorAll('.thumb-preview.over').forEach(x=>x!==slot&&x.classList.remove('over'));slot?.classList.add('over')}});
document.addEventListener('drop',async e=>{
  if(!current||tab!=='thumbnails')return;const slot=e.target.closest('.thumb-preview');
  const asset=e.dataTransfer.getData('application/x-thumb'),files=[...e.dataTransfer.files];
  if(!slot&&!files.length)return;e.preventDefault();document.querySelectorAll('.thumb-preview.over').forEach(x=>x.classList.remove('over'));
  const i=slot?Number(slot.dataset.rank):null;
  try{
    if(asset&&i!==null)thumbsToSlot(asset,i);
    else if(files.length){const added=await thumbsImport(files);if(i!==null)added.slice(0,3-i).forEach((a,k)=>thumbsToSlot(a,i+k))}
    else return;
    const first=asset||current.thumbnails.at(-1)?.asset,at=current.top_three.indexOf(first);
    mark();await save();toast(at>=0?`Set as option ${at+1}`:'Added to the library');
  }catch(err){toast(err.message,true)}
});

// Header: the phase bar replaces guessing which stage comes next.
const studioBaseDetail=renderDetail;
renderDetail=function(){
  const r=studioBaseDetail.apply(this,arguments);const p=current;
  const top=document.querySelector('#workspace .project-top');
  if(p&&top&&!document.querySelector('.phase-bar')){top.insertAdjacentHTML('afterend',phaseBar(p));top.classList.add('has-phases')}
  thumbsWire();
  return r;
};
document.addEventListener('click',e=>{const b=e.target.closest('[data-phase]');if(!b||!current)return;
  const sel=document.querySelector('.project-top select[data-field=stage]');const stages=PHASES.find(([,s])=>s[0]===b.dataset.phase)[1];
  if(stages.includes(current.stage)||!sel)return;sel.value=b.dataset.phase;sel.dispatchEvent(new Event('change',{bubbles:true}));renderDetail()});
