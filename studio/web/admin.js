'use strict';
/* Josh OS · Admin: Clients and Customers. */
const token = document.querySelector('meta[name=dj-token]').content;
const $ = s => document.querySelector(s);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const TZ = 'Asia/Dubai';
const ICON = {
  back: '<svg viewBox="0 0 16 16"><path d="M10 3 5 8l5 5"/></svg>',
  chev: '<svg viewBox="0 0 16 16"><path d="m6 3 5 5-5 5"/></svg>',
  down: '<svg viewBox="0 0 16 16"><path d="m4 6 4 4 4-4"/></svg>',
  search: '<svg viewBox="0 0 16 16"><circle cx="7" cy="7" r="4.6"/><path d="M10.5 10.5 14 14"/></svg>',
  sync: '<svg viewBox="0 0 16 16"><path d="M13.5 8a5.5 5.5 0 0 1-9.6 3.7M2.5 8a5.5 5.5 0 0 1 9.6-3.7"/><path d="M12.4 1.8v2.6H9.8M3.6 14.2v-2.6h2.6"/></svg>',
  folder: '<svg viewBox="0 0 16 16"><path d="M2 4.5A1.5 1.5 0 0 1 3.5 3h2.8l1.4 1.5h4.8A1.5 1.5 0 0 1 14 6v5.5a1.5 1.5 0 0 1-1.5 1.5h-9A1.5 1.5 0 0 1 2 11.5z"/></svg>',
  file: '<svg viewBox="0 0 16 16"><path d="M4 1.8h5l3 3v9.4H4z"/><path d="M9 1.8v3h3"/></svg>',
  doc: '<svg viewBox="0 0 16 16"><path d="M4 1.8h5l3 3v9.4H4z"/><path d="M9 1.8v3h3M6 8.5h4M6 11h4"/></svg>',
  video: '<svg viewBox="0 0 16 16"><rect x="1.8" y="4" width="9" height="8" rx="1.5"/><path d="m10.8 7 3.4-2v6l-3.4-2"/></svg>',
  ext: '<svg viewBox="0 0 16 16"><path d="M9 3h4v4M13 3 7.5 8.5M11 9.5V13H3V5h3.5"/></svg>',
  mail: '<svg viewBox="0 0 16 16"><rect x="2" y="3.5" width="12" height="9" rx="1.5"/><path d="m2.5 4.5 5.5 4.5 5.5-4.5"/></svg>',
  brief: '<svg viewBox="0 0 16 16"><rect x="2" y="5" width="12" height="8.5" rx="1.5"/><path d="M5.5 5V3.5h5V5"/></svg>',
  cal: '<svg viewBox="0 0 16 16"><rect x="2.5" y="3.5" width="11" height="10" rx="2"/><path d="M2.5 6.5h11M5.5 2v3M10.5 2v3"/></svg>',
  up: '<svg viewBox="0 0 16 16"><path d="M8 13V3M4 7l4-4 4 4"/></svg>',
  dn: '<svg viewBox="0 0 16 16"><path d="M8 3v10M4 9l4 4 4-4"/></svg>',
  dash: '<svg viewBox="0 0 16 16"><path d="M4 8h8"/></svg>',
  edit: '<svg viewBox="0 0 16 16"><path d="M10.5 2.5 13.5 5.5 6 13H3v-3z"/></svg>',
};

let route = {name: 'clients'};
let clientsData = null, customersData = null, clientData = null;
const ui = {
  clients: {q: '', filter: 'all', sort: 'joined', dir: -1},
  customers: {q: '', sort: 'first', dir: -1, page: 0, open: new Set()},
  openCalls: new Set(), callTab: {}, callFind: {}, textCache: {}, closedDirs: new Set(),
};

/* ---------- helpers ---------- */
async function api(path, data) {
  const r = await fetch(path, {method: data === undefined ? 'GET' : 'POST', headers: {'X-DJ-Token': token, 'Content-Type': 'application/json'}, body: data === undefined ? undefined : JSON.stringify(data)});
  const out = await r.json().catch(() => ({error: 'The local app did not answer'}));
  if (!r.ok) throw Error(out.error || 'Request failed');
  return out;
}
function toast(msg, err = false) {
  const t = $('#toast'); t.textContent = msg; t.classList.toggle('err', err); t.classList.add('show');
  clearTimeout(toast.t); toast.t = setTimeout(() => t.classList.remove('show'), err ? 7000 : 3200);
}
function money(cents, cur = 'USD', exact = false) {
  const v = (cents || 0) / 100, whole = Number.isInteger(v);
  return new Intl.NumberFormat('en-US', {style: 'currency', currency: cur || 'USD', minimumFractionDigits: whole && !exact ? 0 : 2, maximumFractionDigits: 2}).format(v);
}
function compactMoney(cents, cur = 'USD') {
  const v = (cents || 0) / 100;
  if (v < 10000) return money(cents, cur);
  return new Intl.NumberFormat('en-US', {style: 'currency', currency: cur, notation: 'compact', maximumFractionDigits: 1}).format(v);
}
function asDate(s) { return s ? new Date(s.length === 10 ? s + 'T12:00:00+04:00' : s) : null; }
function day(s, opts = {}) {
  const d = asDate(s); if (!d) return '';
  return d.toLocaleDateString('en-GB', {day: 'numeric', month: 'short', year: opts.year === false ? undefined : 'numeric', timeZone: TZ});
}
function weekday(s) { const d = asDate(s); return d ? d.toLocaleDateString('en-GB', {weekday: 'short', day: 'numeric', month: 'short', timeZone: TZ}) : ''; }
function time(s) { const d = asDate(s); return d ? d.toLocaleTimeString('en-GB', {hour: '2-digit', minute: '2-digit', timeZone: TZ}) : ''; }
function ago(s) {
  if (!s) return 'never';
  const m = Math.round((Date.now() - new Date(s)) / 60000);
  if (m < 1) return 'just now'; if (m < 60) return m + ' min ago';
  const h = Math.round(m / 60); if (h < 24) return h + (h === 1 ? ' hour ago' : ' hours ago');
  return day(s, {year: false});
}
function relDay(s) {
  if (!s) return '';
  const d = Math.round((asDate(s) - Date.now()) / 86400000);
  if (d === 0) return 'Today'; if (d === 1) return 'Tomorrow'; if (d === -1) return 'Yesterday';
  if (d > 1 && d < 7) return 'In ' + d + ' days';
  if (d < 0 && d > -45) return -d + ' days ago';
  return '';
}
function initials(n) { const p = String(n || '?').trim().split(/\s+/); return ((p[0] || '?')[0] + (p.length > 1 ? p[p.length - 1][0] : '')).toUpperCase(); }
function hue(n) { let h = 0; for (const c of String(n)) h = (h * 31 + c.charCodeAt(0)) >>> 0; return 'av' + (h % 6); }
function avatar(n) { return `<span class="avatar ${hue(n)}" aria-hidden="true">${esc(initials(n))}</span>`; }
function statusPill(s) {
  const k = (s || '').toLowerCase();
  const cls = k === 'active' ? 'pill-active' : k === 'new' ? 'pill-new' : k === 'paused' ? 'pill-paused' : k === 'lapsed' ? 'pill-lapsed' : k === 'completed' ? 'pill-done' : 'pill-soft';
  return s ? `<span class="pill ${cls}"><i></i>${esc(s)}</span>` : '';
}
let regionNames; try { regionNames = new Intl.DisplayNames(['en'], {type: 'region'}); } catch (e) { regionNames = null; }
function country(code) { if (!code) return ''; try { return regionNames ? regionNames.of(code) : code; } catch (e) { return code; } }
function size(b) { if (b < 1024) return b + ' B'; if (b < 1048576) return Math.round(b / 1024) + ' KB'; return (b / 1048576).toFixed(1) + ' MB'; }
function sourceNote(conn) {
  const s = conn.stripe, c = conn.calendly;
  const live = s.connected || c.connected;
  const stamp = [s.synced_at, c.synced_at].filter(Boolean).sort().pop();
  return {live, stamp};
}

