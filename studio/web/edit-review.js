'use strict';
// The edit review: Tariq's animation deck shown as the finished video, part by part.
// A big picture of the part, a row of pictures for each press as it builds, and the script
// with each press's words marked. Down the right, every part as a slide. Tariq is in the dock.
let erPid=null,erData=null,erAt=0,erBeat=null,erBusy=false,erScript=false;
const ER_TYPES={pip:'Picture in picture',half:'Half and half',full:'Full screen'};

function erImg(name){return `/api/projects/${encodeURIComponent(erPid)}/edit-review/img/${encodeURIComponent(name)}?v=${encodeURIComponent(erData?.rendered||'')}`}
function erParts(){return (erData?.parts||[]).filter(p=>p.kind!=='section')}
function erSectionOf(i){let t='';for(const p of erData.parts){if(p.kind==='section')t=p.title;else if(erParts().indexOf(p)===i)return t}return t}
function erLabel(p){return p.kind==='talk'?'Talking head':`Animation ${p.n}`}
function erSub(p){return p.kind==='talk'?'You, full frame':ER_TYPES[p.type]||'Animation'}
function erFinal(p){return p.kind==='animation'&&p.images?.length?erImg(p.images.at(-1)):'/edit-standin.webp'}

async function openEditReview(pid){
  if(typeof leave==='function'&&!leave())return;
  erPid=pid;erAt=0;erBeat=null;erScript=false;current=null;view='edit-review';
  $('#workspace').innerHTML='<p class="hint">Loading the review…</p>';
  try{erData=await api(`/api/projects/${encodeURIComponent(pid)}/edit-review`)}catch(e){toast(e.message,true);return}
  render();scrollTo(0,0);
  // the slides changed since the pictures were taken, or none were taken: take them now so every picture matches its words
  if(erData.slides&&(erData.stale||!erData.rendered))erRender();
}
async function erRender(only){
  if(erBusy)return;erBusy=true;renderEditReview();
  try{erData=await api(`/api/projects/${encodeURIComponent(erPid)}/edit-review`,{only:only||[]});toast('Review pictures updated')}
  catch(e){toast(e.message,true)}
  finally{erBusy=false;renderEditReview()}
}
// The script of a part with each press's words marked, numbered by press.
function erMarked(p){
  let html=esc(p.script);
  if(p.kind!=='animation')return `<p>${html}</p>`;
  const marks=[];
  for(const pr of p.presses||[]){
    const re=new RegExp(pr.words.trim().replace(/[.*+?^${}()|[\]\\]/g,'\\$&').replace(/\s+/g,'\\W+'),'i');
    const m=re.exec(p.script);if(m)marks.push({start:m.index,end:m.index+m[0].length,press:pr.press});
  }
  marks.sort((a,b)=>a.start-b.start);
  let out='',at=0;
  for(const m of marks){if(m.start<at)continue;out+=esc(p.script.slice(at,m.start))+`<mark class="er-cue ${erBeat===m.press?'on':''}" data-er-beat="${m.press}"><sup>${m.press}</sup>${esc(p.script.slice(m.start,m.end))}</mark>`;at=m.end}
  out+=esc(p.script.slice(at));
  const missing=(p.presses||[]).filter(pr=>!marks.some(m=>m.press===pr.press));
  return `<p>${out}</p>${missing.length?`<p class="er-missing">Press ${missing.map(m=>m.press).join(', ')} ${missing.length===1?'isn’t':'aren’t'} in this stretch of script: ${missing.map(m=>`“${esc(m.words)}”`).join(', ')}</p>`:''}`;
}
function renderEditReview(){
  const project=state.projects.find(p=>p.id===erPid);
  page('Animation review','');subPage(project?.name||'Video','Animation review','data-er-back');
  if(!erData){$('#workspace').innerHTML='<p class="hint">Loading…</p>';return}
  const parts=erParts();
  if(!parts.length){$('#workspace').innerHTML=`<div class="er-empty"><h2>No deck to review yet</h2><p>Tariq makes the slides and shot list in this video's folder. ${erData.slides?'':'There are no slides yet.'} ${erData.shot_list?'':'There is no shot list yet.'}</p>
    <button class="primary" data-dock-ask="Set up the edit folders for this video if they aren't there, then make the slides and shot list for it.">Ask Tariq to make the deck</button></div>`;return}
  erAt=Math.max(0,Math.min(parts.length-1,erAt));const p=parts[erAt];
  const anims=parts.filter(x=>x.kind==='animation'),missingPics=anims.some(a=>!(a.images||[]).length);
  // A part that brings an animation back starts partway through its presses, so pictures are numbered by press
  const off=(p.first_press||1)-1,lastP=off+(p.images?.length||0);
  const beat=p.kind==='animation'&&p.images?.length?Math.min(Math.max(erBeat??lastP,off+1),lastP):null;
  const big=p.kind==='animation'&&p.images?.length?erImg(p.images[beat-1-off]):'/edit-standin.webp';
  $('#workspace').innerHTML=`<div class="er">
    <header class="er-head">
      <div><h1>${esc(project?.name||'Animation review')}</h1><p>${anims.length?`${new Set(anims.map(a=>a.n)).size} animations`:'No animations'} across ${parts.length} parts, from ${esc(erData.slides)} and ${esc(erData.shot_list)}.${erData.stale?' The slides have changed since these pictures were taken.':''}</p></div>
      <div class="er-actions">
        <button class="subtle" data-er-script>${erScript?'Back to the slides':'Whole script'}</button>
        <button class="subtle" data-er-render ${erBusy?'disabled':''}>${erBusy?'Taking pictures…':missingPics||erData.stale?'Take the pictures':'Retake pictures'}</button>
        <button class="primary" data-dock-ask="I've reviewed the animation deck and I'm happy with it. Go ahead and render the animations and assemble them into the Premiere edit, following the editing handoff.">Approve the deck</button>
      </div>
    </header>
    ${erScript?erWholeScript(parts):`<div class="er-body">
      <section class="er-main">
        <div class="er-stage"><img src="${big}" alt="${esc(erLabel(p))}${beat?`, after press ${beat}`:''}">
          <button class="er-nav prev" data-er-go="${erAt-1}" ${erAt===0?'disabled':''} aria-label="Previous part">‹</button>
          <button class="er-nav next" data-er-go="${erAt+1}" ${erAt===parts.length-1?'disabled':''} aria-label="Next part">›</button></div>
        <div class="er-meta"><b>${esc(erLabel(p))}</b><span>${esc(erSub(p))}${p.time?`, from ${esc(p.time)}`:''}</span>${erSectionOf(erAt)?`<span class="er-sec">${esc(erSectionOf(erAt))}</span>`:''}</div>
        ${p.kind==='animation'&&p.images?.length?`<div class="er-beats">${p.images.map((n,i)=>`<button class="er-beat ${off+i+1===beat?'on':''}" data-er-beat="${off+i+1}"><img src="${erImg(n)}" alt=""><span>${off+i+1}</span></button>`).join('')}</div>`:''}
        ${p.desc?`<p class="er-desc">${esc(p.desc)}</p>`:''}
        <div class="er-script">${erMarked(p)}</div>
      </section>
      <nav class="er-rail" aria-label="Parts">${erRail(parts)}</nav>
    </div>`}
  </div>`;
  document.querySelector('.er-rail .on')?.scrollIntoView({block:'nearest'});
}
function erRail(parts){
  let sec='',html='',i=-1;
  for(const p of erData.parts){
    if(p.kind==='section'){sec=p.title;html+=`<p class="er-rail-sec">${esc(p.title)}</p>`;continue}
    i++;html+=`<button class="er-slide ${i===erAt?'on':''} ${p.kind}" data-er-go="${i}"><span class="er-thumb"><img src="${erFinal(p)}" alt="" loading="lazy"></span>
      <span class="er-slide-text"><b>${esc(erLabel(p))}</b><small>${esc(p.script.slice(0,110))}${p.script.length>110?'…':''}</small></span></button>`;
  }
  return html;
}
function erWholeScript(parts){
  let html='',i=-1;
  for(const p of erData.parts){
    if(p.kind==='section'){html+=`<h2>${esc(p.title)}</h2>`;continue}
    i++;html+=`<span class="er-ws ${p.kind}" data-er-part="${i}" title="${esc(erLabel(p))}">${esc(p.script)} </span>`;
  }
  return `<div class="er-whole"><p class="hint">Select or click any words to open that part. Animation stretches are underlined.</p><div class="er-whole-text">${html}</div></div>`;
}
function erGo(i){erAt=i;erBeat=null;erScript=false;renderEditReview();if(typeof dockRender==='function')dockRender()}

