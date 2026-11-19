'use strict';
// Node-only tests of the shipped card/poller with a fake DOM, clock and fetch. No browser/network.
const fs=require('fs'),vm=require('vm'),assert=require('assert'),path=require('path');
const source=fs.readFileSync(path.join(__dirname,'../studio/web/team.js'),'utf8').replace(/\r\n/g,'\n');
const code=source.slice(source.indexOf('// BEGIN FRAMEIO CHAT PROGRESS'),source.indexOf('// END FRAMEIO CHAT PROGRESS'));
let passed=0;
async function test(name,fn){await fn();passed++;console.log('ok '+name)}
function fixture(){
  let clock=Date.parse('2026-11-19T12:00:00Z'),id=0,calls=[],responses=[],nodes=[],timers=new Map(),observer;
  const events={},windowEvents={},composer={value:'Unsaved words',selectionStart:3,selectionEnd:5},thread={scrollTop:41};
  const row=(changes={})=>({kind:'frameio',project_id:'project-1',review_id:'review-1',state:'uploading',terminal:false,
    progress:{bytes_sent:100,bytes_total:400,parts_done:1,parts:4},updated_at:new Date(clock).toISOString(),...changes});
  const ctx={console,Map,URL,AbortController,encodeURIComponent,Number,Object,Error,String,Math,
    Date:class extends Date{constructor(...a){super(...(a.length?a:[clock]))}static now(){return clock}},
    view:'team',teamOpen:'tariq',current:null,token:'fixture-only',roomThread:{tariq:[{saved:[row()]}]},
    esc:s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])),
    document:{hidden:false,addEventListener:(e,f)=>(events[e]??=[]).push(f),querySelectorAll:()=>nodes},
    window:{addEventListener:(e,f)=>(windowEvents[e]??=[]).push(f)},
    $:selector=>selector==='#workspace'?{}:selector==='#room-thread'?(ctx.view==='team'?thread:null):selector==='#room-input'?composer:null,
    MutationObserver:class{constructor(fn){observer=fn}observe(){}},
    setTimeout:(fn,delay)=>{timers.set(++id,{fn,at:clock+delay});return id},clearTimeout:key=>timers.delete(key),
    fetch:(url,opts)=>{calls.push({url,opts});const next=responses.shift();return typeof next==='function'?next(url,opts):Promise.resolve(next||{ok:true,status:200,json:async()=>row()})},
  };
  vm.createContext(ctx);vm.runInContext(code+'\nglobalThis.testAPI={sync:roomFrameioSync,card:roomFrameioCard,watching:roomFrameioWatching,cache:roomFrameioCache,link:roomFrameioLink};',ctx);
  const api=ctx.testAPI,key='project-1:review-1';
  function cards(n=1){nodes=Array.from({length:n},()=>({dataset:{frameioKey:key},outerHTML:''}));return nodes}
  async function flush(){for(let i=0;i<12;i++)await Promise.resolve()}
  async function advance(ms){clock+=ms;for(let n=0;n<50;n++){const due=[...timers].filter(([,t])=>t.at<=clock);if(!due.length)break;for(const [k,t]of due){if(!timers.has(k))continue;timers.delete(k);t.fn()}await flush()}}
  const answer=x=>({ok:true,status:200,json:async()=>row(x)});
  return {ctx,api,key,row,cards,calls,responses,timers,composer,thread,flush,advance,answer,
    fire:async(e,win=false)=>{for(const f of (win?windowEvents:events)[e]||[])f();await flush()},
    mutate:async()=>{observer();await flush()},click:async()=>{for(const f of events.click||[])f({target:{closest:()=>({dataset:{frameioCheck:key}})}});await flush()}};
}
(async()=>{
  await test('integration resumes receipts on profile and room render; other job watcher unchanged',async()=>{
    assert(source.includes("if(x.kind==='frameio')return roomFrameioCard(x)"));
    assert(source.includes('hubStart(a);\n  roomFrameioSync();'));
    assert(source.includes('roomFrameioSync();\n}\nfunction roomWorkRender'));
    assert(source.includes("(x.kind==='thumbnail'||x.kind==='edit')&&x.action==='started'"));
  });
  await test('exact review route and GET only; no latest/auth/external polling',async()=>{
    const f=fixture();f.cards();f.api.sync();await f.flush();
    assert.equal(f.calls.length,1);assert.equal(f.calls[0].url,'/api/projects/project-1/frameio/review-1/status');
    assert.equal(f.calls[0].opts.method,'GET');assert.equal(f.calls[0].opts.cache,'no-store');
  });
  await test('numeric bar only from validated counters; no invented elapsed percent',async()=>{
    const f=fixture();let html=f.api.card(f.row());assert(html.includes('value="100"'));assert(html.includes('100 of 400 bytes accepted'));
    for(const progress of [{},{bytes_sent:true,bytes_total:400},{bytes_sent:-1,bytes_total:400},{bytes_sent:401,bytes_total:400},{bytes_sent:1,bytes_total:0},{bytes_sent:'1',bytes_total:400}]){
      assert(!f.api.card(f.row({progress})).includes('<progress'));
    }
    html=f.api.card(f.row({progress:{parts_done:2,parts:4}}));assert(html.includes('2 of 4 parts accepted'));
  });
  await test('100 percent uploaded still processing and has no success link',async()=>{
    const f=fixture(),html=f.api.card(f.row({state:'processing',progress:{bytes_sent:400,bytes_total:400},view_url:'https://next.frame.io/review/a'}));
    assert(html.includes('Processing'));assert(html.includes('processing still pending'));assert(!html.includes('Open verified review'));
  });
  await test('verified complete permits exact https hosts only',async()=>{
    const f=fixture();for(const url of ['https://next.frame.io/review/a','https://app.frame.io/projects/b'])assert(f.api.card(f.row({state:'complete',view_url:url})).includes('Open verified review'));
    for(const url of ['http://next.frame.io/a','https://next.frame.io.evil.test/a','https://user@next.frame.io/a','https://next.frame.io/a?token=secret','https://next.frame.io/a#secret','https://next.frame.io:444/a','javascript:alert(1)'])assert(!f.api.card(f.row({state:'complete',view_url:url})).includes('href='));
  });
  await test('completion is from exact status, never unrelated chat message',async()=>{
    const f=fixture();f.cards();f.api.sync();await f.flush();f.ctx.roomThread.tariq.push({role:'agent',text:'Finished something else'});
    await f.advance(5000);assert.equal(f.api.cache.get(f.key).snapshot.state,'uploading');assert.equal(f.calls.length,2);
  });
  await test('terminal success stops polling and paints verified link',async()=>{
    const f=fixture(),nodes=f.cards();f.responses.push(f.answer({state:'complete',view_url:'https://next.frame.io/review/a'}));
    f.api.sync();await f.flush();await f.advance(60000);assert.equal(f.calls.length,1);assert(nodes[0].outerHTML.includes('Open verified review'));assert.equal(f.timers.size,0);
  });
  await test('blocked stops automatic polling without declaring success',async()=>{
    const f=fixture(),nodes=f.cards();f.responses.push(f.answer({state:'blocked'}));f.api.sync();await f.flush();await f.advance(60000);
    assert.equal(f.calls.length,1);assert(nodes[0].outerHTML.includes('Blocked'));assert(!nodes[0].outerHTML.includes('Complete'));
  });
  await test('stale status remains stalled and polls more slowly',async()=>{
    const f=fixture(),nodes=f.cards();f.responses.push(f.answer({state:'stalled',updated_at:'2026-11-19T11:00:00Z'}));f.api.sync();await f.flush();
    assert(nodes[0].outerHTML.includes('Stalled'));await f.advance(5000);assert.equal(f.calls.length,1);await f.advance(25000);assert.equal(f.calls.length,2);
    assert(f.api.card(f.row({review_id:'old-review',updated_at:'2026-11-19T11:00:00Z'})).includes('Stalled'));
  });
  await test('network failure shows last confirmed values and retries with bounded backoff',async()=>{
    const f=fixture(),nodes=f.cards();f.responses.push(()=>Promise.reject(Error('secret/path/token')));f.api.sync();await f.flush();
    assert(nodes[0].outerHTML.includes('Status unavailable'));assert(nodes[0].outerHTML.includes('last confirmed'));assert(!nodes[0].outerHTML.includes('secret'));
    await f.advance(10000);assert.equal(f.calls.length,2);assert(!nodes[0].outerHTML.includes('Status unavailable'));
  });
  await test('not found stops polling; explicit status retry never resends',async()=>{
    const f=fixture(),nodes=f.cards();f.responses.push({ok:false,status:404});f.api.sync();await f.flush();await f.advance(60000);assert.equal(f.calls.length,1);
    assert(nodes[0].outerHTML.includes('Check status'));await f.click();assert.equal(f.calls.length,2);assert(f.calls.every(c=>c.opts.method==='GET'));
  });
  await test('duplicate receipts share one poll and update all matching cards',async()=>{
    const f=fixture(),nodes=f.cards(2);f.ctx.roomThread.tariq.push({saved:[f.row()]});f.api.sync();f.api.sync();await f.flush();
    assert.equal(f.calls.length,1);assert.equal(f.api.watching.size,1);assert(nodes.every(n=>n.outerHTML.includes('Uploading')));
  });
  await test('reopening chat resumes saved receipt and rechecks previously terminal review',async()=>{
    const f=fixture();f.cards();f.responses.push(f.answer({state:'blocked'}));f.api.sync();await f.flush();f.ctx.view='board';await f.mutate();assert.equal(f.api.watching.size,0);
    f.ctx.view='team';await f.mutate();assert.equal(f.calls.length,2);assert.equal(f.api.cache.get(f.key).snapshot.state,'uploading');
  });
  await test('a repeated request for the same stopped review starts a fresh status watch',async()=>{
    const f=fixture(),nodes=f.cards();f.responses.push(f.answer({state:'blocked'}));f.api.sync();await f.flush();
    assert.equal(f.calls.length,1);assert(nodes[0].outerHTML.includes('Blocked'));
    f.ctx.roomThread.tariq.push({id:'new-request',saved:[f.row()]});f.api.sync();await f.flush();
    assert.equal(f.calls.length,2);assert(nodes[0].outerHTML.includes('Uploading'));await f.advance(5000);assert.equal(f.calls.length,3);
  });
  await test('visibility pauses timers and resumes from persisted identity',async()=>{
    const f=fixture();f.cards();f.api.sync();await f.flush();f.ctx.document.hidden=true;await f.fire('visibilitychange');await f.advance(60000);assert.equal(f.calls.length,1);
    f.ctx.document.hidden=false;await f.fire('visibilitychange');assert.equal(f.calls.length,2);
  });
  await test('navigation aborts in-flight request and late result cannot overwrite reopened review',async()=>{
    const f=fixture();f.cards();let resolve;f.responses.push(()=>new Promise(r=>resolve=r));f.api.sync();await f.flush();
    f.ctx.view='board';await f.mutate();assert(f.calls[0].opts.signal.aborted);f.ctx.view='team';await f.mutate();
    resolve(f.answer({state:'complete',view_url:'https://next.frame.io/wrong'}));await f.flush();assert.equal(f.api.cache.get(f.key).snapshot.state,'uploading');
  });
  await test('page teardown aborts and cancels; pageshow restarts exact receipt',async()=>{
    const f=fixture();f.cards();f.api.sync();await f.flush();await f.fire('pagehide',true);assert.equal(f.timers.size,0);assert.equal(f.api.watching.size,0);
    await f.fire('pageshow',true);assert.equal(f.calls.length,2);
  });
  await test('status paint preserves composer, selection and thread scroll',async()=>{
    const f=fixture();f.cards();f.api.sync();await f.flush();await f.advance(5000);
    assert.deepEqual(f.composer,{value:'Unsaved words',selectionStart:3,selectionEnd:5});assert.equal(f.thread.scrollTop,41);
  });
  await test('mismatched review response cannot mark requested review complete',async()=>{
    const f=fixture(),nodes=f.cards();f.responses.push(f.answer({review_id:'review-2',state:'complete'}));f.api.sync();await f.flush();
    assert.equal(f.api.cache.get(f.key).snapshot.state,'uploading');assert(nodes[0].outerHTML.includes('Status unavailable'));
  });
  await test('failed staging without a review ID does not poll or display model errors',async()=>{
    const f=fixture(),row=f.row({review_id:'',state:'blocked',message:'secret/path'});f.ctx.roomThread.tariq=[{saved:[row]}];f.api.sync();await f.flush();assert.equal(f.calls.length,0);
    const html=f.api.card(row);assert(html.includes('could not be confirmed'));assert(!html.includes('secret'));
  });
  await test('request timeout is a recoverable status error, not completion',async()=>{
    const f=fixture(),nodes=f.cards();f.responses.push((url,opts)=>new Promise((resolve,reject)=>opts.signal.addEventListener('abort',()=>reject(Error('timeout')))));
    f.api.sync();await f.flush();await f.advance(10000);assert(nodes[0].outerHTML.includes('Status unavailable'));assert.equal(f.api.watching.get(f.key).pending,false);
    await f.advance(10000);assert.equal(f.calls.length,2);
  });
  console.log(`${passed} Frame.io frontend scenarios passed; no browser, network, upload or live writes.`);
})().catch(error=>{console.error(error);process.exitCode=1});
