/* Flow Bench interface. Talks only to the local server it was served from. */
'use strict';
const $ = s => document.querySelector(s), $$ = s => [...document.querySelectorAll(s)];
const ic = n => `<svg class="i"><use href="#i-${n}"/></svg>`;
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const fmt = n => n == null ? '–' : Math.round(n).toLocaleString('en-US');
const pct = (v, d = 1) => v == null ? 'n/a' : v.toFixed(d) + '%';
const DPR = () => Math.min(2, window.devicePixelRatio || 1);
const SANS = '-apple-system,BlinkMacSystemFont,"SF Pro Text","Helvetica Neue",Arial,sans-serif', MONO = 'ui-monospace,"SF Mono",Menlo,Consolas,monospace';
const TABS = ['Home', 'Data', 'Gating', 'Insert', 'Compensation', 'Statistics', 'Export'];
const VIEW = { Home: 'layout', Gating: 'layout', Insert: 'layout', Data: 'data', Compensation: 'comp', Statistics: 'stats', Export: 'export' };
const GROUP_COLOURS = ['#3b82f6', '#f59e0b', '#10b981', '#ef4444', '#8b5cf6', '#06b6d4', '#ec4899', '#84cc16', '#f97316', '#64748b'];
const CM = {
  light: [[0, 48, 60, 160], [.25, 40, 170, 240], [.5, 90, 220, 120], [.75, 250, 200, 50], [1, 210, 50, 30]],
  dark: [[0, 20, 40, 170], [.25, 0, 160, 235], [.5, 40, 190, 90], [.75, 250, 225, 40], [1, 225, 35, 30]]
};
let S = null, R = null, TAB = 'Home', TOOL = 'select', SCOPE = 'all';
let SEL = { gate: null, obj: null }, STATSEL = null, COMPSEL = null, COMPFILE = null, PRISM_STALE = false, CHECKS = [];
const PTS = {}, PLOTS = {};
let draft = null, drag = null;

/* ---------------------------------------------------------------- helpers */
async function api(path, body) {
  const r = await fetch(path, body ? { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) } : undefined);
  let j; try { j = await r.json(); } catch (e) { j = { error: 'The app did not answer. It may have quit: open it again.' }; }
  if (!r.ok || j.error) throw new Error(j.error || ('Error ' + r.status));
  return j;
}
let toastTimer;
function toast(msg, err) { const t = $('#toast'); t.textContent = msg; t.className = 'toast' + (err ? ' err' : ''); t.hidden = false; clearTimeout(toastTimer); toastTimer = setTimeout(() => t.hidden = true, err ? 7000 : 3200); }
function busy(on, text) { $('#busyText').textContent = text || 'Working…'; $('#busy').hidden = !on; }
async function act(fn, text) { try { if (text) busy(true, text); return await fn(); } catch (e) { toast(e.message, true); } finally { if (text) busy(false); } }
function ask(title, text, value, okLabel) {
  return new Promise(res => {
    const d = $('#ask'), inp = $('#askInput');
    $('#askTitle').textContent = title; $('#askText').textContent = text || ''; $('#askText').hidden = !text;
    inp.hidden = value == null; inp.value = value ?? ''; $('#askOk').textContent = okLabel || 'OK';
    d.onclose = () => res(d.returnValue === 'ok' ? (value == null ? true : inp.value.trim()) : null);
    d.showModal(); if (value != null) { inp.focus(); inp.select(); }
  });
}
function u16(b) { const s = atob(b), a = new Uint8Array(s.length); for (let i = 0; i < s.length; i++) a[i] = s.charCodeAt(i); return new Uint16Array(a.buffer); }
function theme() { return document.documentElement.getAttribute('data-theme') === 'dark' ? 'dark' : 'light'; }
function cssv(n) { return getComputedStyle(document.documentElement).getPropertyValue(n).trim(); }
function ramp(t) { const s = CM[theme()]; for (let i = 1; i < s.length; i++) if (t <= s[i][0]) { const a = s[i - 1], b = s[i], k = (t - a[0]) / (b[0] - a[0]); return `rgb(${a[1] + (b[1] - a[1]) * k | 0},${a[2] + (b[2] - a[2]) * k | 0},${a[3] + (b[3] - a[3]) * k | 0})`; } const l = s[s.length - 1]; return `rgb(${l[1]},${l[2]},${l[3]})`; }
function fit(cv) { const w = cv.clientWidth, h = cv.clientHeight, d = DPR(); if (cv.width !== Math.round(w * d) || cv.height !== Math.round(h * d)) { cv.width = Math.round(w * d); cv.height = Math.round(h * d); } const c = cv.getContext('2d'); c.setTransform(d, 0, 0, d, 0, 0); c.clearRect(0, 0, w, h); return [c, w, h]; }
function menu(anchor, html, onPick) {
  const m = $('#menu'); m.innerHTML = html; m.hidden = false;
  const r = anchor.getBoundingClientRect(); m.style.left = Math.min(r.left, innerWidth - m.offsetWidth - 8) + 'px';
  const top = r.bottom + 4; m.style.top = (top + m.offsetHeight > innerHeight - 8 ? Math.max(8, r.top - m.offsetHeight - 4) : top) + 'px';
  m.onclick = e => { const b = e.target.closest('button'); if (b) { m.hidden = true; onPick(b.dataset); } };
}
document.addEventListener('pointerdown', e => { if (!e.target.closest('#menu') && !e.target.closest('.axb') && !e.target.closest('[data-act=examples]')) $('#menu').hidden = true; }, true);
async function download(url, text) {
  await act(async () => {
    const r = await fetch(url); if (!r.ok) throw new Error(await r.text());
    const name = decodeURIComponent((r.headers.get('Content-Disposition') || '').split("UTF-8''")[1] || 'download');
    const a = document.createElement('a'); a.href = URL.createObjectURL(await r.blob()); a.download = name; document.body.appendChild(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(a.href), 4000);
    toast('Saved ' + name + ' to your Downloads folder');
  }, text);
}
const cur = () => S && S.samples.find(s => s.id === S.current);
const gateById = id => S.gates.find(g => g.id === id);
const chan = n => S.channels.find(c => c.name === n);

/* ---------------------------------------------------------------- appearance */
window.setTheme = t => { document.documentElement.setAttribute('data-theme', t); try { localStorage.setItem('flowbench-theme', t); } catch (e) { } syncPill(); for (const k in PLOTS) PLOTS[k].layerKey = ''; redrawAll(); };
function syncPill() { $$('#themePill button').forEach(b => { b.classList.toggle('on', b.dataset.t === theme()); b.setAttribute('aria-pressed', b.dataset.t === theme()); }); }
$('#themePill').onclick = e => { const b = e.target.closest('button'); if (b) window.setTheme(b.dataset.t); };

/* ---------------------------------------------------------------- shell */
function renderShell() {
  const has = S.samples.length > 0;
  $('#tabs').innerHTML = TABS.map(t => `<button class="tab${t === TAB ? ' on' : ''}" data-tab="${t}">${t}</button>`).join('');
  $('#docname').textContent = S.project.name ? S.project.name.replace(/\.flowbench$/, '') + (S.project.dirty ? ' · edited' : '') : (S.example ? S.example.title : (has ? 'Untitled analysis' : ''));
  renderRibbon();
  const v = has ? VIEW[TAB] : (VIEW[TAB] === 'layout' ? 'start' : VIEW[TAB]);
  $$('.view').forEach(e => e.classList.toggle('on', e.id === 'v-' + v));
  const c = cur();
  $('#stFile').textContent = c ? c.name : 'No files yet';
  $('#stEvents').textContent = c ? fmt(c.n) + ' events' : '';
  $('#stCyt').textContent = c ? c.cyt : '';
  $('#stCredit').innerHTML = S.example ? `Example: ${esc(S.example.citation)}, <a href="https://doi.org/${esc(S.example.doi)}" target="_blank" rel="noopener">doi:${esc(S.example.doi)}</a>, ${esc(S.example.licence)}` : '';
  $('#stVer').textContent = 'Flow Bench ' + S.version;
  if (!has) renderStart();
}
$('#tabs').onclick = e => { const b = e.target.closest('[data-tab]'); if (b) { TAB = b.dataset.tab; cancelDraft(); refresh(); } };

function rb(icon, label, a, cls, off) { return `<button class="rb ${cls || ''}" data-act="${a}"${off ? ' disabled' : ''} title="${esc(label)}">${ic(icon)}${esc(label)}</button>`; }
function grp(cap, inner) { return `<div class="rg"><div class="row">${inner}</div><div class="cap">${cap}</div></div>`; }
function fileNav() {
  if (!S.samples.length) return '';
  const opts = [], groups = {}; S.samples.forEach(s => { const k = s.role === 'sample' ? (s.group || 'No group') : 'Controls'; (groups[k] = groups[k] || []).push(s); });
  for (const k in groups) opts.push(`<optgroup label="${esc(k)}">${groups[k].map(s => `<option value="${s.id}"${s.id === S.current ? ' selected' : ''}>${esc(s.name)}</option>`).join('')}</optgroup>`);
  return `<div class="rg"><div class="fnav"><select id="fileSel" aria-label="Current file">${opts.join('')}</select><div class="two"><button data-act="prev" title="Previous file (Page Up)">${ic('prev')}Previous</button><button data-act="next" title="Next file (Page Down)">Next${ic('next')}</button></div></div><div class="cap">Current file</div></div>`;
}
function renderRibbon() {
  const has = S.samples.length > 0, g = SEL.gate && gateById(SEL.gate), o = SEL.obj && S.objects.find(q => q.id === SEL.obj);
  let h = '';
  if (TAB === 'Home') h = grp('Files', rb('file', 'Add files', 'addFiles') + rb('folder', 'Add folder', 'addFolder') + rb('book', 'Examples', 'examples'))
    + grp('Project', rb('new', 'New', 'newProject') + rb('open', 'Open', 'openProject') + rb('save', 'Save', 'saveProject', '', !has)) + fileNav();
  if (TAB === 'Gating') h = fileNav()
    + grp('Create gate', rb('select', 'Select', 'tool:select', TOOL === 'select' ? 'on' : '') + rb('poly', 'Polygon', 'tool:poly', TOOL === 'poly' ? 'on' : '') + rb('rect', 'Rectangle', 'tool:rect', TOOL === 'rect' ? 'on' : '') + rb('range', 'Range', 'tool:range', TOOL === 'range' ? 'on' : '') + rb('quad', 'Quadrant', 'tool:quad', TOOL === 'quad' ? 'on' : ''))
    + grp('Apply changes to', rb('one', 'This file', 'scope:file', SCOPE === 'file' ? 'on' : '') + rb('grp', 'Group', 'scope:group', SCOPE === 'group' ? 'on' : '') + rb('all', 'All files', 'scope:all', SCOPE === 'all' ? 'on' : ''))
    + grp('Selected gate', rb('edit', 'Rename', 'renameGate', '', !g) + rb('dens', 'Open plot', 'plotGate', '', !g) + rb('undo', 'Use shared', 'resetGate', '', !(g && R && R.tree.find(t => t.id === g.id && t.own))) + rb('trash', 'Delete', 'deleteGate', '', !g));
  if (TAB === 'Insert') h = grp('Plots', rb('dens', 'Density plot', 'addDensity', '', !has) + rb('hist', 'Histogram', 'addHist', '', !has))
    + grp('Tables and charts', rb('tab', 'Statistics', 'addStats', '', !has) + rb('chart', 'Chart', 'addChart', '', !has))
    + grp('Selected object', rb('left', 'Earlier', 'objLeft', '', !o) + rb('right', 'Later', 'objRight', '', !o) + rb('trash', 'Remove', 'objRemove', '', !o));
  if (TAB === 'Data') h = grp('Files', rb('file', 'Add files', 'addFiles') + rb('folder', 'Add folder', 'addFolder')) + grp('Roles and groups', rb('wand', 'Guess from names', 'guess', '', !has));
  if (TAB === 'Compensation') h = grp('Use', rb('file', 'Matrix in each file', 'comp:file', S.comp.mode === 'file' ? 'on' : '', !has) + rb('tab', 'Project matrix', 'comp:matrix', S.comp.mode === 'matrix' ? 'on' : '', !has) + rb('none', 'None', 'comp:none', S.comp.mode === 'none' ? 'on' : '', !has))
    + grp('Project matrix', rb('calc', 'From controls', 'compCompute', '', !has) + rb('copy', 'Copy file matrix', 'compCopy', '', !has));
  if (TAB === 'Statistics') h = grp('Export', rb('prism', S.prism.path ? 'Update Prism' : 'New Prism file', S.prism.path ? 'prismUpdate' : 'prismNew', 'go', !has) + rb('xls', 'Excel', 'xlsx', '', !has));
  if (TAB === 'Export') h = grp('GraphPad Prism', rb('prism', 'New file', 'prismNew', '', !has) + rb('link', 'Link existing', 'prismLink', '', !has) + rb('prism', 'Update', 'prismUpdate', 'go', !S.prism.path) + rb('ext', 'Open in Prism', 'prismOpen', '', !S.prism.path))
    + grp('Report and tables', rb('pdf', 'PDF report', 'pdf', '', !has) + rb('xls', 'Excel', 'xlsx', '', !has) + rb('gml', 'Gating-ML', 'gml', '', !has));
  $('#ribbon').innerHTML = h;
  const fs = $('#fileSel'); if (fs) fs.onchange = () => setCurrent(fs.value);
}
$('#ribbon').onclick = e => { const b = e.target.closest('[data-act]'); if (b && !b.disabled) doAct(b.dataset.act, b); };

