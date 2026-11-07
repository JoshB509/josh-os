'use strict';
// Desks: each team member sits in their own workspace and tells you what needs you.
// Until the agents run, briefings are worked out from the app's own data, so every
// line is true and every item opens the thing it talks about.
const DESKS={board:'sam',carousels:'sam',editing:'tariq',email:'laia'};
let deskCarousels=null,deskTeamLoading=null,deskLessons=null;

function deskToday(){const d=plannerToday();d.setHours(0,0,0,0);return d}
function deskDays(iso){if(!iso)return null;const d=new Date(iso+'T00:00:00');return Math.round((d-deskToday())/864e5)}
function deskWhen(iso){const n=deskDays(iso);if(n===null)return 'no date';if(n<0)return `${-n} day${n===-1?'':'s'} overdue`;if(n===0)return 'today';if(n===1)return 'tomorrow';if(n<7)return new Date(iso+'T12:00:00').toLocaleDateString(undefined,{weekday:'long'});return new Date(iso+'T12:00:00').toLocaleDateString(undefined,{day:'numeric',month:'short'})}
function deskPlural(n,one,many){return `${n} ${n===1?one:many||one+'s'}`}
function deskGreeting(){const h=plannerToday().getHours();return h<12?'Morning':h<18?'Afternoon':'Evening'}

async function deskData(){
  const jobs=[];
  if(!team&&!deskTeamLoading)deskTeamLoading=api('/api/team').then(t=>team=t).catch(()=>{});
  if(!deskCarousels)jobs.push(api('/api/carousels').then(c=>{deskCarousels=c;if(typeof carousels!=='undefined'&&!carousels)carousels=c}).catch(()=>{}));
  if(!deskLessons)jobs.push(api('/api/lessons').then(l=>deskLessons=l).catch(()=>{deskLessons={lessons:[],missing:true}}));
  if(deskTeamLoading)jobs.push(deskTeamLoading);
  await Promise.all(jobs);
}
function deskCarouselRows(){return ((typeof carousels!=='undefined'&&carousels)||deskCarousels||{items:[]}).items.filter(c=>!c.archived)}
function deskEmailRows(){return (typeof emailRows!=='undefined'?emailRows:[]).filter(e=>!e.archived)}

