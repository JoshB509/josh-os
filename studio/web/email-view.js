'use strict';
/* Kit-style email list and a Notion-style email page. Reuses emails.js for data, saving, links and export. */
const EV_STATUSES=['Draft','Ready','Scheduled','Published','Sent'];
const EV_MEANING={Draft:'Still being written',Ready:'Agreed. Saving it sends it to Kit and schedules it',Scheduled:'In Kit and set to send. Check it there, then mark it Published',Published:'Checked and signed off in Kit',Sent:'Gone out to subscribers'};
const evIcon={
  Draft:'<svg viewBox="0 0 16 16"><circle cx="8" cy="8" r="5.6" stroke-dasharray="2.1 1.9"/></svg>',
  Ready:'<svg viewBox="0 0 16 16"><circle cx="8" cy="8" r="5.6"/><path d="M8 2.4a5.6 5.6 0 0 1 0 11.2z" fill="currentColor" stroke="none"/></svg>',
  Scheduled:'<svg viewBox="0 0 16 16"><circle cx="8" cy="8" r="5.6"/><path d="M8 4.9V8l2.1 1.3"/></svg>',
  Published:'<svg viewBox="0 0 16 16"><circle cx="8" cy="8" r="5.6"/><path d="m5.5 8.2 1.7 1.7 3.3-3.6"/></svg>',
  Sent:'<svg viewBox="0 0 16 16"><circle cx="8" cy="8" r="6.2" fill="currentColor" stroke="none"/><path d="m5.5 8.2 1.7 1.7 3.3-3.6" stroke="#fff"/></svg>'
};
const evTypeColors=['blue','orange','green','purple','pink','brown','yellow','red'];
let evFilter='All',evMode='write';
try{evFilter=localStorage.getItem('dj-email-filter')||'All'}catch{}
emailFields.push('description','type','send_time');

function evStatus(p){return EV_STATUSES.includes(p.status)?p.status:({'Ready for review':'Ready',Processing:'Published'}[p.status]||'Draft')}
function evTypeTag(t){t=t||'Newsletter';let h=0;for(const c of t)h=(h*31+c.charCodeAt(0))>>>0;const c=t==='Newsletter'?'blue':evTypeColors[h%evTypeColors.length];return `<span class="ev-type ev-${c}">${esc(t)}</span>`}
function evClock(hhmm){if(!hhmm)return '';let [h,m]=hhmm.split(':').map(Number);const ap=h>=12?'pm':'am';h=h%12||12;return h+':'+String(m).padStart(2,'0')+ap}
function evWhen(p){
  if(!p.planned_date)return '<span class="ev-when-top ev-faint">No date</span>';
  const d=new Date(p.planned_date+'T12:00:00');
  const top=d.toLocaleDateString('en-US',{weekday:'short'})+(p.send_time?' '+evClock(p.send_time):'');
  return `<span class="ev-when-top">${top}</span><span class="ev-when-bottom">${d.toLocaleDateString('en-US',{month:'short',day:'numeric',year:'numeric'})}</span>`;
}
function evStatusIcon(p,button=true){const s=evStatus(p);return button?`<button class="ev-st ev-st-${s}" data-ev-status-menu="${esc(p.id)}" title="${s} · ${EV_MEANING[s]}">${evIcon[s]}</button>`:`<span class="ev-st ev-st-${s}">${evIcon[s]}</span>`}

emailShell=function(){
  page('Email','');
  $('#page-title').textContent='Email';
  $('#page-title').parentElement.hidden=!!emailCurrent;
  $('#crumbs').innerHTML=emailCurrent?`<button data-email-action="back">Email</button><i>/</i><span>${esc(emailCurrent.subject||'Untitled email')}</span>`:'<span>Email</span>';
};

renderEmail=function(){
  emailShell();
  if(emailCurrent){renderEmailEditor();return}
  if(emailPane==='library'){
    $('#workspace').innerHTML=`<div class="ev-toolbar"><button class="subtle" data-email-pane="plan">← All emails</button><span class="hint">Inspiration library</span></div><div id="email-content"></div>`;
    renderEmailLibrary();if(!emailRefs.length)loadEmailLibrary();return;
  }
  $('#workspace').innerHTML=`<div id="email-content"></div>`;
  renderEmailPlan();
};

