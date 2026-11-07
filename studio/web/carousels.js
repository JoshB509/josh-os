'use strict';
// Carousels: a simple tracker from idea to posted, with a slide-by-slide preview.
// Slides are written as plain text with a line of three dashes between slides.
let carousels=null,carouselOpen=null;
const carouselSaver=makeAutosaver({url:c=>'/api/carousels/'+c.id,note:t=>{const n=$('#carousel-save');if(n)n.textContent=t},onSaved:()=>carouselSidebar()});

function carouselSlides(text){return text.split(/\n-{3,}\n?/).map(s=>s.trim()).filter(Boolean)}
function carouselFind(id){return carousels.items.find(c=>c.id===id)}
function carouselDate(d){return d?new Date(d+'T12:00:00').toLocaleDateString(undefined,{weekday:'short',day:'numeric',month:'short'}):'No date'}
async function carouselLoad(){carousels=await api('/api/carousels')}
function carouselSidebar(){/* list view refreshes on next render */}

async function renderCarousels(){
  page('Carousels','Plan them, write them slide by slide, then mark them posted with a link.');
  if(!carousels){$('#workspace').innerHTML='<p class="hint">Loading carousels…</p>';try{await carouselLoad()}catch(e){toast(e.message,true);return}if(view!=='carousels'||current)return}
  if(carouselOpen)return renderCarouselEditor();
  const live=carousels.items.filter(c=>!c.archived),week=new Date();week.setDate(week.getDate()+7);
  const dueSoon=live.filter(c=>c.stage!=='Posted'&&c.post_date&&new Date(c.post_date+'T23:59:59')<=week).length;
  $('#workspace').innerHTML=`<div class="carousel-summary">
      <div><b>${live.filter(c=>c.stage!=='Posted').length}</b><span>in progress</span></div>
      <div><b>${dueSoon}</b><span>due in the next 7 days</span></div>
      <div><b>${live.filter(c=>c.stage==='Posted').length}</b><span>posted</span></div></div>
    <div class="carousel-board">${carousels.stages.map(stage=>{const items=live.filter(c=>c.stage===stage).sort((a,b)=>(a.post_date||'9999').localeCompare(b.post_date||'9999'));
      return `<section class="carousel-column"><h3>${esc(stage)} <span class="count">${items.length}</span></h3>${items.map(c=>`
        <button class="carousel-card" data-carousel-open="${esc(c.id)}"><b>${esc(c.title)}</b>
          <span>${esc(carouselDate(c.post_date))} · ${carouselSlides(c.slides).length} slides</span>
          <span class="flows">${c.platforms.map(p=>`<span class="flow">${esc(p)}</span>`).join('')}</span></button>`).join('')||'<p class="empty-column">Nothing here</p>'}</section>`}).join('')}</div>`;
}

