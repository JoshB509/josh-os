'use strict';
// Customer center: everyone Anouk looks after, plus every client and course buyer CareerOS can see.
// Overview, Leads, Clients and Learners. Leads and the people Anouk watches come from her tracker
// (/api/people). Clients, their folders and transcripts, and course buyers come from Admin
// (/api/admin/...), which reads Stripe, Calendly and the client folders. Nothing here sends a message
// or books a call. Where a source isn't connected, the page says so.
let people=null,peopleTab='all',personOpen=null,personTimer=null,personBusy=null;
let ccTab='overview',ccClient=null,ccClientData=null,ccDoc=null,ccAdmin=null,ccAdminLoading=null,ccScrollTo=null;
const ccQ={leads:'',clients:'',learners:''};
const CC_TABS=[['overview','Overview'],['leads','Leads'],['clients','Clients'],['learners','Learners'],['replies','Replies']];
// Anouk's lead rules, the same as team/playbooks/anouk/lead-rules.md. One natural reply, then at most two
// check-ins, each three to four weeks after the last message Josh sent.
const CC_RULE={gap:21,late:28,checkins:2};
const CC_CLOSED=['Bought','Not now','Said yes','Said no'];

function pToday(){return (typeof dateKey==='function'&&typeof plannerToday==='function')?dateKey(plannerToday()):new Date().toISOString().slice(0,10)}
function pDue(d){if(!d)return '';const n=typeof deskDays==='function'?deskDays(d):0;return n<0?`overdue since ${typeof deskWhen==='function'?deskWhen(d):d}`:`due ${typeof deskWhen==='function'?deskWhen(d):d}`}
function pLate(p){return !!(p.next_due&&p.next_due<pToday())||(p.promises||[]).some(x=>!x.done&&x.due&&x.due<pToday())}
async function peopleLoad(){people=await api('/api/people')}
function personFind(id){return people.items.find(p=>p.id===id)}

// Small helpers ------------------------------------------------------------------------------------------
function ccName(p){return p.name||p.email||'Someone'}
function ccDay(iso){if(!iso)return '';const d=new Date(String(iso).slice(0,10)+'T12:00:00');if(isNaN(d))return String(iso);return d.toLocaleDateString(undefined,{day:'numeric',month:'short',...(d.getFullYear()===new Date().getFullYear()?{}:{year:'numeric'})})}
function ccAddDays(iso,n){const d=new Date(String(iso).slice(0,10)+'T12:00:00');d.setDate(d.getDate()+n);return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`}
function ccSince(iso){if(!iso)return null;return Math.round((new Date(pToday()+'T12:00:00')-new Date(String(iso).slice(0,10)+'T12:00:00'))/864e5)}
function ccFold(s){return String(s||'').normalize('NFD').replace(/[̀-ͯ]/g,'').toLowerCase().trim()}
function ccHit(q,...fields){q=ccFold(q);return !q||fields.some(f=>ccFold(f).includes(q))}
function ccLastIn(p){const h=p.history||[];for(let i=h.length-1;i>=0;i--)if(h[i].direction==='in')return h[i].at;return ''}
function ccLastAt(p){return ccLastIn(p)||(p.history||[]).at(-1)?.at||(p.created_at||'').slice(0,10)}
function ccMoney(n,cur){try{return new Intl.NumberFormat(undefined,{style:'currency',currency:cur||'USD',maximumFractionDigits:0}).format((n||0)/100)}catch{return String(Math.round((n||0)/100))}}
function ccKind(p){return {reply:'lead',lead:'lead',client:'client',buyer:'customer'}[p.pathway]||'person'}
function ccLive(){return people?people.items.filter(p=>!p.archived):[]}
function ccChip(s){return `<span class="cc-chip s-${esc(s.key)}${s.needs?' needs':''}">${esc(s.label)}</span>`}

// Where a tracked person stands, worked out from their history. "needs" means Josh has to do something.
// Everything else is waiting on the other person or being watched.
function ccState(p){
  const h=p.history||[],today=pToday(),lead=p.pathway==='reply'||p.pathway==='lead';
  let li=-1;h.forEach((x,i)=>{if(x.direction==='in')li=i});
  const outs=h.filter((x,i)=>i>li&&x.direction==='out');
  const due=(p.promises||[]).filter(x=>!x.done&&x.due&&x.due<=today);
  if(p.archived)return {key:'closed',label:'Archived',next:''};
  if(lead&&CC_CLOSED.includes(p.stage))return {key:'closed',label:p.stage,next:'No more follow-ups'};
  if((li>=0&&!outs.length)||(lead&&!h.length&&p.stage==='New reply'))
    return {key:'reply',needs:true,label:'Needs your reply',next:p.next_step||(li>=0?`Reply to what they said on ${ccDay(h[li].at)}`:'Reply to their message')};
  if(due.length)return {key:'promise',needs:true,label:'Promise due',next:due[0].text};
  if(lead){
    if(!outs.length)return {key:'watch',label:'Nothing logged yet',next:p.next_step||'Log their message so the rules can work'};
    const checks=outs.length-1,last=outs.at(-1).at;
    if(checks>=CC_RULE.checkins)return {key:'rest',label:'Both check-ins sent',next:'No more check-ins. Anouk waits for them to write back.',checks};
    const from=ccAddDays(last,CC_RULE.gap);
    if(from<=today)return {key:'checkin',needs:true,label:`Check-in ${checks+1} of ${CC_RULE.checkins} due`,next:`Send one if there's something useful to say, by ${ccDay(ccAddDays(last,CC_RULE.late))}`,checks};
    return {key:'waiting',label:'Waiting for them',next:`Check-in ${checks+1} of ${CC_RULE.checkins} from ${ccDay(from)} if they don't write`,checks};
  }
  if(p.next_due&&p.next_due<=today)return {key:'due',needs:true,label:'Check-in due',next:p.next_step||'Check in'};
  if(p.next_due)return {key:'waiting',label:'Waiting',next:`${p.next_step||'Next check-in'} by ${ccDay(p.next_due)}`};
  if(outs.length)return {key:'waiting',label:'Waiting for them',next:p.next_step||`Last message sent ${ccDay(outs.at(-1).at)}`};
  return {key:'watch',label:'Keeping an eye',next:p.next_step||'No next step yet'};
}

// Admin's clients and course buyers. Each part can fail on its own, and the page says which.
async function ccLoadAdmin(force){
  if(ccAdmin&&!force)return ccAdmin;
  if(ccAdminLoading)return ccAdminLoading;
  const one=path=>api(path).then(v=>({v}),e=>({e:e.message||'Request failed'}));
  ccAdminLoading=Promise.all([one('/api/admin/clients'),one('/api/admin/customers')])
    .then(([c,b])=>{
      // Only use a reply that has the shape the page needs. Anything else counts as a failed load.
      const okC=c.v&&Array.isArray(c.v.clients)&&c.v.stats,okB=b.v&&Array.isArray(b.v.customers)&&b.v.stats;
      return ccAdmin={clients:okC?c.v:null,clientsErr:c.e||(okC?'':'Admin sent back something unexpected.'),buyers:okB?b.v:null,buyersErr:b.e||(okB?'':'Admin sent back something unexpected.')};
    })
    .finally(()=>{ccAdminLoading=null});
  return ccAdminLoading;
}
function ccTracked(emails,name){
  const es=(emails||[]).filter(Boolean).map(e=>e.toLowerCase()),n=ccFold(name);
  return ccLive().find(p=>(p.email&&es.includes(p.email))||(n&&p.name&&ccFold(p.name)===n))||null;
}
function ccClientRows(){return ccAdmin?.clients?.clients||[]}
function ccActiveClient(c){return ['active','new'].includes(String(c.status||'').toLowerCase())}
function ccClientState(c){
  const p=ccTracked([c.email,...(c.emails||[])],c.name);
  if(p)return {...ccState(p),person:p};
  if(c.next_call)return {key:'booked',label:'Call booked',next:`Next call ${ccDay(c.next_call.start)}`};
  const n=ccSince(c.last_call);
  if(n!==null&&n>14)return {key:'stale',label:'No recent call',next:`Last call ${ccDay(c.last_call)}, ${n} days ago`};
  if(c.last_call)return {key:'ok',label:'Recent call',next:`Last call ${ccDay(c.last_call)}`};
  return {key:'stale',label:'No calls yet',next:'No call on record'};
}
function ccSynced(part){
  const c=ccAdmin?.clients?.connections?.[part]||ccAdmin?.buyers?.connections?.[part];
  if(!c)return 'not known';
  if(!c.connected)return 'not connected';
  if(c.error)return `last sync failed (${c.error})`;
  return c.synced_at?`last synced ${ccDay(c.synced_at)}`:'not synced yet';
}