// Each briefing: what they say, what needs Josh, and quick replies that act.
// `needs` marks a decision only Josh can make; those items get the gold marker and go first.
// `scope` narrows Sam to videos on YouTube and to carousels on Carousels, words included.
function deskBrief(id,scope=null){
  const ps=state.projects.filter(p=>!p.archived);
  if(id==='sam'){
    const cs=deskCarouselRows(),open=cs.filter(c=>!['Posted','Scheduled'].includes(c.stage));
    const due=open.filter(c=>c.post_date&&deskDays(c.post_date)<=7).sort((a,b)=>a.post_date.localeCompare(b.post_date));
    const review=cs.filter(c=>c.stage==='Ready for review');
    const noScript=ps.filter(p=>['Idea','Scripting'].includes(p.stage)&&!p.script.trim());
    const noThumb=ps.filter(p=>['Editing','Review','Scheduled'].includes(p.stage)&&!p.top_three.length);
    const urgentThumb=noThumb.filter(p=>p.stage!=='Editing');
    const next=ps.filter(p=>p.publish_date&&deskDays(p.publish_date)>=0&&p.stage!=='Published').sort((a,b)=>a.publish_date.localeCompare(b.publish_date))[0];
    const carouselItems=[
      ...review.map(c=>({needs:true,icon:'eye',text:`Review “${c.title}”`,sub:'Carousel ready for your yes',act:'carousel:'+c.id})),
      ...due.filter(c=>c.stage!=='Ready for review').map(c=>({icon:'clock',text:c.title,sub:`Carousel · ${c.stage} · due ${deskWhen(c.post_date)}`,act:'carousel:'+c.id}))];
    const videoItems=[
      ...noThumb.map(p=>({needs:p.stage!=='Editing',icon:'image',text:`Pick a thumbnail for “${p.name}”`,sub:`Video · ${p.stage}${p.publish_date?` · out ${deskWhen(p.publish_date)}`:''}`,act:'project-tab:thumbnails:'+p.id})),
      ...noScript.slice(0,3).map(p=>({icon:'pen',text:p.name,sub:'Video idea · no script yet',act:'project-tab:script:'+p.id}))];
    const carouselSay=due.length?`${deskPlural(due.length,'carousel')} ${due.length===1?'is':'are'} due in the next 7 days, and the first one is due ${deskWhen(due[0].post_date)}.${review.length?` ${review.length} ${review.length===1?'is':'are'} waiting for your review.`:''}`
      :review.length?`${deskPlural(review.length,'carousel')} ready for your review.`
      :open.length?`Nothing's due this week, and ${deskPlural(open.length,'carousel')} ${open.length===1?'is':'are'} in the works.`:'Nothing is due this week, so it is a good time to line up the next carousels.';
    const videoSay=next?`The next video out is “${next.name}”, ${deskWhen(next.publish_date)}.${urgentThumb.length?` ${deskPlural(urgentThumb.length,'video')} close to release still need${urgentThumb.length===1?'s':''} a thumbnail.`:''}`
      :ps.length?`No video has a release date yet. ${noScript.length?`${deskPlural(noScript.length,'idea')} still need${noScript.length===1?'s':''} a script.`:''}`:'The board is empty. Give me a topic and I\'ll turn it into a video plan.';
    const chips=[{label:'New carousel',act:'new:carousel'},{label:'New video',act:'new:video'},{label:'Open carousels',act:'view:carousels'}];
    if(scope==='board')return {say:videoSay,items:videoItems,chips};
    if(scope==='carousels')return {say:carouselSay,items:carouselItems,chips};
    if(!cs.length&&!ps.length)return {say:'The board is empty, so let\'s start with an idea. Give me a topic and I\'ll turn it into a carousel and a video plan.',items:[],chips};
    return {say:carouselSay+(urgentThumb.length?` On the video side, ${deskPlural(urgentThumb.length,'thumbnail')} ${urgentThumb.length===1?'needs':'need'} picking.`:''),items:[...carouselItems,...videoItems],chips};
  }
  if(id==='tariq'){
    const review=ps.filter(p=>p.stage==='Review'),ready=ps.filter(p=>p.stage==='Recorded'),editing=ps.filter(p=>p.stage==='Editing');
    const running=state.jobs.filter(j=>['running','starting'].includes(j.status?.state));
    const items=[
      ...review.map(p=>({needs:true,icon:'eye',text:`Watch “${p.name}”`,sub:'Edit ready for your review',act:'project-tab:edit:'+p.id})),
      ...ready.map(p=>({icon:'film',text:p.name,sub:'Recorded · ready to start the edit',act:'project-tab:edit:'+p.id})),
      ...editing.map(p=>({icon:'scissors',text:p.name,sub:'In the edit',act:'project-tab:edit:'+p.id})),
    ];
    let say;
    if(running.length)say=`I'm running ${deskPlural(running.length,'edit')} right now.${review.length?` ${deskPlural(review.length,'video')} ready for you to watch.`:''}`;
    else if(review.length)say=`${deskPlural(review.length,'video')} ready for you to watch. Tell me what to change and I'll recut it.`;
    else if(ready.length)say=`${deskPlural(ready.length,'recording')} waiting for the edit. Open one and I'll start the clean cut.`;
    else say='Nothing to edit right now. Once a video is marked Recorded, I\'ll pick it up.';
    return {say,items,chips:[{label:'Editing board',act:'view:editing'}]};
  }
  if(id==='laia'){
    const es=deskEmailRows(),queued=es.filter(e=>['Ready','Scheduled','Published'].includes(e.status)&&e.planned_date&&deskDays(e.planned_date)>=0);
    const last=queued.map(e=>e.planned_date).sort().pop(),ahead=last?deskDays(last):0;
    const drafts=es.filter(e=>e.status==='Draft').sort((a,b)=>(a.planned_date||'9999').localeCompare(b.planned_date||'9999'));
    const soon=drafts.filter(e=>e.planned_date&&deskDays(e.planned_date)<=14);
    const items=[
      ...soon.map(e=>({needs:deskDays(e.planned_date)<=7,icon:'pen',text:e.subject||'Untitled email',sub:`Draft · goes out ${deskWhen(e.planned_date)}`,act:'email:'+e.id})),
      ...drafts.filter(e=>!soon.includes(e)).slice(0,3).map(e=>({icon:'mail',text:e.subject||'Untitled email',sub:`Draft · ${deskWhen(e.planned_date)}`,act:'email:'+e.id})),
    ];
    let say;
    if(!es.length)say='No emails yet. Want me to plan the next two weeks of newsletters?';
    else if(ahead>=14)say=`We're ${ahead} days ahead on email, so the two-week buffer is covered.${soon.length?` ${deskPlural(soon.length,'draft')} still need${soon.length===1?'s':''} your review.`:''}`;
    else{const week=soon.filter(e=>deskDays(e.planned_date)<=7).length;
      say=`We're ${ahead} day${ahead===1?'':'s'} ahead on email, and the goal is 14.${soon.length?` ${deskPlural(soon.length,'draft')} to review${week?`, and ${week===soon.length?(week===1?'it goes':'they all go'):`${week} of them ${week===1?'goes':'go'}`} out this week`:''}.`:' Let\'s plan the next few.'}`}
    return {say,items,chips:[{label:'New email',act:'new:email'},{label:'Email plan',act:'view:email'}]};
  }
  if(id==='saskia'){
    if(!deskLessons||deskLessons.missing)return {unknown:true,say:'The course lessons aren\'t set up on this PC yet. Once the lesson catalogue is in Josh OS, I\'ll track what\'s recorded and what\'s next.',items:[],chips:[{label:'Course lessons',act:'view:lessons'}]};
    const ls=deskLessons.lessons||[],done=l=>!!(l.recording?.preferred_take||l.recording?.takes?.length);
    const todo=ls.filter(l=>!done(l)),planned=todo.filter(l=>l.recording?.planned_date).sort((a,b)=>a.recording.planned_date.localeCompare(b.recording.planned_date));
    const late=planned.filter(l=>deskDays(l.recording.planned_date)<0),undated=todo.filter(l=>!l.recording?.planned_date);
    const label=l=>`${l.code?l.code+' ':''}${l.title||'Untitled lesson'}`;
    const items=[...late.map(l=>({needs:true,icon:'clock',text:label(l),sub:`Lesson · planned ${deskWhen(l.recording.planned_date)}`,act:'view:lessons'})),
      ...planned.filter(l=>!late.includes(l)).slice(0,4).map(l=>({needs:deskDays(l.recording.planned_date)<=1,icon:'film',text:label(l),sub:`Lesson · record ${deskWhen(l.recording.planned_date)}`,act:'view:lessons'})),
      ...undated.slice(0,3).map(l=>({icon:'pen',text:label(l),sub:'Lesson · no recording date',act:'view:lessons'}))];
    const say=!ls.length?'No lessons in the catalogue yet.'
      :`${ls.length-todo.length} of ${ls.length} lessons are recorded.${planned.length?` The next one to record is ${label(planned[0])}, ${deskWhen(planned[0].recording.planned_date)}.`:''}${undated.length?` ${deskPlural(undated.length,'lesson')} still need${undated.length===1?'s':''} a recording date.`:''}`;
    return {say,items,chips:[{label:'Course lessons',act:'view:lessons'},{label:'Tasks & goals',act:'view:tasks'}]};
  }
  return {say:'I start once the business numbers are connected to Josh OS. Then I\'ll track sales and revenue against the USD 15,000 a month goal.',items:[],chips:[]};
}
// One count used everywhere: decisions waiting for Josh.
function deskNeeds(id,scope=null){return deskBrief(id,scope).items.filter(i=>i.needs).length}

