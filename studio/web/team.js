'use strict';
// Team page: the named agents, their profiles and the shared lessons-learned log.
// Each member has their own page with a chat (run through Claude Code on this PC) and what they're working on.
// Profiles autosave.
const TEAM_HOME={sam:'carousels',tariq:'editing',laia:'email',saskia:'lessons',anouk:'success'};
let team=null,teamOpen=null,teamTimer=null,teamSaving=null;
let roomThread={},roomBusy=null,roomStatus=null,roomLoaded={},roomTab={},roomStarted=0,roomTick=null,roomIdeas=false;

// A sub-page under one of the views: hides the big heading and shows "Parent / Name" crumbs.
function subPage(parent,name,back){
  $('#page-title').parentElement.hidden=true;document.title=name+' · CareerOS';
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

// What each person needs from Josh right now, worked out from the same rules as their own pages.
function teamNeeds(a){
  const week=new Date();week.setHours(12,0,0,0);week.setDate(week.getDate()+6);const wk=week.toISOString().slice(0,10);
  if(a.id==='sam'&&typeof carouselNeeds==='function'){
    const list=(typeof carousels!=='undefined'&&carousels?carousels:deskCarousels)?.items||[];
    return list.filter(c=>!c.archived&&carouselNeeds(c)&&(!c.post_date||c.post_date<=wk)).sort((x,y)=>(x.post_date||'9999').localeCompare(y.post_date||'9999'))
      .map(c=>({text:c.title,act:`data-desk-act="carousel:${esc(c.id)}"`,what:carouselNeeds(c)==='Ready for your review'?'carousel to review':'carousel to design',date:c.post_date}));
  }
  if(a.id==='laia'&&typeof emailStep==='function'&&typeof emailRows!=='undefined'){
    return emailRows.filter(p=>!p.archived&&emailStep(p).key==='approve'&&(!p.planned_date||p.planned_date<=wk)).sort((x,y)=>(x.planned_date||'9999').localeCompare(y.planned_date||'9999'))
      .map(p=>({text:p.subject,act:`data-email-go="${esc(p.id)}"`,what:'email to approve',date:p.planned_date}));
  }
  const b=typeof deskBrief==='function'?deskBrief(a.id):{items:[]};
  return (b.items||[]).filter(i=>i.needs).map(i=>({text:i.text,act:`data-desk-act="${esc(i.act)}"`,what:(i.sub||'').split('·')[0].trim().toLowerCase()||'thing',date:''}));
}
// Everything that needs Josh from one person: their own list, plus any decision their brief flags that the list
// doesn't already cover, so nothing waiting on Josh falls out of both the needs list and the work list.
function teamAllNeeds(a){
  if(a.status==='Coming later')return [];
  const own=teamNeeds(a),b=typeof deskBrief==='function'?deskBrief(a.id):{items:[]};
  const seen=new Set(own.map(n=>(n.act.match(/="([^"]*)"/)||[])[1]));
  const extra=(b.items||[]).filter(i=>i.needs&&!seen.has(esc(i.act))&&!seen.has(i.act)&&!(i.act.startsWith('carousel:')&&own.some(n=>n.act.includes(esc(i.act)))))
    .map(i=>({text:i.text,act:`data-desk-act="${esc(i.act)}"`,what:(i.sub||'').split('·')[0].trim().toLowerCase()||'thing',date:''}));
  return a.id==='sam'||a.id==='laia'?[...own,...extra]:own;
}
function teamCount(needs){
  const groups={};for(const n of needs)groups[n.what]=(groups[n.what]||0)+1;
  return Object.entries(groups).map(([w,n])=>`${n} ${n===1?w:w.replace(/^(\w+)/,'$1s')}`).join(' and ');
}
function teamLast(a){const m=(roomThread[a.id]||[]).filter(x=>x.role==='agent').at(-1);return m?{text:m.text.replace(/\s+/g,' ').slice(0,160),at:m.at}:null}
function teamAgo(at){const s=(Date.now()-new Date(at))/1000;if(s<90)return 'just now';if(s<3600)return Math.round(s/60)+' min ago';if(s<86400)return Math.round(s/3600)+' h ago';return new Date(at).toLocaleDateString(undefined,{day:'numeric',month:'short'})}

// The office scene for a desk: the approved image of the person at their desk, or an honest pending panel with
// their portrait. The Team page uses their profile picture instead.
function teamOffice(a){
  const o=team.offices?.[a.id];
  if(o?.ready)return `<img class="office-img" src="/team-office/${encodeURIComponent(a.id)}?v=${esc(o.version||'')}" alt="${esc(a.name)} at their desk, turned to talk to you">`;
  return `<div class="office-pending" role="img" aria-label="${esc(a.name)}'s office image is not added yet">${teamAvatar(a,'xxl')}
    <small>${o?.approved?'Office image approved, waiting to be added':'No office image yet'}</small></div>`;
}
// A big profile picture for the Team page: their photo, or their initial on their colour.
function teamPortrait(a){
  return a.photo?`<img class="tc-photo" src="/assets/${esc(a.photo)}?w=640" alt="">`
    :`<span class="tc-photo none" aria-hidden="true">${esc((a.name||'?').trim().slice(0,1).toUpperCase())}</span>`;
}
// The short line under each person: what needs Josh first, or what they're on, from their real brief.
function teamSummary(a,needs,asked){
  if(a.status==='Coming later')return `<p class="tc-say">Starts later.</p>`;
  if(asked)return `<p class="tc-say ask"><span class="tc-dot" aria-hidden="true"></span>Has a question for you</p>`;
  if(needs.length)return `<p class="tc-say needs"><span class="tc-dot" aria-hidden="true"></span>Needs you for ${esc(teamCount(needs))}</p>
    <div class="tc-items">${needs.slice(0,2).map(n=>`<button class="tc-item" ${n.act}><span>${esc(n.text)}</span>${n.date?`<small>${esc(new Date(n.date+'T12:00').toLocaleDateString(undefined,{weekday:'short',day:'numeric'}))}</small>`:''}</button>`).join('')}
    ${needs.length>2?`<button class="tc-item more" data-team-open="${esc(a.id)}">${needs.length-2} more on ${esc(a.name)}'s desk</button>`:''}</div>`;
  const b=typeof deskBrief==='function'?deskBrief(a.id):null;
  return `<p class="tc-say">${esc(b?.say||'Nothing needs you right now.')}</p>`;
}
async function renderTeam(){
  page('Team','');$('#page-title').parentElement.hidden=true;
  if(!team){$('#workspace').innerHTML=`<div class="people" aria-label="Getting the team's notes">${typeof deskSkeleton==='function'?deskSkeleton(4):''}</div>`;try{team=await api('/api/team')}catch(e){$('#workspace').innerHTML=`<div class="empty"><h2>Could not load the team.</h2><p>${esc(e.message)}</p></div>`;return}
    if(view!=='team'||current)return}
  if(teamOpen)return renderTeamProfile();
  api('/api/notices').then(v=>{teamNotices=v;const box=$('#tm-notices');if(box)box.innerHTML=teamNoticesBar()}).catch(()=>{});
  if(typeof deskData==='function'){await deskData();if(view!=='team'||current||teamOpen)return}
  const missing=team.agents.filter(a=>a.status!=='Coming later'&&!roomLoaded[a.id]);
  if(missing.length){await Promise.all(missing.map(a=>api(`/api/agents/thread?project=desk&agent=${encodeURIComponent(a.id)}`).then(t=>{roomThread[a.id]=t;roomLoaded[a.id]=true}).catch(()=>{})));if(view!=='team'||current||teamOpen)return}
  const rows=team.agents.map(a=>({a,later:a.status==='Coming later',needs:teamAllNeeds(a),last:teamLast(a),asked:typeof dockUnread!=='undefined'&&dockUnread[a.id]}));
  const active=rows.filter(r=>!r.later).sort((x,y)=>(y.asked-x.asked)||(y.needs.length-x.needs.length)),later=rows.filter(r=>r.later);
  const total=active.reduce((n,r)=>n+r.needs.length,0),who=active.filter(r=>r.needs.length||r.asked);
  const lead=total?`${total} thing${total===1?'':'s'} need${total===1?'s':''} you this week, across ${who.map(r=>r.a.name).join(' and ').replace(/ and (?=.* and )/g,', ')}.`:'Nothing needs you right now.';
  // The approved group picture of the whole team, shown whole at full width (never cropped), above the roster.
  const group=team.group?.ready?`<figure class="tm-group"><img src="/team-group?v=${esc(team.group.version||'')}" width="1983" height="793" alt="Josh's team of six together at a shared table in the office" decoding="async"></figure>`:'';
  $('#workspace').innerHTML=`<div class="tm">${group}
    <header class="tm-head"><h1>Team</h1><p>${esc(lead)} Open anyone to go to their desk.</p></header>
    <div id="tm-notices">${teamNoticesBar()}</div>
    <div class="tc-grid">${[...active,...later].map(({a,needs,asked,later,last})=>`
      <article class="tc ${needs.length||asked?'wants':''} ${later?'later':''}" style="--agent:${esc(a.color)}">
        <button class="tc-open" data-team-open="${esc(a.id)}" aria-label="Go to ${esc(a.name)}'s desk">
          <span class="tc-portrait">${teamPortrait(a)}</span>
          <span class="tc-who"><b>${esc(a.name)}</b><small>${esc(a.role)}</small></span>
          <span class="tc-go" aria-hidden="true">Go to desk ›</span>
        </button>
        <div class="tc-body">${teamSummary(a,needs,asked)}
          ${later?'':last?`<p class="tc-last"><q>${esc(last.text)}${last.text.length>=160?'…':''}</q><small>${esc(teamAgo(last.at))}</small></p>`:`<p class="tc-last none">No messages yet.</p>`}</div>
      </article>`).join('')}</div>
  </div>`;
}

// What each person's work looks like in numbers, counted from CareerOS's own records. Nothing is estimated, and a
// number only shows where its source is there.
function deskStats(a){
  const ps=(typeof state!=='undefined'?state.projects:[]).filter(p=>!p.archived),since=new Date(Date.now()-30*864e5).toISOString().slice(0,10);
  const n=(label,value,sub,act)=>({label,value,sub,act});
  if(a.id==='sam'){
    const cs=typeof deskCarouselRows==='function'?deskCarouselRows():[];
    return [n('Carousels to review',cs.filter(c=>c.stage==='Ready for review').length,'Ready for your yes','view:carousels'),
      n('Carousels scheduled',cs.filter(c=>['Approved','Scheduled'].includes(c.stage)).length,'Approved or scheduled','view:carousels'),
      n('Carousels posted',cs.filter(c=>c.stage==='Posted'&&(c.post_date||'')>=since).length,'In the last 30 days','view:carousels'),
      n('Videos in production',ps.filter(p=>!['Idea','Published'].includes(p.stage)).length,'Scripting to scheduled','view:board'),
      n('Videos published',ps.filter(p=>p.stage==='Published'&&(p.publish_date||'')>=since).length,'In the last 30 days','view:board')];
  }
  if(a.id==='tariq'){
    const jobs=(typeof state!=='undefined'?state.jobs||[]:[]).filter(j=>['running','starting'].includes(j.status?.state));
    return [n('Ready for you to watch',ps.filter(p=>p.stage==='Review').length,'Edits in review','view:editing'),
      n('In the edit',ps.filter(p=>p.stage==='Editing').length,'Being cut','view:editing'),
      n('Waiting for the edit',ps.filter(p=>p.stage==='Recorded').length,'Recorded','view:editing'),
      n('Jobs running',jobs.length,'On this PC right now','view:editing')];
  }
  if(a.id==='laia'){
    const es=typeof deskEmailRows==='function'?deskEmailRows():[];
    const queued=es.filter(e=>['Ready','Scheduled','Published'].includes(e.status)&&e.planned_date&&deskDays(e.planned_date)>=0);
    const last=queued.map(e=>e.planned_date).sort().pop();
    return [{...n('Days ahead',last?deskDays(last):0,'The goal is 14','view:email'),goal:14},
      n('Drafts',es.filter(e=>e.status==='Draft').length,'Waiting for your review','view:email'),
      n('Ready or scheduled',es.filter(e=>['Ready','Scheduled'].includes(e.status)).length,'Set to go out','view:email'),
      n('Sent',es.filter(e=>['Published','Sent'].includes(e.status)&&(e.planned_date||'')>=since).length,'In the last 30 days','view:email')];
  }
  if(a.id==='saskia'){
    if(typeof deskLessons==='undefined'||!deskLessons||deskLessons.missing)return [{label:'Lessons',unavailable:'The lesson catalogue isn\'t set up on this PC yet.',act:'view:lessons'}];
    const ls=deskLessons.lessons||[],done=l=>!!(l.recording?.preferred_take||l.recording?.takes?.length),todo=ls.filter(l=>!done(l));
    return [n('Lessons recorded',`${ls.length-todo.length} of ${ls.length}`,'Across the courses','view:lessons'),
      n('Planned to record',todo.filter(l=>l.recording?.planned_date).length,'With a date','view:lessons'),
      n('No recording date',todo.filter(l=>!l.recording?.planned_date).length,'Still to plan','view:lessons')];
  }
  if(a.id==='anouk'&&typeof ccDeskStats==='function')return ccDeskStats();
  if(a.id==='anouk'){
    if(typeof people==='undefined'||!people)return [{label:'Customers',unavailable:'The customer tracker hasn\'t loaded.',act:'view:success'}];
    const live=people.items.filter(p=>!p.archived),today=typeof pToday==='function'?pToday():new Date().toISOString().slice(0,10);
    return [n('People looked after',live.length,'In the tracker','view:success'),
      n('Waiting for a reply',live.filter(p=>p.stage==='New reply').length,'Replied to a newsletter','view:success'),
      n('Check-ins overdue',live.filter(p=>p.next_due&&p.next_due<today).length,'Past their next step','view:success'),
      n('Promises open',live.reduce((k,p)=>k+(p.promises||[]).filter(x=>!x.done).length,0),'Things you said you would do','view:success')];
  }
  if(a.id==='max')return [{label:'Business numbers',unavailable:'Max isn\'t running yet. The live business numbers are on the Scorecard.',act:'view:scorecard',link:'Open the Scorecard'}];
  return [];
}
function deskStatTiles(a){
  const st=deskStats(a);
  if(!st.length)return '';
  return `<div class="hub-stats">${st.map(x=>x.unavailable
    ?`<button class="hub-stat na" data-desk-act="${esc(x.act)}"><span class="hub-stat-label">${esc(x.label)}</span><span class="hub-stat-na">${esc(x.unavailable)}</span>${x.link?`<span class="hub-stat-link">${esc(x.link)} ›</span>`:''}</button>`
    :`<button class="hub-stat" data-desk-act="${esc(x.act)}"><span class="hub-stat-label">${esc(x.label)}</span><span class="hub-stat-val">${esc(x.value)}</span>${x.goal?`<span class="hub-meter" aria-hidden="true"><i style="width:${Math.min(100,Math.round((+x.value||0)/x.goal*100))}%"></i></span>`:''}<span class="hub-stat-sub">${esc(x.sub)}</span></button>`).join('')}</div>`;
}
// Beside the chat: what needs Josh, then what's in progress, each opening the real item.
function deskFocus(a){
  if(a.status==='Coming later')return `<section class="hub-card"><header><h3>${esc(a.name)} starts later</h3></header><p class="hint">Nothing is assigned yet.</p></section>`;
  if(a.id==='anouk'&&typeof ccDeskFocus==='function')return ccDeskFocus();
  const needs=teamAllNeeds(a),b=typeof deskBrief==='function'?deskBrief(a.id):{items:[],say:''};
  const work=(b.items||[]).filter(i=>!i.needs);
  return `<section class="hub-card needs"><header><h3>${needs.length?`Needs you for ${esc(teamCount(needs))}`:'Nothing needs you right now'}</h3>${needs.length?`<span class="hub-count">${needs.length}</span>`:''}</header>
      ${needs.length?needs.map(n=>`<button class="hub-row" ${n.act}><span class="hub-row-text">${esc(n.text)}</span><small>${n.date?esc(new Date(n.date+'T12:00').toLocaleDateString(undefined,{weekday:'short',day:'numeric',month:'short'})):esc(n.what)}</small><i aria-hidden="true">›</i></button>`).join(''):`<p class="hub-say">${esc(b.say||'')}</p>`}
    </section>
    <section class="hub-card"><header><h3>In progress</h3>${work.length?`<span class="hub-meta">${work.length}</span>`:''}</header>
      ${work.length?`<div class="desk-items">${work.slice(0,6).map(deskItem).join('')}</div>${work.length>6&&TEAM_HOME[a.id]?`<button class="desk-more" data-desk-act="view:${esc(TEAM_HOME[a.id])}">See all ${work.length}</button>`:''}`
        :`<p class="hint">${b.unknown?esc(b.say):'Nothing else in progress right now.'}</p>`}
    </section>`;
}

function renderTeamProfile(){
  const a=teamAgent(teamOpen);if(!a){teamOpen=null;return renderTeam()}
  const lines=x=>esc(x.join('\n'));
  const mine=team.learnings.filter(l=>l.agent_id===a.id);
  page(a.name,a.role);subPage('Team',a.name,'data-team-back');
  const more=roomWork(a);
  $('#workspace').innerHTML=`<div class="profile hub hub-${esc(a.id)}" style="--agent:${esc(a.color)}">
    <section class="hub-hero">
      <div class="hub-office">${teamOffice(a)}</div>
      ${HUB_ROOM[a.id]?`<span class="hub-room">${esc(HUB_ROOM[a.id])}</span>`:''}
      ${HUB_FUN[a.id]?`<div class="hub-fun-box"><p class="hub-fun" id="hub-fun" hidden><span id="hub-fun-text"></span><button class="hub-fun-x" data-hub-fun-hide aria-label="Hide this note">×</button></p></div>`:''}
      <header class="hub-id">
        ${teamAvatar(a,'md')}
        <div class="room-who"><h1>${esc(a.name)}</h1><span>${esc(a.role)}</span></div>
        ${TEAM_HOME[a.id]?`<button class="subtle" data-desk-act="view:${TEAM_HOME[a.id]}">Open ${esc({success:'Customer center'}[TEAM_HOME[a.id]]||(team.workflows||{})[TEAM_HOME[a.id]]||TEAM_HOME[a.id])}</button>`:''}
      </header>
    </section>
    <div class="room hub-main">
      <section class="room-chat" id="room-chat" aria-label="Chat with ${esc(a.name)}">${roomChat(a)}</section>
      <div class="room-work hub-focus" id="room-focus">${deskFocus(a)}</div>
    </div>
    <section class="hub-more" aria-label="${esc(a.name)}'s ongoing work">
      <h2><span>${esc(HUB_ROOM[a.id]||a.name)}</span> Ongoing work and numbers</h2>
      ${deskStatTiles(a)}
      ${more?`<div class="room-work" id="room-work">${more}</div>`:''}
    </section>
    <details class="how-works"><summary><span>Profile and how ${esc(a.name)} works</span><small id="team-save" class="save-note">Saved</small></summary>
      <div class="profile-edit">
        <label class="photo-pick" title="Change photo">${teamAvatar(a,'xl')}<input type="file" accept="image/png,image/jpeg,image/webp" data-team-photo hidden><span>Change photo</span></label>
        <label>Name<input data-team-field="name" value="${esc(a.name)}" maxlength="60"></label>
        <label>Role<input data-team-field="role" value="${esc(a.role)}" maxlength="120"></label>
      </div>
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
          ${HUB_FUN[a.id]?`<label class="check"><input type="checkbox" data-hub-fun-pref ${hubFunOff()?'':'checked'}>Show made-up fun messages on the desks now and then</label>`:''}
        </aside>
      </div>
    </details>
    <details class="tm-lessons"><summary>What ${esc(a.name)} has learned <span>${mine.length}</span></summary>
      <p class="hint">${esc(a.name)} saves these from your edits, your answers and the results, and reads them before every job.</p>
      <ul class="learning-list">${mine.length?mine.map(teamLearning).join(''):'<li class="hint">Nothing yet.</li>'}</ul>
      <details class="tm-add"><summary>Add a lesson yourself</summary>${teamLearnForm(a.id)}</details>
    </details>
  </div>`;
  const t=$('#room-thread');if(t)t.scrollTop=t.scrollHeight;
  if(!roomLoaded[a.id])roomLoad(a.id);
  roomCarouselSync();
  hubStart(a);
  roomFrameioSync();
}

// Each desk's own room name. It labels the space and changes nothing else.
const HUB_ROOM={sam:"Sam's studio",tariq:"Tariq's edit bay",laia:"Laia's planning room",saskia:"Saskia's workshop",anouk:"Anouk's corner café",max:"Max's office"};
// Made-up notes about each person's day, shown now and then on their desk. They never say anything about real work,
// which lives in the needs and work lists. Each person has their own lines, plus shared ones built from the parts
// below, so there are dozens per desk. They are picked at random, and the last HUB_FUN_SKIP shown on a desk are
// skipped, so the same note doesn't come round again soon (changed 11 Nov 2026, Josh asked for no repeats).
const HUB_FUN={
  sam:['Sam rearranged the plants on the shelf again.','Sam found a camera lens that went missing in March.','Sam had a poke bowl for lunch.','Sam waved at the camera. Old habit.','Sam is testing which mug looks best on camera.','Sam reorganised the cable drawer by colour.','Sam tried a new pose for a thumbnail and pulled a muscle.','Sam is wearing the lucky hoodie today.'],
  tariq:['Tariq had a falafel wrap for lunch.','Tariq cleaned the keyboard. Twice.','Tariq is humming a film score.','Tariq named a test folder FINAL_final_v3 as a joke.','Tariq is choosing between two greys for a new desk mat.','Tariq renamed every track in a project just because.','Tariq is watching a two-hour video about one transition.','Tariq dimmed the lights for "colour accuracy".'],
  laia:['Laia colour-coded the notebook again.','Laia wrote a to-do list for the to-do lists.','Laia had soup for lunch and gave it eight out of ten.','Laia moved the desk lamp two centimetres to the left.','Laia is reading a paperback with three bookmarks in it.','Laia found the perfect word and did a small fist pump.','Laia rewrote a shopping list so it reads better.'],
  saskia:['Saskia watered the desk plants.','Saskia lined the sticky notes up in a neat grid.','Saskia had a big salad for lunch.','Saskia found a nicer pen pot.','Saskia is doodling a diagram in the margin.','Saskia drew a flowchart of what to cook tonight.','Saskia has three highlighters going at once.','Saskia labelled the drawer that holds the labels.'],
  anouk:['Anouk made a flat white with a little heart on top.','Anouk is thinking about cats again.','Anouk switched the desk lamp on. It\'s cosy o\'clock.','Anouk shared a biscuit with the office plant. The plant declined.','Anouk set a cat photo as the phone background.','Anouk is knitting a scarf that is now longer than the desk.','Anouk brought pastries in for everyone.'],
  max:['Max ironed a shirt that was already ironed.','Max is reading the financial pages with a pen in hand.','Max lined the pens up by length.','Max made a spreadsheet of his favourite coffees.','Max is doing a crossword in ink.','Max straightened the picture on the wall. It was straight.','Max had a steak salad for lunch.']
};
const HUB_FUN_SKIP=30;
const HUB_FUN_PARTS={
  lunch:['a toastie','leftover curry','sushi','a burrito','soup','a halloumi wrap','pasta salad','a bagel','dumplings','a jacket potato','noodles','a club sandwich','shawarma','a grain bowl'],
  drink:['a flat white','green tea','an oat latte','a chai','a cold brew','sparkling water with lime','peppermint tea','an espresso'],
  music:['lo-fi beats','an old jazz record','a film score','90s pop','a podcast about space','rain sounds','a Spanish guitar playlist','bossa nova'],
  small:['tidied the desk drawer','found a pen that works first time','reorganised the bookshelf','swapped the desk plant to the sunny side','opened the window for some fresh air','fixed the wobbly chair','sorted the post-its by colour','did ten minutes of stretching','cleaned the screen','refilled the water bottle']
};
const HUB_FUN_TEMPLATES=[n=>HUB_FUN_PARTS.lunch.map(x=>`${n} had ${x} for lunch.`),n=>HUB_FUN_PARTS.drink.map(x=>`${n} is drinking ${x}.`),
  n=>HUB_FUN_PARTS.music.map(x=>`${n} is listening to ${x}.`),n=>HUB_FUN_PARTS.small.map(x=>`${n} ${x}.`)];
function hubFunPool(a){
  const n=a.name||a.id;return [...(HUB_FUN[a.id]||[]),...HUB_FUN_TEMPLATES.flatMap(t=>t(n))];
}
function hubFunSeen(id){try{return JSON.parse(localStorage.getItem('hub-fun-seen-'+id)||'[]')}catch{return []}}
function hubFunPick(run){
  const seen=hubFunSeen(run.id),fresh=run.lines.filter(l=>!seen.includes(l)),from=fresh.length?fresh:run.lines;
  const line=from[Math.floor(Math.random()*from.length)];
  try{localStorage.setItem('hub-fun-seen-'+run.id,JSON.stringify([...seen.filter(l=>l!==line),line].slice(-HUB_FUN_SKIP)))}catch{}
  return line;
}
let hubRun=null;
function hubFunOff(){try{return localStorage.getItem('hub-fun-off')==='1'}catch{return false}}
function hubReduced(){return !!window.matchMedia?.('(prefers-reduced-motion: reduce)').matches}
// Starts the scene's parallax and the fun notes for the desk on screen. Everything stops when that desk leaves the
// page, whichever screen replaces it.
function hubStart(a){
  hubStop();
  const el=$('.hub');if(!el)return;
  const run=hubRun={id:a.id,el,timer:null,hideTimer:null,frame:0,lines:hubFunPool(a)};
  const img=el.querySelector('.office-img'),office=el.querySelector('.hub-office');
  if(img&&office&&!hubReduced()&&!matchMedia('(max-width:760px)').matches){
    // Whole pixels and no zoom: a fractional shift or a 106% scale made the browser resample the picture every frame,
    // which left the desk scene soft (fixed 11 Nov 2026). The scene moves at 40% of the scroll, so the work slides up
    // over it, and fades out over the scene's own height (13 Nov 2026).
    run.scroll=()=>{if(run.frame)return;run.frame=requestAnimationFrame(()=>{run.frame=0;const h=office.offsetHeight||500,y=Math.max(0,Math.min(scrollY,h));
      img.style.transform=`translate3d(0,${Math.round(y*.4)}px,0)`;office.style.opacity=String(Math.max(0,1-y/(h*.9)).toFixed(3))})};
    addEventListener('scroll',run.scroll,{passive:true});run.scroll();
  }
  run.watch=new MutationObserver(()=>{if(!run.el.isConnected)hubStop()});
  run.watch.observe($('#workspace'),{childList:true});
  if(run.lines.length&&!hubFunOff())hubFunNext(run,6000);
}
function hubFunNext(run,wait){
  clearTimeout(run.timer);
  run.timer=setTimeout(()=>{
    if(hubRun!==run||hubFunOff())return;
    const box=$('#hub-fun'),t=$('#hub-fun-text');if(!box||!t)return;
    t.textContent=hubFunPick(run);box.hidden=false;requestAnimationFrame(()=>box.classList.add('on'));
    clearTimeout(run.hideTimer);run.hideTimer=setTimeout(()=>hubFunHide(run),7000);
    hubFunNext(run,7000+30000+Math.round(Math.random()*20000));
  },wait);
}
function hubFunHide(run){
  const box=$('#hub-fun');if(!box||hubRun!==run)return;
  box.classList.remove('on');setTimeout(()=>{if(!box.classList.contains('on'))box.hidden=true},hubReduced()?0:250);
}
function hubStop(){
  const run=hubRun;if(!run)return;hubRun=null;
  clearTimeout(run.timer);clearTimeout(run.hideTimer);if(run.frame)cancelAnimationFrame(run.frame);
  if(run.scroll)removeEventListener('scroll',run.scroll);run.watch?.disconnect();
}
document.addEventListener('click',e=>{
  if(e.target.closest('[data-hub-fun-hide]')){if(hubRun)hubFunHide(hubRun);return}
});
// The opt-out lives with the other profile settings. It is a preference for this browser, the same on every desk.
document.addEventListener('change',e=>{
  const t=e.target.closest?.('[data-hub-fun-pref]');if(!t)return;
  const off=!t.checked;try{localStorage.setItem('hub-fun-off',off?'1':'0')}catch{}
  if(off){if(hubRun){clearTimeout(hubRun.timer);hubFunHide(hubRun)}}else if(hubRun)hubFunNext(hubRun,800);
});

// The room: chat with the team member, and everything they're working on.
function roomQuick(a){return a.id==='sam'?['What are you working on?','How far ahead is the carousel queue?','What is working best right now?','What should we test next?']:['What are you working on?','What do you need from me?']}
function roomElapsed(){const t=Math.max(0,Math.round((Date.now()-roomStarted)/1000));return `${Math.floor(t/60)}:${String(t%60).padStart(2,'0')}`}
function roomChat(a){
  const msgs=roomThread[a.id]||[],busy=roomBusy===a.id,off=roomStatus&&!roomStatus.available,later=a.status==='Coming later';
  const can={sam:'Reads your files and training, makes and changes carousels, and uses Chrome to post and check stats',laia:'Reads Kit and your files, and writes, edits and re-plans emails',saskia:'Reads your files and training, adds and changes lessons, and starts drafts, slides, guides and thumbnails'}[a.id]||'Reads your files and training, but can’t change anything from here';
  const st=later?['later','Starts later']:off?['off','Offline']:busy?['busy','Working']:['ready','Ready'];
  const quick=roomQuick(a),locked=off||later;
  const empty=`<div class="room-empty"><p>Ask ${esc(a.name)} what's in progress, talk through ideas, or give direction.</p>
      <div class="room-starters">${quick.map(q=>`<button class="room-starter" data-room-quick="${esc(q)}" ${locked||busy?'disabled':''}>${esc(q)}</button>`).join('')}</div></div>`;
  return `<header class="room-head"><div><b>Chat with ${esc(a.name)}</b><small>${esc(can)}</small></div><span class="room-state ${st[0]}">${st[1]}</span></header>
    <div class="room-thread" id="room-thread" aria-live="polite">${!roomLoaded[a.id]?deskSkeletonSafe():msgs.length?roomMessages(a,msgs):empty}
      ${a.id==='sam'?'<div id="room-carousel-progress" aria-live="polite"></div>':''}${busy?`<div class="room-working"><span class="dots"><i></i><i></i><i></i></span><span>${esc(a.name)} is working <b id="room-clock">${roomElapsed()}</b></span><small>${a.id==='sam'?'Anything in Chrome can take a minute or two.':''}</small></div>`:''}</div>
    ${off?`<p class="sam-off">${esc(roomStatus.help)}</p>`:''}
    <div class="room-compose">
      ${msgs.length&&!locked?`<div class="room-chips">${quick.map(q=>`<button class="chip-btn" data-room-quick="${esc(q)}" ${busy?'disabled':''}>${esc(q)}</button>`).join('')}</div>`:''}
      ${chatAttachStrip('room')}
      <div class="sam-input">${chatAttachButton('room',locked)}<textarea id="room-input" rows="1" placeholder="Message ${esc(a.name)}" ${locked?'disabled':''}></textarea><button class="primary" data-room-send ${locked||busy?'disabled':''} aria-label="Send to ${esc(a.name)}">Send</button></div>
      <small class="room-hint">Enter sends. Shift and Enter adds a new line. Paste or drop images to attach them.</small>
    </div>`;
}
function deskSkeletonSafe(){return typeof deskSkeleton==='function'?deskSkeleton(2):''}
// Light formatting for replies: paragraphs, "- " lists, **bold** and links.
function roomFormat(text){
  const inline=t=>esc(t).replace(/\*\*(.+?)\*\*/g,'<b>$1</b>').replace(/(https?:\/\/[^\s<]+[^\s<.,;:)\]])/g,'<a href="$1" target="_blank" rel="noopener">$1</a>');
  return String(text||'').split(/\n{2,}/).map(block=>{
    const lines=block.split('\n');
    if(lines.every(l=>/^\s*([-*•]|\d+[.)])\s+/.test(l))){const ol=/^\s*\d/.test(lines[0]);return `<${ol?'ol':'ul'}>${lines.map(l=>`<li>${inline(l.replace(/^\s*([-*•]|\d+[.)])\s+/,''))}</li>`).join('')}</${ol?'ol':'ul'}>`}
    const head=[],items=[];let inList=false;
    for(const l of lines){if(/^\s*([-*•]|\d+[.)])\s+/.test(l)){inList=true;items.push(l)}else if(!inList)head.push(l);else items.push(l)}
    if(head.length&&items.length&&items.every(l=>/^\s*([-*•]|\d+[.)])\s+/.test(l)))return `<p>${head.map(inline).join('<br>')}</p><ul>${items.map(l=>`<li>${inline(l.replace(/^\s*([-*•]|\d+[.)])\s+/,''))}</li>`).join('')}</ul>`;
    return `<p>${lines.map(inline).join('<br>')}</p>`;
  }).join('');
}
function roomTime(at){if(!at)return '';const d=new Date(at);if(isNaN(d))return '';return d.toLocaleTimeString(undefined,{hour:'numeric',minute:'2-digit'})}
function roomLocalAsset(x){
  if(x.action==='failed')return `<span class="room-saved-item bad">Couldn't create the visual. ${esc(x.error||'')}</span>`;
  if(!/^[0-9a-f]{32}_[0-9a-f]{32}$/.test(x.id||'')||!['sam','tariq','laia','saskia','anouk','max'].includes(x.owner))return '';
  const url='/api/local-assets/'+x.owner+'/'+x.id+'.png';
  return `<figure class="room-saved-item" style="display:block;margin:0"><a href="${url}" target="_blank" rel="noopener"><img src="${url}" alt="${esc(x.alt||'Local visual')}" loading="lazy" style="max-width:100%;height:auto;border-radius:8px"></a><figcaption>${esc(x.title||'Local visual')} · <a href="${url}" download="visual-${x.id}.png">Download PNG</a></figcaption></figure>`;
}
function roomSavedItem(x){if(x.kind==='carousel'&&x.job)return roomCarouselCard(x.job);if(x.kind==='frameio')return roomFrameioCard(x);if(x.kind==='local_asset')return roomLocalAsset(x);if(x.kind==='thumbnail'){if(x.action==='failed')return `<span class="room-saved-item bad">Couldn't start the thumbnails. ${esc(x.error||'')}</span>`;return `<button class="room-saved-item" data-desk-act="project:${esc(x.id)}">${x.action==='started'?'Making thumbnails for':`Made ${x.count||''} thumbnail${x.count===1?'':'s'} for`} ${esc(x.title)} <i>›</i></button>`}if(x.kind==='lesson'){if(x.action==='failed')return `<span class="room-saved-item bad">Couldn't change ${esc(x.title||'that lesson')}. ${esc(x.error||'')}</span>`;const what={added:'Added',updated:'Updated',moved:`Moved to ${x.stage||'a new stage'}:`,started:`Started ${({prepare:'preparing',draft:'drafting',slides:'the slides for',guide:'the guide for',thumbnail:'the thumbnail for'})[x.task]||'work on'}`}[x.action]||'Updated';return `<button class="room-saved-item" data-lesson-go="${esc(x.id)}">${esc(what)} ${esc(x.title)} <i>›</i></button>`}if(x.kind==='project')return x.action==='failed'?`<span class="room-saved-item bad">Couldn't change ${esc(x.title||'that video')}. ${esc(x.error||'')}</span>`:`<button class="room-saved-item" data-desk-act="project-tab:brief:${esc(x.id)}">Updated ${esc(x.title)} <i>›</i></button>`;if(x.kind==='video')return x.action==='failed'?`<span class="room-saved-item bad">Couldn't save the results for ${esc(x.title||'that video')}. ${esc(x.error||'')}</span>`:`<button class="room-saved-item" data-desk-act="project-tab:results:${esc(x.id)}">Saved the results for ${esc(x.title)} <i>›</i></button>${x.note?`<span class="room-saved-item bad">${esc(x.note)}</span>`:''}`;if(x.kind==='edit')return x.action==='failed'?`<span class="room-saved-item bad">Couldn't update ${esc(x.title)}. ${esc(x.error||'')}</span>`:`<button class="room-saved-item" data-er-open="${esc(x.id)}">${x.action==='started'?'Changing':'Updated'} ${esc(x.title)} <i>›</i></button>`;if(x.kind==='reply')return x.action==='failed'?`<span class="room-saved-item bad">Couldn't change ${esc(x.title)}. ${esc(x.error||'')}</span>`:`<button class="room-saved-item" data-desk-act="cc:replies">Updated the ${esc(x.title)} in CareerOS <i>›</i></button>`;if(x.kind==='library')return `<span class="room-saved-item">${x.action==='added'?'Saved to the library':x.action==='already saved'?'Already in the library':'Couldn’t save'}: ${esc(x.title)}${x.error?'. '+esc(x.error):''}</span>`;return x.action==='failed'?`<span class="room-saved-item bad">Couldn't save ${esc(x.title||'a carousel')}. ${esc(x.error||'')}</span>`:`<button class="room-saved-item" ${x.kind==='inspo'?`data-inspo-open="${esc(x.id)}"`:x.kind==='email'?`data-email-go="${esc(x.id)}"`:`data-desk-act="carousel:${esc(x.id)}"`}>${x.action==='added'?'Added':x.action==='designed'?'Made the design for':'Updated'} ${x.kind==='inspo'?'creator post ':x.kind==='email'?'email ':''}${esc(x.title)} <i>›</i></button>`}
function roomSaved(list){
  const items=list.map(roomSavedItem).join('');
  if(list.length<=3)return `<div class="room-saved">${items}</div>`;
  const n=a=>list.filter(x=>x.action===a).length,parts=[n('added')&&`added ${n('added')}`,n('updated')&&`updated ${n('updated')}`,n('failed')&&`${n('failed')} didn't save`].filter(Boolean).join(', ');
  return `<details class="room-saved"><summary>Saved changes: ${esc(parts)}</summary>${items}</details>`;
}
function roomMessages(a,msgs){
  let last='';
  return msgs.map(m=>{
    const d=m.at?new Date(m.at):null,day=d&&!isNaN(d)?d.toDateString():'';
    const sep=day&&day!==last?`<div class="room-day">${esc(day===new Date().toDateString()?'Today':d.toLocaleDateString(undefined,{weekday:'long',day:'numeric',month:'long'}))}</div>`:'';if(day)last=day;
    const who=m.role==='josh'?'josh':'agent';
    const meta=m.failed?`<span class="room-failed">Didn't go through. ${esc(m.failed)} <button class="link" data-room-retry="${esc(m.id)}">Try again</button></span>`:m.id==='pending'?'<span>Sent</span>':`<span>${esc(m.role==='josh'?'You':a.name)}${m.at?' · '+esc(roomTime(m.at)):''}</span>`;
    return `${sep}<div class="room-msg ${who}${m.failed?' failed':''}"><div class="room-bubble">${chatImagesHtml(m.images)}${who==='agent'?roomFormat(m.text):m.text?`<p>${esc(m.text).replace(/\n/g,'<br>')}</p>`:''}</div>${m.saved?.length?roomSaved(m.saved):''}${m.learned?.length?m.learned.map(l=>`<div class="room-learned">Added to lessons: ${esc(l)}</div>`).join(''):''}<div class="room-meta-line">${meta}</div></div>`;
  }).join('');
}
function roomWork(a){
  const cards=(typeof deskCarouselRows==='function'?deskCarouselRows():[]).filter(c=>c.owner===a.id);
  const upcoming=cards.filter(c=>c.stage!=='Posted').sort((x,y)=>(x.post_date||'9999').localeCompare(y.post_date||'9999'));
  const posted=cards.filter(c=>c.stage==='Posted').sort((x,y)=>(y.post_date||'').localeCompare(x.post_date||'')).slice(0,6);
  const ahead=(()=>{let d=new Date(),n=0;for(;n<60;n++){const k=d.toISOString().slice(0,10);if(!cards.some(c=>c.post_date===k&&['Scheduled','Posted'].includes(c.stage)))break;d.setDate(d.getDate()+1)}return n})();
  const day=k=>k?new Date(k+'T12:00').toLocaleDateString(undefined,{weekday:'short',day:'numeric',month:'short'}):'No date';
  const row=(act,date,title,stage)=>`<button class="room-row" data-desk-act="${esc(act)}"><span class="room-date">${esc(day(date))}</span><span class="room-title" title="${esc(title)}">${esc(title)}</span><span class="room-stage">${esc(stage)}</span><i aria-hidden="true">›</i></button>`;
  const flows=a.workflows||[],tabs=[];
  if(flows.includes('carousels'))tabs.push(['carousels','Carousels',upcoming.length,`<section class="room-list"><header><h3>Queue</h3><span class="room-meta">${ahead} day${ahead===1?'':'s'} ahead, aiming for 10</span></header>
      ${upcoming.length?upcoming.map(c=>row('carousel:'+c.id,c.post_date,c.title,c.stage)).join(''):'<p class="hint">Nothing in the queue yet.</p>'}
      ${posted.length?`<h4>Recently posted</h4>${posted.map(c=>row('carousel:'+c.id,c.post_date,c.title,c.stage)).join('')}`:''}</section>`]);
  if(flows.includes('youtube')&&typeof state!=='undefined'){
    const title=p=>(p.titles||[]).find(t=>t.id===p.chosen_title)?.text||p.name;
    const live=state.projects.filter(p=>!p.archived&&p.stage!=='Published');
    const work=live.filter(p=>p.stage!=='Idea').sort((x,y)=>(x.publish_date||'9999').localeCompare(y.publish_date||'9999'));
    const ideas=live.filter(p=>p.stage==='Idea');
    const out=state.projects.filter(p=>!p.archived&&p.stage==='Published'&&p.publish_date).sort((x,y)=>y.publish_date.localeCompare(x.publish_date)).slice(0,4);
    tabs.push(['videos','Videos',work.length,`<section class="room-list"><header><h3>In production</h3><span class="room-meta">Out Mon, Wed and Sat</span></header>
      ${work.length?work.map(p=>row('project:'+p.id,p.publish_date,title(p),p.stage)).join(''):'<p class="hint">Nothing in production.</p>'}
      ${ideas.length?`<button class="room-more" data-room-ideas>${roomIdeas?'Hide':'Show'} ${ideas.length} idea${ideas.length===1?'':'s'}</button>${roomIdeas?ideas.map(p=>row('project:'+p.id,p.publish_date,title(p),p.stage)).join(''):''}`:''}
      ${out.length?`<h4>Recently out</h4>${out.map(p=>row('project:'+p.id,p.publish_date,title(p),p.stage)).join('')}`:''}</section>`]);
  }
  if(!tabs.length)return '';
  const on=tabs.some(t=>t[0]===roomTab[a.id])?roomTab[a.id]:tabs[0][0];
  return `<div class="room-tabs" role="tablist">${tabs.map(t=>`<button role="tab" aria-selected="${t[0]===on}" data-room-tab="${t[0]}">${esc(t[1])}${t[2]?` <span>${t[2]}</span>`:''}</button>`).join('')}</div>
    <div class="room-panel" role="tabpanel">${tabs.find(t=>t[0]===on)[3]}</div>`;
}
function roomRender(){
  const a=teamAgent(teamOpen),box=$('#room-chat');if(!a||!box)return;
  const old=$('#room-input'),draft=old?old.value:'',focused=document.activeElement===old;
  box.innerHTML=roomChat(a);
  const i=$('#room-input');if(i){i.value=draft;roomGrow(i);if(focused)i.focus()}
  const t=$('#room-thread');if(t)t.scrollTop=t.scrollHeight;
  roomCarouselSync();
  roomFrameioSync();
}
function roomWorkRender(){const a=teamAgent(teamOpen);if(!a)return;const box=$('#room-work');if(box)box.innerHTML=roomWork(a);const f=$('#room-focus');if(f)f.innerHTML=deskFocus(a);const st=$('.hub-stats');if(st)st.outerHTML=deskStatTiles(a)}
function roomGrow(i){i.style.height='auto';i.style.height=Math.min(i.scrollHeight,160)+'px'}
async function roomLoad(id){
  try{
    const [thread,status]=await Promise.all([api(`/api/agents/thread?project=desk&agent=${encodeURIComponent(id)}`),roomStatus?Promise.resolve(roomStatus):api('/api/agents/status')]);
    roomThread[id]=thread;roomStatus=status;roomLoaded[id]=true;if(teamOpen===id)roomRender();
  }catch(e){roomLoaded[id]=true;toast(e.message,true);if(teamOpen===id)roomRender()}
}
async function roomSend(message,retryId=null,images=null){
  const a=teamAgent(teamOpen);message=(message||'').trim();if(!a||roomBusy)return;
  if(chatUploading.room&&!images){toast('Wait a moment for the images to finish adding.',true);return}
  if(!message&&!(images||chatAttach.room).length)return;
  images=images||chatTake('room');
  const id=a.id;
  const requestId=id==='sam'?((roomThread[id]||[]).find(m=>m.id===retryId)?.request_id||crypto.randomUUID()):null;
  if(requestId)roomCarouselRemember(requestId);
  const list=(roomThread[id]||[]).filter(m=>m.id!==retryId);
  roomThread[id]=[...list,{id:'pending',role:'josh',text:message,images,request_id:requestId,at:new Date().toISOString()}];
  roomBusy=id;roomStarted=Date.now();roomRender();
  clearInterval(roomTick);roomTick=setInterval(()=>{const c=$('#room-clock');if(c)c.textContent=roomElapsed()},1000);
  try{const r=await api('/api/agents/chat',{agent_id:id,message,images,...(requestId?{request_id:requestId}:{})});roomThread[id]=r.messages;if(requestId)roomCarouselFinish(requestId);roomWatchJobs(id,r.reply?.saved);if(r.reply?.saved?.length){carousels=null;deskCarousels=null;if(typeof inspo!=='undefined')inspo=null;if(typeof dockRefresh==='function')dockRefresh(r.reply.saved);deskData().then(roomWorkRender).catch(()=>{})}}
  catch(e){roomThread[id]=(roomThread[id]||[]).map(m=>m.id==='pending'?{...m,id:'failed-'+Date.now(),failed:e.message}:m)}
  finally{clearInterval(roomTick);roomBusy=null;if(teamOpen===id)roomRender()}
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
  const rt=e.target.closest('[data-room-retry]');if(rt){const a=teamAgent(teamOpen),m=(roomThread[a?.id]||[]).find(x=>x.id===rt.dataset.roomRetry);if(m)roomSend(m.text,m.id,m.images||[]);return}
  const tb=e.target.closest('[data-room-tab]');if(tb){roomTab[teamOpen]=tb.dataset.roomTab;roomWorkRender();return}
  if(e.target.closest('[data-room-ideas]')){roomIdeas=!roomIdeas;roomWorkRender();return}
  if(e.target.closest('[data-room-send]')){const i=$('#room-input');const v=i.value;i.value='';roomSend(v);return}
  const forget=e.target.closest('[data-team-forget]');
  if(forget&&confirm('Remove this lesson?')){try{await api('/api/team/learnings/'+forget.dataset.teamForget,{delete:true});team.learnings=team.learnings.filter(l=>l.id!==forget.dataset.teamForget);render()}catch(err){toast(err.message,true)}}
});
document.addEventListener('keydown',e=>{if(e.target.id==='room-input'&&e.key==='Enter'&&!e.shiftKey&&!e.isComposing){e.preventDefault();if(roomBusy)return;const v=e.target.value;e.target.value='';roomGrow(e.target);roomSend(v)}});
document.addEventListener('input',e=>{if(e.target.id==='room-input')roomGrow(e.target)});
document.addEventListener('keydown',e=>{const c=e.target.closest?.('[data-team-open]');if(c&&e.key==='Enter'){teamOpen=c.dataset.teamOpen;render()}});
const teamBaseRender=render;render=function(){if(!current&&view==='team')return renderTeam();return teamBaseRender()};

