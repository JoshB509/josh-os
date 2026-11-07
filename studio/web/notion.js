'use strict';
/* Notion-style shell: page names, breadcrumbs and the sidebar list of videos. Presentation only. */
// Notion's tag colours for the eight stages, in order.
colors.splice(0,colors.length,'#9b9a97','#d9730d','#dfab01','#0b6e99','#6940a5','#ad1a72','#2383e2','#0f7b6c');
const viewNames={board:'YouTube',calendar:'Calendar',inspiration:'Library',archive:'Archive'};
const _page=page;
page=function(title,subtitle){
  _page(title,subtitle);
  const name=viewNames[view]||'YouTube';
  $('#page-title').parentElement.hidden=!!current;
  $('#page-title').textContent=name;
  $('#page-subtitle').textContent='';
  document.title=(current?current.name:name)+' · Josh OS';
  $('#crumbs').innerHTML=current
    ?`<button data-action="back">${esc(name)}</button><i>/</i><span>${esc(current.name||'Untitled')}</span>`
    :`<span>${esc(name)}</span>`;
  railPages();
};
function railPages(){
  const host=$('#rail-pages');if(!host)return;
  const order=s=>state.stages.indexOf(s);
  const ps=state.projects.filter(p=>!p.archived&&p.stage!=='Published').sort((a,b)=>order(a.stage)-order(b.stage)||a.name.localeCompare(b.name));
  host.innerHTML=ps.map(p=>`<button class="rail-page ${current?.id===p.id?'on':''}" data-open="${esc(p.id)}" title="${esc(p.name)} · ${esc(p.stage)}" style="--stage:${colors[order(p.stage)]||'#9b9a97'}"><i></i><span>${esc(p.name)}</span></button>`).join('')||'<div class="hint" style="padding:4px 10px;font-size:13px">No videos in progress</div>';
}
const _refresh=refresh;
refresh=async function(){await _refresh();railPages()};
// Keep the breadcrumb title in step while renaming.
document.addEventListener('input',e=>{if(current&&e.target.dataset?.field==='name'){const c=$('#crumbs span');if(c)c.textContent=e.target.value||'Untitled'}});
// Arriving from the lessons page with #calendar, #inspiration and so on.
if(typeof initialView==='string'&&viewNames[initialView]){view=initialView;document.querySelectorAll('[data-view]').forEach(b=>b.classList.toggle('active',b.dataset.view===view));refresh().then(render).catch(()=>{})}

/* Ideas stay tucked away unless asked for, so the board, list, calendar and sidebar stay short. */
const IDEA='Idea';
let showIdeas=false;try{showIdeas=localStorage.getItem('dj-show-ideas')==='1'}catch{}
function ideaCount(){return state.projects.filter(p=>!p.archived&&p.stage===IDEA).length}
function tuckIdeas(){
  const n=ideaCount();if(!n||(typeof stageFilter!=='undefined'&&stageFilter===IDEA))return;
  const col=document.querySelector(`.column[data-stage="${IDEA}"]`);
  if(col){col.classList.toggle('tucked',!showIdeas);col.querySelector('.column-head').insertAdjacentHTML('beforeend',`<button class="tuck" data-ideas>${showIdeas?'Hide':'Show'}</button>`)}
  if(showIdeas)return;
  for(const [box,rows,label] of [[document.querySelector('.project-list'),':scope>button','.stage-pill'],[document.querySelector('.unscheduled'),'.cal-project','small']]){
    if(!box)continue;let hidden=0;
    box.querySelectorAll(rows).forEach(b=>{if(b.querySelector(label)?.textContent.trim()===IDEA){b.hidden=true;hidden++}});
    const c=box.querySelector('h3 .count');if(c&&hidden)c.textContent=Number(c.textContent)-hidden;
    if(hidden)box.insertAdjacentHTML('beforeend',`<button class="tuck-row" data-ideas>${hidden} idea${hidden>1?'s':''} tucked away · Show</button>`);
  }
}
const _renderBoard=renderBoard;renderBoard=function(){_renderBoard();tuckIdeas()};
const _renderCalendar=renderCalendar;renderCalendar=function(){_renderCalendar();tuckIdeas()};
railPages=function(){
  const host=$('#rail-pages');if(!host)return;
  const order=s=>state.stages.indexOf(s),row=p=>`<button class="rail-page ${current?.id===p.id?'on':''}" data-open="${esc(p.id)}" title="${esc(p.name)} · ${esc(p.stage)}" style="--stage:${colors[order(p.stage)]||'#9b9a97'}"><i></i><span>${esc(p.name)}</span></button>`;
  const live=state.projects.filter(p=>!p.archived&&p.stage!=='Published').sort((a,b)=>order(a.stage)-order(b.stage)||a.name.localeCompare(b.name));
  const work=live.filter(p=>p.stage!==IDEA),ideas=live.filter(p=>p.stage===IDEA),openIdea=ideas.some(p=>p.id===current?.id);
  host.innerHTML=(work.map(row).join('')||'<div class="hint" style="padding:4px 10px;font-size:13px">Nothing in progress</div>')
    +(ideas.length?`<button class="rail-page rail-toggle ${showIdeas||openIdea?'open':''}" data-ideas><b>›</b><span>Ideas</span><em>${ideas.length}</em></button>${showIdeas||openIdea?ideas.map(row).join(''):''}`:'');
};
document.addEventListener('click',e=>{if(!e.target.closest('[data-ideas]'))return;showIdeas=!showIdeas;try{localStorage.setItem('dj-show-ideas',showIdeas?'1':'0')}catch{}railPages();if(!current&&(view==='board'||view==='calendar'))render()});