renderEmailPlan=function(){
  const live=emailRows.filter(p=>!p.archived);
  const count=s=>live.filter(p=>evStatus(p)===s).length;
  const rows=live.filter(p=>evFilter==='All'||evStatus(p)===evFilter)
    .sort((a,b)=>(b.planned_date||'9999').localeCompare(a.planned_date||'9999')||(b.send_time||'').localeCompare(a.send_time||''));
  const archived=emailRows.filter(p=>p.archived);
  $('#email-content').innerHTML=`
    <div class="ev-toolbar">
      <div class="ev-tabs">${['All',...EV_STATUSES].map(s=>{const n=s==='All'?0:count(s);return `<button data-ev-filter="${s}" class="${evFilter===s?'on':''}">${s}${n&&(s==='Ready'||s==='Scheduled'||s==='Published')?`<b>${n}</b>`:''}</button>`}).join('')}</div>
      <span class="ev-tools">${evKitPill()}<button class="subtle" data-ev-library>Email inspiration</button></span>
    </div>
    <div class="ev-list">
      <div class="ev-row ev-head"><span></span><span>Subject line</span><span>Type</span><span class="ev-date">Date</span></div>
      ${rows.map(p=>`<div class="ev-row" data-ev-open="${esc(p.id)}" tabindex="0">
        ${evStatusIcon(p)}
        <span class="ev-subject-cell"><b>${esc(p.subject||'Untitled email')}</b>${p.kit_error?`<small class="ev-kit-warn">⚠ ${esc(p.kit_error)}</small>`:`<small>${esc(p.description||p.preview||'No description')}</small>`}</span>
        <span>${evTypeTag(p.type)}</span>
        <span class="ev-date">${evWhen(p)}</span>
      </div>`).join('')||`<div class="ev-empty">${evFilter==='All'?'No emails yet. Start one with New email.':'Nothing '+evFilter.toLowerCase()+' right now.'}</div>`}
    </div>
    ${archived.length?`<details class="ev-archive"><summary>Archived · ${archived.length}</summary>${archived.map(p=>`<button data-email-open="${esc(p.id)}">${esc(p.subject)}</button>`).join('')}</details>`:''}`;
};

