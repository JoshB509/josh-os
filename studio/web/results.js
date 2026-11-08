'use strict';
// Results tab on every video: funnel stage, the views-to-customers numbers, thumbnail test
// rounds with screenshots, and the retention graph read against the script. Sam fills it in
// from YouTube Studio and Linklr; Josh can add screenshots and notes too. Everything autosaves.
let res=null,resFor=null,resTimer=null,resBusy=false,resAsking=false;
const RES_FUNNEL=[['top','Top','Reach: brings new people in'],['middle','Middle','Trust: teaches people who know you'],['bottom','Bottom','Converts: closest to what you sell']];
const RES_CHANGED=[['','What changed?'],['text','The text'],['visual','The visual style'],['font','The font'],['colour','The colour'],['face','The face'],['layout','The layout'],['other','Something else']];

function resNum(v){return v===null||v===undefined||v===''?'':v}
function resRate(a,b,per=1000){return a&&b?(a/b*per):null}
function resFmt(x,d=1){return x===null?'–':x>=100?Math.round(x).toLocaleString():x.toFixed(d)}
function resShot(aid,kind,i=''){return `<label class="res-shot ${aid?'has':''}" data-res-drop="${kind}" data-res-i="${i}">
  ${aid?`<img src="/assets/${esc(aid)}?w=1200" alt="Screenshot">`:`<span><b>Drop a screenshot</b><small>or click to choose one</small></span>`}
  <input type="file" accept="image/png,image/jpeg,image/webp" hidden data-res-file="${kind}" data-res-i="${i}"></label>`}

