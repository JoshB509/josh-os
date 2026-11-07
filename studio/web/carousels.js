'use strict';
// Carousels: every carousel from idea to posted, with its slides, tags and results.
// Board shows the pipeline, Posted shows each post with its numbers, and What's working
// groups the posts by tag so the team can see which hooks, formats and styles do best.
// Slides are written as plain text with a line of three dashes between slides. Rendered
// slide images come from the carousel's folder on the E drive.
let carousels=null,carouselOpen=null,carouselTab='board',carouselSort='newest',carouselWindow='d7',carouselFilter={};
const carouselSaver=makeAutosaver({url:c=>'/api/carousels/'+c.id,note:t=>{const n=$('#carousel-save');if(n)n.textContent=t},onSaved:()=>{}});
const CAROUSEL_TAG_LABELS={theme:'Theme',topic:'Topic',hook_type:'Hook type',hook_text:'Hook text',hook_number:'Number in the hook',angle:'Angle',format:'Format',style:'Style',slide_count:'Slides',cta_type:'Call to action',lead_magnet:'Lead magnet',cta_place:'Where the call to action sits',caption_style:'Caption style',post_time:'Post time (Dubai)',video_same_day:'Video out the same day',testing:'What it tests',idea_source:'Idea came from',tracked_link:'Tracked link'};
const CAROUSEL_RESULT_LABELS={views:'Views',likes:'Likes',comments:'Comments',clicks:'Clicks',downloads:'Downloads',new_people:'New people',course_clicks:'Course clicks',sale_value:'Sale it started ($)'};
const CAROUSEL_GROUPS=['style','format','theme','hook_type','angle','cta_type','caption_style','idea_source'];

function carouselSlides(text){return (text||'').split(/\n-{3,}\n?/).map(s=>s.trim()).filter(Boolean)}
function carouselFind(id){return carousels.items.find(c=>c.id===id)}
function carouselDate(d){return d?new Date(d+'T12:00:00').toLocaleDateString(undefined,{weekday:'short',day:'numeric',month:'short'}):'No date'}
function carouselImg(c,i=0){return `/api/carousels/${encodeURIComponent(c.id)}/slide/${i}?v=${encodeURIComponent(c.updated_at||'')}`}
async function carouselLoad(){carousels=await api('/api/carousels')}
function carouselNum(n){if(n==null||n==='')return '–';n=+n;return n>=10000?(n/1000).toFixed(0)+'k':n>=1000?(n/1000).toFixed(1)+'k':Number.isInteger(n)?String(n):n.toFixed(1)}
function carouselRes(c,w=carouselWindow){return (c.results||{})[w]||{}}
function carouselRate(r,key){return r.views&&r[key]!=null?r[key]/r.views*1000:null}
function carouselCover(c,size=''){
  if(c.images)return `<img class="cv-img" src="${carouselImg(c)}" alt="" loading="lazy">`;
  const first=carouselSlides(c.slides)[0]||c.hook||c.title;
  return `<div class="cv-text ${size}"><p>${esc(first.replace(/\*\*/g,'').slice(0,140))}</p></div>`;
}
function carouselChips(c){const t=c.tags||{};return [t.style,t.format,t.theme].filter(Boolean).map(v=>`<span class="cv-chip">${esc(v)}</span>`).join('')}