renderEmailEditor=function(){
  const p=emailCurrent,s=evStatus(p),kit=p.kit,types=[...new Set(['Newsletter',...emailRows.map(x=>x.type).filter(Boolean)])];
  const kitLine=evKitLine(p);
  const links=(p.links||[]).map((l,i)=>`<div class="ev-link"><span>${esc(l.label)}</span><textarea class="email-literal-url" data-email-url="${i}" readonly rows="2" spellcheck="false">${esc(l.href)}</textarea><button class="btn small" data-email-copy-url="${i}">Copy link</button></div>`).join('');
  $('#workspace').innerHTML=`<div class="ev-page">
    <article class="ev-doc">
      <textarea class="ev-subject" data-email-field="subject" maxlength="500" rows="1" placeholder="Subject line">${esc(p.subject)}</textarea>
      <input class="ev-preview" data-email-field="preview" maxlength="1000" value="${esc(p.preview)}" placeholder="Preview text">
      <div class="ev-mode"><button data-ev-mode="write" class="${evMode==='write'?'on':''}">Write</button><button data-ev-mode="preview" class="${evMode==='preview'?'on':''}">Preview with links</button></div>
      <textarea class="ev-body" data-email-field="body" maxlength="200000" placeholder="Write the email…" ${evMode==='preview'?'hidden':''}>${esc(p.body)}</textarea>
      <div class="ev-reading" ${evMode==='write'?'hidden':''}><div id="email-preview-body"></div></div>
    </article>
    <aside class="ev-side">
      <section class="ev-card">
        <label class="ev-field"><span>Status</span><span class="ev-status-pick ev-st-${s}">${evIcon[s]}<select data-email-field="status">${EV_STATUSES.map(x=>`<option ${x===s?'selected':''}>${x}</option>`).join('')}</select></span></label><p class="ev-meaning hint" id="ev-meaning">${EV_MEANING[s]}</p>
        <label class="ev-field"><span>Send</span><span class="ev-when-pick"><input type="date" data-email-field="planned_date" value="${esc(p.planned_date)}"><input type="time" data-email-field="send_time" value="${esc(p.send_time||'')}"></span></label>
        <label class="ev-field"><span>Type</span><input data-email-field="type" list="ev-types" value="${esc(p.type||'Newsletter')}" placeholder="Newsletter, or a sequence name"><datalist id="ev-types">${types.map(t=>`<option value="${esc(t)}">`).join('')}</datalist></label>
        <div class="ev-save"><button class="primary" data-email-action="save" ${emailBusy?'disabled':''}>Save</button><span id="email-save-note" class="hint">${dirty?'Unsaved changes':'Saved'}</span></div>
        <div class="ev-kit">
          ${kit?`<a class="btn ev-kit-btn" href="${esc(kit.url)}" target="_blank" rel="noopener noreferrer">${kit.status==='completed'?'View report in Kit':kit.status==='scheduled'?'Check it in Kit':'Open in Kit'} ↗</a>`:evKit?.connected?'':`<button class="btn ev-kit-btn" data-ev-kit>Connect Kit to schedule automatically</button>`}
          <p class="hint">${esc(kitLine)}</p>
          ${p.kit_error?`<p class="ev-kit-error">⚠ ${esc(p.kit_error)} <button class="small subtle" data-ev-kit-retry="${esc(p.id)}">Try again</button></p>`:''}
        </div>
      </section>
      ${links?`<section class="ev-card"><h4>Link</h4>${links}<p id="email-link-warning" class="hint"></p></section>`:''}
      <details class="ev-more">
        <summary>More details</summary>
        <label>Description<textarea data-email-field="description" rows="3" maxlength="1000" placeholder="Shown under the subject in the list">${esc(p.description||'')}</textarea></label>
        <label>Audience<textarea data-email-field="audience" rows="2" maxlength="2000" placeholder="Who gets it, who's excluded">${esc(p.audience)}</textarea></label>
        <label>Tags<input data-email-tags value="${esc(p.tags.join(', '))}" placeholder="Proof, reminder"></label>
        <label>Notes<textarea data-email-field="notes" rows="3" maxlength="20000">${esc(p.notes)}</textarea></label>
        <div class="ev-sources"><span class="hint">Inspiration</span>${p.source_ids.map(id=>`<div class="email-source-chip"><button data-email-ref="${esc(id)}">${esc(emailRefs.find(r=>r.id===id)?.subject||id)}</button><button class="subtle" data-email-unlink="${esc(id)}" aria-label="Unlink source">×</button></div>`).join('')}<button class="small subtle" data-email-action="browse">Browse inspiration</button></div>
        ${emailSubjectAlternatives(p)}
        <div class="row"><button class="small subtle" data-email-action="history">Version history</button><button class="small subtle" data-email-export="json">Export draft</button><button class="small subtle danger" data-email-action="archive">${p.archived?'Restore':'Archive'}</button></div>
        ${emailSourceNotes(p)}
      </details>
    </aside>
  </div>`;
  refreshEmailLinkPreview();
};

