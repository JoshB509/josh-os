'use strict';
const $=s=>document.querySelector(s),token=$('meta[name=dj-token]').content;
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const icon={
  status:'<svg viewBox="0 0 16 16"><circle cx="8" cy="8" r="5.5"/><path d="m5.6 8 1.7 1.7 3.2-3.4"/></svg>',
  date:'<svg viewBox="0 0 16 16"><rect x="2.5" y="3.5" width="11" height="10" rx="2"/><path d="M2.5 6.5h11M5.5 2v3M10.5 2v3"/></svg>',
  time:'<svg viewBox="0 0 16 16"><circle cx="8" cy="8" r="5.5"/><path d="M8 5v3.2l2 1.3"/></svg>',
  slides:'<svg viewBox="0 0 16 16"><rect x="2" y="3" width="12" height="8.5" rx="1.5"/><path d="M8 11.5V14M5.5 14h5"/></svg>',
  script:'<svg viewBox="0 0 16 16"><path d="M4 2.5h5.5L12 5v8.5H4z"/><path d="M6 7h4M6 9.5h4"/></svg>',
  chev:'<svg viewBox="0 0 10 10"><path d="m3.5 2 3 3-3 3"/></svg>'
};
let catalog,selected=null,detail=null,rec=false,obsState=null,prompterState=null,poll=null,wasLive=false,busy=false,recordings=[],evidence=null;
let openChapters=new Set();
try{openChapters=new Set(JSON.parse(localStorage.getItem('dj-lessons-open')||'[]'))}catch{}
function saveOpen(){try{localStorage.setItem('dj-lessons-open',JSON.stringify([...openChapters]))}catch{}}

async function api(path,data){
  const r=await fetch(path,{method:data===undefined?'GET':'POST',headers:{'X-DJ-Token':token,'Content-Type':'application/json'},body:data===undefined?undefined:JSON.stringify(data)});
  const d=await r.json();if(!r.ok)throw Error(d.error||'Request failed');return d;
}
function toast(message,error=false){const t=$('#toast');t.textContent=message;t.classList.toggle('error',error);t.style.display='block';clearTimeout(toast.timer);toast.timer=setTimeout(()=>t.style.display='none',error?9000:4000)}

/* ---------- Sidebar ---------- */
const chapterKey=x=>x.track_key+'|'+x.chapter;
function lessonDot(x){const r=x.recording;return r.takes.some(t=>t.id===r.preferred_take&&t.audio_validation?.can_continue)?'chosen':r.takes.length?'takes':''}
function tree(){
  const q=$('#search').value.trim().toLowerCase();
  const rows=catalog.lessons.filter(x=>!q||(x.code+' '+x.title).toLowerCase().includes(q));
  let html='',track='',chapter='';
  for(const x of rows){
    if(x.track!==track){if(chapter)html+='</div></div>';chapter='';track=x.track;html+=`<div class="track">${esc(track)}</div>`}
    const key=chapterKey(x);
    if(key!==chapter){if(chapter)html+='</div></div>';chapter=key;const open=q||openChapters.has(key);html+=`<div class="chapter ${open?'open':''}" data-chapter="${esc(key)}"><button class="row" data-toggle="${esc(key)}"><span class="chev">${icon.chev}</span><span class="name">${esc(x.chapter)}</span></button><div class="kids">`}
    html+=`<button class="row lesson ${x.id===selected?'on':''}" data-lesson="${x.id}" title="${esc(x.code+' '+x.title)}"><span class="dot ${lessonDot(x)}"></span><span class="num">${esc(x.code)}</span><span class="name">${esc(x.title)}</span></button>`;
  }
  if(chapter)html+='</div></div>';
  $('#tree').innerHTML=html||'<div class="none">No lessons match.</div>';
}