async function renderCarousels(){
  page('Carousels','Every carousel with its slides, tags and results, from idea to posted.');
  if(!carousels){$('#workspace').innerHTML='<p class="hint">Loading carousels…</p>';try{await carouselLoad()}catch(e){toast(e.message,true);return}if(view!=='carousels'||current)return}
  if(carouselOpen)return renderCarouselEditor();
  if(inspoOpen)return renderInspoPost();
  if(carouselTab==='creators'&&!inspo){try{await inspoLoad()}catch(e){toast(e.message,true)}if(view!=='carousels'||current)return}
  const live=carousels.items.filter(c=>!c.archived),week=new Date();week.setDate(week.getDate()+7);
  const posted=live.filter(c=>c.stage==='Posted'),queue=live.filter(c=>c.stage!=='Posted');
  const dueSoon=queue.filter(c=>c.post_date&&new Date(c.post_date+'T23:59:59')<=week).length;
  const ahead=(()=>{let d=new Date(),n=0;for(;n<60;n++){const k=d.toISOString().slice(0,10);if(!live.some(c=>c.post_date===k&&['Scheduled','Posted'].includes(c.stage)))break;d.setDate(d.getDate()+1)}return n})();
  const tabs=[['board','Board',queue.length],['posted','Posted',posted.length],['working',"What's working",''],['creators','Creators',inspo?inspo.items.length:'']];
  $('#workspace').innerHTML=`<div class="carousel-summary">
      <div><b>${ahead}</b><span>days scheduled ahead</span></div>
      <div><b>${queue.length}</b><span>in progress</span></div>
      <div><b>${dueSoon}</b><span>due in the next 7 days</span></div>
      <div><b>${posted.length}</b><span>posted</span></div></div>
    <div class="cv-tabs" role="tablist">${tabs.map(t=>`<button role="tab" aria-selected="${t[0]===carouselTab}" data-cv-tab="${t[0]}">${esc(t[1])}${t[2]!==''?` <span>${t[2]}</span>`:''}</button>`).join('')}</div>
    <div id="cv-body">${carouselTab==='posted'?carouselPosted(posted):carouselTab==='working'?carouselWorking(posted):carouselTab==='creators'?inspoTab():carouselBoard(queue)}</div>`;
}

function carouselBoard(queue){
  return `<div class="carousel-board">${carousels.stages.filter(s=>s!=='Posted').map(stage=>{const items=queue.filter(c=>c.stage===stage).sort((a,b)=>(a.post_date||'9999').localeCompare(b.post_date||'9999'));
    return `<section class="carousel-column"><h3>${esc(stage)} <span class="count">${items.length}</span></h3>${items.map(c=>`
      <button class="cv-card" data-carousel-open="${esc(c.id)}"><div class="cv-cover">${carouselCover(c,'sm')}</div>
        <div class="cv-body"><b>${esc(c.title)}</b><span class="cv-sub">${esc(carouselDate(c.post_date))}${c.tags?.post_time?' · '+esc(c.tags.post_time):''} · ${c.images||carouselSlides(c.slides).length} slides</span>
        <span class="cv-chips">${carouselChips(c)}</span></div></button>`).join('')||'<p class="empty-column">Nothing here</p>'}</section>`}).join('')}</div>`;
}

