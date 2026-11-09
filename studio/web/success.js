'use strict';
// Customer success: everyone Anouk looks after, by pathway. Each person shows their stage,
// the next step and when it's due, what Josh promised them, and every touchpoint.
let people=null,peopleTab='all',personOpen=null,personTimer=null,personBusy=null;
const PEOPLE_TABS=[['all','Everyone'],['reply','Newsletter replies'],['lead','Leads from calls'],['client','Clients'],['buyer','Course buyers']];

function pToday(){return (typeof dateKey==='function'&&typeof plannerToday==='function')?dateKey(plannerToday()):new Date().toISOString().slice(0,10)}
function pDue(d){if(!d)return '';const n=typeof deskDays==='function'?deskDays(d):0;return n<0?`overdue since ${typeof deskWhen==='function'?deskWhen(d):d}`:`due ${typeof deskWhen==='function'?deskWhen(d):d}`}
function pLate(p){return !!(p.next_due&&p.next_due<pToday())||(p.promises||[]).some(x=>!x.done&&x.due&&x.due<pToday())}
async function peopleLoad(){people=await api('/api/people')}
function personFind(id){return people.items.find(p=>p.id===id)}

function personCard(p){
  const open=(p.promises||[]).filter(x=>!x.done),last=(p.history||[]).at(-1),stages=people.pathways[p.pathway].stages;
  return `<article class="person-row ${pLate(p)?'late':''} ${personOpen===p.id?'open':''}" data-person="${esc(p.id)}">
    <button class="person-sum" data-person-open="${esc(p.id)}">
      <span class="who"><b>${esc(p.name||p.email)}</b><small>${esc(p.name?p.email:'')} · ${esc(people.pathways[p.pathway].label)}</small></span>
      <span class="stage">${esc(p.stage)}</span>
      <span class="next">${p.next_step?`${esc(p.next_step)}<small>${esc(pDue(p.next_due))}</small>`:'<small>No next step</small>'}</span>
      <span class="meta">${open.length?`<i>${open.length} promised</i>`:''}${last?`<small>Last: ${esc(last.at)}</small>`:''}</span>
    </button>
    ${personOpen===p.id?personDetail(p,stages):''}
  </article>`;
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

async function renderSuccess(){
  page('Customer success','');
  if(!team&&typeof deskData==='function')await deskData();
  if(!people){$('#workspace').innerHTML='<p class="hint">Getting the tracker…</p>';try{await peopleLoad()}catch(e){toast(e.message,true);return}if(view!=='success'||current)return}
  const rows=people.items.filter(p=>!p.archived&&(peopleTab==='all'||p.pathway===peopleTab));
  rows.sort((a,b)=>(pLate(b)-pLate(a))||(a.next_due||'9999').localeCompare(b.next_due||'9999'));
  const count=k=>people.items.filter(p=>!p.archived&&(k==='all'||p.pathway===k)).length;
  $('#workspace').innerHTML=`${typeof agentDesk==='function'?agentDesk('anouk',{compact:true}):''}<div class="success">
    <div class="success-tabs" role="tablist">${PEOPLE_TABS.map(([k,l])=>`<button role="tab" class="${peopleTab===k?'on':''}" data-people-tab="${k}">${esc(l)} <span class="count">${count(k)}</span></button>`).join('')}</div>
    <form class="person-new" id="person-new"><input name="name" placeholder="Name"><input name="email" type="email" placeholder="Email"><select name="pathway">${Object.entries(people.pathways).map(([k,v])=>`<option value="${k}" ${k===peopleTab?'selected':''}>${esc(v.label)}</option>`).join('')}</select><button class="small">Add person</button></form>
    <div class="people-list">${rows.map(personCard).join('')||`<p class="lane-empty">Nobody here yet. Anouk adds people as she finds them, or add someone above.</p>`}</div>
  </div>`;
}

function personNote(t){const n=$('#person-save');if(n)n.textContent=t}
function personQueue(changes,history){
  const p=personFind(personOpen);if(!p)return;Object.assign(p,changes);p._draft=Object.assign(p._draft||{},changes);if(history)(p._hist=p._hist||[]).push(...history);
  personNote('Saving…');clearTimeout(personTimer);personTimer=setTimeout(()=>personSave(p.id),600);
}
async function personSave(id){
  const p=personFind(id);if(!p||(!p._draft&&!p._hist))return;if(personBusy){await personBusy;return personSave(id)}
  const changes=p._draft||{},history=p._hist||[];delete p._draft;delete p._hist;
  personBusy=(async()=>{try{const saved=await api('/api/people/'+id,{revision:p.revision,changes,add_history:history},'PUT');Object.assign(p,saved);personNote('Saved')}
    catch(e){toast(e.message,true);await peopleLoad();render()}})();await personBusy;personBusy=null;
}

document.addEventListener('click',async e=>{if(view!=='success'||current)return;
  const t=e.target.closest('[data-people-tab]');if(t){peopleTab=t.dataset.peopleTab;personOpen=null;render();return}
  const o=e.target.closest('[data-person-open]');if(o){await personSave(personOpen);personOpen=personOpen===o.dataset.personOpen?null:o.dataset.personOpen;render();return}
  if(e.target.closest('[data-promise-add]')){const p=personFind(personOpen),text=$('#promise-text').value.trim();if(!text)return;
    personQueue({promises:[...(p.promises||[]),{text,due:$('#promise-due').value,done:false}]});render();return}
  if(e.target.closest('[data-note-add]')){const text=$('#note-text').value.trim();if(!text)return;const p=personFind(personOpen);
    const h={at:pToday(),direction:$('#note-dir').value,channel:'note',summary:text};p.history=[...(p.history||[]),h];personQueue({},[h]);render();return}
  if(e.target.closest('[data-person-archive]')&&confirm('Archive this person? They stay in the database but leave the list.')){personQueue({archived:true});await personSave(personOpen);personOpen=null;render();return}
});
document.addEventListener('change',e=>{if(view!=='success'||!personOpen)return;const el=e.target;
  if(el.dataset.p){const ch={[el.dataset.p]:el.value};if(el.dataset.p==='pathway')ch.stage=people.pathways[el.value].stages[0];personQueue(ch);if(['pathway','stage','next_due'].includes(el.dataset.p))render()}
  if(el.dataset.promise!==undefined){const p=personFind(personOpen),list=(p.promises||[]).map((x,i)=>i===+el.dataset.promise?{...x,done:el.checked}:x);personQueue({promises:list});render()}
});
document.addEventListener('input',e=>{if(view!=='success'||!personOpen)return;const el=e.target;if(el.dataset.p&&el.tagName!=='SELECT'&&el.type!=='date')personQueue({[el.dataset.p]:el.value})});
document.addEventListener('submit',async e=>{if(e.target.id!=='person-new')return;e.preventDefault();const f=new FormData(e.target);
  try{const p=await api('/api/people',{id:crypto.randomUUID(),name:f.get('name'),email:f.get('email'),pathway:f.get('pathway')});people.items.unshift(p);personOpen=p.id;render()}catch(err){toast(err.message,true)}});

// Anouk's desk: what's overdue, who's waiting on an answer, and what Josh promised.
function anoukBrief(){
  const live=people.items.filter(p=>!p.archived),today=pToday();
  const late=live.filter(p=>p.next_due&&p.next_due<today),promised=live.flatMap(p=>(p.promises||[]).filter(x=>!x.done).map(x=>({p,x})));
  const latePromised=promised.filter(({x})=>x.due&&x.due<=today),waiting=live.filter(p=>p.stage==='New reply');
  const items=[...latePromised.map(({p,x})=>({needs:true,icon:'clock',text:`Send ${p.name||p.email} ${x.text.replace(/^send\s+/i,'')}`,sub:`Promised${x.due?' for '+x.due:''}`,act:'view:success'})),
    ...waiting.map(p=>({needs:true,icon:'mail',text:`Reply to ${p.name||p.email}`,sub:p.source||'Replied to a newsletter',act:'view:success'})),
    ...late.filter(p=>!waiting.includes(p)).map(p=>({icon:'clock',text:p.next_step||`Check in with ${p.name||p.email}`,sub:`${p.name||p.email} · ${pDue(p.next_due)}`,act:'view:success'}))];
  const say=!live.length?'Nobody is in the tracker yet. Tell me about the people you\'d like me to look after, and I\'ll start keeping track.'
    :`I'm looking after ${live.length} ${live.length===1?'person':'people'}.${waiting.length?` ${waiting.length} replied and ${waiting.length===1?'is':'are'} waiting for an answer.`:''}${latePromised.length?` ${latePromised.length} promise${latePromised.length===1?' is':'s are'} due.`:''}${late.length?` ${late.length} check-in${late.length===1?' is':'s are'} overdue.`:''}`;
  return {say,items,chips:view==='success'?[]:[{label:'Customer success',act:'view:success'}]};
}

const successBaseRender=render;
render=function(){if(!current&&view==='success')return renderSuccess();return successBaseRender.apply(this,arguments)};