// BEGIN CAROUSEL CHAT PROGRESS
// Same compact progress-card styling as Frame.io; carousel stages never imply a percentage.
const roomCarouselCache=new Map(),roomCarouselWatching=new Map(),roomCarouselRequests=new Map();
const roomCarouselLabels={queued:'Queued',preparing:'Preparing',rendering:'Rendering',verifying:'Verifying',saving:'Saving',complete:'Complete',failed:'Failed',conflict:'Conflict',custom:'Blocked',binding:'Blocked',context:'Blocked',interrupted:'Interrupted',stalled:'Stalled'};
const roomCarouselNotes={queued:'Waiting to prepare this carousel.',preparing:'Checking the requested changes.',rendering:'Rendering slides. Incremental counts are not available.',verifying:'Checking the ordered slide images.',saving:'Saving the verified carousel.',complete:'Carousel changes committed.',failed:'The carousel was not saved. Review the request before retrying.',conflict:'The carousel changed. Reload it before retrying.',custom:'Custom slides were preserved. Use a single-slide override.',context:'This carousel needs saved separate caption and QR link context. No Photo draft was saved.',binding:'Photo promotion needs an owner-approved post/offer binding. No draft was saved.',interrupted:'The app restarted before completion was confirmed. Check the carousel before retrying.',stalled:'No recent stage update. Completion is not confirmed.'};
function roomCarouselId(x){return typeof x==='string'&&/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(x)}
function roomCarouselSnapshot(x){
  if(!x||x.kind!=='carousel_job'||!roomCarouselId(x.job_id)||!roomCarouselId(x.request_id)||!roomCarouselId(x.carousel_id)||!Object.hasOwn(roomCarouselLabels,x.state))return null;
  return {kind:'carousel_job',job_id:x.job_id,request_id:x.request_id,carousel_id:x.carousel_id,state:x.state,
    terminal:['complete','failed','conflict','custom','binding','context','interrupted'].includes(x.state),updated_at:typeof x.updated_at==='string'?x.updated_at:'',
    revision:Number.isSafeInteger(x.revision)&&x.revision>0?x.revision:null,changed:x.changed!==false};
}
function roomCarouselCard(value){
  const s=roomCarouselSnapshot(roomCarouselCache.get(value?.job_id)||value);if(!s)return '';
  const age=Date.now()-Date.parse(s.updated_at);if(!s.terminal&&(!Number.isFinite(age)||age>300000||age< -30000))s.state='stalled';
  const note=s.state==='complete'&&!s.changed?'No changes needed. The current revision was verified.':roomCarouselNotes[s.state];
  return `<div class="room-saved-item room-frameio" data-carousel-job="${esc(s.job_id)}" role="group" aria-label="Carousel progress"><b>Carousel <span>${esc(roomCarouselLabels[s.state])}</span></b><p>${esc(note)}</p>${s.state==='complete'&&s.revision?`<small>Saved revision ${s.revision}</small><button type="button" class="link" data-desk-act="carousel:${esc(s.carousel_id)}">Open carousel</button>`:''}</div>`;
}
function roomCarouselPersist(){try{localStorage.setItem('careeros-carousel-progress-v1',JSON.stringify([...roomCarouselRequests.values()].sort((a,b)=>b.at-a.at).slice(0,20)))}catch{}}
function roomCarouselRemember(id){if(!roomCarouselId(id))return;roomCarouselRequests.set(id,{id,at:Date.now(),awaiting:true});roomCarouselStop('request:'+id);roomCarouselPersist()}
function roomCarouselFinish(id){const r=roomCarouselRequests.get(id);if(r){r.awaiting=false;roomCarouselPersist()}}
try{for(const r of JSON.parse(localStorage.getItem('careeros-carousel-progress-v1')||'[]'))if(roomCarouselId(r.id)&&Number.isFinite(r.at)&&Date.now()-r.at<86400000)roomCarouselRequests.set(r.id,{id:r.id,at:r.at,awaiting:r.awaiting===true})}catch{}
function roomCarouselVisible(){return !document.hidden&&!current&&((view==='team'&&teamOpen==='sam'&&!!$('#room-thread'))||(view==='carousels'&&!!$('#carousel-job-progress')))}
function roomCarouselScopes(){
  const scopes=new Map();if(!roomCarouselVisible())return scopes;
  if(view==='carousels'){
    const id=$('#carousel-job-progress')?.dataset.carouselProgress;if(roomCarouselId(id))scopes.set('carousel:'+id,{type:'carousel',id});
  }else{
    for(const message of roomThread.sam||[])for(const row of message.saved||[]){const job=roomCarouselSnapshot(row.job);if(!job)continue;if(!roomCarouselCache.has(job.job_id))roomCarouselCache.set(job.job_id,job);if(!roomCarouselRequests.has(job.request_id))roomCarouselRequests.set(job.request_id,{id:job.request_id,at:Date.now(),awaiting:false})}
    for(const r of [...roomCarouselRequests.values()].sort((a,b)=>b.at-a.at).slice(0,20))if(Date.now()-r.at<86400000)scopes.set('request:'+r.id,{type:'request',id:r.id});
  }
  return scopes;
}
function roomCarouselPaint(){
  if(!roomCarouselVisible())return;
  for(const node of document.querySelectorAll('[data-carousel-job]')){const row=roomCarouselCache.get(node.dataset.carouselJob);if(row){const html=roomCarouselCard(row);if(node.outerHTML!==html)node.outerHTML=html}}
  const box=view==='carousels'?$('#carousel-job-progress'):$('#room-carousel-progress');if(!box)return;
  const saved=new Set();if(view==='team')for(const m of roomThread.sam||[])for(const row of m.saved||[])if(row.job?.job_id)saved.add(row.job.job_id);
  const keys=roomCarouselScopes(),rows=[...roomCarouselCache.values()].filter(x=>keys.has((view==='carousels'?'carousel:':'request:')+(view==='carousels'?x.carousel_id:x.request_id))&&!saved.has(x.job_id));
  const issue=[...roomCarouselWatching.values()].some(w=>w.issue)?'<p class="hint">Progress unavailable. Last confirmed stages are shown. <button type="button" class="link" data-carousel-check>Check status</button></p>':'';
  const html=issue+rows.map(roomCarouselCard).join('');if(box.innerHTML!==html)box.innerHTML=html;
}
function roomCarouselStop(key){for(const [k,w] of roomCarouselWatching){if(key&&key!==k)continue;clearTimeout(w.timer);w.controller?.abort();roomCarouselWatching.delete(k)}}
async function roomCarouselPoll(key,w){
  if(roomCarouselWatching.get(key)!==w||w.pending||!roomCarouselVisible())return;
  w.pending=true;w.controller=new AbortController();const timeout=setTimeout(()=>w.controller.abort(),10000);let stop=false;
  try{
    const url=w.type==='request'?`/api/carousel-jobs?request_id=${encodeURIComponent(w.id)}`:`/api/carousels/${encodeURIComponent(w.id)}/jobs`;
    const response=await fetch(url,{method:'GET',headers:{'X-DJ-Token':token},cache:'no-store',signal:w.controller.signal});
    if(!response.ok){stop=[401,403,404].includes(response.status);throw Error('unavailable')}
    const data=await response.json();if(!Array.isArray(data.jobs))throw Error('invalid status');
    const rows=data.jobs.map(roomCarouselSnapshot);
    if(rows.some(x=>!x||(w.type==='request'?x.request_id:x.carousel_id)!==w.id))throw Error('wrong job scope');
    if(new Set(rows.map(x=>x.job_id)).size!==rows.length||rows.some(x=>{const known=roomCarouselCache.get(x.job_id);return known&&(known.request_id!==x.request_id||known.carousel_id!==x.carousel_id)}))throw Error('changed job identity');
    if(roomCarouselWatching.get(key)!==w||!roomCarouselVisible())return;
    for(const row of rows)roomCarouselCache.set(row.job_id,row);
    w.issue=false;w.failures=0;w.stopped=false;
    const request=roomCarouselRequests.get(w.id);
    w.idle=!rows.length||rows.every(x=>x.terminal);
    stop=w.type==='request'&&(rows.length?w.idle:(!request?.awaiting||Date.now()-request.at>1800000));
    if(stop&&w.type==='request')roomCarouselFinish(w.id);
  }catch{
    if(roomCarouselWatching.get(key)!==w||!roomCarouselVisible())return;
    w.issue=true;w.failures++;
  }finally{
    clearTimeout(timeout);w.pending=false;
    if(roomCarouselWatching.get(key)===w){
      w.stopped=stop;roomCarouselPaint();
      if(!roomCarouselVisible())roomCarouselStop(key);
      else if(!stop)w.timer=setTimeout(()=>roomCarouselPoll(key,w),w.failures?Math.min(60000,3000*2**w.failures):w.type==='carousel'&&w.idle?15000:3000);
    }
  }
}
function roomCarouselSync(){
  const scopes=roomCarouselScopes();for(const key of roomCarouselWatching.keys())if(!scopes.has(key))roomCarouselStop(key);
  for(const [key,scope] of scopes){if(roomCarouselWatching.has(key))continue;const w={...scope,pending:false,failures:0,issue:false};roomCarouselWatching.set(key,w);roomCarouselPoll(key,w)}
}
document.addEventListener('click',e=>{if(e.target.closest?.('[data-carousel-check]')){roomCarouselStop();roomCarouselSync()}});
document.addEventListener('visibilitychange',roomCarouselSync);
window.addEventListener('pagehide',()=>roomCarouselStop());window.addEventListener('pageshow',roomCarouselSync);
if($('#workspace'))new MutationObserver(roomCarouselSync).observe($('#workspace'),{childList:true,subtree:true});
// END CAROUSEL CHAT PROGRESS