function carouselPosted(posted){
  const opt=carousels.tag_options||{},f=carouselFilter;
  let list=posted.filter(c=>Object.entries(f).every(([k,v])=>!v||(c.tags||{})[k]===v));
  const val=c=>{const r=carouselRes(c);return carouselSort==='views'?r.views??-1:carouselSort==='likes'?carouselRate(r,'likes')??-1:carouselSort==='people'?carouselRate(r,'new_people')??-1:carouselSort==='comments'?carouselRate(r,'comments')??-1:0};
  list=carouselSort==='newest'?list.sort((a,b)=>(b.post_date||'').localeCompare(a.post_date||'')):list.sort((a,b)=>val(b)-val(a));
  const views=list.map(c=>carouselRes(c).views).filter(v=>v!=null).sort((a,b)=>a-b),median=views.length?views[Math.floor(views.length/2)]:null;
  const sel=(k,label)=>`<label class="cv-filter">${esc(label)}<select data-cv-filter="${k}"><option value="">All</option>${(opt[k]||[]).map(v=>`<option ${f[k]===v?'selected':''}>${esc(v)}</option>`).join('')}</select></label>`;
  return `<div class="cv-tools">
      <label class="cv-filter">Sort by<select data-cv-sort><option value="newest" ${carouselSort==='newest'?'selected':''}>Newest</option><option value="views" ${carouselSort==='views'?'selected':''}>Views</option><option value="likes" ${carouselSort==='likes'?'selected':''}>Likes per 1,000 views</option><option value="comments" ${carouselSort==='comments'?'selected':''}>Comments per 1,000 views</option><option value="people" ${carouselSort==='people'?'selected':''}>New people per 1,000 views</option></select></label>
      ${sel('style','Style')}${sel('format','Format')}${sel('theme','Theme')}
      <div class="cv-window" role="group" aria-label="Results window"><button data-cv-window="d1" aria-pressed="${carouselWindow==='d1'}">1 day</button><button data-cv-window="d7" aria-pressed="${carouselWindow==='d7'}">7 days</button></div>
    </div>
    ${list.length?`<div class="cv-grid">${list.map(c=>{const r=carouselRes(c),top=median!=null&&r.views!=null&&views.length>=4&&r.views>=median*2;
      return `<button class="cv-post" data-carousel-open="${esc(c.id)}"><div class="cv-cover tall">${carouselCover(c)}${top?'<span class="cv-flag">Breakout</span>':''}</div>
        <div class="cv-body"><b>${esc(c.title)}</b><span class="cv-sub">${esc(carouselDate(c.post_date))}</span><span class="cv-chips">${carouselChips(c)}</span>
        <dl class="cv-stats"><div><dt>Views</dt><dd>${carouselNum(r.views)}</dd></div><div><dt>Likes /1k</dt><dd>${carouselNum(carouselRate(r,'likes'))}</dd></div><div><dt>New people</dt><dd>${carouselNum(r.new_people)}</dd></div></dl>
        ${r.views==null&&Object.keys(r).length?'<span class="cv-sub">No views yet, so no per 1,000 numbers</span>':!Object.keys(r).length?`<span class="cv-sub">No ${carouselWindow==='d1'?'1 day':'7 day'} results yet</span>`:''}</div></button>`}).join('')}</div>`
    :`<p class="hint">${posted.length?'No posts match those filters.':'Nothing posted yet. Once Sam marks a carousel as Posted, it shows here with its results.'}</p>`}`;
}

function carouselWorking(posted){
  const w=carouselWindow,rows=posted.map(c=>({c,r:carouselRes(c,w)})).filter(x=>x.r.views);
  const head=`<div class="cv-tools"><p class="hint">Averages per 1,000 views, from posts with ${w==='d1'?'1 day':'7 day'} views recorded. Groups with fewer than 3 posts are greyed out, since one post can swing them.</p>
    <div class="cv-window" role="group" aria-label="Results window"><button data-cv-window="d1" aria-pressed="${w==='d1'}">1 day</button><button data-cv-window="d7" aria-pressed="${w==='d7'}">7 days</button></div></div>`;
  if(!rows.length)return head+'<p class="hint">No results with views yet. Sam adds views, likes, comments and Linklr numbers to each post at 1 day and 7 days, and this fills in from there.</p>';
  const avg=a=>a.length?a.reduce((x,y)=>x+y,0)/a.length:null;
  const table=key=>{
    const groups={};for(const x of rows){const v=(x.c.tags||{})[key];if(v==null||v==='')continue;(groups[v]=groups[v]||[]).push(x)}
    const list=Object.entries(groups).map(([v,xs])=>({v,n:xs.length,views:avg(xs.map(x=>x.r.views)),likes:avg(xs.map(x=>carouselRate(x.r,'likes')).filter(n=>n!=null)),comments:avg(xs.map(x=>carouselRate(x.r,'comments')).filter(n=>n!=null)),people:avg(xs.map(x=>carouselRate(x.r,'new_people')).filter(n=>n!=null))}));
    if(!list.length)return '';
    const best=k=>Math.max(...list.filter(g=>g.n>=3&&g[k]!=null).map(g=>g[k]));
    const b={likes:best('likes'),people:best('people'),comments:best('comments')};
    list.sort((a,b2)=>(b2.likes??-1)-(a.likes??-1));
    const cell=(g,k)=>`<td class="${g.n>=3&&g[k]===b[k]&&isFinite(b[k])?'best':''}">${carouselNum(g[k])}</td>`;
    return `<section class="cv-group"><h3>${esc(CAROUSEL_TAG_LABELS[key]||key)}</h3><table><thead><tr><th></th><th>Posts</th><th>Views</th><th>Likes /1k</th><th>Comments /1k</th><th>New people /1k</th></tr></thead>
      <tbody>${list.map(g=>`<tr class="${g.n<3?'thin':''}"><th>${esc(g.v)}</th><td>${g.n}</td><td>${carouselNum(g.views)}</td>${cell(g,'likes')}${cell(g,'comments')}${cell(g,'people')}</tr>`).join('')}</tbody></table></section>`;
  };
  return head+`<div class="cv-groups">${CAROUSEL_GROUPS.map(table).join('')}</div>`;
}