function evQuickStatus(btn){
  const id=btn.dataset.evStatusMenu,p=emailRows.find(x=>x.id===id);
  let m=$('#ev-menu');if(!m){m=document.createElement('div');m.id='ev-menu';m.className='ev-menu';document.body.append(m)}
  m.innerHTML=EV_STATUSES.map(s=>`<button data-ev-set="${s}" data-ev-id="${esc(id)}" class="${evStatus(p)===s?'on':''}"><span class="ev-st ev-st-${s}">${evIcon[s]}</span><span><b>${s}</b><small>${EV_MEANING[s]}</small></span></button>`).join('');
  m.hidden=false;const r=btn.getBoundingClientRect();m.style.top=(r.bottom+4)+'px';m.style.left=r.left+'px';
}
async function evSetStatus(id,status){
  const p=emailRows.find(x=>x.id===id);if(!p||evStatus(p)===status)return;
  try{const r=await api('/api/emails/'+id,{revision:p.revision,changes:{status}},'PUT');await refresh();render();evKitToast(p,r)||toast('Status updated')}catch(e){toast(e.message,true)}
}
document.addEventListener('click',e=>{
  const menu=$('#ev-menu');
  const set=e.target.closest('[data-ev-set]');if(set){menu.hidden=true;evSetStatus(set.dataset.evId,set.dataset.evSet);return}
  if(menu&&!menu.hidden&&!e.target.closest('#ev-menu'))menu.hidden=true;
  const st=e.target.closest('[data-ev-status-menu]');if(st){e.stopPropagation();evQuickStatus(st);return}
  const f=e.target.closest('[data-ev-filter]');if(f){evFilter=f.dataset.evFilter;try{localStorage.setItem('dj-email-filter',evFilter)}catch{}renderEmailPlan();return}
  const md=e.target.closest('[data-ev-mode]');if(md){evMode=md.dataset.evMode;$('.ev-body').hidden=evMode!=='write';$('.ev-reading').hidden=evMode==='write';document.querySelectorAll('[data-ev-mode]').forEach(b=>b.classList.toggle('on',b===md));refreshEmailLinkPreview();return}
  const row=e.target.closest('[data-ev-open]');if(row&&!e.target.closest('button'))openEmail(row.dataset.evOpen);
},true);
document.addEventListener('keydown',e=>{const row=e.target.closest?.('[data-ev-open]');if(row&&e.key==='Enter')openEmail(row.dataset.evOpen);if(e.key==='Escape'&&$('#ev-menu'))$('#ev-menu').hidden=true});
document.addEventListener('change',e=>{const sel=e.target.closest('.ev-status-pick select');if(sel){const pick=sel.parentElement,s=sel.value;pick.className='ev-status-pick ev-st-'+s;pick.querySelector('svg').outerHTML=evIcon[s];const m=$('#ev-meaning');if(m)m.textContent=EV_MEANING[s]}});
document.addEventListener('input',e=>{if(e.target.classList?.contains('ev-subject')){const c=$('#crumbs span');if(c)c.textContent=e.target.value||'Untitled email'}});

