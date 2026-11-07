'use strict';
// Editing: every video from recording onwards, with its runs, in one place.
const EDIT_LANES=[['Ready to record','Ready to record'],['Recording','Recording'],['Recorded','Waiting for the edit'],['Editing','In the edit'],['Review','Ready for your review']];

function renderEditing(){
  page('Editing','Every video from the recording to the Premiere hand-off. Open one to run the editing passes.');
  const ps=state.projects.filter(p=>!p.archived);
  const running=state.jobs.filter(j=>['running','starting'].includes(j.status?.state));
  const editor=team?.agents?.find(a=>a.id==='tariq');
  $('#workspace').innerHTML=`${editor?`<div class="owner-note">${teamAvatar(editor,'xs')}<span><b>${esc(editor.name)}</b> owns this workflow once agents are switched on.</span></div>`:''}
    ${running.length?`<div class="running-note"><span class="spinner"></span>${running.length} editing run${running.length>1?'s':''} in progress</div>`:''}
    <div class="edit-lanes">${EDIT_LANES.map(([stage,label])=>{const items=ps.filter(p=>p.stage===stage);
      return `<section class="edit-lane"><h3>${esc(label)} <span class="count">${items.length}</span></h3>${items.map(p=>{
        const jobs=state.jobs.filter(j=>j.project_id===p.id),last=jobs[0],active=jobs.some(j=>['running','starting'].includes(j.status?.state));
        return `<button class="edit-row" data-edit-open="${esc(p.id)}"><b>${esc(p.name)}</b><span>${active?'<span class="spinner"></span> Running':last?esc(last.status?.state||'Run saved'):'No runs yet'} · ${esc(formatDate(p.publish_date))}</span></button>`}).join('')||'<p class="empty-column">Nothing here</p>'}</section>`}).join('')}</div>`;
  if(!team)api('/api/team').then(t=>{team=t;if(view==='editing'&&!current)renderEditing()}).catch(()=>{});
}

document.addEventListener('click',e=>{
  const open=e.target.closest('[data-edit-open]');if(!open||view!=='editing')return;
  openProject(open.dataset.editOpen);if(current){tab='edit';renderDetail();scrollTo(0,0)}
});
const editingBaseRender=render;
render=function(){if(!current&&view==='editing')return renderEditing();return editingBaseRender()};
