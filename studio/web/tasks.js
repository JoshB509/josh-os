'use strict';
(() => {
  const $=s=>document.querySelector(s),esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const token=$('meta[name=dj-token]').content;
  let data={items:[],targets:[],statuses:[]},editing=null,saving=false,search='',loading=false;
  async function api(path,body,method='POST'){
    const r=await fetch(path,{method:body===undefined?'GET':method,headers:{'X-DJ-Token':token,'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)});
    const result=await r.json();if(!r.ok)throw Error(result.error||'Could not save. Try again.');return result;
  }
  function toast(text,error=false){const el=$('#toast');el.textContent=text;el.style.display='block';el.classList.toggle('error',error);clearTimeout(toast.timer);toast.timer=setTimeout(()=>el.style.display='none',5000)}
  function status(item){return `<select class="status-select" data-status-id="${item.id}" data-status="${esc(item.status)}" aria-label="Status for ${esc(item.title)}">${data.statuses.map(s=>`<option ${s===item.status?'selected':''}>${s}</option>`).join('')}</select>`}
  function meta(item){const target=data.targets.find(t=>t.value===item.link);return `<div class="item-meta">${target?`<a href="${esc(target.url)}">${esc(target.label)} ↗</a>${target.stage?`<span>${esc(target.stage)}${target.archived?' · Archived':''}</span>`:''}`:''}${item.due_date?`<span>Due ${esc(new Date(item.due_date+'T12:00:00').toLocaleDateString(undefined,{month:'short',day:'numeric',year:'numeric'}))}</span>`:''}</div>`}
  function row(item){return `<article class="task-row ${item.status==='Done'?'is-done':''}" data-item="${item.id}"><button class="complete-item" data-complete="${item.id}" aria-label="${item.status==='Done'?'Reopen':'Complete'} ${esc(item.title)}" title="${item.status==='Done'?'Reopen in Next':'Mark done'}">${item.status==='Done'?'✓':''}</button><div class="task-content"><button class="item-title" data-edit="${item.id}">${esc(item.title)}</button>${item.next_step?`<p>${esc(item.next_step)}</p>`:''}${meta(item)}</div>${status(item)}</article>`}
  function render(){
    const visible=data.items.filter(x=>(x.title+' '+x.next_step).toLowerCase().includes(search.toLowerCase()));
    const goals=visible.filter(x=>x.kind==='goal'&&x.status!=='Done');
    $('#goals').innerHTML=goals.map(x=>`<article class="goal-card" data-item="${x.id}"><span class="goal-icon" aria-hidden="true">◎</span><div><button class="goal-title" data-edit="${x.id}">${esc(x.title)}</button></div>${x.next_step?`<p>${esc(x.next_step)}</p>`:''}<div class="item-meta">${status(x)}${meta(x)}</div></article>`).join('')||'<div class="empty-goals">'+(search?'No goals match your search.':'What are you working towards? Add a goal to keep it in view.')+'</div>';
    $('#task-groups').innerHTML=data.statuses.filter(s=>s!=='Done').map(s=>{const items=visible.filter(x=>x.kind==='task'&&x.status===s);return `<section class="task-group" data-status="${esc(s)}"><div class="task-group-head"><span class="group-dot"></span><h3>${esc(s)}</h3><span class="task-count">${items.length}</span><button class="subtle small" data-add-status="${esc(s)}" aria-label="Add task to ${esc(s)}">+ Add</button></div>${items.map(row).join('')||'<div class="group-empty">'+(search?'No matching tasks.':'Nothing here yet.')+'</div>'}</section>`}).join('');
    const done=visible.filter(x=>x.status==='Done');$('#completed-label').textContent='Completed · '+done.length;$('#completed-list').innerHTML=done.map(row).join('')||'<p class="group-empty">Completed tasks and goals will stay here.</p>';
    const open=data.items.filter(x=>x.status!=='Done').length;$('#task-summary').textContent=open+' open · '+data.items.filter(x=>x.status==='Done').length+' completed';$('#tasks-loading').hidden=true;$('#tasks-content').hidden=false;
  }
  async function load(){if(loading)return;loading=true;try{data=await api('/api/tasks');$('#load-error').hidden=true;render()}catch(e){$('#load-error').hidden=false;$('#load-error-text').textContent=e.message}finally{loading=false;$('#tasks-loading').hidden=true}}
  function replace(item){const i=data.items.findIndex(x=>x.id===item.id);if(i<0)data.items.push(item);else data.items[i]=item;render()}
  function edit(item=null,kind='task',state='Next'){
    editing=item?structuredClone(item):{id:crypto.randomUUID(),kind,status:state,title:'',next_step:'',link:'',due_date:''};$('#dialog-title').textContent=item?'Edit '+item.kind:'New '+kind;
    $('#item-title').value=editing.title;$('#item-kind').value=editing.kind;$('#item-status').innerHTML=data.statuses.map(s=>`<option ${s===editing.status?'selected':''}>${esc(s)}</option>`).join('');$('#item-next').value=editing.next_step;
    $('#item-link').innerHTML='<option value="">No link</option>'+data.targets.map(t=>`<option value="${esc(t.value)}" ${t.value===editing.link?'selected':''}>${esc(t.label)}${t.archived?' (archived)':''}</option>`).join('');$('#item-due').value=editing.due_date;$('#form-error').textContent='';$('#reload-item').hidden=true;$('#task-dialog').showModal();$('#item-title').focus();
  }
  async function change(id,changes){const item=data.items.find(x=>x.id===id);if(!item)return;document.querySelectorAll(`[data-item="${id}"] button,[data-item="${id}"] select`).forEach(b=>b.disabled=true);try{replace(await api('/api/tasks/'+id,{revision:item.revision,changes},'PUT'));toast(changes.status==='Done'?'Marked done':changes.status==='Next'&&item.status==='Done'?'Moved back to Next':'Saved')}catch(e){toast(e.message,true);await load()}}
  $('#new-task').onclick=()=>edit();$('#new-goal').onclick=()=>edit(null,'goal');$('#cancel-item').onclick=()=>$('#task-dialog').close();$('#retry-load').onclick=load;
  $('#task-search').oninput=e=>{search=e.target.value;render()};
  document.addEventListener('click',e=>{const b=e.target.closest('button');if(!b)return;if(b.dataset.edit)edit(data.items.find(x=>x.id===b.dataset.edit));if(b.dataset.complete){const item=data.items.find(x=>x.id===b.dataset.complete);change(item.id,{status:item.status==='Done'?'Next':'Done'})}if(b.dataset.addStatus)edit(null,'task',b.dataset.addStatus)});
  document.addEventListener('change',e=>{if(e.target.dataset.statusId)change(e.target.dataset.statusId,{status:e.target.value})});
  $('#task-form').onsubmit=async e=>{e.preventDefault();if(saving)return;saving=true;$('#save-item').disabled=true;$('#form-error').textContent='';const changes={title:$('#item-title').value,kind:$('#item-kind').value,status:$('#item-status').value,next_step:$('#item-next').value,link:$('#item-link').value,due_date:$('#item-due').value};try{const item=editing.revision?await api('/api/tasks/'+editing.id,{revision:editing.revision,changes},'PUT'):await api('/api/tasks',{...changes,id:editing.id});replace(item);$('#task-dialog').close();toast('Saved')}catch(err){$('#form-error').textContent=err.message;$('#reload-item').hidden=!editing.revision}finally{saving=false;$('#save-item').disabled=false}};
  $('#reload-item').onclick=async()=>{await load();const latest=data.items.find(x=>x.id===editing.id);if(latest){$('#task-dialog').close();edit(latest)}};
  setInterval(()=>{if(!document.hidden&&!$('#task-dialog').open&&!saving&&!document.activeElement?.matches('input,textarea,select,button'))load()},5000);
  window.addEventListener('focus',()=>{if(!$('#task-dialog').open)load()});load();
})();
