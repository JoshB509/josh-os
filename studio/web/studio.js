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
  try{const r=await api('/api/agents/ask',body);samThread=r.messages;if(typeof roomThread!=='undefined')roomThread.sam=r.messages;if(typeof roomWatchJobs==='function')roomWatchJobs('sam',r.reply?.saved);
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

async function thumbsImport(files){
  const added=[];
  for(const f of files){if(!/^image\/(png|jpeg|webp)$/.test(f.type)){toast(`${f.name} isn't a PNG, JPEG or WebP`,true);continue}
    const data=await new Promise((ok,bad)=>{const r=new FileReader();r.onload=()=>ok(r.result.split(',')[1]);r.onerror=bad;r.readAsDataURL(f)});
    const t=await api('/api/images',{name:f.name,data});if(!current.thumbnails.some(x=>x.asset===t.asset))current.thumbnails.push(t);added.push(t.asset)}
  return added;
}
// Thumbnails workspace (16 Nov 2026, Josh's brief). The video's thumbnail library and three positions, Main, B and C,
// each shown as the YouTube card a viewer would see. Placing a thumbnail in a position is the confirmation. Those three
// are the confirmed set, saved with the project like any other edit, so there is no separate creator or lock-in step.
// That is not approval to publish. The release check keeps its own byte-exact thumbnail approval.
// The channel name is Josh's own, as saved in Data/youtube-comments/settings.json (channel_title). Nothing here invents
// views, dates or durations. A line appears only when real saved data exists, and no saved data has durations.
const TW_CHANNEL='Josh Brindley',TW_POS=['Main','B','C'];
let twRefs=null,twHome=null;
function thumbsToSlot(asset,i){twPlace(current,asset,i)}
function twTop(p){return (p.top_three||[]).filter((a,i,arr)=>a&&arr.indexOf(a)===i&&(p.thumbnails||[]).some(t=>t.asset===a)).slice(0,3)}
// Put a thumbnail in position i. One already in the three swaps places with whatever is there. A new one replaces it,
// and the replaced one stays in the library.
function twPlace(p,asset,i){
  const arr=twTop(p),from=arr.indexOf(asset);
  if(from>=0){const to=Math.min(i,arr.length-1);arr[from]=arr[to];arr[to]=asset}
  else if(i<arr.length)arr[i]=asset;else arr.push(asset);
  p.top_three=arr.slice(0,3);
}
// Every change to the three goes through here, one at a time, for the video that was open when it started.
// - The change is worked out on a copy and sent as a save of only top_three, thumbnails and locks for that video's id.
//   Nothing on screen changes until the server answers, and the screen then shows what the server saved. A failed save
//   leaves the three exactly as they were saved, so nothing looks confirmed that wasn't.
// - Other edits to the video waiting to autosave are saved first, so this starts from the newest revision.
// - The store refuses a save that changes the main thumbnail while it stays locked, so a locked video is saved
//   unlocked first and locked again in a second save. The lock follows Main: set when Main holds a thumbnail, cleared
//   when it is empty. Being placed is the confirmation, and the lock only keeps Packaging in step.
// - If Josh opens another video meanwhile, the saved result goes to the video it belongs to and the open one is left alone.
let twBusy=null;
function twLock(on){document.querySelector('.tw')?.classList.toggle('busy',on);
  document.querySelectorAll('.tw [data-tw-place],.tw [data-tw-move],.tw [data-tw-unpick],.tw [data-tw-remove],.tw [data-action="upload"]').forEach(b=>{
    if(on){b.dataset.twWas=b.disabled?'1':'';b.disabled=true}else if('twWas' in b.dataset){b.disabled=b.dataset.twWas==='1';delete b.dataset.twWas}})}
function twApply(pid,saved){
  const keys=['top_three','thumbnails','locks','revision','updated_at'];
  for(const p of [current?.id===pid?current:null,typeof autoDrafts!=='undefined'?autoDrafts[pid]:null,state.projects.find(x=>x.id===pid)])
    if(p)for(const k of keys)if(k in saved)p[k]=structuredClone(saved[k]);
  if(current?.id===pid&&tab==='thumbnails')renderDetail();
}
async function twCommit(change,msg){
  if(twBusy||!current)return;
  const pid=current.id;
  twBusy=(async()=>{
    twLock(true);let saved=null;
    try{
      if(typeof autosaveProject==='function'){clearTimeout(autoTimer);if(dirty&&current?.id===pid)await autosaveProject(pid);while(autoBusy)await autoBusy}
      const live=(current?.id===pid&&current)||state.projects.find(x=>x.id===pid);
      if(!live)throw Error('That video is no longer open.');
      const next={top_three:structuredClone(live.top_three||[]),thumbnails:structuredClone(live.thumbnails||[]),locks:{...(live.locks||{})}};
      change(next);next.top_three=twTop(next);
      const main=next.top_three[0],wasLocked=!!(live.locks||{}).thumbnail,moved=main!==twTop(live)[0];
      let rev=live.revision;
      const put=async changes=>{saved=await api('/api/projects/'+encodeURIComponent(pid),{revision:rev,changes},'PUT');rev=saved.revision;return saved};
      const L={...next.locks};
      if(!main||(wasLocked&&moved))delete L.thumbnail;else if(!L.thumbnail)L.thumbnail=new Date().toISOString().slice(0,19);
      await put({top_three:next.top_three,thumbnails:next.thumbnails,locks:L});
      if(main&&wasLocked&&moved)await put({locks:{...(saved.locks||{}),thumbnail:new Date().toISOString().slice(0,19)}});
      twApply(pid,saved);if(msg)toast(msg);
    }catch(err){if(saved)twApply(pid,saved);toast(err.message,true)}
    finally{twBusy=null;twLock(false)}
  })();
  return twBusy;
}
function twTitleFor(p,t){return (t&&p.titles.find(x=>x.id===t.title_id)?.text)||(typeof vpTitle==='function'&&vpTitle(p))||chosen(p)||p.name}
function twAvatar(name,mine){const n=String(name||'').trim();return `<span class="yc-av ${mine?'mine':''}" aria-hidden="true">${n?esc(n[0].toUpperCase()):''}</span>`}
// Preview values on Josh's own cards (his request, 16 Nov 2026), so he can see how a views line and a duration badge
// sit against the thumbnail's text. They are fixed constants in this file, the same on every card, never saved and
// never read from any data. Josh asked for no on-screen explanation of them.
const TW_EXAMPLE={stats:'123K views • 3 days ago',duration:'12:38'};
function twCard({img,title,channel,stats,duration,mine,alt,example}){
  if(example){stats={text:TW_EXAMPLE.stats};duration=TW_EXAMPLE.duration}
  const ex=example?' data-example':'',sr='';
  return `<div class="yc ${mine?'mine':''}"><div class="yc-thumb">${img?`<img src="${img}" alt="${esc(alt||title)}" loading="lazy" decoding="async">`:''}${duration?`<span class="yc-dur"${ex}>${esc(duration)}${sr}</span>`:''}</div>
    <div class="yc-meta">${twAvatar(channel,mine)}<div class="yc-text"><h3 class="yc-title">${esc(title)}</h3>${channel?`<p class="yc-line">${esc(channel)}</p>`:''}${stats?`<p class="yc-line"${ex||(stats.note?` title="${esc(stats.note)}"`:'')}>${esc(stats.text)}${sr}</p>`:''}</div></div></div>`;
}
thumbnails=function(){
  const p=current,top=twTop(p);
  const slot=i=>{const aid=top[i],t=aid&&p.thumbnails.find(x=>x.asset===aid);
    return `<article class="thumb-preview tw-slot ${t?'':'empty'}" data-rank="${i}">
      <div class="tw-pos"><b>${TW_POS[i]}</b>${t?`<details class="tw-menu"><summary aria-label="Options for ${TW_POS[i]}">⋯</summary><div class="tw-pop">
        ${i>0?`<button data-tw-move="${i}" data-dir="-1">Move left</button>`:''}${i<top.length-1?`<button data-tw-move="${i}" data-dir="1">Move right</button>`:''}
        <button data-tw-home="${i}">View on homepage</button>
        <label>Title on this card<select data-thumb-title="${esc(aid)}">${titleOptions(t)}</select></label>
        <button data-tw-unpick="${esc(aid)}">Take out of ${TW_POS[i]}</button></div></details>`:''}</div>
      ${t?twCard({img:`/assets/${encodeURIComponent(aid)}?w=960`,title:twTitleFor(p,t),channel:TW_CHANNEL,mine:true,example:true,alt:`${TW_POS[i]} thumbnail`})
        :'<div class="yc"><div class="yc-thumb tw-hole"><span>Empty</span></div></div>'}
    </article>`};
  const lib=p.thumbnails.map(t=>{const at=top.indexOf(t.asset);
    return `<article class="thumb-small tw-item ${at>=0?'selected':''}" data-thumb-asset="${esc(t.asset)}" draggable="true">
      <span hidden data-remove-thumb="${esc(t.asset)}"></span>
      <div class="tw-img"><img src="/assets/${encodeURIComponent(t.asset)}?w=480" alt="${esc(t.name)}" loading="lazy" decoding="async">${at>=0?`<span class="tw-badge">${TW_POS[at]}</span>`:''}</div>
      <div class="tw-place" role="group" aria-label="Place this thumbnail">${TW_POS.map((n,i)=>`<button data-tw-place="${i}" data-asset="${esc(t.asset)}" ${top[i]===t.asset?'disabled aria-pressed="true"':''} aria-label="Place in ${n}">${n}</button>`).join('')}
        <details class="tw-menu"><summary aria-label="More for this thumbnail">⋯</summary><div class="tw-pop">${t.candidate_status?`<p>${esc(t.candidate_status)}</p>`:''}<button data-tw-remove="${esc(t.asset)}">Remove from this video</button></div></details></div>
    </article>`}).join('');
  return `<div class="tw">
    <div class="tw-head"><h2>Thumbnails</h2><button class="primary" data-tw-home="0" ${top.length?'':'disabled'}>View on homepage</button></div>
    <div class="top-three tw-three">${[0,1,2].map(slot).join('')}</div>
    <section class="tw-lib" aria-label="Thumbnail library"><div class="tw-lib-head"><h3>Library <span class="count">${p.thumbnails.length}</span></h3><button class="subtle small" data-action="upload">Import</button></div>
      <div class="thumbnail-grid tw-grid">${lib}</div>${p.thumbnails.length?'':'<div class="empty tw-none"><p>No thumbnails for this video yet.</p><button data-action="upload">Import thumbnails</button></div>'}</section>
  </div>`;
};
function thumbsWire(){}
document.addEventListener('dragstart',e=>{const c=e.target.closest?.('[data-thumb-asset]');if(c){e.dataTransfer.setData('application/x-thumb',c.dataset.thumbAsset);e.dataTransfer.effectAllowed='copy'}});
document.addEventListener('dragover',e=>{if(!current||tab!=='thumbnails')return;const slot=e.target.closest('.thumb-preview');if(slot||e.dataTransfer.types.includes('Files')){e.preventDefault();document.querySelectorAll('.thumb-preview.over').forEach(x=>x!==slot&&x.classList.remove('over'));slot?.classList.add('over')}});
document.addEventListener('dragleave',e=>{const s=e.target.closest?.('.thumb-preview');if(s&&!s.contains(e.relatedTarget))s.classList.remove('over')});
document.addEventListener('drop',async e=>{
  if(!current||tab!=='thumbnails')return;const slot=e.target.closest('.thumb-preview');
  const asset=e.dataTransfer.getData('application/x-thumb'),files=[...e.dataTransfer.files];
  if(!slot&&!files.length)return;e.preventDefault();document.querySelectorAll('.thumb-preview.over').forEach(x=>x.classList.remove('over'));
  const i=slot?Number(slot.dataset.rank):null;
  try{
    if(asset&&i!==null)return twCommit(p=>twPlace(p,asset,i),`Now in ${TW_POS[Math.min(i,twTop(current).length)]}`);
    if(!files.length)return;
    const added=await thumbsImport(files);
    return twCommit(p=>{if(i!==null)added.slice(0,3-i).forEach((a,k)=>twPlace(p,a,i+k))},i!==null&&added.length?`Now in ${TW_POS[i]}`:'Added to the library');
  }catch(err){toast(err.message,true)}
});
document.addEventListener('click',e=>{
  if(!current||tab!=='thumbnails')return;
  const b=e.target.closest('[data-tw-place],[data-tw-move],[data-tw-unpick],[data-tw-remove],[data-tw-home]');if(!b||b.disabled)return;
  b.closest('details')?.removeAttribute('open');
  if(b.dataset.twHome!==undefined)return twHomeOpen(Number(b.dataset.twHome));
  if(b.dataset.twPlace!==undefined){const i=Number(b.dataset.twPlace),n=twTop(current).length;return twCommit(p=>twPlace(p,b.dataset.asset,i),`Now in ${TW_POS[Math.min(i,n)]}`)}
  if(b.dataset.twMove!==undefined){const i=Number(b.dataset.twMove),j=i+Number(b.dataset.dir);return twCommit(p=>{const a=twTop(p);[a[i],a[j]]=[a[j],a[i]];p.top_three=a},`Now in ${TW_POS[j]}`)}
  if(b.dataset.twUnpick!==undefined)return twCommit(p=>{p.top_three=twTop(p).filter(a=>a!==b.dataset.twUnpick)},'Taken out of the three');
  if(b.dataset.twRemove!==undefined){const t=current.thumbnails.find(x=>x.asset===b.dataset.twRemove);
    if(!t||!confirm(`Remove ${t.name} from this video? The image file stays on this PC.`))return;
    return twCommit(p=>{p.top_three=twTop(p).filter(a=>a!==t.asset);p.thumbnails=p.thumbnails.filter(x=>x.asset!==t.asset)},'Removed from this video')}
});

// View on homepage: a preview of a YouTube home feed made from Josh's saved inspiration videos and his thumbnail-format
// references, with one of his three in the first row. It only draws on this page. Nothing is sent or published.
function twRand(seed){let s=(seed>>>0)||1;return()=>{s^=s<<13;s^=s>>>17;s^=s<<5;return (s>>>0)/4294967296}}
function twViews(n){return n<1e3?String(n):n<1e6?(n<1e4?(n/1e3).toFixed(1).replace(/\.0$/,''):Math.round(n/1e3))+'K':(n<1e7?(n/1e6).toFixed(1).replace(/\.0$/,''):Math.round(n/1e6))+'M'}
function twAgo(day){const d=Math.floor((Date.now()-new Date(day+'T00:00:00Z'))/864e5);if(!(d>=0))return '';
  const u=d<1?['today',0]:d<7?[d,'day']:d<31?[Math.floor(d/7),'week']:d<365?[Math.floor(d/30.4),'month']:[Math.floor(d/365),'year'];
  return u[1]===0?'today':`${u[0]} ${u[1]}${u[0]===1?'':'s'} ago`}
// Saved inspiration videos keep their publish date and the views they had when saved in the catalogue's description.
function twStats(r){if(r.kind!=='videos')return null;const d=r.description||'';
  const v=/([\d,]+) views when saved/.exec(d),pub=/Published (\d{4}-\d{2}-\d{2})/.exec(d),saved=/Saved (\d{4}-\d{2}-\d{2})/.exec(d);
  const parts=[];if(v)parts.push(twViews(Number(v[1].replace(/,/g,'')))+' views');if(pub)parts.push(twAgo(pub[1]));
  return parts.length?{text:parts.join(' • '),note:v&&saved?`Views when it was saved on ${saved[1]}`:''}:null}
function twChannel(r){const c=String(r.credit||'');return /^original creator/i.test(c)?'':c}
async function twHomeOpen(rank){
  if(!twRefs){try{twRefs=(await api('/api/inspiration')).filter(r=>(r.kind==='videos'||r.kind==='formats')&&r.image)}catch(err){toast(err.message,true);return}}
  twHome={rank,seed:twHome?.seed||[...current.id].reduce((a,c)=>a*31+c.charCodeAt(0)>>>0,7)};twHomeRender();
}
function twHomeRender(){
  const p=current,top=twTop(p);if(!p||!top.length){document.querySelector('dialog.yh')?.remove();return}
  const rank=Math.min(twHome.rank,top.length-1),aid=top[rank],t=p.thumbnails.find(x=>x.asset===aid),r=twRand(twHome.seed);
  const vids=twRefs.filter(x=>x.kind==='videos'),fmts=twRefs.filter(x=>x.kind==='formats').map(x=>[r(),x]).sort((a,b)=>a[0]-b[0]).map(x=>x[1]);
  const others=[...vids,...fmts].slice(0,11).map(x=>[r(),x]).sort((a,b)=>a[0]-b[0]).map(x=>x[1]);
  const mine=`<article class="yh-card mine">${twCard({img:`/assets/${encodeURIComponent(aid)}?w=960`,title:twTitleFor(p,t),channel:TW_CHANNEL,mine:true,example:true,alt:`${TW_POS[rank]} thumbnail`})}</article>`;
  const cards=others.map(x=>`<article class="yh-card">${twCard({img:`${x.image}?w=640`,title:x.title,channel:twChannel(x),stats:twStats(x)})}</article>`);
  cards.splice(1,0,mine);
  const icon=d=>`<svg viewBox="0 0 24 24" aria-hidden="true"><path d="${d}"/></svg>`;
  let d=document.querySelector('dialog.yh');
  if(!d){d=document.createElement('dialog');d.className='yh';d.setAttribute('aria-label','Homepage preview');document.body.append(d);d.addEventListener('close',()=>d.remove());d.showModal()}
  d.innerHTML=`<header class="yh-top"><span class="yh-ico">${icon('M3 6h18M3 12h18M3 18h18')}</span><span class="yh-logo"><i aria-hidden="true"></i>Home</span>
      <div class="yh-search"><input readonly tabindex="-1" placeholder="Search" aria-label="Search, preview only"><span class="yh-ico">${icon('M10.5 4a6.5 6.5 0 1 1 0 13 6.5 6.5 0 0 1 0-13zM15.5 15.5 20 20')}</span></div>
      ${twAvatar(TW_CHANNEL,true)}</header>
    <div class="yh-body"><nav class="yh-side" aria-hidden="true">${[['Home','M4 11 12 4l8 7v9h-5v-6H9v6H4z'],['Shorts','M9 7l7 5-7 5z'],['Subscriptions','M4 7h16v12H4zM7 4h10'],['You','M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM4 20c1-4 5-5 8-5s7 1 8 5']].map(([n,pth],i)=>`<span class="${i?'':'on'}">${icon(pth)}<small>${n}</small></span>`).join('')}</nav>
      <main class="yh-main"><div class="yh-chips" aria-hidden="true">${['All','Careers','Leadership','Communication','Presentations','Recently uploaded','New to you'].map((c,i)=>`<span class="${i?'':'on'}">${c}</span>`).join('')}</div>
        <div class="yh-grid">${cards.join('')}</div></main></div>
    <div class="yh-bar" role="toolbar" aria-label="Preview controls">
      <div class="yh-pick" role="group" aria-label="Which thumbnail">${top.map((a,i)=>`<button data-yh-rank="${i}" aria-pressed="${i===rank}" class="${i===rank?'on':''}">${TW_POS[i]}</button>`).join('')}</div>
      <button data-yh-shuffle>Shuffle</button><button data-yh-close>Close</button></div>`;
}
document.addEventListener('click',e=>{const d=e.target.closest('dialog.yh');if(!d||!twHome)return;
  const k=e.target.closest('[data-yh-rank],[data-yh-shuffle],[data-yh-close]');if(!k)return;
  if(k.dataset.yhClose!==undefined)return d.close();
  if(k.dataset.yhShuffle!==undefined)twHome.seed=(twHome.seed*48271)%2147483647||1;else twHome.rank=Number(k.dataset.yhRank);
  twHomeRender()});

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
