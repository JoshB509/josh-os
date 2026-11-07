'use strict';
// Team page: the named agents, their profiles and the shared lessons-learned log.
// Profiles autosave. The agents do not run yet, so the chat box is a placeholder.
let team=null,teamOpen=null,teamTimer=null,teamSaving=null;

// A sub-page under one of the views: hides the big heading and shows "Parent / Name" crumbs.
function subPage(parent,name,back){
  $('#page-title').parentElement.hidden=true;document.title=name+' · Josh OS';
  $('#crumbs').innerHTML=`<button ${back}>${esc(parent)}</button><i>/</i><span>${esc(name)}</span>`;
}
function teamAvatar(a,size=''){
  const initials=esc((a.name||'?').trim().slice(0,1).toUpperCase());
  return a.photo?`<img class="avatar ${size}" src="/assets/${esc(a.photo)}?w=256" alt="">`
    :`<span class="avatar ${size}" style="--agent:${esc(a.color)}">${initials}</span>`;
}
function teamStatus(a){return `<span class="agent-status s-${esc(a.status.toLowerCase().replace(/\s+/g,'-'))}">${esc(a.status)}</span>`}
function teamFlows(a){return a.workflows.map(w=>`<span class="flow">${esc(team.workflows[w]||w)}</span>`).join('')}
function teamAgent(id){return team.agents.find(a=>a.id===id)}
function teamLearning(l){
  const a=teamAgent(l.agent_id);
  return `<li class="learning"><div><p>${esc(l.lesson)}</p>${l.evidence?`<small>${esc(l.evidence)}</small>`:''}
    <div class="learning-meta">${a?`${teamAvatar(a,'xs')} ${esc(a.name)}`:'Josh'}${l.workflow?` · ${esc(team.workflows[l.workflow]||l.workflow)}`:''} · ${esc(new Date(l.created_at).toLocaleDateString(undefined,{day:'numeric',month:'short'}))}</div></div>
    <button class="subtle small" data-team-forget="${esc(l.id)}" aria-label="Remove lesson">Remove</button></li>`;
}
function teamLearnForm(agentId=''){
  const options=Object.entries(team.workflows).map(([k,v])=>`<option value="${esc(k)}">${esc(v)}</option>`).join('');
  return `<form class="learn-form" data-team-learn="${esc(agentId)}">
    <textarea name="lesson" rows="2" placeholder="What did we learn? e.g. Carousels that open with a number get twice the saves" required maxlength="2000"></textarea>
    <div class="learn-row">${agentId?'':`<select name="agent_id"><option value="">From Josh</option>${team.agents.map(a=>`<option value="${esc(a.id)}">${esc(a.name)}</option>`).join('')}</select>`}
    <select name="workflow"><option value="">Any workflow</option>${options}</select>
    <input name="evidence" placeholder="Evidence (optional), e.g. post link or numbers" maxlength="2000">
    <button class="primary small">Add lesson</button></div></form>`;
}

async function renderTeam(){
  page('Your team','The people who run each workflow with you. Open a profile to shape who they are and what they own.');
  if(!team){$('#workspace').innerHTML='<p class="hint">Loading your team…</p>';try{team=await api('/api/team')}catch(e){$('#workspace').innerHTML=`<div class="empty"><h2>Could not load the team.</h2><p>${esc(e.message)}</p></div>`;return}
    if(view!=='team'||current)return}
  if(teamOpen)return renderTeamProfile();
  $('#workspace').innerHTML=`<div class="team-grid">${team.agents.map(a=>`
    <article class="agent-card" data-team-open="${esc(a.id)}" tabindex="0" style="--agent:${esc(a.color)}">
      <div class="agent-head">${teamAvatar(a,'lg')}<div><h3>${esc(a.name)}</h3><p class="role">${esc(a.role)}</p></div>${teamStatus(a)}</div>
      <p class="mission">${esc(a.mission)}</p>
      <div class="flows">${teamFlows(a)}</div>
    </article>`).join('')}</div>
    <section class="learnings"><h2>Lessons learned</h2>
      <p class="hint">What the team has found works and what doesn't. Bob and Vera add to this as they track performance, and every agent reads it before starting work.</p>
      ${teamLearnForm()}
      <ul class="learning-list">${team.learnings.length?team.learnings.map(teamLearning).join(''):'<li class="hint">No lessons yet. Add the first one above.</li>'}</ul>
    </section>`;
}

