'use strict';
// The dock: the right team member follows Josh to every page, knows what he has open,
// and can change it. One conversation per team member, shared with their Team page.
const DOCK_AGENT={success:'anouk','edit-review':'tariq','edit-flow':'tariq',courses:'saskia',board:'sam',youtube:'sam',carousels:'sam',calendar:'sam',today:'sam',editing:'tariq',email:'laia'};
let dockOpen=(()=>{try{return localStorage.getItem('dock-open')==='1'}catch{return false}})(),dockStarted=0,dockTick=null,dockError=null;

function dockAgentId(){return current?(typeof tab!=='undefined'&&tab==='edit'?'tariq':'sam'):(DOCK_AGENT[view]||'sam')}
function dockContext(){
  if(view==='edit-review'&&typeof erPid!=='undefined'&&erPid){const p=erParts()[erAt];return {kind:'edit',id:erPid,part:erAt,label:p?(p.kind==='talk'?'the talking head part':`Animation ${p.n}`):'the animation deck'}}
  if(view==='carousels'&&typeof carouselOpen!=='undefined'&&carouselOpen){const c=carouselFind(carouselOpen);return c?{kind:'carousel',id:c.id,label:c.title}:null}
  if(view==='carousels'&&typeof inspoOpen!=='undefined'&&inspoOpen){const p=inspoFind(inspoOpen);return p?{kind:'inspo',id:p.id,label:`${p.creator}'s post`}:null}
  if(view==='courses'&&typeof courseOpen!=='undefined'&&courseOpen)return {kind:'lesson',id:courseOpen.id,label:`${courseOpen.number} ${courseOpen.title}`};
  if(view==='success'&&typeof personOpen!=='undefined'&&personOpen){const p=personFind(personOpen);return p?{kind:'person',id:p.id,label:p.name||p.email}:null}
  if(view==='email'&&typeof emailCurrent!=='undefined'&&emailCurrent)return {kind:'email',id:emailCurrent.id,label:emailCurrent.subject||'this email'};
  if(current)return {kind:'project',id:current.id,label:current.name};
  const names={courses:'Lessons',board:'YouTube videos',carousels:'Carousels',calendar:'Calendar',today:'Today',editing:'Editing',email:'Email'};
  return names[view]?{kind:'page',name:names[view],label:names[view]}:null;
}
function dockQuick(ctx){
  if(ctx?.kind==='edit')return ['Make this one land faster','Move a press to a better word','What would you change here?'];
  if(ctx?.kind==='carousel')return ['Tighten the hook','Write the caption','What would you change?','How is it doing?'];
  if(ctx?.kind==='inspo')return ['Why did this work?','Could we try this?','Tag this post'];
  if(ctx?.kind==='lesson')return ['What does this lesson need next?','Tighten the hook','Check it against the lesson rules'];
  if(ctx?.kind==='person')return ['Where are we with them?','Draft my next message to them','What did I promise them?'];
  if(view==='success')return ['Who needs me this week?','Who haven\'t we heard from?','What is overdue?'];
  if(view==='courses')return ['What should we work on next?','Which lessons are ready to record?'];
  if(ctx?.kind==='project')return ['What does this video need next?','Ideas for a carousel from this'];
  if(view==='carousels')return ['What should we post next?','Draft three carousels','Check Ali and Hormozi for new carousels'];
  return ['What are you working on?','What needs me today?'];
}
// The dock hides where a full chat with the same person is already on screen.
function dockHidden(){return !!(document.getElementById('room-chat')||document.getElementById('sam-panel'))}