function personDetail(p,stages){
  return `<div class="person-detail">
    <div class="pd-grid">
      <section>
        <label>Name<input data-p="name" value="${esc(p.name)}"></label>
        <label>Email<input data-p="email" value="${esc(p.email)}"></label>
        <label>Pathway<select data-p="pathway">${Object.entries(people.pathways).map(([k,v])=>`<option value="${k}" ${k===p.pathway?'selected':''}>${esc(v.label)}</option>`).join('')}</select></label>
        <label>Stage<select data-p="stage">${stages.map(s=>`<option ${s===p.stage?'selected':''}>${esc(s)}</option>`).join('')}</select></label>
        <label>Where they came from<input data-p="source" value="${esc(p.source)}"></label>
        <label>Who they are and what they need<textarea rows="4" data-p="summary">${esc(p.summary)}</textarea></label>
      </section>
      <section>
        <label>Next step<input data-p="next_step" value="${esc(p.next_step)}" placeholder="e.g. Check in on how the first week went"></label>
        <label>By<input type="date" data-p="next_due" value="${esc(p.next_due)}"></label>
        <h4>What Josh promised</h4>
        <ul class="pd-promises">${(p.promises||[]).map((x,i)=>`<li class="${x.done?'done':''} ${!x.done&&x.due&&x.due<pToday()?'late':''}"><label class="check"><input type="checkbox" data-promise="${i}" ${x.done?'checked':''}>${esc(x.text)}</label>${x.due?`<small>${esc(x.due)}</small>`:''}</li>`).join('')||'<li class="hint">Nothing promised yet.</li>'}</ul>
        <div class="pd-add"><input id="promise-text" placeholder="Add a promise, e.g. Send the template"><input type="date" id="promise-due"><button class="small" data-promise-add>Add</button></div>
      </section>
    </div>
    <h4>History</h4>
    <div class="pd-add"><input id="note-text" placeholder="Log a touchpoint or note"><select id="note-dir"><option value="note">Note</option><option value="in">They wrote</option><option value="out">Josh sent</option></select><button class="small" data-note-add>Log</button></div>
    <ol class="pd-history">${(p.history||[]).slice().reverse().map(h=>`<li class="d-${esc(h.direction)}"><small>${esc(h.at)} · ${esc({in:'They wrote',out:'Josh sent',draft:'Draft saved',note:'Note'}[h.direction]||h.direction)} · ${esc(h.channel)}</small><p>${esc(h.summary)}</p>${h.link?`<a href="${esc(h.link)}" target="_blank" rel="noopener">Open</a>`:''}</li>`).join('')||'<li class="hint">No touchpoints yet.</li>'}</ol>
    <footer><span class="save-note" id="person-save">Saved</span><button class="subtle small danger" data-person-archive>Archive</button></footer>
  </div>`;
}

