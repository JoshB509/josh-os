'use strict';
// Creators: the carousels we learn from (Ali Abdaal, Alex Hormozi), kept with their slides,
// likes, comments and the same tags as ours. Breakouts are worked out by Josh OS (at least
// twice the median likes of the creator's previous 20 posts). Sam adds and tags posts from chat.
let inspo=null,inspoOpen=null,inspoView='posts',inspoSort='newest',inspoFilter={creator:'',format:'',only:''};
const inspoSaver=makeAutosaver({url:p=>'/api/inspo/'+p.id,note:t=>{const n=$('#inspo-save');if(n)n.textContent=t},onSaved:()=>{}});
const INSPO_TAGS=['theme','format','style','hook_type','angle','cta_type','cta_place','caption_style'];

async function inspoLoad(){inspo=await api('/api/inspo')}
function inspoFind(id){return inspo?.items.find(p=>p.id===id)}
function inspoImg(p,i=0){return `/api/inspo/${encodeURIComponent(p.id)}/slide/${i}`}
function inspoDate(d){return d?new Date(d+'T12:00:00').toLocaleDateString(undefined,{day:'numeric',month:'short',year:'numeric'}):''}
function inspoMedian(a){if(!a.length)return null;const s=[...a].sort((x,y)=>x-y),m=Math.floor(s.length/2);return s.length%2?s[m]:(s[m-1]+s[m])/2}

function inspoTab(){
  if(!inspo)return '<p class="hint">Loading creator posts…</p>';
  const f=inspoFilter,all=inspo.items;
  const creators=inspo.creators.map(c=>{const ps=all.filter(p=>p.creator===c),likes=ps.slice(0,20).map(p=>p.likes).filter(n=>n!=null);
    return {c,n:ps.length,median:inspoMedian(likes),breakouts:ps.filter(p=>p.breakout).length,news:ps.filter(p=>p.new_thing).length,latest:ps[0]?.date,untagged:ps.filter(p=>!Object.keys(p.tags||{}).length).length}});
  const summary=`<div class="ins-creators">${creators.map(x=>`<button class="ins-creator ${f.creator===x.c?'on':''}" data-ins-creator="${esc(x.c)}">
      <b>${esc(x.c)}</b><span>${x.n?`${x.n} posts, up to ${esc(inspoDate(x.latest))}`:'Nothing saved yet'}</span>
      ${x.n?`<span>Median ${carouselNum(x.median==null?null:Math.round(x.median))} likes over the last 20 · ${x.breakouts} breakout${x.breakouts===1?'':'s'} · ${x.news} new thing${x.news===1?'':'s'}</span>`:''}
      ${x.untagged?`<span class="ins-warn">${x.untagged} not tagged yet</span>`:''}</button>`).join('')}</div>`;
  const sub=`<div class="cv-window ins-views" role="group" aria-label="Creator view"><button data-ins-view="posts" aria-pressed="${inspoView==='posts'}">Posts</button><button data-ins-view="patterns" aria-pressed="${inspoView==='patterns'}">Patterns</button></div>`;
  return summary+(inspoView==='patterns'?sub+inspoPatterns(all):sub+inspoPosts(all));
}

function inspoPosts(all){
  const f=inspoFilter,opt=carousels.tag_options||{};
  let list=all.filter(p=>(!f.creator||p.creator===f.creator)&&(!f.format||(p.tags||{}).format===f.format)&&(f.only!=='breakout'||p.breakout)&&(f.only!=='new'||p.new_thing));
  if(inspoSort==='likes')list=[...list].sort((a,b)=>(b.likes??-1)-(a.likes??-1));
  if(inspoSort==='comments')list=[...list].sort((a,b)=>(b.comments??-1)-(a.comments??-1));
  const tools=`<div class="cv-tools">
    <label class="cv-filter">Sort by<select data-ins-sort><option value="newest" ${inspoSort==='newest'?'selected':''}>Newest</option><option value="likes" ${inspoSort==='likes'?'selected':''}>Likes</option><option value="comments" ${inspoSort==='comments'?'selected':''}>Comments</option></select></label>
    <label class="cv-filter">Format<select data-ins-filter="format"><option value="">All</option>${(opt.format||[]).map(v=>`<option ${f.format===v?'selected':''}>${esc(v)}</option>`).join('')}</select></label>
    <label class="cv-filter">Show<select data-ins-filter="only"><option value="">Everything</option><option value="breakout" ${f.only==='breakout'?'selected':''}>Breakouts only</option><option value="new" ${f.only==='new'?'selected':''}>New things only</option></select></label>
    <p class="hint">Ask Sam to "check Ali and Hormozi for new carousels" and he adds them here.</p></div>`;
  if(!list.length)return tools+`<p class="hint">${all.length?'No posts match those filters.':'No creator posts saved yet.'}</p>`;
  return tools+`<div class="cv-grid">${list.map(p=>`<button class="cv-post" data-inspo-open="${esc(p.id)}">
    <div class="cv-cover tall">${p.images?`<img class="cv-img" src="${inspoImg(p)}" alt="" loading="lazy">`:`<div class="cv-text"><p>${esc((p.hook||'').slice(0,140))}</p></div>`}
      ${p.breakout?'<span class="cv-flag">Breakout</span>':''}${p.new_thing?`<span class="cv-flag ins-new" style="${p.breakout?'top:34px':''}">New thing</span>`:''}</div>
    <div class="cv-body"><span class="cv-sub">${esc(p.creator)} · ${esc(inspoDate(p.date))}</span><b>${esc(p.hook)}</b>
      <span class="cv-chips">${[p.tags?.format,p.tags?.theme].filter(Boolean).map(v=>`<span class="cv-chip">${esc(v)}</span>`).join('')}</span>
      <dl class="cv-stats"><div><dt>Likes</dt><dd>${carouselNum(p.likes)}</dd></div><div><dt>Comments</dt><dd>${carouselNum(p.comments)}</dd></div><div><dt>Slides</dt><dd>${p.images||p.slides||'–'}</dd></div></dl></div></button>`).join('')}</div>`;
}