const DESK_ICONS={eye:'<circle cx="8" cy="8" r="2"/><path d="M1.5 8s2.4-4.5 6.5-4.5S14.5 8 14.5 8 12.1 12.5 8 12.5 1.5 8 1.5 8z"/>',clock:'<circle cx="8" cy="8" r="6"/><path d="M8 4.5V8l2.5 1.5"/>',image:'<rect x="2" y="3" width="12" height="10" rx="2"/><circle cx="6" cy="6.5" r="1.2"/><path d="m3 12 3.5-3.5 2.5 2.5 2-2 2 2"/>',pen:'<path d="M10.5 2.5l3 3L6 13H3v-3z"/>',film:'<rect x="2" y="3" width="12" height="10" rx="2"/><path d="M5 3v10M11 3v10M2 6h3M2 10h3M11 6h3M11 10h3"/>',scissors:'<circle cx="4.5" cy="4.5" r="2"/><circle cx="4.5" cy="11.5" r="2"/><path d="M6.2 5.6 14 12M6.2 10.4 14 4"/>',mail:'<rect x="2" y="3" width="12" height="10" rx="2"/><path d="m2 4 6 5 6-5"/>'};
function deskItem(i){return `<button class="desk-item ${i.needs?'needs':''}" data-desk-act="${esc(i.act)}"><svg viewBox="0 0 16 16">${DESK_ICONS[i.icon]||''}</svg><span><b>${esc(i.text)}</b><small>${esc(i.sub)}</small></span><i>›</i></button>`}
function deskCollapsed(id){try{return localStorage.getItem('desk-min-'+id)==='1'}catch{return false}}

