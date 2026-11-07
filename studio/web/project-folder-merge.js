'use strict';
// Pure merge: only server-certified discovery revisions may advance a draft.
// A missing old thumbnail means the user removed it locally; keep it removed.
function mergeFolderDiscovery(draft, base, remote) {
  if (!draft || !base || !remote || !Number.isInteger(remote.discovery_base_revision) || draft.id !== base.id || draft.id !== remote.id ||
      draft.revision !== base.revision || remote.revision < draft.revision ||
      remote.discovery_base_revision > draft.revision) return null;
  if (remote.revision === draft.revision) return {project: {...draft, folder_info: remote.folder_info}, added: []};
  const known = new Set(base.thumbnails.map(t => t.asset));
  const local = new Set(draft.thumbnails.map(t => t.asset));
  const added = remote.thumbnails.filter(t => !known.has(t.asset) && !local.has(t.asset));
  return {project: {...draft, thumbnails: [...draft.thumbnails, ...structuredClone(added)],
    revision: remote.revision, updated_at: remote.updated_at,
    discovery_base_revision: remote.discovery_base_revision, folder_info: remote.folder_info}, added};
}
if (typeof module !== 'undefined') module.exports = {mergeFolderDiscovery};