function renderCarouselEditor(){
  const c=carouselFind(carouselOpen);if(!c){carouselOpen=null;return renderCarousels()}
  const slides=carouselSlides(c.slides),videos=state.projects.filter(p=>!p.archived);
  page(c.title,'Carousel');subPage('Carousels',c.title,'data-carousel-back');
  $('#workspace').innerHTML=`<div class="crumb"><button data-carousel-back>← Back to carousels</button></div>
  <div class="project-top"><input class="name" data-carousel-field="title" value="${esc(c.title)}" maxlength="240" aria-label="Title">
    <select data-carousel-field="stage" aria-label="Stage">${carousels.stages.map(s=>`<option ${s===c.stage?'selected':''}>${esc(s)}</option>`).join('')}</select>
    <span id="carousel-save" class="save-note">Saved</span></div>
  <div class="carousel-editor">
    <section>
      <label>Hook<textarea data-carousel-field="hook" rows="2" maxlength="1000" placeholder="The first line people see">${esc(c.hook)}</textarea></label>
      <label>Slides <small>put a line of three dashes between slides</small><textarea data-carousel-field="slides" rows="14" placeholder="Slide 1 text&#10;---&#10;Slide 2 text">${esc(c.slides)}</textarea></label>
      <div class="slide-strip" id="slide-strip">${carouselStrip(slides)}</div>
      <label>Caption<textarea data-carousel-field="caption" rows="5" maxlength="5000">${esc(c.caption)}</textarea></label>
    </section>
    <aside>
      <label>Post date<input type="date" data-carousel-field="post_date" value="${esc(c.post_date)}"></label>
      <h4>Platforms</h4><div class="flow-picks">${carousels.platforms.map(p=>`<label class="check"><input type="checkbox" data-carousel-platform="${esc(p)}" ${c.platforms.includes(p)?'checked':''}>${esc(p)}</label>`).join('')}</div>
      <label>Linked video<select data-carousel-field="project_id"><option value="">None</option>${videos.map(p=>`<option value="${esc(p.id)}" ${p.id===c.project_id?'selected':''}>${esc(p.name)}</option>`).join('')}</select></label>
      <label>Owner<select data-carousel-field="owner">${(team?.agents||[{id:'bob',name:'Bob'}]).map(a=>`<option value="${esc(a.id)}" ${a.id===c.owner?'selected':''}>${esc(a.name)}</option>`).join('')}<option value="josh" ${c.owner==='josh'?'selected':''}>Josh</option></select></label>
      <label>Link once posted<input data-carousel-field="link" value="${esc(c.link)}" placeholder="https://"></label>
      <label>Notes<textarea data-carousel-field="notes" rows="5">${esc(c.notes)}</textarea></label>
      <label class="check"><input type="checkbox" data-carousel-archive ${c.archived?'checked':''}>Archived</label>
    </aside>
  </div>`;
}
function carouselStrip(slides){return slides.length?slides.map((s,i)=>`<div class="slide"><span>${i+1}</span><p>${esc(s)}</p></div>`).join(''):'<p class="hint">Your slides will preview here as you write.</p>'}

async function newCarousel(){
  try{if(!carousels)await carouselLoad();
    const c=await api('/api/carousels',{id:crypto.randomUUID(),title:'New carousel'});carousels.items.unshift(c);carouselOpen=c.id;view='carousels';render();
    const t=document.querySelector('[data-carousel-field=title]');t?.focus();t?.select()}
  catch(e){toast(e.message,true)}
}

document.addEventListener('input',e=>{
  if(view!=='carousels'||!carouselOpen)return;const el=e.target,c=carouselFind(carouselOpen);
  if(el.dataset.carouselField&&el.tagName!=='SELECT'&&el.type!=='date'){carouselSaver.change(c,el.dataset.carouselField,el.value);if(el.dataset.carouselField==='title'){const crumb=$('#crumbs span');if(crumb)crumb.textContent=el.value}if(el.dataset.carouselField==='slides')$('#slide-strip').innerHTML=carouselStrip(carouselSlides(el.value))}
});
document.addEventListener('change',e=>{
  if(view!=='carousels'||!carouselOpen)return;const el=e.target,c=carouselFind(carouselOpen);
  if(el.dataset.carouselField&&(el.tagName==='SELECT'||el.type==='date'))carouselSaver.change(c,el.dataset.carouselField,el.value);
  if(el.dataset.carouselPlatform)carouselSaver.change(c,'platforms',carousels.platforms.filter(p=>p===el.dataset.carouselPlatform?el.checked:c.platforms.includes(p)));
  if(el.dataset.carouselArchive!==undefined)carouselSaver.change(c,'archived',el.checked);
});
document.addEventListener('click',async e=>{
  if(view!=='carousels'&&view!=='calendar')return;
  const open=e.target.closest('[data-carousel-open]');if(open){if(!leave())return;current=null;carouselOpen=open.dataset.carouselOpen;view='carousels';if(!carousels)await carouselLoad();render();scrollTo(0,0);return}
  if(e.target.closest('[data-carousel-back]')){await carouselSaver.flush();carouselOpen=null;render()}
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
