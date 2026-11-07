'use strict';
// Email, built around Josh's flow. Laia writes, Josh reads it the way a subscriber will,
// edits if he needs to, and approves it with its send time. Then it goes to Kit and out.
// The full editor (links, sources, history, export) and the full list stay one click away.
let emailFull=false,emailEditing=false,emailFlowSaver=null,emailCompare=false,emailSourceAt=0,emailSources={};
const EMAIL_STEPS=[['write','Write'],['approve','Approve'],['kit','In Kit'],['sent','Sent']];

function emailToday(){const d=new Date();d.setHours(12,0,0,0);return d}
function emailKey(d){return d.toISOString().slice(0,10)}
function emailDay(k,long){return k?new Date(k+'T12:00:00').toLocaleDateString(undefined,long?{weekday:'long',day:'numeric',month:'long'}:{weekday:'short',day:'numeric',month:'short'}):'No date'}
function emailTime(p){return p.send_time?evClock(p.send_time):''}
function emailPast(p){return !!p.planned_date&&p.planned_date<emailKey(emailToday())}
function emailStep(p){
  const s=evStatus(p);
  if(s==='Sent')return {key:'sent',label:'Sent'};
  if(s==='Scheduled'||s==='Published')return {key:'kit',label:s==='Published'?'Scheduled in Kit':'In Kit, check it there',done:s==='Published'};
  if(s==='Ready')return {key:'kit',label:evKit?.connected?'Approved, going to Kit':'Approved, waiting for Kit'};
  if(!(p.body||'').trim())return {key:'write',label:'Being written'};
  return {key:'approve',label:emailPast(p)?'Ready for your review, and its date has passed':'Ready for your review'};
}
function emailSaver(){
  if(!emailFlowSaver)emailFlowSaver=makeAutosaver({url:p=>'/api/emails/'+p.id,note:t=>{const n=$('#ef-save');if(n)n.textContent=t},
    onSaved:p=>{const i=emailRows.findIndex(x=>x.id===p.id);if(i>=0)emailRows[i]=structuredClone(p)}});
  return emailFlowSaver;
}