/* Jon's and Ed's emails as an "Emails" tab in the Library, next to the thumbnails and hooks. */
let evRefs=[],evRefsLoading=false,evLibQuery='',evLibAuthor='',evLibTopic='',evLibPage=0;
async function evLoadRefs(){if(evRefsLoading)return;evRefsLoading=true;try{evRefs=await api('/api/email-inspiration?q=');emailRefs=evRefs;if(view==='inspiration'&&!current)renderLibrary()}catch(e){toast(e.message,true)}finally{evRefsLoading=false}}
function evEmailTab(){const seg=document.querySelector('.library-head .segmented');if(!seg||seg.querySelector('[data-library-kind="emails"]'))return;seg.insertAdjacentHTML('beforeend',`<button data-library-kind="emails" class="${libraryKind==='emails'?'active':''}">Emails<span>${evRefs.length||''}</span></button>`)}
const evOldLibrary=renderLibrary;
renderLibrary=function(){
  if(!evRefs.length)evLoadRefs();
  if(libraryKind!=='emails'){evOldLibrary();evEmailTab();return}
  page('Inspiration library','');
  const q=evLibQuery.toLowerCase();
  const all=evRefs.filter(r=>(!evLibAuthor||r.author===evLibAuthor)&&(!evLibTopic||r.tags.includes(evLibTopic))&&(!q||(r.subject+' '+r.excerpt+' '+r.tags.join(' ')).toLowerCase().includes(q))).sort((a,b)=>String(b.date).localeCompare(String(a.date)));
  const pages=Math.max(1,Math.ceil(all.length/24));evLibPage=Math.min(evLibPage,pages-1);
  const kinds=[['formats','Thumbnail formats'],['hooks','Creator Hooks'],['saved','Saved thumbnails']];
  $('#workspace').innerHTML=`<div class="library-head"><div class="segmented">${kinds.map(([k,n])=>`<button data-library-kind="${k}">${n}<span>${library.filter(r=>r.kind===k).length||''}</span></button>`).join('')}<button data-library-kind="emails" class="active">Emails<span>${evRefs.length||''}</span></button></div><input id="ev-lib-search" placeholder="Search subjects, topics or text…" value="${esc(evLibQuery)}"></div>
  <div class="toolbar"><div class="row"><select id="ev-lib-author" class="ev-lib-select"><option value="">All writers</option>${['Jon Brosio','Ed Lawrence'].map(a=>`<option ${a===evLibAuthor?'selected':''}>${a}</option>`).join('')}</select><select id="ev-lib-topic" class="ev-lib-select"><option value="">All topics</option>${[...new Set(evRefs.flatMap(r=>r.tags))].sort().map(t=>`<option ${t===evLibTopic?'selected':''}>${esc(t)}</option>`).join('')}</select></div><span class="count">${evRefsLoading&&!evRefs.length?'Loading emails…':all.length+' emails'}</span></div>
  <div class="inspiration-grid">${all.slice(evLibPage*24,evLibPage*24+24).map(r=>`<article class="inspiration-card ev-ref-card"><button class="ev-ref-cover" data-email-ref="${esc(r.id)}"><span>${esc(r.author)} · ${esc(String(r.date).slice(0,10))}</span><strong>${esc(r.subject)}</strong><small>${esc(r.excerpt)}</small></button><div class="inspiration-inside"><div class="tags">${r.tags.map(t=>`<span>${esc(t)}</span>`).join('')}${r.used_in.length?`<span class="ev-used">Used in ${r.used_in.length}</span>`:''}</div><div class="row"><button class="subtle small" data-email-ref="${esc(r.id)}">Read email</button><button class="small" data-ev-use-ref="${esc(r.id)}">+ Email</button></div></div></article>`).join('')||'<p class="empty">No emails match.</p>'}</div>
  <div class="pagination"><button data-ev-lib-page="-1" ${evLibPage===0?'disabled':''}>← Previous</button><span>${evLibPage+1} / ${pages}</span><button data-ev-lib-page="1" ${evLibPage+1>=pages?'disabled':''}>Next →</button></div>`;
};
document.addEventListener('click',async e=>{
  const go=e.target.closest('[data-ev-library]');if(go){if(!leave())return;libraryKind='emails';view='inspiration';render();return}
  const browse=e.target.closest('[data-email-action="browse"]');if(browse){e.stopImmediatePropagation();if(dirty){toast('Save the email before browsing inspiration.',true);return}if(!leave())return;libraryKind='emails';view='inspiration';render();return}
  const use=e.target.closest('[data-ev-use-ref]');if(use){const r=evRefs.find(x=>x.id===use.dataset.evUseRef);if(r)await newEmail(r);return}
  const pg=e.target.closest('[data-ev-lib-page]');if(pg){evLibPage+=Number(pg.dataset.evLibPage);renderLibrary();$('main').scrollTo?.(0,0);window.scrollTo(0,0);return}
},true);
document.addEventListener('input',e=>{if(e.target.id==='ev-lib-search'){evLibQuery=e.target.value;evLibPage=0;const pos=e.target.selectionStart;renderLibrary();const i=$('#ev-lib-search');i.focus();i.setSelectionRange(pos,pos)}});
document.addEventListener('change',e=>{if(e.target.id==='ev-lib-author'){evLibAuthor=e.target.value;evLibPage=0;renderLibrary()}if(e.target.id==='ev-lib-topic'){evLibTopic=e.target.value;evLibPage=0;renderLibrary()}});
if(view==='email'&&!current)render();

/* Kit: the app schedules Ready emails in Kit and keeps their status in step. */
var evKit=null,evKitSynced=0; // var, not let: the email view can render before this line runs when Josh OS opens on #email
function evKitLoad(){return api('/api/kit').then(k=>{evKit=k;if(view==='email'&&!current)render()}).catch(()=>{})}
function evKitPill(){if(!evKit)return '';return evKit.connected?`<button class="subtle ev-kit-pill on" data-ev-kit title="${esc(evKit.last_error||'Connected to Kit')}"><i></i>Kit · ${esc(evKit.segment_name)}${evKit.last_error?' ⚠':''}</button>`:`<button class="ev-kit-pill" data-ev-kit><i></i>Connect Kit</button>`}
function evSendText(iso){if(!iso)return '';const d=new Date(iso);return d.toLocaleDateString('en-GB',{weekday:'short',day:'numeric',month:'short',timeZone:'Asia/Dubai'})+', '+d.toLocaleTimeString('en-GB',{hour:'numeric',minute:'2-digit',hour12:true,timeZone:'Asia/Dubai'}).replace(' ','')}
function evKitLine(p){const k=p.kit;
  if(!k)return evKit?.connected?(p.status==='Draft'?'Goes to Kit when you set it to Ready and save':'Not in Kit yet'):'Not in Kit yet';
  const synced=k.synced_at?' · checked '+new Date(k.synced_at).toLocaleString('en-GB',{day:'numeric',month:'short',hour:'numeric',minute:'2-digit'}):'';
  if(k.status==='scheduled')return 'Scheduled in Kit for '+evSendText(k.send_at)+synced;
  if(k.status==='completed')return 'Sent from Kit'+(k.send_at?' on '+evSendText(k.send_at):'')+synced;
  if(k.status==='sending')return 'Sending from Kit now'+synced;
  return 'Draft in Kit, not scheduled yet'+synced}
