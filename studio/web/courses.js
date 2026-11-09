'use strict';
// Lessons: every course lesson from the first idea to the course app, in the same shape as the
// video and carousel workflows. Saskia prepares and drafts, Josh reads and approves the script,
// Saskia builds the slides only once it's approved, Josh records on the recording page, then the
// guide and thumbnail are added and Publish copies everything to the Google Drive folder Lovable reads.
let courses=null,courseOpen=null,courseFilter='all',courseStageFilter='',courseTab='script',courseTimer=null,coursePoll=null,courseBusy=null;
const COURSE_SHORT={hatt:'Heard at the Top',career:'Career Track',practical:'Practical Career Track',data:'Data storytelling'};
const COURSE_ACTION={
  'Planned':['work:prepare','Ask Saskia to prepare it'],
  'Preparing':null,
  'Drafting':['work:draft','Ask Saskia to draft the script'],
  'Script review':['approve','Approve the script'],
  'Approved':['work:slides','Ask Saskia to build the slides'],
  'Slides':['check','Check the slides again'],
  'Ready to record':['record','Record it'],
  'Recorded':['work:guide','Ask Saskia for the lesson guide'],
  'Guide and thumbnail':['work:guide','Ask Saskia for the lesson guide'],
  'Ready to publish':['publish','Publish to Google Drive'],
  'Published':['open:published','Open the published folder'],
};
const COURSE_TASKS=[['prepare','Prepare'],['draft','Draft the script'],['slides','Build the slides'],['guide','Lesson guide'],['thumbnail','Thumbnail']];
const COURSE_FILES=[['script','Script'],['slides','Slides'],['guide','Lesson guide'],['thumbnail','Thumbnail'],['video','Video']];

function courseSlug(s){return s.toLowerCase().replace(/[^a-z]+/g,'-')}
function coursePill(stage){return `<span class="ls-pill s-${courseSlug(stage)}">${esc(stage)}</span>`}
function courseBusyNow(l){return (l.job&&l.job.status==='running')||(l.publish&&l.publish.status==='copying')}
async function courseLoad(){courses=await api('/api/courses')}
async function courseFetch(id){courseOpen=await api('/api/courses/'+encodeURIComponent(id))}

