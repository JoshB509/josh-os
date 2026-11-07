'use strict';
// YouTube board: four production lanes that fit on one screen come first, with the idea bank
// underneath. Each lane groups related stages, shown as a coloured sub-stage you can change on
// the card. Drag a card between lanes to move it on.
const YT_LANES=[
  {key:'preparing',label:'Preparing',hint:'Script, titles and thumbnail',stages:['Scripting'],drop:'Scripting'},
  {key:'recording',label:'Recording',hint:'Ready, recording, recorded',stages:['Ready to record','Recording','Recorded'],drop:'Ready to record'},
  {key:'editing',label:'Editing',hint:'In the edit, then your review',stages:['Editing','Review'],drop:'Editing'},
  {key:'scheduled',label:'Scheduled',hint:'Done and dated',stages:['Scheduled'],drop:'Scheduled'},
];
const YT_SUB={'Scripting':'Scripting','Ready to record':'Ready','Recording':'Recording','Recorded':'Recorded','Editing':'In the edit','Review':'Your review','Scheduled':'Scheduled'};
let ytQuery='',ytAllPublished=false;
const YT_ICONS={Script:'<path d="M10.5 2.5l3 3L6 13H3v-3z"/>',Title:'<path d="M3 4h10M8 4v9"/>',Thumbnail:'<rect x="2" y="3.5" width="12" height="9" rx="1.5"/><path d="m3 11.5 3.5-3.5 2.5 2.5 2-2 2 2"/>'};

function ytColor(stage){return colors[state.stages.indexOf(stage)]||'var(--faint)'}
function ytAgo(iso){if(!iso)return '';const d=Math.round((Date.now()-new Date(iso))/864e5);return d<1?'today':d===1?'yesterday':d<30?`${d} days ago`:new Date(iso).toLocaleDateString(undefined,{day:'numeric',month:'short'})}
function ytChecks(p){
  const words=p.script.trim()?p.script.trim().split(/\s+/).length:0;
  return [['Script',words>=150,words?`${words} word${words===1?'':'s'}`:'Not started'],['Title',!!(p.chosen_title||p.titles.length),p.chosen_title?'Chosen':p.titles.length?`${p.titles.length} ideas`:'None yet'],['Thumbnail',p.top_three.length>0,p.top_three.length?`${p.top_three.length} picked`:'None yet']];
}
function ytCard(p,lane){
  const thumb=p.top_three[0]||p.thumbnails[0]?.asset,sub=lane.stages.length>1;
  return `<article class="vcard" data-open="${esc(p.id)}" draggable="true" tabindex="0" style="--stage:${ytColor(p.stage)}">
    ${thumb?`<img class="vcard-thumb" loading="lazy" src="/assets/${esc(thumb)}?w=480" alt="">`:''}
    <div class="vcard-body">
      ${sub?`<label class="substage" title="Change stage"><i></i><select data-substage="${esc(p.id)}" aria-label="Stage of ${esc(p.name)}">${lane.stages.map(s=>`<option value="${esc(s)}" ${s===p.stage?'selected':''}>${esc(YT_SUB[s])}</option>`).join('')}</select></label>`:''}
      <h3>${esc(p.name)}</h3>
      <div class="vcard-meta"><span>${p.publish_date?esc(typeof deskWhen==='function'?'Out '+deskWhen(p.publish_date):p.publish_date):'No date yet'}</span>
        <span class="checks">${ytChecks(p).map(([k,ok,t])=>`<i class="${ok?'ok':''}" title="${k}: ${esc(t)}" aria-label="${k}: ${esc(t)}"><svg viewBox="0 0 16 16">${YT_ICONS[k]}</svg></i>`).join('')}</span></div>
    </div></article>`;
}
function ytIdea(p){
  const notes=(p.notes||'').trim();
  return `<article class="idea-tile" data-open="${esc(p.id)}" draggable="true" tabindex="0">
    <h3>${esc(p.name)}</h3>${notes?`<p>${esc(notes.slice(0,180))}</p>`:'<p class="idea-empty">No angle written yet</p>'}
    <footer><span>${esc(ytAgo(p.created_at))}</span><button class="small" data-promote="${esc(p.id)}">Start preparing →</button></footer></article>`;
}

