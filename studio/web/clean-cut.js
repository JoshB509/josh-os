'use strict';
// The clean cut from AutoCut (studio/clean_edit.py). On a video Tariq cut with Josh's AutoCut, the Clean Cut tab shows
// Premiere's own words against what AutoCut really kept and took out. Josh selects words to take out or bring back,
// listens to the recording and the cut side by side, has Tariq build a fresh sequence, then approves it. Nothing
// downstream (animations, assembly) starts before that approval.
let ccData={},ccSel=null,ccCtx=2,ccPrev={},ccBusy=false,ccPoll=null;
const ccUrl=(pid,a)=>`/api/projects/${encodeURIComponent(pid)}/clean-edit${a?'/'+a:''}`;
const ccT=s=>`${Math.floor(s/60)}:${(s%60).toFixed(1).padStart(4,'0')}`;
const CC_BY={silences:'Silences',repeat:'Repeat',josh:'you',outside_camera:'before or after the camera'};
const CC_NATURAL=new Set(['sorry','okay','yes','no','very','so','that','had']);

async function ccLoad(pid){
  try{const d=await api(ccUrl(pid));ccData[pid]=Array.isArray(d?.words)?d:null}catch(e){ccData[pid]=null}
}
const ccNorm=t=>String(t).toLowerCase().replace(/[^a-z0-9']+/g,'');

// Things to listen to: repeats still in the cut, words cut through at a join, new edges with words right next to them.
function ccFlags(d){
  const out=[],kept=d.words.filter(w=>w.state!=='removed');
  for(let i=0;i<kept.length;i++){
    for(let n=4;n>=1;n--){
      const a=kept.slice(i,i+n),b=kept.slice(i+n,i+2*n);
      if(b.length<n||a.some((x,k)=>ccNorm(x.text)!==ccNorm(b[k].text)||!ccNorm(x.text)))continue;
      if(n===1&&CC_NATURAL.has(ccNorm(a[0].text)))continue;
      out.push({kind:'repeat',id:a[0].id,label:'Said twice',text:[...a,...b].map(x=>x.text).join(' '),at:a[0].start});i+=2*n-1;break;
    }
  }
  d.words.filter(w=>w.state==='partial'&&w.edge_sound?.sound).forEach(w=>out.push({kind:'partial',id:w.id,label:'Sound cut at a join',text:`${w.text} (${w.edge_sound.lost_ms} ms, ${Math.round(w.edge_sound.below_word_db)} dB under the word)`,at:w.start}));
  d.words.filter(w=>w.state!=='kept'&&w.removed_by==='silences').forEach(w=>out.push({kind:'silences',id:w.id,label:'Silences took a word',text:w.text,at:w.start}));
  (d.new_edges||[]).filter(e=>e.tight).forEach(e=>{
    const w=d.words.reduce((best,x)=>Math.abs(x.start-e.at)<Math.abs((best?.start??1e9)-e.at)?x:best,null);
    if(w)out.push({kind:'tight',id:w.id,label:'Words touch at a new cut',text:w.text,at:e.at});
  });
  const rank={repeat:0,tight:1,silences:2,partial:3};
  return out.sort((a,b)=>rank[a.kind]-rank[b.kind]||a.at-b.at);
}

function ccScript(d,flags){
  const fl=new Set(flags.map(f=>f.id));
  let html='',prevBy=null;
  for(const w of d.words){
    const by=w.state==='kept'?null:w.removed_by;
    if(by&&by!==prevBy&&w.state==='removed')html+=`<span class="cc-tag">${esc(CC_BY[by]||by)}</span>`;
    prevBy=by;
    const cls=['ef-w',w.state==='partial'&&!w.edge_sound?.sound?'kept edge':w.state,w.removed_by==='josh'||w.restored?'josh':'',w.restored?'restored':'',fl.has(w.id)?'flag':'',ccSel===w.id?'on':''].filter(Boolean).join(' ');
    const tip=`${ccT(w.start)} in the recording${w.clean_at!=null?`, ${ccT(w.clean_at)} in the cut`:''}${by?`. Taken out by ${CC_BY[by]||by}`:''}${w.restored?'. You brought it back':''}`;
    html+=`<span class="${cls}" data-cc-w="${esc(w.id)}" title="${esc(tip)}">${esc(w.text)}</span> `;
  }
  return `<div class="ef-script cc-script" id="cc-script">${html}</div>`;
}

function ccCard(p,d){
  const w=d.words.find(x=>x.id===ccSel);
  if(!w)return `<div class="ef-cardview empty"><p>Click a word or a flag to hear it in the recording and in the cut. Select words in the transcript to take them out or bring them back.</p></div>`;
  const pv=ccPrev[w.id+':'+ccCtx+':'+d.edits.length];
  const state=w.state==='kept'?(w.restored?'Kept, you brought it back':'Kept'):w.state==='partial'?`Partly kept (${Math.round(w.kept_fraction*100)}%), cut at a join`:`Taken out by ${CC_BY[w.removed_by]||w.removed_by}`;
  const act=w.state==='kept'?`<button class="subtle" data-cc-do="remove">Take this word out</button>`:`<button class="subtle" data-cc-do="restore">Bring this word back</button>`;
  return `<div class="ef-cardview">
    <div class="ef-card-head"><b>“${esc(w.text)}”</b><span>${ccT(w.start)}${w.clean_at!=null?` · ${ccT(w.clean_at)} in the cut`:''}</span></div>
    <p>${esc(state)}</p>
    <div class="ef-listen">
      ${pv?`<div><small>The recording</small>${efWave(pv.source_peaks,pv.source_cuts,pv.source_len)}<audio controls preload="none" src="${ccUrl(p.id,'audio/'+pv.source)}"></audio></div>
      <div><small>The cut</small>${efWave(pv.clean_peaks,[],0)}<audio controls preload="none" src="${ccUrl(p.id,'audio/'+pv.clean)}"></audio></div>
      <p class="hint">Red in the recording is what the cut leaves out.</p>`:`<button class="subtle" data-cc-listen ${ccBusy?'disabled':''}>Make the clips</button>`}
      <div class="ef-ctx">Context ${[2,5,10].map(s=>`<button class="${ccCtx===s?'on':''}" data-cc-ctx="${s}">${s}s</button>`).join('')}</div>
    </div>
    <div class="ef-row">${act}</div>
  </div>`;
}

function ccFlagList(flags){
  if(!flags.length)return '<p class="hint">Nothing flagged.</p>';
  return `<ol class="ef-queue">${flags.map(f=>`<li><button class="ef-qi ${ccSel===f.id?'on':''}" data-cc-w="${esc(f.id)}"><b>${esc(f.label)}</b><span>${ccT(f.at)} · ${esc(f.text)}</span></button></li>`).join('')}</ol>`;
}

function ccNext(p,d){
  const nEdits=d.edits.length,b=d.current_build,last=d.builds[d.builds.length-1];
  if(d.approved){
    const tx=d.transcripts||[],clean=tx.filter(t=>t.kind==='clean').pop(),fin=tx.filter(t=>t.kind==='final').pop();
    return `<section class="ef-card cc-next"><h2>Approved</h2>
      <p>You approved <b>${esc(d.approved.sequence)}</b> on ${esc(String(d.approved.at).slice(0,10))}. Tariq builds the animations on this cut, and nothing else changes it.</p>
      <div class="ef-row"><button class="subtle" data-cc-clean ${ccBusy?'disabled':''}>${clean?'Make the clean transcript again':'Make the clean transcript'}</button>
      <label class="cc-seq">Finished sequence <input id="cc-final-seq" value="${esc(fin?.sequence||d.approved.sequence)}"></label>
      <button class="subtle" data-cc-final ${ccBusy?'disabled':''}>Make the final transcript</button></div>
      ${clean?`<p class="hint">Clean transcript ${esc(clean.digest)} from ${esc(clean.sequence)}.</p>`:''}
      ${fin?`<p class="hint">Final transcript ${esc(fin.digest)} from ${esc(fin.sequence)}${fin.same===false?'. It differs from the approved cut, so your hand edits are in it.':'. It matches the approved cut.'}</p>`:''}
    </section>`;
  }
  const building=d._building;
  return `<section class="ef-card cc-next">
    <h2>${nEdits?`${nEdits} change${nEdits>1?'s':''} from AutoCut's cut`:'AutoCut\'s cut, as it came'}</h2>
    <p>${nEdits?(b?`<b>${esc(b.sequence)}</b> is built with these changes and matches them to the frame. Listen to it in Premiere, then approve it.`
      :`Build them in Premiere as a new sequence. ${esc(d.sequence)} and every earlier sequence stay as they are.`)
      :`This is <b>${esc(d.sequence)}</b>. Approve it as it is, or change words first.`}</p>
    ${last&&!last.exact?`<p class="ef-err">The last build, ${esc(last.sequence)}, didn't match the plan, so it can't be approved.</p>`:''}
    <div class="ef-row">
      <button class="subtle" data-cc-undo ${nEdits&&!ccBusy?'':'disabled'}>Undo</button>
      <button class="${nEdits&&!b?'primary':'subtle'}" data-cc-build ${nEdits&&!b&&!ccBusy&&!building?'':'disabled'}>${building?'Building…':'Build in Premiere'}</button>
      <button class="${d.approvable?'primary':'subtle'}" data-cc-approve ${d.approvable&&!ccBusy?'':'disabled'}>Approve ${esc(d.approvable||'the cut')}</button>
    </div>
  </section>`;
}

function ccPane(p){
  const d=ccData[p.id],c=d.counts||{},flags=ccFlags(d);
  const took=(k)=>(c['removed:'+k]||0)+(c['partial:'+k]||0);
  return `<div class="ef embedded cc">
    <header class="ef-head"><div><p>AutoCut's Silences and Repeat on <b>${esc(d.sequence)}</b>, read from Premiere's own words of the good mic. ${c.kept||0} words kept, ${took('repeat')} taken out by Repeat, ${took('silences')} by Silences${took('josh')?`, ${took('josh')} by you`:''}.</p></div></header>
    ${ccNext(p,d)}
    <div class="ef-body">
      <div class="ef-main">
        <p class="ef-legend"><span class="ef-w removed">Taken out</span> · <span class="ef-w partial">Cut at a join</span> · <span class="ef-w josh">Your change</span> · <span class="ef-w flag">Flagged</span>. Hover a word for its times.</p>
        ${ccScript(d,flags)}
        <div class="ef-selbar" id="cc-selbar" hidden><button class="subtle" data-cc-sel="remove">Take these words out</button><button class="subtle" data-cc-sel="restore">Bring these words back</button></div>
      </div>
      <aside class="ef-rail">${ccCard(p,d)}<h2>To listen to</h2>${ccFlagList(flags)}${ccCleanup(d)}</aside>
    </div>
  </div>`;
}

// Tariq's second cleanup pass (studio/cleanup_review.py). Proposals only: each waits for Josh to accept, dismiss or do by hand.
function ccMs(v){return v==null?'':`${v} ms`}
const CC_JOIN={within_reference_range_unreviewed:'pause within the reference editors\' range, not listened to',below_reference_lead_unreviewed:'shorter pause than the reference editors left, not listened to',above_review_gate_unreviewed:'longer than 700 ms, worth a listen'};
function ccClock(t){const m=Math.floor(t/60);return `${m}:${(t-m*60).toFixed(2).padStart(5,'0')}`}
function ccCleanup(d){
  const c=d.cleanup;
  if(!c)return `<h2>Tariq's cleanup</h2><p class="muted">No cleanup pass yet.</p><button class="subtle" data-cc-cleanup>Look for restarts and fillers</button>`;
  const rows=(c.proposals||[]).map(x=>{
    const dec=x.decision&&x.decision.decision, j=x.join||{};
    const what=x.status==='blocked'?'Blocked: the words touch, so it needs your ear or a hand edit in Premiere':
      `Cut "${esc(x.use_text||'')}"${x.status==='alt'?' (the second take)':''}, join ${esc(j.before||'')} | ${esc(j.after||'')} ${ccMs(j.pause_ms)}${x.join_flag?` · ${esc(CC_JOIN[x.join_flag]||x.join_flag)}`:''}`;
    return `<li><b>${esc(x.kind)}</b> at ${ccClock(x.at)}: "${esc(x.cut_text)}"<br><span class="muted">${what}</span><br>
      ${dec?`<span class="muted">You chose ${esc(dec)}.</span>`:`${x.status!=='blocked'?`<button class="subtle" data-cc-decide="accept" data-key="${x.key}">Accept</button>`:''}<button class="subtle" data-cc-decide="dismiss" data-key="${x.key}">Dismiss</button><button class="subtle" data-cc-decide="manual" data-key="${x.key}">By hand</button>`}</li>`}).join('');
  const ns=c.nonspeech||{}, yam=(ns.yamnet_candidates||[]).map(y=>`<li>${y.at.toFixed(2)} to ${y.end.toFixed(2)}s: YAMNet ${esc(y.top)} ${y[y.top].toFixed(2)}, speech ${y.Speech.toFixed(2)}</li>`).join('');
  return `<h2>Tariq's cleanup</h2><p class="muted">Proposals only. Nothing here has been listened to. Clips and images are in ${esc(c.folder)}.</p>
    <ul class="cc-cleanup">${rows||'<li class="muted">No restarts or filler clusters found.</li>'}</ul>
    <p class="muted">Sounds to listen to: ${ns.screen||0} from the level and spectrum screen, ${ns.yamnet||0} from YAMNet${ns.yamnet_available?'':' (YAMNet not available)'}.</p>${yam?`<ul class="cc-cleanup">${yam}</ul>`:''}
    <button class="subtle" data-cc-cleanup>Run the cleanup pass again</button>`;
}

function ccSelected(){
  const sel=getSelection();if(!sel||sel.isCollapsed)return [];
  return [...document.querySelectorAll('#cc-script .ef-w')].filter(el=>sel.containsNode(el,true)).map(el=>el.dataset.ccW);
}
async function ccPost(pid,act,body){
  ccBusy=true;vpEditRender();
  try{const r=await api(ccUrl(pid,act),body||{});return r}
  catch(e){toast(e.message,true);return null}
  finally{ccBusy=false}
}
// Every edit is one run of neighbouring words. A selection across kept and removed words is split into runs.
function ccRuns(d,ids,op){
  const idx=Object.fromEntries(d.words.map((w,i)=>[w.id,i])),want=op==='remove'?w=>w.state!=='removed':w=>w.state!=='kept';
  const pick=ids.map(i=>idx[i]).sort((a,b)=>a-b).filter(i=>want(d.words[i]));
  const runs=[];for(const i of pick){const r=runs[runs.length-1];if(r&&r[r.length-1]===i-1)r.push(i);else runs.push([i])}
  return runs.map(r=>r.map(i=>d.words[i].id));
}

document.addEventListener('click',async e=>{
  const p=current;if(!p||!ccData[p.id]||vpPiece(p)!=='edit')return;
  const d=ccData[p.id];
  const w=e.target.closest('[data-cc-w]');if(w&&!getSelection()?.toString()){ccSel=w.dataset.ccW;vpEditRender();return}
  const ctx=e.target.closest('[data-cc-ctx]');if(ctx){ccCtx=+ctx.dataset.ccCtx;vpEditRender();return}
  if(e.target.closest('[data-cc-listen]')){const r=await ccPost(p.id,'preview',{word:ccSel,context:ccCtx});if(r)ccPrev[ccSel+':'+ccCtx+':'+d.edits.length]=r;vpEditRender();return}
  const one=e.target.closest('[data-cc-do]');if(one){const r=await ccPost(p.id,one.dataset.ccDo,{words:[ccSel]});if(r)ccData[p.id]=r;vpEditRender();return}
  const sel=e.target.closest('[data-cc-sel]');if(sel){
    const runs=ccRuns(d,ccSelected(),sel.dataset.ccSel);getSelection().removeAllRanges();$('#cc-selbar').hidden=true;
    if(!runs.length){toast(sel.dataset.ccSel==='remove'?'Those words are already out':'Those words are already in',true);return}
    let r=null;for(const run of runs){r=await ccPost(p.id,sel.dataset.ccSel,{words:run});if(!r)break}
    if(r)ccData[p.id]=r;vpEditRender();return}
  if(e.target.closest('[data-cc-cleanup]')){const r=await ccPost(p.id,'cleanup');if(r){toast(`Cleanup pass: ${r.proposals.length} proposals`);await ccLoad(p.id)}vpEditRender();return}
  const dec=e.target.closest('[data-cc-decide]');if(dec){const r=await ccPost(p.id,'decide',{key:dec.dataset.key,decision:dec.dataset.ccDecide,revision:d.cleanup&&d.cleanup.revision&&d.cleanup.revision.digest});if(r)ccData[p.id]=r;vpEditRender();return}
  if(e.target.closest('[data-cc-undo]')){const r=await ccPost(p.id,'undo');if(r)ccData[p.id]=r;vpEditRender();return}
  if(e.target.closest('[data-cc-build]')){
    const before=d.builds.length,r=await ccPost(p.id,'build');if(!r){vpEditRender();return}
    toast('Tariq is building it in Premiere');ccData[p.id]={...r,_building:true};vpEditRender();
    const tick=async()=>{await ccLoad(p.id);const x=ccData[p.id];
      if(x&&x.builds.length===before){x._building=true;ccPoll=setTimeout(tick,3000)}
      else if(x){const nb=x.builds[x.builds.length-1];toast(nb.exact?`${nb.sequence} is built and matches the plan`:`${nb.sequence} did not match the plan`,!nb.exact)}
      if(current?.id===p.id)vpEditRender()};
    clearTimeout(ccPoll);ccPoll=setTimeout(tick,3000);return}
  if(e.target.closest('[data-cc-approve]')){
    if(!confirm(`Approve ${d.approvable} as the clean cut? Animations and assembly will use it.`))return;
    const r=await ccPost(p.id,'approve',{sequence:d.approvable});if(r){ccData[p.id]=r;toast('Approved')}vpEditRender();return}
  if(e.target.closest('[data-cc-clean]')){const r=await ccPost(p.id,'clean_transcript');if(r){toast(`Clean transcript made, ${r.words} words`);await ccLoad(p.id)}vpEditRender();return}
  if(e.target.closest('[data-cc-final]')){const seq=$('#cc-final-seq')?.value.trim();const r=await ccPost(p.id,'final_transcript',{sequence:seq});
    if(r){toast(r.same_as_approved?'Final transcript made. It matches the approved cut.':`Final transcript made. ${r.words_changed_by_hand.length} words differ from the approved cut.`);await ccLoad(p.id)}vpEditRender();return}
});
document.addEventListener('mouseup',()=>{const bar=$('#cc-selbar');if(bar)bar.hidden=!ccSelected().length});

// On the Clean Cut tab, a video with an AutoCut clean cut shows it in place of the older planner's script.
const ccBaseScript=vpEditScript;
vpEditScript=function(p,d){return ccData[p.id]?ccPane(p):ccBaseScript(p,d)};
const ccBaseLoad=vpEditLoad;
vpEditLoad=async function(p){await Promise.all([ccBaseLoad(p),ccLoad(p.id)])};