function renderTeamProfile(){
  const a=teamAgent(teamOpen);if(!a){teamOpen=null;return renderTeam()}
  const lines=x=>esc(x.join('\n'));
  const mine=team.learnings.filter(l=>l.agent_id===a.id);
  page(a.name,a.role);subPage('Team',a.name,'data-team-back');
  $('#workspace').innerHTML=`<div class="crumb"><button data-team-back>← Back to the team</button></div>
  <div class="profile" style="--agent:${esc(a.color)}">
    <div class="profile-top">
      <label class="photo-pick" title="Change photo">${teamAvatar(a,'xl')}<input type="file" accept="image/png,image/jpeg,image/webp" data-team-photo hidden><span>Change photo</span></label>
      <div class="profile-name"><input class="name" data-team-field="name" value="${esc(a.name)}" maxlength="60" aria-label="Name">
        <input class="role-input" data-team-field="role" value="${esc(a.role)}" maxlength="120" aria-label="Role"></div>
      <div class="profile-side"><select data-team-field="status" aria-label="Status">${team.statuses.map(s=>`<option ${s===a.status?'selected':''}>${esc(s)}</option>`).join('')}</select>
        <input type="color" data-team-field="color" value="${esc(a.color)}" aria-label="Colour"><span id="team-save" class="save-note">Saved</span></div>
    </div>
    <div class="profile-grid">
      <section>
        <section class="chat-preview"><h2>Talk to ${esc(a.name)}</h2>
          <div class="chat-bubble">${teamAvatar(a,'xs')}<p>Hi Josh, I'm ${esc(a.name)}. Once I'm switched on I'll check in with you here.${a.check_in?`<br><small>Check-ins: ${esc(a.check_in)}</small>`:''}</p></div>
          <div class="chat-input"><input disabled placeholder="Chat arrives in the next build"><button class="primary small" disabled>Send</button></div>
        </section>
        <label>Mission<textarea data-team-field="mission" rows="4" maxlength="2000">${esc(a.mission)}</textarea></label>
        <label>Responsibilities <small>one per line</small><textarea data-team-list="responsibilities" rows="6">${lines(a.responsibilities)}</textarea></label>
        <label>Personality and tone<textarea data-team-field="personality" rows="3" maxlength="1000">${esc(a.personality)}</textarea></label>
        <label>Check-in<input data-team-field="check_in" value="${esc(a.check_in)}" maxlength="500" placeholder="When ${esc(a.name)} messages you, and with what"></label>
      </section>
      <aside>
        <h3>Workflows</h3>
        <div class="flow-picks">${Object.entries(team.workflows).map(([k,v])=>`<label class="check"><input type="checkbox" data-team-flow="${esc(k)}" ${a.workflows.includes(k)?'checked':''}>${esc(v)}</label>`).join('')}</div>
        <label>Skills and tools <small>one per line</small><textarea data-team-list="skills" rows="5">${lines(a.skills)}</textarea></label>
        <h3>Lessons from ${esc(a.name)}</h3>
        ${teamLearnForm(a.id)}
        <ul class="learning-list">${mine.length?mine.map(teamLearning).join(''):'<li class="hint">Nothing yet.</li>'}</ul>
      </aside>
    </div>
  </div>`;
}