async function ccOpenClient(id){
  ccClient=id;ccClientData=null;ccDoc=null;if(view==='success'&&!current){render();scrollTo(0,0)}
  try{ccClientData=await api('/api/admin/client?id='+encodeURIComponent(id))}catch(e){ccClientData={error:e.message}}
  if(ccClient===id&&view==='success'&&!current)render();
}
function ccDocView(){
  if(!ccDoc)return '';
  return `<section class="cc-doc" id="cc-doc" aria-label="${esc(ccDoc.name||'File')}"><header><b>${esc(ccDoc.name||'File')}</b><span>${esc(ccDoc.call||'')}</span><button class="subtle small" data-cc-doc-close>Close</button></header>
    ${ccDoc.loading?'<p class="hint">Opening…</p>':ccDoc.error?`<p class="cc-error">${esc(ccDoc.error)}</p>`:`<pre>${esc(ccDoc.text)}</pre>`}</section>`;
}
function ccClientView(){
  const back=`<button class="subtle small cc-back" data-cc-back>‹ All clients</button>`;
  if(!ccClientData)return back+'<p class="hint">Opening the client…</p>';
  if(ccClientData.error)return back+`<div class="cc-error"><p>This client couldn't open. ${esc(ccClientData.error)}</p></div>`;
  const c=ccClientData,p=ccTracked(c.emails,c.name),s=p?ccState(p):null,PART={transcript:'Transcript',summary:'Summary',notes:'Notes'};
  const calls=c.calls||[],upcoming=(c.upcoming||[]).slice().sort((a,b)=>a.start.localeCompare(b.start));
  return `${back}
    <header class="cc-client-head"><div><h2>${esc(c.name)}</h2><p>${esc([c.programme||'Programme not recorded',c.status,c.joined?`client since ${ccDay(c.joined)}`:'',c.company].filter(Boolean).join(' · '))}</p>
      ${c.emails?.length?`<p class="cc-emails">${c.emails.map(esc).join(', ')}</p>`:''}</div>${s?ccChip(s):''}</header>
    ${c.about?`<p class="cc-about">${esc(c.about)}</p>`:''}
    <div class="cc-detail">
      <div class="cc-detail-main">
        ${ccDocView()}
        <section class="cc-panel"><header><h3>Calls and transcripts</h3><span class="hub-meta">${calls.length}</span></header>
          ${calls.length?`<ol class="cc-calls">${calls.map(k=>`<li><div class="cc-call-head"><b>${esc(k.title)}</b><small>${esc(ccDay(k.date))} · ${esc(k.section)}${k.source==='calendly'?' · booked in Calendly, no folder notes':''}</small></div>
              <div class="cc-call-acts">${Object.entries(k.parts||{}).map(([key,file])=>`<button class="small" data-cc-doc="${esc(k.id+'/'+file)}" data-cc-doc-call="${esc(k.title+', '+ccDay(k.date))}">${esc(PART[key]||key)}</button>`).join('')}
                ${(k.links||[]).map(l=>`<a class="cc-link" href="${esc(l.url)}" target="_blank" rel="noopener">${esc(l.label)} ↗</a>`).join('')}
                ${c.folder&&k.source!=='calendly'?`<button class="subtle small" data-cc-open="${esc(k.id)}">Open folder</button>`:''}</div></li>`).join('')}</ol>`
            :`<p class="hint">${c.folder?'No calls in the folder yet.':'No calls on record.'}</p>`}
          ${c.folder?'':'<p class="cc-note">This client has no folder yet, so there are no transcripts or notes to show.</p>'}
        </section>
      </div>
      <aside class="cc-detail-side">
        <section class="cc-panel ${s?.needs?'needs':''}"><header><h3>Anouk's notes</h3></header>
          ${p?`<p class="cc-next">${esc(s.next)}</p>
            ${(p.promises||[]).filter(x=>!x.done).map(x=>`<p class="cc-promise">Promised ${esc(x.text)}${x.due?` by ${esc(ccDay(x.due))}`:''}</p>`).join('')}
            <ol class="pd-history">${(p.history||[]).slice(-3).reverse().map(h=>`<li class="d-${esc(h.direction)}"><small>${esc(ccDay(h.at))} · ${esc({in:'They wrote',out:'Josh sent',draft:'Draft saved',note:'Note'}[h.direction]||h.direction)}</small><p>${esc(h.summary)}</p></li>`).join('')}</ol>
            <button class="small" data-cc-person="${esc(p.id)}">Open the full record</button>`
            :`<p class="hint">Anouk isn't tracking ${esc(c.name)}. Add them when they need watching.</p><button class="small" data-cc-track>Add to Anouk's tracker</button>`}
        </section>
        <section class="cc-panel"><header><h3>Scheduling</h3></header>
          ${c.next_call?`<p class="cc-next">Next call ${esc(ccDay(c.next_call.start))}${c.next_call.event?`, ${esc(c.next_call.event)}`:''}</p>`:'<p class="hint">No call booked.</p>'}
          ${upcoming.length>1?`<ul class="cc-upcoming">${upcoming.slice(1).map(e=>`<li>${esc(ccDay(e.start))} · ${esc(e.event||'Call')}</li>`).join('')}</ul>`:''}
          <p class="cc-note">Bookings come from Calendly, ${esc(ccSynced('calendly'))}. Booking from CareerOS isn't connected, so book calls in Calendly as usual.</p>
          <button class="small" data-cc-ask="${esc(`Can you suggest a check-in message for ${c.name}? Don't send anything.`)}">Ask Anouk to suggest a check-in</button>
        </section>
        <section class="cc-panel"><header><h3>Folder and payments</h3></header>
          ${c.folder?`<p class="cc-path" title="${esc(c.folder_path)}">${esc(c.folder_path)}</p><button class="small" data-cc-open="">Open the client folder</button>`:'<p class="hint">No client folder yet.</p>'}
          <p class="cc-note">${c.payments?.length?`${c.payments.length} payment${c.payments.length===1?'':'s'}, ${esc(ccMoney(c.ltv,c.currency))} in total, from Stripe (${esc(ccSynced('stripe'))}).`:'No payments found in Stripe.'}</p>
        </section>
      </aside>
    </div>`;
}

function ccLeadsAll(){return ccLive().filter(p=>p.pathway==='reply'||p.pathway==='lead')}
// Tiles ------------------------------------------------------------------------------------------------------
// Every tab is the same thing, a column of long rounded tiles with a little space between them. A tile shows who it
// is and the one fact that matters, and opens in place for a bit of context and the next action. Gold marks only
// what needs Josh (docs/design/mood.md), always with a word beside the dot (critique, semantic use).
let ccEdit=null,ccShowDone=false,ccAdding=false,ccLookupOpen=false;
function ccAgo(iso){
  if(!iso)return '';const n=ccSince(iso);
  if(n===null||isNaN(n))return ccDay(iso);
  if(n<=0)return 'today';if(n===1)return 'yesterday';if(n<7)return `${n} days ago`;return ccDay(iso);
}
const CC_DIR={in:'Wrote',out:'You replied',note:'Note',draft:'Draft saved'};
function ccUpdate(p){
  const h=(p.history||[]).at(-1);
  return h?`${CC_DIR[h.direction]||'Updated'} ${ccAgo(h.at)}`:`Added ${ccAgo((p.created_at||'').slice(0,10))}`;
}
function ccTile({key,needs,name,sub,meta,metaSub,attrs,open,body}){
  return `<li class="cct${needs?' needs':''}${open?' open':''}" id="${esc(key)}">
    <button class="cct-head" ${attrs} aria-expanded="${!!open}">
      <span class="cct-dot" aria-hidden="true"></span>
      <span class="cct-who"><b>${esc(name)}</b>${sub?`<small>${esc(sub)}</small>`:''}</span>
      <span class="cct-meta">${meta}${metaSub?`<small>${metaSub}</small>`:''}</span>
      <i class="cct-chev" aria-hidden="true">›</i>
    </button>
    ${open&&body?`<div class="cct-body">${body}</div>`:''}
  </li>`;
}
function ccList(items,empty){return items.length?`<ul class="cct-list" id="cc-list">${items.join('')}</ul>`:`<ul class="cct-list" id="cc-list"><li class="cct-empty">${esc(empty)}</li></ul>`}

// A tracked person, used on Overview and Leads.
function ccPersonTile(p){
  const s=ccState(p),open=personOpen===p.id;
  return ccTile({key:'person-'+p.id,needs:s.needs,name:ccName(p),sub:p.name?p.email:'',attrs:`data-person-open="${esc(p.id)}"`,open,
    meta:s.needs?`<span class="cct-flag">${esc(s.label)}</span>`:esc(ccUpdate(p)),metaSub:s.needs?esc(ccUpdate(p)):'',
    body:open?(ccEdit===p.id?personDetail(p,people.pathways[p.pathway].stages):ccPersonBody(p,s)):''});
}
function ccPersonBody(p,s){
  const h=p.history||[],said=[...h].reverse().find(x=>x.direction==='in'),lead=p.pathway==='reply'||p.pathway==='lead';
  const rest=h.slice(-4).reverse().filter(x=>x!==said).slice(0,3);
  return `${said?`<p class="cct-quote">${esc(said.summary||'They wrote.')} <small>${esc(ccDay(said.at))}</small></p>`:''}
    ${p.summary||p.source?`<p class="cct-line">${esc(p.summary||`Came from ${p.source}.`)}</p>`:''}
    <p class="cct-next">${esc(s.next)}</p>
    ${rest.length?`<ol class="cct-hist">${rest.map(x=>`<li><time>${esc(ccDay(x.at))}</time>${esc(CC_DIR[x.direction]||'Note')}${x.summary?`, ${esc(x.summary)}`:''}</li>`).join('')}</ol>`:''}
    ${(p.promises||[]).filter(x=>!x.done).map(x=>`<p class="cct-line">You promised ${esc(x.text)}${x.due?` by ${esc(ccDay(x.due))}`:''}.</p>`).join('')}
    <div class="cct-acts">
      ${s.key==='reply'||s.key==='checkin'?`<button class="small" data-cc-replied>I've replied</button>`:''}
      ${p.pathway==='client'?`<button class="small" data-cc-person="${esc(p.id)}">Open client page</button>`:''}
      ${lead&&!['closed'].includes(s.key)?`<button class="subtle small" data-cc-close>Not interested</button>`:''}
      <button class="subtle small" data-cc-edit>Edit details</button>
    </div>`;
}

// Overview: Anouk's one-line briefing, then who needs you, then who you're waiting on.
function ccOverview(){
  const rows=ccLive().map(p=>({p,s:ccState(p)})),needs=rows.filter(r=>r.s.needs);
  const waiting=rows.filter(r=>!r.s.needs&&!['closed','rest'].includes(r.s.key)).sort((a,b)=>ccLastAt(b.p).localeCompare(ccLastAt(a.p)));
  const a=typeof teamAgent==='function'&&team?teamAgent('anouk'):null,b=anoukBrief();
  const shown=ccShowDone?waiting:waiting.slice(0,6);
  return `<div class="cc-intro">${a&&typeof teamAvatar==='function'?teamAvatar(a):''}<p>${esc(b.say)}</p></div>
    <h2 class="cc-h">Needs you</h2>
    ${ccList(needs.map(r=>ccPersonTile(r.p)),'Nothing needs you right now.')}
    <h2 class="cc-h">Waiting on them</h2>
    ${ccList(shown.map(r=>ccPersonTile(r.p)),'Nobody is waiting on a reply or a check-in.')}
    ${waiting.length>6&&!ccShowDone?`<button class="cc-textbtn" data-cc-more>Show all ${waiting.length}</button>`:''}
    <p class="cc-foot">${esc(ccSourcesLine())}</p>`;
}
function ccSourcesLine(){
  return `Gmail updates when Anouk checks it. Stripe ${ccSynced('stripe').replace('last ','')}, Calendly ${ccSynced('calendly').replace('last ','')}.`;
}

// Leads: name, email and last update, newest first. Finished leads are tucked away.
function ccLeadList(){
  const q=ccQ.leads,rows=ccLeadsAll().map(p=>({p,s:ccState(p)})).filter(({p,s})=>ccHit(q,p.name,p.email,p.source,p.summary)&&(ccShowDone||!['closed','rest'].includes(s.key)));
  rows.sort((a,b)=>ccLastAt(b.p).localeCompare(ccLastAt(a.p)));
  return ccList(rows.map(r=>ccPersonTile(r.p)),ccLeadsAll().length?'No leads match.':'No leads yet. Anouk adds people here when she reads Gmail.');
}
function ccLeads(){
  const done=ccLeadsAll().filter(p=>['closed','rest'].includes(ccState(p).key)).length;
  return `<div class="cc-bar"><input type="search" class="cc-search" data-cc-q="leads" value="${esc(ccQ.leads)}" placeholder="Search leads" aria-label="Search leads"><button class="cc-textbtn" data-cc-add>Add a lead</button></div>
    ${ccAdding?`<form class="cct-add" id="person-new"><label>Name<input name="name" autocomplete="off"></label><label>Email<input name="email" type="email" autocomplete="off"></label><input type="hidden" name="pathway" value="reply"><button class="small">Add</button><button type="button" class="subtle small" data-cc-add>Cancel</button></form>`:''}
    ${ccLeadList()}
    ${done?`<button class="cc-textbtn" data-cc-more>${ccShowDone?'Hide':'Show'} ${done} finished</button>`:''}`;
}

// Clients: the same tiles. Opening one goes to the client page, which is unchanged.
function ccClientList(){
  const rows=ccClientRows().map(c=>({c,s:ccClientState(c)})).filter(({c})=>ccHit(ccQ.clients,c.name,c.email,c.programme,c.status));
  const rank=r=>r.s.needs?0:r.c.next_call?1:2;
  rows.sort((a,b)=>rank(a)-rank(b)||(a.c.next_call?.start||'').localeCompare(b.c.next_call?.start||'')||(b.c.last_call||'').localeCompare(a.c.last_call||''));
  return ccList(rows.map(({c,s})=>ccTile({key:'client-'+c.id,needs:s.needs,name:c.name,sub:c.programme||'Programme not recorded',attrs:`data-cc-client="${esc(c.id)}"`,
    meta:s.needs?`<span class="cct-flag">${esc(s.label)}</span>`:esc(c.next_call?`Call ${ccDay(c.next_call.start)}`:c.last_call?`Last call ${ccAgo(c.last_call)}`:'No calls yet'),
    metaSub:s.needs?esc(c.last_call?`Last call ${ccAgo(c.last_call)}`:''):c.folder?'':'No folder'})),
    ccClientRows().length?'No clients match.':'No clients found in the client folders or coaching payments.');
}
function ccClients(){
  if(!ccAdmin)return '<p class="hint">Getting the client list…</p>';
  if(!ccAdmin.clients)return `<div class="cc-error"><p>The client list couldn't load. ${esc(ccAdmin.clientsErr)}</p><button class="small" data-cc-retry>Try again</button></div>`;
  return `<div class="cc-bar"><input type="search" class="cc-search" data-cc-q="clients" value="${esc(ccQ.clients)}" placeholder="Search clients" aria-label="Search clients"></div>
    ${ccClientList()}
    <p class="cc-foot">Calls come from the client folders and Calendly, ${esc(ccSynced('calendly'))}.</p>`;
}

