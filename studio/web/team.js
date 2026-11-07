'use strict';
// Team page: the named agents, their profiles and the shared lessons-learned log.
// Each member has their own page with a chat (run through Claude Code on this PC) and what they're working on.
// Profiles autosave.
const TEAM_HOME={sam:'carousels',tariq:'editing',laia:'email',saskia:'lessons'};
let team=null,teamOpen=null,teamTimer=null,teamSaving=null;
let roomThread={},roomBusy=null,roomStatus=null,roomLoaded={};

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
  page('Team','');$('#page-title').parentElement.hidden=true;
  if(!team){$('#workspace').innerHTML=`<div class="people" aria-label="Getting the team's notes">${typeof deskSkeleton==='function'?deskSkeleton(4):''}</div>`;try{team=await api('/api/team')}catch(e){$('#workspace').innerHTML=`<div class="empty"><h2>Could not load the team.</h2><p>${esc(e.message)}</p></div>`;return}
    if(view!=='team'||current)return}
  if(teamOpen)return renderTeamProfile();
  if(typeof deskData==='function'){await deskData();if(view!=='team'||current||teamOpen)return}
  const brief=a=>typeof deskBrief==='function'?deskBrief(a.id):{say:a.mission,items:[]};
  $('#workspace').innerHTML=`<header class="team-intro"><h1>Your team</h1><p>Each of them looks after one part of the business. Tap someone to see what they're working on.</p></header>
    <div class="people">${team.agents.map(a=>{const b=brief(a),later=a.status==='Coming later';return `
    <article class="person ${later?'later':''}" style="--agent:${esc(a.color)}">
      <button class="person-main" data-team-open="${esc(a.id)}" aria-label="Open ${esc(a.name)}">
        ${teamAvatar(a,'xxl')}
        <span class="person-name">${esc(a.name)}</span><span class="person-role">${esc(a.role)}</span>
        ${later?'<span class="person-badge later">Starts later</span>':b.unknown?'<span class="person-badge later">Not set up</span>':(n=>n?`<span class="person-badge">${n} for you</span>`:'<span class="person-badge clear">All clear</span>')(b.items.filter(i=>i.needs).length)}
        <span class="person-say">${esc(b.say)}</span>
      </button>
      ${TEAM_HOME[a.id]?`<button class="person-go" data-desk-act="view:${TEAM_HOME[a.id]}">Go to ${esc(a.name)}'s desk <i>→</i></button>`:''}
    </article>`}).join('')}</div>
    <section class="learnings"><h2>Lessons learned</h2>
      <p class="hint">What the team has learned about what works. Sam and Laia add to this as they track results, and everyone reads it before they start work.</p>
      ${teamLearnForm()}
      <ul class="learning-list">${team.learnings.length?team.learnings.map(teamLearning).join(''):'<li class="hint">No lessons yet. Add the first one above.</li>'}</ul>
    </section>`;
}