async function setCurrent(id) { cancelDraft(); await act(async () => { await api('/api/sample', { op: 'current', id }); await refresh(); }); }
function step(d) { const list = S.samples, i = list.findIndex(s => s.id === S.current); if (i < 0) return; setCurrent(list[(i + d + list.length) % list.length].id); }
function changed() { PRISM_STALE = !!(S && S.prism.path); }

async function doAct(a, el) {
  if (a.startsWith('tool:')) { TOOL = a.slice(5); cancelDraft(); renderRibbon(); cursors(); return; }
  if (a.startsWith('scope:')) { SCOPE = a.slice(6); renderRibbon(); return; }
  if (a.startsWith('comp:')) return act(async () => { await api('/api/comp', { op: 'mode', mode: a.slice(5) }); changed(); await refresh(); });
  const A = {
    addFiles: () => act(async () => { const r = await api('/api/files', { op: 'files' }); if (!r.cancelled) { added(r); await refresh(); } }),
    addFolder: () => act(async () => { const r = await api('/api/files', { op: 'folder' }); if (!r.cancelled) { added(r); await refresh(); } }),
    examples: () => menu(el, '<h6>Published example data</h6>' + S.examples.map(x => `<button data-key="${x.key}">${esc(x.title)}<em>${x.installed ? 'open' : x.size_mb + ' MB'}</em></button>`).join(''), d => openExample(d.key)),
    newProject: async () => { if (S.samples.length && !(await ask('Start a new analysis?', 'The files stay where they are. Gates and settings that are not saved are lost.', null, 'Start new'))) return; await act(async () => { await api('/api/project', { op: 'new' }); reset(); await refresh(); }); },
    openProject: () => act(async () => { const r = await api('/api/project', { op: 'open' }); if (r.cancelled) return; reset(); if (r.missing && r.missing.length) toast(r.missing.length + ' file(s) were not found where they used to be.', true); await refresh(); }),
    saveProject: () => act(async () => { const r = await api('/api/project', { op: 'save' }); if (!r.cancelled) { toast('Project saved'); await refresh(); } }),
    prev: () => step(-1), next: () => step(1),
    renameGate: async () => { const g = gateById(SEL.gate); const n = await ask('Rename gate', '', g.name); if (n) await act(async () => { await api('/api/gate', { op: 'rename', id: g.id, name: n }); changed(); await refresh(); }); },
    deleteGate: () => deleteGate(),
    resetGate: () => act(async () => { await api('/api/gate', { op: 'reset', id: SEL.gate, sid: S.current }); changed(); await refresh(); }),
    plotGate: () => act(async () => { const r = await api('/api/object', { op: 'add', type: 'density', pop: SEL.gate }); SEL.obj = r.id; await refresh(); scrollToObj(r.id); }),
    addDensity: () => addObj('density'), addHist: () => addObj('hist'), addStats: () => addObj('stats'), addChart: () => addObj('chart'),
    objLeft: () => act(async () => { await api('/api/object', { op: 'move', id: SEL.obj, delta: -1 }); await refresh(); }),
    objRight: () => act(async () => { await api('/api/object', { op: 'move', id: SEL.obj, delta: 1 }); await refresh(); }),
    objRemove: () => act(async () => { await api('/api/object', { op: 'delete', id: SEL.obj }); SEL.obj = null; await refresh(); }),
    guess: async () => { if (await ask('Guess roles and groups again?', 'This replaces the roles, stained channels and groups you have set with guesses from the file names.', null, 'Guess')) await act(async () => { await api('/api/sample', { op: 'guess' }); changed(); await refresh(); }); },
    compCompute: () => act(async () => { const r = await api('/api/comp', { op: 'compute' }); changed(); toast('Matrix computed from ' + Object.keys(r.used).length + ' control(s)' + (r.missing.length ? '; no control for ' + r.missing.join(', ') : '')); await refresh(); }, 'Computing from controls…'),
    compCopy: () => act(async () => { await api('/api/comp', { op: 'copy' }); changed(); await refresh(); }),
    prismNew: () => act(async () => { const r = await api('/api/prism', { op: 'new' }); if (!r.cancelled) { PRISM_STALE = false; toast(r.written + ' table(s) written to ' + r.path.split('/').pop()); await refresh(); } }),
    prismLink: () => act(async () => { const r = await api('/api/prism', { op: 'link' }); if (!r.cancelled) { PRISM_STALE = false; prismToast(r); await refresh(); } }),
    prismUpdate: () => act(async () => { const r = await api('/api/prism', { op: 'update' }); PRISM_STALE = false; prismToast(r); await refresh(); }),
    prismOpen: () => act(() => api('/api/prism', { op: 'open' })),
    prismUnlink: () => act(async () => { await api('/api/prism', { op: 'unlink' }); await refresh(); }),
    pdf: () => download('/api/export/pdf', 'Building the report…'), xlsx: () => download('/api/export/xlsx'), gml: () => download('/api/export/gml'),
    saveAs: () => act(async () => { const r = await api('/api/project', { op: 'save', as: true }); if (!r.cancelled) { toast('Project saved'); await refresh(); } }),
  };
  if (A[a]) return A[a]();
}
function prismToast(r) { toast(`Prism file updated: ${r.updated.length} table(s) refreshed` + (r.added.length ? `, ${r.added.length} added` : '') + `. Backup: ${r.backup}`); }
function added(r) { if (r.skipped && r.skipped.length) toast(r.skipped.length + ' file(s) could not be read: ' + r.skipped[0], true); else if (r.added != null) toast(r.added ? r.added + ' file(s) added' : 'Those files are already in the project'); }
function reset() { SEL = { gate: null, obj: null }; STATSEL = null; COMPSEL = null; COMPFILE = null; PRISM_STALE = false; for (const k in PTS) delete PTS[k]; }
async function addObj(type) {
  await act(async () => {
    const o = SEL.obj && S.objects.find(q => q.id === SEL.obj); const pop = SEL.gate || (o && o.pop) || null;
    const body = { op: 'add', type, pop }; if (type === 'chart' && STATSEL) body.stat = { key: STATSEL };
    const r = await api('/api/object', body); SEL.obj = r.id; await refresh(); scrollToObj(r.id);
  });
}
function scrollToObj(id) { const e = document.querySelector(`.obj[data-id="${id}"]`); if (e) e.scrollIntoView({ block: 'nearest', behavior: 'smooth' }); }
async function deleteGate() {
  const g = gateById(SEL.gate); if (!g) return;
  const kids = S.gates.filter(q => q.parent === g.id).length;
  const what = g.quad ? 'This removes all four quadrants' : 'This removes “' + g.name + '”';
  if (!(await ask('Delete gate?', what + (kids ? ', the gates inside it,' : '') + ' and any plots that show it, in every file.', null, 'Delete'))) return;
  await act(async () => { await api('/api/gate', { op: 'delete', id: g.id }); SEL.gate = null; changed(); await refresh(); });
}

/* ---------------------------------------------------------------- loading */
async function refresh() {
  S = await api('/api/state');
  if (SEL.gate && !gateById(SEL.gate)) SEL.gate = null;
  if (SEL.obj && !S.objects.find(o => o.id === SEL.obj)) SEL.obj = null;
  renderShell();
  if (!S.samples.length) return;
  const v = VIEW[TAB];
  if (v === 'layout') await loadRender();
  if (v === 'data') renderData();
  if (v === 'comp') await renderComp();
  if (v === 'stats') await renderStats();
  if (v === 'export') renderExport();
}
async function loadRender() {
  const have = Object.entries(PTS).map(([id, p]) => id + ':' + p.key).join(',');
  R = await api('/api/render?sid=' + encodeURIComponent(S.current) + '&have=' + encodeURIComponent(have));
  const ids = new Set(R.objects.map(o => o.id));
  for (const k in PTS) if (!ids.has(k)) delete PTS[k];
  for (const o of R.objects) {
    if (o.px) PTS[o.id] = { key: o.key, x: u16(o.px), y: u16(o.py), shown: o.shown };
    else if (o.hist) PTS[o.id] = { key: o.key, hist: o.hist, control: o.control };
  }
  renderLayout();
  api('/api/checks').then(r => { CHECKS = r.checks; renderChecks(); }).catch(() => { });
}

