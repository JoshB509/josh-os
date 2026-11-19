const fs = require('fs'), vm = require('vm'), assert = require('assert'), path = require('path');
const source = fs.readFileSync(process.argv[2] || path.resolve(__dirname, '../../DaJoshiCut/panel/index.js'),'utf8');
const bridge = source.slice(source.indexOf('// BEGIN NATIVE SOURCE TRANSCRIPT'), source.indexOf('// END NATIVE SOURCE TRANSCRIPT'));
assert(bridge.includes('async function tqNativeTranscript'));
assert(source.includes('async tq_native_transcript(args) { return tqNativeTranscript(args || {}); }'));
async function fixture({unsupported=false, wrong=false, duplicates=false, fail=false, switchProject=false}={}) {
  const expected='e:/video/tariq/fresh.prproj', mic='c:/audio/microphone.wav';
  let active=expected, calls=0, saves=0, release;
  const pending=new Promise(resolve=>release=resolve);
  const project={path:expected,save:async()=>{saves++;return true;}};
  const ctx={Map,Error,String,ppro:{Transcript:unsupported?{}:{transcribeClipProjectItem:async clip=>{assert.equal(clip,'mic');calls++;await pending;if(switchProject)active='other';return !fail;}}},
    guardedPath:p=>p.toLowerCase(),guardedProject:async p=>{if(wrong||active!==p.toLowerCase())throw new Error('wrong project');return project;},
    walkProject:async(p,visit)=>{visit({path:mic,clip:'mic'});if(duplicates)visit({path:mic,clip:'mic2'});visit({path:'c:/audio/camera.mp4',clip:'camera'});}};
  vm.createContext(ctx);vm.runInContext(bridge,ctx);
  const call=action=>ctx.tqNativeTranscript({action,request_id:'request-001',expected_project:expected,mic_path:mic});
  return {ctx,call,release,counts:()=>({calls,saves})};
}
(async()=>{
  let f=await fixture(); assert.equal((await f.call('capabilities')).supported,true);
  assert.equal((await f.call('start')).state,'running');
  assert.equal((await f.call('start')).state,'running');assert.equal(f.counts().calls,1);
  f.release();await new Promise(setImmediate);assert.equal((await f.call('status')).state,'done');assert.equal(f.counts().saves,1);
  for(const opts of [{wrong:true},{duplicates:true}]){f=await fixture(opts);await assert.rejects(f.call('start'));assert.deepEqual(f.counts(),{calls:0,saves:0});}
  f=await fixture({unsupported:true});assert.equal((await f.call('capabilities')).supported,false);await assert.rejects(f.call('start'));assert.equal(f.counts().calls,0);
  for(const opts of [{fail:true},{switchProject:true}]){f=await fixture(opts);await f.call('start');f.release();await new Promise(setImmediate);assert.equal((await f.call('status')).state,'blocked');assert.equal(f.counts().saves,0);}
  f=await fixture();assert.equal((await f.call('status')).state,'missing');assert.equal(f.counts().calls,0);
  console.log('7 native bridge fixture scenarios passed; no Premiere or network calls');
})().catch(e=>{console.error(e);process.exitCode=1;});