// Learners: Stripe buyers, plus anyone looked up by email. Progress is the share of the lessons they can open
// today that they've finished, worked out by CareerOS from CareerOS's counts (studio/learners.py).
let ccProg=null,ccProgErr='',ccProgTimer=null,ccLearnerOpen=null;
const CC_EVENT={signed_in:'Signed in',lesson_completed:'Finished a lesson',lesson_uncompleted:'Unmarked a lesson',access_granted:'Got access',
  access_revoked:'Lost access',access_expiry_changed:'Access dates changed',file_requested:'Downloaded a file',question_posted:'Asked a question',
  reply_posted:'Got a reply',reply_notification_queued:'Reply notice queued',review_submitted:'Left a review',review_status_changed:'Review status changed',
  email_handed_to_provider:'Email handed to the mail service'};
async function ccProgLoad(){
  try{ccProg=await api('/api/learners');ccProgErr=''}
  catch(e){ccProg=null;ccProgErr=/not found|404/i.test(e.message)?'old':e.message}
  clearTimeout(ccProgTimer);ccProgTimer=null;
  if(ccProg&&Object.keys(ccProg.running||{}).length)ccProgTimer=setTimeout(ccProgTick,3000);
}
async function ccProgTick(){
  ccProgTimer=null;
  if(view!=='success'||current||ccTab!=='learners')return;
  await ccProgLoad();
  if(view==='success'&&!current&&ccTab==='learners'){const box=$('#cc-list');if(box)box.outerHTML=ccLearnerList()}
}
function ccProgFor(email){
  email=(email||'').toLowerCase();const p=ccProg||{};
  return {item:(p.items||{})[email],running:(p.running||{})[email],failed:(p.failed||{})[email]};
}
function ccWhen(iso){if(!iso)return '';const d=new Date(iso);if(isNaN(d))return iso;return d.toLocaleString(undefined,{day:'numeric',month:'short',hour:'numeric',minute:'2-digit'})}
function ccLearnerRows(){
  const buyers=ccAdmin?.buyers?.customers||[],seen=new Set(buyers.map(b=>b.email));
  const extra=ccLive().filter(p=>p.pathway==='buyer'&&p.email&&!seen.has(p.email)).map(p=>({email:p.email,name:p.name,items:[],first:'',trackerOnly:true}));
  extra.forEach(x=>seen.add(x.email));
  const looked=[...Object.keys(ccProg?.items||{}),...Object.keys(ccProg?.running||{}),...Object.keys(ccProg?.failed||{})]
    .filter((e,i,all)=>!seen.has(e)&&all.indexOf(e)===i).map(e=>({email:e,name:ccProg.items?.[e]?.learner?.name||'',items:[],first:'',lookupOnly:true}));
  return [...buyers,...extra,...looked].map(b=>({b,p:ccTracked([b.email],'')}));
}
function ccProgMeta(email){
  if(ccProgErr)return ['Progress not available',''];
  if(!ccProg)return ['…',''];
  const {item,running,failed}=ccProgFor(email);
  if(running)return ['Checking CareerOS…',''];
  if(failed&&!item)return ['Couldn\'t check',''];
  if(!item)return ['Not checked',''];
  if(item.status==='not_found')return ['No CareerOS account',`Checked ${esc(ccAgo(item.fetched_at))}`];
  if(item.status==='ambiguous')return ['More than one match',''];
  const m=item.main;
  if(!m)return ['Progress unavailable',`Checked ${esc(ccAgo(item.fetched_at))}`];
  return [`<span class="cct-prog"><span class="cct-bar" aria-hidden="true"><i style="width:${m.percent}%"></i></span><b>${m.percent}%</b></span>`,esc(m.course)];
}
function ccLearnerBody(b,p){
  const {item,running,failed}=ccProgFor(b.email),can=ccProg&&!ccProgErr,products=[...new Set((b.items||[]).map(i=>i.product))];
  const stripe=b.lookupOnly?'Not a Stripe buyer. Looked up by email.':b.first?`Bought ${products.join(', ')||'a course'} on ${ccDay(b.first)}${b.refunded?', refunded':''}.`:'Not found in Stripe.';
  const s=p?ccState(p):null;
  const tracker=p?`<p class="cct-line">Anouk is tracking them. ${esc(s.next)} <button class="cc-textbtn" data-cc-person="${esc(p.id)}">Open record</button></p>`:'';
  if(!can)return `<p class="cct-line">${esc(stripe)}</p>${tracker}
    <p class="cct-line">${ccProgErr==='old'?'Progress checks start working once the app restarts. Anouk can read it from CareerOS in her chat until then.':`Progress checks couldn't load. ${esc(ccProgErr)}`}</p>
    <div class="cct-acts"><button class="small" data-cc-ask="${esc(`Can you look up ${b.name||b.email} (${b.email}) in CareerOS and tell me how their course progress looks? Read only.`)}">Ask Anouk</button></div>`;
  if(running)return `<p class="cct-line">${esc(stripe)}</p>${tracker}<p class="cct-line">Reading CareerOS. This takes about half a minute.</p>`;
  if(!item)return `<p class="cct-line">${esc(stripe)}</p>${tracker}${failed?`<p class="cct-line bad">The last check failed. ${esc(failed.error)}</p>`:''}
    <div class="cct-acts"><button class="small" data-cc-prog="${esc(b.email)}">${failed?'Try again':'Check progress'}</button></div>`;
  const read=`<p class="cc-foot">Read from CareerOS ${esc(ccWhen(item.fetched_at))}. <button class="cc-textbtn" data-cc-prog="${esc(b.email)}" data-force="1">Check again</button></p>`;
  if(item.status!=='found')return `<p class="cct-line">${esc(stripe)}</p>${tracker}<p class="cct-line">${item.status==='not_found'?'No CareerOS account uses this email. They may have signed up with another one.':'More than one CareerOS account could match, so nothing is shown.'}</p>${read}`;
  const L=item.learner||{},since=(item.tracking||[]).map(t=>t.live_from).concat(L.sign_in_tracking_began||[]).filter(Boolean).sort()[0];
  const ev=item.events||[];
  return `<div class="cct-cols">
      <section><h4>Progress</h4><ul>${item.courses.length?item.courses.map(c=>`<li>${esc(c.course)}<span>${c.percent!=null?`${c.completed} of ${c.available} lessons, ${c.percent}%`:'Not enough data for a percentage'}</span></li>`).join(''):'<li>No courses returned.</li>'}</ul>
        <p class="cct-small">Out of the published lessons they can open today.</p></section>
      <section><h4>About them</h4><ul>
        ${L.signed_up_at?`<li>Signed up<span>${esc(ccDay(L.signed_up_at))}</span></li>`:''}
        <li>Last sign-in<span>${esc(L.last_sign_in_at?ccAgo(L.last_sign_in_at):'Not recorded')}</span></li>
        ${L.recorded_sign_ins!=null?`<li>Sign-ins recorded<span>${L.recorded_sign_ins}</span></li>`:''}
        ${(item.access||[]).map(a=>`<li>${esc(a.item)}<span>${esc([a.status,a.reason].filter(Boolean).join(', '))}</span></li>`).join('')}
        <li>Stripe<span>${esc(stripe)}</span></li></ul></section>
      <section><h4>Sign-ins and activity</h4>
        ${ev.length?`<ol class="cct-hist">${ev.slice(0,10).map(e=>`<li><time>${esc(ccDay(e.at))}</time>${esc(CC_EVENT[e.kind]||e.kind)}</li>`).join('')}</ol>`:'<p class="cct-line">Nothing recorded yet.</p>'}
        <p class="cct-small">${since?`CareerOS has tracked this since ${esc(ccDay(since))}, so anything earlier is missing.`:'CareerOS didn\'t say when tracking started, so earlier activity may be missing.'}</p></section>
    </div>${tracker}${read}`;
}
function ccLearnerList(){
  const rows=ccLearnerRows().filter(({b})=>ccHit(ccQ.learners,b.name,b.email,b.country,...(b.items||[]).map(i=>i.product)));
  return ccList(rows.map(({b,p})=>{const s=p?ccState(p):null,[meta,metaSub]=ccProgMeta(b.email);
    return ccTile({key:'learner-'+b.email,needs:!!s?.needs,name:b.name||b.email,sub:b.name?b.email:'',attrs:`data-cc-prog-open="${esc(b.email)}"`,
      open:ccLearnerOpen===b.email,meta,metaSub,body:ccLearnerOpen===b.email?ccLearnerBody(b,p):''})}),
    ccLearnerRows().length?'No learners match.':'No course buyers found in Stripe.');
}
function ccLearners(){
  if(!ccAdmin)return '<p class="hint">Getting the course buyers…</p>';
  if(!ccAdmin.buyers)return `<div class="cc-error"><p>The course buyers couldn't load. ${esc(ccAdmin.buyersErr)}</p><button class="small" data-cc-retry>Try again</button></div>`;
  if(!ccProg&&!ccProgErr)ccProgLoad().then(()=>{if(view==='success'&&!current&&ccTab==='learners')render()});
  const can=ccProg&&!ccProgErr;
  return `<div class="cc-bar"><input type="search" class="cc-search" data-cc-q="learners" value="${esc(ccQ.learners)}" placeholder="Search learners" aria-label="Search learners">${can?`<button class="cc-textbtn" data-cc-lookup>Look up an email</button>`:''}</div>
    ${can&&ccLookupOpen?`<form class="cct-add" id="cc-lookup"><label>Email to look up in CareerOS<input type="email" name="email" required autocomplete="off"></label><button class="small">Check progress</button><button type="button" class="subtle small" data-cc-lookup>Cancel</button></form>`:''}
    ${ccLearnerList()}
    <p class="cc-foot">${ccProgErr==='old'?'Progress checks start working once the app restarts.':'Open someone to check their progress in CareerOS. Each check reads one person and is kept for 12 hours.'} Buyers come from Stripe, ${esc(ccSynced('stripe'))}.</p>`;
}
async function ccProgStart(email,force){
  try{await api('/api/learners/lookup',{email,force:!!force});await ccProgLoad()}
  catch(e){toast(e.message,true)}
  if(view==='success'&&!current&&ccTab==='learners')render();
}