/* ---------------------------------------------------------------- layout view */
function renderLayout() {
  // data list
  const groups = {}; const order = [];
  S.samples.forEach(s => { const k = s.role === 'sample' ? (s.group || 'No group') : 'Controls'; if (!groups[k]) { groups[k] = []; order.push(k); } groups[k].push(s); });
  order.sort((a, b) => (a === 'Controls') - (b === 'Controls') || (a === 'No group') - (b === 'No group') || (S.groups.indexOf(a) - S.groups.indexOf(b)));
  $('#dlCount').textContent = S.samples.length + ' files';
  $('#dataList').innerHTML = order.map(k => `<div class="gh" title="${esc(k)}">${esc(k)}</div>` + groups[k].map(s => {
    const gi = S.groups.indexOf(s.group); const col = s.role !== 'sample' ? 'var(--dim)' : (gi >= 0 ? GROUP_COLOURS[gi % GROUP_COLOURS.length] : 'var(--line2)');
    return `<button class="fr${s.id === S.current ? ' on' : ''}" data-sid="${s.id}" title="${esc(s.name)}"><i style="background:${col}"></i><span>${esc(s.name.replace(/\.fcs$/i, '').replace(/^Specimen_\d+_/, ''))}</span><em>${fmt(s.n)}</em></button>`;
  }).join('')).join('');
  // gate view
  $('#gateView').innerHTML = R.tree.map(q => `<button class="gt${q.id === SEL.gate && q.id ? ' on' : ''}" data-gid="${q.id || ''}" style="padding-left:${7 + q.depth * 11}px" title="Double-click to open a plot of these events"><span class="n">${ic('gate')}<span>${esc(q.name)}</span>${q.own ? '<span class="own" title="This file has its own version of this gate">own</span>' : ''}</span><em>${fmt(q.count)}</em><b>${q.id ? pct(q.pct_parent) : '100%'}</b><u style="width:${Math.max(1.5, q.pct_total || 0)}%"></u></button>`).join('');
  // page
  const c = cur();
  $('#pgTitle').textContent = c.name.replace(/\.fcs$/i, '');
  $('#pgMeta').textContent = [c.role === 'sample' ? (c.group || 'no group') : c.role + ' control', fmt(R.n) + ' events', c.cyt, c.date].filter(Boolean).join('  ·  ');
  $('#pgNote').textContent = S.comp.mode === 'none' ? 'not compensated' : (S.comp.mode === 'file' ? 'compensated with the file’s matrix' : 'compensated with the project matrix');
  const host = $('#objs'); host.innerHTML = '';
  for (const k in PLOTS) delete PLOTS[k];
  const popOpts = sel => `<option value=""${!sel ? ' selected' : ''}>All events</option>` + S.gates.map(g => `<option value="${g.id}"${g.id === sel ? ' selected' : ''}>${esc(g.name)}</option>`).join('');
  for (const o of R.objects) {
    const el = document.createElement('div'); el.className = 'obj' + (o.id === SEL.obj ? ' sel' : ''); el.dataset.id = o.id;
    const x = `<button class="x" data-rm="${o.id}" title="Remove from the page" aria-label="Remove">${ic('x')}</button>`;
    if (o.type === 'density' || o.type === 'hist') {
      el.innerHTML = `<div class="t"><select class="pop" aria-label="Events shown">${popOpts(o.pop)}</select><span>${esc(o.sub)} · ${fmt(o.n)}${PTS[o.id] && PTS[o.id].shown && PTS[o.id].shown < o.n ? ' (' + fmt(PTS[o.id].shown) + ' drawn)' : ''}</span>${x}</div>
        <div class="cw"><canvas></canvas><button class="axb x" data-ax="x">${esc(o.xl)} ▾</button>${o.type === 'density' ? `<button class="axb y" data-ax="y">${esc(o.yl)} ▾</button>` : ''}</div><s></s><s></s><s></s><s></s>`;
      host.appendChild(el);
      const cv = el.querySelector('canvas'); PLOTS[o.id] = { o, cv, layerKey: '' }; attach(o.id);
      el.querySelector('.pop').onchange = e => act(async () => { await api('/api/object', { op: 'update', id: o.id, pop: e.target.value || null }); await refresh(); });
    } else if (o.type === 'stats') {
      el.innerHTML = `<div class="t">Statistics<span>${esc(c.name)}</span>${x}</div><div class="tw"><table><tr><th>Gate</th><th>Events</th><th>% parent</th><th>% total</th></tr>${R.tree.filter(q => q.id).map(q => `<tr><td style="padding-left:${8 + (q.depth - 1) * 10}px">${esc(q.name)}</td><td>${fmt(q.count)}</td><td>${q.pct_parent == null ? 'n/a' : q.pct_parent.toFixed(1)}</td><td>${q.pct_total == null ? 'n/a' : q.pct_total.toFixed(1)}</td></tr>`).join('') || '<tr><td colspan="4" class="empty">No gates yet</td></tr>'}</table></div><s></s><s></s><s></s><s></s>`;
      host.appendChild(el);
    } else if (o.type === 'chart') {
      const ch = o.chart;
      el.innerHTML = `<div class="t"><select class="stat" aria-label="Statistic">${S.stats.map(d => `<option value="${esc(d.key)}"${ch && d.key === ch.key ? ' selected' : ''}>${esc(d.title)}</option>`).join('')}</select><span>one dot per file</span>${x}</div><div class="cw">${ch && ch.groups.length ? '<canvas></canvas>' : `<div class="empty">${S.stats.length ? 'Give the files a group on the Data tab to compare them.' : 'Draw a gate to chart it.'}</div>`}</div><s></s><s></s><s></s><s></s>`;
      host.appendChild(el);
      const st = el.querySelector('.stat'); if (st) st.onchange = e => act(async () => { await api('/api/object', { op: 'update', id: o.id, stat: { key: e.target.value } }); await refresh(); });
      const cv = el.querySelector('canvas'); if (cv) { el._chart = ch; }
    }
  }
  if (!R.objects.length) host.innerHTML = '<div class="empty" style="grid-column:1/-1">The page is empty. Use the Insert tab to add a plot.</div>';
  renderSelGate(); renderPrismCard(); renderChecks(); redrawAll(); cursors();
}
$('#dataList').onclick = e => { const b = e.target.closest('[data-sid]'); if (b && b.dataset.sid !== S.current) setCurrent(b.dataset.sid); };
$('#gateView').onclick = e => { const b = e.target.closest('[data-gid]'); if (b) selectGate(b.dataset.gid || null); };
$('#gateView').ondblclick = e => { const b = e.target.closest('[data-gid]'); if (b) act(async () => { const r = await api('/api/object', { op: 'add', type: 'density', pop: b.dataset.gid || null }); SEL.obj = r.id; await refresh(); scrollToObj(r.id); }); };
$('#objs').addEventListener('click', e => {
  const rm = e.target.closest('[data-rm]'); if (rm) return act(async () => { await api('/api/object', { op: 'delete', id: rm.dataset.rm }); await refresh(); });
  const ax = e.target.closest('.axb'); if (ax) return axisMenu(ax.closest('.obj').dataset.id, ax.dataset.ax, ax);
  const ob = e.target.closest('.obj'); if (ob && ob.dataset.id !== SEL.obj) { SEL.obj = ob.dataset.id; $$('.obj').forEach(q => q.classList.toggle('sel', q.dataset.id === SEL.obj)); renderRibbon(); }
});
function selectGate(id) { SEL.gate = id; $$('.gt').forEach(q => q.classList.toggle('on', !!id && q.dataset.gid === id)); renderSelGate(); renderRibbon(); redrawAll(true); }
function renderSelGate() {
  const q = SEL.gate && R && R.tree.find(t => t.id === SEL.gate);
  if (!q) { $('#selGate').innerHTML = '<div class="big"><div class="none">Click a gate on a plot or in the gate view. On the Gating tab, pick a shape and draw on a plot to make one.</div></div>'; return; }
  const par = R.tree.find(t => t.id === (q.parent || null));
  $('#selGate').innerHTML = `<div class="big"><div class="k">${esc(q.name)} · of ${esc(par ? par.name : 'All events')}</div><div class="v">${pct(q.pct_parent)}</div>
    <div class="r"><div>Events<b>${fmt(q.count)}</b></div><div>Of all events<b>${pct(q.pct_total)}</b></div>${q.own ? '<div style="grid-column:1/-1;color:var(--warn)">This file has its own version of this gate.</div>' : ''}</div></div>`;
}
function renderPrismCard() {
  const p = S.prism, has = S.stats.length && S.groups.length;
  $('#prismCard').innerHTML = p.path ? `<div class="prism"><div class="f"><i class="${p.exists ? '' : 'off'}"></i><span title="${esc(p.path)}">${esc(p.name)}</span></div>
      <small>${!p.exists ? 'The file is not where it was. Link it again on the Export tab.' : (PRISM_STALE ? 'Numbers have changed since the last update.' : S.stats.length + ' table(s). Graphs made in Prism redraw when it opens the file.')}</small>
      <div class="bts"><button class="btn go" data-act="prismUpdate"${p.exists ? '' : ' disabled'}>Update Prism</button><button class="btn" data-act="prismOpen"${p.exists ? '' : ' disabled'}>Open</button></div></div>`
    : `<div class="prism"><small>${has ? 'Send one table per statistic to GraphPad Prism, then keep that file up to date from here.' : 'Draw a gate and give the files a group, then send the numbers to GraphPad Prism.'}</small>
      <div class="bts"><button class="btn go" data-act="prismNew"${has ? '' : ' disabled'}>New Prism file</button><button class="btn" data-act="prismLink"${has ? '' : ' disabled'}>Link existing</button></div></div>`;
}
$('#prismCard').onclick = e => { const b = e.target.closest('[data-act]'); if (b && !b.disabled) doAct(b.dataset.act, b); };
function renderChecks() {
  const list = CHECKS.filter(c => !c.sid || c.sid === S.current), w = list.filter(c => c.level === 'warn').length;
  $('#ckCount').textContent = list.length ? (w ? w + ' to look at' : 'all clear') : '';
  $('#checks').innerHTML = list.map(c => `<div class="ck${c.level === 'warn' ? ' w' : ''}">${ic(c.level === 'warn' ? 'warn' : 'ok')}<div>${esc(c.title)}<small>${esc(c.detail)}</small></div></div>`).join('') || '<div class="ck"><div></div><div><small>Checks appear here once files are loaded.</small></div></div>';
}