// The queue -------------------------------------------------------------------
function renderEmailQueue(){
  if(!evKit&&typeof evKitLoad==='function')evKitLoad();
  const live=emailRows.filter(p=>!p.archived),today=emailToday(),tk=emailKey(today);
  const days=Array.from({length:14},(_,i)=>{const d=new Date(today);d.setDate(d.getDate()+i);return d});
  const weekOut=emailKey(days[6]);
  const drafts=live.filter(p=>evStatus(p)==='Draft');
  const missed=drafts.filter(emailPast).sort((a,b)=>a.planned_date.localeCompare(b.planned_date));
  const needs=drafts.filter(p=>!emailPast(p)&&(p.body||'').trim()&&(!p.planned_date||p.planned_date<=weekOut)).sort((a,b)=>(a.planned_date||'9999').localeCompare(b.planned_date||'9999'));
  const later=drafts.filter(p=>!emailPast(p)&&!needs.includes(p)).sort((a,b)=>(a.planned_date||'9999').localeCompare(b.planned_date||'9999'));
  const approved=live.filter(p=>['Ready','Scheduled','Published'].includes(evStatus(p))&&p.planned_date>=tk).sort((a,b)=>a.planned_date.localeCompare(b.planned_date));
  const sent=live.filter(p=>evStatus(p)==='Sent').sort((a,b)=>(b.planned_date||'').localeCompare(a.planned_date||'')).slice(0,6);
  const next=approved[0];
  const lead=next?`The next approved email goes out ${emailDay(next.planned_date,true)}${emailTime(next)?` at ${emailTime(next)}`:''}.`:`Nothing is approved yet, so nothing is set to go out.`;
  const kitLine=evKit&&!evKit.connected?`<p class="eq-kit">Kit isn't connected, so approved emails can't be scheduled from here yet. <button class="link" data-ev-kit>Connect Kit</button></p>`:'';
  const row=p=>`<button class="eq-row" data-email-go="${esc(p.id)}"><span class="eq-row-main"><b>${esc(p.subject||'Untitled email')}</b><span>${esc(p.preview||p.description||'')}</span></span><span class="eq-row-when">${esc(emailDay(p.planned_date))}</span></button>`;
  $('#workspace').innerHTML=`<div class="cq eq">
    <div class="cq-top"><div><p class="cq-lead">${esc(lead)}</p>${kitLine}</div>
      <div class="cv-tabs" role="tablist"><button role="tab" aria-selected="true">Queue</button><button role="tab" aria-selected="false" data-email-pane="all">All emails</button><button role="tab" aria-selected="false" data-email-pane="library">Inspiration</button></div></div>
    ${missed.length?`<section class="eq-missed"><div><b>${missed.length} draft${missed.length===1?'':'s'} missed ${missed.length===1?'its':'their'} send date.</b><span>They were planned between ${esc(emailDay(missed[0].planned_date))} and ${esc(emailDay(missed.at(-1).planned_date))} and never went out.</span></div>
      <button class="primary" data-dock-ask="Some drafts missed their send dates. Re-plan every draft whose date has passed into open days from tomorrow, at our usual pace, keeping the best ones first. Save the new dates and tell me the plan.">Ask Laia to re-plan them</button></section>`:''}
    ${needs.length?`<section><h2>Needs you</h2><div class="eq-list">${needs.map(row).join('')}</div></section>`:''}
    <section class="cq-weeks"><h2>Next two weeks</h2><div class="cq-strip eq-strip">${days.map(d=>{const k=emailKey(d),es=live.filter(p=>p.planned_date===k);
      return `<div class="cq-day ${d.getDay()===0||d.getDay()===6?'wkend':''}"><span class="cq-date"><b>${d.getDate()}</b>${esc(d.toLocaleDateString(undefined,{weekday:'short'}))}</span>${es.length?es.map(p=>`<button class="eq-slot s-${esc(evStatus(p).toLowerCase())}" data-email-go="${esc(p.id)}"><span class="eq-slot-subject">${esc(p.subject||'Untitled')}</span><span class="cq-slot-stage">${esc(emailStep(p).key==='approve'?'For review':evStatus(p)==='Draft'?'Being written':evStatus(p)==='Ready'?'Approved':evStatus(p))}${emailTime(p)?`, ${esc(emailTime(p))}`:''}</span></button>`).join(''):`<button class="cq-open eq-open" data-dock-ask="Plan an email for ${esc(emailDay(k,true))}">Open</button>`}</div>`}).join('')}</div></section>
    ${later.length?`<section class="cq-works"><h2>Later</h2>${later.map(p=>`<button class="cq-row" data-email-go="${esc(p.id)}"><span class="cq-row-title">${esc(p.subject||'Untitled email')}</span><span class="cq-row-meta">${esc(emailStep(p).label)}, for ${esc(emailDay(p.planned_date))}</span></button>`).join('')}</section>`:''}
    ${sent.length?`<section class="cq-works"><h2>Recently sent</h2>${sent.map(p=>`<button class="cq-row" data-email-go="${esc(p.id)}"><span class="cq-row-title">${esc(p.subject)}</span><span class="cq-row-meta">${esc([emailResultLine(p),emailDay(p.planned_date)].filter(Boolean).join(', '))}</span></button>`).join('')}</section>`:''}
  </div>`;
}

