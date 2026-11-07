'use strict';
// Polling never rebuilds the editor or creator dialog. Only new library cards
// and the folder hint are touched; focused inputs and local choices survive.
function folderHint() {
  if (!current?.folder_info || !$('#workspace .project-top')) return;
  let panel = $('#project-folder-hint');
  if (!panel) {
    $('#workspace .project-top').insertAdjacentHTML('afterend','<section id="project-folder-hint" class="panel"></section>');
    panel = $('#project-folder-hint');
  }
  const f = current.folder_info;
  const text = f.project ? `<details><summary>Video folder</summary><p class="path">${esc(f.project)}</p><label>Save thumbnail PNG, JPEG or WebP files here<input readonly aria-label="Thumbnail folder" value="${esc(f.thumbnails)}"></label><p class="hint">New images appear automatically after saving finishes. Keep Photoshop sources in thumbnails / sources. Existing choices stay as they are.</p></details>` : '';
  const markup = text + (f.scan_error ? `<p role="status">${esc(f.scan_error)}</p>` : '');
  if (panel.dataset.markup !== markup) { panel.innerHTML = markup; panel.dataset.markup = markup; }
}
const folderDetail = renderDetail;
renderDetail = function() { folderDetail(); folderHint(); };

function receiveFolderProjects(projects) {
  if (!Array.isArray(projects)) return;
  projects = projects.map(p => {
    const prior = state.projects.find(x => x.id === p.id);
    return prior && prior.revision > p.revision ? prior : p;
  });
  if (current) {
    const base = state.projects.find(p => p.id === current.id);
    const remote = projects.find(p => p.id === current.id);
    const merged = mergeFolderDiscovery(current, base, remote);
    if (merged) {
      current = merged.project;
      if (merged.added.length && tab === 'thumbnails' && $('.thumbnail-grid')) {
        const template = document.createElement('template'); template.innerHTML = thumbnails();
        for (const t of merged.added) {
          const card = template.content.querySelector(`[data-remove-thumb="${t.asset}"]`)?.closest('article');
          if (card) $('.thumbnail-grid').append(card);
        }
        const count = $('.thumbnail-grid')?.previousElementSibling?.querySelector('.count');
        if (count) count.textContent = current.thumbnails.length + ' imported';
        const empty = $('.thumbnail-grid')?.nextElementSibling;
        if (current.thumbnails.length && empty?.classList.contains('empty')) empty.remove();
      }
      folderHint();
    }
  }
  state.projects = projects;
}