/* ---------------------------------------------------------------- drawing plots */
function geomOf(cv) { const w = cv.clientWidth, h = cv.clientHeight; return { w, h, L: 54, T: 8, pw: Math.max(40, w - 54 - 12), ph: Math.max(40, h - 8 - 46) }; }
function density(P) {
  if (P.den) return P.den; const G = 80, n = P.x.length, h = new Float32Array(G * G), b = new Float32Array(G * G);
  const gi = i => Math.min(G - 1, P.y[i] * G >> 16) * G + Math.min(G - 1, P.x[i] * G >> 16);
  for (let i = 0; i < n; i++) h[gi(i)]++;
  let m = 0; for (let y = 0; y < G; y++) for (let x = 0; x < G; x++) { let v = 0, k = y * G + x; for (let dy = -1; dy <= 1; dy++) for (let dx = -1; dx <= 1; dx++) { const yy = y + dy, xx = x + dx; if (yy >= 0 && yy < G && xx >= 0 && xx < G) v += h[yy * G + xx] * (dx === 0 && dy === 0 ? 4 : (dx === 0 || dy === 0 ? 2 : 1)); } b[k] = v / 16; if (b[k] > m) m = b[k]; }
  const d = new Float32Array(n), lm = Math.log(1 + m) || 1; for (let i = 0; i < n; i++) d[i] = Math.log(1 + b[gi(i)]) / lm;
  const o = new Uint32Array(n); for (let i = 0; i < n; i++) o[i] = i; o.sort((a, c) => d[a] - d[c]);
  return P.den = { d, o };
}
function buildLayer(pl) {
  const { cv, o } = pl, f = geomOf(cv), dpr = DPR(), P = PTS[o.id];
  const L = document.createElement('canvas'); L.width = Math.max(1, Math.round(f.w * dpr)); L.height = Math.max(1, Math.round(f.h * dpr));
  const c = L.getContext('2d'); c.setTransform(dpr, 0, 0, dpr, 0, 0);
  const ink = cssv('--plot-ink'), line = cssv('--plot-line');
  c.fillStyle = cssv('--plot-bg'); c.fillRect(f.L, f.T, f.pw, f.ph);
  if (o.missing || !P) { c.fillStyle = ink; c.font = '12px ' + SANS; c.textAlign = 'center'; c.textBaseline = 'middle'; c.fillText(o.missing ? 'This channel is not in this file' : 'No events', f.L + f.pw / 2, f.T + f.ph / 2); }
  else if (o.type === 'density') {
    const dn = density(P), n = P.x.length, sz = n > 20000 ? 1.4 : (n > 4000 ? 1.8 : 2.4);
    c.save(); c.beginPath(); c.rect(f.L, f.T, f.pw, f.ph); c.clip();
    let last = ''; for (let k = 0; k < n; k++) { const i = dn.o[k], col = ramp(dn.d[i]); if (col !== last) { c.fillStyle = col; last = col; } c.fillRect(f.L + P.x[i] / 65535 * f.pw - sz / 2, f.T + (1 - P.y[i] / 65535) * f.ph - sz / 2, sz, sz); }
    c.restore();
  } else {
    const draw = (h, fill) => { const N = h.length, sm = h.map((v, i) => ((h[i - 1] ?? v) + 2 * v + (h[i + 1] ?? v)) / 4), m = Math.max(1, ...sm); c.beginPath(); c.moveTo(f.L, f.T + f.ph); sm.forEach((v, i) => c.lineTo(f.L + (i + .5) / N * f.pw, f.T + f.ph - v / m * (f.ph - 26))); c.lineTo(f.L + f.pw, f.T + f.ph); if (fill) { c.closePath(); c.fillStyle = ramp(.3); c.globalAlpha = .28; c.fill(); c.globalAlpha = 1; c.strokeStyle = ramp(.12); c.lineWidth = 1.4; c.setLineDash([]); c.stroke(); } else { c.strokeStyle = line; c.lineWidth = 1.2; c.setLineDash([4, 3]); c.stroke(); c.setLineDash([]); } };
    c.save(); c.beginPath(); c.rect(f.L, f.T, f.pw, f.ph); c.clip();
    draw(P.hist, true); if (P.control) draw(P.control.hist, false);
    c.restore();
    if (P.control) { c.font = '10.5px ' + SANS; c.fillStyle = ink; c.textAlign = 'right'; c.textBaseline = 'top'; c.fillText('dashed: ' + P.control.role + ' control', f.L + f.pw - 6, f.T + 4); }
  }
  c.strokeStyle = line; c.lineWidth = 1; c.strokeRect(f.L + .5, f.T + .5, f.pw - 1, f.ph - 1);
  c.font = '10px ' + MONO; c.fillStyle = ink;
  (o.xt || []).forEach(t => { if (t[0] < 0 || t[0] > 1) return; const x = Math.round(f.L + t[0] * f.pw) + .5; c.beginPath(); c.moveTo(x, f.T + f.ph); c.lineTo(x, f.T + f.ph + (t[1] ? 5 : 3)); c.stroke(); if (t[1]) { c.textAlign = 'center'; c.textBaseline = 'top'; c.fillText(t[1], x, f.T + f.ph + 7); } });
  (o.yt || []).forEach(t => { if (t[0] < 0 || t[0] > 1) return; const y = Math.round(f.T + (1 - t[0]) * f.ph) + .5; c.beginPath(); c.moveTo(f.L, y); c.lineTo(f.L - (t[1] ? 5 : 3), y); c.stroke(); if (t[1]) { c.textAlign = 'right'; c.textBaseline = 'middle'; c.fillText(t[1], f.L - 7, y); } });
  pl.layer = L; pl.f = f;
}
const X = (f, d) => f.L + d * f.pw, Y = (f, d) => f.T + (1 - d) * f.ph;
function tag(c, f, text, x, y, col, align) {
  c.font = '600 11px ' + SANS; const w = c.measureText(text).width + 10; let x0 = align === 'left' ? x : (align === 'right' ? x - w : x - w / 2);
  x0 = Math.max(f.L + 2, Math.min(f.L + f.pw - w - 2, x0)); const y0 = Math.max(f.T + 2, Math.min(f.T + f.ph - 18, y));
  c.fillStyle = cssv('--plot-bg'); c.globalAlpha = .88; c.fillRect(x0, y0, w, 16); c.globalAlpha = 1; c.fillStyle = col; c.textAlign = 'left'; c.textBaseline = 'middle'; c.fillText(text, x0 + 5, y0 + 8.5);
}
function handle(c, x, y, col) { c.fillStyle = cssv('--plot-bg'); c.strokeStyle = col; c.lineWidth = 1.5; c.fillRect(x - 3.5, y - 3.5, 7, 7); c.strokeRect(x - 3.5, y - 3.5, 7, 7); }
function isSel(g) { return g.id === SEL.gate || (g.parts && g.parts.some(p => p.id === SEL.gate)); }
function drawPlot(id, onlyGates) {
  const pl = PLOTS[id]; if (!pl || !pl.cv.isConnected) return;
  const key = (pl.o.key || '') + '|' + pl.cv.clientWidth + 'x' + pl.cv.clientHeight + '|' + theme();
  if (pl.layerKey !== key) { buildLayer(pl); pl.layerKey = key; }
  const [c] = fit(pl.cv), f = pl.f, o = pl.o;
  c.drawImage(pl.layer, 0, 0, f.w, f.h);
  if (o.missing) return;
  c.save(); c.beginPath(); c.rect(f.L - 1, f.T - 1, f.pw + 2, f.ph + 2); c.clip();
  for (const g of (o.gates || [])) {
    const sel = isSel(g), col = sel ? cssv('--gate-sel') : cssv('--gate'); c.strokeStyle = col; c.lineWidth = sel ? 2 : 1.4; c.setLineDash(g.own ? [5, 3] : []);
    const lab = g.name ? g.name + '  ' + pct(g.pct) : '';
    if (g.type === 'poly') {
      c.beginPath(); g.pts.forEach((p, i) => i ? c.lineTo(X(f, p[0]), Y(f, p[1])) : c.moveTo(X(f, p[0]), Y(f, p[1]))); c.closePath(); if (sel) { c.fillStyle = col; c.globalAlpha = .07; c.fill(); c.globalAlpha = 1; } c.stroke(); c.setLineDash([]);
      const top = g.pts.reduce((a, b) => b[1] > a[1] ? b : a), cx = g.pts.reduce((a, b) => a + b[0], 0) / g.pts.length;
      tag(c, f, lab, X(f, cx), Y(f, top[1]) - 19, col);
      if (sel) g.pts.forEach(p => handle(c, X(f, p[0]), Y(f, p[1]), col));
    } else if (g.type === 'rect') {
      const x0 = X(f, g.x0), x1 = X(f, g.x1), y0 = Y(f, g.y0), y1 = Y(f, g.y1); if (sel) { c.fillStyle = col; c.globalAlpha = .07; c.fillRect(x0, y1, x1 - x0, y0 - y1); c.globalAlpha = 1; }
      c.strokeRect(x0, y1, x1 - x0, y0 - y1); c.setLineDash([]); tag(c, f, lab, (x0 + x1) / 2, Math.min(y0, y1) - 19, col);
      if (sel) [[x0, y0], [x1, y0], [x1, y1], [x0, y1]].forEach(p => handle(c, p[0], p[1], col));
    } else if (g.type === 'range') {
      const x0 = X(f, g.x0), x1 = X(f, g.x1);
      if (o.type === 'hist') { const yy = f.T + 15; c.beginPath(); c.moveTo(x0, yy - 6); c.lineTo(x0, f.T + f.ph); c.moveTo(x1, yy - 6); c.lineTo(x1, f.T + f.ph); c.globalAlpha = .35; c.stroke(); c.globalAlpha = 1; c.beginPath(); c.moveTo(x0, yy); c.lineTo(x1, yy); c.stroke(); c.setLineDash([]); tag(c, f, lab, (x0 + x1) / 2, yy + 5, col); if (sel) { handle(c, x0, yy, col); handle(c, x1, yy, col); } }
      else { c.beginPath(); c.moveTo(x0, f.T); c.lineTo(x0, f.T + f.ph); c.moveTo(x1, f.T); c.lineTo(x1, f.T + f.ph); c.stroke(); c.setLineDash([]); tag(c, f, lab, (x0 + x1) / 2, f.T + 4, col); }
    } else if (g.type === 'quad') {
      const cx = X(f, g.cx), cy = Y(f, g.cy); c.beginPath(); c.moveTo(cx, f.T); c.lineTo(cx, f.T + f.ph); c.moveTo(f.L, cy); c.lineTo(f.L + f.pw, cy); c.stroke(); c.setLineDash([]);
      const at = { UL: [f.L + 4, f.T + 4, 'left'], UR: [f.L + f.pw - 4, f.T + 4, 'right'], LL: [f.L + 4, f.T + f.ph - 20, 'left'], LR: [f.L + f.pw - 4, f.T + f.ph - 20, 'right'] };
      g.parts.forEach(p => tag(c, f, pct(p.pct), at[p.q][0], at[p.q][1], p.id === SEL.gate ? cssv('--gate-sel') : cssv('--gate'), at[p.q][2]));
      if (sel) handle(c, cx, cy, col);
    }
  }
  if (draft && draft.id === id) {
    const col = cssv('--gate-sel'); c.strokeStyle = col; c.lineWidth = 1.6; c.setLineDash([4, 3]);
    if (draft.type === 'poly') { c.beginPath(); draft.pts.forEach((p, i) => i ? c.lineTo(X(f, p[0]), Y(f, p[1])) : c.moveTo(X(f, p[0]), Y(f, p[1]))); if (draft.cursor) c.lineTo(X(f, draft.cursor[0]), Y(f, draft.cursor[1])); c.stroke(); c.setLineDash([]); draft.pts.forEach(p => handle(c, X(f, p[0]), Y(f, p[1]), col)); }
    else if (draft.type === 'rect') { c.strokeRect(X(f, draft.a[0]), Y(f, draft.a[1]), X(f, draft.b[0]) - X(f, draft.a[0]), Y(f, draft.b[1]) - Y(f, draft.a[1])); }
    else if (draft.type === 'range') { c.beginPath(); c.moveTo(X(f, draft.a[0]), f.T); c.lineTo(X(f, draft.a[0]), f.T + f.ph); c.moveTo(X(f, draft.b[0]), f.T); c.lineTo(X(f, draft.b[0]), f.T + f.ph); c.stroke(); }
    c.setLineDash([]);
  }
  c.restore();
}
function redrawAll() {
  requestAnimationFrame(() => {
    for (const id in PLOTS) drawPlot(id);
    $$('.obj').forEach(el => { if (el._chart) { const cv = el.querySelector('canvas'); if (cv) drawChart(cv, el._chart); } });
  });
}
function cursors() { for (const id in PLOTS) PLOTS[id].cv.style.cursor = TOOL === 'select' ? 'default' : 'crosshair'; }
new ResizeObserver(() => redrawAll()).observe($('#objs'));