document.addEventListener('click',e=>{
  if(view!=='edit-review')return;
  if(e.target.closest('[data-er-back]')){const pid=erPid;view='board';erPid=null;erData=null;openProject(pid);return}
  const go=e.target.closest('[data-er-go]');if(go&&!go.disabled){erGo(+go.dataset.erGo);return}
  const b=e.target.closest('[data-er-beat]');if(b){erBeat=+b.dataset.erBeat;renderEditReview();return}
  if(e.target.closest('[data-er-script]')){erScript=!erScript;renderEditReview();return}
  if(e.target.closest('[data-er-render]')){erRender();return}
  const ws=e.target.closest('[data-er-part]');if(ws&&!String(getSelection()).trim()){erGo(+ws.dataset.erPart);return}
});
document.addEventListener('mouseup',()=>{
  if(view!=='edit-review'||!erScript)return;const sel=getSelection();if(!sel||!String(sel).trim())return;
  const el=(sel.anchorNode?.nodeType===3?sel.anchorNode.parentElement:sel.anchorNode)?.closest?.('[data-er-part]');if(el){sel.removeAllRanges();erGo(+el.dataset.erPart)}
});
document.addEventListener('keydown',e=>{
  if(view!=='edit-review'||erScript||e.target.closest?.('input,textarea,[contenteditable]'))return;
  const parts=erParts(),p=parts[erAt];
  if(e.key==='ArrowRight'||e.key==='PageDown'||(e.key===' '&&!e.shiftKey)){e.preventDefault();if(erAt<parts.length-1)erGo(erAt+1)}
  else if(e.key==='ArrowLeft'||e.key==='PageUp'){e.preventDefault();if(erAt>0)erGo(erAt-1)}
  else if(/^[1-9]$/.test(e.key)&&+e.key>=(p?.first_press||1)&&+e.key<(p?.first_press||1)+(p?.images?.length||0)){erBeat=+e.key;renderEditReview()}
});