function inspoPatterns(all){
  const tagged=all.filter(p=>Object.keys(p.tags||{}).length&&p.likes!=null);
  if(!tagged.length)return '<p class="hint">Patterns show once Sam has tagged the posts. Ask him to tag the saved creator posts.</p>';
  const table=key=>{
    const rows=[];for(const c of inspo.creators){const ps=tagged.filter(p=>p.creator===c);if(!ps.length)continue;
      const med=inspoMedian(ps.map(p=>p.likes)),g={};for(const p of ps){const v=p.tags[key];if(!v)continue;(g[v]=g[v]||[]).push(p)}
      for(const [v,xs] of Object.entries(g))rows.push({c,v,n:xs.length,median:inspoMedian(xs.map(p=>p.likes)),lift:med?inspoMedian(xs.map(p=>p.likes))/med:null,breakouts:xs.filter(p=>p.breakout).length})}
    if(!rows.length)return '';
    rows.sort((a,b)=>a.c.localeCompare(b.c)||(b.lift??0)-(a.lift??0));
    return `<section class="cv-group"><h3>${esc(CAROUSEL_TAG_LABELS[key]||key)}</h3><table><thead><tr><th></th><th>Creator</th><th>Posts</th><th>Median likes</th><th>vs their usual</th><th>Breakouts</th></tr></thead>
      <tbody>${rows.map(r=>`<tr class="${r.n<3?'thin':''}"><th>${esc(r.v)}</th><td>${esc(r.c.split(' ')[0])}</td><td>${r.n}</td><td>${carouselNum(r.median)}</td><td class="${r.n>=3&&r.lift>=1.3?'best':''}">${r.lift?r.lift.toFixed(1)+'×':'–'}</td><td>${r.breakouts||''}</td></tr>`).join('')}</tbody></table></section>`;
  };
  const news=all.filter(p=>p.new_thing);
  return `<p class="hint">Median likes for each tag against that creator's usual median. Above 1.3× is highlighted, and groups with fewer than 3 posts are greyed out.</p>
    <div class="cv-groups">${['format','hook_type','theme','style','angle','cta_type'].map(table).join('')}</div>
    ${news.length?`<section class="cv-group ins-news"><h3>New things they tried</h3><ul>${news.map(p=>`<li><button class="link" data-inspo-open="${esc(p.id)}">${esc(p.creator)}, ${esc(inspoDate(p.date))}</button> ${esc(p.new_note||'No note yet.')} <span class="cv-sub">${carouselNum(p.likes)} likes${p.breakout?', a breakout':''}</span></li>`).join('')}</ul></section>`:''}`;
}