function resMarkup(){
  const r=res,m=r.metrics||{},p=current;
  const subsK=resRate(m.subscribers,m.views),mailsK=resRate(m.emails,m.views),conv=m.emails&&m.customers!=null?m.customers/m.emails*100:null;
  return `<div class="results">
    <section class="res-sam"><div>${team?.agents?teamAvatar(team.agents.find(a=>a.id==='sam')||{name:'Sam'},'sm'):''}<p>${r.review?esc(r.review):'Sam reads YouTube Studio, Linklr and your screenshots, lines the retention up with the script, and writes up what this video taught.'}</p></div>
      <button class="primary" data-res-ask ${resAsking?'disabled':''}>${resAsking?'Sam is reviewing…':r.review?'Ask Sam to update the review':'Ask Sam to review this video'}</button></section>

    <section class="res-block"><header><h3>Funnel stage</h3><p>Judge the video on what it's for.</p></header>
      <div class="res-funnel">${RES_FUNNEL.map(([k,l,h])=>`<button class="${r.funnel===k?'on':''}" data-res-funnel="${k}"><b>${l}</b><small>${h}</small></button>`).join('')}</div></section>

    <section class="res-block"><header><h3>Views to customers</h3><p>${m.as_of?`As of ${esc(m.as_of)}. `:''}Linklr tracks email signups and customers from the video's links.</p></header>
      <div class="res-chain">
        ${[['views','Views'],['subscribers','Subscribers'],['emails','Email signups'],['customers','Customers']].map(([k,l],i)=>`<label class="res-metric"><small>${l}</small><input type="number" min="0" data-res-metric="${k}" value="${resNum(m[k])}" placeholder="–">
          ${i===1?`<em>${resFmt(subsK)} per 1,000 views</em>`:i===2?`<em>${resFmt(mailsK)} per 1,000 views</em>`:i===3?`<em>${conv===null?'–':conv.toFixed(1)+'%'} of signups</em>`:'<em>&nbsp;</em>'}</label>`).join('<i class="res-arrow">→</i>')}
      </div>
      <div class="res-small">${[['ctr','Click-through rate %'],['avg_percent_viewed','Average % viewed'],['avg_view_duration','Average view (seconds)'],['impressions','Impressions']].map(([k,l])=>`<label><small>${l}</small><input type="number" min="0" step="any" data-res-metric="${k}" value="${resNum(m[k])}" placeholder="–"></label>`).join('')}
        <label><small>Numbers as of</small><input type="date" data-res-metric="as_of" value="${esc(m.as_of||'')}"></label></div></section>

    <section class="res-block"><header><h3>Thumbnail tests</h3><p>Every video goes out with 3 thumbnails. Test one idea per round, keep the winner, then test the next thing.</p></header>
      ${r.tests.map((t,i)=>`<article class="res-round ${t.winner?'won':''}">
        <header><b>Round ${i+1}</b><select data-res-test="${i}" data-k="changed">${RES_CHANGED.map(([k,l])=>`<option value="${k}" ${t.changed===k?'selected':''}>${l}</option>`).join('')}</select>
          <span class="res-status">${t.winner?`${esc(t.winner)} won`:'Running'}</span><button class="subtle small" data-res-remove="${i}" aria-label="Remove round ${i+1}">Remove</button></header>
        <input class="res-hyp" data-res-test="${i}" data-k="hypothesis" value="${esc(t.hypothesis)}" placeholder="What are we testing, and what do we expect?">
        <div class="res-round-body">${resShot(t.screenshot,'test',i)}
          <div class="res-variants">${[0,1,2].map(j=>{const v=t.variants[j]||{label:'ABC'[j],text:'',look:'',share:null};return `<div class="res-variant ${t.winner&&t.winner===v.label?'winner':''}">
            <header><b>${esc(v.label)}</b><label class="res-win"><input type="radio" name="win-${i}" data-res-winner="${i}" value="${esc(v.label)}" ${t.winner===v.label?'checked':''}> Winner</label></header>
            <input data-res-variant="${i}" data-j="${j}" data-k="text" value="${esc(v.text)}" placeholder="Text on the thumbnail">
            <input data-res-variant="${i}" data-j="${j}" data-k="look" value="${esc(v.look)}" placeholder="Look (style, font, colour, face)">
            <label class="res-share"><input type="number" min="0" max="100" step="0.1" data-res-variant="${i}" data-j="${j}" data-k="share" value="${resNum(v.share)}" placeholder="–"><small>% watch time</small></label></div>`}).join('')}</div></div>
        <label class="res-why"><small>Why it won, and what it teaches</small><textarea rows="2" data-res-test="${i}" data-k="why" placeholder="Sam fills this in once there's a winner">${esc(t.why)}</textarea></label>
      </article>`).join('')||'<p class="res-empty">No tests recorded yet.</p>'}
      ${r.tests.length<12?`<button class="small" data-res-add-round>＋ Add a test round</button>`:''}</section>

    <section class="res-block"><header><h3>Retention</h3><p>Where people dropped or stayed, lined up with the script.</p></header>
      <div class="res-retention">${resShot(r.retention.screenshot,'retention')}
        <div>${r.retention.summary?`<p class="res-summary">${esc(r.retention.summary)}</p>`:''}
          ${(r.retention.dips||[]).length?`<ol class="res-dips">${r.retention.dips.map(d=>`<li class="k-${esc(d.kind)}"><span class="at">${esc(d.at)}</span><span class="kind">${esc(d.kind)}</span><q>${esc(d.script)}</q><p>${esc(d.why)}</p></li>`).join('')}</ol>`:'<p class="res-empty">Drop the retention graph in and ask Sam to line it up with the script.</p>'}
          <label><small>Your notes</small><textarea rows="2" data-res-retention="notes">${esc(r.retention.notes||'')}</textarea></label></div></div></section>

    ${r.lessons.length?`<section class="res-block"><header><h3>What this video taught</h3></header><ul class="res-lessons">${r.lessons.map(l=>`<li>${esc(l)}</li>`).join('')}</ul></section>`:''}
    <p class="res-saved" id="res-saved">${r.updated_at?'Saved':'Nothing saved yet'}</p>
  </div>`;
}
function resPaint(){const body=$('#tab-body');if(!body||tab!=='results'||!res)return;const y=scrollY;body.innerHTML=resMarkup();scrollTo(0,y)}
async function resLoad(){const id=current?.id;if(!id)return;try{if(!team)team=await api('/api/team');res=await api('/api/results/'+id);resFor=id;if(current?.id===id&&tab==='results')resPaint()}catch(e){toast(e.message,true)}}
function resNote(t){const n=$('#res-saved');if(n)n.textContent=t}
function resQueue(paint=false){resNote('Saving…');clearTimeout(resTimer);resTimer=setTimeout(()=>resSave(paint),600)}
async function resSave(paint){
  if(!res||!current)return;if(resBusy){await resBusy;return resSave(paint)}
  const id=current.id,body={revision:res.revision,changes:{funnel:res.funnel,metrics:res.metrics,tests:res.tests,retention:res.retention}};
  resBusy=(async()=>{try{const saved=await api('/api/results/'+id,body,'PUT');if(current?.id===id){res=saved;resNote('Saved');if(paint)resPaint()}}
    catch(e){toast(e.message,true);resNote('Not saved');await resLoad()}})();
  await resBusy;resBusy=false;
}
async function resUpload(file){const data=await new Promise((ok,bad)=>{const r=new FileReader();r.onload=()=>ok(r.result.split(',')[1]);r.onerror=bad;r.readAsDataURL(file)});return (await api('/api/images',{name:file.name,data})).asset}
async function resPlace(kind,i,file){
  if(!/^image\/(png|jpeg|webp)$/.test(file.type)){toast('Use a PNG, JPEG or WebP screenshot',true);return}
  try{const aid=await resUpload(file);if(kind==='test')res.tests[i].screenshot=aid;else res.retention.screenshot=aid;resPaint();resQueue()}catch(e){toast(e.message,true)}
}