/* ---------- navigation ---------- */
function parseHash() {
  const h = decodeURIComponent(location.hash.slice(1));
  if (h.startsWith('client/')) return {name: 'client', id: h.slice(7)};
  if (h === 'customers') return {name: 'customers'};
  return {name: 'clients'};
}
function markNav() {
  const sec = route.name === 'customers' ? 'customers' : 'clients';
  document.querySelectorAll('[data-section]').forEach(a => {
    const on = a.dataset.section === sec;
    a.classList.toggle('on', on);
    if (on) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current');
  });
}
async function go() {
  route = parseHash(); markNav();
  try {
    if (route.name === 'customers') { customersData = await api('/api/admin/customers'); renderCustomers(); }
    else if (route.name === 'client') { clientData = await api('/api/admin/client?id=' + encodeURIComponent(route.id)); renderClient(); }
    else { clientsData = await api('/api/admin/clients'); renderClients(); }
    const fresh = (route.name === 'customers' ? customersData : route.name === 'clients' ? clientsData : null);
    lastConn = fresh ? fresh.connections : await api('/api/admin/connections').catch(() => lastConn);
    renderSideFoot();
  } catch (e) {
    $('#main').innerHTML = `<div class="wrap"><div class="panel empty"><b>That didn't load.</b>${esc(e.message)}<p><button class="btn btn-secondary" data-act="retry">Try again</button></p></div></div>`;
  }
}
window.addEventListener('hashchange', () => { go(); window.scrollTo(0, 0); });

let lastConn = null;
function connections() { return lastConn; }
async function renderSideFoot() {
  const conn = connections(); if (!conn) return;
  const {live, stamp} = sourceNote(conn);
  $('#side-sync').innerHTML = live
    ? `<b>Stripe and Calendly</b>Updated ${esc(ago(stamp))}<br><button data-act="sync">Sync now</button> · <button data-act="connect">Keys</button>`
    : `<b>Snapshot from ${esc(day(stamp, {year: false}))}</b>Add keys to keep it live.<br><button data-act="connect">Connect Stripe and Calendly</button>`;
}

function head(title, lede, conn) {
  const {live, stamp} = sourceNote(conn);
  return `<div class="page-head"><div><h1>${esc(title)}</h1><p>${lede}</p></div>
    <div class="head-actions"><div class="sync-note">${live ? 'Updated' : 'Snapshot taken'}<br><b>${esc(ago(stamp))}</b></div>
    ${live ? `<button class="btn btn-secondary" data-act="sync">${ICON.sync}Sync now</button>` : `<button class="btn btn-secondary" data-act="connect">${ICON.sync}Keep it live</button>`}</div></div>`;
}
function stat(label, value, sub, lead = false) {
  return `<div class="stat${lead ? ' lead' : ''}"><div class="stat-label">${esc(label)}</div><div class="stat-value" title="${esc(String(value).replace(/<[^>]+>/g, ''))}">${value}</div><div class="stat-sub">${sub || ''}</div></div>`;
}