// One email ---------------------------------------------------------------------
function emailLetter(p){
  if(emailEditing)return `<textarea class="ef-body-edit" data-ef-field="body" placeholder="Write the email">${esc(p.body)}</textarea>`;
  const linked=emailLinkedBody(p),html=(linked.html||esc(p.body).replace(/\n/g,'<br>')).replace(/\{\{\s*subscriber\.first_name\s*\}\}/g,'<span class="ef-merge">First name</span>');
  return (p.body||'').trim()?`<div class="ef-letter">${html}</div>`:`<div class="ef-letter ef-empty"><p>Nothing written yet.</p><button class="primary" data-dock-ask="Write this email in full and save it to it.">Ask Laia to write it</button></div>`;
}
function emailPct(v){if(v==null||v==='')return null;v=+v;return (v<=1?v*100:v).toFixed(1)+'%'}
function emailResultLine(p){const r=p.results||{},o=emailPct(r.open_rate),c=emailPct(r.click_rate);return [o&&`${o} opened`,c&&`${c} clicked`].filter(Boolean).join(', ')}
function emailResults(p){
  const r=p.results||{};if(evStatus(p)!=='Sent')return '';
  if(!Object.keys(r).filter(k=>k!=='checked_at'&&k!=='kit_broadcast_id').length)return `<section class="pv-card"><h3>How it did</h3><p class="hint">No numbers yet. Laia reads them from Kit.</p></section>`;
  const stat=(v,l)=>v==null||v===''?'':`<div><b>${esc(String(v))}</b><span>${l}</span></div>`;
  return `<section class="pv-card"><h3>How it did</h3><div class="pv-stats">${stat(emailPct(r.open_rate),'opened')}${stat(emailPct(r.click_rate),'clicked')}${stat(r.recipients,'sent to')}${stat(r.unsubscribes,'unsubscribed')}${stat(r.new_people,'new people')}</div>${r.checked_at?`<p class="hint">Checked ${esc(r.checked_at)}.</p>`:''}</section>`;
}
function emailSteps(p){
  const s=emailStep(p),at=EMAIL_STEPS.findIndex(x=>x[0]===s.key)+(s.done?1:0);
  return `<ol class="ps" aria-label="Progress">${EMAIL_STEPS.map(([k,l],i)=>`<li class="${i<at?'done':i===at?'now':''}"${i===at?' aria-current="step"':''}>${esc(l)}</li>`).join('')}</ol>`;
}
function emailCompareButton(p){return (p.source_ids||[]).length?`<button class="subtle" data-ef-compare>${emailCompare?'Hide the original':'Compare with the original'}</button>`:`<button class="subtle" data-dock-ask="Which library email is this one based on? Link it in source_ids so I can compare them side by side. If it isn't based on one, say so.">Find the original</button>`}
function emailActions(p){return emailActionsBase(p).replace(/<\/div>(?![\s\S]*<\/div>)/,emailCompareButton(p)+'</div>')||`<div class="pa">${emailCompareButton(p)}</div>`}
function emailActionsBase(p){
  const s=emailStep(p),st=evStatus(p);
  const edit=`<button class="subtle" data-ef-edit>${emailEditing?'Done editing':'Edit'}</button>`;
  const when=`<label class="pa-when"><span>Send on</span><input type="date" data-ef-field="planned_date" value="${esc(p.planned_date)}"><input type="time" data-ef-field="send_time" value="${esc(p.send_time||'')}" aria-label="Send time"></label>`;
  if(s.key==='write')return `<div class="pa"><button class="primary" data-dock-ask="Write this email in full and save it to it.">Ask Laia to write it</button>${edit}</div>`;
  if(s.key==='approve')return `<div class="pa">${when}<button class="primary" data-ef-approve>Approve</button>${edit}</div>${emailPast(p)?'<p class="pa-warn">Its send date has passed, so pick a new one before you approve it.</p>':''}`;
  if(st==='Ready')return `<div class="pa"><p class="pa-note">${evKit?.connected?'Approved. It goes to Kit and gets scheduled there.':'Approved. Kit isn’t connected, so it can’t be scheduled from here yet.'}</p>${evKit?.connected?'':'<button class="subtle" data-ev-kit>Connect Kit</button>'}<button class="subtle" data-ef-status="Draft">Undo approval</button></div>`;
  if(p.kit?.url)return `<div class="pa"><p class="pa-note">${esc(evKitLine(p))}</p><a class="subtle-link" href="${esc(p.kit.url)}" target="_blank" rel="noopener noreferrer">${st==='Sent'?'See the report in Kit':'Check it in Kit'}</a></div>`;
  return '';
}
async function emailSource(id){if(!emailSources[id])emailSources[id]=await api('/api/email-inspiration/'+encodeURIComponent(id));return emailSources[id]}
function emailOriginal(p){
  const ids=p.source_ids||[];if(!ids.length)return '';
  const id=ids[Math.min(emailSourceAt,ids.length-1)],r=emailSources[id];
  const pick=ids.length>1?`<div class="cv-window ef-pick" role="group" aria-label="Original">${ids.map((x,i)=>`<button data-ef-source="${i}" aria-pressed="${i===emailSourceAt}">${esc((emailSources[x]?.author||x.split(':')[0]).split(' ')[0])} ${i+1}</button>`).join('')}</div>`:'';
  if(!r){emailSource(id).then(()=>{if(emailCurrent?.id===p.id&&emailCompare)renderEmailReview()}).catch(e=>toast(e.message,true));return `<article class="ef-mail ef-orig"><p class="hint">Loading the original…</p></article>`}
  const day=r.date?new Date(r.date).toLocaleDateString(undefined,{day:'numeric',month:'short',year:'numeric'}):'';
  return `<article class="ef-mail ef-orig">${pick}
    <div class="ef-inbox"><span class="ef-from">${esc(r.author)}${day?`, ${esc(day)}`:''}</span><b class="ef-orig-subject">${esc(r.subject)}</b>${r.source_url?`<a class="ef-orig-link" href="${esc(r.source_url)}" target="_blank" rel="noopener noreferrer">Open in Gmail</a>`:''}</div>
    <div class="ef-letter">${esc(r.body).replace(/\n/g,'<br>')}</div></article>`;
}
function renderEmailReview(){
  const p=emailCurrent;
  $('#workspace').innerHTML=`<div class="ef">
    <header class="pv-head">
      <textarea class="pv-title ef-subject" data-ef-field="subject" rows="1" maxlength="500" aria-label="Subject line" placeholder="Subject line">${esc(p.subject)}</textarea>
      ${emailSteps(p)}
      <p class="pv-when">${esc(emailStep(p).label)}. ${p.planned_date?`${evStatus(p)==='Sent'?'Went':'Goes'} out ${esc(emailDay(p.planned_date,true))}${emailTime(p)?` at ${esc(emailTime(p))}`:''}.`:'No send date yet.'}<span id="ef-save" class="save-note"></span></p>
      <div id="ef-actions">${emailActions(p)}</div>
    </header>
    <div class="ef-body ${emailCompare&&(p.source_ids||[]).length?'ef-compare':''}">
      <article class="ef-mail">${emailCompare&&(p.source_ids||[]).length?'<p class="ef-label">Laia’s draft</p>':''}
        <div class="ef-inbox"><span class="ef-from">Josh Brindley</span><input class="ef-preview" data-ef-field="preview" value="${esc(p.preview)}" maxlength="1000" placeholder="Preview text, the line people see in their inbox"></div>
        <div id="ef-letter">${emailLetter(p)}</div>
      </article>
      ${emailCompare&&(p.source_ids||[]).length?emailOriginal(p):`<aside class="ef-side">
        ${emailResults(p)}
        ${p.notes?`<details class="pv-card ef-notes"><summary>Laia's notes</summary><p class="pv-notes">${esc(p.notes)}</p></details>`:''}
        ${(p.subject_alternatives||[]).length?`<section class="pv-card"><h3>Other subject lines</h3>${p.subject_alternatives.map(s=>`<button class="ef-alt" data-ef-subject="${esc(typeof s==='string'?s:s.text||'')}">${esc(typeof s==='string'?s:s.text||'')}</button>`).join('')}</section>`:''}
        ${(p.links||[]).length?`<section class="pv-card"><h3>Link in this email</h3>${p.links.map(l=>`<p class="ef-link"><b>${esc(l.label)}</b><span>${esc(l.href)}</span></p>`).join('')}</section>`:''}
        <section class="pv-card"><button class="link" data-ef-full>Open the full editor</button><p class="hint">For links, sources, type, audience, version history and export.</p></section>
      </aside>`}
    </div>
  </div>`;
  document.querySelectorAll('.ef-body-edit,.ef-subject').forEach(roomGrow);
}
function emailRefreshTop(){const p=emailCurrent;if(!p)return;const a=$('#ef-actions');if(a)a.innerHTML=emailActions(p);const s=$('.ef .ps');if(s)s.outerHTML=emailSteps(p)}
async function emailSetStatus(p,status){
  const saver=emailSaver();saver.change(p,'status',status);await saver.flush();
  if(status==='Ready'&&evKit?.connected)setTimeout(()=>{refresh().then(()=>{const fresh=emailRows.find(x=>x.id===p.id);if(fresh&&emailCurrent?.id===p.id){emailCurrent=structuredClone(fresh);renderEmailReview()}}).catch(()=>{})},1500);
  renderEmailReview();
}