function drawChart(cv, ch) {
  const [c, w, h] = fit(cv), many = ch.groups.length > 3 || ch.groups.some(g => g.name.length > 14), L = 50, R2 = 12, T = 12, B = many ? 104 : 34, pw = w - L - R2, ph = h - T - B;
  const all = ch.groups.flatMap(g => g.values.map(v => v.v)); if (!all.length) return;
  let mx = Math.max(...all); const step0 = Math.pow(10, Math.floor(Math.log10(mx || 1))); const step = [1, 2, 5, 10].map(m => m * step0).find(s => mx / s <= 5) || step0 * 10; mx = Math.max(step, Math.ceil(mx / step) * step); if (ch.unit === '%' && mx > 100) mx = 100;
  const ink = '#39424c', line = '#8f98a2', acc = theme() === 'dark' ? '#d9590a' : '#0071e3';
  c.strokeStyle = line; c.lineWidth = 1; c.beginPath(); c.moveTo(L + .5, T); c.lineTo(L + .5, T + ph + .5); c.lineTo(L + pw, T + ph + .5); c.stroke();
  c.font = '10px ' + MONO; c.fillStyle = ink; for (let v = 0; v <= mx + 1e-9; v += step) { const y = T + ph - v / mx * ph; c.beginPath(); c.moveTo(L, y); c.lineTo(L - 4, y); c.stroke(); c.textAlign = 'right'; c.textBaseline = 'middle'; c.fillText(v >= 1000 ? (v / 1000) + 'K' : +v.toFixed(2), L - 7, y); }
  const slot = pw / ch.groups.length, bw = Math.min(46, slot * .6);
  ch.groups.forEach((g, i) => {
    const cx = L + (i + .5) * slot, vals = g.values.map(v => v.v);
    if (vals.length) { const mean = vals.reduce((a, b) => a + b, 0) / vals.length, bh = mean / mx * ph; c.fillStyle = acc; c.globalAlpha = .14; c.fillRect(cx - bw / 2, T + ph - bh, bw, bh); c.globalAlpha = 1; c.strokeStyle = '#14171a'; c.lineWidth = 1.5; c.beginPath(); c.moveTo(cx - bw / 2, T + ph - bh); c.lineTo(cx + bw / 2, T + ph - bh); c.stroke();
      const sp = Math.min(11, bw / Math.max(1, vals.length)); vals.forEach((v, j) => { c.beginPath(); c.arc(cx + (j - (vals.length - 1) / 2) * sp, T + ph - Math.min(v, mx) / mx * ph, 3.6, 0, 7); c.fillStyle = acc; c.fill(); c.strokeStyle = '#fff'; c.lineWidth = 1; c.stroke(); }); }
    c.font = '600 11px ' + SANS; c.fillStyle = '#14171a';
    if (many) { c.save(); c.translate(cx + 4, T + ph + 9); c.rotate(-Math.PI / 5); c.textAlign = 'right'; c.textBaseline = 'middle'; c.fillText(g.name.length > 24 ? g.name.slice(0, 23) + '…' : g.name, 0, 0); c.restore(); }
    else { c.textAlign = 'center'; c.textBaseline = 'top'; c.fillText(g.name, cx, T + ph + 8); }
  });
}

/* ---------------------------------------------------------------- drawing and editing gates */
function evD(pl, e, clamp) { const r = pl.cv.getBoundingClientRect(), f = pl.f; let x = (e.clientX - r.left - f.L) / f.pw, y = 1 - (e.clientY - r.top - f.T) / f.ph; if (clamp !== false) { x = Math.max(0, Math.min(1, x)); y = Math.max(0, Math.min(1, y)); } return [x, y]; }
const near = (pl, a, b, tol) => Math.hypot((a[0] - b[0]) * pl.f.pw, (a[1] - b[1]) * pl.f.ph) <= (tol || 9);
function inPoly(p, pts) { let c = false; for (let i = 0, j = pts.length - 1; i < pts.length; j = i++) { const [xi, yi] = pts[i], [xj, yj] = pts[j]; if ((yi > p[1]) !== (yj > p[1]) && p[0] < (xj - xi) * (p[1] - yi) / (yj - yi) + xi) c = !c; } return c; }
function hit(pl, d) {
  const gs = (pl.o.gates || []).slice().sort((a, b) => isSel(b) - isSel(a)), f = pl.f, px = 7 / f.pw;
  for (const g of gs) {
    if (g.type === 'poly') { const i = g.pts.findIndex(p => near(pl, p, d)); if (i >= 0) return { g, kind: 'vertex', i }; }
    if (g.type === 'rect') { const cs = [[g.x0, g.y0], [g.x1, g.y0], [g.x1, g.y1], [g.x0, g.y1]], i = cs.findIndex(p => near(pl, p, d)); if (i >= 0) return { g, kind: 'corner', i }; }
    if (g.type === 'range') { if (Math.abs(d[0] - g.x0) < px) return { g, kind: 'end', i: 0 }; if (Math.abs(d[0] - g.x1) < px) return { g, kind: 'end', i: 1 }; }
    if (g.type === 'quad') { const nx = Math.abs(d[0] - g.cx) < px, ny = Math.abs(d[1] - g.cy) < 7 / f.ph; if (nx || ny) return { g, kind: 'quad', mx: nx, my: ny }; }
  }
  for (const g of gs) {
    if (g.type === 'poly' && inPoly(d, g.pts)) return { g, kind: 'move' };
    if (g.type === 'rect' && d[0] >= Math.min(g.x0, g.x1) && d[0] <= Math.max(g.x0, g.x1) && d[1] >= Math.min(g.y0, g.y1) && d[1] <= Math.max(g.y0, g.y1)) return { g, kind: 'move' };
    if (g.type === 'range' && d[0] >= g.x0 && d[0] <= g.x1) return { g, kind: 'move' };
  }
  const q = (pl.o.gates || []).find(g => g.type === 'quad');
  if (q) { const k = (d[1] >= q.cy ? 'U' : 'L') + (d[0] >= q.cx ? 'R' : 'L'), part = q.parts.find(p => p.q === k); if (part) return { g: q, kind: 'part', part }; }
  return null;
}
function geomBody(g) { if (g.type === 'poly') return { pts: g.pts }; if (g.type === 'rect') return { x0: g.x0, x1: g.x1, y0: g.y0, y1: g.y1 }; if (g.type === 'range') return { x0: g.x0, x1: g.x1 }; return { cx: g.cx, cy: g.cy }; }
function cancelDraft() { if (draft) { const id = draft.id; draft = null; drawPlot(id); } }
async function createGate(id, type, geom) {
  const o = PLOTS[id].o; draft = null;
  await act(async () => {
    const r = await api('/api/gate', { op: 'add', parent: o.pop, type, x: o.x, y: type === 'range' ? null : o.y, geom });
    TOOL = 'select'; SEL.gate = r.ids[0]; changed();
    if (type !== 'quad') { S = await api('/api/state'); const g = gateById(r.ids[0]); const n = await ask('Name this gate', 'The events inside become a population you can gate again.', g.name); if (n && n !== g.name) await api('/api/gate', { op: 'rename', id: g.id, name: n }); }
    await refresh();
  });
}
function attach(id) {
  const pl = PLOTS[id], cv = pl.cv;
  cv.onpointerdown = e => {
    if (e.button !== 0 || !pl.f || pl.o.missing) return;
    const d = evD(pl, e), o = pl.o;
    if (SEL.obj !== id) { SEL.obj = id; $$('.obj').forEach(q => q.classList.toggle('sel', q.dataset.id === id)); renderRibbon(); }
    if (TOOL !== 'select') {
      if (o.type === 'hist' && TOOL !== 'range') return toast('On a histogram, use the Range tool. Polygons, rectangles and quadrants are drawn on density plots.', true);
      if (TOOL === 'poly') { if (!draft || draft.id !== id) draft = { id, type: 'poly', pts: [] }; if (draft.pts.length >= 3 && near(pl, draft.pts[0], d, 11)) return createGate(id, 'poly', { pts: draft.pts }); if (!draft.pts.length || !near(pl, draft.pts[draft.pts.length - 1], d, 3)) draft.pts.push(d); drawPlot(id); return; }
      if (TOOL === 'quad') return createGate(id, 'quad', { cx: d[0], cy: d[1] });
      draft = { id, type: TOOL, a: d, b: d }; cv.setPointerCapture(e.pointerId); return;
    }
    const h = hit(pl, d);
    if (!h) { if (SEL.gate) selectGate(null); return; }
    const gid = h.kind === 'part' ? h.part.id : (h.g.parts ? (h.g.parts.find(p => p.id === SEL.gate) || h.g.parts[0]).id : h.g.id);
    if (gid !== SEL.gate) selectGate(gid);
    if (h.kind === 'part') return;
    drag = { id, h, start: d, orig: JSON.parse(JSON.stringify(geomBody(h.g))), moved: false }; cv.setPointerCapture(e.pointerId);
  };
  cv.onpointermove = e => {
    if (!pl.f) return; const d = evD(pl, e);
    if (draft && draft.id === id) { if (draft.type === 'poly') draft.cursor = d; else draft.b = d; drawPlot(id); return; }
    if (!drag || drag.id !== id) { if (TOOL === 'select') { const h = hit(pl, d); cv.style.cursor = !h ? 'default' : (h.kind === 'move' ? 'move' : (h.kind === 'part' ? 'pointer' : 'grab')); } return; }
    const g = drag.h.g, o = drag.orig, dx = d[0] - drag.start[0], dy = d[1] - drag.start[1]; drag.moved = drag.moved || Math.hypot(dx * pl.f.pw, dy * pl.f.ph) > 2;
    const cl = v => Math.max(0, Math.min(1, v));
    if (drag.h.kind === 'vertex') g.pts[drag.h.i] = d;
    else if (drag.h.kind === 'corner') { const i = drag.h.i; if (i === 0 || i === 3) g.x0 = d[0]; else g.x1 = d[0]; if (i === 0 || i === 1) g.y0 = d[1]; else g.y1 = d[1]; }
    else if (drag.h.kind === 'end') { if (drag.h.i === 0) g.x0 = Math.min(d[0], g.x1 - .004); else g.x1 = Math.max(d[0], g.x0 + .004); }
    else if (drag.h.kind === 'quad') { if (drag.h.mx) g.cx = d[0]; if (drag.h.my) g.cy = d[1]; }
    else if (g.type === 'poly') { const mx = Math.max(-Math.min(...o.pts.map(p => p[0])), Math.min(1 - Math.max(...o.pts.map(p => p[0])), dx)), my = Math.max(-Math.min(...o.pts.map(p => p[1])), Math.min(1 - Math.max(...o.pts.map(p => p[1])), dy)); g.pts = o.pts.map(p => [p[0] + mx, p[1] + my]); }
    else if (g.type === 'rect') { g.x0 = cl(o.x0 + dx); g.x1 = cl(o.x1 + dx); g.y0 = cl(o.y0 + dy); g.y1 = cl(o.y1 + dy); }
    else if (g.type === 'range') { g.x0 = cl(o.x0 + dx); g.x1 = cl(o.x1 + dx); }
    drawPlot(id);
  };
  cv.onpointerup = async e => {
    if (draft && draft.id === id && draft.type !== 'poly') {
      const a = draft.a, b = draft.b, big = Math.abs(a[0] - b[0]) * pl.f.pw > 5 && (draft.type === 'range' || Math.abs(a[1] - b[1]) * pl.f.ph > 5);
      if (!big) { draft = null; drawPlot(id); return; }
      return createGate(id, draft.type, draft.type === 'range' ? { x0: Math.min(a[0], b[0]), x1: Math.max(a[0], b[0]) } : { x0: a[0], x1: b[0], y0: a[1], y1: b[1] });
    }
    if (drag && drag.id === id) {
      const dg = drag; drag = null; if (!dg.moved) return;
      await act(async () => { await api('/api/gate', { op: 'update', id: dg.h.g.id, geom: geomBody(dg.h.g), scope: SCOPE, sid: S.current }); changed(); await refresh(); });
    }
  };
  cv.ondblclick = e => {
    const d = evD(pl, e);
    if (draft && draft.id === id && draft.type === 'poly') { if (draft.pts.length >= 3) createGate(id, 'poly', { pts: draft.pts }); return; }
    if (TOOL === 'select') { const h = hit(pl, d); if (h) { const gid = h.kind === 'part' ? h.part.id : (h.g.parts ? SEL.gate : h.g.id); act(async () => { const r = await api('/api/object', { op: 'add', type: 'density', pop: gid }); SEL.obj = r.id; await refresh(); scrollToObj(r.id); }); } }
  };
}
document.addEventListener('keydown', e => {
  if (e.target.closest('input,select,textarea,dialog')) return;
  if (e.key === 'Escape') { if (draft) cancelDraft(); else if (TOOL !== 'select') { TOOL = 'select'; renderRibbon(); cursors(); } else if (SEL.gate) selectGate(null); }
  if ((e.key === 'Delete' || e.key === 'Backspace') && SEL.gate && VIEW[TAB] === 'layout') { e.preventDefault(); deleteGate(); }
  if (e.key === 'Enter' && draft && draft.type === 'poly' && draft.pts.length >= 3) createGate(draft.id, 'poly', { pts: draft.pts });
  if (e.key === 'PageDown' && S && S.samples.length) { e.preventDefault(); step(1); }
  if (e.key === 'PageUp' && S && S.samples.length) { e.preventDefault(); step(-1); }
});
function axisMenu(id, ax, anchor) {
  const o = S.objects.find(q => q.id === id), curCh = o[ax], sc = chan(curCh).scale;
  const kinds = [['logicle', 'Logicle (biexponential)'], ['linear', 'Linear'], ['asinh', 'Arcsinh'], ['log', 'Log']];
  let h = '<h6>Channel</h6>' + S.channels.map(c => `<button data-ch="${esc(c.name)}" class="${c.name === curCh ? 'on' : ''}">${esc(c.label)}</button>`).join('')
    + '<hr><h6>Scale of ' + esc(curCh) + ' (every plot)</h6>' + kinds.map(k => `<button data-scale="${k[0]}" class="${sc.type === k[0] ? 'on' : ''}">${k[1]}</button>`).join('');
  if (sc.type === 'logicle') h += '<hr><h6>Logicle width (room below zero)</h6>' + [0.5, 1, 1.5, 2].map(w => `<button data-w="${w}" class="${Math.abs(sc.W - w) < 1e-6 ? 'on' : ''}">W = ${w}${w === 0.5 ? '<em>default</em>' : ''}</button>`).join('');
  menu(anchor, h, d => act(async () => {
    if (d.ch) await api('/api/object', { op: 'update', id, [ax]: d.ch });
    else { await api('/api/transform', { channel: curCh, scale: d.scale ? { type: d.scale } : { type: 'logicle', W: +d.w } }); changed(); }
    await refresh();
  }));
}