// The page ---------------------------------------------------------------------------------------------------
function ccCount(k){
  if(k==='replies'&&rq)return rq.waiting.length||'';
  if(k==='replies')return ccReplies?ccReplies.items.filter(r=>r.state==='proposed'||r.state==='drafted').length||'':'';
  if(k==='leads')return ccLeadsAll().filter(p=>!['closed','rest'].includes(ccState(p).key)).length;
  if(k==='clients')return ccAdmin?.clients?ccAdmin.clients.clients.length:'';
  if(k==='learners')return ccAdmin?.buyers?ccLearnerRows().length:'';
  const n=ccLive().filter(p=>ccState(p).needs).length;return n||'';
}
function ccListHtml(){return {leads:ccLeadList,clients:ccClientList,learners:ccLearnerList}[ccTab]?.()||''}
async function renderSuccess(){
  page('Customer center','');
  if(!team&&typeof deskData==='function')await deskData();
  if(!people){$('#workspace').innerHTML='<p class="hint">Getting the tracker…</p>';
    try{await peopleLoad()}catch(e){$('#workspace').innerHTML=`<div class="cc-error"><p>The tracker couldn't load. ${esc(e.message)}</p><button class="small" data-cc-reload>Try again</button></div>`;return}
    if(view!=='success'||current)return}
  if(!ccAdmin&&!ccAdminLoading)ccLoadAdmin().then(()=>{if(view==='success'&&!current)render()});
  const body=ccTab==='clients'&&ccClient?ccClientView():({overview:ccOverview,leads:ccLeads,clients:ccClients,learners:ccLearners,replies:ccRepliesView}[ccTab]||ccOverview)();
  $('#workspace').innerHTML=`<div class="success cc">
    <div class="success-tabs cc-tabs" role="tablist" aria-label="Customer center">${CC_TABS.map(([k,l])=>{const n=ccCount(k);return `<button role="tab" aria-selected="${ccTab===k}" class="${ccTab===k?'on':''}" data-cc-tab="${k}">${esc(l)}${n!==''?` <span class="count">${n}</span>`:''}</button>`}).join('')}</div>
    <div class="cc-body" role="tabpanel">${body}</div>
  </div>`;
  if(personOpen&&ccScrollTo===personOpen){const r=document.getElementById('person-'+personOpen);if(r){r.scrollIntoView({block:'center'});ccScrollTo=null}}
}
document.addEventListener('click',e=>{if(view!=='success'||current)return;
  const b=e.target.closest('[data-cc-prog]');if(b){ccProgStart(b.dataset.ccProg,b.dataset.force==='1');return}
  const o=e.target.closest('[data-cc-prog-open]');if(o){ccLearnerOpen=ccLearnerOpen===o.dataset.ccProgOpen?null:o.dataset.ccProgOpen;const box=$('#cc-list');if(box)box.outerHTML=ccLearnerList();return}
  if(e.target.closest('[data-cc-lookup]')){ccLookupOpen=!ccLookupOpen;render();if(ccLookupOpen)$('#cc-lookup input')?.focus();return}
  if(e.target.closest('[data-cc-add]')){ccAdding=!ccAdding;render();if(ccAdding)$('#person-new input')?.focus();return}
  if(e.target.closest('[data-cc-more]')){ccShowDone=!ccShowDone;render();return}
  if(e.target.closest('[data-cc-edit]')){ccEdit=personOpen;render();return}
  const p=personOpen&&personFind(personOpen);
  if(p&&e.target.closest('[data-cc-replied]')){const h={at:pToday(),direction:'out',channel:'email',summary:'You replied. Logged in the Customer center.'};p.history=[...(p.history||[]),h];personQueue({},[h]);render();return}
  if(p&&e.target.closest('[data-cc-close]')){personQueue({stage:p.pathway==='lead'?'Said no':'Not now'});render();return}
});
document.addEventListener('submit',e=>{if(e.target.id!=='cc-lookup')return;e.preventDefault();const v=String(new FormData(e.target).get('email')||'').trim().toLowerCase();if(v){ccLearnerOpen=v;ccLookupOpen=false;ccProgStart(v,false)}});

// Opening a person or a client from anywhere, such as Anouk's desk.
async function ccOpenPerson(id){
  const p=people&&personFind(id);if(!p)return ccGo('overview');
  if(p.pathway==='client'){await ccLoadAdmin();const c=ccClientRows().find(c=>ccTracked([c.email],c.name)?.id===id);if(c)return ccGo('clients',c.id)}
  const tab=(p.pathway==='reply'||p.pathway==='lead')?'leads':'overview';
  if(tab==='leads'){ccQ.leads='';if(['closed','rest'].includes(ccState(p).key))ccShowDone=true}
  await ccGo(tab);personOpen=id;ccScrollTo=id;render();
}
async function ccGo(tab,clientId){
  ccTab=tab;ccClient=null;ccClientData=null;ccDoc=null;personOpen=null;
  if(view!=='success'||current)await ccBaseDeskAct('view:success');else render();
  if(clientId)await ccOpenClient(clientId);
}
async function ccAskAnouk(text){
  if(typeof teamOpen==='undefined')return;
  teamOpen='anouk';await ccBaseDeskAct('view:team');
  const put=()=>{const i=$('#room-input');if(!i)return false;i.value=text;if(typeof roomGrow==='function')roomGrow(i);i.focus();return true};
  if(!put())requestAnimationFrame(put);
  toast('Your question is in Anouk\'s chat. Nothing is sent until you press send.');
}
const ccBaseDeskAct=deskAct;
deskAct=async function(act){
  const [kind,...rest]=act.split(':'),arg=rest.join(':');
  if(kind==='cc')return ccGo(arg||'overview');
  if(kind==='cc-person')return ccOpenPerson(arg);
  if(kind==='cc-client'){await ccLoadAdmin();return ccGo('clients',arg)}
  return ccBaseDeskAct(act);
};

function personNote(t){const n=$('#person-save');if(n)n.textContent=t}
function personQueue(changes,history){
  const p=personFind(personOpen);if(!p)return;Object.assign(p,changes);p._draft=Object.assign(p._draft||{},changes);if(history)(p._hist=p._hist||[]).push(...history);
  personNote('Saving…');clearTimeout(personTimer);personTimer=setTimeout(()=>personSave(p.id),600);
}
async function personSave(id){
  if(!id||!people)return;
  const p=personFind(id);if(!p||(!p._draft&&!p._hist))return;if(personBusy){await personBusy;return personSave(id)}
  const changes=p._draft||{},history=p._hist||[];delete p._draft;delete p._hist;
  personBusy=(async()=>{try{const saved=await api('/api/people/'+id,{revision:p.revision,changes,add_history:history},'PUT');Object.assign(p,saved);personNote('Saved')}
    catch(e){toast(e.message,true);await peopleLoad();render()}})();await personBusy;personBusy=null;
}