document.addEventListener('input',e=>{if(tab!=='results'||!res)return;const el=e.target,d=el.dataset;
  if(d.resMetric){const v=d.resMetric==='as_of'?el.value:(el.value===''?null:Number(el.value));if(v===null)delete res.metrics[d.resMetric];else res.metrics[d.resMetric]=v;resQueue()}
  else if(d.resTest!==undefined&&d.k){res.tests[+d.resTest][d.k]=el.value;resQueue()}
  else if(d.resVariant!==undefined){const t=res.tests[+d.resVariant];while(t.variants.length<=+d.j)t.variants.push({label:'ABC'[t.variants.length],text:'',look:'',share:null});
    t.variants[+d.j][d.k]=d.k==='share'?(el.value===''?null:Number(el.value)):el.value;resQueue()}
  else if(d.resRetention){res.retention[d.resRetention]=el.value;resQueue()}
});
document.addEventListener('change',e=>{if(tab!=='results'||!res)return;const el=e.target,d=el.dataset;
  if(d.resWinner!==undefined){const t=res.tests[+d.resWinner];while(t.variants.length<3)t.variants.push({label:'ABC'[t.variants.length],text:'',look:'',share:null});t.winner=el.value;resQueue(true)}
  if(d.resFile&&el.files[0])resPlace(d.resFile,+d.resI,el.files[0]);
  if(el.tagName==='SELECT'&&d.resTest!==undefined){res.tests[+d.resTest][d.k]=el.value;resQueue()}
});
document.addEventListener('click',async e=>{if(tab!=='results'||!res||!current)return;
  const f=e.target.closest('[data-res-funnel]');if(f){res.funnel=res.funnel===f.dataset.resFunnel?'':f.dataset.resFunnel;resPaint();resQueue();return}
  if(e.target.closest('[data-res-add-round]')){const prev=res.tests.at(-1);res.tests.push({changed:'',hypothesis:'',screenshot:'',winner:'',why:'',
    variants:['A','B','C'].map((l,j)=>({label:l,text:prev?.winner&&prev.changed==='text'?(prev.variants.find(v=>v.label===prev.winner)?.text||''):'',look:'',share:null}))});resPaint();resQueue();return}
  const rm=e.target.closest('[data-res-remove]');if(rm&&confirm('Remove this test round?')){res.tests.splice(+rm.dataset.resRemove,1);resPaint();resQueue();return}
  if(e.target.closest('[data-res-ask]')){const p=current;resAsking=true;resPaint();
    try{await resSave();const r=await api('/api/agents/chat',{agent_id:'sam',context:{kind:'project',id:p.id},
      message:`Review "${p.name}" for its Results tab. Open any screenshots saved on it, check YouTube Studio and Linklr for its numbers and thumbnail test, line the retention up with the script, and save the numbers, test rounds, retention reading, your review and the lessons it teaches.`});
      toast('Sam has reviewed the video');if(r?.reply?.text)res.review=res.review||r.reply.text}
    catch(err){toast(err.message,true)}finally{resAsking=false;if(current?.id===p.id){await resLoad()}}}
});
document.addEventListener('dragover',e=>{const z=e.target.closest?.('[data-res-drop]');if(z&&e.dataTransfer.types.includes('Files')){e.preventDefault();z.classList.add('over')}});
document.addEventListener('dragleave',e=>{const z=e.target.closest?.('[data-res-drop]');if(z&&!z.contains(e.relatedTarget))z.classList.remove('over')});
document.addEventListener('drop',e=>{const z=e.target.closest?.('[data-res-drop]');if(!z||tab!=='results')return;e.preventDefault();z.classList.remove('over');
  const f=e.dataTransfer.files[0];if(f)resPlace(z.dataset.resDrop,+z.dataset.resI,f)});

// Add the Results tab to the video page.
const resBaseDetail=renderDetail;
renderDetail=function(){
  const showing=tab==='results';if(showing)tab='brief';
  let r;try{r=resBaseDetail.apply(this,arguments)}finally{if(showing)tab='results'}
  const tabs=document.querySelector('#workspace .tabs');
  if(tabs&&!tabs.querySelector('[data-tab=results]'))tabs.insertAdjacentHTML('beforeend',`<button data-tab="results" class="${showing?'active':''}">Results</button>`);
  if(showing){tabs?.querySelectorAll('[data-tab]').forEach(b=>b.classList.toggle('active',b.dataset.tab==='results'));
    const body=$('#tab-body');if(body){if(res&&resFor===current?.id)body.innerHTML=resMarkup();else{body.innerHTML='<p class="hint">Getting the results…</p>';resLoad()}}}
  return r;
};