/* ---------------------------------------------------------------- Data tab */
function renderData() {
  const roles = [['sample', 'Study sample'], ['unstained', 'Unstained control'], ['single', 'Single-stain control'], ['fmo', 'FMO control'], ['isotype', 'Isotype control']];
  const fl = S.channels.filter(c => c.kind === 'fluor');
  $('#v-data').innerHTML = `<div class="vh"><h2>Files</h2><p>Roles and groups are guessed from the file names. Check them: controls drive the checks, and groups drive the statistics and the Prism tables.</p></div>
    ${S.example && S.example.note ? `<div class="banner">${esc(S.example.note)}</div>` : ''}
    <datalist id="groupsDL">${S.groups.map(g => `<option value="${esc(g)}">`).join('')}</datalist>
    <div class="wide"><table class="tbl"><tr><th class="l">File</th><th>Events</th><th class="l">Role</th><th class="l">Stained channel</th><th class="l">Group</th><th>Use</th><th></th></tr>
    ${S.samples.map(s => `<tr data-sid="${s.id}"><td>${esc(s.name)}</td><td>${fmt(s.n)}</td>
      <td class="l"><select data-f="role">${roles.map(r => `<option value="${r[0]}"${r[0] === s.role ? ' selected' : ''}>${r[1]}</option>`).join('')}</select></td>
      <td class="l"><select data-f="stain"${['single', 'fmo', 'isotype'].includes(s.role) ? '' : ' disabled'}><option value="">–</option>${fl.map(c => `<option value="${esc(c.name)}"${c.name === s.stain ? ' selected' : ''}>${esc(c.label)}</option>`).join('')}</select></td>
      <td class="l"><input data-f="group" list="groupsDL" value="${esc(s.group)}" placeholder="${s.role === 'sample' ? 'e.g. Control' : ''}"${s.role === 'sample' ? '' : ' disabled'} style="width:240px"></td>
      <td><input type="checkbox" data-f="include"${s.include ? ' checked' : ''} aria-label="Use this file in the statistics"></td>
      <td><button data-f="remove" title="Remove from the project (the file itself is not touched)" aria-label="Remove">${ic('x')}</button></td></tr>`).join('')}</table></div>`;
}
$('#v-data').addEventListener('change', e => { const f = e.target.dataset.f, tr = e.target.closest('tr'); if (!f || !tr) return; act(async () => { await api('/api/sample', { id: tr.dataset.sid, [f]: f === 'include' ? e.target.checked : e.target.value }); changed(); await refresh(); }); });
$('#v-data').addEventListener('click', e => { const b = e.target.closest('[data-f=remove]'); if (b) act(async () => { await api('/api/sample', { op: 'remove', id: b.closest('tr').dataset.sid }); delete PTS[0]; changed(); await refresh(); }); });