let carouselSlide=0;
function carouselWhen(c){
  if(!c.post_date)return c.stage==='Posted'?'Posted, date not set.':'No post date yet.';
  const d=new Date(c.post_date+'T12:00:00').toLocaleDateString(undefined,{weekday:'long',day:'numeric',month:'long'});
  const time=c.tags?.post_time?` at ${c.tags.post_time}`:'';
  return c.stage==='Posted'?`Went out ${d}${time}.`:`Goes out ${d}${time}.`;
}
function carouselViewer(c){
  const slides=carouselSlides(c.slides),n=c.images||slides.length;
  if(!n)return `<div class="pv-stage pv-empty"><p>No slides yet. Ask Sam to draft them, or write them under Edit the words.</p></div>`;
  const i=Math.min(carouselSlide,n-1);
  const face=k=>c.images?`<img src="${carouselImg(c,k)}" alt="Slide ${k+1}">`:`<div class="pv-paper"><p>${esc(slides[k].replace(/\*\*/g,''))}</p></div>`;
  return `<div class="pv-stage" tabindex="0" aria-label="Slide ${i+1} of ${n}. Use the arrow keys to move." data-pv-stage>
      <div class="pv-frame">${face(i)}</div>
      ${n>1?`<button class="pv-nav prev" data-pv-go="${i-1}" ${i===0?'disabled':''} aria-label="Previous slide">‹</button><button class="pv-nav next" data-pv-go="${i+1}" ${i===n-1?'disabled':''} aria-label="Next slide">›</button>`:''}
    </div>
    <div class="pv-thumbs">${Array.from({length:n},(_,k)=>`<button class="pv-thumb ${k===i?'on':''}" data-pv-go="${k}" aria-label="Slide ${k+1}">${c.images?`<img src="${carouselImg(c,k)}" alt="" loading="lazy">`:`<span>${k+1}</span>`}</button>`).join('')}</div>`;
}
function carouselPerformance(c){
  const r=c.results?.d7?.views?c.results.d7:c.results?.d1||{},w=c.results?.d7?.views?'a week':'the first check';
  if(c.stage!=='Posted')return '';
  if(!Object.keys(r).filter(k=>k!=='checked_at').length)return `<section class="pv-card"><h3>How it's doing</h3><p class="hint">No numbers yet. Sam adds them a day and a week after it goes out.</p></section>`;
  const likeRate=r.views&&r.likes!=null?(r.likes/r.views*100):null;
  const stat=(v,l)=>v==null?'':`<div><b>${carouselNum(v)}</b><span>${l}</span></div>`;
  return `<section class="pv-card"><h3>How it's doing</h3>
    <div class="pv-stats">${stat(r.views,'views')}${stat(r.likes,'likes')}${stat(r.comments,'comments')}${stat(r.new_people,'new people')}</div>
    <p class="pv-read">${likeRate!=null?`${likeRate.toFixed(1)}% of people who saw it liked it, as of ${esc(w)}.`:'Views aren\u2019t in yet, so there\u2019s no like rate.'}</p></section>`;
}
function renderCarouselEditor(){
  const c=carouselFind(carouselOpen);if(!c){carouselOpen=null;return renderCarousels()}
  const videos=state.projects.filter(p=>!p.archived),t=c.tags||{},opt=carousels.tag_options||{};
  page(c.title,'Carousel');subPage('Carousels',c.title,'data-carousel-back');
  const tagSel=k=>`<label>${esc(CAROUSEL_TAG_LABELS[k])}<select data-cv-tag="${k}"><option value="">Not set</option>${(opt[k]||[]).map(v=>`<option ${t[k]===v?'selected':''}>${esc(v)}</option>`).join('')}</select></label>`;
  const tagText=(k,ph='')=>`<label>${esc(CAROUSEL_TAG_LABELS[k])}<input data-cv-tag="${k}" value="${esc(t[k]??'')}" placeholder="${esc(ph)}"></label>`;
  const tagFlag=k=>`<label class="check"><input type="checkbox" data-cv-flag="${k}" ${t[k]?'checked':''}>${esc(CAROUSEL_TAG_LABELS[k])}</label>`;
  const res=w=>(carousels.result_numbers||[]).map(k=>`<td><input type="number" min="0" step="any" inputmode="decimal" data-cv-result="${w}:${k}" value="${esc((c.results?.[w]||{})[k]??'')}" aria-label="${esc(CAROUSEL_RESULT_LABELS[k])} at ${w==='d1'?'1 day':'7 days'}"></td>`).join('');
  const describe=[t.style,t.format&&t.format.toLowerCase()].filter(Boolean).join(', ');
  $('#workspace').innerHTML=`<div class="pv">
  <header class="pv-head">
    <input class="pv-title" data-carousel-field="title" value="${esc(c.title)}" maxlength="240" aria-label="Title">
    <div class="pv-meta"><p>${esc(carouselWhen(c))}${describe?` ${esc(describe.charAt(0).toUpperCase()+describe.slice(1))}.`:''}</p>
      <label class="pv-stage-pick"><span class="sr-only">Stage</span><select data-carousel-field="stage" class="stage-${esc(c.stage.toLowerCase().replace(/ /g,'-'))}">${carousels.stages.map(s=>`<option ${s===c.stage?'selected':''}>${esc(s)}</option>`).join('')}</select></label>
      <span id="carousel-save" class="save-note">Saved</span></div>
  </header>
  <div class="pv-body">
    <div class="pv-left" id="pv-viewer">${carouselViewer(c)}</div>
    <div class="pv-right">
      ${carouselPerformance(c)}
      <section class="pv-card"><h3>Caption</h3>${c.caption?`<p class="pv-caption">${esc(c.caption)}</p>`:'<p class="hint">No caption yet.</p>'}</section>
      ${c.link||t.tracked_link?`<section class="pv-card pv-links">${c.link?`<a href="${esc(c.link)}" target="_blank" rel="noopener">Open the post on YouTube</a>`:''}${t.tracked_link?`<a href="${esc(t.tracked_link)}" target="_blank" rel="noopener">Tracked link</a>`:''}</section>`:''}
      ${c.tags?.testing?`<section class="pv-card"><h3>What it's testing</h3><p>${esc(c.tags.testing)}</p></section>`:''}
    </div>
  </div>
  <details class="pv-more"><summary>Edit the words</summary><div class="pv-more-body">
    <label>Hook<textarea data-carousel-field="hook" rows="2" maxlength="1000">${esc(c.hook)}</textarea></label>
    <label>Slides <small>a line of three dashes between slides</small><textarea data-carousel-field="slides" rows="12">${esc(c.slides)}</textarea></label>
    <label>Caption<textarea data-carousel-field="caption" rows="5" maxlength="5000">${esc(c.caption)}</textarea></label>
    <label>Notes<textarea data-carousel-field="notes" rows="3">${esc(c.notes)}</textarea></label>
  </div></details>
  <details class="pv-more"><summary>Details Sam keeps</summary><div class="pv-more-body pv-grid">
    <div>
      <label>Post date<input type="date" data-carousel-field="post_date" value="${esc(c.post_date)}"></label>
      ${tagText('post_time','21:00')}
      <label>Link to the post<input data-carousel-field="link" value="${esc(c.link)}" placeholder="https://"></label>
      ${tagText('tracked_link','https://linklr.app/r/…')}
      <label>Slides folder<input data-carousel-field="folder" value="${esc(c.folder)}"><small class="hint">${c.folder?(c.images?`${c.images} images found`:'No images found in that folder'):'Inside E:\\CareerOS\\03_Community_Posts'}</small></label>
      <label>Linked video<select data-carousel-field="project_id"><option value="">None</option>${videos.map(p=>`<option value="${esc(p.id)}" ${p.id===c.project_id?'selected':''}>${esc(p.name)}</option>`).join('')}</select></label>
      <label>Owner<select data-carousel-field="owner">${(team?.agents||[{id:'sam',name:'Sam'}]).map(a=>`<option value="${esc(a.id)}" ${a.id===c.owner?'selected':''}>${esc(a.name)}</option>`).join('')}<option value="josh" ${c.owner==='josh'?'selected':''}>Josh</option></select></label>
      <div class="flow-picks">${carousels.platforms.map(p=>`<label class="check"><input type="checkbox" data-carousel-platform="${esc(p)}" ${c.platforms.includes(p)?'checked':''}>${esc(p)}</label>`).join('')}</div>
      <label class="check"><input type="checkbox" data-carousel-archive ${c.archived?'checked':''}>Archived</label>
    </div>
    <div class="cv-tags">
      ${tagSel('theme')}${tagText('topic')}${tagSel('format')}${tagSel('style')}
      ${tagSel('hook_type')}${tagText('hook_text')}${tagFlag('hook_number')}${tagSel('angle')}
      ${tagSel('cta_type')}${tagText('lead_magnet')}${tagSel('cta_place')}${tagSel('caption_style')}
      ${tagFlag('video_same_day')}${tagText('testing')}${tagSel('idea_source')}
    </div>
    <div class="cv-results pv-wide"><table><thead><tr><th></th>${(carousels.result_numbers||[]).map(k=>`<th>${esc(CAROUSEL_RESULT_LABELS[k])}</th>`).join('')}</tr></thead>
      <tbody><tr><th>1 day</th>${res('d1')}</tr><tr><th>7 days</th>${res('d7')}</tr></tbody></table></div>
  </div></details>
  </div>`;
}
function carouselGo(i){const c=carouselFind(carouselOpen);if(!c)return;const n=c.images||carouselSlides(c.slides).length;carouselSlide=Math.max(0,Math.min(n-1,i));const v=$('#pv-viewer');if(v){v.innerHTML=carouselViewer(c);v.querySelector('[data-pv-stage]')?.focus({preventScroll:true})}}
document.addEventListener('click',e=>{const g=e.target.closest('[data-pv-go]');if(g&&view==='carousels'&&carouselOpen)carouselGo(+g.dataset.pvGo)});
document.addEventListener('keydown',e=>{if(!e.target.closest?.('[data-pv-stage]'))return;if(e.key==='ArrowRight'){e.preventDefault();carouselGo(carouselSlide+1)}if(e.key==='ArrowLeft'){e.preventDefault();carouselGo(carouselSlide-1)}});

function carouselStrip(slides){return slides.length?slides.map((s,i)=>`<div class="slide"><span>${i+1}</span><p>${esc(s)}</p></div>`).join(''):'<p class="hint">Your slides will preview here as you write.</p>'}

async function newCarousel(){
  try{if(!carousels)await carouselLoad();
    const c=await api('/api/carousels',{id:crypto.randomUUID(),title:'New carousel'});carousels.items.unshift(c);carouselOpen=c.id;view='carousels';render();
    const t=document.querySelector('[data-carousel-field=title]');t?.focus();t?.select()}
  catch(e){toast(e.message,true)}
}
function carouselTags(c,key,value){const t={...(c.tags||{})};if(value===''||value==null||value===false)delete t[key];else t[key]=value;carouselSaver.change(c,'tags',t)}

document.addEventListener('input',e=>{
  if(view!=='carousels'||!carouselOpen)return;const el=e.target,c=carouselFind(carouselOpen);
  if(el.dataset.carouselField&&el.tagName!=='SELECT'&&el.type!=='date'){carouselSaver.change(c,el.dataset.carouselField,el.value);if(el.dataset.carouselField==='title'){const crumb=$('#crumbs span');if(crumb)crumb.textContent=el.value}if(el.dataset.carouselField==='slides'&&!c.images){const v=$('#pv-viewer');if(v)v.innerHTML=carouselViewer(c)}}
  if(el.dataset.cvTag&&el.tagName==='INPUT')carouselTags(c,el.dataset.cvTag,el.value.trim());
  if(el.dataset.cvResult){const [w,k]=el.dataset.cvResult.split(':'),r={...(c.results||{})};const row={...(r[w]||{})};if(el.value==='')delete row[k];else row[k]=+el.value;
    if(Object.keys(row).filter(x=>x!=='checked_at').length){row.checked_at=row.checked_at||new Date().toISOString().slice(0,10);r[w]=row}else delete r[w];carouselSaver.change(c,'results',r)}
});
document.addEventListener('change',e=>{
  const el=e.target;
  if(view==='carousels'&&!carouselOpen){
    if(el.dataset.cvSort!==undefined){carouselSort=el.value;renderCarousels();return}
    if(el.dataset.cvFilter){carouselFilter[el.dataset.cvFilter]=el.value;renderCarousels();return}
  }
  if(view!=='carousels'||!carouselOpen)return;const c=carouselFind(carouselOpen);
  if(el.dataset.carouselField&&(el.tagName==='SELECT'||el.type==='date'))carouselSaver.change(c,el.dataset.carouselField,el.value);
  if(el.dataset.carouselField==='folder'){(async()=>{await carouselSaver.flush();try{await carouselLoad()}catch{}if(carouselOpen===c.id)renderCarouselEditor()})()}
  if(el.dataset.cvTag&&el.tagName==='SELECT')carouselTags(c,el.dataset.cvTag,el.value);
  if(el.dataset.cvFlag)carouselTags(c,el.dataset.cvFlag,el.checked);
  if(el.dataset.carouselPlatform)carouselSaver.change(c,'platforms',carousels.platforms.filter(p=>p===el.dataset.carouselPlatform?el.checked:c.platforms.includes(p)));
  if(el.dataset.carouselArchive!==undefined)carouselSaver.change(c,'archived',el.checked);
});
document.addEventListener('click',async e=>{
  if(view==='carousels'&&!carouselOpen){
    const tb=e.target.closest('[data-cv-tab]');if(tb){carouselTab=tb.dataset.cvTab;renderCarousels();return}
    const wn=e.target.closest('[data-cv-window]');if(wn){carouselWindow=wn.dataset.cvWindow;renderCarousels();return}
  }
  if(view!=='carousels'&&view!=='calendar')return;
  const open=e.target.closest('[data-carousel-open]');if(open){if(!leave())return;current=null;carouselOpen=open.dataset.carouselOpen;carouselSlide=0;view='carousels';if(!carousels)await carouselLoad();render();scrollTo(0,0);return}
  if(e.target.closest('[data-carousel-back]')){await carouselSaver.flush();carouselOpen=null;try{await carouselLoad()}catch{}render()}
});

// Show carousels on the main calendar next to videos and emails.
const carouselBaseCalendar=renderCalendar;
renderCalendar=function(){carouselBaseCalendar();
  const paint=()=>{if(view!=='calendar'||current)return;for(const c of carousels.items.filter(c=>!c.archived&&c.post_date)){
    const day=document.querySelector(`.day[data-date="${c.post_date}"]`);
    if(day&&!day.querySelector(`[data-carousel-open="${c.id}"]`))day.insertAdjacentHTML('beforeend',`<button class="cal-carousel ${c.stage==='Posted'?'posted':''}" data-carousel-open="${esc(c.id)}">${esc(c.title)}<small>Carousel · ${esc(c.stage)}</small></button>`)}};
  if(carousels)paint();else carouselLoad().then(paint).catch(()=>{});
};

const carouselBaseRender=render;
render=function(){if(!current&&view==='carousels')return renderCarousels();if(view!=='carousels')carouselOpen=null;return carouselBaseRender()};
const carouselBaseNew=$('#new-project').onclick;
$('#new-project').onclick=e=>view==='carousels'?newCarousel():carouselBaseNew(e);
