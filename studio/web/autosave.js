'use strict';
// Autosave for video projects. Every edit saves itself shortly after you stop typing,
// so there is no Save button to remember. Ctrl+S still saves straight away.
let autoTimer=null,autoBusy=null;
const AUTOSAVE_DELAY=900;

function autoNote(text){const n=$('#save-note');if(n)n.textContent=text}

// Background folder sync can swap `current` for a fresh copy of the same video, so saves
// look the open video up by id, falling back to the last copy edited if you have moved on.
const autoDrafts={};
const autoLive=id=>current&&current.id===id?current:autoDrafts[id];

mark=function(){dirty=true;autoNote('Saving…');clearTimeout(autoTimer);const id=current.id;autoDrafts[id]=current;autoTimer=setTimeout(()=>autosaveProject(id),AUTOSAVE_DELAY)};

async function autosaveProject(id){
  if(!id||!autoLive(id))return;
  if(autoBusy){await autoBusy;if(current?.id===id&&dirty)return autosaveProject(id);return}
  const first=autoLive(id),sent=Object.fromEntries(fields.map(k=>[k,structuredClone(first[k])]));
  autoBusy=(async()=>{
    try{
      const saved=await api('/api/projects/'+id,{revision:first.revision,changes:sent},'PUT');
      const p=autoLive(id),same=k=>JSON.stringify(p[k])===JSON.stringify(sent[k]);
      // Keep anything typed while the request was in flight; take the server's copy of the rest.
      const pending=!fields.every(same);
      for(const k of fields)if(same(k))p[k]=saved[k];
      for(const k of Object.keys(saved))if(!fields.includes(k))p[k]=saved[k];
      const i=state.projects.findIndex(x=>x.id===id);if(i>=0)state.projects[i]=structuredClone(saved);
      if(current?.id===id){dirty=pending;autoNote(pending?'Saving…':'Saved')}
      if(!pending)delete autoDrafts[id];
      return pending;
    }catch(e){if(current?.id===id)autoNote('Not saved');toast(e.message,true);return false}
  })();
  const again=await autoBusy;autoBusy=null;
  if(again)return autosaveProject(id);
}

// The full save (used by thumbnail and edit actions) waits for any autosave first.
const autoBaseSave=save;
save=async function(){clearTimeout(autoTimer);while(autoBusy)await autoBusy;return autoBaseSave()};

// Leaving a project flushes pending edits instead of asking to discard them.
const autoBaseLeave=leave;
leave=function(){if(dirty&&current){clearTimeout(autoTimer);autosaveProject(current.id);dirty=false;return true}return autoBaseLeave()};

// A small reusable autosaver for other records (carousels and similar).
function makeAutosaver({url,note,delay=700,onSaved}){
  let timer=null,busy=null,draft=null,record=null;
  async function flush(){
    if(busy){await busy;return draft?flush():undefined}
    if(!draft||!record)return;
    const r=record,changes=draft;draft=null;
    busy=(async()=>{
      try{const saved=await api(url(r),{revision:r.revision,changes},'PUT');Object.assign(r,saved,draft||{});note(draft?'Saving…':'Saved');onSaved?.(r)}
      catch(e){draft=Object.assign(changes,draft||{});note('Not saved');toast(e.message,true)}
      finally{busy=null}
    })();
    await busy;if(draft)return flush();
  }
  return{
    change(r,key,value){if(record&&record!==r)flush();record=r;r[key]=value;draft=Object.assign(draft||{},{[key]:value});note('Saving…');clearTimeout(timer);timer=setTimeout(flush,delay)},
    async flush(){clearTimeout(timer);await flush()},
  };
}