// Which items belong on which page: Sam's videos on YouTube, his carousels on Carousels.
const DESK_SCOPE={board:/^project/,carousels:/^carousel/,editing:/^project/,email:/^email/};
// Quick replies that repeat the page's own button or link back to the page you're on.
const DESK_SKIP={board:['new:video'],carousels:['new:carousel','view:carousels'],editing:['view:editing'],email:['new:email','view:email']};
const DESK_CHEVRON='<svg viewBox="0 0 16 16"><path d="m4 6 4 4 4-4"/></svg>';

function agentDesk(id,{compact=false,scope=null,head=true}={}){
  const a=team?.agents?.find(x=>x.id===id);if(!a)return '';
  const b=deskBrief(id,scope),min=compact&&deskCollapsed(id);
  let all=b.items;if(scope&&DESK_SCOPE[scope])all=all.filter(i=>DESK_SCOPE[scope].test(i.act));
  all=[...all.filter(i=>i.needs),...all.filter(i=>!i.needs)];
  const items=all.slice(0,compact?(scope==='board'?2:3):5),needs=all.filter(i=>i.needs).length;
  const chips=b.chips.filter(c=>!(DESK_SKIP[scope]||[]).includes(c.act));
  const home={sam:'carousels',tariq:'editing',laia:'email',saskia:'lessons'}[id];
  const count=a.status==='Coming later'?'':needs?`<span class="desk-count">${needs} for you</span>`:all.length?'':`<span class="desk-count clear">All clear</span>`;
  return `<section class="desk ${compact?'compact':''} ${min?'min':''}" style="--agent:${esc(a.color)}" data-desk="${esc(id)}" aria-label="${esc(a.name)}'s desk">
    <header class="desk-head" ${head?'':'hidden'}>${teamAvatar(a,'md')}
      <div class="desk-who"><b>${esc(a.name)}</b><span>${esc(a.role)}</span></div>${count}
      ${compact?`<button class="subtle small desk-toggle" data-desk-toggle="${esc(id)}" aria-expanded="${!min}" aria-label="${min?'Show':'Minimise'} ${esc(a.name)}'s desk">${DESK_CHEVRON}</button>`:''}
    </header>
    <div class="desk-body" ${head?'':'style="margin-top:0"'}>
      <p class="desk-say">${esc(b.say)}</p>
      ${items.length?`<div class="desk-items">${items.map(deskItem).join('')}</div>${all.length>items.length?`<button class="desk-more" data-desk-act="view:${esc(scope&&DESK_SCOPE[scope]?scope:home||'today')}">See all ${all.length}</button>`:''}`:''}
      ${chips.length?`<div class="desk-chips">${chips.map(c=>`<button class="chip-btn" data-desk-act="${esc(c.act)}">${esc(c.label)}</button>`).join('')}</div>`:''}
    </div>
  </section>`;
}
function deskSkeleton(n=1){return Array.from({length:n},()=>'<div class="skeleton" aria-hidden="true"><i></i><i style="width:70%"></i><i style="width:45%"></i></div>').join('')}

