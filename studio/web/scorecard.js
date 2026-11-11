'use strict';
// The business scorecard: six cards, the last 30 completed UTC days against the 30 before, and month to date against
// the revenue goal. Numbers come from /api/scorecard. Anything a source can't give is shown as unavailable with the
// reason, never as zero, and a change figure only shows when both windows are fully recorded.
let scData=null,scMode='daily',scBusy=false,scPoll=null,scOpenDef={};

const scNum=(v,unit,exact)=>{
  if(v==null)return '—';
  if(unit==='usd')return (v<0?'−':'')+'$'+Math.abs(v).toLocaleString(undefined,{minimumFractionDigits:exact?2:0,maximumFractionDigits:exact?2:0});
  return Math.round(v).toLocaleString();
};
const scDelta=(d,unit)=>d==null?'':`<span class="sc-delta ${d>0?'up':d<0?'down':''}">${d>0?'+':d<0?'−':'±'}${scNum(Math.abs(d),unit)}</span>`;
const scDate=(s,opts)=>new Date(s+'T12:00:00Z').toLocaleDateString(undefined,Object.assign({day:'numeric',month:'short',timeZone:'UTC'},opts||{}));
const scWhen=s=>s?new Date(s).toLocaleString(undefined,{day:'numeric',month:'short',hour:'numeric',minute:'2-digit'}):'unknown';