/* ---------------------------------------------------------------- Compensation tab */
function drawPair(cv, P, o) {
  const [c, w, h] = fit(cv), f = { w, h, L: 46, T: 8, pw: w - 46 - 10, ph: h - 8 - 40 };
  c.fillStyle = '#fff'; c.fillRect(f.L, f.T, f.pw, f.ph);
  const dn = density(P), n = P.x.length, sz = 1.8; c.save(); c.beginPath(); c.rect(f.L, f.T, f.pw, f.ph); c.clip();
  for (let k = 0; k < n; k++) { const i = dn.o[k]; c.fillStyle = ramp(dn.d[i]); c.fillRect(f.L + P.x[i] / 65535 * f.pw - sz / 2, f.T + (1 - P.y[i] / 65535) * f.ph - sz / 2, sz, sz); } c.restore();
  c.strokeStyle = '#8f98a2'; c.lineWidth = 1; c.strokeRect(f.L + .5, f.T + .5, f.pw - 1, f.ph - 1); c.font = '10px ' + MONO; c.fillStyle = '#39424c';
  o.xt.forEach(t => { if (t[0] < 0 || t[0] > 1) return; const x = f.L + t[0] * f.pw; c.beginPath(); c.moveTo(x, f.T + f.ph); c.lineTo(x, f.T + f.ph + (t[1] ? 5 : 3)); c.stroke(); if (t[1]) { c.textAlign = 'center'; c.textBaseline = 'top'; c.fillText(t[1], x, f.T + f.ph + 7); } });
  o.yt.forEach(t => { if (t[0] < 0 || t[0] > 1) return; const y = f.T + (1 - t[0]) * f.ph; c.beginPath(); c.moveTo(f.L, y); c.lineTo(f.L - (t[1] ? 5 : 3), y); c.stroke(); if (t[1]) { c.textAlign = 'right'; c.textBaseline = 'middle'; c.fillText(t[1], f.L - 7, y); } });
  c.font = '600 11px ' + SANS; c.fillStyle = '#14171a'; c.textAlign = 'center'; c.textBaseline = 'bottom'; c.fillText(o.xl, f.L + f.pw / 2, h - 3);
  c.save(); c.translate(10, f.T + f.ph / 2); c.rotate(-Math.PI / 2); c.textBaseline = 'middle'; c.fillText(o.yl, 0, 0); c.restore();
}
async function renderComp() {
  const v = await api('/api/comp'), n = v.names.length;
  if (COMPSEL && (COMPSEL[0] >= n || COMPSEL[1] >= n)) COMPSEL = null;
  if (!COMPSEL && n > 1) COMPSEL = [0, 1];
  const modeText = { none: 'Nothing is subtracted. Fluorescence is used as measured.', file: 'Each file is compensated with the matrix stored in it by the cytometer software. Control tubes that carry no matrix use the study files’ matrix.', matrix: 'All files are compensated with one project matrix. Click a cell to see its plot; change the value below.' }[v.mode];
  const bad = v.residuals.filter(r => Math.abs(r.pct) > v.threshold);
  const cell = (i, j) => { const x = v.matrix[i][j] * 100, a = Math.min(1, Math.abs(x) / 50), bg = i === j ? '#eceef0' : `rgba(${x < 0 ? '60,120,235' : '235,100,40'},${(a * .75).toFixed(3)})`; return `<td class="${i === j ? 'diag' : ''}${COMPSEL && COMPSEL[0] === i && COMPSEL[1] === j ? ' sel' : ''}" style="background:${bg}"><button data-i="${i}" data-j="${j}">${x.toFixed(1)}</button></td>`; };
  const sel = COMPSEL && n ? { from: v.names[COMPSEL[0]], into: v.names[COMPSEL[1]] } : null;
  const ctrl = sel && S.samples.find(s => s.role === 'single' && s.stain === sel.from);
  if (!COMPFILE || !S.samples.find(s => s.id === COMPFILE.id) || COMPFILE.auto) COMPFILE = { id: ctrl ? ctrl.id : S.current, auto: true };
  $('#v-comp').innerHTML = `<div class="vh"><h2>Compensation</h2><p>${esc(modeText)}</p></div>
    <div class="two"><div class="card">
      <h3>Spillover matrix, percent <span style="font-weight:400;color:var(--dim);font-size:12px">${esc(v.source)}</span></h3>
      <p>Rows are fluorochromes, columns are detectors: each cell is how much of the row’s signal appears in the column’s detector.</p>
      ${n ? `<div style="overflow:auto"><table class="mx"><tr><th></th>${v.names.map(k => `<th>${esc(k.replace(/-A$/, ''))}</th>`).join('')}</tr>${v.names.map((k, i) => `<tr><th class="rowh">${esc(k.replace(/-A$/, ''))}</th>${v.names.map((_, j) => cell(i, j)).join('')}</tr>`).join('')}</table></div>` : '<div class="empty">No matrix to show for this file under the current setting.</div>'}
      ${v.mode === 'matrix' && sel && COMPSEL[0] !== COMPSEL[1] ? `<div class="form"><span>${esc(sel.from.replace(/-A$/, ''))} into ${esc(sel.into.replace(/-A$/, ''))}</span><input id="cellVal" type="number" step="0.1" value="${(v.matrix[COMPSEL[0]][COMPSEL[1]] * 100).toFixed(2)}" style="width:90px"> %<button class="btn" id="cellSet">Set</button></div>` : ''}
      <h3 style="margin-top:16px">Check against the single-stain controls</h3>
      ${!v.controls.length ? '<p>No single-stain controls are loaded. Mark them on the Data tab to check the matrix or to compute one.</p>' : (v.mode === 'none' ? '<p>Compensation is off, so there is nothing to check.</p>' : `<p>After compensation a single-stain control should show no signal in the other detectors. ${bad.length ? `<b style="color:var(--warn)">${bad.length} pair(s) are off by more than ${v.threshold} %.</b>` : `Every pair is within ${v.threshold} %.`} Positive means under-compensated, negative over-compensated.</p>
        ${bad.length ? `<table class="tbl" style="max-width:520px"><tr><th>Control</th><th>From</th><th>Into</th><th>Left over</th></tr>${bad.sort((a, b) => Math.abs(b.pct) - Math.abs(a.pct)).slice(0, 12).map(r => `<tr><td>${esc(r.control.replace(/\.fcs$/i, ''))}</td><td>${esc(r.from)}</td><td>${esc(r.into)}</td><td>${r.pct.toFixed(1)} %</td></tr>`).join('')}</table>` : ''}`)}
      <p class="note">“From controls” takes the brightest ${S.prefs.comp_top_pct} % of events in each control’s stained channel and compares their median with the unstained control (or, without one, the dimmest 15 % of the same tube). It is a starting point: check each pair by eye.</p>
    </div><div class="card">
      <h3>${sel ? esc(sel.from.replace(/-A$/, '')) + ' against ' + esc(sel.into.replace(/-A$/, '')) : 'Pair'}</h3>
      <div class="form" style="margin:0 0 10px"><span>File</span><select id="compFile" style="max-width:320px">${S.samples.map(s => `<option value="${s.id}"${s.id === COMPFILE.id ? ' selected' : ''}>${esc(s.name)}</option>`).join('')}</select></div>
      <div class="pairs"><div><h5>As measured</h5><div class="pc"><canvas id="pairRaw"></canvas></div></div><div><h5>Compensated</h5><div class="pc"><canvas id="pairComp"></canvas></div></div></div>
      <p class="note" id="pairNote"></p>
    </div></div>`;
  $$('#v-comp .mx td button').forEach(b => b.onclick = () => { COMPSEL = [+b.dataset.i, +b.dataset.j]; COMPFILE = null; renderComp(); });
  const cf = $('#compFile'); if (cf) cf.onchange = () => { COMPFILE = { id: cf.value, auto: false }; renderComp(); };
  const cs = $('#cellSet'); if (cs) cs.onclick = () => act(async () => { await api('/api/comp', { op: 'cell', i: COMPSEL[0], j: COMPSEL[1], pct: +$('#cellVal').value }); changed(); await refresh(); });
  if (sel && COMPSEL[0] !== COMPSEL[1]) {
    try { const p = await api(`/api/comp/pair?sid=${COMPFILE.id}&x=${encodeURIComponent(sel.from)}&y=${encodeURIComponent(sel.into)}`);
      drawPair($('#pairRaw'), { x: u16(p.raw.px), y: u16(p.raw.py) }, p); drawPair($('#pairComp'), { x: u16(p.comp.px), y: u16(p.comp.py) }, p);
      $('#pairNote').textContent = ctrl && COMPFILE.id === ctrl.id ? 'Showing the single-stain control for ' + sel.from + '. When the pair is compensated correctly the cloud lies flat.' : 'Showing ' + p.name + '.';
    } catch (e) { $('#pairNote').textContent = e.message; }
  } else if ($('#pairNote')) $('#pairNote').textContent = 'Click a cell off the diagonal to see that pair.';
}

/* ---------------------------------------------------------------- Statistics tab */
async function renderStats() {
  if (!S.stats.length) { $('#v-stats').innerHTML = '<div class="vh"><h2>Statistics</h2><p>Draw a gate first: every gate gets a percentage of its parent for every file.</p></div>'; return; }
  if (!STATSEL || !S.stats.find(d => d.key === STATSEL)) STATSEL = S.stats[S.stats.length - 1].key;
  const [tb, cmp] = await Promise.all([api('/api/table'), api('/api/compare?key=' + encodeURIComponent(STATSEL))]);
  const d = S.stats.find(q => q.key === STATSEL), dec = d.stat === 'count' ? 0 : 2, nf = v => v == null ? '–' : v.toLocaleString('en-US', { minimumFractionDigits: dec, maximumFractionDigits: dec });
  const fl = S.channels.filter(c => c.kind !== 'time');
  $('#v-stats').innerHTML = `<div class="vh"><h2>Statistics</h2><p>One value per file. Groups are compared file against file, never event against event.</p></div>
    <div class="two" style="grid-template-columns:minmax(260px,.7fr) minmax(0,1.6fr)"><div class="card"><h3>What is reported</h3><p>Each of these becomes one table in Prism and one column in Excel.</p>
      <div class="list">${S.stats.map(q => `<button class="li${q.key === STATSEL ? ' on' : ''}" data-key="${esc(q.key)}">${esc(q.title)}${q.auto ? '' : `<span class="x" data-rm="${esc(q.key)}" title="Remove">${ic('x')}</span>`}</button>`).join('')}</div>
      <h3 style="margin-top:14px">Add a statistic</h3>
      <div class="form"><select id="stPop"><option value="">All events</option>${S.gates.map(g => `<option value="${g.id}">${esc(g.name)}</option>`).join('')}</select>
        <select id="stKind"><option value="median">Median</option><option value="geomean">Geometric mean</option><option value="count">Events</option><option value="pct_total">% of all events</option></select>
        <select id="stCh">${fl.map(c => `<option value="${esc(c.name)}">${esc(c.label)}</option>`).join('')}</select><button class="btn" id="stAdd">${ic('plus')}Add</button></div>
    </div><div class="card"><h3>${esc(cmp.title)}</h3>
      ${cmp.groups.length ? '<div class="chartbox"><canvas id="statChart"></canvas></div>' : '<div class="empty">Give the files a group on the Data tab to compare them.</div>'}
      ${cmp.groups.length ? `<table class="tbl" style="margin-top:10px"><tr><th>Group</th><th>Files</th><th>Mean</th><th>SD</th><th>Median</th></tr>${cmp.groups.map(g => `<tr><td>${esc(g.name)}</td><td>${g.n}</td><td>${nf(g.mean)}</td><td>${nf(g.sd)}</td><td>${nf(g.median)}</td></tr>`).join('')}</table>` : ''}
      ${cmp.tests.length ? `<table class="tbl" style="margin-top:10px;max-width:560px"><tr><th>Test</th><th>Compares</th><th>Statistic</th><th>p</th></tr>${cmp.tests.map(t => `<tr><td>${esc(t.name)}</td><td style="font-family:var(--font)">${esc(t.what)}</td><td>${t.stat.toFixed(3)}</td><td>${t.p < 0.0001 ? t.p.toExponential(2) : t.p.toFixed(4)}</td></tr>`).join('')}</table>` : ''}
      ${cmp.note ? `<p class="note">${esc(cmp.note)}</p>` : ''}
      <p class="note">Both a test on means and a test on ranks are shown; which one suits your design is your call. No correction for testing several statistics is applied.</p>
    </div></div>
    <div class="vh" style="margin-top:18px"><h2 style="font-size:15px">Every file</h2></div>
    <div class="wide"><table class="tbl"><tr><th class="l">File</th><th class="l">Group</th>${tb.stats.map(q => `<th>${esc(q.title)}</th>`).join('')}</tr>
    ${tb.rows.map(r => `<tr><td>${esc(r.name)}</td><td class="l" style="font-family:var(--font)">${esc(r.group || '–')}</td>${r.values.map((v, i) => `<td>${v == null ? '–' : v.toLocaleString('en-US', { maximumFractionDigits: tb.stats[i].stat === 'count' ? 0 : 2 })}</td>`).join('')}</tr>`).join('')}</table></div>`;
  const cv = $('#statChart'); if (cv) drawChart(cv, { groups: cmp.groups.map(g => ({ name: g.name, values: g.values.map(v => ({ v })) })), unit: d.stat.startsWith('pct') ? '%' : '' });
  const kind = $('#stKind'), syncCh = () => $('#stCh').disabled = !['median', 'geomean'].includes(kind.value); kind.onchange = syncCh; syncCh();
  $('#stAdd').onclick = () => act(async () => { await api('/api/stat', { op: 'add', pop: $('#stPop').value || null, stat: kind.value, channel: $('#stCh').value }); STATSEL = null; changed(); await refresh(); });
}
$('#v-stats').addEventListener('click', e => {
  const rm = e.target.closest('[data-rm]'); if (rm) { e.stopPropagation(); return act(async () => { await api('/api/stat', { op: 'remove', key: rm.dataset.rm }); changed(); await refresh(); }); }
  const b = e.target.closest('.li[data-key]'); if (b) { STATSEL = b.dataset.key; renderStats(); }
});