// Routing: the queue and review page replace the list and editor; the old ones stay reachable.
const emailFlowBase=renderEmail;
renderEmail=function(){
  if(emailCurrent&&emailFull)return emailFlowBase();
  if(emailCurrent){emailShell();return renderEmailReview()}
  if(emailPane==='library'||emailPane==='all'){emailShell();if(emailPane==='all'){$('#workspace').innerHTML=`<div class="ev-toolbar"><button class="subtle" data-email-pane="plan">← Queue</button></div><div id="email-content"></div>`;renderEmailPlan();return}return emailFlowBase()}
  emailShell();renderEmailQueue();
};
const emailFlowOpen=openEmail;
openEmail=function(id){emailFull=false;emailEditing=false;emailCompare=false;emailSourceAt=0;return emailFlowOpen(id)};

document.addEventListener('click',async e=>{
  const go=e.target.closest('[data-email-go]');
  if(go){e.preventDefault();if(typeof leave==='function'&&!leave())return;current=null;view='email';emailPane='plan';openEmail(go.dataset.emailGo);scrollTo(0,0);return}
  if(view!=='email')return;
  const pane=e.target.closest('[data-email-pane]');if(pane&&!emailCurrent){emailPane=pane.dataset.emailPane;render();return}
  if(!emailCurrent||emailFull)return;const p=emailCurrent;
  if(e.target.closest('[data-ef-edit]')){emailEditing=!emailEditing;if(!emailEditing)await emailSaver().flush();renderEmailReview();if(emailEditing)document.querySelector('.ef-body-edit')?.focus();return}
  if(e.target.closest('[data-ef-compare]')){emailCompare=!emailCompare;emailSourceAt=0;renderEmailReview();return}
  const src=e.target.closest('[data-ef-source]');if(src){emailSourceAt=+src.dataset.efSource;renderEmailReview();return}
  if(e.target.closest('[data-ef-full]')){await emailSaver().flush();emailFull=true;render();return}
  const alt=e.target.closest('[data-ef-subject]');if(alt){emailSaver().change(p,'subject',alt.dataset.efSubject);renderEmailReview();return}
  if(e.target.closest('[data-ef-approve]')){
    if(!p.planned_date||emailPast(p)){toast('Pick a send date from today onwards first',true);document.querySelector('.pa-when input[type=date]')?.focus();return}
    await emailSetStatus(p,'Ready');toast(evKit?.connected?'Approved. It’s going to Kit.':'Approved');return}
  const st=e.target.closest('[data-ef-status]');if(st){await emailSetStatus(p,st.dataset.efStatus);return}
});
document.addEventListener('input',e=>{
  if(view!=='email'||!emailCurrent||emailFull)return;const f=e.target.dataset.efField;if(!f||e.target.type==='date'||e.target.type==='time')return;
  emailSaver().change(emailCurrent,f,e.target.value);if(f==='body'||f==='subject')roomGrow(e.target);
});
document.addEventListener('change',e=>{
  if(view!=='email'||!emailCurrent||emailFull)return;const f=e.target.dataset.efField;if(!f||(e.target.type!=='date'&&e.target.type!=='time'))return;
  emailSaver().change(emailCurrent,f,e.target.value);setTimeout(emailRefreshTop,0);
});
// Leaving an email saves anything still waiting.
const emailFlowLeave=leave;
leave=function(){if(emailFlowSaver)emailFlowSaver.flush();return emailFlowLeave()};
