/* Optional project links from Tasks. No change to ordinary app navigation. */
(() => {
  const id=new URLSearchParams(location.search).get('project');
  if(!id||!/^[0-9a-f-]{36}$/i.test(id))return;
  document.addEventListener('DOMContentLoaded',async()=>{
    try{await refresh();if(state.projects.some(p=>p.id===id))openProject(id);else toast('This linked project is no longer available.',true)}catch(e){toast(e.message,true)}
  },{once:true});
})();
