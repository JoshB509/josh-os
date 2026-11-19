'use strict';
// Josh reviews and approves Sam's prepared changes to videos already on his channel (studio/youtube_manage.py).
const $=id=>document.getElementById(id);let current=null,busy=false,seq=0,canUpdate=false;
function notice(t){$('notice').textContent=t||''}
async function api(path,body){const r=await fetch(path,{method:body===undefined?'GET':'POST',credentials:'same-origin',headers:body===undefined?{}:{'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)});const v=await r.json();if(!r.ok)throw new Error(v.error||'Request failed');return v}
const LABEL={title:'Title',description:'Description',privacy:'Privacy',publish_at:'Release time',thumbnail:'Thumbnail'};
function controls(){
  const ok=$('consent').checked,s=current?.state;
  $('approve').disabled=busy||!ok||!current||s!=='needs_review'||!current.review_nonce;
  $('retry').disabled=busy||!current||!['approved','blocked','approved_waiting'].includes(s)||!canUpdate;
  $('verify').disabled=busy||!current||!['approved','approved_waiting','blocked'].includes(s);
}
function row(dl,label,before,after){
  const dt=document.createElement('dt'),dd=document.createElement('dd');dt.textContent=label;
  const b=document.createElement('div'),a=document.createElement('div');b.className='muted';
  b.textContent='Now: '+(before==null||before===''?'(none)':before);a.textContent='Becomes: '+(after==null||after===''?'(none)':after);
  dd.append(b,a);dl.append(dt,dd);
}
function render(job){
  current=job;$('review').hidden=false;$('consent').checked=false;
  const dl=document.createElement('dl'),b=job.before||{};
  row(dl,'Video',null,job.video_id+' on channel '+job.channel);
  for(const[k,v]of Object.entries(job.changes||{}))row(dl,LABEL[k]||k,k==='thumbnail'?'the current thumbnail':b[k],k==='thumbnail'?v.path:v);
  $('details').replaceChildren(dl);
  $('job-state').textContent='Change '+job.id+' · '+job.state+(job.error?' · '+job.error:'');
  const r={...job};delete r.review_nonce;$('result').textContent=JSON.stringify(r,null,2);controls();
}
async function load(id){const n=++seq;try{const j=await api('/api/publishing/updates/'+encodeURIComponent(id));if(n===seq)render(j)}catch(e){if(n===seq)notice(e.message)}}
async function refresh(){
  const st=await api('/api/publishing/updates');canUpdate=!!st.can_update;
  $('connection').textContent=st.can_update?`CareerOS can change videos on ${st.edit_channel}. Each change still needs your approval here.`
    :st.read_connected?`CareerOS can read ${st.expected_channel} but can't change videos yet. Until the edit permission is connected, Sam makes an approved change in YouTube Studio and CareerOS checks it.`
    :'Not connected to YouTube.';
  $('jobs').replaceChildren();
  for(const j of st.updates||[]){const b=document.createElement('button');b.type='button';b.textContent=j.video_id+' · '+Object.keys(j.changes||{}).map(k=>LABEL[k]||k).join(', ')+' · '+j.state;b.onclick=()=>{location.hash=j.id;load(j.id)};$('jobs').append(b)}
  controls();
}
async function act(path,body,msg){if(!current||busy)return;busy=true;controls();notice(msg);try{await api(path,body);await refresh();await load(current.id)}catch(e){notice(e.message);try{await load(current.id)}catch(_){}}finally{busy=false;controls()}}
$('consent').onchange=controls;
$('approve').onclick=()=>act('/api/publishing/updates/approve',{update_id:current.id,digest:current.digest,review_nonce:current.review_nonce},'Approved. CareerOS is making the change and will read it back. Refresh to see the result.');
$('retry').onclick=()=>act('/api/publishing/updates/apply',{update_id:current.id},'Trying the approved change again…');
$('verify').onclick=()=>act('/api/publishing/updates/verify',{update_id:current.id},'Reading the video on YouTube…');
$('refresh').onclick=async()=>{try{await refresh();if(current)await load(current.id)}catch(e){notice(e.message)}};
window.addEventListener('hashchange',()=>{if(location.hash.length>1)load(decodeURIComponent(location.hash.slice(1)))});
(async()=>{try{await refresh();if(location.hash.length>1)await load(decodeURIComponent(location.hash.slice(1)))}catch(e){notice(e.message)}})();