// BEGIN FRAMEIO CHAT PROGRESS
// Exact local review snapshots only. This never starts/resumes uploads or polls Frame.io itself.
const roomFrameioCache=new Map(),roomFrameioWatching=new Map();
const roomFrameioLabels={queued:'Queued',uploading:'Uploading',processing:'Processing',complete:'Complete',blocked:'Blocked',stalled:'Stalled'};
const roomFrameioNotes={queued:'Waiting for the upload worker.',uploading:'Counts show accepted upload parts.',processing:'Waiting for verified playback.',complete:'Upload and playback verified.',blocked:'Open the video review details before retrying.',stalled:'No recent progress update. Completion is not confirmed.'};
function roomFrameioId(x){return typeof x==='string'&&/^[A-Za-z0-9_-]{1,80}$/.test(x)}
function roomFrameioKey(x){return roomFrameioId(x.project_id)&&roomFrameioId(x.review_id)?x.project_id+':'+x.review_id:''}
function roomFrameioLink(value){
  if(typeof value!=='string'||value.length>500||/[\s\\\x00-\x1f]/.test(value))return '';
  try{const u=new URL(value);return u.protocol==='https:'&&['app.frame.io','next.frame.io'].includes(u.hostname)&&!u.username&&!u.password&&!u.search&&!u.hash&&(!u.port||u.port==='443')?value:''}catch{return ''}
}
function roomFrameioSnapshot(x){
  const state=Object.hasOwn(roomFrameioLabels,x.state)?x.state:'blocked',p=x.progress||{},progress={};
  for(const [done,total] of [['bytes_sent','bytes_total'],['parts_done','parts']]){
    if(Number.isSafeInteger(p[done])&&Number.isSafeInteger(p[total])&&p[done]>=0&&p[total]>0&&p[done]<=p[total]){progress[done]=p[done];progress[total]=p[total]}
  }
  return {kind:'frameio',project_id:x.project_id,review_id:x.review_id,state,terminal:['complete','blocked'].includes(state),progress,
    updated_at:typeof x.updated_at==='string'&&Number.isFinite(Date.parse(x.updated_at))?x.updated_at:null,
    view_url:state==='complete'?roomFrameioLink(x.view_url):''};
}
function roomFrameioCard(receipt){
  const key=roomFrameioKey(receipt),cached=key&&roomFrameioCache.get(key),s=roomFrameioSnapshot(cached?.snapshot||receipt);
  const p=s.progress,pair=p.bytes_total?['bytes_sent','bytes_total','bytes']:p.parts?['parts_done','parts','parts']:null;
  const age=s.updated_at?Date.now()-Date.parse(s.updated_at):Infinity;
  if(!s.terminal&&(age>300000||age< -30000))s.state='stalled';
  const issue=cached?.issue,label=issue?'Status unavailable':roomFrameioLabels[s.state];
  const note=issue||(key?roomFrameioNotes[s.state]:'The request could not be confirmed. Check the video review details before retrying.');
  const meter=pair?`<progress max="${p[pair[1]]}" value="${p[pair[0]]}" aria-label="Upload ${pair[2]} accepted"></progress><small>${p[pair[0]].toLocaleString()} of ${p[pair[1]].toLocaleString()} ${pair[2]} accepted${s.state==='processing'?' · processing still pending':''}${issue?' (last confirmed)':''}</small>`:'';
  return `<div class="room-saved-item room-frameio" data-frameio-key="${esc(key)}" role="group" aria-label="Frame.io review progress"><b>Frame.io review <span>${esc(label)}</span></b><p>${esc(note)}</p>${meter}${s.updated_at?`<small>Last update ${esc(new Date(s.updated_at).toLocaleTimeString(undefined,{hour:'numeric',minute:'2-digit'}))}</small>`:''}${s.state==='complete'&&!issue&&s.view_url?`<a href="${esc(s.view_url)}" target="_blank" rel="noopener noreferrer">Open verified review</a>`:''}${key&&issue?`<button type="button" class="link" data-frameio-check="${esc(key)}">Check status</button>`:''}${roomFrameioId(s.project_id)?`<button type="button" class="link" data-er-open="${esc(s.project_id)}">Open video review details</button>`:''}</div>`;
}
function roomFrameioVisible(){return view==='team'&&teamOpen==='tariq'&&!current&&!document.hidden&&!!$('#room-thread')}
function roomFrameioPaint(key){
  if(!roomFrameioVisible())return;
  const row=roomFrameioCache.get(key);if(!row)return;
  // Replace only the small status cards; never rerender the thread or composer.
  for(const node of document.querySelectorAll('[data-frameio-key]'))if(node.dataset.frameioKey===key)node.outerHTML=roomFrameioCard(row.snapshot);
}
function roomFrameioStop(key){
  for(const [k,watch] of roomFrameioWatching){
    if(key&&k!==key)continue;
    clearTimeout(watch.timer);watch.controller?.abort();roomFrameioWatching.delete(k);
  }
}
async function roomFrameioPoll(key,watch){
  if(roomFrameioWatching.get(key)!==watch||watch.pending||!roomFrameioVisible())return;
  watch.pending=true;watch.controller=new AbortController();
  const timeout=setTimeout(()=>watch.controller.abort(),10000);let stop=false;
  try{
    const r=await fetch(`/api/projects/${encodeURIComponent(watch.snapshot.project_id)}/frameio/${encodeURIComponent(watch.snapshot.review_id)}/status`,
      {method:'GET',headers:{'X-DJ-Token':token},cache:'no-store',signal:watch.controller.signal});
    if(!r.ok){stop=[401,403,404].includes(r.status);throw Error('status unavailable')}
    const value=await r.json();
    if(roomFrameioKey(value)!==key||value.kind!=='frameio'||!Object.hasOwn(roomFrameioLabels,value.state))throw Error('wrong review status');
    if(roomFrameioWatching.get(key)!==watch||!roomFrameioVisible())return;
    watch.snapshot=roomFrameioSnapshot(value);watch.failures=0;
    roomFrameioCache.set(key,{snapshot:watch.snapshot,receipt:watch.receipt,issue:''});stop=watch.snapshot.terminal;
  }catch{
    if(roomFrameioWatching.get(key)!==watch||!roomFrameioVisible())return;
    watch.failures++;
    roomFrameioCache.set(key,{snapshot:watch.snapshot,receipt:watch.receipt,issue:stop?'This review status is unavailable. Check access or reopen the video.':'Could not refresh status. Retrying; the last update is shown.'});
  }finally{
    clearTimeout(timeout);watch.pending=false;
    if(roomFrameioWatching.get(key)===watch){
      roomFrameioPaint(key);
      if(!roomFrameioVisible())roomFrameioStop(key);
      else if(!stop)watch.timer=setTimeout(()=>roomFrameioPoll(key,watch),watch.failures?Math.min(60000,5000*2**watch.failures):watch.snapshot.state==='stalled'?30000:5000);
    }
  }
}
function roomFrameioSync(){
  if(!roomFrameioVisible()){roomFrameioStop();return}
  const receipts=new Map();
  for(const [index,message] of (roomThread.tariq||[]).entries())for(const row of message.saved||[]){const key=roomFrameioKey(row);if(row.kind==='frameio'&&key)receipts.set(key,{row,receipt:message.id||String(index)})}
  for(const key of roomFrameioWatching.keys())if(!receipts.has(key))roomFrameioStop(key);
  for(const [key,{row,receipt}] of receipts){
    const prior=roomFrameioWatching.get(key),cached=roomFrameioCache.get(key);
    if(prior?.receipt===receipt)continue;
    if(prior)roomFrameioStop(key); // A new chat request can resume the same review after a terminal stop.
    const fresh=cached?.receipt!==receipt,snapshot=fresh?roomFrameioSnapshot(row):cached.snapshot;
    const watch={snapshot,receipt,failures:0,pending:false};
    roomFrameioCache.set(key,{snapshot,receipt,issue:fresh?'':cached.issue||''});roomFrameioWatching.set(key,watch);
    if(fresh)roomFrameioPaint(key);
    // Refresh once on reopen, even for a previously terminal receipt. Then stop at terminal status.
    roomFrameioPoll(key,watch);
  }
}
document.addEventListener('visibilitychange',roomFrameioSync);
window.addEventListener('pagehide',()=>roomFrameioStop());
window.addEventListener('pageshow',roomFrameioSync);
document.addEventListener('click',e=>{
  const button=e.target.closest?.('[data-frameio-check]');if(!button||!roomFrameioVisible())return;
  const key=button.dataset.frameioCheck,watch=roomFrameioWatching.get(key);if(!watch||watch.pending)return;
  clearTimeout(watch.timer);roomFrameioPoll(key,watch);
});
// Navigation may replace the workspace without calling roomRender.
if($('#workspace'))new MutationObserver(roomFrameioSync).observe($('#workspace'),{childList:true,subtree:true});
// END FRAMEIO CHAT PROGRESS