async function renderCourses(){
  page('Lessons','');
  if(!courses){$('#workspace').innerHTML='<p class="hint">Loading lessons…</p>';try{await courseLoad()}catch(e){toast(e.message,true);return}if(view!=='courses'||current)return}
  if(courseOpen)return renderCourseLesson();
  const items=courses.items.filter(l=>courseFilter==='all'||l.course===courseFilter);
  const counts=Object.fromEntries(courses.stages.map(s=>[s,items.filter(l=>l.stage===s).length]));
  const needs=items.filter(l=>['Script review','Ready to publish'].includes(l.stage)||(l.job&&l.job.status==='failed')||(l.publish&&l.publish.status==='failed'));
  const working=items.filter(courseBusyNow);
  const shown=items.filter(l=>!courseStageFilter||l.stage===courseStageFilter);
  const groups=[];for(const l of shown){const key=l.course+'|'+l.part;let g=groups.find(x=>x.key===key);if(!g)groups.push(g={key,course:l.course,title:`${COURSE_SHORT[l.course]} · ${/^[A-Z]$/.test(l.part)?'':'Part '+l.part+' · '}${l.part_name}`,items:[]});g.items.push(l)}
  const needWhy=l=>l.job&&l.job.status==='failed'?'Saskia got stuck':l.publish&&l.publish.status==='failed'?'Publishing failed':l.stage==='Script review'?(l.stale?'Script changed since you approved it':'Read and approve the script'):'Ready to publish';
  $('#workspace').innerHTML=`<div class="ls">
    ${courses.publish_ready?'':`<section class="ls-drive"><div><b>Google Drive isn’t open on this PC.</b><p>Publishing copies lessons into ${esc(courses.publish_root||'the CareerOS Drive folder')}. Start Google Drive for desktop and that folder comes back.</p></div></section>`}
    <div class="ls-top"><div class="cv-tabs ls-courses" role="tablist">${[['all','All courses'],...Object.entries(COURSE_SHORT)].map(([k,v])=>`<button role="tab" aria-selected="${courseFilter===k}" data-course-filter="${k}">${esc(v)}</button>`).join('')}</div></div>
    <div class="ls-stages" role="group" aria-label="Filter by stage">${courses.stages.map(s=>`<button class="ls-stage ${courseStageFilter===s?'on':''}" data-course-stage="${esc(s)}" ${counts[s]?'':'disabled'}><b>${counts[s]}</b><span>${esc(s)}</span></button>`).join('')}</div>
    ${needs.length&&!courseStageFilter?`<section class="ls-needs"><h2>Needs you</h2>${needs.slice(0,12).map(l=>`<button class="ls-need" data-course-open="${esc(l.id)}"><span class="ls-num">${esc(l.number)}</span><span class="ls-need-body"><b>${esc(l.title)}</b><small>${esc(COURSE_SHORT[l.course])}. ${esc(needWhy(l))}</small></span><span class="ls-go">Open</span></button>`).join('')}${needs.length>12?`<p class="hint">${needs.length-12} more below.</p>`:''}</section>`:''}
    ${working.length?`<section class="ls-working"><h2>In progress</h2>${working.map(l=>`<button class="ls-row" data-course-open="${esc(l.id)}"><span class="ls-num">${esc(l.number)}</span><span class="ls-title">${esc(l.title)}</span><span class="ls-meta"><span class="dots"><i></i><i></i><i></i></span> ${l.publish&&l.publish.status==='copying'?'Copying to Google Drive':'Saskia is working'}</span></button>`).join('')}</section>`:''}
    ${groups.map(g=>`<section class="ls-part"><h2>${esc(g.title)}</h2>${g.items.map(courseRow).join('')}</section>`).join('')||'<p class="hint">No lessons at this stage.</p>'}
  </div>`;
  courseWatch();
}
function courseRow(l){
  const has=COURSE_FILES.map(([k,n])=>`<i class="${l.has[k]?'on':''}" title="${esc(n)}${l.has[k]?'':' missing'}">${esc(n[0])}</i>`).join('');
  return `<button class="ls-row" data-course-open="${esc(l.id)}"><span class="ls-num">${esc(l.number)}</span><span class="ls-title">${esc(l.title)}<small>${esc(l.next)}</small></span><span class="ls-has" aria-label="Files">${has}</span>${coursePill(l.stage)}</button>`;
}

