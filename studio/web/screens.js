'use strict';
// Screen fixes from the whole-app critique (docs/design/critique-2026-11-07.md):
// a "next step" line on every video, the right breadcrumb, and a phone agenda for the calendar.

// The video page says what happens next and who is on it.
function videoNext(p){
  const has=x=>x&&String(x).trim();
  if(['Idea','Scripting'].includes(p.stage)&&!has(p.script))return ['sam','The angle is set, so the next step is the script.','script','Write the script'];
  if(['Idea','Scripting'].includes(p.stage)&&!p.titles.length)return ['sam','The script is underway. Add a few title ideas so we can test the promise.','titles','Add title ideas'];
  if(['Idea','Scripting'].includes(p.stage))return ['sam','Script and titles are in. When you\'re happy, move it to Ready to record.',null,null];
  if(['Ready to record','Recording'].includes(p.stage))return ['tariq','Once it\'s recorded, set the stage to Recorded and I\'ll start the edit.','edit','Add the recording'];
  if(p.stage==='Recorded')return ['tariq','The recording is in, so I can start the clean cut.','edit','Start the edit'];
  if(p.stage==='Editing')return ['tariq','I\'m working on the edit. You can follow each pass here.','edit','See the edit'];
  if(p.stage==='Review'&&!p.top_three.length)return ['sam','The edit is ready for your review, and it still needs a thumbnail.','thumbnails','Pick a thumbnail'];
  if(p.stage==='Review')return ['tariq','The edit is ready. Watch it and tell me what to change.','edit','Watch the edit'];
  if(p.stage==='Scheduled'&&!p.top_three.length)return ['sam',`It goes out ${typeof deskWhen==='function'?deskWhen(p.publish_date):p.publish_date||'soon'}, and it still needs a thumbnail.`,'thumbnails','Pick a thumbnail'];
  if(p.stage==='Scheduled')return ['sam',`Everything is set. It goes out ${typeof deskWhen==='function'?deskWhen(p.publish_date):p.publish_date||'soon'}.`,null,null];
  return ['sam','It\'s live. Add what we learned from it to the team\'s lessons.',null,null];
}
const screensBaseDetail=renderDetail;
renderDetail=function(){
  const r=screensBaseDetail.apply(this,arguments);
  const p=current,top=document.querySelector('#workspace .project-top');
  if(p&&top&&!document.querySelector('.next-step')){
    const [who,say,t,label]=videoNext(p),a=team?.agents?.find(x=>x.id===who);
    top.insertAdjacentHTML('afterend',`<div class="next-step" style="--agent:${esc(a?.color||'var(--ink)')}">${a?teamAvatar(a,'sm'):''}<p><b>${esc(a?.name||'Next')}</b> ${esc(say)}</p>${t&&tab!==t?`<button class="small" data-tab="${t}">${esc(label)}</button>`:''}</div>`);
  }
  return r;
};

// Opening a video from Today or Team files it under the workflow it belongs to.
const screensBaseOpen=openProject;
openProject=function(id){if(['today','team'].includes(view)){const p=state.projects.find(x=>x.id===id);view=p&&['Recorded','Editing','Review'].includes(p.stage)?'editing':'board'}return screensBaseOpen(id)};

// Calendar on a phone: a four-week agenda instead of a seven-column grid that doesn't fit.
const screensBaseCalendar=renderCalendar;
renderCalendar=function(){
  const r=screensBaseCalendar.apply(this,arguments);
  if(!matchMedia('(max-width:760px)').matches||current)return r;
  const layout=document.querySelector('#workspace .calendar-layout');if(!layout)return r;
  if(!deskCarousels&&typeof carousels!=='undefined'&&!carousels){deskData().then(()=>{if(view==='calendar'&&!current)render()});}
  const start=plannerToday(),rows=[];
  for(let i=0;i<28;i++){const d=new Date(start);d.setDate(d.getDate()+i);const key=dateKey(d);
    const items=[...state.projects.filter(p=>!p.archived&&p.publish_date===key).map(p=>['Video',p.name,'project:'+p.id]),
      ...deskCarouselRows().filter(c=>c.post_date===key).map(c=>['Carousel',c.title,'carousel:'+c.id]),
      ...deskEmailRows().filter(e=>e.planned_date===key).map(e=>['Email',e.subject||'Untitled email','email:'+e.id])];
    if(items.length)rows.push(`<section class="agenda-day ${i===0?'is-today':''}"><h3>${esc(i===0?'Today':i===1?'Tomorrow':d.toLocaleDateString(undefined,{weekday:'long',day:'numeric',month:'short'}))}</h3>${items.map(([k,t,a])=>`<button class="week-item k-${k.toLowerCase()}" data-desk-act="${esc(a)}"><small>${k}</small>${esc(t)}</button>`).join('')}</section>`)}
  layout.hidden=true;
  layout.insertAdjacentHTML('beforebegin',`<div class="agenda"><p class="hint">The next four weeks. Turn your phone sideways or use a computer to drag dates on the full month.</p>${rows.join('')||'<p class="week-empty">Nothing planned in the next four weeks.</p>'}</div>`);
  return r;
};