function dockRender(){
  let root=document.getElementById('dock');
  if(!root){root=document.createElement('div');root.id='dock';document.body.appendChild(root)}
  const a=typeof teamAgent==='function'&&team?teamAgent(dockAgentId()):null;
  if(!a||dockHidden()){root.hidden=true;document.body.classList.remove('dock-on');return}
  root.hidden=false;root.style.setProperty('--agent',a.color);
  const ctx=dockContext(),busy=roomBusy===a.id,msgs=roomThread[a.id]||[],loaded=roomLoaded[a.id];
  root.className='dock'+(dockOpen?' open':'');document.body.classList.toggle('dock-on',dockOpen);
  if(!dockOpen){root.innerHTML=`<button class="dock-fab" data-dock-toggle aria-label="Chat with ${esc(a.name)}">${teamAvatar(a,'sm')}<span>${busy?`${esc(a.name)} is working`:`Ask ${esc(a.name)}`}</span>${busy?'<i class="dock-pulse"></i>':''}</button>`;return}
  const input=document.getElementById('dock-input'),draft=input?input.value:'',focused=document.activeElement===input;
  root.innerHTML=`<section class="dock-panel" role="dialog" aria-label="Chat with ${esc(a.name)}">
    <header class="dock-head">${teamAvatar(a,'sm')}<div><b>${esc(a.name)}</b><small>${ctx?.kind&&ctx.kind!=='page'?`Looking at ${esc(ctx.label)}`:esc(a.role)}</small></div>
      <button class="dock-close" data-dock-toggle aria-label="Close chat">✕</button></header>
    <div class="dock-thread" id="dock-thread" aria-live="polite">${!loaded?'<p class="hint">Loading…</p>':msgs.length?roomMessages(a,msgs.slice(-30)):`<p class="dock-empty">${esc(a.name)} can see what you have open. Ask for a change and it's made here.</p>`}
      ${busy?`<div class="room-working"><span class="dots"><i></i><i></i><i></i></span><span>${esc(a.name)} is working <b id="dock-clock">${dockElapsed()}</b></span></div>`:''}
      ${dockError?`<p class="room-failed">Didn't go through. ${esc(dockError.error)} <button class="link" data-dock-retry>Try again</button></p>`:''}</div>
    <div class="dock-compose">
      <div class="room-chips">${dockQuick(ctx).map(q=>`<button class="chip-btn" data-dock-quick="${esc(q)}" ${busy?'disabled':''}>${esc(q)}</button>`).join('')}</div>
      ${chatAttachStrip('dock')}
      <div class="sam-input">${chatAttachButton('dock',false)}<textarea id="dock-input" rows="1" placeholder="${ctx?.kind==='carousel'?'Ask for a change to this post':`Message ${esc(a.name)}`}"></textarea><button class="primary" data-dock-send ${busy?'disabled':''} aria-label="Send">Send</button></div>
    </div></section>`;
  const i=document.getElementById('dock-input');if(i){i.value=draft;roomGrow(i);if(focused)i.focus()}
  const t=document.getElementById('dock-thread');if(t)t.scrollTop=t.scrollHeight;
  if(!loaded)dockLoad(a.id);
}
function dockElapsed(){const t=Math.max(0,Math.round((Date.now()-dockStarted)/1000));return `${Math.floor(t/60)}:${String(t%60).padStart(2,'0')}`}
async function dockLoad(id){
  try{roomThread[id]=await api(`/api/agents/thread?project=desk&agent=${encodeURIComponent(id)}`)}catch{roomThread[id]=roomThread[id]||[]}
  roomLoaded[id]=true;dockRender();
}
async function dockSend(message,images=null){
  message=(message||'').trim();const id=dockAgentId();if(roomBusy)return;
  if(chatUploading.dock&&!images){toast('Wait a moment for the images to finish adding.',true);return}
  if(!message&&!(images||chatAttach.dock).length)return;
  images=images||chatTake('dock');
  const ctx=dockContext();dockError=null;
  if(typeof carouselSaver!=='undefined')await carouselSaver.flush();
  roomThread[id]=[...(roomThread[id]||[]),{id:'pending',role:'josh',text:message,images,at:new Date().toISOString()}];
  roomBusy=id;dockStarted=Date.now();dockRender();
  clearInterval(dockTick);dockTick=setInterval(()=>{const c=document.getElementById('dock-clock');if(c)c.textContent=dockElapsed()},1000);
  try{
    const r=await api('/api/agents/chat',{agent_id:id,message,images,context:ctx&&{kind:ctx.kind,id:ctx.id,name:ctx.name,part:ctx.part}});
    roomThread[id]=r.messages;
    if(typeof roomWatchJobs==='function')roomWatchJobs(id,r.reply?.saved);
    if(r.reply?.saved?.length)await dockRefresh(r.reply.saved);
  }catch(e){roomThread[id]=(roomThread[id]||[]).filter(m=>m.id!=='pending');dockError={error:e.message,message,images}}
  finally{clearInterval(dockTick);roomBusy=null;dockRender()}
}
// After an agent saves something from any chat, show the latest version of it on the page Josh is looking at.
// Anything Josh is in the middle of editing and hasn't saved is left alone.
async function dockRefresh(saved=[]){
  const kinds=new Set(saved.filter(x=>x.action!=='failed').map(x=>x.kind||'carousel')),ids=new Set(saved.map(x=>x.id));
  try{
    if(kinds.has('edit')&&view==='edit-review'&&typeof erPid!=='undefined'&&erPid){erData=await api(`/api/projects/${encodeURIComponent(erPid)}/edit-review`);renderEditReview()}
    if(kinds.has('carousel')||kinds.has('inspo')){
      if(typeof deskCarousels!=='undefined')deskCarousels=null;
      if(view==='carousels'){if(typeof carouselSaver!=='undefined')await carouselSaver.flush();await carouselLoad();if(typeof inspo!=='undefined'&&inspo)await inspoLoad();if(!current)render()}
      else if(typeof carousels!=='undefined')carousels=null;
    }
    if(kinds.has('email')&&typeof emailRows!=='undefined'){
      emailRows=await api('/api/emails');
      if(emailCurrent&&ids.has(emailCurrent.id)&&!dirty){const f=emailRows.find(p=>p.id===emailCurrent.id);if(f)emailCurrent=structuredClone(f)}
      if(view==='email'&&!current)render();
    }
    if(kinds.has('lesson')&&typeof courses!=='undefined'){
      courses=null;
      if(view==='courses'&&!current){const pending=courseQueue.pending&&Object.keys(courseQueue.pending).length;if(courseOpen&&!pending)await courseFetch(courseOpen.id);render()}
    }
    if((kinds.has('project')||kinds.has('video'))&&current&&ids.has(current.id)&&!dirty){
      const id=current.id;await refresh();const p=state.projects.find(x=>x.id===id);
      if(p&&current?.id===id&&!dirty){current=structuredClone(p);if(tab==='results'&&typeof resLoad==='function')await resLoad();else renderDetail()}
    }
  }catch{}
}

document.addEventListener('click',e=>{
  if(e.target.closest('[data-dock-toggle]')){dockOpen=!dockOpen;try{localStorage.setItem('dock-open',dockOpen?'1':'0')}catch{}dockRender();if(dockOpen)document.getElementById('dock-input')?.focus();return}
  const q=e.target.closest('[data-dock-quick]');if(q){dockSend(q.dataset.dockQuick);return}
  const ask=e.target.closest('[data-dock-ask]');if(ask){dockOpen=true;try{localStorage.setItem('dock-open','1')}catch{}dockRender();dockSend(ask.dataset.dockAsk);return}
  if(e.target.closest('[data-dock-send]')){const i=document.getElementById('dock-input');const v=i.value;i.value='';dockSend(v);return}
  if(e.target.closest('[data-dock-retry]')){const m=dockError?.message,im=dockError?.images||[];dockError=null;dockSend(m,im);return}
});
document.addEventListener('keydown',e=>{
  if(e.target.id==='dock-input'&&e.key==='Enter'&&!e.shiftKey&&!e.isComposing){e.preventDefault();if(roomBusy)return;const v=e.target.value;e.target.value='';roomGrow(e.target);dockSend(v)}
  if(e.key==='Escape'&&dockOpen&&document.getElementById('dock')?.contains(document.activeElement)){dockOpen=false;dockRender()}
});
document.addEventListener('input',e=>{if(e.target.id==='dock-input')roomGrow(e.target)});

// Follow Josh: redraw after every page render.
const dockBaseRender=render;
render=function(){const r=dockBaseRender.apply(this,arguments);Promise.resolve(r).finally(()=>setTimeout(dockRender,0));return r};
(async()=>{try{if(!team)team=await api('/api/team')}catch{}dockRender()})();
// Pages that redraw without render() (opening a video, switching its tabs) still move the dock along.
(()=>{const w=document.getElementById('workspace');if(!w)return;let t=null;new MutationObserver(()=>{clearTimeout(t);t=setTimeout(dockRender,80)}).observe(w,{childList:true})})();

// Learning from Josh's edits. When he changes a team member's writing (a carousel's words,
// an email, a script) and then moves on, that team member looks at what changed and writes
// to him first: what they'll do from now on, and a question about anything they can't work out.
const DOCK_WATCH={carousels:{fields:['title','hook','slides','caption']},emails:{fields:['subject','preview','body'],agent:'laia'},projects:{fields:['script'],agent:'sam'}};
let dockTouched={},dockUnread={},dockIdle=null;
const dockBaseApi=api;
api=async function(path,data,method){
  const out=await dockBaseApi.apply(this,arguments);
  try{
    const m=method==='PUT'&&/^\/api\/(carousels|emails|projects)\/([\w-]+)$/.exec(path);
    if(m&&data?.changes&&DOCK_WATCH[m[1]].fields.some(f=>f in data.changes)){
      const agent=DOCK_WATCH[m[1]].agent||(m[1]==='carousels'?(carouselFind?.(m[2])?.owner||'sam'):'sam');
      if(agent!=='josh'){dockTouched[m[1]+':'+m[2]]={id:m[2],agent};clearTimeout(dockIdle);dockIdle=setTimeout(()=>dockReflectAll(true),5*60*1000)}
    }
  }catch{}
  return out;
};
function dockOpenKey(){const c=dockContext();return c&&c.id?({carousel:'carousels',email:'emails',project:'projects'}[c.kind]||'')+':'+c.id:''}
// Called on every redraw: anything Josh touched that he is no longer looking at gets reflected on.
function dockReflectLeft(){const open=dockOpenKey();for(const k of Object.keys(dockTouched))if(k!==open)dockReflect(k)}
function dockReflectAll(){for(const k of Object.keys(dockTouched))dockReflect(k)}
async function dockReflect(key){
  const t=dockTouched[key];if(!t)return;
  if(roomBusy){setTimeout(()=>dockReflect(key),20000);return}
  delete dockTouched[key];
  if(typeof carouselSaver!=='undefined')await carouselSaver.flush().catch(()=>{});
  try{
    const r=await dockBaseApi('/api/agents/reflect',{agent_id:t.agent});
    if(r.reply){roomThread[t.agent]=r.messages;roomLoaded[t.agent]=true;dockUnread[t.agent]=true;
      const a=team&&teamAgent(t.agent);toast(`${a?.name||'Your team'} has a note about your changes`);dockRender()}
  }catch{}
}
const dockBaseRender2=dockRender;
dockRender=function(){
  dockBaseRender2();
  try{
    dockReflectLeft();
    const a=team&&teamAgent(dockAgentId()),root=document.getElementById('dock');if(!a||!root||root.hidden)return;
    if(dockOpen)dockUnread[a.id]=false;
    const fab=root.querySelector('.dock-fab');
    if(fab&&dockUnread[a.id]){fab.classList.add('unread');const sp=fab.querySelector('span');if(sp)sp.textContent=`${a.name} has a question`}
  }catch{}
};
addEventListener('beforeunload',()=>{for(const [k,t] of Object.entries(dockTouched)){try{navigator.sendBeacon&&fetch('/api/agents/reflect',{method:'POST',keepalive:true,headers:{'X-DJ-Token':token,'Content-Type':'application/json'},body:JSON.stringify({agent_id:t.agent})})}catch{}}});