function renderTeamProfile(){
  const a=teamAgent(teamOpen);if(!a){teamOpen=null;return renderTeam()}
  const lines=x=>esc(x.join('\n'));
  const mine=team.learnings.filter(l=>l.agent_id===a.id);
  page(a.name,a.role);subPage('Team',a.name,'data-team-back');
  $('#workspace').innerHTML=`<div class="profile" style="--agent:${esc(a.color)}">
    <header class="profile-hero">
      <label class="photo-pick" title="Change photo">${teamAvatar(a,'xxl')}<input type="file" accept="image/png,image/jpeg,image/webp" data-team-photo hidden><span>Change photo</span></label>
      <div class="profile-name"><input class="name" data-team-field="name" value="${esc(a.name)}" maxlength="60" aria-label="Name">
        <input class="role-input" data-team-field="role" value="${esc(a.role)}" maxlength="120" aria-label="Role">
        ${TEAM_HOME[a.id]?`<button class="subtle" data-desk-act="view:${TEAM_HOME[a.id]}">Go to ${esc(a.name)}'s desk</button>`:''}</div>
    </header>
    <div class="room">
      <section class="room-chat" id="room-chat" aria-label="Chat with ${esc(a.name)}">${roomChat(a)}</section>
      <div class="room-work">
        ${typeof agentDesk==='function'?agentDesk(a.id,{head:false}):''}
        ${roomWork(a)}
      </div>
    </div>
    <details class="how-works"><summary><span>How ${esc(a.name)} works</span><small id="team-save" class="save-note">Saved</small></summary>
      <div class="profile-grid">
        <section>
          <label>Mission<textarea data-team-field="mission" rows="3" maxlength="2000">${esc(a.mission)}</textarea></label>
          <label>Responsibilities <small>one per line</small><textarea data-team-list="responsibilities" rows="6">${lines(a.responsibilities)}</textarea></label>
          <label>Personality and tone<textarea data-team-field="personality" rows="2" maxlength="1000">${esc(a.personality)}</textarea></label>
          <label>Check-ins<input data-team-field="check_in" value="${esc(a.check_in)}" maxlength="500" placeholder="When ${esc(a.name)} messages you, and with what"></label>
        </section>
        <aside>
          <label>Status<select data-team-field="status">${team.statuses.map(s=>`<option ${s===a.status?'selected':''}>${esc(s)}</option>`).join('')}</select></label>
          <label class="color-row">Colour<input type="color" data-team-field="color" value="${esc(a.color)}"></label>
          <h4>Workflows</h4>
          <div class="flow-picks">${Object.entries(team.workflows).map(([k,v])=>`<label class="check"><input type="checkbox" data-team-flow="${esc(k)}" ${a.workflows.includes(k)?'checked':''}>${esc(v)}</label>`).join('')}</div>
          <label>Skills and tools <small>one per line</small><textarea data-team-list="skills" rows="4">${lines(a.skills)}</textarea></label>
        </aside>
      </div>
    </details>
    <section class="learnings"><h2>What ${esc(a.name)} has learned</h2>
      ${teamLearnForm(a.id)}
      <ul class="learning-list">${mine.length?mine.map(teamLearning).join(''):'<li class="hint">Nothing yet.</li>'}</ul>
    </section>
  </div>`;
  const t=$('#room-thread');if(t)t.scrollTop=t.scrollHeight;
  if(!roomLoaded[a.id])roomLoad(a.id);
}

// The room: chat with the team member, and everything they're working on.
function roomChat(a){
  const msgs=roomThread[a.id]||[],busy=roomBusy===a.id,off=roomStatus&&!roomStatus.available,later=a.status==='Coming later';
  const quick=a.id==='sam'?['What are you working on?','How far ahead is the carousel queue?','What is working best right now?','What should we test next?']:['What are you working on?','What do you need from me?'];
  return `<header class="room-head">${teamAvatar(a,'sm')}<div><b>Chat with ${esc(a.name)}</b><small>${later?'Starts later':off?'Offline on this PC':busy?'Thinking…':'Reads your files and training, but can’t change anything from here'}</small></div></header>
    <div class="room-thread" id="room-thread">${!roomLoaded[a.id]?deskSkeletonSafe():msgs.length?msgs.map(roomMessage).join(''):`<div class="sam-empty"><p>Ask ${esc(a.name)} what's in progress, talk through ideas, or give direction.</p></div>`}
      ${busy?'<div class="sam-msg agent typing"><span></span><span></span><span></span></div>':''}</div>
    ${off?`<p class="sam-off">${esc(roomStatus.help)}</p>`:''}
    <div class="room-compose">
      <div class="sam-quick">${quick.map(q=>`<button class="chip-btn" data-room-quick="${esc(q)}" ${off||busy||later?'disabled':''}>${esc(q)}</button>`).join('')}</div>
      <div class="sam-input"><textarea id="room-input" rows="2" placeholder="Message ${esc(a.name)}" ${off||later?'disabled':''}></textarea><button class="primary" data-room-send ${off||busy||later?'disabled':''} aria-label="Send to ${esc(a.name)}">Send</button></div>
    </div>`;
}
function deskSkeletonSafe(){return typeof deskSkeleton==='function'?deskSkeleton(2):''}
function roomMessage(m){
  return `<div class="sam-msg ${m.role==='josh'?'josh':'agent'}"><p>${esc(m.text)}</p></div>`;
}
function roomWork(a){
  const today=new Date().toISOString().slice(0,10);
  const cards=(typeof deskCarouselRows==='function'?deskCarouselRows():[]).filter(c=>c.owner===a.id);
  const upcoming=cards.filter(c=>c.stage!=='Posted').sort((x,y)=>(x.post_date||'9999').localeCompare(y.post_date||'9999'));
  const posted=cards.filter(c=>c.stage==='Posted').sort((x,y)=>(y.post_date||'').localeCompare(x.post_date||'')).slice(0,6);
  const ahead=(()=>{let d=new Date(),n=0;for(;n<60;n++){const k=d.toISOString().slice(0,10);if(!cards.some(c=>c.post_date===k&&['Scheduled','Posted'].includes(c.stage)))break;d.setDate(d.getDate()+1)}return n})();
  const day=k=>k?new Date(k+'T12:00').toLocaleDateString(undefined,{weekday:'short',day:'numeric',month:'short'}):'No date';
  const row=c=>`<button class="room-row" data-desk-act="carousel:${esc(c.id)}"><span class="room-date">${esc(day(c.post_date))}</span><span class="room-title">${esc(c.title)}</span><span class="room-stage">${esc(c.stage)}</span></button>`;
  const flows=a.workflows||[];
  let html='';
  if(flows.includes('carousels')){
    html+=`<section class="room-list"><header><h3>Carousel queue</h3><span class="room-meta">${ahead} day${ahead===1?'':'s'} ahead · aim 10</span></header>
      ${upcoming.length?upcoming.map(row).join(''):'<p class="hint">Nothing in the queue yet.</p>'}
      ${posted.length?`<h4>Recently posted</h4>${posted.map(row).join('')}`:''}</section>`;
  }
  if(flows.includes('youtube')&&typeof state!=='undefined'){
    const vids=state.projects.filter(p=>!p.archived&&p.stage!=='Published').sort((x,y)=>(x.publish_date||'9999').localeCompare(y.publish_date||'9999')).slice(0,12);
    html+=`<section class="room-list"><header><h3>Videos</h3><span class="room-meta">Mon, Wed and Sat</span></header>
      ${vids.length?vids.map(p=>`<button class="room-row" data-desk-act="project:${esc(p.id)}"><span class="room-date">${esc(day(p.publish_date))}</span><span class="room-title">${esc(p.chosen_title||p.name)}</span><span class="room-stage">${esc(p.stage)}</span></button>`).join(''):'<p class="hint">No videos in production.</p>'}</section>`;
  }
  return html;
}
function roomRender(){const a=teamAgent(teamOpen),box=$('#room-chat');if(!a||!box)return;box.innerHTML=roomChat(a);const t=$('#room-thread');if(t)t.scrollTop=t.scrollHeight}
async function roomLoad(id){
  try{
    const [thread,status]=await Promise.all([api(`/api/agents/thread?project=desk&agent=${encodeURIComponent(id)}`),roomStatus?Promise.resolve(roomStatus):api('/api/agents/status')]);
    roomThread[id]=thread;roomStatus=status;roomLoaded[id]=true;if(teamOpen===id)roomRender();
  }catch(e){roomLoaded[id]=true;toast(e.message,true);if(teamOpen===id)roomRender()}
}
async function roomSend(message){
  const a=teamAgent(teamOpen);message=(message||'').trim();if(!a||!message||roomBusy)return;
  const id=a.id;roomThread[id]=[...(roomThread[id]||[]),{id:'pending',role:'josh',text:message}];roomBusy=id;roomRender();
  try{const r=await api('/api/agents/chat',{agent_id:id,message});roomThread[id]=r.messages}
  catch(e){roomThread[id]=(roomThread[id]||[]).filter(m=>m.id!=='pending');toast(e.message,true)}
  finally{roomBusy=null;if(teamOpen===id)roomRender()}
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
  const q=e.target.closest('[data-room-quick]');if(q){roomSend(q.dataset.roomQuick);return}
  if(e.target.closest('[data-room-send]')){const i=$('#room-input');const v=i.value;i.value='';roomSend(v);return}
  const forget=e.target.closest('[data-team-forget]');
  if(forget&&confirm('Remove this lesson?')){try{await api('/api/team/learnings/'+forget.dataset.teamForget,{delete:true});team.learnings=team.learnings.filter(l=>l.id!==forget.dataset.teamForget);render()}catch(err){toast(err.message,true)}}
});
document.addEventListener('keydown',e=>{if(e.target.id==='room-input'&&e.key==='Enter'&&!e.shiftKey){e.preventDefault();const v=e.target.value;e.target.value='';roomSend(v)}});
document.addEventListener('keydown',e=>{const c=e.target.closest?.('[data-team-open]');if(c&&e.key==='Enter'){teamOpen=c.dataset.teamOpen;render()}});
const teamBaseRender=render;render=function(){if(!current&&view==='team')return renderTeam();return teamBaseRender()};