function evKitToast(before,after){if(!after)return false;
  if(after.kit_error){toast('Saved here. Kit: '+after.kit_error,true);return true}
  const k=after.kit,b=before.kit||{};
  if(k&&k.status==='scheduled'&&(b.status!=='scheduled'||b.send_at!==k.send_at||b.pushed_hash!==k.pushed_hash)){toast('Saved and scheduled in Kit for '+evSendText(k.send_at));return true}
  if(k&&k.status==='draft'&&b.status==='scheduled'){toast('Saved and unscheduled in Kit');return true}
  return false}
const _evSave=saveEmail;
saveEmail=async function(){const before=structuredClone(emailCurrent||{});const ok=await _evSave();if(ok&&emailCurrent)evKitToast(before,emailCurrent);return ok};
async function evKitSync(force){if(!evKit?.connected)return;if(!force&&Date.now()-evKitSynced<120000)return;evKitSynced=Date.now();
  try{const r=await api('/api/kit/sync',{});if(r.changed){await refresh();if(view==='email')render()}await evKitLoad();if(force)toast(r.errors?.length?'Kit: '+r.errors[0]:'Kit checked'+(r.changed?' · '+r.changed+' updated':''),!!r.errors?.length)}catch(e){if(force)toast(e.message,true)}}
function evKitDialog(){const k=evKit||{};
  const d=dialog(`<h2>Kit</h2>
    <p class="hint">When an email is Ready and you save it, the app puts it in Kit and schedules it for its date at 5pm Dubai unless you set another time. Every ten minutes it checks Kit and marks anything that has gone out as Sent.</p>
    <label>API key<input id="ev-kit-key" type="password" autocomplete="off" spellcheck="false" placeholder="${k.has_key?'Saved, ending '+esc(k.key_end)+'. Paste a new one to replace it':'Paste your Kit V4 API key'}"></label>
    <p class="hint">In Kit, open Settings, then Developer, and add a new V4 key. It stays in your Data folder on this computer.</p>
    <label>Audience segment<select id="ev-kit-seg">${(k.segments||[]).length?'<option value="">Choose a segment</option>'+k.segments.map(s=>`<option value="${s.id}" ${s.id===k.segment_id?'selected':''}>${esc(s.name)}</option>`).join(''):'<option value="">Check your key first to load segments</option>'}</select></label>
    <p class="hint">Kit only lets apps send to one audience group, so make a segment in Kit for the newsletter audience (Newsletter tag, excluding cold subscribers and the customer tag) and choose it here.</p>
    <div class="row"><button class="primary" id="ev-kit-save">${k.has_key?'Save':'Check key'}</button>${k.connected?'<button id="ev-kit-now">Check Kit now</button><button class="subtle danger" id="ev-kit-off">Disconnect</button>':''}</div>
    ${k.last_error?`<p class="ev-kit-error">⚠ ${esc(k.last_error)}</p>`:''}`,'ev-kit-dialog');
  d.querySelector('#ev-kit-save').onclick=async()=>{const b=d.querySelector('#ev-kit-save');b.disabled=true;
    try{evKit=await api('/api/kit/connect',{api_key:d.querySelector('#ev-kit-key').value.trim(),segment_id:d.querySelector('#ev-kit-seg').value});
      if(evKit.connected){closeDialog();toast('Kit connected. Ready emails will be scheduled for '+evKit.segment_name);setTimeout(()=>{refresh().then(render)},4000);render()}else{evKitDialog();toast('Key works. Now choose the audience segment')}}
    catch(e){toast(e.message,true)}finally{b.disabled=false}};
  d.querySelector('#ev-kit-now')?.addEventListener('click',()=>{closeDialog();evKitSync(true)});
  d.querySelector('#ev-kit-off')?.addEventListener('click',async()=>{evKit=await api('/api/kit/disconnect',{});closeDialog();render();toast('Kit disconnected. Nothing in Kit was changed')});
}
document.addEventListener('click',async e=>{
  if(e.target.closest('[data-ev-kit]')){e.preventDefault();await evKitLoad();evKitDialog();return}
  const r=e.target.closest('[data-ev-kit-retry]');if(r){r.disabled=true;try{const before=emailRows.find(x=>x.id===r.dataset.evKitRetry)||{};const p=await api('/api/emails/'+r.dataset.evKitRetry+'/kit',{});await refresh();if(emailCurrent?.id===p.id&&!dirty)emailCurrent=structuredClone(p);render();evKitToast(before,p)||toast('Kit is up to date')}catch(err){toast(err.message,true)}finally{r.disabled=false}}
});
evKitLoad().then(()=>evKitSync(false));
setInterval(()=>{if(view==='email'&&document.visibilityState==='visible')evKitSync(false)},300000);