const ytPrevBoard=renderBoard;
renderBoard=function(){
  if(view!=='board')return ytPrevBoard.apply(this,arguments);
  page('YouTube videos','');
  const q=ytQuery.toLowerCase(),match=p=>!q||(p.name+' '+(p.notes||'')).toLowerCase().includes(q);
  const all=state.projects.filter(p=>!p.archived),ideas=all.filter(p=>p.stage==='Idea'&&match(p));
  const published=all.filter(p=>p.stage==='Published'&&match(p)).sort((a,b)=>(b.publish_date||'').localeCompare(a.publish_date||''));
  const inLane=l=>all.filter(p=>l.stages.includes(p.stage)&&match(p)).sort((a,b)=>(a.publish_date||'9999').localeCompare(b.publish_date||'9999'));
  $('#workspace').innerHTML=`
  <div class="yt-tools"><input id="yt-search" type="search" placeholder="Search videos and ideas" value="${esc(ytQuery)}" aria-label="Search videos"><span class="count">${all.length-published.length} in progress · ${published.length} published</span></div>
  <section class="production" aria-label="In production">
    <header class="lane-head"><h2>In production</h2><p>Drag a video to the next lane when it moves on.</p></header>
    <div class="lanes">${YT_LANES.map(l=>{const items=inLane(l);return `<section class="lane" data-lane-drop="${esc(l.drop)}" data-lane="${l.key}">
      <header><b>${l.label}</b><span class="count">${items.length}</span><small>${l.hint}</small></header>
      ${items.map(p=>ytCard(p,l)).join('')||`<p class="lane-empty">Drop a video here</p>`}</section>`}).join('')}</div>
  </section>
  <section class="idea-bank" data-lane-drop="Idea" aria-label="Idea bank">
    <header class="lane-head"><h2>Ideas <span class="count">${ideas.length}</span></h2><p>Your holding area. Open one to shape the angle, then start preparing it.</p></header>
    <div class="idea-grid"><button class="idea-new" data-yt-new>＋ New idea</button>${ideas.map(ytIdea).join('')}</div>
  </section>
  ${published.length?`<section class="published"><header class="lane-head"><h2>Published <span class="count">${published.length}</span></h2>${published.length>4?`<button class="subtle small" data-yt-published>${ytAllPublished?'Show fewer':'Show all'}</button>`:''}</header>
    <div class="published-row">${(ytAllPublished?published:published.slice(0,4)).map(p=>{const t=p.top_three[0]||p.thumbnails[0]?.asset;return `<button class="pub-card" data-open="${esc(p.id)}">${t?`<img src="/assets/${esc(t)}?w=320" alt="">`:'<span class="pub-blank"></span>'}<b>${esc(p.name)}</b><small>${esc(formatDate(p.publish_date))}</small></button>`}).join('')}</div></section>`:''}`;
};

async function ytMove(id,stage){
  const p=state.projects.find(x=>x.id===id);if(!p||p.stage===stage)return;
  try{await api('/api/projects/'+id,{revision:p.revision,changes:{stage}},'PUT');await refresh();render();toast(`Moved to ${YT_SUB[stage]||stage}`)}catch(e){toast(e.message,true);await refresh();render()}
}
document.addEventListener('click',e=>{if(view!=='board'||current)return;
  if(e.target.closest('.substage')){e.stopPropagation();return}
  const promote=e.target.closest('[data-promote]');if(promote){e.stopPropagation();ytMove(promote.dataset.promote,'Scripting');return}
  if(e.target.closest('[data-yt-new]')){e.stopPropagation();showNew();return}
  if(e.target.closest('[data-yt-published]')){e.stopPropagation();ytAllPublished=!ytAllPublished;render()}
},true);
document.addEventListener('change',e=>{const s=e.target.closest('[data-substage]');if(s&&view==='board'&&!current)ytMove(s.dataset.substage,s.value)});
document.addEventListener('input',e=>{if(e.target.id!=='yt-search')return;ytQuery=e.target.value;const pos=e.target.selectionStart;render();const i=$('#yt-search');i.focus();i.setSelectionRange(pos,pos)});
document.addEventListener('dragover',e=>{const z=e.target.closest('[data-lane-drop]');if(!z||view!=='board')return;e.preventDefault();z.classList.add('over')});
document.addEventListener('dragleave',e=>{const z=e.target.closest('[data-lane-drop]');if(z&&!z.contains(e.relatedTarget))z.classList.remove('over')});
document.addEventListener('drop',e=>{const z=e.target.closest('[data-lane-drop]');if(!z||view!=='board')return;e.preventDefault();z.classList.remove('over');
  const id=e.dataTransfer.getData('text/plain'),p=state.projects.find(x=>x.id===id);if(!p)return;
  const lane=YT_LANES.find(l=>l.drop===z.dataset.laneDrop);
  ytMove(id,lane&&lane.stages.includes(p.stage)?p.stage:z.dataset.laneDrop);
});