// Routing, and a way in from the video page.
const erBaseRender=render;
render=function(){if(view==='edit-review'&&!current)return renderEditReview();return erBaseRender.apply(this,arguments)};
const erDecks={};
function erInjectButton(){
  if(!current||view==='edit-review')return;const top=document.querySelector('.project-top');if(!top||top.classList.contains('vp-top')||top.querySelector('[data-er-open]'))return;
  const pid=current.id,add=()=>{if(current?.id!==pid||top.querySelector('[data-er-open]'))return;
    top.insertAdjacentHTML('beforeend',`<button class="subtle" data-er-open="${esc(pid)}">${erDecks[pid]?'Review the animations':'Set up the edit'}</button>`)};
  if(pid in erDecks)return add();
  api(`/api/projects/${encodeURIComponent(pid)}/edit-review`).then(r=>{erDecks[pid]=!!(r.slides&&r.shot_list);add()}).catch(()=>{});
}
document.addEventListener('click',e=>{const o=e.target.closest('[data-er-open]');if(o)openEditReview(o.dataset.erOpen)});
(()=>{const w=document.getElementById('workspace');if(!w)return;let t=null;new MutationObserver(()=>{clearTimeout(t);t=setTimeout(erInjectButton,120)}).observe(w,{childList:true})})();

// The Editing page leads with the animation decks, so a review is one click from where Josh looks for edits.
let erDeckList=null;
function erDecksSection(){
  if(!erDeckList)return '<section class="er-decks"><h2>Animation decks</h2><p class="hint">Looking for decks…</p></section>';
  if(!erDeckList.length)return `<section class="er-decks"><h2>Animation decks</h2><p class="hint">No decks yet. Once a video is recorded, Tariq makes its slides and shot list, and they show here to review.</p></section>`;
  const open=erDeckList.filter(d=>d.stage!=='Published'),done=erDeckList.filter(d=>d.stage==='Published');
  const card=d=>`<button class="er-deck" data-er-open="${esc(d.id)}"><span class="er-deck-cover">${d.cover?`<img src="/api/projects/${encodeURIComponent(d.id)}/edit-review/img/${encodeURIComponent(d.cover)}?v=${encodeURIComponent(d.rendered||'')}" alt="">`:'<span>No pictures yet</span>'}</span>
    <span class="er-deck-text"><b>${esc(d.name)}</b><small>${d.animations} animation${d.animations===1?'':'s'}, ${esc(d.stage)}${d.stale?', slides changed since the pictures':''}</small></span><span class="er-deck-go">Review</span></button>`;
  return `<section class="er-decks"><h2>Animation decks</h2>${open.length?`<div class="er-deck-grid">${open.map(card).join('')}</div>`:'<p class="hint">No decks waiting for review.</p>'}
    ${done.length?`<details class="er-done"><summary>Published videos (${done.length})</summary><div class="er-deck-grid">${done.map(card).join('')}</div></details>`:''}</section>`;
}
const erBaseEditing=renderEditing;
renderEditing=function(){
  const r=erBaseEditing.apply(this,arguments);
  const lanes=document.querySelector('#workspace .edit-lanes');
  if(lanes&&!document.querySelector('.er-decks'))lanes.insertAdjacentHTML('beforebegin',erDecksSection());
  if(!erDeckList)api('/api/edit-review/decks').then(d=>{erDeckList=d;const s=document.querySelector('.er-decks');if(s&&view==='editing')s.outerHTML=erDecksSection()}).catch(()=>{erDeckList=[]});
  return r;
};