/* ---------------------------------------------------------------- Export tab */
function renderExport() {
  const p = S.prism, has = S.stats.length && S.groups.length;
  const lim = [['min_events', 'Fewest events in a gate before a warning'], ['fmo_inside_pct', 'Most of an FMO control allowed inside a gate, %'], ['comp_residual_pct', 'Largest signal left in another detector after compensation, %'], ['saturation_pct', 'Most events allowed at a detector maximum, %'], ['rate_deviation_pct', 'How far a time slice may differ from the median flow rate, %'], ['rate_bad_bins_pct', 'Share of time slices allowed to differ, %'], ['comp_top_pct', 'Brightest share of a control used to compute spillover, %']];
  $('#v-export').innerHTML = `<div class="vh"><h2>Export</h2><p>Reports and tables always print light, whatever the app looks like.</p></div>
    <div class="cardgrid">
      <div class="card"><h3>GraphPad Prism</h3><p>One Column table per statistic: groups as columns, one value per file. Linking a Prism file lets you update its numbers from here; graphs and analyses you made in Prism are left alone and redraw when Prism opens the file. A dated backup is kept each time.</p>
        ${p.path ? `<p><b style="color:var(--ink)">${esc(p.name)}</b><br>${p.exists ? (PRISM_STALE ? 'Numbers have changed since the last update.' : 'Up to date with the last update from here.') : 'This file is not where it was.'}</p>` : ''}
        <div class="bts"><button class="btn go" data-act="${p.path ? 'prismUpdate' : 'prismNew'}"${has && (!p.path || p.exists) ? '' : ' disabled'}>${ic('prism')}${p.path ? 'Update Prism file' : 'New Prism file'}</button>
          <button class="btn" data-act="prismLink"${has ? '' : ' disabled'}>${ic('link')}Link an existing file</button>${p.path ? `<button class="btn" data-act="prismOpen"${p.exists ? '' : ' disabled'}>${ic('ext')}Open in Prism</button><button class="btn" data-act="prismUnlink">Unlink</button>` : ''}</div>
        ${has ? '' : '<p class="note">Needs at least one gate and one group.</p>'}<p class="note">Prism must have the file closed while it is updated. Files saved as .prism cannot be updated: save as .pzfx.</p></div>
      <div class="card"><h3>PDF report</h3><p>Files, compensation, scales, every check, statistics by group with the tests, and for each study file every plot on the page with its gates and numbers.</p><div class="bts"><button class="btn go" data-act="pdf">${ic('pdf')}Save PDF report</button></div></div>
      <div class="card"><h3>Excel workbook</h3><p>Statistics per file, every gate in every file with events and percentages, the settings used, and the checks.</p><div class="bts"><button class="btn" data-act="xlsx">${ic('xls')}Save Excel workbook</button></div></div>
      <div class="card"><h3>Gating-ML 2.0</h3><p>The gates in the open standard, with the scale of each axis, for other cytometry software or for the methods section.</p><div class="bts"><button class="btn" data-act="gml">${ic('gml')}Save Gating-ML</button></div></div>
      <div class="card"><h3>Project</h3><p>Saves the list of files, roles, groups, gates, scales, compensation, page layout and the Prism link in one small file. The FCS files stay where they are.</p><div class="bts"><button class="btn" data-act="saveProject">${ic('save')}Save</button><button class="btn" data-act="saveAs">Save as…</button></div></div>
      <div class="card"><h3>Limits used by the checks</h3><p>These are starting values proposed by Flow Bench, not rules. Set them to what your lab accepts; they are remembered on this Mac and printed in every report.</p>
        <div class="lim">${lim.map(([k, t]) => `<label for="lim_${k}">${t}${S.prefs[k] !== S.prefs_default[k] ? ' <span style="color:var(--accent)">(changed)</span>' : ''}</label><input id="lim_${k}" data-lim="${k}" type="number" min="0" step="any" value="${S.prefs[k]}">`).join('')}</div>
        <div class="bts" style="margin-top:12px"><button class="btn" id="limReset">Back to the proposed values</button></div></div>
    </div>`;
  $('#limReset').onclick = () => act(async () => { await api('/api/prefs', { op: 'reset' }); await refresh(); });
}
$('#v-export').addEventListener('click', e => { const b = e.target.closest('[data-act]'); if (b && !b.disabled) doAct(b.dataset.act, b); });
$('#v-export').addEventListener('change', e => { const k = e.target.dataset.lim; if (k) act(async () => { await api('/api/prefs', { values: { [k]: +e.target.value } }); await refresh(); }); });

/* ---------------------------------------------------------------- start screen and examples */
let bgRun = null;
function renderStart() {
  const host = $('#v-start');
  host.innerHTML = `<canvas class="bg" aria-hidden="true"></canvas><div class="hero"><h1>Flow Bench</h1><p>Gate, check and compare flow cytometry files on this Mac. Nothing is uploaded.</p>
    <div class="dz"><div><b>Drop FCS files or a folder here</b><span>FCS 2.0, 3.0 and 3.1. The files are read where they are and never changed.</span></div><div class="bts"><button class="btn go" data-act="addFiles">${ic('file')}Add files</button><button class="btn" data-act="addFolder">${ic('folder')}Add folder</button><button class="btn" data-act="openProject">${ic('open')}Open project</button></div></div>
    <h4>Or try it on published data</h4><div class="ex">${S.examples.map(x => `<div class="card"><h3>${esc(x.title)}</h3><p>${esc(x.blurb)}</p><div class="bts"><button class="btn${x.installed ? ' go' : ''}" data-ex="${x.key}">${x.installed ? 'Open' : 'Download (' + x.size_mb + ' MB) and open'}</button></div><div class="prog" id="prog_${x.key}" hidden><i></i></div>
      <small>${esc(x.citation)}, <a href="https://doi.org/${esc(x.doi)}" target="_blank" rel="noopener">doi:${esc(x.doi)}</a>. Data: <a href="https://zenodo.org/records/${esc(x.record)}" target="_blank" rel="noopener">Zenodo ${esc(x.record)}</a>, ${esc(x.licence)}.</small></div>`).join('')}</div></div>`;
  startBg(host.querySelector('canvas'));
}
$('#v-start').addEventListener('click', e => { const x = e.target.closest('[data-ex]'); if (x) return openExample(x.dataset.ex); const b = e.target.closest('[data-act]'); if (b) doAct(b.dataset.act, b); });
async function openExample(key) {
  const x = S.examples.find(q => q.key === key);
  if (S.samples.length && !(await ask('Open the example?', 'It replaces the analysis that is open. Gates and settings that are not saved are lost.', null, 'Open example'))) return;
  try {
    if (!x.installed) {
      await api('/api/example', { op: 'download', key }); const bar = $('#prog_' + key); if (bar) bar.hidden = false; else busy(true, 'Downloading from Zenodo…');
      for (;;) { await new Promise(r => setTimeout(r, 700)); const j = await api('/api/job?id=' + key); if (bar && j.total) bar.firstChild.style.width = (100 * j.done / j.total) + '%'; else if (!bar) $('#busyText').textContent = j.total ? `Downloading from Zenodo… ${j.done} of ${j.total} files` : j.message; if (j.state === 'done') break; if (j.state === 'error') throw new Error(j.error); }
    }
    busy(true, 'Opening the example…'); await api('/api/example', { op: 'load', key }); reset(); TAB = 'Home'; await refresh();
  } catch (e) { toast(e.message, true); } finally { busy(false); }
}
function startBg(cv) {
  if (bgRun) cancelAnimationFrame(bgRun);
  const still = matchMedia('(prefers-reduced-motion:reduce)').matches; const cells = Array.from({ length: 70 }, () => ({ x: Math.random(), v: Math.random() * 2 - 1, s: .0012 + Math.random() * .0016, r: 1.6 + Math.random() * 2.2, hot: Math.random() < .35 }));
  const tick = () => {
    if (!cv.isConnected) { bgRun = null; return; }
    const [c, w, h] = fit(cv), cy = h * .5, lx = w * .72, acc = cssv('--accent'), dim = cssv('--line2');
    const half = x => { const t = Math.min(1, Math.max(0, (x - .1) / .35)); return 120 - 104 * (t * t * (3 - 2 * t)); };
    c.strokeStyle = dim; c.lineWidth = 1; c.beginPath(); for (let i = 0; i <= 60; i++) { const x = i / 60; c.lineTo(x * w, cy - half(x)); } c.stroke(); c.beginPath(); for (let i = 0; i <= 60; i++) { const x = i / 60; c.lineTo(x * w, cy + half(x)); } c.stroke();
    c.strokeStyle = acc; c.globalAlpha = .5; c.lineWidth = 2; c.beginPath(); c.moveTo(lx, cy - 90); c.lineTo(lx, cy + 90); c.stroke(); c.globalAlpha = 1;
    for (const p of cells) { if (!still) { p.x += p.s; if (p.x > 1.05) { p.x = -.05; p.v = Math.random() * 2 - 1; } } const x = p.x * w, y = cy + p.v * half(p.x) * .8, d = Math.abs(x - lx);
      c.beginPath(); c.arc(x, y, p.r, 0, 7); c.fillStyle = d < 14 && p.hot ? acc : dim; c.globalAlpha = d < 14 ? 1 : .8; c.fill();
      if (d < 14 && p.hot) { c.globalAlpha = .25 * (1 - d / 14); c.beginPath(); c.arc(x, y, p.r + 9, 0, 7); c.fill(); } c.globalAlpha = 1; }
    if (!still) bgRun = requestAnimationFrame(tick);
  };
  tick();
}

/* ---------------------------------------------------------------- drag and drop */
let dragDepth = 0;
addEventListener('dragenter', e => { if (e.dataTransfer && [...e.dataTransfer.types].includes('Files')) { dragDepth++; $('#drop').hidden = false; } });
addEventListener('dragleave', () => { dragDepth = Math.max(0, dragDepth - 1); if (!dragDepth) $('#drop').hidden = true; });
addEventListener('dragover', e => { if (e.dataTransfer && [...e.dataTransfer.types].includes('Files')) e.preventDefault(); });
addEventListener('drop', async e => {
  if (!e.dataTransfer || !e.dataTransfer.files.length) return; e.preventDefault(); dragDepth = 0; $('#drop').hidden = true;
  const all = [], walk = async en => { if (en.isFile) await new Promise(r => en.file(f => { all.push(f); r(); }, r)); else if (en.isDirectory) { const rd = en.createReader(); for (;;) { const batch = await new Promise(r => rd.readEntries(r, () => r([]))); if (!batch.length) break; for (const b of batch) await walk(b); } } };
  const items = [...e.dataTransfer.items].map(i => i.webkitGetAsEntry && i.webkitGetAsEntry()).filter(Boolean);
  if (items.length) for (const it of items) await walk(it); else all.push(...e.dataTransfer.files);
  const files = all.filter(f => /\.fcs$/i.test(f.name)); if (!files.length) return toast('None of those are .fcs files.', true);
  try { let n = 0; for (const f of files) { busy(true, `Adding ${++n} of ${files.length}: ${f.name}`); const r = await fetch('/api/upload?name=' + encodeURIComponent(f.name), { method: 'POST', body: f }); const j = await r.json(); if (!r.ok || j.error) throw new Error(j.error || 'Upload failed'); } toast(files.length + ' file(s) added'); await refresh(); }
  catch (err) { toast(err.message, true); } finally { busy(false); }
});

/* ---------------------------------------------------------------- start */
syncPill();
try { new EventSource('/api/window'); } catch (e) { }
refresh().catch(e => toast(e.message, true));
