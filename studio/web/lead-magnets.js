'use strict';
(()=>{
  const $=id=>document.getElementById(id);
  const esc=s=>String(s||'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const safe=s=>{try{const u=new URL(s);return ['http:','https:'].includes(u.protocol)&&!u.username&&!u.password}catch{return false}};
  let magnets=[];
  function render(){
    const q=$('magnet-search').value.trim().toLocaleLowerCase();
    const shown=magnets.filter(m=>[m.name,m.description,m.audience,m.contents,...(m.aliases||[])].join(' ').toLocaleLowerCase().includes(q));
    $('magnet-status').textContent=shown.length?'':q?'No lead magnets match your search.':'No lead magnets yet.';
    $('magnet-cards').innerHTML=shown.map(m=>`<article class="magnet-card"><div>${m.format?`<span class="magnet-format">${esc(m.format)}</span>`:''}<h2>${esc(m.name)}</h2>${m.contents||m.description?`<p>${esc(m.contents||m.description)}</p>`:''}</div><div class="magnet-links">${safe(m.landing_url)?`<a href="${esc(m.landing_url)}" target="_blank" rel="noopener noreferrer"><span>Website</span><span class="magnet-url">${esc(m.landing_url)}</span></a>`:''}${safe(m.download_url)?`<a href="${esc(m.download_url)}" target="_blank" rel="noopener noreferrer"><span>Download</span><span class="magnet-url">${esc(m.download_url)}</span></a>`:''}</div></article>`).join('');
  }
  async function load(){
    $('magnet-error').hidden=true;$('magnet-status').textContent='Loading lead magnets…';
    try{
      const r=await fetch('/api/lead-magnets',{headers:{'X-DJ-Token':document.querySelector('meta[name=dj-token]').content}});
      if(!r.ok)throw new Error('Load failed');const data=await r.json();
      if(!Array.isArray(data.magnets))throw new Error('Invalid response');
      magnets=data.magnets;render();
    }catch{$('magnet-status').textContent='';$('magnet-error').hidden=false}
  }
  $('magnet-search').addEventListener('input',render);$('magnet-retry').addEventListener('click',load);load();
})();