// A job a team member started from chat (like thumbnails) posts its result later. Check for it while it runs.
const roomWatching={},roomWatchStart={};
function roomWatchJobs(agentId,saved){
  if(!(saved||[]).some(x=>(x.kind==='thumbnail'||x.kind==='edit')&&x.action==='started')||roomWatching[agentId])return;
  const since=(roomThread[agentId]||[]).length,until=Date.now()+40*60*1000;roomWatchStart[agentId]=new Date(Date.now()+5000).toISOString();
  roomWatching[agentId]=setInterval(async()=>{
    try{
      const t=await api(`/api/agents/thread?project=desk&agent=${encodeURIComponent(agentId)}`);
      if(t.length>since&&t.at(-1).role==='agent'&&t.at(-1).at>(roomWatchStart[agentId]||'')||Date.now()>until){
        clearInterval(roomWatching[agentId]);delete roomWatching[agentId];roomThread[agentId]=t;
        if(teamOpen===agentId)roomRender();if(typeof dockRender==='function')dockRender();
        if(agentId==='sam'&&typeof samThread!=='undefined'){samThread=t;if(typeof samRender==='function')samRender()}
        if(t.at(-1).role==='agent')toast(`${teamAgent(agentId)?.name||'Your team'} has finished`);
        if(current&&typeof refresh==='function')refresh().catch(()=>{});
      }
    }catch{}
  },15000);
}
// The team's own Discord messages to Josh (studio/notices.py). Pause everyone, mute one person, or switch banter off.
let teamNotices=null;
function ntDubaiMorning(){const d=new Date();const dub=new Date(d.getTime()+4*3600e3);const m=new Date(Date.UTC(dub.getUTCFullYear(),dub.getUTCMonth(),dub.getUTCDate(),4));if(dub.getUTCHours()>=8)m.setUTCDate(m.getUTCDate()+1);return m}
function teamNoticesBar(){
  const v=teamNotices;if(!v)return '';const s=v.settings,paused=v.paused_until&&new Date(v.paused_until)>new Date();
  const bots=v.bots.map(b=>`<button class="nt-bot ${b.muted?'off':''}" data-nt-mute="${esc(b.id)}" aria-pressed="${b.muted}" title="${esc(b.muted?'Muted. Click to unmute.':b.ready?'Click to mute.':b.why)}">${esc(b.name)}${b.muted?' (muted)':b.ready?'':' (not on Discord)'}</button>`).join('');
  return `<section class="nt" aria-label="Team messages on Discord">
    <p class="nt-status">${esc(v.status)}</p>
    <div class="nt-row">${paused?`<button class="small" data-nt-resume>Resume</button>`:`<button class="small" data-nt-pause="2">Pause 2 hours</button><button class="small" data-nt-pause="morning">Pause until morning</button>`}
      <button class="small" data-nt-banter>${s.banter?'Turn banter off':'Turn banter on'}</button></div>
    <div class="nt-row nt-bots">${bots}</div>
  </section>`}
async function teamNoticesSet(changes){try{teamNotices=await api('/api/notices/settings',{changes});const box=$('#tm-notices');if(box)box.innerHTML=teamNoticesBar()}catch(e){toast(e.message,true)}}
document.addEventListener('click',e=>{
  if(!e.target.closest('#tm-notices')||!teamNotices)return;
  const p=e.target.closest('[data-nt-pause]');if(p){const until=p.dataset.ntPause==='morning'?ntDubaiMorning():new Date(Date.now()+2*3600e3);return teamNoticesSet({paused_until:until.toISOString()})}
  if(e.target.closest('[data-nt-resume]'))return teamNoticesSet({paused_until:''});
  if(e.target.closest('[data-nt-banter]'))return teamNoticesSet({banter:!teamNotices.settings.banter});
  const m=e.target.closest('[data-nt-mute]');if(m){const id=m.dataset.ntMute,cur=new Set(teamNotices.settings.muted);cur.has(id)?cur.delete(id):cur.add(id);return teamNoticesSet({muted:[...cur]})}
});