function teamNote(text){const n=$('#team-save');if(n)n.textContent=text}
function teamQueue(){teamNote('Saving…');clearTimeout(teamTimer);teamTimer=setTimeout(teamSave,700)}
async function teamSave(){
  const a=teamAgent(teamOpen);if(!a)return;
  if(teamSaving){await teamSaving;return teamSave()}
  const changes=a._draft;if(!changes)return;delete a._draft;
  teamSaving=(async()=>{
    try{const saved=await api('/api/team/'+a.id,{revision:a.revision,changes},'PUT');
      const pending=a._draft;Object.assign(a,saved);if(pending){a._draft=pending;Object.assign(a,pending)}
      teamNote(a._draft?'Saving…':'Saved');if(a._draft)teamQueue();}
    catch(e){a._draft=Object.assign(changes,a._draft||{});teamNote('Not saved');toast(e.message,true)}
    finally{teamSaving=null}
  })();
  return teamSaving;
}
function teamChange(key,value){const a=teamAgent(teamOpen);if(!a)return;a[key]=value;a._draft=Object.assign(a._draft||{},{[key]:value});teamQueue()}

document.addEventListener('input',e=>{
  if(view!=='team'||!teamOpen)return;const el=e.target;
  if(el.dataset.teamField&&el.type!=='color')teamChange(el.dataset.teamField,el.value);
  if(el.dataset.teamList)teamChange(el.dataset.teamList,el.value.split('\n').map(x=>x.trim()).filter(Boolean));
});
document.addEventListener('change',async e=>{
  if(view!=='team'||!teamOpen)return;const el=e.target;
  if(el.dataset.teamField&&(el.type==='color'||el.tagName==='SELECT')){teamChange(el.dataset.teamField,el.value);if(el.type==='color')document.querySelector('.profile').style.setProperty('--agent',el.value)}
  if(el.dataset.teamFlow){const a=teamAgent(teamOpen);teamChange('workflows',Object.keys(team.workflows).filter(k=>k===el.dataset.teamFlow?el.checked:a.workflows.includes(k)))}
  if(el.dataset.teamPhoto!==undefined&&el.files[0]){
    try{const data=await new Promise((ok,bad)=>{const r=new FileReader();r.onload=()=>ok(r.result.split(',')[1]);r.onerror=bad;r.readAsDataURL(el.files[0])});
      const img=await api('/api/images',{name:el.files[0].name,data});teamChange('photo',img.asset);await teamSaving;renderTeamProfile()}
    catch(err){toast(err.message,true)}}
});
document.addEventListener('submit',async e=>{
  const form=e.target.closest('[data-team-learn]');if(!form)return;e.preventDefault();
  const f=new FormData(form),body={id:crypto.randomUUID(),lesson:f.get('lesson')||'',workflow:f.get('workflow')||'',evidence:f.get('evidence')||'',agent_id:form.dataset.teamLearn||f.get('agent_id')||''};
  try{const item=await api('/api/team/learnings',body);team.learnings.unshift(item);toast('Lesson added');render()}catch(err){toast(err.message,true)}
});
document.addEventListener('click',async e=>{
  if(view!=='team')return;
  const open=e.target.closest('[data-team-open]');if(open){teamOpen=open.dataset.teamOpen;render();scrollTo(0,0);return}
  if(e.target.closest('[data-team-back]')){await teamSaving;teamOpen=null;render();return}
  const forget=e.target.closest('[data-team-forget]');
  if(forget&&confirm('Remove this lesson?')){try{await api('/api/team/learnings/'+forget.dataset.teamForget,{delete:true});team.learnings=team.learnings.filter(l=>l.id!==forget.dataset.teamForget);render()}catch(err){toast(err.message,true)}}
});
document.addEventListener('keydown',e=>{const c=e.target.closest?.('[data-team-open]');if(c&&e.key==='Enter'){teamOpen=c.dataset.teamOpen;render()}});
const teamBaseRender=render;render=function(){if(!current&&view==='team')return renderTeam();return teamBaseRender()};
