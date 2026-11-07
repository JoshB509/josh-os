'use strict';
// Carousels: every carousel from idea to posted, with its slides, tags and results.
// Board shows the pipeline, Posted shows each post with its numbers, and What's working
// groups the posts by tag so the team can see which hooks, formats and styles do best.
// Slides are written as plain text with a line of three dashes between slides. Rendered
// slide images come from the carousel's folder on the E drive.
let carousels=null,carouselOpen=null,carouselTab='queue',carouselSort='newest',carouselWindow='d7',carouselFilter={};
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

// The queue: what needs Josh, the next two weeks, what's in the works, and what just went out.
// Insights (what's working, every posted post, creators) sit behind the second view.
const CAROUSEL_INSIGHTS=[['working',"What's working"],['posted','Every post'],['creators','Creators']];
function carouselDayKey(d){return d.toISOString().slice(0,10)}
async function renderCarousels(){
  page('Carousels','');
  if(!carousels){$('#workspace').innerHTML='<p class="hint">Loading carousels…</p>';try{await carouselLoad()}catch(e){toast(e.message,true);return}if(view!=='carousels'||current)return}
  if(carouselOpen)return renderCarouselEditor();
  if(inspoOpen)return renderInspoPost();
  if(carouselTab==='creators'&&!inspo){try{await inspoLoad()}catch(e){toast(e.message,true)}if(view!=='carousels'||current)return}
  const live=carousels.items.filter(c=>!c.archived),posted=live.filter(c=>c.stage==='Posted'),queue=live.filter(c=>c.stage!=='Posted');
  const insights=carouselTab!=='queue'&&carouselTab!=='board';
  const switcher=`<div class="cv-tabs" role="tablist"><button role="tab" aria-selected="${!insights}" data-cv-tab="queue">Queue</button><button role="tab" aria-selected="${insights}" data-cv-tab="${insights?carouselTab:'working'}">Insights</button></div>`;
  if(insights){
    $('#workspace').innerHTML=`<div class="cq-top">${switcher}</div>
      <div class="cv-window cq-sub" role="group" aria-label="Insights">${CAROUSEL_INSIGHTS.map(([k,l])=>`<button data-cv-tab="${k}" aria-pressed="${carouselTab===k}">${esc(l)}</button>`).join('')}</div>
      <div id="cv-body">${carouselTab==='posted'?carouselPosted(posted):carouselTab==='creators'?inspoTab():carouselWorking(posted)}</div>`;
    return;
  }
  const today=new Date();today.setHours(12,0,0,0);
  const days=Array.from({length:14},(_,i)=>{const d=new Date(today);d.setDate(d.getDate()+i);return d});
  const onDay=k=>live.filter(c=>c.post_date===k);
  let ahead=0;for(const d of days.concat(Array.from({length:46},(_,i)=>{const x=new Date(today);x.setDate(x.getDate()+14+i);return x}))){if(!live.some(c=>c.post_date===carouselDayKey(d)&&['Approved','Scheduled','Posted'].includes(c.stage)))break;ahead++}
  const weekOut=carouselDayKey(days[6]);
  const allNeeds=queue.filter(c=>carouselNeeds(c)).sort((a,b)=>(a.post_date||'9999').localeCompare(b.post_date||'9999'));
  const needs=allNeeds.filter(c=>!c.post_date||c.post_date<=weekOut),later=allNeeds.length-needs.length;
  const works=queue.filter(c=>!carouselNeeds(c)&&!['Approved','Scheduled'].includes(c.stage)).sort((a,b)=>(a.post_date||'9999').localeCompare(b.post_date||'9999'));
  const recent=posted.sort((a,b)=>(b.post_date||'').localeCompare(a.post_date||'')).slice(0,6);
  const open=days.filter(d=>!onDay(carouselDayKey(d)).length).length;
  const lead=ahead>=10?`Approved and scheduled ${ahead} days ahead.`:`Approved and scheduled ${ahead} day${ahead===1?'':'s'} ahead. Sam aims for 10.`;
  $('#workspace').innerHTML=`<div class="cq">
    <div class="cq-top"><p class="cq-lead">${esc(lead)}${open?` ${open} of the next 14 days ${open===1?'is':'are'} still open.`:''}</p>${switcher}</div>
    ${needs.length?`<section class="cq-needs"><h2>Needs you</h2><div class="cq-need-list">${needs.map(c=>`<button class="cq-need" data-carousel-open="${esc(c.id)}">
        <span class="cq-need-cover">${carouselCover(c,'sm')}</span>
        <span class="cq-need-body"><b>${esc(c.title)}</b><span>${esc(carouselNeeds(c))}${c.post_date?`, for ${esc(carouselDate(c.post_date))}`:''}</span></span>
        <span class="cq-need-go">Review</span></button>`).join('')}</div>${later?`<p class="cq-more">${later} more after this week. They're in the next two weeks below.</p>`:''}</section>`:''}
    <section class="cq-weeks"><h2>Next two weeks</h2><div class="cq-strip">${days.map(d=>{const k=carouselDayKey(d),cs=onDay(k),wk=d.toLocaleDateString(undefined,{weekday:'short'}),n=d.getDate();
      return `<div class="cq-day ${d.getDay()===0||d.getDay()===6?'wkend':''}"><span class="cq-date"><b>${n}</b>${esc(wk)}</span>${cs.length?cs.map(c=>`<button class="cq-slot s-${esc(c.stage.toLowerCase().replace(/ /g,'-'))}" data-carousel-open="${esc(c.id)}" title="${esc(c.title)}"><span class="cq-thumb">${carouselCover(c,'sm')}</span><span class="cq-slot-title">${esc(c.title)}</span><span class="cq-slot-stage">${esc(c.stage==='Ready for review'?'For review':c.stage)}</span></button>`).join(''):`<button class="cq-open" data-dock-ask="Plan a carousel for ${esc(d.toLocaleDateString(undefined,{weekday:'long',day:'numeric',month:'long'}))}">Open</button>`}</div>`}).join('')}</div></section>
    ${works.length?`<section class="cq-works"><h2>In the works</h2>${works.map(c=>`<button class="cq-row" data-carousel-open="${esc(c.id)}"><span class="cq-row-title">${esc(c.title)}</span><span class="cq-row-meta">${esc(carouselStep(c).label)}${c.post_date?`, for ${esc(carouselDate(c.post_date))}`:''}</span></button>`).join('')}</section>`:''}
    ${recent.length?`<section class="cq-recent"><h2>Recently out</h2><div class="cq-recent-grid">${recent.map(c=>{const r=carouselRes(c,c.results?.d7?'d7':'d1');return `<button class="cq-post" data-carousel-open="${esc(c.id)}"><span class="cq-post-cover">${carouselCover(c)}</span><b>${esc(c.title)}</b><span>${r.likes!=null?`${carouselNum(r.likes)} likes`:'No numbers yet'}${r.views?`, ${carouselNum(r.views)} views`:''}</span></button>`}).join('')}</div></section>`:''}
  </div>`;
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

// A carousel's page follows Josh's flow: write it, make the design, approve it with its date,
// Sam schedules it, then it goes out and the numbers come in. One main action at a time.
let carouselSlide=0,carouselEditing=false,carouselDesigning=false;
const CAROUSEL_STEPS=[['write','Write'],['design','Design'],['approve','Approve'],['schedule','Schedule'],['posted','Out']];
function carouselStep(c){
  const words=carouselSlides(c.slides).length,paper=(c.tags?.style||'Paper')==='Paper',stale=!!(c.design_stale||c._edited);
  if(c.stage==='Posted')return {key:'posted',label:'Posted'};
  if(c.stage==='Scheduled')return {key:'schedule',label:'Scheduled',done:true};
  if(c.stage==='Approved')return {key:'schedule',label:'Approved, Sam schedules it'};
  if(!words&&!c.images)return {key:'write',label:'Being written'};
  if(!c.images||(paper&&stale))return {key:'design',label:c.images?'Words changed, needs a new design':'Needs its design',paper};
  return {key:'approve',label:'Ready for your review'};
}
function carouselNeeds(c){const s=carouselStep(c);return s.key==='approve'||(s.key==='design'&&carouselSlides(c.slides).length)?s.label:''}
function carouselWhen(c){
  if(!c.post_date)return c.stage==='Posted'?'Posted, date not set.':'No date yet.';
  const d=new Date(c.post_date+'T12:00:00').toLocaleDateString(undefined,{weekday:'long',day:'numeric',month:'long'});
  const time=c.tags?.post_time?` at ${c.tags.post_time}`:'';
  return c.stage==='Posted'?`Went out ${d}${time}.`:`Goes out ${d}${time}.`;
}
function carouselViewer(c){
  if(carouselEditing)return carouselWordsEditor(c);
  const slides=carouselSlides(c.slides),n=c.images||slides.length;
  if(!n)return `<div class="pv-stage pv-empty"><p>No slides yet.</p><button class="primary" data-dock-ask="Write the slides and caption for this carousel, and save them to it.">Ask Sam to write it</button></div>`;
  const i=Math.min(carouselSlide,n-1);
  const face=k=>c.images?`<img src="${carouselImg(c,k)}" alt="Slide ${k+1}">`:`<div class="pv-paper"><p>${esc(slides[k].replace(/\*\*/g,''))}</p></div>`;
  return `<div class="pv-stage" tabindex="0" aria-label="Slide ${i+1} of ${n}. Use the arrow keys to move." data-pv-stage>
      <div class="pv-frame">${face(i)}</div>
      ${n>1?`<button class="pv-nav prev" data-pv-go="${i-1}" ${i===0?'disabled':''} aria-label="Previous slide">‹</button><button class="pv-nav next" data-pv-go="${i+1}" ${i===n-1?'disabled':''} aria-label="Next slide">›</button>`:''}
      ${c.images&&(c.design_stale||c._edited)?'<span class="pv-stale">Words changed since this design was made</span>':''}
    </div>
    <div class="pv-thumbs">${Array.from({length:n},(_,k)=>`<button class="pv-thumb ${k===i?'on':''}" data-pv-go="${k}" aria-label="Slide ${k+1}">${c.images?`<img src="${carouselImg(c,k)}" alt="" loading="lazy">`:`<span>${k+1}</span>`}</button>`).join('')}</div>`;
}
// Editing the words: one box per slide, in order, instead of one long text with dashes.
function carouselWordsEditor(c){
  const slides=carouselSlides(c.slides);if(!slides.length)slides.push('');
  return `<div class="pw">${slides.map((s,k)=>`<div class="pw-slide"><span class="pw-n">${k+1}</span><textarea data-pw-slide="${k}" rows="3" aria-label="Slide ${k+1}">${esc(s)}</textarea><button class="pw-del" data-pw-del="${k}" aria-label="Remove slide ${k+1}" ${slides.length===1?'disabled':''}>✕</button></div>`).join('')}
    <button class="pw-add" data-pw-add>Add a slide</button>
    <p class="hint">Put **two stars** around words to highlight them in gold. The first line of a slide is its headline.</p>
    <div class="pw-done"><button class="primary" data-pw-done>Done</button></div></div>`;
}
function carouselActions(c){
  const s=carouselStep(c);
  const dateField=`<label class="pa-when"><span>Post on</span><input type="date" data-carousel-field="post_date" value="${esc(c.post_date)}"><input data-cv-tag="post_time" value="${esc(c.tags?.post_time||'')}" placeholder="21:00" aria-label="Time, Dubai" maxlength="5"></label>`;
  const edit=`<button class="subtle" data-pw-toggle>${carouselEditing?'Stop editing':'Edit the words'}</button>`;
  if(s.key==='write')return `<div class="pa"><button class="primary" data-dock-ask="Write the slides and caption for this carousel, and save them to it.">Ask Sam to write it</button>${edit}</div>`;
  if(s.key==='design')return `<div class="pa">${s.paper?`<button class="primary" data-pv-design ${carouselDesigning?'disabled':''}>${carouselDesigning?'Making the design…':c.images?'Make a new design':'Make the design'}</button>`:`<button class="primary" data-dock-ask="Build the Photo design for this carousel and point it at the slide images.">Ask Sam to build the Photo design</button>`}${edit}</div>`;
  if(s.key==='approve')return `<div class="pa">${dateField}<button class="primary" data-pv-approve>Approve</button>${edit}</div>`;
  if(c.stage==='Approved')return `<div class="pa"><p class="pa-note">Approved. Sam schedules it on YouTube and marks it scheduled.</p><button class="subtle" data-pv-set="Ready for review">Undo approval</button></div>`;
  if(c.stage==='Scheduled')return `<div class="pa"><p class="pa-note">Scheduled on YouTube.</p>${c.link?`<a class="subtle-link" href="${esc(c.link)}" target="_blank" rel="noopener">Open on YouTube</a>`:''}</div>`;
  return c.link?`<div class="pa"><a class="subtle-link" href="${esc(c.link)}" target="_blank" rel="noopener">Open the post on YouTube</a></div>`:'';
}
function carouselPerformance(c){
  if(c.stage!=='Posted')return '';
  const r=c.results?.d7?.views?c.results.d7:c.results?.d1||{},w=c.results?.d7?.views?'a week':'the first check';
  if(!Object.keys(r).filter(k=>k!=='checked_at').length)return `<section class="pv-card"><h3>How it's doing</h3><p class="hint">No numbers yet. Sam adds them a day and a week after it goes out.</p></section>`;
  const likeRate=r.views&&r.likes!=null?(r.likes/r.views*100):null;
  const stat=(v,l)=>v==null?'':`<div><b>${carouselNum(v)}</b><span>${l}</span></div>`;
  return `<section class="pv-card"><h3>How it's doing</h3>
    <div class="pv-stats">${stat(r.views,'views')}${stat(r.likes,'likes')}${stat(r.comments,'comments')}${stat(r.new_people,'new people')}</div>
    <p class="pv-read">${likeRate!=null?`${likeRate.toFixed(1)}% of people who saw it liked it, as of ${esc(w)}.`:'Views aren\u2019t in yet, so there\u2019s no like rate.'}</p></section>`;
}
function carouselSteps(c){
  const s=carouselStep(c),at=CAROUSEL_STEPS.findIndex(x=>x[0]===s.key)+(s.done?1:0);
  return `<ol class="ps" aria-label="Progress">${CAROUSEL_STEPS.map(([k,l],i)=>`<li class="${i<at?'done':i===at?'now':''}"${i===at?' aria-current="step"':''}>${esc(l)}</li>`).join('')}</ol>`;
}
function renderCarouselEditor(){
  const c=carouselFind(carouselOpen);if(!c){carouselOpen=null;return renderCarousels()}
  page(c.title,'Carousel');subPage('Carousels',c.title,'data-carousel-back');
  $('#workspace').innerHTML=`<div class="pv">
  <header class="pv-head">
    <input class="pv-title" data-carousel-field="title" value="${esc(c.title)}" maxlength="240" aria-label="Title">
    ${carouselSteps(c)}
    <p class="pv-when">${esc(carouselStep(c).label)}. ${esc(carouselWhen(c))}<span id="carousel-save" class="save-note"></span></p>
    <div id="pv-actions">${carouselActions(c)}</div>
  </header>
  <div class="pv-body">
    <div class="pv-left" id="pv-viewer">${carouselViewer(c)}</div>
    <div class="pv-right">
      ${carouselPerformance(c)}
      <section class="pv-card"><h3>Caption</h3><textarea class="pv-caption-edit" data-carousel-field="caption" rows="4" placeholder="The words that sit above the post">${esc(c.caption)}</textarea></section>
      ${c.tags?.testing?`<section class="pv-card"><h3>What it's testing</h3><p>${esc(c.tags.testing)}</p></section>`:''}
      ${c.notes?`<section class="pv-card"><h3>Sam's notes</h3><p class="pv-notes">${esc(c.notes)}</p></section>`:''}
    </div>
  </div>
  ${carouselTracking(c)}
  </div>`;
  document.querySelectorAll('.pv-caption-edit,[data-pw-slide]').forEach(roomGrow);
}
// Everything Sam tracks about a post, kept out of the way.
function carouselTracking(c){
  const videos=state.projects.filter(p=>!p.archived),t=c.tags||{},opt=carousels.tag_options||{};
  const tagSel=k=>`<label>${esc(CAROUSEL_TAG_LABELS[k])}<select data-cv-tag="${k}"><option value="">Not set</option>${(opt[k]||[]).map(v=>`<option ${t[k]===v?'selected':''}>${esc(v)}</option>`).join('')}</select></label>`;
  const tagText=(k,ph='')=>`<label>${esc(CAROUSEL_TAG_LABELS[k])}<input data-cv-tag="${k}" value="${esc(t[k]??'')}" placeholder="${esc(ph)}"></label>`;
  const tagFlag=k=>`<label class="check"><input type="checkbox" data-cv-flag="${k}" ${t[k]?'checked':''}>${esc(CAROUSEL_TAG_LABELS[k])}</label>`;
  const res=w=>(carousels.result_numbers||[]).map(k=>`<td><input type="number" min="0" step="any" inputmode="decimal" data-cv-result="${w}:${k}" value="${esc((c.results?.[w]||{})[k]??'')}" aria-label="${esc(CAROUSEL_RESULT_LABELS[k])} at ${w==='d1'?'1 day':'7 days'}"></td>`).join('');
  return `<details class="pv-more"><summary>Tracking details</summary><p class="hint">Sam keeps these up to date so posts can be compared. You don't need to fill them in.</p><div class="pv-more-body pv-grid">
    <div>
      <label>Stage<select data-carousel-field="stage">${carousels.stages.map(s=>`<option ${s===c.stage?'selected':''}>${esc(s)}</option>`).join('')}</select></label>
      <label>Link to the post<input data-carousel-field="link" value="${esc(c.link)}" placeholder="https://"></label>
      ${tagText('tracked_link','https://linklr.app/r/…')}
      <label>Slides folder<input data-carousel-field="folder" value="${esc(c.folder)}"><small class="hint">${c.folder?(c.images?`${c.images} images found`:'No images found in that folder'):'Inside E:\\CareerOS\\03_Community_Posts'}</small></label>
      <label>Linked video<select data-carousel-field="project_id"><option value="">None</option>${videos.map(p=>`<option value="${esc(p.id)}" ${p.id===c.project_id?'selected':''}>${esc(p.name)}</option>`).join('')}</select></label>
      <label>Hook<input data-carousel-field="hook" value="${esc(c.hook)}" maxlength="1000"></label>
      <label>Notes<textarea data-carousel-field="notes" rows="3">${esc(c.notes)}</textarea></label>
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
  </div></details>`;
}
function carouselRefreshTop(){const c=carouselFind(carouselOpen);if(!c)return;const a=$('#pv-actions');if(a)a.innerHTML=carouselActions(c);const s=$('.ps');if(s)s.outerHTML=carouselSteps(c);const w=$('.pv-when');if(w)w.firstChild.textContent=`${carouselStep(c).label}. ${carouselWhen(c)}`}
function carouselGo(i){const c=carouselFind(carouselOpen);if(!c)return;const n=c.images||carouselSlides(c.slides).length;carouselSlide=Math.max(0,Math.min(n-1,i));const v=$('#pv-viewer');if(v){v.innerHTML=carouselViewer(c);v.querySelector('[data-pv-stage]')?.focus({preventScroll:true})}}
function carouselWordsSave(c){const boxes=[...document.querySelectorAll('[data-pw-slide]')];const text=boxes.map(b=>b.value.trim()).filter(Boolean).join('\n---\n');c._edited=true;carouselSaver.change(c,'slides',text)}
async function carouselDesign(c){
  carouselDesigning=true;carouselRefreshTop();
  try{await carouselSaver.flush();const saved=await api(`/api/carousels/${encodeURIComponent(c.id)}/design`,{});Object.assign(c,saved);c._edited=false;c.design_stale=false;carouselSlide=0;toast('Design made')}
  catch(e){toast(e.message,true)}
  finally{carouselDesigning=false;if(carouselOpen===c.id)renderCarouselEditor()}
}
async function carouselSetStage(c,stage){carouselSaver.change(c,'stage',stage);await carouselSaver.flush();renderCarouselEditor()}

document.addEventListener('click',e=>{
  if(view!=='carousels'||!carouselOpen)return;const c=carouselFind(carouselOpen);if(!c)return;
  const g=e.target.closest('[data-pv-go]');if(g){carouselGo(+g.dataset.pvGo);return}
  if(e.target.closest('[data-pv-design]')){carouselDesign(c);return}
  if(e.target.closest('[data-pv-approve]')){if(!c.post_date){toast('Pick the day it goes out first',true);document.querySelector('.pa-when input[type=date]')?.focus();return}carouselSetStage(c,'Approved');toast('Approved. Sam will schedule it.');return}
  const st=e.target.closest('[data-pv-set]');if(st){carouselSetStage(c,st.dataset.pvSet);return}
  if(e.target.closest('[data-pw-toggle]')||e.target.closest('[data-pw-done]')){carouselEditing=!carouselEditing;renderCarouselEditor();return}
  if(e.target.closest('[data-pw-add]')){carouselWordsSave(c);const t=c.slides?c.slides+'\n---\n ':' ';c.slides=t;$('#pv-viewer').innerHTML=carouselViewer(c);document.querySelectorAll('[data-pw-slide]').forEach(roomGrow);const b=[...document.querySelectorAll('[data-pw-slide]')].pop();b?.focus();return}
  const del=e.target.closest('[data-pw-del]');if(del){const s=carouselSlides(c.slides);s.splice(+del.dataset.pwDel,1);c._edited=true;carouselSaver.change(c,'slides',s.join('\n---\n'));$('#pv-viewer').innerHTML=carouselViewer(c);document.querySelectorAll('[data-pw-slide]').forEach(roomGrow);carouselRefreshTop();return}
});
document.addEventListener('input',e=>{
  if(view!=='carousels'||!carouselOpen)return;const c=carouselFind(carouselOpen);if(!c)return;
  if(e.target.dataset.pwSlide!==undefined){roomGrow(e.target);carouselWordsSave(c);carouselRefreshTop()}
  if(e.target.classList.contains('pv-caption-edit'))roomGrow(e.target);
});
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
document.addEventListener('change',e=>{if(view==='carousels'&&carouselOpen&&(e.target.dataset.carouselField==='post_date'||e.target.dataset.cvTag==='post_time'))setTimeout(carouselRefreshTop,0)});