function renderInspoPost(){
  const p=inspoFind(inspoOpen);if(!p){inspoOpen=null;return renderCarousels()}
  const t=p.tags||{},opt=carousels.tag_options||{};
  page(p.hook.slice(0,80),'Creator post');subPage('Carousels',p.creator,'data-inspo-back');
  const sel=k=>`<label>${esc(CAROUSEL_TAG_LABELS[k])}<select data-ins-tag="${k}"><option value="">Not set</option>${(opt[k]||[]).map(v=>`<option ${t[k]===v?'selected':''}>${esc(v)}</option>`).join('')}</select></label>`;
  const txt=k=>`<label>${esc(CAROUSEL_TAG_LABELS[k])}<input data-ins-tag="${k}" value="${esc(t[k]??'')}"></label>`;
  $('#workspace').innerHTML=`<div class="crumb"><button data-inspo-back>← Back to creators</button></div>
  <div class="ins-head"><div><span class="cv-sub">${esc(p.creator)} · ${esc(inspoDate(p.date))}${p.url?` · <a href="${esc(p.url)}" target="_blank" rel="noopener">Open on YouTube</a>`:''}</span><h2>${esc(p.hook)}</h2></div>
    <dl class="cv-stats ins-stats"><div><dt>Likes</dt><dd>${carouselNum(p.likes)}</dd></div><div><dt>Comments</dt><dd>${carouselNum(p.comments)}</dd></div><div><dt>Their usual</dt><dd>${carouselNum(p.median_before==null?null:Math.round(p.median_before))}</dd></div></dl>
    ${p.breakout?'<span class="cv-flag ins-inline">Breakout</span>':''}<span id="inspo-save" class="save-note">Saved</span></div>
  ${p.images?`<div class="cv-strip" aria-label="Slides">${Array.from({length:p.images},(_,i)=>`<a href="${inspoImg(p,i)}" target="_blank" rel="noopener" class="cv-slide"><img src="${inspoImg(p,i)}" alt="Slide ${i+1}" loading="lazy"><span>${i+1}</span></a>`).join('')}</div>`:'<p class="hint">No slide images saved for this post.</p>'}
  <div class="carousel-editor">
    <section>
      <label class="check"><input type="checkbox" data-ins-field="new_thing" ${p.new_thing?'checked':''}>Trying something new</label>
      <label>What's new, and did it work<textarea data-ins-field="new_note" rows="2">${esc(p.new_note||'')}</textarea></label>
      <label>Visual look<input data-ins-field="look" value="${esc(p.look||'')}" placeholder="e.g. white background, big black serif headline, one icon per slide"></label>
      <label>Notes<textarea data-ins-field="notes" rows="3">${esc(p.notes||'')}</textarea></label>
      <h4>Caption</h4><div class="ins-caption">${esc(p.caption||'No caption saved.')}</div>
    </section>
    <aside><details class="cv-section" open><summary>Tags</summary><div class="cv-tags">
      ${sel('theme')}${txt('topic')}${sel('format')}${sel('style')}${sel('hook_type')}
      <label class="check"><input type="checkbox" data-ins-flag="hook_number" ${t.hook_number?'checked':''}>Number in the hook</label>
      ${sel('angle')}${sel('cta_type')}${txt('lead_magnet')}${sel('cta_place')}${sel('caption_style')}
    </div></details>
    <p class="hint">Likes and comments as of ${esc(p.checked_at||'the day it was saved')}.</p></aside>
  </div>`;
}
function inspoTag(p,k,v){const t={...(p.tags||{})};if(v===''||v==null||v===false)delete t[k];else t[k]=v;inspoSaver.change(p,'tags',t)}

document.addEventListener('click',async e=>{
  const open=e.target.closest('[data-inspo-open]');
  if(open){e.preventDefault();if(typeof leave==='function'&&!leave())return;current=null;view='carousels';carouselOpen=null;carouselTab='creators';inspoOpen=open.dataset.inspoOpen;
    if(!carousels)await carouselLoad();if(!inspo||!inspoFind(inspoOpen))await inspoLoad();render();scrollTo(0,0);return}
  if(view!=='carousels')return;
  if(e.target.closest('[data-inspo-back]')){await inspoSaver.flush();inspoOpen=null;try{await inspoLoad()}catch{}render();return}
  const cr=e.target.closest('[data-ins-creator]');if(cr){inspoFilter.creator=inspoFilter.creator===cr.dataset.insCreator?'':cr.dataset.insCreator;inspoView='posts';renderCarousels();return}
  const vw=e.target.closest('[data-ins-view]');if(vw){inspoView=vw.dataset.insView;renderCarousels();return}
});
document.addEventListener('change',e=>{
  if(view!=='carousels')return;const el=e.target;
  if(!inspoOpen){
    if(el.dataset.insSort!==undefined){inspoSort=el.value;renderCarousels();return}
    if(el.dataset.insFilter){inspoFilter[el.dataset.insFilter]=el.value;renderCarousels();return}
    return;
  }
  const p=inspoFind(inspoOpen);if(!p)return;
  if(el.dataset.insTag&&el.tagName==='SELECT')inspoTag(p,el.dataset.insTag,el.value);
  if(el.dataset.insFlag)inspoTag(p,el.dataset.insFlag,el.checked);
  if(el.dataset.insField==='new_thing')inspoSaver.change(p,'new_thing',el.checked);
});
document.addEventListener('input',e=>{
  if(view!=='carousels'||!inspoOpen)return;const el=e.target,p=inspoFind(inspoOpen);if(!p)return;
  if(el.dataset.insTag&&el.tagName==='INPUT')inspoTag(p,el.dataset.insTag,el.value.trim());
  if(el.dataset.insField&&el.dataset.insField!=='new_thing')inspoSaver.change(p,el.dataset.insField,el.value);
});
// Leaving the Carousels page closes any open creator post.
const inspoBaseRender=render;
render=function(){if(view!=='carousels')inspoOpen=null;return inspoBaseRender()};
