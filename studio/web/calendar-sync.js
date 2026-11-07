'use strict';
// Views share the existing project, email and task records. No calendar table.
let calendarTaskRows=[],plannerRefreshing=false,plannerDragging=false;
const plannerBaseRefresh=refresh;
refresh=async function(){
  await Promise.all([plannerBaseRefresh(),api('/api/tasks').then(r=>calendarTaskRows=r.items)]);
};
function calendarTaskCard(t){
  const card=slotExtraCard('task',t.title,t.status==='Done'?'check':'task',t.status);
  card.dataset.taskId=t.id;card.draggable=true;card.tabIndex=0;
  card.setAttribute('role','link');card.title=t.title+'\n'+t.status+'\nOpen Tasks & Goals to edit';
  return card;
}
const plannerBaseCalendar=renderCalendar;
renderCalendar=function(){
  plannerBaseCalendar();
  const host=document.querySelector('.unscheduled');
  if(host){
    calendarTaskRows.filter(t=>!t.due_date).forEach(t=>host.append(calendarTaskCard(t)));
    const count=host.querySelector('h3 .count');
    if(count)count.textContent=host.querySelectorAll('.cal-project:not([hidden])').length;
  }
};
document.addEventListener('dragstart',e=>{
  plannerDragging=true;
  const t=e.target.closest('[data-task-id]');
  if(t)e.dataTransfer.setData('text/plain','task:'+t.dataset.taskId);
});
document.addEventListener('dragend',()=>plannerDragging=false);
document.addEventListener('drop',async e=>{
  plannerDragging=false;
  const target=e.target.closest('[data-date]'),key=e.dataTransfer.getData('text/plain');
  if(!target||!key.startsWith('task:'))return;
  e.preventDefault();target.classList.remove('over');
  const t=calendarTaskRows.find(t=>t.id===key.slice(5));if(!t)return;
  try{
    const saved=await api('/api/tasks/'+t.id,{revision:t.revision,changes:{due_date:target.dataset.date}},'PUT');
    await refresh();render();toast('Task date saved');
    const undo=document.createElement('button');undo.textContent='Undo';undo.className='small subtle';
    undo.onclick=async()=>{try{
      await api('/api/tasks/'+t.id,{revision:saved.revision,changes:{due_date:t.due_date}},'PUT');
      await refresh();render();toast('Task date restored');
    }catch(err){toast(err.message,true)}};
    $('#toast').append(undo);
  }catch(err){toast(err.message,true);await refresh();render()}
});
function openCalendarTask(e){
  const t=e.target.closest('[data-task-id]');
  if(t&&(e.type==='click'||e.key==='Enter')){e.preventDefault();location.href='/tasks'}
}
document.addEventListener('click',openCalendarTask);
document.addEventListener('keydown',openCalendarTask);
async function refreshPlannerViews(){
  // Never replace a draft, focused form control, or active drag.
  if(plannerRefreshing||dirty||current||emailCurrent||plannerDragging||document.hidden||
     !['board','calendar','email','archive'].includes(view)||
     document.activeElement?.matches('input,textarea,select')||
     document.querySelector('dialog[open]'))return;
  plannerRefreshing=true;
  const before=JSON.stringify([state.projects,emailRows,calendarTaskRows]);
  try{
    await refresh();
    if(!dirty&&!current&&!emailCurrent&&!plannerDragging&&
       before!==JSON.stringify([state.projects,emailRows,calendarTaskRows]))render();
  }catch(e){/* Retain the last good view while the local server is unavailable. */}
  finally{plannerRefreshing=false}
}
window.addEventListener('focus',refreshPlannerViews);
document.addEventListener('visibilitychange',()=>{if(!document.hidden)refreshPlannerViews()});
setInterval(refreshPlannerViews,4000);
refresh().then(()=>{if(!dirty&&!current&&!emailCurrent)render()}).catch(e=>toast(e.message,true));
