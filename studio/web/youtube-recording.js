'use strict';
colors.splice(3,0,'#c97863');
// Recording selection is explicit and persisted on the server, never inferred from a tab.
const recordingDetail=renderDetail;
renderDetail=function(){
  recordingDetail();
  if(!current || !['Recording','Recorded'].includes(current.stage))return;
  const pid=current.id;
  $('#tab-body').insertAdjacentHTML('afterbegin','<section class="panel" id="youtube-recording"><h3>Recording</h3><p>Loading saved recording selection…</p></section>');
  api(`/api/projects/${pid}/recording`).then(r=>{if(current?.id===pid && $('#youtube-recording'))showRecording(r)}).catch(e=>{if($('#youtube-recording'))$('#youtube-recording').textContent=e.message});
};
function showRecording(r){
 if(current?.id!==r.project_id||!$('#youtube-recording'))return;
 const b=r.binding,s=b.selection;
 $('#youtube-recording').innerHTML=`<h3>${r.active?'Active YouTube video':'YouTube recording'}</h3>
 <p>${r.active?'The Stream Deck Lock Take button is bound to this video.':'Set this video active to use Lock Take.'}</p>
 <label>Video project folder<input id="recording-folder" value="${esc(b.project_folder||current.folder_info?.project||'')}" placeholder="Choose the existing folder for this video"></label>
 <div class="row"><button class="subtle small" data-recording="folder" ${!r.eligible?'disabled':''}>Browse folder</button><button class="primary" data-recording="active" ${!r.eligible?'disabled':''}>Set as active recording</button></div>
 <p class="hint">Takes go into Recorded / Input inside this folder. Only one video is active.</p>
 <p>Selected take: <b>${esc(s?.source?.path||'None selected')}</b></p>
 <div class="row"><button class="subtle" data-recording="source" ${!r.active||!r.eligible?'disabled':''}>Choose finished take</button><button class="primary" data-recording="lock" ${!r.active||!s?'disabled':''}>Lock Take &amp; open folder</button></div>
 <p class="hint">Finish recording and remuxing first. Lock Take checks the complete video and audio, moves the exact selected file, then marks this video Recorded. It does not start or stop OBS.</p>
 ${r.takes.length?`<details><summary>Saved takes &amp; recovery</summary>${r.takes.map(t=>`<p>${esc(t.phase==='complete'?'Saved':'Pending — press Lock Take to recover')}<br><span class="path">${esc(t.destination)}</span></p>`).join('')}</details>`:''}`;
}
document.addEventListener('click',async e=>{
 const button=e.target.closest('[data-recording]');if(!button||!current)return;
 const action=button.dataset.recording;button.disabled=true;
 try{
   if(dirty)throw Error('Save your video changes before changing its recording selection.');
   if(action==='folder'){const r=await api('/api/pick',{kind:'folder'});if(r.path)$('#recording-folder').value=r.path;return}
   const pid=current.id;
   if(action==='active')showRecording(await api(`/api/projects/${pid}/recording/active`,{revision:current.revision,folder:$('#recording-folder').value}));
   if(action==='source'){
     const r=await api('/api/pick',{kind:'video'});if(!r.path)return;
     showRecording(await api(`/api/projects/${pid}/recording/source`,{revision:current.revision,path:r.path}));
   }
   if(action==='lock'){
     toast('Checking the complete take and audio…');const result=await api('/api/youtube-recording/lock',{});
     await refresh();if(current?.id===pid&&!dirty){current=structuredClone(state.projects.find(p=>p.id===pid));renderDetail()}toast(result.folder_opened?'Take saved; recording folder opened.':result.message);
   }
 }catch(err){toast(err.message,true)}finally{button.disabled=false}
});