/* Calendar slots (18 Oct 2026). Each day holds fixed slots in order: video, email, client calls, tasks.
   Cards are thin: one line for the title, one for a status icon and status (calls show the time).
   Presentation only: it rearranges the cards the calendar already drew, so dates, drag and drop and clicks behave as before.
   Calls and tasks have no local source yet. Set window.calendarCalls = [{date:'2026-10-21', person:'Kate', time:'14:00'}]
   or window.calendarTasks = [{date:'2026-10-21', title:'Send invoice', status:'To do'}] and they fill their slots. */
const slotOldCalendar=renderCalendar;
const SLOT_TYPES=[['video','Video'],['email','Email'],['call','Client call'],['task','Task']];
const SLOT_ICONS={
 video:'<rect x="1.8" y="3.5" width="12.4" height="9" rx="2.2"/><path d="m6.8 6.3 3.2 1.7-3.2 1.7z"/>',
 email:'<rect x="2" y="3" width="12" height="10" rx="2"/><path d="m2 4 6 5 6-5"/>',
 call:'<circle cx="8" cy="5.5" r="2.5"/><path d="M3 14c.6-2.8 2.6-4.2 5-4.2s4.4 1.4 5 4.2"/>',
 task:'<rect x="2.5" y="2.5" width="11" height="11" rx="2.5"/><path d="m5.5 8 2 2 3-3.5"/>',
 idea:'<path d="M6 12.5h4M6.6 14.5h2.8M8 1.8a4.2 4.2 0 0 0-2.5 7.6c.5.4.8.9.8 1.5v.3h3.4v-.3c0-.6.3-1.1.8-1.5A4.2 4.2 0 0 0 8 1.8z"/>',
 pen:'<path d="M10.5 2.5l3 3L6 13H3v-3z"/>',
 rec:'<circle cx="8" cy="8" r="5.8"/><circle cx="8" cy="8" r="2.4" fill="currentColor" stroke="none"/>',
 check:'<path d="M2.8 8.6l3.2 3.2 7.2-7.4"/>',
 cut:'<circle cx="4.5" cy="11.5" r="2"/><circle cx="11.5" cy="11.5" r="2"/><path d="M6 10 12 2M10 10 4 2"/>',
 eye:'<path d="M1.5 8S4 3.5 8 3.5 14.5 8 14.5 8 12 12.5 8 12.5 1.5 8 1.5 8z"/><circle cx="8" cy="8" r="2"/>',
 clock:'<circle cx="8" cy="8" r="6"/><path d="M8 4.6V8l2.4 1.5"/>',
 sent:'<path d="M14 2 7 9M14 2l-4.5 12L7 9 2 6.5z"/>',
 dot:'<circle cx="8" cy="8" r="3" fill="currentColor" stroke="none"/>'
};
const SLOT_VIDEO_ICON={'Idea':'idea','Scripting':'pen','Ready to record':'rec','Recorded':'check','Editing':'cut','Review':'eye','Scheduled':'clock','Published':'sent'};
function slotEmailLook(status){const s=String(status||'').toLowerCase();
 if(s.startsWith('sent')||s.startsWith('published'))return ['sent','#0f7b6c'];
 if(s.startsWith('scheduled'))return ['clock','#2383e2'];
 if(s.startsWith('ready'))return ['eye','#6940a5'];
 if(s.startsWith('draft'))return ['pen','#9b9a97'];
 return ['dot','#9b9a97'];}