/* ---------- Clients ---------- */
function clientRows() {
  const s = ui.clients, q = s.q.trim().toLowerCase();
  let rows = clientsData.clients.filter(c => {
    if (s.filter === 'active' && !/^(active|new)$/i.test(c.status)) return false;
    if (s.filter === 'nofolder' && c.folder) return false;
    if (s.filter === 'upcoming' && !c.next_call) return false;
    if (!q) return true;
    return [c.name, c.email, c.programme, c.status].join(' ').toLowerCase().includes(q);
  });
  const key = {name: c => c.name.toLowerCase(), joined: c => c.joined || '', last: c => c.last_call || '', next: c => c.next_call ? c.next_call.start : (s.dir > 0 ? '9' : ''), calls: c => c.call_count, ltv: c => c.ltv}[s.sort];
  rows.sort((a, b) => { const x = key(a), y = key(b); return (x > y ? 1 : x < y ? -1 : 0) * s.dir; });
  return rows;
}
function th(label, key, state, cls = '') {
  const on = state.sort === key;
  return `<th class="sortable ${cls}" data-sort="${key}" aria-sort="${on ? (state.dir > 0 ? 'ascending' : 'descending') : 'none'}">${esc(label)}<span class="arrow">${on ? (state.dir > 0 ? '↑' : '↓') : ''}</span></th>`;
}
function renderClients() {
  const d = clientsData, st = d.stats, s = ui.clients;
  const rows = clientRows();
  const next = st.next;
  const counts = {all: d.clients.length, active: d.clients.filter(c => /^(active|new)$/i.test(c.status)).length, upcoming: d.clients.filter(c => c.next_call).length, nofolder: st.no_folder};
  const {live} = sourceNote(d.connections);
  $('#main').innerHTML = `<div class="wrap">
    ${head('Clients', 'Everyone you coach, what they’ve paid and when you next speak.', d.connections)}
    ${live ? '' : `<div class="banner"><span>These numbers come from a Stripe and Calendly snapshot. <b>Add two read-only keys</b> and they stay current on their own.</span><span class="spacer"></span><button class="btn btn-sm btn-secondary" data-act="connect">Connect</button></div>`}
    <div class="stats">
      ${stat('Client revenue', esc(money(st.revenue)), `<strong>${esc(money(st.revenue_month))}</strong> this month`, true)}
      ${stat('Active clients', st.active, st.no_folder ? `<strong>${st.no_folder}</strong> still need a folder` : `of ${st.total} in total`)}
      ${stat('Average lifetime value', esc(money(st.avg_ltv)), `across ${d.clients.filter(c => c.ltv > 0).length} paying clients`)}
      ${stat('Calls next 7 days', st.calls_week, next ? `<strong>${esc(next.client.split(' ')[0])}</strong> ${esc(weekday(next.start))}, ${esc(time(next.start))}` : 'Nothing booked')}
    </div>
    <section class="panel">
      <div class="panel-head"><h2>All clients<span class="count">${rows.length}</span></h2></div>
      <div class="toolbar">
        <label class="search">${ICON.search}<input id="client-q" type="search" placeholder="Search by name, email or programme" value="${esc(s.q)}" aria-label="Search clients"></label>
        <div class="filters" role="group" aria-label="Filter clients">${[['all', 'All'], ['active', 'Active'], ['upcoming', 'Call booked'], ['nofolder', 'No folder']].map(([k, l]) => `<button data-filter="${k}" class="${s.filter === k ? 'on' : ''}" aria-pressed="${s.filter === k}">${l}<em>${counts[k]}</em></button>`).join('')}</div>
      </div>
      <div class="table-wrap"><table class="data"><thead><tr>
        ${th('Client', 'name', s)}<th>Programme</th>${th('Joined', 'joined', s)}${th('Last call', 'last', s)}${th('Next call', 'next', s)}${th('Calls', 'calls', s, 'r')}${th('LTV', 'ltv', s, 'r')}<th>Status</th>
      </tr></thead><tbody>
        ${rows.map(c => `<tr class="click" data-client="${esc(c.id)}" tabindex="0">
          <td><div class="who">${avatar(c.name)}<div class="who-text"><b>${esc(c.name)}${c.folder ? '' : '<span class="tag-nofolder">No folder</span>'}</b><small>${esc(c.email || 'No email on file')}</small></div></div></td>
          <td class="muted clip" title="${esc(c.programme)}">${esc(c.programme || '—')}</td>
          <td>${esc(day(c.joined))}</td>
          <td>${c.last_call ? `<div class="when"><b>${esc(day(c.last_call, {year: false}))}</b><small>${esc(relDay(c.last_call))}</small></div>` : '<span class="muted">None yet</span>'}</td>
          <td>${c.next_call ? `<div class="when"><b>${esc(weekday(c.next_call.start))}</b><small>${esc(time(c.next_call.start))} Dubai</small></div>` : '<span class="muted">Not booked</span>'}</td>
          <td class="r num">${c.call_count}</td>
          <td class="r num">${c.ltv ? `<b>${esc(money(c.ltv, c.currency))}</b>` : '<span class="muted">$0</span>'}</td>
          <td>${statusPill(c.status)}</td></tr>`).join('') || `<tr><td colspan="8"><div class="empty"><b>No clients match.</b>Try a different name or clear the filter.</div></td></tr>`}
      </tbody></table></div>
    </section></div>`;
}

