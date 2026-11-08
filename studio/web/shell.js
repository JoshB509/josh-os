'use strict';
// One navigation for every page. The main app (index.html) has its own buttons; Tasks,
// Lessons and Admin are separate pages, so this rebuilds their sidebars from the same list
// and adds the phone tab bar and a "More" sheet. See docs/design/critique-2026-11-07.md.
(function(){
  const I={
    today:'<circle cx="8" cy="8" r="3"/><path d="M8 1.5v1.5M8 13v1.5M1.5 8H3M13 8h1.5M3.4 3.4l1 1M11.6 11.6l1 1M3.4 12.6l1-1M11.6 4.4l1-1"/>',
    calendar:'<rect x="2.5" y="3.5" width="11" height="10" rx="2"/><path d="M2.5 6.5h11M5.5 2v3M10.5 2v3"/>',
    team:'<circle cx="8" cy="5" r="2.4"/><path d="M3.5 13.5c.6-2.6 2.4-3.9 4.5-3.9s3.9 1.3 4.5 3.9"/><circle cx="3" cy="6.5" r="1.5"/><circle cx="13" cy="6.5" r="1.5"/>',
    youtube:'<rect x="1.8" y="3.5" width="12.4" height="9" rx="2.2"/><path d="m6.8 6.3 3.2 1.7-3.2 1.7z"/>',
    carousels:'<rect x="4" y="3" width="8" height="10" rx="1.6"/><path d="M2 5v6M14 5v6"/>',
    editing:'<circle cx="4.5" cy="4.5" r="2"/><circle cx="4.5" cy="11.5" r="2"/><path d="M6.2 5.6 14 12M6.2 10.4 14 4"/>',
    email:'<rect x="2" y="3" width="12" height="10" rx="2"/><path d="m2 4 6 5 6-5"/>',
    library:'<rect x="2" y="2.5" width="5" height="5" rx="1.2"/><rect x="9" y="2.5" width="5" height="5" rx="1.2"/><rect x="2" y="9.5" width="5" height="4" rx="1.2"/><rect x="9" y="9.5" width="5" height="4" rx="1.2"/>',
    tasks:'<rect x="2" y="2" width="12" height="12" rx="2"/><path d="m5 8 2 2 4-4"/>',
    lessons:'<path d="M2 4.5 8 2l6 2.5L8 7z"/><path d="M4.5 5.6v3.6C5.6 10.4 6.7 11 8 11s2.4-.6 3.5-1.8V5.6M14 4.5v4"/>',
    archive:'<rect x="2" y="3" width="12" height="3" rx="1"/><path d="M3 6v6.5a1 1 0 0 0 1 1h8a1 1 0 0 0 1-1V6M6.5 9h3"/>',
    clients:'<circle cx="6" cy="5.5" r="2.5"/><path d="M1.8 13.5c.6-2.4 2.2-3.6 4.2-3.6s3.6 1.2 4.2 3.6"/><path d="M10.5 3.2a2.4 2.4 0 0 1 0 4.6M12 9.9c1.2.4 2 1.6 2.3 3.6"/>',
    customers:'<path d="M2 3h1.6l1.6 7.4h7.2L14 5.2H4.3"/><circle cx="6" cy="13" r="1"/><circle cx="11.6" cy="13" r="1"/>',
    more:'<circle cx="3.5" cy="8" r="1"/><circle cx="8" cy="8" r="1"/><circle cx="12.5" cy="8" r="1"/>',
  };
  const svg=k=>`<svg viewBox="0 0 16 16" aria-hidden="true">${I[k]}</svg>`;
  const NAV=[
    {items:[['today','Today','/#today'],['calendar','Calendar','/#calendar'],['team','Team','/#team']]},
    {label:'Workflows',items:[['youtube','YouTube videos','/#board'],['carousels','Carousels','/#carousels'],['lessons','Lessons','/#lessons-board'],['editing','Editing','/#editing'],['email','Email','/#email']]},
    {label:'Workspace',items:[['library','Library','/#inspiration'],['tasks','Tasks & Goals','/tasks'],['lessons','Lesson recording','/lessons'],['archive','Archive','/#archive']]},
    {label:'Clients',later:true,items:[['clients','Clients','/admin#clients'],['customers','Customers','/admin#customers']]},
  ];
  const TABS=[['today','Today','/#today'],['youtube','YouTube','/#board'],['editing','Editing','/#editing'],['email','Email','/#email']];
  const page=location.pathname.replace(/\/$/,'')||'/';
  const here=href=>{const [p,h]=href.split('#');return p===page&&(!h||page!=='/admin'||location.hash.slice(1)===h)};
  const esc=s=>String(s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
  // On Admin the Clients/Customers links switch sections in place.
  const link=(cls,[k,label,href])=>{const inAdmin=page==='/admin'&&href.startsWith('/admin#');const h=inAdmin?href.slice(6):href;
    return `<a class="${cls}${here(href)?' active on':''}" href="${esc(h)}"${inAdmin?` data-section="${href.split('#')[1]}"`:''}${here(href)?' aria-current="page"':''}>${svg(k)}${esc(label)}</a>`};

  function build(cls,labelCls){return NAV.map(g=>`${g.label?`<div class="${labelCls}">${esc(g.label)}${g.later?' <span class="rail-soon">later</span>':''}</div>`:''}${g.items.map(i=>link(cls,i)).join('')}`).join('')}

  if(page!=='/'){
    const rail=document.querySelector('aside.rail');
    if(rail){const navs=rail.querySelectorAll('nav,.rail-label');const first=navs[0];
      if(first){const box=document.createElement('nav');box.setAttribute('aria-label','Main');box.className='shell-nav';box.innerHTML=build('nav','rail-label');first.before(box);navs.forEach(n=>n.remove())}}
    const side=document.querySelector('aside.side nav[aria-label=Workspace]');
    if(side)side.innerHTML=build('side-link','side-label');
    const app=document.querySelector('#side .appnav');
    if(app)app.innerHTML=build('','navlabel');
    for(const a of document.querySelectorAll('a.brand,a.ws,.side a[href="/#board"]:not(.side-link)'))a.href='/#today';
  }

  // Phone tab bar and More sheet on the separate pages (index.html has its own).
  if(page!=='/'&&!document.querySelector('.tabbar')){
    const bar=document.createElement('nav');bar.className='tabbar';bar.setAttribute('aria-label','Main');
    bar.innerHTML=TABS.map(([k,l,h])=>`<a class="tab${here(h)?' active':''}" href="${h}">${svg(k)}<span>${l}</span></a>`).join('')+`<button class="tab" id="shell-more" aria-haspopup="dialog">${svg('more')}<span>More</span></button>`;
    document.body.append(bar);
    const sheet=document.createElement('dialog');sheet.className='more-sheet';sheet.setAttribute('aria-label','All pages');
    sheet.innerHTML=`<div class="more-head"><b>Josh OS</b><button class="subtle" data-close aria-label="Close">✕</button></div>${NAV.map(g=>`${g.label?`<div class="more-label">${esc(g.label)}</div>`:''}<div class="more-grid">${g.items.map(([k,l,h])=>`<a href="${h}" class="${here(h)?'active':''}">${svg(k)}<span>${esc(l)}</span></a>`).join('')}</div>`).join('')}`;
    document.body.append(sheet);
    bar.querySelector('#shell-more').onclick=()=>sheet.showModal();
    sheet.addEventListener('click',e=>{if(e.target===sheet||e.target.closest('[data-close]'))sheet.close()});
    document.body.classList.add('has-tabbar');
  }
})();