// These work from any page, since Anouk's desk links into the Customer center too.
document.addEventListener('click',async e=>{
  const pr=e.target.closest('[data-cc-person]');if(pr){e.preventDefault();await personSave(personOpen);return ccOpenPerson(pr.dataset.ccPerson)}
  const ask=e.target.closest('[data-cc-ask]');if(ask){e.preventDefault();return ccAskAnouk(ask.dataset.ccAsk)}
});
document.addEventListener('click',async e=>{if(view!=='success'||current)return;
  const tb=e.target.closest('[data-cc-tab]');if(tb){await personSave(personOpen);ccTab=tb.dataset.ccTab;ccClient=null;ccDoc=null;personOpen=null;ccEdit=null;ccAdding=false;ccLookupOpen=false;if(ccTab==='learners'){ccProg=null;ccProgErr=''}if(ccTab==='replies')ccReplies=null;render();return}
  const cl=e.target.closest('[data-cc-client]');if(cl){ccOpenClient(cl.dataset.ccClient);return}
  if(e.target.closest('[data-cc-back]')){ccClient=null;ccClientData=null;ccDoc=null;render();return}
  if(e.target.closest('[data-cc-retry]')){ccAdmin=null;render();return}
  if(e.target.closest('[data-cc-reload]')){people=null;render();return}
  const doc=e.target.closest('[data-cc-doc]');if(doc&&ccClientData?.folder){
    const rel=doc.dataset.ccDoc,name=rel.split('/').pop();ccDoc={rel,name,call:doc.dataset.ccDocCall,loading:true};render();
    try{const r=await api(`/api/admin/text?id=${encodeURIComponent(ccClientData.folder)}&path=${encodeURIComponent(rel)}`);if(ccDoc?.rel===rel)ccDoc={...ccDoc,loading:false,text:r.text,name:r.name||name}}
    catch(err){if(ccDoc?.rel===rel)ccDoc={...ccDoc,loading:false,error:err.message}}
    render();$('#cc-doc')?.scrollIntoView({block:'start'});return}
  if(e.target.closest('[data-cc-doc-close]')){ccDoc=null;render();return}
  const op=e.target.closest('[data-cc-open]');if(op&&ccClientData?.folder){
    try{await api('/api/admin/open',{id:ccClientData.folder,path:op.dataset.ccOpen});toast('Opened on this PC')}catch(err){toast(err.message,true)}return}
  if(e.target.closest('[data-cc-track]')&&ccClientData){const c=ccClientData;
    try{const p=await api('/api/people',{id:crypto.randomUUID(),name:c.name,email:(c.emails||[])[0]||'',pathway:'client',stage:'Active',source:'Client in Admin'});people.items.unshift(p);toast(`Anouk is now tracking ${c.name}`);render()}catch(err){toast(err.message,true)}return}
  const o=e.target.closest('[data-person-open]');if(o){await personSave(personOpen);personOpen=personOpen===o.dataset.personOpen?null:o.dataset.personOpen;ccEdit=null;render();return}
  if(e.target.closest('[data-promise-add]')){const p=personFind(personOpen),text=$('#promise-text').value.trim();if(!text)return;
    personQueue({promises:[...(p.promises||[]),{text,due:$('#promise-due').value,done:false}]});render();return}
  if(e.target.closest('[data-note-add]')){const text=$('#note-text').value.trim();if(!text)return;const p=personFind(personOpen);
    const h={at:pToday(),direction:$('#note-dir').value,channel:'note',summary:text};p.history=[...(p.history||[]),h];personQueue({},[h]);render();return}
  if(e.target.closest('[data-person-archive]')&&confirm('Archive this person? They stay in the database but leave the list.')){personQueue({archived:true});await personSave(personOpen);personOpen=null;render();return}
});
document.addEventListener('input',e=>{
  if(view!=='success')return;const el=e.target;
  if(el.dataset?.ccQ!==undefined){ccQ[el.dataset.ccQ]=el.value;const box=$('#cc-list');if(box)box.innerHTML=ccListHtml();return}
  if(personOpen&&el.dataset.p&&el.tagName!=='SELECT'&&el.type!=='date')personQueue({[el.dataset.p]:el.value});
});
document.addEventListener('change',e=>{if(view!=='success'||!personOpen)return;const el=e.target;
  if(el.dataset.p){const ch={[el.dataset.p]:el.value};if(el.dataset.p==='pathway')ch.stage=people.pathways[el.value].stages[0];personQueue(ch);if(['pathway','stage','next_due'].includes(el.dataset.p))render()}
  if(el.dataset.promise!==undefined){const p=personFind(personOpen),list=(p.promises||[]).map((x,i)=>i===+el.dataset.promise?{...x,done:el.checked}:x);personQueue({promises:list});render()}
});
document.addEventListener('submit',async e=>{if(e.target.id!=='person-new')return;e.preventDefault();const f=new FormData(e.target);
  try{const p=await api('/api/people',{id:crypto.randomUUID(),name:f.get('name'),email:f.get('email'),pathway:f.get('pathway')});people.items.unshift(p);personOpen=p.id;render()}catch(err){toast(err.message,true)}});

// Anouk's desk -------------------------------------------------------------------------------------------------
// The brief: who needs Josh, then who she's waiting on. Every item opens that person.
function anoukBrief(){
  const rows=ccLive().map(p=>({p,s:ccState(p)})),needs=rows.filter(r=>r.s.needs);
  const waiting=rows.filter(r=>!r.s.needs&&!['closed','rest'].includes(r.s.key));
  const item=({p,s})=>({needs:!!s.needs,icon:s.key==='reply'?'mail':'clock',text:ccName(p),sub:`${ccKind(p)} · ${s.label}`,act:'cc-person:'+p.id});
  const items=[...needs.map(item),...waiting.map(item)];
  const say=!rows.length?'Nobody is in the tracker yet. Tell me about the people you\'d like me to look after, and I\'ll start keeping track.'
    :`I'm keeping an eye on ${rows.length} ${rows.length===1?'person':'people'}. ${needs.length?`${needs.length} ${needs.length===1?'needs':'need'} you`:'Nobody needs you right now'}${waiting.length?`, and I'm waiting on ${waiting.length}.`:'.'}`;
  return {say,items,chips:view==='success'?[]:[{label:'Customer center',act:'view:success'}]};
}
function ccDeskRow(act,name,s){return `<button class="cc-row" data-desk-act="${esc(act)}"><span class="cc-row-who"><b>${esc(name)}</b><small>${esc(s.next)}</small></span>${ccChip(s)}<i aria-hidden="true">›</i></button>`}
function ccCap(s){return s[0].toUpperCase()+s.slice(1)}
// The work beside Anouk's chat. Needs you first, then the people she's following in three groups.
function ccDeskFocus(){
  if(!people)return '<section class="hub-card"><p class="hint">Getting the tracker…</p></section>';
  if(!ccAdmin&&!ccAdminLoading)ccLoadAdmin().then(()=>{if(view==='team'&&teamOpen==='anouk'&&typeof roomWorkRender==='function')roomWorkRender()});
  const rows=ccLive().map(p=>({p,s:ccState(p)})),needs=rows.filter(r=>r.s.needs),act=r=>'cc-person:'+r.p.id;
  const by=(a,b)=>ccLastAt(b.p).localeCompare(ccLastAt(a.p));
  const leads=rows.filter(r=>['reply','lead'].includes(r.p.pathway)&&!r.s.needs&&!['closed','rest'].includes(r.s.key)).sort(by);
  const custs=rows.filter(r=>r.p.pathway==='buyer'&&!r.s.needs&&r.s.key!=='closed').sort(by);
  const tracked=rows.filter(r=>r.p.pathway==='client'&&!r.s.needs).sort(by);
  const roster=ccClientRows().filter(c=>ccActiveClient(c)&&!ccTracked([c.email],c.name));
  const soon=ccAddDays(pToday(),7),booked=roster.filter(c=>c.next_call&&c.next_call.start.slice(0,10)<=soon);
  const stale=roster.filter(c=>!c.next_call&&ccClientState(c).key==='stale');
  const leadRows=leads.map(r=>ccDeskRow(act(r),ccName(r.p),r.s)),custRows=custs.map(r=>ccDeskRow(act(r),ccName(r.p),r.s));
  const clientRows=[...tracked.map(r=>ccDeskRow(act(r),ccName(r.p),r.s)),...booked.map(c=>ccDeskRow('cc-client:'+c.id,c.name,{key:'booked',label:'Call booked',next:`Next call ${ccDay(c.next_call.start)}`}))];
  const clientNote=!ccAdmin?'<p class="hint">Loading the client list…</p>':!ccAdmin.clients?'<p class="hint">The client list couldn\'t load.</p>'
    :stale.length?`<button class="cc-more" data-desk-act="cc:clients">${stale.length} active client${stale.length===1?' has':'s have'} had no call in over two weeks ›</button>`:'';
  const group=(title,list,empty,more,extra='')=>`<div class="cc-group"><h4>${esc(title)} <span>${list.length}</span></h4>${list.slice(0,4).join('')||(extra?'':`<p class="hint">${esc(empty)}</p>`)}${list.length>4?more:''}${extra}</div>`;
  return `<section class="hub-card needs"><header><h3>${needs.length?'Needs you':'Nothing needs you right now'}</h3>${needs.length?`<span class="hub-count">${needs.length}</span>`:''}</header>
      ${needs.length?needs.map(r=>ccDeskRow(act(r),ccName(r.p),{...r.s,next:`${ccCap(ccKind(r.p))}. ${r.s.next}`})).join(''):'<p class="hub-say">Replies, promises and check-ins that are due show here.</p>'}
    </section>
    <section class="hub-card cc-desk"><header><h3>Who Anouk is following</h3><button class="cc-link" data-desk-act="view:success">Customer center ›</button></header>
      ${group('Leads',leadRows,'No leads waiting on a reply.',`<button class="cc-more" data-desk-act="cc:leads">See all ${leadRows.length} leads ›</button>`)}
      ${group('Clients',clientRows,'No client needs watching this week.',`<button class="cc-more" data-desk-act="cc:clients">See all clients ›</button>`,clientNote)}
      ${group('Customers',custRows,'No course buyers flagged. Anouk adds one when someone needs something.',`<button class="cc-more" data-desk-act="cc:learners">See all ${custRows.length} ›</button>`)}
    </section>`;
}
// Anouk's numbers under her desk, all counted from the tracker and Admin.
function ccDeskStats(){
  if(!people)return [{label:'Customers',unavailable:'The customer tracker hasn\'t loaded.',act:'view:success'}];
  const rows=ccLive().map(p=>({p,s:ccState(p)})),n=(label,value,sub,act)=>({label,value,sub,act});
  const leads=rows.filter(r=>['reply','lead'].includes(r.p.pathway)&&!['closed','rest'].includes(r.s.key));
  const cl=ccAdmin?.clients;
  return [n('Need you',rows.filter(r=>r.s.needs).length,'Replies, promises and check-ins','view:success'),
    n('Leads followed',leads.length,'Replied to or waiting on a check-in','cc:leads'),
    cl?n('Active clients',cl.clients.filter(ccActiveClient).length,`${cl.stats.calls_week} call${cl.stats.calls_week===1?'':'s'} booked in the next week`,'cc:clients')
      :{label:'Clients',unavailable:ccAdmin?'The client list couldn\'t load.':'Loading the client list…',act:'cc:clients'},
    n('Promises open',rows.reduce((k,r)=>k+(r.p.promises||[]).filter(x=>!x.done).length,0),'Things you said you would do','view:success')];
}