/* ---------- Client detail ---------- */
function callParts(k) {
  const tabs = [];
  if (k.parts.summary) tabs.push(['summary', 'Summary', k.parts.summary]);
  if (k.parts.transcript) tabs.push(['transcript', 'Transcript', k.parts.transcript]);
  if (k.parts.notes) tabs.push(['notes', 'Notes', k.parts.notes]);
  return tabs;
}
function callRow(k, folder) {
  const tabs = callParts(k), open = ui.openCalls.has(k.id), b = k.booking;
  const span = b ? `${time(b.start)}–${time(b.end)}` : '';
  const sub = [asDate(k.date) ? asDate(k.date).toLocaleDateString('en-GB', {weekday: 'long', timeZone: TZ}) : '', k.section === 'Booking' ? 'Calendly booking' : k.section, b && b.event !== k.title && k.section !== 'Booking' ? b.event : '', span].filter(Boolean).join(' · ');
  const has = tabs.map(t => `<span class="pill pill-soft">${t[1]}</span>`).join('') + (k.links.length || k.local_video ? '<span class="pill pill-gold">Recording</span>' : '');
  const expandable = tabs.length || k.links.length || k.local_video;
  const d = asDate(k.date);
  const chip = `<span class="date"><b>${d ? d.toLocaleDateString('en-GB', {day: 'numeric', timeZone: TZ}) : '–'}</b><small>${d ? d.toLocaleDateString('en-GB', {month: 'short', timeZone: TZ}) : ''}</small></span>`;
  const main = `${chip}<span class="call-main"><b>${esc(k.title)}</b><small>${esc(sub)}</small></span>`;
  if (!expandable) return `<li class="call"><div class="call-row">${main}<span class="call-has"><span class="nothing">Nothing saved for this call</span></span></div></li>`;
  return `<li class="call ${open ? 'open' : ''}"><button class="call-row" data-call="${esc(k.id)}" aria-expanded="${open}">${main}<span class="call-has">${has}</span><span class="chev">${ICON.chev}</span></button>
    ${open ? `<div class="call-body">${callBody(k, folder, tabs)}</div>` : ''}</li>`;
}
function callBody(k, folder, tabs) {
  const tab = ui.callTab[k.id] || (tabs[0] && tabs[0][0]);
  const links = k.links.map(l => `<a class="btn btn-sm btn-secondary ext" href="${esc(l.url)}" target="_blank" rel="noopener noreferrer">${l.label === 'Recording' ? ICON.video : ICON.ext}${esc(l.label === 'Recording' ? 'Watch recording' : l.label)}</a>`).join('');
  const video = k.local_video ? `<button class="btn btn-sm btn-secondary" data-open-path="${esc(k.id + '/' + k.local_video)}">${ICON.video}Play recording</button>` : '';
  const find = tab === 'transcript' ? `<input class="find-in" data-find="${esc(k.id)}" placeholder="Find in transcript" value="${esc(ui.callFind[k.id] || '')}" aria-label="Find in transcript">` : '';
  const head = `<div class="tabs">${tabs.map(t => `<button data-calltab="${esc(k.id)}" data-tab="${t[0]}" class="${t[0] === tab ? 'on' : ''}">${t[1]}</button>`).join('')}<span class="spacer"></span>${find}${links}${video}<button class="btn btn-sm btn-quiet" data-open-path="${esc(k.id)}">${ICON.folder}Folder</button></div>`;
  if (!tab) return head;
  const file = tabs.find(t => t[0] === tab)[2], key = folder + '|' + k.id + '/' + file;
  const text = ui.textCache[key];
  if (text === undefined) {
    api('/api/admin/text?id=' + encodeURIComponent(folder) + '&path=' + encodeURIComponent(k.id + '/' + file)).then(r => { ui.textCache[key] = r.text; renderCalls(); }).catch(e => { ui.textCache[key] = '\u0000' + e.message; renderCalls(); });
    return head + '<div class="well"><span class="nothing">Loading…</span></div>';
  }
  if (text.startsWith('\u0000')) return head + `<div class="well"><span class="nothing">${esc(text.slice(1))}</span></div>`;
  const body = tab === 'transcript' ? transcript(text, ui.callFind[k.id]) : file.endsWith('.md') ? markdown(text) : notes(text);
  return head + `<div class="well"><div class="prose">${body}</div></div>`;
}
function upcomingRow(e) {
  const d = asDate(e.start);
  return `<li class="call upcoming"><div class="call-row"><span class="date"><b>${d.toLocaleDateString('en-GB', {day: 'numeric', timeZone: TZ})}</b><small>${d.toLocaleDateString('en-GB', {month: 'short', timeZone: TZ})}</small></span>
    <span class="call-main"><b>${esc(e.event)}</b><small>${esc(weekday(e.start))} · ${esc(time(e.start))}–${esc(time(e.end))} Dubai${relDay(e.start) ? ' · ' + esc(relDay(e.start)) : ''}</small></span>
    <span class="call-has"><span class="pill pill-gold">Upcoming</span>${e.join_url ? `<a class="btn btn-sm btn-secondary ext" href="${esc(e.join_url)}" target="_blank" rel="noopener noreferrer">${ICON.ext}Join</a>` : ''}</span></div></li>`;
}
function renderCalls() {
  const el = $('#calls'); if (!el || !clientData) return;
  const c = clientData;
  const list = c.upcoming.map(upcomingRow).join('') + c.calls.map(k => callRow(k, c.folder)).join('');
  el.innerHTML = list || `<li><div class="empty"><b>No calls yet.</b>Calendly bookings and saved transcripts show up here.</div></li>`;
  for (const id of Object.keys(ui.callFind)) {
    const inp = el.querySelector(`[data-find="${CSS.escape(id)}"]`);
    if (inp && document.activeElement !== inp && inp.dataset.focus) inp.focus();
  }
}
function treeHTML(nodes, depth = 0) {
  return nodes.map(n => {
    if (n.dir) {
      const closed = depth > 0 ? !ui.closedDirs.has('open:' + n.path) : ui.closedDirs.has(n.path);
      const count = countFiles(n);
      return `<li class="${closed ? 'closed' : ''}"><button class="node dir ${closed ? 'closed' : ''}" data-dir="${esc(n.path)}" data-depth="${depth}" aria-expanded="${!closed}"><span class="tw">${n.children.length ? ICON.down : ''}</span><span class="ico">${ICON.folder}</span><span class="nm">${esc(n.name)}</span><span class="sz">${count ? count + (count === 1 ? ' file' : ' files') : 'Empty'}</span></button>${n.children.length ? `<ul>${treeHTML(n.children, depth + 1)}</ul>` : ''}</li>`;
    }
    const ic = /\.(mp4|mov|mkv|m4a|webm|mp3|wav)$/i.test(n.name) ? ICON.video : /\.(md|txt|docx?|pdf|html)$/i.test(n.name) ? ICON.doc : ICON.file;
    return `<li><button class="node" data-open-path="${esc(n.path)}" title="Open ${esc(n.name)}"><span class="tw"></span><span class="ico">${ic}</span><span class="nm">${esc(n.name)}</span><span class="act">Open</span><span class="sz">${esc(size(n.size))}</span></button></li>`;
  }).join('');
}
function countFiles(n) { return n.dir ? n.children.reduce((a, c) => a + countFiles(c), 0) : 1; }
function renderClient() {
  const c = clientData, next = c.upcoming[0];
  const last = c.calls[0];
  const emails = c.emails.length ? c.emails : [];
  $('#main').innerHTML = `<div class="wrap">
    <a class="crumb" href="#clients">${ICON.back}Clients</a>
    <section class="panel hero">
      <div class="hero-top">${avatar(c.name)}
        <div class="hero-id"><h1>${esc(c.name)} ${statusPill(c.status)}</h1>
          <div class="hero-meta">${emails[0] ? `<span>${ICON.mail}${esc(emails[0])}</span>` : ''}${c.programme ? `<span>${ICON.brief}${esc(c.programme)}</span>` : ''}${c.joined ? `<span>${ICON.cal}Joined ${esc(day(c.joined))}</span>` : ''}</div>
        </div>
        <div class="hero-actions">
          <button class="btn btn-secondary" data-act="edit">${ICON.edit}Edit details</button>
          ${c.folder ? `<button class="btn btn-primary" data-open-path="">${ICON.folder}Open folder</button>` : `<button class="btn btn-primary" data-act="create-folder">${ICON.folder}Create folder</button>`}
        </div>
      </div>
      ${c.about ? `<p class="about">${esc(c.about)}</p>` : ''}
      <div class="figures">
        <div class="figure"><b>${esc(money(c.ltv, c.currency))}</b><small>Lifetime value</small></div>
        <div class="figure"><b>${c.calls.length}</b><small>${c.calls.length === 1 ? 'Call on record' : 'Calls on record'}</small></div>
        <div class="figure"><b class="${last ? '' : 'none'}">${last ? esc(day(last.date, {year: false})) : 'None yet'}</b><small>${last ? 'Last call' + (relDay(last.date) ? ', ' + esc(relDay(last.date).toLowerCase()) : '') : 'No calls yet'}</small></div>
        <div class="figure"><b class="${next ? '' : 'none'}">${next ? esc(weekday(next.start)) : 'Not booked'}</b><small>${next ? 'Next call at ' + esc(time(next.start)) : 'Next call'}</small></div>
      </div>
    </section>
    <div class="grid-detail">
      <div class="stack">
        <section class="panel"><div class="panel-head"><h2>Calls<span class="count">${c.calls.length + c.upcoming.length}</span></h2></div><ul class="calls" id="calls"></ul></section>
        <section class="panel"><div class="panel-head"><h2>Files</h2>${c.folder ? `<button class="btn btn-sm btn-quiet" data-open-path="">${ICON.ext}Open in Explorer</button>` : ''}</div>
          ${c.folder ? `<ul class="tree" id="tree">${treeHTML(c.tree)}</ul>` : `<div class="empty"><b>No folder yet.</b>Create one and it gets the usual five sections, plus a row in the client index.<p><button class="btn btn-primary" data-act="create-folder">${ICON.folder}Create folder</button></p></div>`}
        </section>
      </div>
      <div class="stack">
        ${next ? `<section class="panel side-card next-card"><h3>Next call <small>${esc(relDay(next.start))}</small></h3><div class="next-when">${esc(weekday(next.start))}, ${esc(time(next.start))}</div><div class="next-sub">${esc(next.event)} · Dubai time</div>
          <div class="next-actions">${next.join_url ? `<a class="btn btn-sm btn-primary ext" href="${esc(next.join_url)}" target="_blank" rel="noopener noreferrer">${ICON.ext}Join call</a>` : ''}${next.reschedule_url ? `<a class="btn btn-sm btn-quiet" href="${esc(next.reschedule_url)}" target="_blank" rel="noopener noreferrer">Reschedule</a>` : ''}</div></section>` : ''}
        <section class="panel side-card"><h3>Payments <small>${c.payments.length}</small></h3>
          ${c.payments.length ? c.payments.map(p => `<div class="pay"><span><b>${esc(p.product || 'Payment')}</b><small>${esc(day(p.created))}${p.refunded ? ' · refunded ' + esc(money(p.refunded, p.currency)) : ''}</small></span><span class="amt">${esc(money(p.amount - p.refunded, p.currency))}</span></div>`).join('') + `<div class="pay-total"><span>Total</span><span class="num">${esc(money(c.ltv, c.currency))}</span></div>` : '<p class="nothing">No Stripe payments linked. Add the email they paid with under Edit details.</p>'}
        </section>
        <section class="panel side-card"><h3>Details</h3><div class="kv">
          <div><small>${emails.length > 1 ? 'Emails' : 'Email'}</small>${emails.map(e => `<span>${esc(e)}</span>`).join('') || '<span class="nothing">None yet</span>'}</div>
          ${c.testimonial ? `<div><small>Testimonial</small><span>${esc(c.testimonial)}</span></div>` : ''}
          <div><small>Folder</small><span>${c.folder ? esc(c.folder_path) : 'Not created'}</span></div>
        </div></section>
      </div>
    </div></div>`;
  renderCalls();
}