function renderCourseLesson(){
  const l=courseOpen,act=l.stage==='Published'&&!(l.publish&&l.publish.folder)?null:COURSE_ACTION[l.stage],at=courses.stages.indexOf(l.stage),job=l.job,pub=l.publish;
  page(`${l.number} ${l.title}`,'');
  const stepper=`<ol class="ls-steps">${courses.stages.map((s,i)=>`<li class="${i<at?'done':i===at?'now':''}" title="${esc(s)}"><span></span><small>${esc(s)}</small></li>`).join('')}</ol>`;
  const tabs=[['script','Script'],['prep','Preparation'],['slides','Slides'],['publish','Files and publishing'],['history','History']];
  $('#workspace').innerHTML=`<div class="ls-lesson">
    <div class="crumb"><button data-course-back>← All lessons</button>${l.neighbours.previous?`<button data-course-open="${esc(l.neighbours.previous.id)}">‹ ${esc(l.neighbours.previous.number)}</button>`:''}${l.neighbours.next?`<button data-course-open="${esc(l.neighbours.next.id)}">${esc(l.neighbours.next.number)} ›</button>`:''}</div>
    <header class="ls-head"><p class="ls-eyebrow">${esc(COURSE_SHORT[l.course])} · ${/^[A-Z]$/.test(l.part)?'':'Part '+esc(l.part)+' · '}${esc(l.part_name)} · ${esc(l.type)}${l.minutes?` · ${esc(l.minutes)} min`:''}</p>
      <h1><span>${esc(l.number)}</span> ${esc(l.title)}</h1>${stepper}</header>
    <section class="ls-now">
      <div><p class="ls-next">${coursePill(l.stage)} ${esc(l.next)}</p>
        ${l.stale?'<p class="ls-warn">The script changed after you approved it, so it needs approving again before slides.</p>':''}
        ${job?`<p class="ls-job ${esc(job.status)}">${job.status==='running'?`<span class="dots"><i></i><i></i><i></i></span> ${job.waiting?`${esc((COURSE_TASKS.find(t=>t[0]===job.task)||[,'This'])[1])} is waiting its turn. Saskia works on two lessons at a time.`:`Saskia is working on ${esc((COURSE_TASKS.find(t=>t[0]===job.task)||[,'it'])[1].toLowerCase())}. This can take a while for slides.`}`:job.status==='failed'?`Saskia got stuck. ${esc(job.message||'')}`:esc(job.message||'Saskia finished.')}</p>`:''}
        ${pub&&!['done','elsewhere'].includes(pub.status)?`<p class="ls-job ${esc(pub.status)}">${pub.status==='copying'?'<span class="dots"><i></i><i></i><i></i></span> Copying the files to Google Drive.':`Publishing failed. ${esc(pub.message||'')}`}</p>`:''}</div>
      <div class="ls-acts">${act?`<button class="primary" data-course-act="${esc(act[0])}" ${courseBusyNow(l)?'disabled':''}>${esc(act[1])}</button>`:''}
        <label class="ls-move"><span>Move to</span><select data-course-move aria-label="Move to stage">${courses.stages.map(s=>`<option ${s===l.stage?'selected':''} ${s==='Published'?'disabled':''}>${esc(s)}</option>`).join('')}</select></label></div>
    </section>
    <section class="ls-saskia"><h2>Ask Saskia</h2><div class="ls-task-row">${COURSE_TASKS.map(([k,v])=>`<button class="chip-btn" data-course-act="work:${k}" ${courseBusyNow(l)?'disabled':''}>${esc(v)}</button>`).join('')}</div>
      <textarea id="course-note" rows="2" placeholder="Anything she should know for this one (optional)"></textarea>
      <p class="hint">Slides only start once the current script is approved. You can also chat with her about this lesson in the dock.</p></section>
    <div class="tabs">${tabs.map(([k,v])=>`<button data-course-tab="${k}" class="${courseTab===k?'active':''}">${v}</button>`).join('')}</div>
    <div id="course-body">${courseBody(l)}</div>
    <p class="save-note ls-saved" id="course-saved">Saved</p>
  </div>`;
  courseWatch();
}
function courseBody(l){
  if(courseTab==='script')return `<div class="ls-script"><div class="ls-bar"><span>${l.approved?(l.stale?'Changed since approval':'Approved '+new Date(l.approved.at).toLocaleDateString(undefined,{day:'numeric',month:'short'})+(l.approved.by==='import'?' (from Notion)':'')):'Not approved yet'}</span>${l.files.script?`<button class="link" data-course-open-file="script">Open the E drive copy</button>`:''}${(l.script||'').trim()&&(!l.approved||l.stale)?'<button class="primary" data-course-act="approve">Approve the script</button>':''}</div>
    <textarea class="ls-text" data-course-field="script" spellcheck="true" placeholder="The script appears here once Saskia drafts it, or paste one in.">${esc(l.script||'')}</textarea>
    <p class="hint">${courseWords(l.script)} words, about ${Math.max(1,Math.round(courseWords(l.script)/150))} min spoken. Approving saves the next version to the course's Scripts folder.</p></div>`;
  if(courseTab==='prep')return `<div class="ls-script"><textarea class="ls-text" data-course-field="prep" placeholder="Saskia's preparation notes go here.">${esc(l.prep||'')}</textarea>
    <label class="ls-notes"><small>Notes</small><textarea rows="3" data-course-field="notes">${esc(l.notes||'')}</textarea></label></div>`;
  if(courseTab==='slides'){const c=l.slides_check;return `<div class="ls-slides">
    <p>${l.files.slides?`<b>${esc(l.files.slides.path.split(/[\\/]/).pop())}</b> <button class="link" data-course-open-file="slides">Open</button> <button class="link" data-course-act="check">Check every beat</button>`:'No slide file yet.'}</p>
    ${c?`<p class="${c.ok?'ls-ok':'ls-warn'}">${c.ok?`All ${c.beats} beats checked with no errors.`:`The check found problems in ${c.beats} beats.`} Checked ${new Date(c.checked_at).toLocaleString(undefined,{day:'numeric',month:'short',hour:'2-digit',minute:'2-digit'})}.</p>
      ${[...c.errors.map(e=>'Script error: '+e),...c.problems].map(p=>`<p class="ls-problem">${esc(p)}</p>`).join('')}
      <div class="ls-sheets">${c.sheets.map(s=>`<figure><img src="/assets/${esc(s.asset)}?w=1600" alt="Beats ${s.first} to ${s.last}" loading="lazy"><figcaption>Beats ${s.first} to ${s.last}</figcaption></figure>`).join('')}</div>`:''}
    <h3>Cued script for the recording page</h3><p class="hint">${l.cues?'The recording page shows this beside the slides.':'Saskia adds it when she builds the slides.'}</p>
    <button class="chip-btn" data-course-cues>${l.cues?'Show and edit the cued script':'Add a cued script'}</button><div id="course-cues"></div></div>`}
  if(courseTab==='publish'){const f=l.files,rec=l.recording;return `<div class="ls-files">
    ${COURSE_FILES.map(([k,n])=>`<div class="ls-file ${f[k]?'':'missing'}"><span>${esc(n)}</span><code>${f[k]?esc(f[k].path):'Not there yet'}</code>${f[k]?`<button class="link" data-course-open-file="${k}">Open</button>`:''}</div>`).join('')}
    <p class="hint">${rec?(rec.take?`The recording page has ${rec.takes} take${rec.takes===1?'':'s'}, and ${esc(rec.take.name)} is the one chosen.`:rec.takes?`The recording page has ${rec.takes} take${rec.takes===1?'':'s'} and none is chosen yet.`:'Nothing has been recorded on the recording page yet.'):''} <a href="/lessons?lesson=${encodeURIComponent(l.id)}">Open the recording page</a></p>
    <label class="ls-notes"><small>Video file, if it wasn't recorded through Josh OS</small><input data-course-field="video" value="${esc(l.video||'')}" placeholder="Full path of the MP4"></label>
    <h3>Publishing</h3>
    ${l.destination?`<p>The video and lesson guide PDF go to <code>${esc(l.destination)}</code>, and the thumbnail goes to the Thumbnails folder beside it. An older version of either moves to an archive folder there.</p>`:'<p class="ls-warn">Google Drive isn’t open on this PC, so there is nowhere to publish yet.</p>'}
    ${l.blockers['Ready to publish']?`<p class="ls-warn">${esc(l.blockers['Ready to publish'])}</p>`:''}
    ${l.publish&&l.publish.status==='elsewhere'?`<p class="ls-ok">Marked as already live in the course app${l.publish.by&&l.publish.by!=='Josh'?' by '+esc(l.publish.by):''}.</p>`:''}
    ${l.stage!=='Published'?`<p class="hint">If this lesson is already in the course app, mark it as live and it moves to Published without the files. <button class="link" data-course-act="live" ${courseBusyNow(l)?'disabled':''}>It's already live</button></p>`:''}
    ${l.publish&&l.publish.status==='done'?`<p class="ls-ok">Published ${new Date(l.publish.finished_at).toLocaleString(undefined,{day:'numeric',month:'short',hour:'2-digit',minute:'2-digit'})}. <button class="link" data-course-act="open:published">Open the folder</button></p>`:''}
    <button class="${l.stage==='Ready to publish'?'primary':'chip-btn'}" data-course-act="publish" ${l.blockers['Ready to publish']||!l.destination||courseBusyNow(l)?'disabled':''}>${l.publish&&l.publish.status==='done'?'Publish again':'Publish to Google Drive'}</button></div>`}
  return `<ol class="ls-history">${(l.history||[]).slice().reverse().map(h=>`<li><b>${esc(h.stage)}</b> <small>${new Date(h.at).toLocaleString(undefined,{day:'numeric',month:'short',hour:'2-digit',minute:'2-digit'})}</small>${h.why?`<p>${esc(h.why)}</p>`:''}</li>`).join('')}</ol>`;
}
function courseWords(t){return ((t||'').split('---').slice(-1)[0].match(/[A-Za-z0-9’']+/g)||[]).length}

// Saving: typing autosaves shortly after you stop, like the rest of Josh OS.
function courseQueue(field,value){
  courseOpen[field]=value;const n=$('#course-saved');if(n)n.textContent='Saving…';
  clearTimeout(courseTimer);courseTimer=setTimeout(()=>courseSave(),800);
  (courseQueue.pending=courseQueue.pending||{})[field]=value;
}
async function courseSave(){
  if(courseBusy){await courseBusy;}
  const changes=courseQueue.pending;courseQueue.pending={};if(!changes||!Object.keys(changes).length||!courseOpen)return;
  const id=courseOpen.id;
  courseBusy=(async()=>{try{const saved=await api('/api/courses/'+encodeURIComponent(id),{changes,revision:courseOpen.revision},'PUT');
      if(courseOpen&&courseOpen.id===id){const stageChanged=saved.stage!==courseOpen.stage;Object.assign(courseOpen,{revision:saved.revision,stage:saved.stage,approved:saved.approved,history:saved.history});
        courseOpen.stale=!!(saved.approved&&saved.script_hash!==saved.approved.hash);const n=$('#course-saved');if(n)n.textContent='Saved';
        if(stageChanged){await courseFetch(id);renderCourseLesson();courses=null}}
    }catch(e){toast(e.message,true);const n=$('#course-saved');if(n)n.textContent='Not saved'}finally{courseBusy=null}})();
  await courseBusy;
}
async function courseAct(act){
  const l=courseOpen;if(!l)return;
  try{
    if(courseQueue.pending&&Object.keys(courseQueue.pending).length){clearTimeout(courseTimer);await courseSave()}
    if(act==='approve')await api(`/api/courses/${encodeURIComponent(l.id)}/approve`,{revision:courseOpen.revision});
    else if(act.startsWith('work:'))await api(`/api/courses/${encodeURIComponent(l.id)}/work`,{task:act.slice(5),note:($('#course-note')||{}).value||''});
    else if(act==='check'){toast('Checking every beat. This takes a little while.');await api(`/api/courses/${encodeURIComponent(l.id)}/check`,{});courseTab='slides'}
    else if(act==='publish')await api(`/api/courses/${encodeURIComponent(l.id)}/publish`,{});
    else if(act==='live')await api(`/api/courses/${encodeURIComponent(l.id)}/live`,{revision:courseOpen.revision});
    else if(act==='record'){location.href='/lessons?lesson='+encodeURIComponent(l.id);return}
    else if(act.startsWith('open:'))await api(`/api/courses/${encodeURIComponent(l.id)}/open`,{kind:act.slice(5)});
    await courseFetch(l.id);courses=null;renderCourseLesson();
  }catch(e){toast(e.message,true)}
}
async function courseMove(stage){
  try{await api(`/api/courses/${encodeURIComponent(courseOpen.id)}/stage`,{stage,revision:courseOpen.revision});await courseFetch(courseOpen.id);courses=null;renderCourseLesson()}
  catch(e){toast(e.message,true);renderCourseLesson()}
}
// While Saskia works or files copy, check back every few seconds.
function courseWatch(){
  clearInterval(coursePoll);
  const busy=courseOpen?courseBusyNow(courseOpen):courses&&courses.items.some(courseBusyNow);
  if(!busy)return;
  coursePoll=setInterval(async()=>{
    if(view!=='courses'||current){clearInterval(coursePoll);return}
    try{if(courseOpen){const id=courseOpen.id;const was=courseBusyNow(courseOpen);const fresh=await api('/api/courses/'+encodeURIComponent(id));if(courseOpen&&courseOpen.id===id&&was&&!courseBusyNow(fresh)){courseOpen=fresh;courses=null;renderCourseLesson()}}
      else{await courseLoad();renderCourses()}}catch{}
  },4000);
}

document.addEventListener('click',async e=>{
  if(view!=='courses')return;
  const t=e.target.closest('[data-course-filter],[data-course-stage],[data-course-open],[data-course-back],[data-course-act],[data-course-tab],[data-course-open-file],[data-course-cues]');if(!t)return;
  if(t.dataset.courseFilter){courseFilter=t.dataset.courseFilter;courseStageFilter='';renderCourses();return}
  if(t.dataset.courseStage!==undefined&&t.hasAttribute('data-course-stage')){courseStageFilter=courseStageFilter===t.dataset.courseStage?'':t.dataset.courseStage;renderCourses();return}
  if(t.dataset.courseOpen){try{if(courseQueue.pending&&Object.keys(courseQueue.pending).length){clearTimeout(courseTimer);await courseSave()}await courseFetch(t.dataset.courseOpen);renderCourseLesson();scrollTo(0,0)}catch(err){toast(err.message,true)}return}
  if(t.hasAttribute('data-course-back')){if(courseQueue.pending&&Object.keys(courseQueue.pending).length){clearTimeout(courseTimer);await courseSave()}courseOpen=null;courses=null;renderCourses();return}
  if(t.dataset.courseAct){courseAct(t.dataset.courseAct);return}
  if(t.dataset.courseTab){courseTab=t.dataset.courseTab;$('#course-body').innerHTML=courseBody(courseOpen);document.querySelectorAll('[data-course-tab]').forEach(b=>b.classList.toggle('active',b.dataset.courseTab===courseTab));return}
  if(t.dataset.courseOpenFile){try{await api(`/api/courses/${encodeURIComponent(courseOpen.id)}/open`,{kind:t.dataset.courseOpenFile})}catch(err){toast(err.message,true)}return}
  if(t.hasAttribute('data-course-cues')){try{const c=await api(`/api/courses/${encodeURIComponent(courseOpen.id)}/cues`);$('#course-cues').innerHTML=`<textarea class="ls-text" id="course-cue-text">${esc(c.text)}</textarea><button class="primary" data-course-cue-save>Save the cued script</button>`}catch(err){toast(err.message,true)}return}
});
document.addEventListener('click',async e=>{
  const b=e.target.closest('[data-course-cue-save]');if(!b||view!=='courses')return;
  try{await api(`/api/courses/${encodeURIComponent(courseOpen.id)}/cues`,{text:$('#course-cue-text').value});courseOpen.cues=!!$('#course-cue-text').value.trim();toast('Cued script saved.')}catch(err){toast(err.message,true)}
});
document.addEventListener('input',e=>{const f=e.target.closest('[data-course-field]');if(f&&view==='courses'&&courseOpen)courseQueue(f.dataset.courseField,f.value)});
document.addEventListener('change',e=>{const s=e.target.closest('[data-course-move]');if(s&&view==='courses'&&courseOpen)courseMove(s.value)});
document.addEventListener('submit',async e=>{
  const f=e.target.closest('[data-course-drive]');if(!f)return;e.preventDefault();
  try{await api('/api/courses/settings',{publish_root:f.root.value});toast('Google Drive folder saved.');courses=null;renderCourses()}catch(err){toast(err.message,true)}
});

if(typeof viewNames==='object')viewNames.courses='Lessons';
const coursesBaseRender=render;
render=function(){if(!current&&view==='courses')return renderCourses();if(view!=='courses'){courseOpen=null;clearInterval(coursePoll)}return coursesBaseRender()};
if(typeof initialView==='string'&&initialView==='lessons-board'){view='courses';refresh().then(render).catch(()=>{})}

// Lessons saved from a chat open their page from anywhere in Josh OS.
document.addEventListener('click',async e=>{const t=e.target.closest?.('[data-lesson-go]');if(!t)return;e.preventDefault();try{if(typeof current!=='undefined')current=null;if(typeof teamOpen!=='undefined')teamOpen=null;view='courses';history.replaceState(null,'','/#lessons-board');courses=null;await courseFetch(t.dataset.lessonGo);courseTab='script';await render();scrollTo(0,0)}catch(err){toast(err.message,true)}});