function scDays(){
  const w=scData.windows,out={cur:[],prev:[]};
  for(let i=0;i<30;i++){const c=new Date(w.current.start+'T00:00:00Z');c.setUTCDate(c.getUTCDate()+i);const p=new Date(w.previous.start+'T00:00:00Z');p.setUTCDate(p.getUTCDate()+i);
    out.cur.push(c.toISOString().slice(0,10));out.prev.push(p.toISOString().slice(0,10))}
  return out;
}
// Weekly: four 7 day groups and a final 2 day group, labelled with their real dates. Sums, since these are counts.
function scGroups(vals,dates){
  if(scMode==='daily')return vals.map((v,i)=>({v,from:dates[i],to:dates[i]}));
  const g=[[0,7],[7,14],[14,21],[21,28],[28,30]];
  return g.map(([a,b])=>{const part=vals.slice(a,b),got=part.filter(x=>x!=null);return {v:got.length?got.reduce((s,x)=>s+x,0):null,partial:got.length&&got.length<part.length,from:dates[a],to:dates[b-1]}});
}
function scChart(m,key){
  if(!m.daily)return '';
  const d=scDays(),cur=scGroups(m.daily.current,d.cur),prev=scGroups(m.daily.previous,d.prev);
  // One scale for both windows. A metric that can go below zero, like net subscribers, gets a zero line with losses below it.
  const all=[...cur,...prev].map(x=>x.v).filter(v=>v!=null),hi=Math.max(1,...all),lo=Math.min(0,...all);
  const W=300,H=96,n=cur.length,step=W/n,bw=Math.max(2,step*(scMode==='daily'?.62:.7));
  const y=v=>3+(hi-v)/(hi-lo)*(H-6),zero=y(0);
  const bars=cur.map((x,i)=>x.v==null?`<rect class="sc-gap" x="${(i*step+(step-bw)/2).toFixed(1)}" y="${(zero-1).toFixed(1)}" width="${bw.toFixed(1)}" height="2"/>`
    :`<rect class="sc-bar${x.partial?' partial':''}${x.v<0?' neg':''}" data-sc-tip="${key}:${i}" x="${(i*step+(step-bw)/2).toFixed(1)}" y="${Math.min(y(x.v),zero).toFixed(1)}" width="${bw.toFixed(1)}" height="${Math.max(1,Math.abs(y(x.v)-zero)).toFixed(1)}"/>`).join('')
    +(lo<0?`<line class="sc-zero" x1="0" x2="${W}" y1="${zero.toFixed(1)}" y2="${zero.toFixed(1)}"/>`:'');
  let path='',pen=false;
  prev.forEach((x,i)=>{if(x.v==null){pen=false;return}const px=(i*step+step/2).toFixed(1),py=y(x.v).toFixed(1);path+=`${pen?'L':'M'}${px} ${py}`;pen=true});
  const hits=cur.map((x,i)=>`<rect class="sc-hit" data-sc-tip="${key}:${i}" x="${(i*step).toFixed(1)}" y="0" width="${step.toFixed(1)}" height="${H}"/>`).join('');
  const first=cur[0],last=cur[n-1];
  return `<figure class="sc-chart" data-sc-chart="${key}">
    <svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="${esc(m.label)} each ${scMode==='daily'?'day':'week'}, with the earlier 30 days as a line">${bars}<path class="sc-prev" d="${path}"/>${hits}</svg>
    <figcaption><span>${scDate(first.from)}</span>${m.chart_basis?`<span class="sc-basis">${esc(m.chart_basis)}</span>`:''}<span>${scDate(last.to)}</span></figcaption>
    <div class="sc-tip" hidden></div>
  </figure>`;
}
function scTip(key,i){
  const [ck,mk]=key.split('|'),c=scData.cards.find(x=>x.key===ck),m=c.headline,d=scDays();
  const cur=scGroups(m.daily.current,d.cur)[i],prev=scGroups(m.daily.previous,d.prev)[i];
  const range=g=>g.from===g.to?scDate(g.from,{weekday:'short'}):`${scDate(g.from)} to ${scDate(g.to)}`;
  return `<b>${range(cur)}</b><span>${cur.v==null?'Not recorded':scNum(cur.v,m.unit,true)+(cur.partial?' (some days not recorded)':'')}</span>
    <small>${range(prev)}: ${prev.v==null?'not recorded':scNum(prev.v,m.unit,true)}</small>`;
}
function scMetricRow(s,i,ck){
  const open=scOpenDef[ck+':'+i];
  return `<li class="sc-row ${s.available?'':'na'}">
    <button class="sc-row-main" data-sc-def="${ck}:${i}" aria-expanded="${!!open}"><span class="sc-row-label">${esc(s.label)}</span>
      <span class="sc-row-val">${s.available?scNum(s.value,s.unit):'Unavailable'}${s.available?scDelta(s.delta,s.unit):''}</span>
      <span class="sc-row-prev">${s.available?(s.previous==null?'Earlier: not recorded':`Earlier ${scNum(s.previous,s.unit)}${s.previous_partial?' (partial)':''}`):''}</span></button>
    ${open?`<p class="sc-def">${esc(s.definition||'')}${s.reason?` <em>${esc(s.reason)}</em>`:''}${s.previous_note?` <em>${esc(s.previous_note)}</em>`:''}${s.source?` <span>Source: ${esc(s.source)}.</span>`:''}</p>`:''}
  </li>`;
}
function scCard(c){
  const m=c.headline,k=c.key,open=scOpenDef[k+':h'];
  return `<article class="sc-card ${m.available?'':'na'}" aria-labelledby="sc-${k}">
    <header class="sc-card-head"><h2 id="sc-${k}">${esc(c.title)}</h2><button class="sc-info" data-sc-def="${k}:h" aria-expanded="${!!open}" aria-label="What ${esc(m.label)} means">i</button></header>
    <p class="sc-label">${esc(m.label)}</p>
    ${m.available?`<p class="sc-big">${scNum(m.value,m.unit)}${scDelta(m.delta,m.unit)}</p>
      <p class="sc-context">${m.previous==null?'Earlier 30 days not recorded':`Earlier 30 days ${scNum(m.previous,m.unit)}${m.previous_partial?', partly recorded':''}`}${m.delta==null&&m.previous!=null?' · no fair change figure':''}</p>`
      :`<p class="sc-big na">Unavailable</p><p class="sc-context">${esc(m.reason||'')}</p>`}
    ${open?`<p class="sc-def">${esc(m.definition||'')}${m.previous_note?` <em>${esc(m.previous_note)}</em>`:''}${m.source?` <span>Source: ${esc(m.source)}.</span>`:''}</p>`:''}
    ${m.available?scChart(m,k):'<div class="sc-chart empty"><span>No daily figures</span></div>'}
    ${m.note?`<p class="sc-note">${esc(m.note)}</p>`:''}
    <ul class="sc-rows">${c.supporting.map((s,i)=>scMetricRow(s,i,k)).join('')}</ul>
  </article>`;
}
function scMtd(){
  const t=scData.mtd,g=scData.goal;
  if(!t)return '';
  const pct=t.course_upsell==null?0:Math.min(100,t.course_upsell/t.target*100);
  return `<section class="sc-mtd" aria-labelledby="sc-mtd-h">
    <div class="sc-mtd-main">
      <h2 id="sc-mtd-h">${esc(t.month)} so far</h2>
      <p class="sc-context">${t.completed_days} completed day${t.completed_days===1?'':'s'}, to ${t.through?scDate(t.through):'—'} (UTC). Separate from the rolling 30 days.</p>
      <p class="sc-big">${scNum(t.course_upsell,'usd')} <span class="sc-of">of ${scNum(t.target,'usd')}</span></p>
      <div class="sc-meter" role="meter" aria-valuemin="0" aria-valuemax="${t.target}" aria-valuenow="${t.course_upsell||0}" aria-label="Course and upsell revenue against the goal"><i style="width:${pct.toFixed(1)}%"></i></div>
      <p class="sc-context">Goal basis: courses and upsells only, as set in Tasks &amp; Goals ("${esc(g.title)}"). ${esc(g.note||'')}</p>
    </div>
    <dl class="sc-mtd-facts">
      <div><dt>Same ${t.completed_days} days of ${esc(t.compare.month)}</dt><dd>${scNum(t.course_upsell_prev,'usd')}${t.course_upsell!=null&&t.course_upsell_prev!=null?scDelta(t.course_upsell-t.course_upsell_prev,'usd'):''}</dd></div>
      <div><dt>Needed each remaining day</dt><dd>${t.needed_per_day==null?'—':scNum(Math.max(0,t.needed_per_day),'usd')}</dd></div>
      <div><dt>All payments so far</dt><dd>${scNum(t.gross,'usd')}<small>course, upsells, clients and other, not Udemy</small></dd></div>
    </dl>
  </section>`;
}
function scFresh(){
  const f=scData.freshness,a=scData.attempt;
  const stale=f&&f.youtube_data_through&&f.youtube_data_through<scData.windows.current.end;
  return `<div class="sc-fresh">
    <div class="sc-fresh-facts">
      ${f?`<span>Refreshed ${scWhen(f.fetched_at)}${f.response_seconds?`, took ${f.response_seconds}s`:''}</span>
      <span>Payments to ${scWhen(f.payments_through)}</span><span>Email list synced ${scWhen(f.email_list_synced)}</span>
      <span class="${stale?'warn':''}">YouTube data to ${f.youtube_data_through?scDate(f.youtube_data_through,{year:'numeric'}):'unknown'}${stale?', behind':''}</span>`:'<span>No figures yet</span>'}
      ${a&&a.state==='failed'?`<span class="warn">Last refresh failed ${scWhen(a.at)}: ${esc(a.error)}. Showing the last good figures.</span>`:''}
      ${f&&f.missing&&f.missing.length?`<span class="warn">Not returned last time: ${esc(f.missing.join(', '))}</span>`:''}
    </div>
    <div class="sc-fresh-act">
      <div class="segmented" role="group" aria-label="Chart grouping"><button data-sc-mode="daily" class="${scMode==='daily'?'active':''}">Daily</button><button data-sc-mode="weekly" class="${scMode==='weekly'?'active':''}">Weekly</button></div>
      <button class="subtle" data-sc-refresh ${scData.refreshing?'disabled':''}>${scData.refreshing?'Refreshing…':'Refresh'}</button>
    </div>
  </div>`;
}
function renderScorecard(){
  const w=scData?.windows;
  if(typeof viewNames!=='undefined')viewNames.scorecard='Scorecard';  // the shell names each view from this list
  page('Scorecard','');
  if(!scData){$('#workspace').innerHTML='<p class="hint">Getting the scorecard…</p>';scLoad();return}
  $('#workspace').innerHTML=`<div class="sc"><p class="sc-window">Last 30 completed days, <b>${scDate(w.current.start)} to ${scDate(w.current.end,{year:'numeric'})}</b>, against ${scDate(w.previous.start)} to ${scDate(w.previous.end)}. Days are UTC.</p>${scFresh()}${scData.snapshot?scMtd()+`<div class="sc-grid">${scData.cards.map(scCard).join('')}</div>`
    :`<div class="sc-empty"><p>${scData.refreshing?'Getting the first figures from Linklr. This takes about half a minute.':'No figures yet.'}</p></div>`}
    ${(scData.freshness?.warnings||[]).length?`<ul class="sc-warnings">${scData.freshness.warnings.map(x=>`<li>${esc(x)}</li>`).join('')}</ul>`:''}</div>`;
}
async function scLoad(){
  try{scData=await api('/api/scorecard')}catch(e){toast(e.message,true);return}
  if(view==='scorecard'&&!current)renderScorecard();
  clearTimeout(scPoll);if(scData.refreshing)scPoll=setTimeout(()=>{if(view==='scorecard')scLoad()},4000);
}
document.addEventListener('click',async e=>{
  if(view!=='scorecard'||current)return;
  const md=e.target.closest('[data-sc-mode]');if(md){scMode=md.dataset.scMode;renderScorecard();return}
  const df=e.target.closest('[data-sc-def]');if(df){scOpenDef[df.dataset.scDef]=!scOpenDef[df.dataset.scDef];renderScorecard();return}
  if(e.target.closest('[data-sc-refresh]')){try{await api('/api/scorecard/refresh',{});scData.refreshing=true;renderScorecard();scLoad()}catch(err){toast(err.message,true)}return}
  const t=e.target.closest('[data-sc-tip]');if(t)scShowTip(t);
});
function scShowTip(t){
  const fig=t.closest('.sc-chart'),tip=fig.querySelector('.sc-tip');const [ck,i]=t.dataset.scTip.split(':');
  fig.querySelectorAll('.sc-bar.on').forEach(b=>b.classList.remove('on'));fig.querySelector(`.sc-bar[data-sc-tip="${ck}:${i}"]`)?.classList.add('on');
  tip.innerHTML=scTip(ck,+i);tip.hidden=false;
  const r=t.getBoundingClientRect(),fr=fig.getBoundingClientRect();tip.style.left=Math.min(Math.max(0,r.left-fr.left+r.width/2-70),fr.width-140)+'px';
}
document.addEventListener('pointerover',e=>{if(view!=='scorecard')return;const t=e.target.closest?.('[data-sc-tip]');if(t)scShowTip(t)});
document.addEventListener('pointerout',e=>{if(view!=='scorecard')return;const fig=e.target.closest?.('.sc-chart');if(fig&&!fig.contains(e.relatedTarget)){fig.querySelector('.sc-tip').hidden=true;fig.querySelectorAll('.sc-bar.on').forEach(b=>b.classList.remove('on'))}});
const scBaseRender=render;
render=function(){if(!current&&view==='scorecard')return renderScorecard();return scBaseRender.apply(this,arguments)};
// Opening Josh OS at /#scorecard goes straight here, the way the other pages do it.
if(typeof viewNames!=='undefined')viewNames.scorecard='Scorecard';
if(typeof initialView==='string'&&initialView==='scorecard'){view='scorecard';document.querySelectorAll('[data-view]').forEach(b=>b.classList.toggle('active',b.dataset.view===view));refresh().then(render).catch(()=>{})}
