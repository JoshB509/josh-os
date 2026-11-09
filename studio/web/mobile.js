'use strict';
// Phone layout: the side rail becomes a slide-out menu. Also opens the new views from
// a #hash link and shows how to reach Josh OS from a phone.
(function(){
  const rail=document.querySelector('.rail'),menu=$('#menu');
  const close=()=>{document.body.classList.remove('menu-open');menu.setAttribute('aria-expanded','false')};
  menu.onclick=()=>{const open=document.body.classList.toggle('menu-open');menu.setAttribute('aria-expanded',String(open))};
  rail.addEventListener('click',e=>{if(e.target.closest('.nav,.rail-page'))close()});
  document.addEventListener('keydown',e=>{if(e.key==='Escape')close()});
  document.addEventListener('click',e=>{if(document.body.classList.contains('menu-open')&&!e.target.closest('.rail,#menu'))close()});

  Object.assign(viewNames,{success:'Customer success',today:'Today',board:'YouTube videos',team:'Team',carousels:'Carousels',editing:'Editing',email:'Email'});

  if(location.hostname!=='127.0.0.1'){const w=$('#where');if(w)w.innerHTML='<i></i>Remote'}

  // Label the New button for the view you are on, and hide it where it does nothing.
  const labels={board:'New video',calendar:'New video',archive:'New video',email:'New email',carousels:'New carousel'};
  const baseRender=render;
  render=function(){const r=baseRender();const b=$('#new-project');const label=labels[view];b.hidden=!label;if(label)b.textContent=label;
    document.querySelectorAll('.tabbar .tab[data-view]').forEach(t=>t.classList.toggle('active',t.dataset.view===view||t.dataset.also===view));return r};

  // Today is the home page unless a link asks for somewhere else.
  if(['today','team','carousels','editing','success'].includes(initialView)||!initialView){view=initialView||'today';refresh().then(render).catch(()=>{})}
  $('#tab-more').onclick=e=>{e.stopPropagation();menu.click()};

  $('#phone-access').onclick=async()=>{
    let info;try{info=await api('/api/phone')}catch(e){toast(e.message,true);return}
    const d=document.createElement('dialog');d.className='phone-dialog';
    d.innerHTML=info.enabled?`<h2>Use Josh OS on your phone</h2>
      <p>Your phone needs Tailscale installed and signed in to the same account as this PC. Open this link on the phone once, then use <b>Add to Home Screen</b>.</p>
      <input readonly value="${esc(info.url)}" id="phone-url"><div class="dialog-actions"><button class="primary" id="phone-copy">Copy link</button><button id="phone-close">Close</button></div>
      <p class="hint">Anyone with this link and access to your tailnet can open Josh OS. To sign every phone out, delete Data/phone-access.json and restart Josh OS.</p>`
      :`<h2>Phone access is switched off</h2>${info.error?`<p class="danger">${esc(info.error)}</p>`:''}
      <p>Josh OS only accepts connections from this PC. To use it on your phone:</p>
      <ol><li>Install Tailscale on this PC and your phone, signed in to the same account.</li>
      <li>Add this to settings.json, using this PC's Tailscale name:<pre>"phone_access": {"host": "your-pc.your-tailnet.ts.net", "port": 8765}</pre></li>
      <li>Restart Josh OS, then run <code>tailscale serve --bg 8765</code> on this PC.</li>
      <li>Come back here for the phone link.</li></ol>
      <div class="dialog-actions"><button id="phone-close">Close</button></div>`;
    document.body.append(d);d.showModal();
    d.querySelector('#phone-close').onclick=()=>{d.close();d.remove()};
    d.addEventListener('close',()=>d.remove());
    const copy=d.querySelector('#phone-copy');if(copy)copy.onclick=async()=>{try{await navigator.clipboard.writeText(info.url);toast('Link copied')}catch{d.querySelector('#phone-url').select()}};
  };
})();