const successBaseRender=render;
render=function(){if(!current&&view==='success')return renderSuccess();return successBaseRender.apply(this,arguments)};
// Anouk's proposed email replies (studio/replies.py). They stay in CareerOS until Josh approves one exact version of a
// Gmail draft or a send. The approval carries that version's hash, so an edit since then makes it fail and need approving again.
let ccReplies=null,ccRepliesErr='',ccConfirm=null;
async function ccRepliesLoad(){try{ccReplies=await api('/api/replies');ccRepliesErr=''}catch(e){ccRepliesErr=e.message}}
function ccReplyConfirm(r){
  const c=ccConfirm;if(!c||c.id!==r.id)return '';const x=r.offer?.[c.action];if(!x)return '';const i=x.input;
  return `<div class="cc-confirm" role="group" aria-label="Check before you approve">
    <p><b>${c.action==='send'?'Send this email now?':'Save this as a Gmail draft?'}</b></p>
    <dl><dt>From</dt><dd>${esc(x.account)}</dd><dt>To</dt><dd>${esc(i.to.join(', '))}</dd>${i.cc?`<dt>Cc</dt><dd>${esc(i.cc.join(', '))}</dd>`:''}<dt>Subject</dt><dd>${esc(i.subject)}</dd><dt>Version</dt><dd>${esc(x.revision)} (code ${esc(x.code)})</dd></dl>
    <pre>${esc(i.body)}</pre>
    <div class="cc-reply-acts"><button class="small primary" data-reply-go="${esc(c.action)}" data-digest="${esc(x.digest)}">${c.action==='send'?'Yes, send exactly this':'Yes, save this draft'}</button><button class="small" data-reply-cancel>Not yet</button></div>
  </div>`}
// Anouk's quick review (studio/reply_queue.py): every email and YouTube comment reply waiting for Josh, each with what he is
// answering, the thread, exactly where it goes and the full reply. "Send this reply" carries the hash of that exact
// version and destination. An edit makes a new version, and the page reloads it before it can be sent.
let rq=null,rqErr='',rqEdit=null,rqBusy={};
async function rqLoad(){try{[rq]=await Promise.all([api('/api/reply-queue'),ccRepliesLoad()]);rqErr=''}catch(e){rqErr=e.message}}
function rqWhen(t){if(!t)return '';const d=new Date(t);return isNaN(d)?'':d.toLocaleString(undefined,{day:'numeric',month:'short',hour:'numeric',minute:'2-digit'})}
function rqDest(x){const d=x.destination;if(!d)return `<p class="rq-why">${esc(x.why||'')}</p>`;
  if(x.kind==='email')return `<dl class="rq-dest"><dt>From</dt><dd>${esc(d.account)}</dd><dt>To</dt><dd>${esc(d.to.join(', '))}</dd>${d.cc.length?`<dt>Cc</dt><dd>${esc(d.cc.join(', '))}</dd>`:''}<dt>Subject</dt><dd>${esc(d.subject)}</dd><dt>Where</dt><dd>${d.thread_id?'A reply in the same Gmail thread':'A new email'}</dd></dl>`;
  return `<dl class="rq-dest"><dt>Where</dt><dd>A public reply on YouTube from your channel, under ${esc(x.original.author||'this viewer')}'s comment</dd><dt>Video</dt><dd><a href="${esc(d.url)}" target="_blank" rel="noopener">${esc(d.video_id)}</a></dd></dl>`}
function rqThread(x){const t=x.thread||[];
  if(!t.length)return x.kind==='email'?`<p class="rq-note">The earlier messages weren't saved with this proposal.${x.link?` <a href="${esc(x.link)}" target="_blank" rel="noopener">Open the thread in Gmail</a>.`:''}</p>`:'';
  return `<details class="rq-thread"><summary>Earlier in the thread (${t.length})</summary>${t.map(m=>`<div class="rq-msg${m.ours?' ours':''}"><b>${esc(m.sender||m.author||'')}</b><small>${esc(rqWhen(m.at))}</small><p>${esc(m.text)}</p></div>`).join('')}</details>`}
function rqOutcome(o){if(!o)return '';const L={replied:'Sent and checked',sent_unchecked:'Sent, not checked yet',unverified:'Sent, but not confirmed',uncertain:'Not clear if it went',sending:'Sending',stale:'Not sent',done:'Done'};
  return `<p class="rq-outcome s-${esc(o.state)}"><b>${esc(L[o.state]||o.state)}</b>${o.at?` <small>${esc(rqWhen(o.at))}</small>`:''}${o.reason?` ${esc(o.reason)}`:''}</p>`}
function rqCard(x){
  const k=`${x.kind}:${x.id}`,editing=rqEdit===k,busy=rqBusy[k];
  const said=x.original.text||x.original.summary||'';
  return `<article class="rq-card" data-rq="${esc(x.kind)}" data-rq-id="${esc(x.id)}" data-rq-digest="${esc(x.digest||'')}">
    <header class="rq-head"><span class="rq-kind">${x.kind==='email'?'Email':'YouTube comment'}</span><b>${esc(x.who)}</b>${x.original.from_email?`<small>${esc(x.original.from_email)}</small>`:''}<small>${esc(rqWhen(x.original.at))}</small></header>
    <section class="rq-orig"><h4>${x.kind==='email'?esc(x.original.subject||'They wrote'):'They wrote'}</h4><p>${esc(said)}</p>${!x.original.text&&x.kind==='email'?'<p class="rq-note">This is Anouk\'s summary. The email\'s own text wasn\'t saved with this proposal.</p>':''}${x.original.needs?`<p class="rq-note">${esc(x.original.needs)}</p>`:''}${rqThread(x)}</section>
    <section class="rq-goes"><h4>Your reply goes to</h4>${rqDest(x)}</section>
    <section class="rq-reply"><h4>Your reply <small>version ${esc(x.version)}</small></h4>
      ${editing?`<textarea rows="8" data-rq-text>${esc(x.reply)}</textarea>`:`<pre class="rq-text">${esc(x.reply)||'<span class="rq-note">No reply written yet.</span>'}</pre>`}</section>
    ${rqOutcome(x.outcome)}
    <div class="rq-acts">${editing?`<button class="primary small" data-rq-save>Save this version</button><button class="small" data-rq-cancel>Cancel</button>`
      :`<button class="primary" data-rq-send ${x.available&&x.digest&&!busy?'':'disabled'} ${x.available?'':`title="${esc(x.why||'')}"`}>${busy?'Sending…':'Send this reply'}</button><button class="small" data-rq-edit>Edit</button><button class="small" data-rq-dismiss>No reply needed</button>${x.link?`<a class="small" href="${esc(x.link)}" target="_blank" rel="noopener">${x.kind==='email'?'Open in Gmail':'Open on YouTube'}</a>`:''}`}</div>
    ${!x.available&&x.why&&!editing?`<p class="rq-why">${esc(x.why)}</p>`:''}
    ${x.kind==='email'&&!editing?rqMore(x):''}
  </article>`}
