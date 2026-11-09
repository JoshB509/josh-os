// Images in team chats. Attach with the clip button, paste, or drop onto the box you type in.
// Each image is saved in Josh OS first, so a message only carries asset ids.
const CHAT_MAX_IMAGES=6;
let chatAttach={room:[],dock:[],sam:[]},chatUploading={room:0,dock:0,sam:0};
const chatClip='<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M10.5 4.5 5.6 9.4a1.4 1.4 0 0 0 2 2l5.2-5.2a2.8 2.8 0 0 0-4-4L3.6 7.4a4.2 4.2 0 0 0 6 6l4-4"/></svg>';

function chatRedraw(key){if(key==='dock'){if(typeof dockRender==='function')dockRender()}else if(key==='sam'){if(typeof samRender==='function')samRender()}else if(typeof roomRender==='function')roomRender()}
function chatAttachButton(key,disabled){
  return `<button class="chat-clip" data-chat-attach="${key}" ${disabled?'disabled':''} aria-label="Attach images" title="Attach images. You can also paste or drop them here.">${chatClip}</button>`;
}
function chatAttachStrip(key){
  const list=chatAttach[key]||[],n=chatUploading[key]||0;
  if(!list.length&&!n)return '';
  return `<div class="chat-attach">${list.map((x,i)=>`<span class="chat-thumb"><img src="/assets/${esc(x)}?w=240" alt="Attached image ${i+1}"><button data-chat-unattach="${key}:${i}" aria-label="Remove image ${i+1}">✕</button></span>`).join('')}${n?`<span class="chat-thumb loading">Adding…</span>`:''}</div>`;
}
function chatImagesHtml(ids){
  if(!ids?.length)return '';
  return `<div class="chat-images">${ids.map((x,i)=>`<a href="/assets/${esc(x)}" target="_blank" rel="noopener"><img src="/assets/${esc(x)}?w=480" alt="Image ${i+1} Josh sent" loading="lazy"></a>`).join('')}</div>`;
}
async function chatAdd(key,files){
  files=[...files].filter(f=>/^image\/(png|jpeg|webp)$/.test(f.type));
  if(!files.length){toast('Use a PNG, JPEG or WebP image.',true);return}
  const room=CHAT_MAX_IMAGES-(chatAttach[key].length+chatUploading[key]);
  if(files.length>room)toast(`You can attach up to ${CHAT_MAX_IMAGES} images to one message.`,true);
  files=files.slice(0,Math.max(0,room));if(!files.length)return;
  chatUploading[key]+=files.length;chatRedraw(key);
  for(const f of files){
    try{
      if(f.size>20*1024*1024)throw Error(`${f.name||'That image'} is over 20 MB.`);
      const data=await new Promise((ok,bad)=>{const r=new FileReader();r.onload=()=>ok(r.result.split(',')[1]);r.onerror=bad;r.readAsDataURL(f)});
      const t=await api('/api/images',{name:f.name||'pasted.png',data});
      if(!chatAttach[key].includes(t.asset))chatAttach[key].push(t.asset);
    }catch(e){toast(e.message,true)}
    finally{chatUploading[key]--;chatRedraw(key)}
  }
}
// Takes the attached images for sending and clears the box.
function chatTake(key){const list=chatAttach[key];chatAttach[key]=[];return list}
function chatKeyFor(el){if(!el?.closest)return null;if(el.closest('.dock-compose'))return 'dock';if(el.closest('.room-compose'))return 'room';if(el.closest('.sam-compose'))return 'sam';return null}

(()=>{
  const input=document.createElement('input');input.type='file';input.accept='image/png,image/jpeg,image/webp';input.multiple=true;input.hidden=true;input.id='chat-files';
  document.body.appendChild(input);let target='room';
  input.onchange=()=>{const f=[...input.files];input.value='';if(f.length)chatAdd(target,f)};
  document.addEventListener('click',e=>{
    const b=e.target.closest?.('[data-chat-attach]');if(b){e.preventDefault();e.stopPropagation();target=b.dataset.chatAttach;input.click();return}
    const x=e.target.closest?.('[data-chat-unattach]');if(x){e.preventDefault();e.stopPropagation();const [k,i]=x.dataset.chatUnattach.split(':');chatAttach[k].splice(+i,1);chatRedraw(k)}
  },true);
  document.addEventListener('paste',e=>{
    const key=chatKeyFor(e.target);if(!key)return;
    const files=[...(e.clipboardData?.files||[])].filter(f=>f.type.startsWith('image/'));
    if(files.length){e.preventDefault();chatAdd(key,files)}
  });
  document.addEventListener('dragover',e=>{const key=chatKeyFor(e.target);if(key&&e.dataTransfer?.types.includes('Files')){e.preventDefault();e.target.closest('.dock-compose,.room-compose,.sam-compose').classList.add('chat-over')}});
  document.addEventListener('dragleave',e=>{const z=e.target.closest?.('.dock-compose,.room-compose,.sam-compose');if(z&&!z.contains(e.relatedTarget))z.classList.remove('chat-over')});
  document.addEventListener('drop',e=>{
    const key=chatKeyFor(e.target);if(!key||!e.dataTransfer?.files.length)return;
    e.preventDefault();e.stopPropagation();e.target.closest('.dock-compose,.room-compose,.sam-compose')?.classList.remove('chat-over');chatAdd(key,e.dataTransfer.files);
  },true);
})();