/* ---------- Script rendering ---------- */
function fmt(s){
  s=s.replace(/<\/?span[^>]*>/g,'').replace(/\\([[\]*_`#()])/g,'$1');
  return esc(s).replace(/\*\*(.+?)\*\*/g,'<b>$1</b>').replace(/`([^`]+)`/g,'<code>$1</code>').replace(/(^|[\s(])\*([^*\s][^*]*?)\*(?=[\s).,;:!?]|$)/g,'$1<i>$2</i>');
}
function inline(s){
  const re=/<span color="red">\s*\*\*CHANGE (\d+)\*\*\s*<\/span>|\*\*CHANGE (\d+)\*\*/g;let out='',last=0,m;
  while((m=re.exec(s))){out+=fmt(s.slice(last,m.index))+`<span class="cue">CHANGE ${m[1]||m[2]}</span>`;last=re.lastIndex}
  return out+fmt(s.slice(last));
}
function renderScript(md,{skipIntro=false}={}){
  const lines=md.replace(/\r/g,'').split('\n');let html='',callout=null,seenRule=!skipIntro;
  for(const raw of lines){
    const line=raw.trimEnd(),t=line.trim();
    if(callout){if(t==='</callout>'){html+=`<div class="callout"><span>🎬</span><div>${callout.map(l=>`<p>${inline(l)}</p>`).join('')}</div></div>`;callout=null}else if(t)callout.push(t);continue}
    if(/^<callout/.test(t)){callout=[];continue}
    if(/^---+$/.test(t)){seenRule=true;continue}
    if(!seenRule||!t||/^# /.test(t)||/^<(?!span)[a-z-]+[\s>/]/.test(t))continue;
    if(/^#{2,3} /.test(t)){html+=`<h3>${inline(t.replace(/^#+ /,''))}</h3>`;continue}
    if(/^[-*] /.test(t)){html+=`<p class="li">•&nbsp; ${inline(t.slice(2))}</p>`;continue}
    html+=`<p>${inline(t)}</p>`;
  }
  return html;
}

/* ---------- Page ---------- */
const fmtDur=s=>{s=Math.round(s||0);return Math.floor(s/60)+':'+String(s%60).padStart(2,'0')};
const fmtDay=iso=>new Date(iso).toLocaleString([], {day:'numeric',month:'short',hour:'numeric',minute:'2-digit'});
const slideIndex=()=>detail.materials.findIndex(m=>m.kind==='slides'&&m.exists);
const fileName=p=>String(p).split(/[\\/]/).pop();
function audioCandidate(){
 const r=detail.recording,manual=r.take_selection?.mode==='manual';
 return manual?r.takes.find(t=>t.id===r.take_selection.take_id):r.takes.filter(t=>!detail.binding||detail.binding.lesson_id!==detail.id||t.binding_id===detail.binding.id).at(-1);
}
function audioBlocked(){const t=audioCandidate();return !t||!t.audio_validation?.can_continue}
function recordedReady(){const r=detail.recording,t=audioCandidate();return !!(t&&t.audio_validation?.can_continue&&r.recorded_completion?.take_id===t.id)}
function audioStatus(t){
 const a=t.audio_validation||{status:'pending',message:'Audio check pending.'};
 const label={pending:'Audio check pending',checking:'Checking saved audio',waiting:'Waiting for finished file',failed:'Audio check failed',warning:'Listening review needed',passed:'Non-silent audio detected'}[a.status]||'Audio check needed';
 return `<div class="audio-check ${esc(a.status)}" aria-live="polite"><strong>${esc(label)}</strong><p>${esc(a.message)}</p>${a.audition?'<p>Listening review: you reported intelligible speech in this exact file.</p>':''}<button class="linkish" data-take="${t.id}" data-action="audio_check" ${['pending','checking'].includes(a.status)?'disabled':''}>${a.status==='failed'?'Retry audio check':'Check again'}</button> <button class="linkish" data-take="${t.id}" data-action="open">Play take</button>${['passed','warning'].includes(a.status)&&!a.audition?`<label class="listening-check"><input type="checkbox" data-listened="${t.id}"> I played this take and heard intelligible speech</label><button class="linkish" data-take="${t.id}" data-action="audition">Save listening review</button>`:''}</div>`;
}
function statusTag(){
  const s=detail.recording,pref=s.takes.find(t=>t.id===s.preferred_take);
  const candidate=audioCandidate();if(candidate&&!candidate.audio_validation?.can_continue)return '<span class="tag yellow">Audio check required</span>';
  if(pref?.student_access_verified)return '<span class="tag green">Live for students</span>';
  if(pref?.uploaded)return '<span class="tag green">Uploaded</span>';
  if(pref?.approved_at)return '<span class="tag green">Approved</span>';
  if(pref&&s.recorded_completion?.take_id===pref.id)return '<span class="tag green">Recorded · done</span>';
  if(pref)return '<span class="tag green">Take chosen</span>';
  if(s.takes.length)return `<span class="tag yellow">${s.takes.length} take${s.takes.length>1?'s':''}, none chosen</span>`;
  return '<span class="tag">Not recorded</span>';
}
function completionPanel(){
 const s=detail.recording,bound=detail.binding?.lesson_id===detail.id,take=audioCandidate();
 return `<section class="completion" aria-label="Recording and recovery"><div class="completion-heading"><div><h2>Recording</h2><p>Done frees the recorder. You can choose any lesson; unfinished files stay in Recovery.</p></div><span class="tag">${s.takes.length} saved takes</span></div><div class="finish-actions">${bound?`<button class="btn" id="another-take" ${obsState?.recording?'disabled':''}>Another take</button><button class="primary" id="review-next" ${obsState?.recording?'disabled':''}>Done</button>`:''}<button class="btn" id="completion-add">Recover recordings</button>${take?`<button class="btn" id="review-play">Play selected take</button><button class="btn" id="confirm-take" ${audioBlocked()?'disabled':''}>Confirm reviewed take</button>`:''}</div><p id="handover-notice" class="fine">${esc(handoverState?.lesson_id===detail.id?handoverState.message:'File and audio review never prevents recording another lesson.')}</p>${take?`<div class="audio-summary"><p class="fine">Reviewing: ${esc(take.name)}</p>${audioStatus(take)}</div>`:''}</section>`;
}
function bindCompletion(){
 $('#completion-add')&&($('#completion-add').onclick=showImport);
 $('#review-play')&&($('#review-play').onclick=()=>takeAction(audioCandidate().id,'open'));
 $('#confirm-take')&&($('#confirm-take').onclick=()=>takeAction(audioCandidate().id,'complete'));
 $('#another-take')&&($('#another-take').onclick=record);
 $('#review-next')&&($('#review-next').onclick=finishLesson);
}
async function finishLesson(){
 if(busy)return;
 if(catalog.lifecycle_version!==2)return toast('The recorder update needs Josh OS safely reloaded. Save current work first; pending recordings are preserved.',true);
 busy=true;const lid=selected;
 try{
  const state=await api('/api/lessons/'+lid+'/done',{confirmed:true,binding_id:detail.binding?.id});
  handoverState={lesson_id:lid,message:state.message};rec=false;clearInterval(poll);obsState=null;
  await load();detail=await api('/api/lessons/'+lid);tree();render();toast(state.message);
 }catch(e){toast(e.message,true)}finally{busy=false}
}
function deliveryPanel(){
 const r=detail.recording,w=r.written_lesson||{},chosen=r.takes.find(t=>t.id===r.preferred_take);
 return `<details class="completion" id="delivery"><summary>Written lesson &amp; delivery <span class="tag">${w.status==='ready'?'Written ready':w.status==='draft'?'Written draft':'Written not started'}</span></summary><p class="fine">Link the written lesson when it exists. Recording Done and Google Drive upload are separate steps.</p><label for="written-status">Written lesson</label> <select id="written-status"><option value="not_started" ${!w.status||w.status==='not_started'?'selected':''}>Not started</option><option value="draft" ${w.status==='draft'?'selected':''}>Draft</option><option value="ready" ${w.status==='ready'?'selected':''}>Ready</option></select><p><input id="written-path" aria-label="Written lesson file path" placeholder="Path to the written lesson file" value="${esc(w.path||'')}" style="width:100%"></p><button class="btn" id="save-written">Save written lesson</button><p class="fine">Recording: ${esc(chosen?.name||'Confirm a reviewed take')}</p><div class="finish-actions"><button class="btn" id="preview-delivery">Review upload files</button><button class="btn" disabled>Upload to Google Drive · setup needed</button></div><p class="fine">A Google Drive destination and upload connection are still needed. Nothing has been uploaded.</p><div id="delivery-preview" class="fine"></div></details>`;
}
function bindDelivery(){
 $('#save-written')&&($('#save-written').onclick=async()=>{try{detail.recording=await api('/api/lessons/'+selected+'/written',{status:$('#written-status').value,path:$('#written-path').value});render();$('#delivery').open=true;toast('Written lesson saved.')}catch(e){toast(e.message,true)}});
 $('#preview-delivery')&&($('#preview-delivery').onclick=async()=>{try{const p=await api('/api/lessons/'+selected+'/delivery-plan',{});$('#delivery-preview').innerHTML=p.files.map(f=>`<p>${esc(f.kind==='recording'?'Video':'Written lesson')}: ${esc(f.path)}</p>`).join('')+p.blockers.map(x=>`<p>${esc(x)}</p>`).join('')}catch(e){toast(e.message,true)}});
}

function takesTable(){
  const s=detail.recording;if(!s.takes.length)return '';
  return `<h2>Takes</h2><table class="takes"><thead><tr><th class="star"></th><th>Take</th><th>Length</th><th>Saved</th><th></th><th class="more"></th></tr></thead><tbody>${s.takes.map(t=>{
    const tags=[t.audio_validation?.can_continue&&t.approved_at&&'Approved',t.uploaded&&'Uploaded',t.student_access_verified&&'Access checked'].filter(Boolean).map(x=>`<span class="tag green">${x}</span>`).join('');
    return `<tr><td class="star"><button class="${s.preferred_take===t.id?'on':''}" data-take="${t.id}" data-action="prefer" title="Keep this choice when clicking Done">${s.preferred_take===t.id?'Chosen':'Use this take'}</button></td><td><button class="linkish tname" data-take="${t.id}" data-action="open" title="Play this take">${esc(t.name.replace(/^.*_(take\d+).*$/,'$1'))}</button></td><td>${fmtDur(t.duration)}</td><td>${esc(fmtDay(t.recorded_at))}</td><td>${tags}${audioStatus(t)}</td><td class="more"><button data-menu="${t.id}" aria-label="More">•••</button></td></tr>`}).join('')}</tbody></table>`;
}
function recBar(){
  const live=obsState?.recording,ok=obsState?.connected;
  const state=!obsState?'<div class="state"><b></b>Getting ready…</div>':live?'<div class="state live"><b></b>Recording</div>':ok?'<div class="state ready"><b></b>Ready to record</div>':'<div class="state"><b></b>OBS not connected</div>';
  const obsChip=ok?`<span class="tag ${obsState.profile==='Lessons'?'green':'yellow'}">OBS · ${esc(obsState.profile)}</span>`:'<span class="tag yellow">Open OBS</span>';
  const pChip=prompterState===null?'<span class="tag">Prompter…</span>':prompterState.screen_mode?'<span class="tag green">Prompter · Screen</span>':'<span class="tag yellow">Prompter · set Screen mode</span>';
  const newTake=wasLive===false&&rec.stopped;
  return `<div class="recbar">${state}<span class="what">${esc(detail.code+' · '+detail.title)}${detail.cue_count?' · '+detail.cue_count+' changes':''}</span><span class="chips">${obsChip}${pChip}</span><span class="btns">${slideIndex()>=0?'<button class="btn" id="reopen">Reopen slides</button>':''}<button class="btn" id="add-take" ${live?'disabled':''}>Another take</button><button class="primary" id="done" ${live?'disabled':''}>Done</button><button class="btn" id="exit-rec" title="Leave recording mode (Esc)">Back to lesson</button></span></div>`;
}
function render(){
  const d=detail,s=d.recording,list=catalog.lessons,i=list.findIndex(x=>x.id===d.id),next=list[i+1];
  document.body.classList.toggle('rec',!!rec);
  const si=slideIndex(),slides=si>=0?d.materials[si]:null;
  const script=d.cues_text?renderScript(d.cues_text):d.script_text?renderScript(d.script_text,{skipIntro:true}):'';
  const top=rec?recBar():`<header class="topbar"><div class="crumbs"><span>${esc(d.track)}</span><i>/</i><span>${esc(d.chapter)}</span><i>/</i><span>${esc(d.code)}</span></div><button class="ghost" id="next" ${next?'':'disabled'}>${next?'Next · '+esc(next.code)+' →':'Last lesson'}</button></header>`;
  const props=rec?'':`<div class="props">
    <div class="prop"><span class="k">${icon.status}Status</span><span class="v">${statusTag()}</span></div>
    <div class="prop"><span class="k">${icon.date}Planned</span><label class="v"><input id="planned" type="date" value="${esc(s.planned_date||d.suggested_date||'')}"></label></div>
    <div class="prop"><span class="k">${icon.time}Length</span><span class="v">${d.estimated_minutes?'About '+d.estimated_minutes+' min':'<span class="faint">Empty</span>'}</span></div>
    <div class="prop"><span class="k">${icon.slides}Slides</span><span class="v">${slides?`<button class="linkish" data-open="${si}" title="${esc(slides.path)}">${esc(fileName(slides.path))}</button>`:'<span class="faint">No slides mapped yet</span>'}</span></div>
    <div class="prop"><span class="k">${icon.script}Script</span><span class="v">${d.cue_count?d.cue_count+' changes':'<span class="faint">No slide cues saved</span>'}${d.source_url?`<a class="linkish" href="${esc(d.source_url)}" target="_blank" rel="noopener noreferrer">Open in Notion ↗</a>`:''}</span></div>
  </div>
  <div class="recline"><button class="record" id="record"><b></b>Record</button><span class="hint">Opens OBS on Lessons, the slides and the cued script</span></div>
  `;
  $('#page').innerHTML=`${top}<article class="doc">${rec?'':`<div class="code">${esc(d.code)}</div><h1>${esc(d.title)}</h1>`}${props}${completionPanel()}${takesTable()}${rec?'':deliveryPanel()}
    ${rec?'':'<h2>Script</h2>'}${!d.cues_text&&!rec?`<p class="note">${d.script_text?'No slide cues saved for this lesson yet, so this is the plain script.':'No script found for this lesson.'}</p>`:''}
    <div class="script">${script}</div></article>`;
  bindPage(next);bindCompletion();bindDelivery();
}
function bindPage(next){
  $('#next')&&($('#next').onclick=()=>{if(next)select(next.id)});
  $('#planned')&&($('#planned').onchange=async e=>{try{await api('/api/lessons/'+selected+'/plan',{date:e.target.value});toast('Planned date saved')}catch(err){toast(err.message,true)}});
  $('#record')&&($('#record').onclick=record);
  $('#reopen')&&($('#reopen').onclick=()=>openSource(String(slideIndex())));
  $('#add-take')&&($('#add-take').onclick=record);
  $('#done')&&($('#done').onclick=finishLesson);
  $('#exit-rec')&&($('#exit-rec').onclick=leaveRec);
}

let handoverBusy=false,handoverState=null;
async function checkHandover(){
 if(busy||handoverBusy||!detail)return;
 const lid=selected,binding=detail.binding?.lesson_id===selected?detail.binding.id:null;handoverBusy=true;
 try{
  const result=await api('/api/lessons/'+lid+'/handover',{binding_id:binding});
  if(selected!==lid||(detail.binding?.lesson_id===selected?detail.binding.id:null)!==binding)return;
  const previous=handoverState;handoverState={...result,lesson_id:lid};
  const priorAudio=JSON.stringify(detail.recording.takes.map(t=>t.audio_validation));
  if(result.recording)detail.recording=result.recording;
  else await refreshAudio();
  const audioChanged=priorAudio!==JSON.stringify(detail.recording.takes.map(t=>t.audio_validation));
  const entry=catalog.lessons.find(x=>x.id===lid);if(entry&&result.recording)entry.recording=result.recording;
  if(result.imported||audioChanged){const y=$('#page').scrollTop;tree();render();$('#page').scrollTop=y;if(result.imported){const n=$('#handover-notice');if(n)n.textContent='Take saved. Audio review can continue after recording another lesson.';}}
  else if(!previous||previous.lesson_id!==lid||previous.message!==result.message){
   // Do not disturb the script scroll or a selected take while polling.
   const notice=$('#handover-notice');if(notice)notice.textContent=result.message;
  }
 }catch(e){if(selected===lid){const notice=$('#handover-notice');if(notice)notice.textContent='Automatic handover could not finish. Your files are preserved. Retry or use Troubleshoot a missing recording. '+e.message;}}
 finally{handoverBusy=false;}
}
async function refreshAudio(){
 const lid=selected;if(!lid||busy)return;
 try{const fresh=await api('/api/lessons/'+lid);if(selected!==lid)return;
 if(JSON.stringify(fresh.recording)!==JSON.stringify(detail.recording)){const y=$('#page').scrollTop;detail=fresh;const e=catalog.lessons.find(x=>x.id===lid);if(e)e.recording=fresh.recording;tree();render();$('#page').scrollTop=y;}
 }catch{}
}
window.addEventListener('focus',()=>checkHandover());
setInterval(()=>checkHandover(),5000);

/* ---------- Actions ---------- */
async function load(){catalog=await api('/api/lessons')}
async function select(id){
  if(busy)return;busy=true;
  try{detail=await api('/api/lessons/'+id+'/select',{});selected=id;rec=false;clearInterval(poll);obsState=null;openChapters.add(chapterKey(detail));saveOpen();tree();render();$('#page').scrollTop=0;
    $('#tree .on')?.scrollIntoView({block:'nearest'})}
  catch(e){toast(e.message,true)}finally{busy=false;checkHandover()}
}
async function openSource(key){try{await api('/api/lessons/'+selected+'/open',{key})}catch(e){toast(e.message,true)}}
async function record(){
  if(busy)return;busy=true;const b=$('#record');if(b){b.disabled=true;b.lastChild.textContent='Getting ready…';}
  try{
    const bound=detail.binding?.lesson_id===detail.id&&!detail.binding?.closed_log_cursor;let res;
    if(bound){
      res=await api('/api/lessons/'+selected+'/prepare',{});
      if(!res.connected||!res.prepared)throw Error(res.message||'OBS is not ready. Open OBS and retry.');
      if(slideIndex()>=0)await api('/api/lessons/'+selected+'/open',{key:String(slideIndex())}).catch(()=>{});
      if(!detail.cues_text&&detail.source_url)window.open(detail.source_url,'_blank','noopener');
    }else{
      res=await api('/api/lessons/'+selected+'/prepare',{});
      detail=await api('/api/lessons/'+selected);
    }
    if(!res.connected||!res.prepared)throw Error(res.message||'OBS is not ready. Open OBS and retry.');
    detail=await api('/api/lessons/'+selected);obsState=res;prompterState=null;wasLive=!!res.recording;rec={stopped:false};render();$('#page').scrollTop=0;startPoll();
    if(res.warnings?.length)toast(res.warnings.join(' '),true);
    else toast('Lesson ready. Start and stop each take with Stream Deck. Click Done when happy.');
    api('/api/lessons/prompter',{}).then(p=>{prompterState=p;rec&&render()}).catch(()=>{prompterState={screen_mode:false};rec&&render()});
  }catch(e){toast(e.message,true);render()}finally{busy=false}
}
function startPoll(){clearInterval(poll);poll=setInterval(async()=>{
  try{const s=await api('/api/lessons/obs');if(!rec)return;const live=!!s.recording;
    if(wasLive&&!live){rec.stopped=true;toast('Take stopped. Saving automatically; record another or click Done.')}
    if(live)rec.stopped=false;wasLive=live;
    const changed=JSON.stringify(s)!==JSON.stringify(obsState);obsState=s;if(changed&&rec){const y=$('#page').scrollTop;render();$('#page').scrollTop=y}
  }catch{}
},3000)}
async function leaveRec(){
 if(busy)return;busy=true;
 try{
  if(detail.binding?.lesson_id===selected){
   if(catalog.lifecycle_version!==2)throw Error('Save current work and safely reload Josh OS to activate the recorder update.');
   await api('/api/lessons/'+selected+'/release',{binding_id:detail.binding.id});
  }
  rec=false;clearInterval(poll);obsState=null;await load();detail=await api('/api/lessons/'+selected);tree();render();
 }catch(e){toast(e.message,true)}finally{busy=false}
}
async function showImport(){
 try{
  if(catalog.lifecycle_version!==2)throw Error('Recovery is preserved. Safely reload Josh OS after saving work to open the new recovery queue.');
  const queue=await api('/api/lessons/recovery');recordings=queue.items;
  $('#import-dialog h2').textContent='Recover a recording';
  $('#import-lesson').textContent='Choose the exact file and original lesson shown below. Nothing is assigned by newest-file guesswork.';
  $('#recording-list').innerHTML=recordings.length?recordings.map((r,i)=>`<label><input type="radio" name="rec" value="${i}" ${r.lesson_available===false?'disabled':''}><span>${esc(r.lesson_code+' - '+r.lesson_title)}<br>${esc(r.name)}${r.lesson_available===false?'<br>Original lesson is unavailable; file and session are preserved without reassignment.':''}</span><small>${(r.size/1048576).toFixed(0)} MB - ${esc(new Date(Number(r.mtime_ns)/1e6).toLocaleString())}</small></label>`).join(''):'<p>No unimported files are available. Saved takes remain with their original lessons; session evidence is preserved.</p>';
  $('#confirm-import').textContent='Confirm file and lesson';$('#confirm-import').disabled=!recordings.length;
  $('#import-dialog').showModal();
 }catch(e){toast(e.message,true)}
}
$('#cancel-import').onclick=()=>$('#import-dialog').close();
$('#confirm-import').onclick=async()=>{
 const pick=document.querySelector('#recording-list input:checked');if(!pick)return;const r=recordings[Number(pick.value)];
 if(!confirm('Confirm '+r.name+' belongs to '+r.lesson_code+' - '+r.lesson_title+'?'))return;
 const buttons=$('#import-dialog').querySelectorAll('button');buttons.forEach(b=>b.disabled=true);
 try{
  await api('/api/lessons/'+r.lesson_id+'/import',{...r,confirmed_stopped:true,confirmed_association:true,expected_binding_id:r.session_id});
  $('#import-dialog').close();await load();detail=await api('/api/lessons/'+selected);tree();render();toast('Take saved to its confirmed lesson. The recorder selection is unchanged.');
 }catch(e){toast(e.message,true)}finally{buttons.forEach(b=>b.disabled=false)}
};
async function takeAction(tid,action){
  try{
    if(action==='complete'&&!confirm('Confirm you reviewed this saved take? This does not lock the recorder.'))return;
    if(action==='approve'&&!confirm('Approve this take for the course?'))return;
    const listened=action==='audition';
    if(listened&&![...document.querySelectorAll('[data-listened]')].some(e=>e.dataset.listened===tid&&e.checked)){const n=$('#handover-notice');if(n)n.textContent='Play the take, then tick the listening confirmation before saving.';return;}
    const state=await api('/api/lessons/'+selected+'/take',{take_id:tid,action,confirmed:true,heard_speech:listened});
    if(action!=='open'){detail.recording=state;const e=catalog.lessons.find(x=>x.id===selected);if(e)e.recording=state;tree();render()}
  }catch(e){toast(e.message,true)}
}
function showMenu(btn){
  const m=$('#menu'),tid=btn.dataset.menu,t=detail.recording.takes.find(x=>x.id===tid);
  m.innerHTML=`<button data-take="${tid}" data-action="open">Play take</button><button data-take="${tid}" data-action="prefer">Use this take</button><button data-take="${tid}" data-action="approve" ${t.approved_at?'disabled':''}>Approve</button><hr><button data-take="${tid}" data-action="uploaded">Log upload…</button><button data-take="${tid}" data-action="student_access_verified" ${t.uploaded?'':'disabled'}>Log student access check…</button>`;
  m.hidden=false;const r=btn.getBoundingClientRect();m.style.top=Math.min(r.bottom+4,innerHeight-m.offsetHeight-8)+'px';m.style.left=Math.max(8,r.right-m.offsetWidth)+'px';
}
document.addEventListener('click',e=>{
  const b=e.target.closest('button');const menu=$('#menu');
  if(!menu.hidden&&!e.target.closest('#menu')&&!b?.dataset.menu)menu.hidden=true;
  if(!b)return;
  if(b.dataset.toggle){const k=b.dataset.toggle;openChapters.has(k)?openChapters.delete(k):openChapters.add(k);saveOpen();b.parentElement.classList.toggle('open');return}
  if(b.dataset.lesson){select(b.dataset.lesson);return}
  if(b.dataset.open!==undefined){openSource(b.dataset.open);return}
  if(b.dataset.menu){if(!menu.hidden&&menu.dataset.for===b.dataset.menu){menu.hidden=true;return}menu.dataset.for=b.dataset.menu;showMenu(b);return}
  if(b.dataset.take){menu.hidden=true;const a=b.dataset.action;
    if(a==='uploaded'||a==='student_access_verified'){evidence={take_id:b.dataset.take,action:a};$('#evidence-title').textContent=a==='uploaded'?'Log upload':'Log student access check';$('#evidence-url').value='';$('#evidence-note').value='';$('#evidence-dialog').showModal();return}
    takeAction(b.dataset.take,a)}
});
$('#cancel-evidence').onclick=()=>$('#evidence-dialog').close();
$('#save-evidence').onclick=async()=>{const b=$('#save-evidence');b.disabled=true;
  try{const state=await api('/api/lessons/'+selected+'/take',{...evidence,url:$('#evidence-url').value,note:$('#evidence-note').value,confirmed:true});detail.recording=state;$('#evidence-dialog').close();render()}
  catch(e){toast(e.message,true)}finally{b.disabled=false}};
$('#search').oninput=tree;
document.addEventListener('keydown',e=>{if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='k'){e.preventDefault();$('#search').focus();$('#search').select()}if(e.key==='Escape'){if(!$('#menu').hidden){$('#menu').hidden=true;return}if(rec&&!document.querySelector('dialog[open]'))leaveRec()}});

(async()=>{try{
  await load();
  const today=new Date().toLocaleDateString('en-CA');
  const start=catalog.binding?.lesson_id||catalog.lessons.find(x=>(x.recording.planned_date||x.suggested_date)===today&&!x.recording.preferred_take)?.id||catalog.lessons[0]?.id;
  if(start)await select(start);else $('#page').innerHTML='<div class="empty">No lessons are mapped yet.</div>';
}catch(e){toast(e.message,true);$('#page').innerHTML='<div class="empty">Could not load lessons.</div>'}})();