function rqMore(x){const r=ccReplies?.items.find(y=>y.id===x.id);if(!r)return '';
  // Sending lives on the main button only, so every send is read back from Gmail. Drafts, copy and edits stay here.
  const one=ccRepliesOldView([r]).replace(/<button[^>]*data-reply-act="send"[^>]*>[^<]*<\/button>/,'');return `<details class="rq-more"><summary>More options: recipients, subject, Gmail draft or copy</summary>${one}</details>`}
function ccRepliesView(){
  if(!rq){rqLoad().then(()=>{if(view==='success'&&!current&&ccTab==='replies')render()});return rqErr?`<p class="hint">${esc(rqErr)}</p>`:'<p class="hint">Getting Anouk\'s replies…</p>'}
  const w=rq.waiting,last=ccReplies?.last;
  const head=`<p class="hint">Each reply goes out only when you press Send this reply, and only the exact version and destination shown. ${rq.email_on?'':'Sending email from Gmail isn\'t switched on yet. '}${rq.comments_on?'':'YouTube comment replies aren\'t connected. '}${last?`Anouk last checked your inbox ${esc(rqWhen(last.at))}.`:''}</p>`;
  return `<div class="rq">${head}${w.length?w.map(rqCard).join(''):'<p class="rq-empty">No replies waiting for you.</p>'}
    ${rq.done.length?`<section class="rq-done"><h3>Recently sent</h3>${rq.done.map(x=>`<div class="rq-done-row"><span class="rq-kind">${x.kind==='email'?'Email':'Comment'}</span><b>${esc(x.who)}</b>${rqOutcome(x.outcome)}</div>`).join('')}</section>`:''}</div>`}
function ccRepliesOldView(only){
  if(only)return only.map(ccReplyRow).join('');
  if(!ccReplies){ccRepliesLoad().then(()=>{if(view==='success'&&!current&&ccTab==='replies')render()});return ccRepliesErr?`<p class="hint">${esc(ccRepliesErr)}</p>`:'<p class="hint">Getting Anouk\'s replies…</p>'}
  const open=ccReplies.items.filter(r=>r.state==='proposed'||r.state==='drafted'),last=ccReplies.last,on=ccReplies.actions_on;
  const head=`<p class="hint">Anouk reads your inbox and writes a reply for each email that needs you. Nothing goes to Gmail until you approve the exact version, here or by sending her its code in chat or on Discord.${on?'':' Gmail drafts and sending aren\'t switched on yet, so for now you can copy a reply into Gmail yourself.'}${ccReplies.configured?'':' Her inbox check is off because Gmail reading isn\'t set up.'}${last?` Last checked ${esc(new Date(last.at).toLocaleString(undefined,{weekday:'short',hour:'numeric',minute:'2-digit'}))}${last.complete===false?', and she couldn\'t cover everything that time':''}.`:''}</p>`;
  if(!open.length)return head+'<p class="hint">No replies waiting.</p>';
  return head+open.map(ccReplyRow).join('')}
function ccReplyRow(r){{const o=r.offer||{},to=o.send?.input?.to||o.draft?.input?.to||[r.from_email],subj=o.send?.input?.subject||o.draft?.input?.subject||r.subject;
    const btn=a=>o[a]?`<button class="small ${a==='send'?'primary':''}" data-reply-act="${a}" ${o[a].available?'':`disabled title="${esc(o[a].why)}"`}>${a==='send'?'Send…':'Save as Gmail draft…'}</button>`:'';
    return `<article class="cc-reply" data-reply="${esc(r.id)}">
    <header><b>${esc(r.from_name||r.from_email)}</b> <small>${esc(r.from_email)}</small><div>${esc(r.subject)}</div><small>${esc(r.summary)}</small>${r.state==='drafted'?'<small class="cc-done">Saved as a Gmail draft.</small>':''}</header>
    <label class="cc-field">To <input data-reply-to value="${esc(to.join(', '))}"></label>
    <label class="cc-field">Subject <input data-reply-subject value="${esc(subj)}"></label>
    <textarea rows="7" data-reply-text>${esc(r.proposed_reply)}</textarea>
    <div class="cc-reply-acts"><button class="small" data-reply-save>Save changes</button>${btn('draft')}${btn('send')}<button class="small" data-reply-copy>Copy</button>${r.link?`<a class="small" href="${esc(r.link)}" target="_blank" rel="noopener">Open in Gmail</a>`:''}<button class="small" data-reply-state="handled">I've replied</button><button class="small" data-reply-state="dismissed">No reply needed</button></div>
    ${ccReplyConfirm(r)}
  </article>`}}
document.addEventListener('click',async e=>{if(view!=='success'||current)return;
  const box=e.target.closest('[data-reply]');if(!box)return;const id=box.dataset.reply,r=ccReplies?.items.find(x=>x.id===id);
  const text=box.querySelector('[data-reply-text]').value,to=box.querySelector('[data-reply-to]').value,subject=box.querySelector('[data-reply-subject]').value;
  const o=r?.offer||{},cur=o.send?.input||o.draft?.input||{},dirty=r&&(text.trim()!==r.proposed_reply||(cur.subject&&subject.trim()!==cur.subject)||(cur.to&&to.split(',').map(x=>x.trim().toLowerCase()).filter(Boolean).join(',')!==cur.to.join(',')));
  try{
    if(e.target.closest('[data-reply-copy]')){await navigator.clipboard.writeText(text);toast('Copied');return}
    if(e.target.closest('[data-reply-save]')){await api('/api/replies/'+id,{changes:{proposed_reply:text,reply_subject:subject,to}});ccConfirm=null;toast('Saved in CareerOS. Any earlier approval no longer counts.');await rqLoad();render();return}
    const act=e.target.closest('[data-reply-act]');if(act){if(dirty){toast('Save your changes first, so you approve the version that will go out.',true);return}ccConfirm={id,action:act.dataset.replyAct};render();return}
    if(e.target.closest('[data-reply-cancel]')){ccConfirm=null;render();return}
    const go=e.target.closest('[data-reply-go]');if(go){go.disabled=true;go.textContent='Working…';
      const a=await api(`/api/replies/${id}/approve`,{action:go.dataset.replyGo,digest:go.dataset.digest});ccConfirm=null;
      toast(a.state==='done'?(a.action==='send'?'Sent, exactly as approved.':'Saved as a Gmail draft.'):a.state==='stale'?a.reason:`Not sure Gmail did it. ${a.reason||''} Check Gmail before trying again.`,a.state!=='done');
      await ccRepliesLoad();render();return}
    const st=e.target.closest('[data-reply-state]');if(st){await api('/api/replies/'+id,{changes:{state:st.dataset.replyState}});await rqLoad();render()}
  }catch(err){toast(err.message,true);await rqLoad();render()}
});

document.addEventListener('click',async e=>{if(view!=='success'||current)return;
  const card=e.target.closest('[data-rq]');if(!card||e.target.closest('.rq-more'))return;
  const kind=card.dataset.rq,id=card.dataset.rqId,k=`${kind}:${id}`,url=`/api/reply-queue/${encodeURIComponent(kind)}/${encodeURIComponent(id)}`;
  try{
    if(e.target.closest('[data-rq-edit]')){rqEdit=k;render();card.ownerDocument.querySelector(`[data-rq-id="${CSS.escape(id)}"] [data-rq-text]`)?.focus();return}
    if(e.target.closest('[data-rq-cancel]')){rqEdit=null;render();return}
    if(e.target.closest('[data-rq-save]')){const text=card.querySelector('[data-rq-text]').value;await api(url,{text});rqEdit=null;toast('Saved as a new version. Check it, then send.');await rqLoad();render();return}
    if(e.target.closest('[data-rq-dismiss]')){await api(url,{dismiss:true});toast('Marked as no reply needed.');await rqLoad();render();return}
    const send=e.target.closest('[data-rq-send]');if(send){if(rqBusy[k])return;rqBusy[k]=true;send.disabled=true;send.textContent='Sending…';
      try{const out=await api(url+'/send',{digest:card.dataset.rqDigest});const o=out.outcome||{};
        toast(o.state==='replied'?(kind==='email'?'Sent, and Gmail shows it as sent.':'Posted, and YouTube shows it under the comment.'):
          o.state==='unverified'?'It may have gone, but it could not be confirmed. Check before doing anything else.':`Not clear if it went. ${o.reason||''}`,o.state!=='replied')}
      finally{rqBusy[k]=false;await rqLoad();render()}return}
  }catch(err){toast(err.message,true);await rqLoad();render()}
});