function slotSvg(name){return `<svg viewBox="0 0 16 16" aria-hidden="true">${SLOT_ICONS[name]||SLOT_ICONS.dot}</svg>`}
function slotInner(title,icon,color,status){return `<span class="slot-title">${esc(title||'Untitled')}</span><span class="slot-status" style="--st:${color}">${slotSvg(icon)}<span>${esc(status||'')}</span></span>`}
function slotDecorate(card,projects,emails){
 if(card.dataset.emailOpen){const p=emails.get(card.dataset.emailOpen);if(!p)return;
  const full=emailPlannerStatus(p),short=String(full).replace(/\s*\(manual\)/i,''),[icon,color]=slotEmailLook(full);
  card.classList.remove('email-cal-card');card.classList.add('slot-card','t-email');card.dataset.slotType='email';
  card.innerHTML=slotInner(p.subject,icon,color,short);
  card.title=[p.subject,full,emailManualSchedule(p)?'':'Approval pending',p.reported_schedule?'Reported schedule, unverified':''].filter(Boolean).join('\n');return;}
 const p=projects.get(card.dataset.open);if(!p)return;const i=state.stages.indexOf(p.stage);
 card.classList.add('slot-card','t-video');card.dataset.slotType='video';
 card.innerHTML=slotInner(p.name,SLOT_VIDEO_ICON[p.stage]||'dot',colors[i]||'#9b9a97',p.stage);card.title=p.name+'\n'+p.stage;}
function slotExtraCard(type,title,icon,status){const d=document.createElement('div');d.className='cal-project slot-card t-'+type;d.dataset.slotType=type;d.innerHTML=slotInner(title,icon,type==='call'?'#0f7b6c':'#d9730d',status);d.title=title+(status?'\n'+status:'');return d}
renderCalendar=function(){slotOldCalendar();
 const cal=document.querySelector('#workspace .calendar');if(!cal)return;cal.classList.add('slotted');
 const projects=new Map(state.projects.map(p=>[p.id,p])),emails=new Map((typeof emailRows!=='undefined'?emailRows:[]).map(p=>[p.id,p]));
 document.querySelectorAll('#workspace .cal-project').forEach(c=>slotDecorate(c,projects,emails));
 const calls=Array.isArray(window.calendarCalls)?window.calendarCalls:[],tasks=calendarTaskRows;
 cal.querySelectorAll('.day[data-date]').forEach(day=>{
  const date=day.dataset.date,slots={video:[],email:[],call:[],task:[]};
  day.querySelectorAll(':scope>.cal-project').forEach(c=>slots[c.dataset.slotType==='email'?'email':'video'].push(c));
  calls.filter(c=>c.date===date).sort((a,b)=>String(a.time).localeCompare(String(b.time))).forEach(c=>slots.call.push(slotExtraCard('call',c.person,'clock',c.time)));
  tasks.filter(t=>t.due_date===date).forEach(t=>slots.task.push(calendarTaskCard(t)));
  const box=document.createElement('div');box.className='slots';
  for(const [type,label] of SLOT_TYPES){const s=document.createElement('div');s.className='slot';s.dataset.slot=type;
   if(slots[type].length)s.append(...slots[type]);else s.innerHTML=`<span class="slot-empty" title="No ${label.toLowerCase()}">${slotSvg(type)}</span>`;
   box.append(s);}
  day.append(box);
 });
 const note=document.querySelector('#workspace .email-calendar-note');
 if(note)note.outerHTML=`<p class="slot-legend">${SLOT_TYPES.map(([t,l])=>`<span class="t-${t}"><i></i>${l}s</span>`).join('')}</p>`;
};
