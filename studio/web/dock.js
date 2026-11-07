'use strict';
// The dock: the right team member follows Josh to every page, knows what he has open,
// and can change it. One conversation per team member, shared with their Team page.
const DOCK_AGENT={board:'sam',youtube:'sam',carousels:'sam',calendar:'sam',today:'sam',editing:'tariq',email:'laia'};
let dockOpen=(()=>{try{return localStorage.getItem('dock-open')==='1'}catch{return false}})(),dockStarted=0,dockTick=null,dockError=null;

function dockAgentId(){return current?(typeof tab!=='undefined'&&tab==='edit'?'tariq':'sam'):(DOCK_AGENT[view]||'sam')}
function dockContext(){
  if(view==='carousels'&&typeof carouselOpen!=='undefined'&&carouselOpen){const c=carouselFind(carouselOpen);return c?{kind:'carousel',id:c.id,label:c.title}:null}
  if(view==='carousels'&&typeof inspoOpen!=='undefined'&&inspoOpen){const p=inspoFind(inspoOpen);return p?{kind:'inspo',id:p.id,label:`${p.creator}'s post`}:null}
  if(current)return {kind:'project',id:current.id,label:current.name};
  const names={board:'YouTube videos',carousels:'Carousels',calendar:'Calendar',today:'Today',editing:'Editing',email:'Email'};
  return names[view]?{kind:'page',name:names[view],label:names[view]}:null;
}
function dockQuick(ctx){
  if(ctx?.kind==='carousel')return ['Tighten the hook','Write the caption','What would you change?','How is it doing?'];
  if(ctx?.kind==='inspo')return ['Why did this work?','Could we try this?','Tag this post'];
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
      <div class="sam-input"><textarea id="dock-input" rows="1" placeholder="${ctx?.kind==='carousel'?'Ask for a change to this post':`Message ${esc(a.name)}`}"></textarea><button class="primary" data-dock-send ${busy?'disabled':''} aria-label="Send">Send</button></div>
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
async function dockSend(message){
  message=(message||'').trim();const id=dockAgentId();if(!message||roomBusy)return;
  const ctx=dockContext();dockError=null;
  if(typeof carouselSaver!=='undefined')await carouselSaver.flush();
  roomThread[id]=[...(roomThread[id]||[]),{id:'pending',role:'josh',text:message,at:new Date().toISOString()}];
  roomBusy=id;dockStarted=Date.now();dockRender();
  clearInterval(dockTick);dockTick=setInterval(()=>{const c=document.getElementById('dock-clock');if(c)c.textContent=dockElapsed()},1000);
  try{
    const r=await api('/api/agents/chat',{agent_id:id,message,context:ctx&&{kind:ctx.kind,id:ctx.id,name:ctx.name}});
    roomThread[id]=r.messages;
    if(r.reply?.saved?.length)await dockRefresh();
  }catch(e){roomThread[id]=(roomThread[id]||[]).filter(m=>m.id!=='pending');dockError={error:e.message,message}}
  finally{clearInterval(dockTick);roomBusy=null;dockRender()}
}
// After the agent saves something, show the change on the page Josh is looking at.
async function dockRefresh(){
  try{
    if(typeof deskCarousels!=='undefined')deskCarousels=null;
    if(view==='carousels'){await carouselLoad();if(typeof inspo!=='undefined'&&inspo)await inspoLoad();if(!current)render()}
    else if(typeof carousels!=='undefined')carousels=null;
  }catch{}
}

document.addEventListener('click',e=>{
  if(e.target.closest('[data-dock-toggle]')){dockOpen=!dockOpen;try{localStorage.setItem('dock-open',dockOpen?'1':'0')}catch{}dockRender();if(dockOpen)document.getElementById('dock-input')?.focus();return}
  const q=e.target.closest('[data-dock-quick]');if(q){dockSend(q.dataset.dockQuick);return}
  if(e.target.closest('[data-dock-send]')){const i=document.getElementById('dock-input');const v=i.value;i.value='';dockSend(v);return}
  if(e.target.closest('[data-dock-retry]')){const m=dockError?.message;dockError=null;dockSend(m);return}
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