/* ---------- text renderers ---------- */
function inline(s) { return esc(s).replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>').replace(/`([^`]+)`/g, '<code>$1</code>'); }
function markdown(text) {
  const out = []; let list = false;
  const close = () => { if (list) { out.push('</ul>'); list = false; } };
  let para = [];
  const flush = () => { if (para.length) { out.push('<p>' + inline(para.join(' ')) + '</p>'); para = []; } };
  for (const raw of text.split(/\r?\n/)) {
    const line = raw.trim();
    if (!line) { flush(); close(); continue; }
    let m;
    if ((m = line.match(/^(#{1,6})\s+(.*)/))) { flush(); close(); out.push(m[1].length <= 1 ? `<h3>${inline(m[2])}</h3>` : `<h4>${inline(m[2])}</h4>`); continue; }
    if ((m = line.match(/^[-*•]\s+(.*)/))) { flush(); if (!list) { out.push('<ul>'); list = true; } out.push('<li>' + inline(m[1]) + '</li>'); continue; }
    if ((m = line.match(/^>\s?(.*)/))) { flush(); close(); out.push('<blockquote>' + inline(m[1]) + '</blockquote>'); continue; }
    close(); para.push(line);
  }
  flush(); close();
  return out.join('');
}
function notes(text) {
  // Calendly Notetaker exports: blank-line blocks, short lines as headings.
  const blocks = text.replace(/\r/g, '').split(/\n\s*\n/).map(b => b.split('\n').map(l => l.trimEnd()).filter(l => l.trim()));
  const out = [];
  const isHead = l => l.length < 90 && !/[.:;,!?]$/.test(l.trim()) && !/^[-•]/.test(l.trim());
  for (const b of blocks) {
    if (!b.length) continue;
    if (b.length === 1 && b[0].length < 40 && isHead(b[0])) { out.push(`<h3>${esc(b[0].trim())}</h3>`); continue; }
    let i = 0;
    if (b.length > 1 && isHead(b[0])) {
      const h = b[0].trim();
      out.push(h === h.toUpperCase() ? `<h4>${esc(h.toLowerCase().replace(/\b\w/g, c => c.toUpperCase()))}</h4>` : `<h4>${esc(h)}</h4>`); i = 1;
    }
    const rest = b.slice(i);
    if (rest.every(l => /^\s*[-•]/.test(l))) out.push('<ul>' + rest.map(l => '<li>' + inline(l.replace(/^\s*[-•]\s*/, '')) + '</li>').join('') + '</ul>');
    else out.push('<p>' + inline(rest.join(' ')) + '</p>');
  }
  return out.join('');
}
function transcript(text, find) {
  const turns = [];
  for (const raw of text.replace(/\r/g, '').split('\n')) {
    const line = raw.trim(); if (!line) continue;
    const m = line.match(/^([^:]{2,40}):\s*(.*)$/);
    if (m && !/^\d/.test(m[1])) {
      const who = m[1].trim();
      if (turns.length && turns[turns.length - 1].who.toLowerCase() === who.toLowerCase()) turns[turns.length - 1].text += ' ' + m[2];
      else turns.push({who, text: m[2]});
    } else if (turns.length) turns[turns.length - 1].text += ' ' + line;
    else turns.push({who: '', text: line});
  }
  const q = (find || '').trim();
  const rx = q ? new RegExp(q.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'i') : null;
  const shown = rx ? turns.filter(t => rx.test(t.text)) : turns;
  if (rx && !shown.length) return `<p class="nothing">Nothing matches “${esc(q)}”.</p>`;
  const nice = w => w === w.toUpperCase() ? w.toLowerCase().replace(/\b\w/g, c => c.toUpperCase()) : w;
  return (rx ? `<p class="nothing">${shown.length} ${shown.length === 1 ? 'passage mentions' : 'passages mention'} “${esc(q)}”</p>` : '') + shown.map(t => {
    let body = esc(t.text.trim());
    if (rx) body = body.replace(new RegExp(esc(q).replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'gi'), m => `<mark>${m}</mark>`);
    return `<div class="turn ${/josh/i.test(t.who) ? 'me' : ''}"><b title="${esc(t.who)}">${esc(nice(t.who))}</b><p>${body}</p></div>`;
  }).join('');
}

/* ---------- Customers ---------- */
function customerRows() {
  const s = ui.customers, q = s.q.trim().toLowerCase();
  let rows = customersData.customers.filter(c => !q || [c.name, c.email, country(c.country), c.country].join(' ').toLowerCase().includes(q));
  const key = {name: c => (c.name || c.email).toLowerCase(), country: c => country(c.country), first: c => c.first, total: c => c.total, orders: c => c.orders}[s.sort];
  rows.sort((a, b) => { const x = key(a), y = key(b); return (x > y ? 1 : x < y ? -1 : 0) * s.dir; });
  return rows;
}
const PER_PAGE = 20;
function delta(now, then) {
  if (!then && !now) return '<span class="delta flat">' + ICON.dash + 'Level</span>';
  if (!then) return '<span class="delta up">' + ICON.up + 'New</span>';
  const pct = Math.round((now - then) / then * 100);
  if (pct === 0) return '<span class="delta flat">' + ICON.dash + '0%</span>';
  return `<span class="delta ${pct > 0 ? 'up' : 'down'}">${pct > 0 ? ICON.up : ICON.dn}${Math.abs(pct)}%</span>`;
}
function salesChart(daily) {
  const days = [];
  const today = new Date(new Date().toLocaleString('en-US', {timeZone: TZ}));
  for (let i = 29; i >= 0; i--) {
    const d = new Date(today); d.setDate(d.getDate() - i);
    const k = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
    days.push({k, n: daily[k] || 0, d});
  }
  const max = Math.max(2, ...days.map(x => x.n));
  const W = 720, H = 180, top = 12, bottom = 28, left = 24, bw = (W - left) / days.length, gap = Math.round(bw * 0.3);
  const y = n => top + (H - top - bottom) * (1 - n / max);
  const ticks = [0, Math.ceil(max / 2), max].filter((v, i, a) => a.indexOf(v) === i);
  const bars = days.map((x, i) => {
    const xx = left + i * bw + gap / 2, w = bw - gap, h = (H - bottom) - y(x.n);
    const label = x.d.toLocaleDateString('en-GB', {weekday: 'short', day: 'numeric', month: 'short'});
    const bar = x.n ? `<path class="barr" d="M${xx},${H - bottom} v${-(h - 4)} a4,4 0 0 1 4,-4 h${w - 8} a4,4 0 0 1 4,4 v${h - 4} z"/>` : `<rect class="barr zero" x="${xx}" y="${H - bottom - 2}" width="${w}" height="2" rx="1"/>`;
    return `<g class="hit" data-tip="${esc(label)}" data-n="${x.n}"><rect class="hitbox" x="${left + i * bw}" y="${top}" width="${bw}" height="${H - top - bottom + 8}"/>${bar}</g>`;
  }).join('');
  const grid = ticks.map(t => `<line class="${t ? 'grid' : 'base'}" x1="${left}" x2="${W}" y1="${y(t)}" y2="${y(t)}"/><text class="axis" x="${left - 8}" y="${y(t) + 4}" text-anchor="end">${t}</text>`).join('');
  const labels = days.map((x, i) => (i % 7 === 2 || i === 29) ? `<text class="axis" x="${left + i * bw + bw / 2}" y="${H - 8}" text-anchor="middle">${i === 29 ? 'Today' : esc(x.d.toLocaleDateString('en-GB', {day: 'numeric', month: 'short'}))}</text>` : '').join('');
  const total = days.reduce((a, x) => a + x.n, 0);
  return {svg: `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Masterclass sales per day for the last 30 days, ${total} in total">${grid}${bars}${labels}</svg>`, total};
}
function renderCustomers() {
  const d = customersData, st = d.stats, s = ui.customers;
  const rows = customerRows(), pages = Math.max(1, Math.ceil(rows.length / PER_PAGE));
  s.page = Math.min(s.page, pages - 1);
  const slice = rows.slice(s.page * PER_PAGE, (s.page + 1) * PER_PAGE);
  const chart = salesChart(st.daily);
  const maxC = Math.max(1, ...st.top_countries.map(x => x[1]));
  const {live} = sourceNote(d.connections);
  $('#main').innerHTML = `<div class="wrap">
    ${head('Customers', 'Everyone who has bought the Executive Communication Masterclass.', d.connections)}
    ${live ? '' : `<div class="banner"><span>Sales come from a Stripe snapshot. <b>Add a read-only Stripe key</b> and new buyers appear here within half an hour.</span><span class="spacer"></span><button class="btn btn-sm btn-secondary" data-act="connect">Connect</button></div>`}
    <div class="stats">
      ${stat('Masterclass revenue', esc(money(st.revenue)), `<strong>${esc(money(st.aov))}</strong> average order`, true)}
      ${stat('Customers', st.customers, `in <strong>${st.countries}</strong> ${st.countries === 1 ? 'country' : 'countries'}`)}
      ${stat(st.month_label + ' sales', st.month_sales, `${delta(st.month_sales, st.last_month_sales)} <span>vs ${st.last_month_sales} by ${st.day} ${esc(st.last_label.slice(0, 3))}</span>`)}
      ${stat('Last 7 days', st.week_sales, `${delta(st.week_sales, st.prev_week_sales)} <span>vs ${st.prev_week_sales} the week before</span>`)}
    </div>
    <div class="split">
      <section class="panel chart-panel" style="margin:0"><div class="panel-head"><h2>Sales per day<span class="count">${chart.total} in the last 30 days</span></h2></div><div class="chart" id="chart">${chart.svg}</div></section>
      <section class="panel" style="margin:0"><div class="panel-head"><h2>Where they are</h2></div><div class="bars-list">
        ${st.top_countries.map(([k, n]) => `<div class="hbar"><span title="${esc(country(k))}">${esc(k)}</span><div class="track"><i style="width:${Math.max(6, n / maxC * 100)}%"></i></div><em>${n}</em></div>`).join('') || '<p class="nothing">No countries recorded yet.</p>'}
      </div></section>
    </div>
    <section class="panel">
      <div class="panel-head"><h2>All customers<span class="count">${rows.length}</span></h2></div>
      <div class="toolbar"><label class="search">${ICON.search}<input id="cust-q" type="search" placeholder="Search by name, email or country" value="${esc(s.q)}" aria-label="Search customers"></label><span class="spacer"></span><span class="meta">First sale ${esc(day(st.first_sale))}</span></div>
      <div class="table-wrap"><table class="data"><thead><tr>
        ${th('Customer', 'name', s)}${th('Country', 'country', s)}${th('Bought', 'first', s)}<th>Items</th>${th('Orders', 'orders', s, 'r')}${th('Total', 'total', s, 'r')}
      </tr></thead><tbody>
      ${slice.map(c => {
        const open = s.open.has(c.email);
        const items = [...new Set(c.items.map(i => /executive communication/i.test(i.product) ? 'Masterclass' : i.product))];
        return `<tr class="click" data-cust="${esc(c.email)}" tabindex="0" aria-expanded="${open}">
          <td><div class="who">${avatar(c.name || c.email)}<div class="who-text"><b>${esc(c.name || 'No name')}</b><small>${esc(c.email)}</small></div></div></td>
          <td>${esc(country(c.country) || '—')}</td>
          <td><div class="when"><b>${esc(day(c.first))}</b><small>${esc(relDay(c.first) || time(c.first) + ' Dubai')}</small></div></td>
          <td>${items.map(i => `<span class="pill pill-soft">${esc(i)}</span>`).join(' ')} ${c.bump ? '<span class="plus">+ Templates</span>' : ''} ${c.refunded ? '<span class="pill pill-lapsed">Refunded</span>' : ''}</td>
          <td class="r num">${c.orders}</td>
          <td class="r num"><b>${esc(money(c.total, c.currency))}</b></td></tr>
          ${open ? `<tr class="detail"><td colspan="6"><div class="orders">${c.items.map(i => `<div class="order"><span>${esc(day(i.created))}, ${esc(time(i.created))}</span><span>${esc(i.product)}${i.bump ? ' + Templates' : ''}${i.refunded ? ' · refunded' : ''}</span><span class="r">${esc(money(i.amount - i.refunded, c.currency))}</span></div>`).join('')}</div></td></tr>` : ''}`;
      }).join('') || `<tr><td colspan="6"><div class="empty"><b>No customers match.</b>Try a different name, email or country.</div></td></tr>`}
      </tbody></table></div>
      <div class="pager"><span>${rows.length ? `Showing ${s.page * PER_PAGE + 1}–${Math.min(rows.length, (s.page + 1) * PER_PAGE)} of ${rows.length}` : 'No results'}</span>
        <span class="pages"><button class="btn btn-sm btn-secondary" data-page="-1" ${s.page === 0 ? 'disabled' : ''}>Previous</button><button class="btn btn-sm btn-secondary" data-page="1" ${s.page >= pages - 1 ? 'disabled' : ''}>Next</button></span></div>
    </section></div>`;
}

/* ---------- dialogs ---------- */
function openConnect() {
  const conn = connections() || {stripe: {}, calendly: {}};
  $('#stripe-key').value = ''; $('#calendly-token').value = '';
  $('#stripe-key').placeholder = conn.stripe.connected ? 'Connected, ends ' + conn.stripe.key_end + '. Paste a new key to replace it' : 'rk_live_…';
  $('#calendly-token').placeholder = conn.calendly.connected ? 'Connected, ends ' + conn.calendly.key_end + '. Paste a new token to replace it' : 'Paste your token';
  $('#stripe-state').textContent = conn.stripe.connected ? 'Connected' : '';
  $('#calendly-state').textContent = conn.calendly.connected ? 'Connected' : '';
  $('#connect-error').textContent = '';
  $('#connect').showModal(); $('#stripe-key').focus();
}
$('#connect-cancel').onclick = () => $('#connect').close();
$('#connect-form').onsubmit = async e => {
  e.preventDefault();
  const b = $('#connect-save'); b.disabled = true; b.innerHTML = '<span class="spin"></span>Checking keys';
  try {
    const out = await api('/api/admin/connect', {stripe_key: $('#stripe-key').value, calendly_token: $('#calendly-token').value});
    $('#connect').close(); toast('Saved. Pulling the latest from Stripe and Calendly…');
    waitForSync(out);
  } catch (err) { $('#connect-error').textContent = err.message; }
  finally { b.disabled = false; b.textContent = 'Save and sync'; }
};
function openEdit() {
  const c = clientData;
  $('#edit-emails').value = c.emails.join(', '); $('#edit-programme').value = c.programme || '';
  $('#edit-status').value = c.status || 'Active'; $('#edit-joined').value = c.joined || '';
  $('#edit-company').value = c.company || ''; $('#edit-error').textContent = '';
  $('#edit').showModal(); $('#edit-emails').focus();
}
$('#edit-cancel').onclick = () => $('#edit').close();
$('#edit-form').onsubmit = async e => {
  e.preventDefault();
  try {
    const changes = {emails: $('#edit-emails').value, programme: $('#edit-programme').value, status: $('#edit-status').value, joined: $('#edit-joined').value};
    if ($('#edit-company').value.trim()) changes.company = $('#edit-company').value;
    clientData = await api('/api/admin/update', {id: clientData.id, changes});
    $('#edit').close(); renderClient(); toast('Details saved');
  } catch (err) { $('#edit-error').textContent = err.message; }
};
async function waitForSync(first) {
  let tries = 0;
  const tick = async () => {
    const c = await api('/api/admin/connections').catch(() => null);
    if (c && !c.running || ++tries > 40) { await go(); const err = c && (c.stripe.error || c.calendly.error); toast(err ? 'Sync finished with a problem: ' + err : 'Up to date', !!err); return; }
    setTimeout(tick, 1500);
  };
  setTimeout(tick, 1200);
}

/* ---------- events ---------- */
document.addEventListener('click', async e => {
  const el = e.target.closest('button,[data-client],[data-cust],a[data-section]');
  if (!el || el.disabled) return;
  try {
    if (el.dataset.act === 'retry') return go();
    if (el.dataset.act === 'connect') return openConnect();
    if (el.dataset.act === 'edit') return openEdit();
    if (el.dataset.act === 'sync') {
      el.disabled = true; const old = el.innerHTML; el.innerHTML = '<span class="spin"></span>Syncing';
      try { await api('/api/admin/sync', {}); await go(); toast('Up to date'); } finally { el.disabled = false; el.innerHTML = old; }
      return;
    }
    if (el.dataset.act === 'create-folder') {
      el.disabled = true;
      clientData = await api('/api/admin/create-folder', {id: clientData.id});
      history.replaceState(null, '', '#client/' + encodeURIComponent(clientData.id)); renderClient(); toast('Folder created and added to the client index');
      return;
    }
    if (el.dataset.client) { location.hash = 'client/' + encodeURIComponent(el.dataset.client); return; }
    if (el.dataset.cust) { const s = ui.customers.open; s.has(el.dataset.cust) ? s.delete(el.dataset.cust) : s.add(el.dataset.cust); renderCustomers(); return; }
    if (el.dataset.filter) { ui.clients.filter = el.dataset.filter; renderClients(); return; }
    if (el.dataset.sort) {
      const s = route.name === 'customers' ? ui.customers : ui.clients;
      if (s.sort === el.dataset.sort) s.dir *= -1; else { s.sort = el.dataset.sort; s.dir = ['name', 'country'].includes(s.sort) ? 1 : -1; }
      route.name === 'customers' ? renderCustomers() : renderClients(); return;
    }
    if (el.dataset.page) { ui.customers.page += Number(el.dataset.page); renderCustomers(); window.scrollTo({top: 0, behavior: 'smooth'}); return; }
    if (el.dataset.call) { const s = ui.openCalls; s.has(el.dataset.call) ? s.delete(el.dataset.call) : s.add(el.dataset.call); renderCalls(); return; }
    if (el.dataset.calltab) { ui.callTab[el.dataset.calltab] = el.dataset.tab; renderCalls(); return; }
    if (el.dataset.dir !== undefined) {
      const p = el.dataset.dir, depth = Number(el.dataset.depth), s = ui.closedDirs;
      const k = depth > 0 ? 'open:' + p : p;
      s.has(k) ? s.delete(k) : s.add(k);
      $('#tree').innerHTML = treeHTML(clientData.tree); return;
    }
    if (el.dataset.openPath !== undefined) {
      await api('/api/admin/open', {id: clientData.folder, path: el.dataset.openPath});
      toast(el.dataset.openPath ? 'Opening on your PC' : 'Opening the folder');
      return;
    }
  } catch (err) { toast(err.message, true); }
});
document.addEventListener('keydown', e => {
  if (e.key === 'Enter' && e.target.matches('tr[data-client],tr[data-cust]')) e.target.click();
  if (e.key === '/' && !e.target.matches('input,textarea,select')) { const s = $('#client-q') || $('#cust-q'); if (s) { e.preventDefault(); s.focus(); } }
});
document.addEventListener('input', e => {
  const el = e.target;
  const keep = sel => { const pos = el.selectionStart; const n = $(sel); n.focus(); n.setSelectionRange(pos, pos); };
  if (el.id === 'client-q') { ui.clients.q = el.value; renderClients(); keep('#client-q'); }
  if (el.id === 'cust-q') { ui.customers.q = el.value; ui.customers.page = 0; renderCustomers(); keep('#cust-q'); }
  if (el.dataset.find) {
    const id = el.dataset.find; ui.callFind[id] = el.value; const pos = el.selectionStart;
    renderCalls();
    const n = document.querySelector(`[data-find="${CSS.escape(id)}"]`); if (n) { n.focus(); n.setSelectionRange(pos, pos); }
  }
});
const tip = document.createElement('div'); tip.className = 'tip'; tip.hidden = true; document.body.append(tip);
document.addEventListener('mousemove', e => {
  const g = e.target.closest && e.target.closest('g.hit');
  if (!g) { tip.hidden = true; return; }
  const n = Number(g.dataset.n);
  tip.innerHTML = `<b>${n} ${n === 1 ? 'sale' : 'sales'}</b>${esc(g.dataset.tip)}`;
  tip.hidden = false;
  tip.style.left = Math.min(window.innerWidth - tip.offsetWidth - 8, e.clientX + 12) + 'px';
  tip.style.top = (e.clientY - tip.offsetHeight - 12) + 'px';
});

if (!location.hash) history.replaceState(null, '', '#clients');
go();