// Today: the home page. Every team member reports in, then the week ahead.
async function renderTodayView(){
  page('Today','');$('#page-title').parentElement.hidden=true;
  if(!team)$('#workspace').innerHTML=`<div class="today"><div class="today-desks" aria-label="Getting the team's notes">${deskSkeleton(3)}</div></div>`;
  await deskData();if(view!=='today'||current)return;
  const now=plannerToday(),week=[];
  for(let i=0;i<7;i++){const d=new Date(now);d.setDate(d.getDate()+i);const key=dateKey(d);
    const items=[...state.projects.filter(p=>!p.archived&&p.publish_date===key).map(p=>({kind:'Video',text:p.name,act:'project:'+p.id})),
      ...deskCarouselRows().filter(c=>c.post_date===key).map(c=>({kind:'Carousel',text:c.title,act:'carousel:'+c.id})),
      ...deskEmailRows().filter(e=>e.planned_date===key).map(e=>({kind:'Email',text:e.subject||'Untitled email',act:'email:'+e.id}))];
    week.push({d,items})}
  const agents=(team?.agents||[]).filter(a=>a.status!=='Coming later');
  $('#workspace').innerHTML=`<div class="today">
    <header class="today-head"><p class="today-date">${esc(now.toLocaleDateString(undefined,{weekday:'long',day:'numeric',month:'long'}))}</p>
      <h1>${deskGreeting()}, Josh.</h1></header>
    <div class="today-desks">${agents.map(a=>agentDesk(a.id)).join('')}</div>
    <section class="week"><h2>This week</h2><div class="week-grid">${week.map(({d,items},i)=>`<div class="week-day ${i===0?'is-today':''}">
      <div class="week-label"><b>${esc(i===0?'Today':d.toLocaleDateString(undefined,{weekday:'short'}))}</b><span>${d.getDate()}</span></div>
      ${items.map(x=>`<button class="week-item k-${x.kind.toLowerCase()}" data-desk-act="${esc(x.act)}"><small>${x.kind}</small>${esc(x.text)}</button>`).join('')||'<p class="week-empty">Free</p>'}</div>`).join('')}</div></section>
  </div>`;
}

// Put the right team member at the top of their workspace.
function deskInject(){
  if(current)return;const id=DESKS[view];if(!id)return;
  if(view==='email'&&typeof emailCurrent!=='undefined'&&emailCurrent)return;
  if(view==='carousels'||view==='email')return; // the queues and the dock cover Sam and Laia here
  const ws=$('#workspace');if(!ws||ws.querySelector('.desk'))return;
  if(!team){deskData().then(()=>{if(DESKS[view]===id&&!current)deskInject()});return}
  ws.insertAdjacentHTML('afterbegin',agentDesk(id,{compact:true,scope:view}));
}

async function deskAct(act){
  const [kind,...rest]=act.split(':'),arg=rest.join(':');
  if(kind==='view'&&(arg==='lessons'||arg==='tasks')){location.href='/'+arg;return}
  if(kind==='view'){if(!leave())return;current=null;view=arg;await refresh();render();scrollTo(0,0);return}
  if(kind==='project'){if(view==='today')view='board';openProject(arg);scrollTo(0,0);return}
  if(kind==='project-tab'){const [t,id]=[rest[0],rest.slice(1).join(':')];if(view==='today')view=t==='edit'?'editing':'board';openProject(id);if(current){tab=t;renderDetail();scrollTo(0,0)}return}
  if(kind==='carousel'){if(!leave())return;current=null;view='carousels';if(!carousels)carousels=deskCarousels||await api('/api/carousels');carouselOpen=arg;carouselSlide=0;render();scrollTo(0,0);return}
  if(kind==='email'){view='email';current=null;openEmail(arg);scrollTo(0,0);return}
  if(kind==='new'){if(arg==='carousel')return newCarousel();if(arg==='email'){view='email';return newEmail()}if(arg==='video')return showNew()}
}

document.addEventListener('click',e=>{
  const t=e.target.closest('[data-desk-toggle]');
  if(t){const id=t.dataset.deskToggle,box=t.closest('.desk'),min=!box.classList.contains('min');box.classList.toggle('min',min);t.setAttribute('aria-expanded',String(!min));try{localStorage.setItem('desk-min-'+id,min?'1':'0')}catch{}return}
  const a=e.target.closest('[data-desk-act]');if(a){e.stopPropagation();deskAct(a.dataset.deskAct)}
},true);

const deskBaseRender=render;
render=function(){
  if(!current&&view==='today'){renderTodayView();return}
  const r=deskBaseRender();deskInject();return r;
};

// Sam's briefing replaces the old fixed planning banner on the board and calendar.
if(typeof planningGuidance==='function')planningGuidance=()=>'';
