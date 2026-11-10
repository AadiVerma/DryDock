/* ECS Lab web interface. Plain JavaScript, no build step.
 *
 * The pages follow the same structure, wording and interaction patterns as the
 * AWS consoles for ECS, EC2 target groups and CloudWatch Logs: the same paths,
 * tabs, column names and field labels; tables you search, page through and
 * select from before acting; banners that report what an action did. What a
 * learner finds here is where they will find it on AWS. The visual identity is
 * ECS Lab's own; this is a training simulator and says so. */
(() => {
  'use strict';

  // ---------- helpers ----------
  const $ = (id) => document.getElementById(id);
  function h(tag, attrs, ...kids) {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v == null || v === false) continue;
      if (k === 'class') el.className = v;
      else if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
      else if (k === 'value') el.value = v;
      else el.setAttribute(k, v === true ? '' : v);
    }
    for (const kid of kids.flat(Infinity)) {
      if (kid == null || kid === false) continue;
      el.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
    }
    return el;
  }
  const qs = (o) => Object.entries(o).filter(([, v]) => v != null && v !== '')
    .map(([k, v]) => `${k}=${encodeURIComponent(v)}`).join('&');
  async function api(path, body) {
    const opts = body === undefined ? {} : {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) };
    const res = await fetch(path, opts);
    let data = {};
    try { data = await res.json(); } catch (e) { /* not JSON */ }
    if (!res.ok) throw new Error(data.error || `Request failed (${res.status})`);
    return data;
  }
  const get = (path, params, user) => api(`/api/aws/${path}?${qs({ ...params, user })}`);
  function toast(msg) {
    const t = $('toast');
    t.textContent = msg; t.classList.add('show');
    clearTimeout(toast.timer);
    toast.timer = setTimeout(() => t.classList.remove('show'), 3500);
  }
  function date(v) {
    if (v == null || v === '') return null;
    const d = typeof v === 'number' ? new Date(v < 1e11 ? v * 1000 : v) : new Date(String(v).replace(' ', 'T'));
    return isNaN(d) ? null : d;
  }
  const when = (v) => { const d = date(v); return d ? d.toLocaleString([], { hour12: false }) : (v ? String(v) : '-'); };
  function ago(v) {
    const d = date(v); if (!d) return '-';
    const s = Math.max(0, (Date.now() - d) / 1000);
    const n = (x, u) => `${x} ${u}${x === 1 ? '' : 's'} ago`;
    return s < 60 ? n(Math.floor(s), 'second') : s < 3600 ? n(Math.floor(s / 60), 'minute')
      : s < 86400 ? n(Math.floor(s / 3600), 'hour') : n(Math.floor(s / 86400), 'day');
  }
  const clock = (s) => { s = Math.max(0, Math.floor(s)); return String(Math.floor(s / 60)).padStart(2, '0') + ':' + String(s % 60).padStart(2, '0'); };
  const cap = (s) => (s ? String(s).charAt(0).toUpperCase() + String(s).slice(1).toLowerCase().replace(/_/g, ' ') : '-');
  const dash = (v) => (v == null || v === '' ? '-' : v);
  const mono = (t) => h('span', { class: 'mono' }, t);
  const dur = (s) => { s = Math.max(0, Math.round(s)); const m = Math.floor(s / 60), r = s % 60; return m && r ? `${m}m ${String(r).padStart(2, '0')}s` : m ? `${m}m` : `${r}s`; };
  const store = { get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } }, set(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* private mode */ } } };
  const stars = (n) => h('span', { class: 'diff', title: `Difficulty ${n} of 5` }, h('span', { 'aria-hidden': 'true' }, '●'.repeat(n) + '○'.repeat(5 - n)), h('span', { class: 'sr' }, `Difficulty ${n} of 5`));
  const errBox = (title, text) => h('div', { class: 'flash bad' }, h('div', {}, h('b', {}, title), text ? h('div', {}, text) : null));
  // A small, safe renderer for the walkthroughs the lab writes itself (headings, lists, code, bold, inline code).
  function md(text) {
    const inline = (t) => t.split(/(`[^`]+`|\*\*[^*]+\*\*)/).map((x) => (x.startsWith('`') && x.length > 1 ? h('code', {}, x.slice(1, -1))
      : x.startsWith('**') && x.length > 3 ? h('b', {}, x.slice(2, -2)) : x));
    const out = h('div', { class: 'md' }); let list = null, code = null;
    for (const raw of text.split('\n')) {
      const line = raw.replace(/^ {3}/, '');
      if (line.startsWith('```')) { if (code) { out.append(h('pre', { class: 'block' }, code.join('\n'))); code = null; } else code = []; continue; }
      if (code) { code.push(line); continue; }
      const m = /^(#{1,3}) (.*)/.exec(line), b = /^- (.*)/.exec(line), n = /^\d+\. (.*)/.exec(line);
      if (m) { list = null; out.append(h('h' + (m[1].length + 1), {}, inline(m[2]))); }
      else if (b || n) { const tag = b ? 'ul' : 'ol'; if (!list || list.tagName.toLowerCase() !== tag || n) { list = h(tag, n ? { start: /^\d+/.exec(line)[0] } : {}); out.append(list); } list.append(h('li', {}, inline((b || n)[1]))); }
      else if (line.trim()) { list = null; out.append(h('p', {}, inline(line))); }
    }
    return out;
  }
  function kvEditor(items, keyLabel, valueLabel, what) {
    const rows = h('div', { class: 'kvs' });
    const add = (k, v) => {
      const ki = h('input', { type: 'text', 'aria-label': keyLabel, placeholder: keyLabel, value: k || '', spellcheck: 'false' });
      const vi = h('input', { type: 'text', 'aria-label': valueLabel, placeholder: valueLabel, value: v || '', spellcheck: 'false' });
      const row = h('div', { class: 'kv' }, ki, vi, h('button', { class: 'btn small', type: 'button', onclick: () => row.remove() }, 'Remove'));
      row.read = () => [ki.value.trim(), vi.value];
      rows.append(row);
    };
    items.forEach(([k, v]) => add(k, v));
    return { node: h('div', {}, rows, h('button', { class: 'btn small', type: 'button', onclick: () => add() }, 'Add ' + what)),
      read: () => [...rows.children].map((r) => r.read()).filter(([k]) => k) };
  }

  const ICONS = {
    sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
    moon: '<path d="M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5z"/>',
    auto: '<circle cx="12" cy="12" r="9"/><path d="M12 3v18"/><path d="M12 7a5 5 0 0 1 0 10z" fill="currentColor"/>',
  };
  function icon(name) {   // static, trusted markup only
    const span = document.createElement('span');
    span.innerHTML = `<svg class="ic" viewBox="0 0 24 24" aria-hidden="true">${ICONS[name] || ''}</svg>`;
    return span.firstChild;
  }

  // Status: a glyph plus sentence-case text.
  const GOOD = ['RUNNING', 'ACTIVE', 'HEALTHY', 'COMPLETED', 'PRIMARY'];
  const BAD = ['STOPPED', 'UNHEALTHY', 'FAILED', 'INACTIVE', 'DEPROVISIONING', 'STOPPING'];
  function ind(status, text) {
    const up = String(status || '').toUpperCase();
    const kind = GOOD.includes(up) ? 'ok' : BAD.includes(up) ? 'bad' : !status || up === 'UNKNOWN' ? 'idle' : 'warn';
    return h('span', { class: 'ind ' + kind }, h('i', { 'aria-hidden': 'true' }, { ok: '✓', bad: '✕', warn: '◔', idle: '–' }[kind]),
      text || cap(status || 'Unknown'));
  }
  const link = (text, view, params) => h('a', { href: '#', onclick: (e) => { e.preventDefault(); go(view, params || {}); } }, text);
  const field = (label, value) => h('div', { class: 'field' }, h('div', { class: 'fl' }, label), h('div', { class: 'fv' }, dash(value)));
  const fields = (...items) => h('div', { class: 'fields' }, items.filter(Boolean).map(([l, v]) => field(l, v)));
  function box(title, ...kids) {
    const head = Array.isArray(title) ? title : [title];
    return h('section', { class: 'box' }, h('div', { class: 'box-h' }, h('h2', {}, head[0]),
      head[1] != null ? h('span', { class: 'count' }, `(${head[1]})`) : null, h('span', { class: 'spacer' }), head.slice(2)),
    h('div', { class: 'box-b' }, kids));
  }
  const help = (text) => h('div', { class: 'help' }, text);
  function tabs(items, current, pick) {
    return h('div', { class: 'tabs', role: 'tablist' }, items.map(([id, label]) => h('button', { role: 'tab',
      'aria-selected': String(id === current), class: id === current ? 'on' : '', onclick: () => pick(id) }, label)));
  }
  function page({ crumbs, title, status, actions }, ...body) {
    return h('div', {},
      h('nav', { class: 'crumbs', 'aria-label': 'Breadcrumb' }, crumbs.map((c, i) => [i ? h('span', { class: 'sep' }, '›') : null,
        i === crumbs.length - 1 ? h('b', {}, c[0]) : c[1] ? link(c[0], c[1], c[2]) : h('span', {}, c[0])])),
      h('div', { class: 'head' }, h('h1', {}, title), status || null, h('span', { class: 'spacer' }), actions || null),
      body);
  }

  // ---------- resource table: search, paging, select-then-act ----------
  // opts: key, title, heads, rows [{ id, cells }], empty, select, actions(row) -> nodes, extra, pageSize, find
  const UI = {};
  function dataTable(o) {
    const st = UI[o.key] || (UI[o.key] = { q: '', page: 0, sel: null });
    const size = o.pageSize || 10;
    const text = (r) => r.text || (r.text = r.cells.map((c) => (c && c.nodeType ? c.textContent : String(c ?? ''))).join(' ').toLowerCase());
    if (st.sel != null && !o.rows.some((r) => r.id === st.sel)) st.sel = null;
    const actions = h('span', { class: 'row' }), pager = h('div', { class: 'pager' }), wrap = h('div', { class: 'scroll' });
    const countEl = h('span', { class: 'count' });
    function draw() {
      const q = st.q.trim().toLowerCase();
      const rows = q ? o.rows.filter((r) => text(r).includes(q)) : o.rows;
      const pages = Math.max(1, Math.ceil(rows.length / size));
      st.page = Math.min(st.page, pages - 1);
      const shown = rows.slice(st.page * size, st.page * size + size);
      countEl.textContent = q ? `(${rows.length}/${o.total ?? o.rows.length})` : `(${o.total ?? o.rows.length})`;
      const sel = o.rows.find((r) => r.id === st.sel) || null;
      actions.replaceChildren(...(o.actions ? [o.actions(sel)].flat().filter(Boolean) : []));
      pager.replaceChildren(
        h('button', { class: 'pg', 'aria-label': 'Previous page', disabled: st.page === 0, onclick: () => { st.page--; draw(); } }, '‹'),
        h('span', { class: 'pgn', 'aria-live': 'polite' }, `${st.page + 1}`), h('span', { class: 'muted' }, `of ${pages}`),
        h('button', { class: 'pg', 'aria-label': 'Next page', disabled: st.page >= pages - 1, onclick: () => { st.page++; draw(); } }, '›'));
      const heads = (o.select ? [h('th', { class: 'pick' }, h('span', { class: 'sr' }, 'Select'))] : []).concat(o.heads.map((x) => h('th', { scope: 'col' }, x)));
      const body = shown.length ? shown.map((r) => h('tr', { class: r.id === st.sel ? 'sel' : '' },
        o.select ? h('td', { class: 'pick' }, h('input', { type: 'radio', name: 'pick-' + o.key, checked: r.id === st.sel,
          'aria-label': 'Select ' + r.id, onchange: () => { st.sel = r.id; draw(); } })) : null,
        r.cells.map((c) => h('td', {}, c == null || c === '' ? '-' : c))))
        : h('tr', {}, h('td', { colspan: String(heads.length), class: 'empty' }, h('b', {}, q ? 'No matches' : (o.empty || 'No resources')),
          h('div', { class: 'muted' }, q ? 'Nothing matches that search.' : 'No resources to display')));
      wrap.replaceChildren(h('table', {}, h('thead', {}, h('tr', {}, heads)), h('tbody', {}, body)));
    }
    const search = h('input', { type: 'search', class: 'find', placeholder: o.find || `Find ${o.title.toLowerCase()}`,
      'aria-label': o.find || `Find ${o.title.toLowerCase()}`, value: st.q, oninput: (e) => { st.q = e.target.value; st.page = 0; draw(); } });
    draw();
    return h('section', { class: 'box' },
      h('div', { class: 'box-h' }, h('h2', {}, o.title), countEl, h('span', { class: 'spacer' }), o.extra || null, actions),
      h('div', { class: 'tools' }, search, h('span', { class: 'spacer' }), pager),
      h('div', { class: 'box-b' }, wrap, o.note ? h('p', { class: 'muted small pad' }, o.note) : null));
  }
  const plainTable = (heads, rows, empty) => h('div', { class: 'scroll' }, h('table', {},
    h('thead', {}, h('tr', {}, heads.map((x) => h('th', { scope: 'col' }, x)))),
    h('tbody', {}, rows.length ? rows.map((r) => h('tr', {}, r.map((c) => h('td', {}, c == null || c === '' ? '-' : c))))
      : h('tr', {}, h('td', { colspan: String(heads.length), class: 'empty' }, h('b', {}, empty || 'No resources'),
        h('div', { class: 'muted' }, 'No resources to display'))))));

  // ---------- banners and confirmation ----------
  let flashId = 0;
  const FLASHES = [];
  function flash(kind, title, text) {
    // One "it worked" banner at a time; errors stay until dismissed (newest two).
    if (kind !== 'bad') for (let i = FLASHES.length - 1; i >= 0; i--) if (FLASHES[i].kind !== 'bad') FLASHES.splice(i, 1);
    FLASHES.unshift({ id: ++flashId, kind, title, text });
    while (FLASHES.filter((f) => f.kind === 'bad').length > 2) FLASHES.splice(FLASHES.map((f) => f.kind).lastIndexOf('bad'), 1);
    drawFlashes();
  }
  function drawFlashes() {
    $('flashes').replaceChildren(...FLASHES.map((f) => h('div', { class: 'flash ' + f.kind, role: f.kind === 'bad' ? 'alert' : 'status' },
      h('div', { style: 'flex:1;min-width:0' }, h('b', {}, f.title), f.text ? h('div', {}, f.text) : null),
      h('button', { class: 'x', 'aria-label': 'Dismiss', onclick: () => { FLASHES.splice(FLASHES.indexOf(f), 1); drawFlashes(); } }, '✕'))));
  }
  function confirmDialog({ title, body, confirm, danger }) {
    return new Promise((resolve) => {
      const d = $('dlg');
      const done = (v) => { d.close(); resolve(v); };
      d.replaceChildren(h('h2', {}, title), h('div', { class: 'dlg-b' }, body),
        h('div', { class: 'row end' }, h('button', { class: 'btn', onclick: () => done(false) }, 'Cancel'),
          h('button', { class: 'btn ' + (danger ? 'danger' : 'primary'), onclick: () => done(true) }, confirm)));
      d.oncancel = () => resolve(false);
      d.showModal();
    });
  }

  // ---------- state and navigation ----------
  const S = { lab: null, view: 'lab', params: {}, editing: false, lastCheck: null, trailSince: 0 };
  const N = () => S.lab.active.names;
  const P = (k) => S.params[k] || N()[k];           // a route parameter, falling back to the scenario's resource
  const NAV = [
    ['ECS Lab', [['lab', 'Lab brief', ['lab']], ['scenarios', 'Scenarios', ['scenarios', 'history']]]],
    ['Elastic Container Service', [['clusters', 'Clusters', ['clusters', 'cluster', 'service', 'task', 'update', 'runtask']],
      ['families', 'Task definitions', ['families', 'family', 'taskdef', 'tdnew', 'tdform']]]],
    ['EC2', [['tgs', 'Target groups', ['tgs', 'tg', 'tgedit']], ['sgs', 'Security groups', ['sgs', 'sg', 'sgedit']]]],
    ['CloudWatch · Logs', [['loggroups', 'Log groups', ['loggroups', 'loggroup', 'logstream']]]],
    ['IAM', [['roles', 'Roles', ['roles', 'role', 'roleedit']]]],
    ['Secrets Manager', [['secrets', 'Secrets', ['secrets', 'secret']]]],
    ['Systems Manager', [['params', 'Parameter Store', ['params', 'param']]]],
    ['AWS Cloud Map', [['namespaces', 'Namespaces', ['namespaces']]]],
    ['Amazon S3', [['buckets', 'Buckets', ['buckets']]]],
  ];
  function renderNav() {
    const nav = $('nav'); nav.replaceChildren();
    const active = S.lab && S.lab.active;
    for (const [group, items] of NAV) {
      nav.append(h('div', { class: 'group' }, group));
      for (const [id, label, owns] of items) {
        const on = owns.includes(S.view), off = id !== 'lab' && id !== 'scenarios' && !active;
        nav.append(h('a', { href: '#', class: (on ? 'on' : '') + (off ? ' off' : ''), 'aria-current': on ? 'page' : null,
          onclick: (e) => { e.preventDefault(); if (off) toast('Start a scenario first.'); else go(id, {}); } }, label));
      }
    }
  }
  function renderHeader() {
    const a = S.lab && S.lab.active;
    $('scenario-title').textContent = a ? a.title : 'No scenario running';
    for (const id of ['btn-hint', 'btn-check', 'btn-reset']) $(id).hidden = !a;
  }
  function tick() {
    const a = S.lab && S.lab.active;
    $('clock').textContent = a ? clock((a.solvedAt || Date.now() / 1000) - a.startedAt) : '';
  }
  async function go(view, params, user = true) {
    // Searches, page numbers and selections belong to the page you are on; leaving it clears them.
    if (user && view !== S.view) for (const k of Object.keys(UI)) delete UI[k];
    S.view = view; S.params = params || {}; S.editing = false;
    renderNav();
    await load(user);
    if (user) { $('main').scrollTop = 0; $('main').focus({ preventScroll: true }); }
  }
  let loadSeq = 0;
  async function load(user) {
    const target = $('view');
    const mine = user ? ++loadSeq : loadSeq, view = S.view;
    const stale = () => mine !== loadSeq || view !== S.view;   // a newer navigation has taken over
    try {
      const node = await VIEWS[view](user ? '1' : '0');
      if (stale()) return;
      if (!S.editing || user) target.replaceChildren(node);
    } catch (e) {
      if (stale() || !user) return;   // a failed background refresh keeps the last good page
      target.replaceChildren(h('div', { class: 'flash bad' }, h('div', {}, h('b', {}, 'This page could not be loaded'), h('div', {}, e.message))));
    }
  }
  const reload = () => load(true);
  async function refreshLab() { S.lab = await api('/api/state'); renderHeader(); renderNav(); tick(); }
  const typing = () => { const a = document.activeElement; return a && $('main').contains(a) && (a.tagName === 'TEXTAREA' || a.tagName === 'SELECT' || (a.tagName === 'INPUT' && a.type !== 'radio' && a.type !== 'checkbox')); };

  // ---------- shared pieces ----------
  const ECS = ['Amazon Elastic Container Service'];
  const tdLink = (name) => (name ? link(name, 'taskdef', { name }) : '-');
  const progress = (running, desired) => h('div', { class: 'prog' },
    h('div', { class: 'bar' }, h('i', { style: `width:${desired ? Math.min(100, (running / desired) * 100) : 0}%` })),
    h('span', {}, `${running ?? 0}/${desired ?? 0} tasks running`));
  const primary = (s) => (s.deployments || []).find((d) => d.status === 'PRIMARY') || {};
  const lastDeployment = (s) => { const r = primary(s).rolloutState; return r ? ind(r, cap(r)) : '-'; };

  async function stopTask(cluster, id, after) {
    const ok = await confirmDialog({ title: 'Stop task', danger: true, confirm: 'Stop',
      body: [h('p', {}, 'Stop task ', mono(id), '?'),
        h('p', { class: 'muted' }, 'Its containers are sent a stop signal and then killed. If the task belongs to a service, ' +
          'the service launches a replacement to get back to its desired count.')] });
    if (!ok) return;
    try {
      await api('/api/aws/stop-task', { cluster, id });
      flash('ok', 'Task stop requested', `Task ${id} is being stopped.`);
    } catch (e) { flash('bad', 'The task could not be stopped', e.message); }
    await (after ? after() : reload());
  }

  async function tasksPanel(user, route, base, cluster, service) {
    const status = S.params.status || 'RUNNING';
    const d = await get('tasks', { cluster, service, status }, user);
    const filter = h('select', { id: 'f-status', onchange: (e) => { delete UI['tasks-' + route]; go(route, { ...base, tab: 'tasks', status: e.target.value }); } },
      h('option', { value: 'RUNNING', selected: status === 'RUNNING' }, 'Running'),
      h('option', { value: 'STOPPED', selected: status === 'STOPPED' }, 'Stopped'));
    const stopped = status === 'STOPPED';
    return dataTable({ key: 'tasks-' + route, title: 'Tasks', total: d.more ? d.total + '+' : d.total, select: true,
      heads: stopped ? ['Task', 'Last status', 'Desired status', 'Task definition', 'Stopped at', 'Stopped reason']
        : ['Task', 'Last status', 'Desired status', 'Task definition', 'Health status', 'Started at'],
      rows: d.tasks.map((t) => ({ id: t.id, cells: stopped
        ? [link(mono(t.id), 'task', { cluster, id: t.id }), ind(t.lastStatus), cap(t.desiredStatus), tdLink(t.taskDefinition), ago(t.stoppedAt), t.stoppedReason]
        : [link(mono(t.id), 'task', { cluster, id: t.id }), ind(t.lastStatus), cap(t.desiredStatus), tdLink(t.taskDefinition), ind(t.healthStatus), ago(t.startedAt)] })),
      empty: 'No tasks', find: 'Find tasks',
      extra: [h('label', { class: 'inline', for: 'f-status' }, 'Filter desired status'), filter],
      actions: (sel) => [h('button', { class: 'btn', disabled: !sel || stopped, onclick: () => stopTask(cluster, sel.id) }, 'Stop'),
        !service ? h('button', { class: 'btn primary', onclick: () => go('runtask', { cluster }) }, 'Run new task') : null],
      note: d.more ? 'Showing the 100 most recent tasks.' : null });
  }
  const eventsTable = (key, events) => dataTable({ key, title: 'Log events', heads: ['Timestamp', 'Message'], pageSize: 25, find: 'Filter events',
    rows: events.map((e, i) => ({ id: i, cells: [h('span', { class: 'nowrap' }, when(e.at)), mono(e.message)] })), empty: 'No log events' });

  // ---------- pages ----------
  const VIEWS = {
    async lab() {
      const a = S.lab && S.lab.active;
      if (!a) return picker();
      const n = a.names;
      const mine = (S.lab.scenarios || []).find((x) => x.id === a.id) || {};
      const out = h('div', {}, h('div', { class: 'head' }, h('h1', {}, a.title),
        a.solvedAt ? ind('COMPLETED', 'Solved') : ind('IN_PROGRESS', 'In progress'), h('span', { class: 'spacer' }),
        h('span', { class: 'muted small' }, stars(a.difficulty), ` · about ${a.minutes} minutes`),
        mine.attempts ? h('button', { class: 'btn', onclick: () => go('history', { scenario: a.id }) }, 'History') : null,
        h('button', { class: 'btn', onclick: (e) => restartScenario(a, e.target) }, 'Restart scenario')));
      if (S.lastCheck) out.append(checkResult(S.lastCheck));
      else if (a.solvedAt) out.append(h('div', { class: 'flash ok' }, h('div', {}, h('b', {}, 'Solved'),
        h('div', {}, 'Press Check my fix to see your replay again, or pick the next scenario.'))));
      out.append(h('section', { class: 'box hero' }, h('h2', {}, 'Your task'), h('pre', { class: 'brief' }, a.brief.trim())),
        box('Where things are', fields(
          ['Cluster', link(n.cluster, 'cluster', { cluster: n.cluster })], ['Service', link(n.service, 'service', { cluster: n.cluster, service: n.service })],
          ['Task definition family', link(n.family, 'family', { family: n.family })], ['Target group', link(n.target_group, 'tg', { name: n.target_group })],
          ['Log group', link(n.log_group, 'loggroup', { group: n.log_group })])),
        box('How to work', h('p', { class: 'pad' }, 'The pages work like the AWS consoles: the same paths, tabs and field names, tables you ' +
          'search and select from, and banners that report what an action did. You can also type AWS CLI commands in the terminal below. ' +
          'Both do the same thing and both count in your trail. When you think it is fixed, press Check my fix.')));
      if (a.hints.length) out.append(box(`Hints (${a.hints.length} of ${a.hintsTotal})`, h('ol', { class: 'hints pad' }, a.hints.map((t) => h('li', {}, t)))));
      if (a.solvedAt) out.append(solutionBox());
      return out;
    },

    async scenarios() { return picker(); },

    async history() {
      const id = S.params.scenario;
      const d = await api('/api/progress?scenario=' + encodeURIComponent(id));
      const sc = ((S.lab && S.lab.scenarios) || []).find((x) => x.id === id) || { title: id };
      const best = d.bestSeconds;
      return page({ crumbs: [['ECS Lab'], ['Scenarios', 'scenarios'], [sc.title]], title: sc.title,
        status: d.solved ? ind('COMPLETED', 'Solved') : ind('', 'Not solved yet') },
      box('Your attempts', fields(['Scenario', mono(id)], ['Times solved', String(d.attempts.length)], ['Best time', best != null ? dur(best) : '-'])),
      d.attempts.length ? d.attempts.map((t, i) => h('section', { class: 'box' },
        h('div', { class: 'box-h' }, h('h2', {}, when(t.at)), t.seconds === best ? h('span', { class: 'chip good' }, 'Best') : null, h('span', { class: 'spacer' }),
          h('span', { class: 'muted small' }, `${dur(t.seconds)} · ${t.calls} AWS call${t.calls === 1 ? '' : 's'} · ${t.hints} hint${t.hints === 1 ? '' : 's'}`)),
        h('details', { class: 'pad', open: i === 0 }, h('summary', {}, 'Replay'), h('pre', { class: 'block' }, t.replay || 'No replay was recorded.'))))
        : h('p', { class: 'muted' }, 'Nothing here yet. Solved attempts are kept on this machine, with their replays, so you can compare them.'));
    },

    // ----- ECS -----
    async clusters(user) {
      const d = await get('clusters', {}, user);
      return page({ crumbs: [ECS, ['Clusters']], title: 'Clusters' },
        dataTable({ key: 'clusters', title: 'Clusters', heads: ['Cluster', 'Services', 'Tasks', 'Status'], empty: 'No clusters',
          rows: d.clusters.map((c) => ({ id: c.name, cells: [link(c.name, 'cluster', { cluster: c.name }), c.services ?? 0,
            `${c.pendingTasks ?? 0} Pending | ${c.runningTasks ?? 0} Running`, ind(c.status)] })) }));
    },

    async cluster(user) {
      const cluster = P('cluster'), tab = S.params.tab || 'services';
      const c = await get('cluster', { cluster }, user);
      let body;
      if (tab === 'tasks') body = await tasksPanel(user, 'cluster', { cluster }, cluster, null);
      else {
        const d = await get('services', { cluster }, user);
        body = dataTable({ key: 'services', title: 'Services', select: true, empty: 'No services',
          heads: ['Service name', 'Status', 'Service type', 'Deployments and tasks', 'Last deployment', 'Task definition', 'Launch type'],
          rows: d.services.map((s) => ({ id: s.name, cells: [link(s.name, 'service', { cluster, service: s.name }), ind(s.status), cap(s.schedulingStrategy),
            progress(s.runningCount, s.desiredCount), lastDeployment(s), tdLink(s.taskDefinition), s.launchType || 'FARGATE'] })),
          actions: (sel) => h('button', { class: 'btn', disabled: !sel, onclick: () => go('update', { cluster, service: sel.id }) }, 'Update') });
      }
      return page({ crumbs: [ECS, ['Clusters', 'clusters'], [c.name]], title: c.name },
        box('Cluster overview', fields(['ARN', mono(c.arn)], ['Status', ind(c.status)], ['Services', `${c.services ?? 0} Active`],
          ['Tasks', `${c.pendingTasks ?? 0} Pending | ${c.runningTasks ?? 0} Running`])),
        tabs([['services', 'Services'], ['tasks', 'Tasks']], tab, (t) => go('cluster', { cluster, tab: t })), body);
    },

    async service(user) {
      const cluster = P('cluster'), service = P('service'), tab = S.params.tab || 'health';
      const here = { cluster, service };
      const s = await get('service', here, user);
      const base = [ECS, ['Clusters', 'clusters'], [cluster, 'cluster', { cluster }], [service]];
      if (s.missing) return page({ crumbs: base, title: service }, h('div', { class: 'flash bad' }, h('div', {}, h('b', {}, 'The service does not exist'))));
      let body;
      if (tab === 'tasks') body = await tasksPanel(user, 'service', here, cluster, service);
      else if (tab === 'logs') {
        const d = await get('service-logs', here, user);
        body = [d.error ? h('div', { class: 'flash warn' }, h('div', {}, d.error)) : null,
          dataTable({ key: 'svc-logs', title: 'Logs', heads: ['Timestamp', 'Message', 'Task'], pageSize: 25, find: 'Filter events', empty: 'No log events',
            rows: d.events.map((e, i) => ({ id: i, cells: [h('span', { class: 'nowrap' }, when(e.at)), mono(e.message),
              link(mono(e.task.slice(0, 12)), 'task', { cluster, id: e.task, tab: 'logs' })] })) })];
      } else if (tab === 'deployments') {
        body = box(['Deployments', s.deployments.length], plainTable(
          ['Deployment ID', 'Status', 'Task definition', 'Rollout state', 'Desired', 'Running', 'Failed', 'Updated at'],
          s.deployments.map((d) => [d.id ? mono(d.id) : '-', ind(d.status), tdLink(d.taskDefinition), d.rolloutState ? ind(d.rolloutState) : '-',
            d.desiredCount, d.runningCount, d.failedTasks, ago(d.updatedAt)]), 'No deployments'));
      } else if (tab === 'events') {
        body = dataTable({ key: 'svc-events', title: 'Events', heads: ['Started at', 'Message'], empty: 'No events',
          rows: s.events.map((e, i) => ({ id: i, cells: [h('span', { class: 'nowrap' }, when(e.at)), e.message] })) });
      } else if (tab === 'config') {
        body = [box('Service configuration', fields(['Service ARN', mono(s.arn)], ['Launch type', s.launchType || 'FARGATE'],
          ['Service type', cap(s.schedulingStrategy)], ['Health check grace period', (s.healthCheckGracePeriodSeconds ?? 0) + ' seconds'],
          ['Created at', when(s.createdAt)])),
        box('Load balancing', plainTable(['Target group', 'Container name', 'Container port'],
          s.loadBalancers.map((l) => [link(l.targetGroup.join(''), 'tg', { name: l.targetGroup.join('') }), l.container, l.port]), 'No load balancers')),
        box('Network configuration', fields(['Subnets', s.network ? (s.network.subnets || []).join(', ') : '-'],
          ['Security groups', s.network ? (s.network.securityGroups || []).map((id, i) => [i ? ', ' : '', link(mono(id), 'sg', { id })]) : '-'],
          ['Auto-assign public IP', s.network ? cap(s.network.assignPublicIp) : '-']))];
      } else {
        const tgName = s.loadBalancers.length ? s.loadBalancers[0].targetGroup.join('') : null;
        const t = tgName ? await get('targets', { name: tgName }, user) : null;
        body = [box('Load balancer health', !t ? h('p', { class: 'muted pad' }, 'This service has no load balancer.')
          : t.missing ? h('div', { class: 'flash bad' }, h('div', {}, `Target group ${t.name} does not exist.`))
            : [fields(['Target group', link(t.name, 'tg', { name: t.name })], ['Healthy targets', `${t.healthy} of ${t.total}`],
              ['Health check path', t.healthCheck.path], ['Protocol : Port', `${t.protocol} : ${t.port}`]),
            plainTable(['Target', 'Port', 'Health status', 'Health status details'], t.targets.map((x) =>
              [mono(x.id), x.port, ind(x.state), [x.reason, x.description].filter(Boolean).join(': ')]), 'No registered targets')]),
        box('Metrics', h('p', { class: 'muted pad' }, 'CPU and memory utilization graphs are not part of this lab.'))];
      }
      return page({ crumbs: base, title: s.name,
        actions: h('button', { class: 'btn primary', onclick: () => go('update', here) }, 'Update service') },
      box('Service overview', fields(['Status', ind(s.status)],
        ['Tasks', h('span', {}, `${s.desiredCount} Desired`, h('br'), `${s.pendingCount ?? 0} Pending | ${s.runningCount ?? 0} Running`)],
        ['Task definition: revision', tdLink(s.taskDefinition)], ['Deployment status', lastDeployment(s)])),
      tabs([['health', 'Health and metrics'], ['tasks', 'Tasks'], ['logs', 'Logs'], ['deployments', 'Deployments'], ['events', 'Events'],
        ['config', 'Configuration and networking']], tab, (t) => go('service', { ...here, tab: t })), body);
    },

    async task(user) {
      const cluster = P('cluster'), tab = S.params.tab || 'configuration';
      const t = await get('task', { cluster, id: S.params.id }, user);
      let body;
      if (tab === 'logs') {
        if (!t.logStream) body = h('div', { class: 'flash warn' }, h('div', {}, 'This task definition does not send logs to CloudWatch Logs.'));
        else {
          const d = await get('logs', { group: t.logGroup, stream: t.logStream }, user).catch((e) => ({ events: [], error: e.message }));
          body = [d.error ? h('div', { class: 'flash warn' }, h('div', {}, d.error)) : null,
            h('p', { class: 'muted small' }, 'Log stream ', link(mono(`${t.logGroup} / ${t.logStream}`), 'logstream', { group: t.logGroup, stream: t.logStream })),
            eventsTable('task-logs', d.events)];
        }
      } else {
        body = [box('Configuration', fields(['Launch type', t.launchType || 'FARGATE'], ['Task definition: revision', tdLink(t.taskDefinition)],
          ['CPU | Memory', t.cpu || t.memory ? `${dash(t.cpu)} | ${dash(t.memory)}` : '-'], ['Task group', t.group])),
        box(['Containers', t.containers.length], plainTable(['Container name', 'Image URI', 'Status', 'Health status', 'Exit code', 'Reason'],
          t.containers.map((c) => [c.name, c.image ? mono(c.image) : '-', ind(c.lastStatus), ind(c.healthStatus),
            c.exitCode == null ? '-' : String(c.exitCode), c.reason]), 'No containers'))];
      }
      const running = t.lastStatus !== 'STOPPED' && t.desiredStatus !== 'STOPPED';
      return page({ crumbs: [ECS, ['Clusters', 'clusters'], [cluster, 'cluster', { cluster }], ['Tasks', 'cluster', { cluster, tab: 'tasks' }], [t.id.slice(0, 12)]],
        title: t.id, actions: h('button', { class: 'btn', disabled: !running, onclick: () => stopTask(cluster, t.id) }, 'Stop') },
      t.stoppedReason ? h('div', { class: 'flash bad' }, h('div', {}, h('b', {}, 'Stopped reason'), h('div', {}, t.stoppedReason))) : null,
      box('Task overview', fields(['ARN', mono(t.arn)], ['Last status', ind(t.lastStatus)], ['Desired status', cap(t.desiredStatus)],
        ['Created at', when(t.createdAt)], ['Started at', when(t.startedAt)], t.stoppedAt && ['Stopped at', when(t.stoppedAt)],
        t.stopCode && ['Stop code', t.stopCode], t.stoppedReason && ['Stopped reason', t.stoppedReason])),
      tabs([['configuration', 'Configuration'], ['logs', 'Logs']], tab, (x) => go('task', { cluster, id: t.id, tab: x })), body);
    },

    async update() {
      const cluster = P('cluster'), service = P('service'), here = { cluster, service };
      const s = await get('service', here, '0');
      const family = s.taskDefinition.split(':')[0];
      const [list, sgs] = await Promise.all([get('taskdefs', { family }, '0'), get('sgs', {}, '0')]);
      S.editing = true;
      const inGroup = new Set((s.network && s.network.securityGroups) || []);
      const groupBoxes = sgs.groups.map((g) => h('input', { type: 'checkbox', value: g.id, checked: inGroup.has(g.id), id: 'f-sg-' + g.id }));
      const current = S.params.pick && S.params.pick.split(':')[0] === family ? S.params.pick : s.taskDefinition;
      const rev = h('select', { id: 'f-rev' }, list.revisions.map((r, i) => h('option', { value: r, selected: r === current },
        r.split(':').pop() + (i === 0 ? ' (LATEST)' : ''))));
      const count = h('input', { id: 'f-count', type: 'number', min: '0', max: '10', value: String(s.desiredCount) });
      const force = h('input', { id: 'f-force', type: 'checkbox' });
      const err = h('div', {});
      const save = h('button', { class: 'btn primary', onclick: async () => {
        const n = Number(count.value);
        if (!Number.isInteger(n) || n < 0) { err.replaceChildren(h('div', { class: 'flash bad' }, h('div', {}, 'Desired tasks must be a whole number, 0 or more.'))); return; }
        if (n === 0 && !(await confirmDialog({ title: 'Set desired tasks to 0', danger: true, confirm: 'Update',
          body: h('p', {}, 'With 0 desired tasks the service stops every task and serves no traffic.') }))) return;
        save.disabled = true; err.replaceChildren();
        try {
          const picked = groupBoxes.filter((b) => b.checked).map((b) => b.value);
          if (!picked.length) { err.replaceChildren(h('div', { class: 'flash bad' }, h('div', {}, 'Choose at least one security group.'))); save.disabled = false; return; }
          await api('/api/aws/update-service', { ...here, taskDefinition: rev.value, desiredCount: n, forceNewDeployment: force.checked, securityGroups: picked });
          flash('ok', `Service ${service} updated`, `A deployment of ${rev.value} has started.`);
          await go('service', { ...here, tab: 'deployments' });
        } catch (e) { err.replaceChildren(h('div', { class: 'flash bad' }, h('div', {}, h('b', {}, 'The service could not be updated'), h('div', {}, e.message)))); save.disabled = false; }
      } }, 'Update');
      return page({ crumbs: [ECS, ['Clusters', 'clusters'], [cluster, 'cluster', { cluster }], [service, 'service', here], ['Update']], title: 'Update ' + service },
        box('Deployment configuration', h('div', { class: 'form' },
          h('label', { class: 'check' }, force, h('span', {}, h('b', {}, 'Force new deployment'),
            help('Start a new deployment even if nothing in the service definition changed. Tasks are replaced with fresh ones.'))),
          h('label', { for: 'f-family' }, 'Task definition family'), h('input', { id: 'f-family', value: family, disabled: true }),
          help('The family this service runs. To change what runs, pick a different revision.'),
          h('label', { for: 'f-rev' }, 'Revision'), rev, help('The service replaces its tasks with tasks from this revision.'),
          h('label', { for: 'f-count' }, 'Desired tasks'), count, help('The number of tasks to launch and keep running.'))),
        box('Networking', h('div', { class: 'form' }, h('div', { class: 'fl', style: 'margin-top:12px' }, 'Security groups'),
          sgs.groups.map((g, i) => h('label', { class: 'check', for: 'f-sg-' + g.id }, groupBoxes[i], h('span', {}, h('b', {}, g.name), ' ', mono(g.id),
            help(g.description || '')))),
          help('The security groups the service\'s tasks are placed in. Changing them replaces the running tasks.'))),
        err, h('div', { class: 'row end' }, h('button', { class: 'btn', onclick: () => go('service', here) }, 'Cancel'), save));
    },

    async runtask() {
      const cluster = P('cluster');
      const fam = await get('families', {}, '0');
      S.editing = true;
      const family = h('select', { id: 'f-fam' }, fam.families.map((f) => h('option', { value: f, selected: f === N().family }, f)));
      const rev = h('select', { id: 'f-rev' });
      const fill = async () => {
        const list = await get('taskdefs', { family: family.value }, '0');
        rev.replaceChildren(...list.revisions.map((r, i) => h('option', { value: r }, r.split(':').pop() + (i === 0 ? ' (LATEST)' : ''))));
      };
      family.addEventListener('change', fill); await fill();
      const count = h('input', { id: 'f-count', type: 'number', min: '1', max: '10', value: '1' });
      const err = h('div', {});
      const save = h('button', { class: 'btn primary', onclick: async () => {
        save.disabled = true; err.replaceChildren();
        try {
          const r = await api('/api/aws/run-task', { cluster, taskDefinition: rev.value, count: Number(count.value) });
          if (r.failures.length) flash('bad', 'Some tasks failed to launch', r.failures.join('; '));
          if (r.tasks.length) flash('ok', `${r.tasks.length} task${r.tasks.length === 1 ? '' : 's'} launched`, r.tasks.join(', '));
          await go('cluster', { cluster, tab: 'tasks' });
        } catch (e) { err.replaceChildren(h('div', { class: 'flash bad' }, h('div', {}, h('b', {}, 'The task could not be run'), h('div', {}, e.message)))); save.disabled = false; }
      } }, 'Create');
      return page({ crumbs: [ECS, ['Clusters', 'clusters'], [cluster, 'cluster', { cluster }], ['Run task']], title: 'Run task' },
        box('Deployment configuration', h('div', { class: 'form' },
          h('p', { class: 'muted' }, 'A standalone task runs once and is not replaced if it stops. It does not belong to any service.'),
          h('label', { for: 'f-fam' }, 'Task definition family'), family,
          h('label', { for: 'f-rev' }, 'Revision'), rev,
          h('label', { for: 'f-count' }, 'Desired tasks'), count, help('The number of tasks to launch, 1 to 10.'))),
        err, h('div', { class: 'row end' }, h('button', { class: 'btn', onclick: () => go('cluster', { cluster, tab: 'tasks' }) }, 'Cancel'), save));
    },

    async families(user) {
      const d = await get('families', {}, user);
      return page({ crumbs: [ECS, ['Task definitions']], title: 'Task definitions' },
        dataTable({ key: 'families', title: 'Task definitions', heads: ['Task definition', 'Status of last revision'], empty: 'No task definitions',
          rows: d.families.map((f) => ({ id: f, cells: [link(f, 'family', { family: f }), ind('ACTIVE')] })) }));
    },

    async family(user) {
      const family = P('family');
      const list = await get('taskdefs', { family }, user);
      return page({ crumbs: [ECS, ['Task definitions', 'families'], [family]], title: family },
        dataTable({ key: 'revisions', title: 'Revisions', select: true, heads: ['Task definition: revision', 'Status'], empty: 'No revisions',
          rows: list.revisions.map((r) => ({ id: r, cells: [tdLink(r), ind('ACTIVE')] })),
          actions: (sel) => [h('button', { class: 'btn', disabled: !sel, onclick: () => go('update', { pick: sel.id }) }, 'Deploy: Update service'),
            h('button', { class: 'btn', disabled: !sel, onclick: () => go('tdnew', { name: sel.id }) }, 'Create new revision with JSON'),
            h('button', { class: 'btn primary', disabled: !sel, onclick: () => go('tdform', { name: sel.id }) }, 'Create new revision')] }));
    },

    async taskdef(user) {
      const tab = S.params.tab || 'containers';
      const td = await get('taskdef', { name: S.params.name }, user);
      const r = td.registerable;
      const opt = (c, k) => (c.logConfiguration && c.logConfiguration.options ? c.logConfiguration.options[k] : '-');
      const full = (c) => (r.containerDefinitions || []).find((x) => x.name === c.name) || {};
      const body = tab === 'json' ? box('JSON', h('pre', { class: 'block' }, JSON.stringify(r, null, 2)))
        : td.containers.map((c) => { const f = full(c), secrets = f.secrets || []; return box('Container: ' + c.name,
          fields(['Image URI', mono(c.image)], ['Essential', f.essential === false ? 'No' : 'Yes'],
            ['Port mappings', c.portMappings.map((p) => `${p.containerPort}/${p.protocol || 'tcp'}`).join(', ')],
            ['Command', f.command ? mono(f.command.join(',')) : '-'], ['User', f.user], ['Memory hard limit', f.memory ? f.memory + ' MiB' : '-'],
            ['Read only root file system', f.readonlyRootFilesystem ? 'Yes' : 'No'],
            (f.dependsOn || []).length && ['Startup dependency ordering', f.dependsOn.map((x) => `${x.containerName}: ${x.condition}`).join(', ')],
            ['Log driver', c.logConfiguration ? c.logConfiguration.logDriver : '-'],
            ['awslogs-group', opt(c, 'awslogs-group')], ['awslogs-stream-prefix', opt(c, 'awslogs-stream-prefix')]),
          h('h3', { class: 'sub' }, `Environment variables (${c.environment.length + secrets.length})`),
          plainTable(['Key', 'Value type', 'Value'], c.environment.map((e) => [mono(e.name), 'Value', mono(e.value)])
            .concat(secrets.map((e) => [mono(e.name), 'ValueFrom', mono(e.valueFrom)])), 'No environment variables')); });
      return page({ crumbs: [ECS, ['Task definitions', 'families'], [r.family, 'family', { family: r.family }], ['Revision ' + td.revision]],
        title: td.name, status: ind(td.status || 'ACTIVE'),
        actions: [h('button', { class: 'btn', onclick: () => go('update', { pick: td.name }) }, 'Deploy: Update service'),
          h('button', { class: 'btn', onclick: () => go('tdnew', { name: td.name }) }, 'Create new revision with JSON'),
          h('button', { class: 'btn primary', onclick: () => go('tdform', { name: td.name }) }, 'Create new revision')] },
      box('Overview', fields(['ARN', td.arn ? mono(td.arn) : '-'], ['Status', ind(td.status || 'ACTIVE')],
        ['App environment', (r.requiresCompatibilities || []).join(', ')],
        ['Task size', r.cpu || r.memory ? `${dash(r.cpu)} CPU | ${dash(r.memory)} MiB` : '-'], ['Network mode', r.networkMode],
        ['Task role', r.taskRoleArn ? link(r.taskRoleArn.split('/').pop(), 'role', { name: r.taskRoleArn.split('/').pop() }) : '-'],
        ['Task execution role', r.executionRoleArn ? r.executionRoleArn.split('/').pop() : '-'])),
      tabs([['containers', 'Containers'], ['json', 'JSON']], tab, (t) => go('taskdef', { name: td.name, tab: t })), body);
    },

    async tdnew() {
      const td = await get('taskdef', { name: S.params.name }, '0');
      S.editing = true;
      const ta = h('textarea', { id: 'f-json', spellcheck: 'false', 'aria-label': 'Task definition JSON' });
      ta.value = JSON.stringify(td.registerable, null, 2);
      const err = h('div', {});
      const bad = (title, text) => err.replaceChildren(h('div', { class: 'flash bad' }, h('div', {}, h('b', {}, title), h('div', {}, text))));
      const save = h('button', { class: 'btn primary', onclick: async () => {
        let definition;
        try { definition = JSON.parse(ta.value); } catch (e) { bad('That is not valid JSON', e.message); return; }
        save.disabled = true;
        try {
          const r = await api('/api/aws/register-taskdef', { definition });
          flash('ok', `Task definition ${r.name} created`, 'It is not deployed until you update a service to use it.');
          await go('taskdef', { name: r.name });
        } catch (e) { bad('The task definition could not be created', e.message); save.disabled = false; }
      } }, 'Create');
      return page({ crumbs: [ECS, ['Task definitions', 'families'], [td.registerable.family, 'family', { family: td.registerable.family }], ['Create new revision with JSON']],
        title: 'Create new task definition revision with JSON' },
      box('Task definition JSON', h('div', { class: 'form wide' },
        help(`Starts from ${td.name}. Edit the JSON, then choose Create. Creating a revision registers it; it does not deploy anything.`), ta)),
      err, h('div', { class: 'row end' }, h('button', { class: 'btn', onclick: () => go('taskdef', { name: td.name }) }, 'Cancel'), save));
    },

    async tdform() {
      const td = await get('taskdef', { name: S.params.name }, '0');
      const roles = await get('roles', {}, '0').then((d) => d.roles).catch(() => []);
      S.editing = true;
      const def = JSON.parse(JSON.stringify(td.registerable));
      const CPUS = [['256', '.25 vCPU'], ['512', '.5 vCPU'], ['1024', '1 vCPU'], ['2048', '2 vCPU'], ['4096', '4 vCPU']];
      if (def.cpu && !CPUS.some(([v]) => v === String(def.cpu))) CPUS.unshift([String(def.cpu), String(def.cpu) + ' CPU units']);
      const cpu = h('select', { id: 'f-cpu' }, def.cpu ? null : h('option', { value: '' }, 'Not set'),
        CPUS.map(([v, l]) => h('option', { value: v, selected: v === String(def.cpu || '') }, l)));
      const mem = h('input', { id: 'f-mem', type: 'number', min: '0', step: '1', value: def.memory ? String(def.memory) : '' });
      const roleArns = roles.map((x) => x.arn);
      if (def.taskRoleArn && !roleArns.includes(def.taskRoleArn)) roleArns.unshift(def.taskRoleArn);
      const role = h('select', { id: 'f-role' }, h('option', { value: '' }, 'None'),
        roleArns.map((arn) => h('option', { value: arn, selected: arn === def.taskRoleArn }, arn.split('/').pop())));
      const editors = def.containerDefinitions.map((c, i) => {
        const id = (x) => `f-c${i}-${x}`;
        const image = h('input', { id: id('image'), type: 'text', value: c.image || '', spellcheck: 'false' });
        const port = h('input', { id: id('port'), type: 'number', min: '1', max: '65535', value: (c.portMappings || [])[0] ? String(c.portMappings[0].containerPort) : '' });
        const command = h('input', { id: id('cmd'), type: 'text', value: c.command ? JSON.stringify(c.command) : '', spellcheck: 'false', placeholder: '["python", "/app/server.py"]' });
        const user = h('input', { id: id('user'), type: 'text', value: c.user || '', spellcheck: 'false' });
        const memory = h('input', { id: id('memory'), type: 'number', min: '0', value: c.memory ? String(c.memory) : '' });
        const ro = h('input', { id: id('ro'), type: 'checkbox', checked: !!c.readonlyRootFilesystem });
        const env = kvEditor((c.environment || []).map((e) => [e.name, e.value]), 'Key', 'Value', 'environment variable');
        const sec = kvEditor((c.secrets || []).map((e) => [e.name, e.valueFrom]), 'Key', 'ValueFrom (ARN of a parameter or secret)', 'secret');
        const apply = () => {
          if (!image.value.trim()) return `Container ${c.name}: an image is required.`;
          c.image = image.value.trim();
          if (command.value.trim()) {
            let v; try { v = JSON.parse(command.value); } catch (e) { v = null; }
            if (!Array.isArray(v) || !v.every((x) => typeof x === 'string')) return `Container ${c.name}: the command must be a JSON array of strings, for example ["python", "/app/server.py"].`;
            c.command = v;
          } else delete c.command;
          if (user.value.trim()) c.user = user.value.trim(); else delete c.user;
          if (memory.value) c.memory = Number(memory.value); else delete c.memory;
          if (ro.checked) c.readonlyRootFilesystem = true; else delete c.readonlyRootFilesystem;
          if (port.value) {
            const pm = (c.portMappings || [])[0] || { protocol: 'tcp' };
            if ('hostPort' in pm && pm.hostPort === pm.containerPort) pm.hostPort = Number(port.value);
            pm.containerPort = Number(port.value); c.portMappings = [pm].concat((c.portMappings || []).slice(1));
          }
          const names = env.read().map(([k]) => k).concat(sec.read().map(([k]) => k));
          const twice = names.find((k, n) => names.indexOf(k) !== n);
          if (twice) return `Container ${c.name}: ${twice} is set more than once.`;
          c.environment = env.read().map(([name, value]) => ({ name, value }));
          const secrets = sec.read();
          if (secrets.some(([, v]) => !v.trim())) return `Container ${c.name}: every secret needs a ValueFrom.`;
          if (secrets.length) c.secrets = secrets.map(([name, valueFrom]) => ({ name, valueFrom: valueFrom.trim() })); else delete c.secrets;
          return null;
        };
        return { apply, node: box(`Container - ${i + 1}: ${c.name}`, h('div', { class: 'form wide' },
          h('label', { for: id('image') }, 'Image URI'), image,
          h('label', { for: id('port') }, 'Container port'), port, help('The port the application listens on. The load balancer sends traffic and health checks here.'),
          h('label', { for: id('memory') }, 'Memory hard limit (MiB)'), memory, help('The container is killed if it uses more than this. Leave empty for no container-level limit.'),
          h('label', { for: id('cmd') }, 'Command'), command, help('Overrides the image\'s own command. A JSON array, as it appears in the task definition. Leave empty to use the image\'s.'),
          h('label', { for: id('user') }, 'User'), user, help('The user the container runs as. It must exist in the image.'),
          h('label', { class: 'check' }, ro, h('span', {}, h('b', {}, 'Read only root file system'), help('The container cannot write to its own filesystem.'))),
          h('h3', { class: 'sub' }, 'Environment variables'), env.node,
          h('h3', { class: 'sub' }, 'Secrets'), help('The container gets each one as an environment variable. ECS fetches the value when the task starts.'), sec.node)) };
      });
      const err = h('div', {});
      const save = h('button', { class: 'btn primary', onclick: async () => {
        err.replaceChildren();
        if (cpu.value) def.cpu = cpu.value; if (mem.value) def.memory = String(mem.value); else delete def.memory;
        if (role.value) def.taskRoleArn = role.value; else delete def.taskRoleArn;
        const problem = editors.map((e) => e.apply()).find(Boolean);
        if (problem) { err.replaceChildren(errBox('Check the form', problem)); return; }
        save.disabled = true;
        try {
          const r = await api('/api/aws/register-taskdef', { definition: def });
          flash('ok', `Task definition ${r.name} created`, 'It is not deployed until you update a service to use it.');
          await go('taskdef', { name: r.name });
        } catch (e) { err.replaceChildren(errBox('The task definition could not be created', e.message)); save.disabled = false; }
      } }, 'Create');
      return page({ crumbs: [ECS, ['Task definitions', 'families'], [def.family, 'family', { family: def.family }], ['Create new revision']],
        title: 'Create new task definition revision' },
      h('p', { class: 'muted' }, `Starts from ${td.name}. A revision cannot be edited after it is created; a change is always a new revision.`),
      box('Infrastructure requirements', h('div', { class: 'form' },
        h('label', { for: 'f-family' }, 'Task definition family'), h('input', { id: 'f-family', value: def.family, disabled: true }),
        h('label', { for: 'f-cpu' }, 'CPU'), cpu, h('label', { for: 'f-mem' }, 'Memory (MiB)'), mem,
        help('On Fargate, CPU and memory are chosen as a pair. Not every combination exists.'),
        h('label', { for: 'f-role' }, 'Task role'), role, help('The IAM role the application in the task uses when it calls AWS.'))),
      editors.map((e) => e.node),
      err, h('div', { class: 'row end' }, h('button', { class: 'btn', onclick: () => go('taskdef', { name: td.name }) }, 'Cancel'), save));
    },

    // ----- EC2 target groups -----
    async tgs(user) {
      const d = await get('tgs', {}, user);
      return page({ crumbs: [['EC2'], ['Target groups']], title: 'Target groups' },
        dataTable({ key: 'tgs', title: 'Target groups', heads: ['Name', 'Port', 'Protocol', 'Target type', 'Load balancer', 'Healthy / Total'], empty: 'No target groups',
          rows: d.targetGroups.map((t) => ({ id: t.name, cells: [link(t.name, 'tg', { name: t.name }), t.port, t.protocol, cap(t.targetType),
            t.loadBalancers.join(', ') || 'None associated', `${t.healthy} / ${t.total}`] })) }));
    },

    async tg(user) {
      const name = S.params.name || N().target_group, tab = S.params.tab || 'targets';
      const t = await get('targets', { name }, user);
      const base = [['EC2'], ['Target groups', 'tgs'], [name]];
      if (t.missing) return page({ crumbs: base, title: name }, h('div', { class: 'flash bad' }, h('div', {}, h('b', {}, `Target group ${name} does not exist`))));
      const count = (st) => t.targets.filter((x) => x.state === st).length, hc = t.healthCheck;
      const body = tab === 'health'
        ? box('Health check settings', h('div', {}, fields(['Protocol', hc.protocol], ['Path', hc.path], ['Port', cap(hc.port)],
          ['Healthy threshold', `${hc.healthyThreshold} consecutive health check successes`],
          ['Unhealthy threshold', `${hc.unhealthyThreshold} consecutive health check failures`],
          ['Timeout', `${hc.timeoutSeconds} seconds`], ['Interval', `${hc.intervalSeconds} seconds`], ['Success codes', hc.matcher],
          hc.enabled === false && ['Health checks', 'Disabled']),
        h('div', { class: 'row end pad' }, h('button', { class: 'btn primary', onclick: () => go('tgedit', { name }) }, 'Edit'))))
        : dataTable({ key: 'targets', title: 'Registered targets', heads: ['IP address', 'Port', 'Health status', 'Health status details'], empty: 'No registered targets',
          rows: t.targets.map((x) => ({ id: `${x.id}:${x.port}`, cells: [mono(x.id), x.port, ind(x.state), [x.reason, x.description].filter(Boolean).join(': ')] })) });
      return page({ crumbs: base, title: t.name },
        box('Details', fields(['Target type', cap(t.targetType)], ['Protocol : Port', `${t.protocol} : ${t.port}`], ['VPC', t.vpc],
          ['Total targets', String(t.total)], ['Healthy', ind('healthy', String(count('healthy')))],
          ['Unhealthy', count('unhealthy') ? ind('unhealthy', String(count('unhealthy'))) : '0'],
          ['Initial', String(count('initial'))], ['Draining', String(count('draining'))])),
        tabs([['targets', 'Targets'], ['health', 'Health checks']], tab, (x) => go('tg', { name, tab: x })), body);
    },
    async tgedit() {
      const name = S.params.name || N().target_group;
      const t = await get('targets', { name }, '0');
      S.editing = true;
      const hc = t.healthCheck;
      const num = (id, v, lo, hi) => h('input', { id, type: 'number', min: String(lo), max: String(hi), value: v == null ? '' : String(v) });
      const path = h('input', { id: 'f-path', type: 'text', value: hc.path || '/', spellcheck: 'false' });
      const codes = h('input', { id: 'f-codes', type: 'text', value: hc.matcher || '200', spellcheck: 'false' });
      const interval = num('f-int', hc.intervalSeconds, 5, 300), timeout = num('f-to', hc.timeoutSeconds, 2, 120);
      const healthy = num('f-ok', hc.healthyThreshold, 2, 10), unhealthy = num('f-bad', hc.unhealthyThreshold, 2, 10);
      const err = h('div', {});
      const back = () => go('tg', { name, tab: 'health' });
      const save = h('button', { class: 'btn primary', onclick: async () => {
        save.disabled = true; err.replaceChildren();
        try {
          await api('/api/aws/tg-health', { name, path: path.value, matcher: codes.value, interval: interval.value, timeout: timeout.value,
            healthy: healthy.value, unhealthy: unhealthy.value });
          flash('ok', 'Successfully modified health check settings', `for target group ${name}. Targets are re-evaluated on the next checks.`);
          await back();
        } catch (e) { err.replaceChildren(errBox('The health check settings could not be saved', e.message)); save.disabled = false; }
      } }, 'Save changes');
      return page({ crumbs: [['EC2'], ['Target groups', 'tgs'], [name, 'tg', { name, tab: 'health' }], ['Edit health check settings']], title: 'Edit health check settings' },
        box('Health checks', h('div', { class: 'form' },
          help('The load balancer sends this request to every registered target and uses the answer to decide whether to send it traffic.'),
          h('label', { for: 'f-proto' }, 'Health check protocol'), h('input', { id: 'f-proto', value: hc.protocol || 'HTTP', disabled: true }),
          h('label', { for: 'f-path' }, 'Health check path'), path, help('The path the request is sent to on each target, for example /health.'))),
        box('Advanced health check settings', h('div', { class: 'form' },
          h('label', { for: 'f-ok' }, 'Healthy threshold'), healthy, help('Consecutive successes before an unhealthy target is considered healthy. 2 to 10.'),
          h('label', { for: 'f-bad' }, 'Unhealthy threshold'), unhealthy, help('Consecutive failures before a target is considered unhealthy. 2 to 10.'),
          h('label', { for: 'f-to' }, 'Timeout'), timeout, help('Seconds without an answer before a check counts as failed.'),
          h('label', { for: 'f-int' }, 'Interval'), interval, help('Seconds between checks of one target.'),
          h('label', { for: 'f-codes' }, 'Success codes'), codes, help('The HTTP codes that count as healthy: one code (200), a list (200,202) or a range (200-299).'))),
        err, h('div', { class: 'row end' }, h('button', { class: 'btn', onclick: back }, 'Cancel'), save));
    },

    // ----- Cloud Map and S3 -----
    async namespaces(user) {
      const d = await get('namespaces', {}, user);
      const rows = d.records.filter((r) => r.service);
      return page({ crumbs: [['AWS Cloud Map'], ['Namespaces']], title: 'Namespaces' },
        dataTable({ key: 'cloudmap', title: 'Services', heads: ['Service name', 'Namespace', 'DNS name', 'Registered instances'], empty: 'No services',
          rows: rows.map((r) => ({ id: r.dns, cells: [r.service, r.namespace, mono(r.dns),
            r.instances.length ? r.instances.map((i, n) => [n ? ', ' : '', mono(`${dash(i.ip)}${i.port ? ':' + i.port : ''}`)]) : ind('', 'None registered')] })),
          note: 'A service with no registered instances has no DNS answer: callers get a name resolution error, not a refused connection.' }),
        d.records.some((r) => !r.service) ? h('p', { class: 'muted small' }, 'Namespaces with no services: ', d.records.filter((r) => !r.service).map((r) => r.namespace).join(', ')) : null);
    },
    async buckets(user) {
      const d = await get('buckets', {}, user);
      return page({ crumbs: [['Amazon S3'], ['Buckets']], title: 'Buckets' },
        dataTable({ key: 'buckets', title: 'General purpose buckets', heads: ['Name', 'ARN', 'Creation date'], empty: 'No buckets',
          rows: d.buckets.map((b) => ({ id: b.name, cells: [b.name, mono('arn:aws:s3:::' + b.name), when(b.createdAt)] })),
          note: 'Bucket contents are not part of this lab. What matters here is the exact bucket name, because IAM policies refer to it.' }));
    },

    // ----- EC2 security groups -----
    async sgs(user) {
      const d = await get('sgs', {}, user);
      return page({ crumbs: [['EC2'], ['Security Groups']], title: 'Security Groups' },
        dataTable({ key: 'sgs', title: 'Security Groups', heads: ['Security group ID', 'Security group name', 'VPC ID', 'Description', 'Inbound rules count'],
          empty: 'No security groups', rows: d.groups.map((g) => ({ id: g.id, cells: [link(mono(g.id), 'sg', { id: g.id }), g.name, g.vpc, g.description,
            `${g.inbound.length} Permission entr${g.inbound.length === 1 ? 'y' : 'ies'}`] })) }));
    },
    async sg(user) {
      const g = await get('sg', { id: S.params.id }, user);
      return page({ crumbs: [['EC2'], ['Security Groups', 'sgs'], [g.id]], title: `${g.id} - ${g.name}` },
        box('Details', fields(['Security group name', g.name], ['Security group ID', mono(g.id)], ['Description', g.description], ['VPC ID', g.vpc],
          ['Inbound rules count', `${g.inbound.length} Permission entr${g.inbound.length === 1 ? 'y' : 'ies'}`])),
        tabs([['inbound', 'Inbound rules']], 'inbound', () => {}),
        dataTable({ key: 'sg-rules', title: 'Inbound rules', heads: ['Type', 'Protocol', 'Port range', 'Source'], empty: 'No inbound rules',
          rows: g.inbound.map((r, i) => ({ id: i, cells: [r.protocol === 'All' ? 'All traffic' : 'Custom TCP', r.protocol, r.ports, mono(r.source)] })),
          actions: () => h('button', { class: 'btn primary', onclick: () => go('sgedit', { id: g.id }) }, 'Edit inbound rules'),
          note: g.inbound.length ? null : 'With no inbound rules, nothing can open a connection to resources in this group.' }));
    },
    async sgedit() {
      const g = await get('sg', { id: S.params.id }, '0');
      S.editing = true;
      const rows = h('div', { class: 'rules' });
      const add = (r) => {
        const from = h('input', { type: 'number', min: '0', max: '65535', 'aria-label': 'From port', value: r && r.fromPort != null ? String(r.fromPort) : '' });
        const to = h('input', { type: 'number', min: '0', max: '65535', 'aria-label': 'To port', value: r && r.toPort != null ? String(r.toPort) : '' });
        const src = h('input', { type: 'text', 'aria-label': 'Source', value: r ? r.source : '0.0.0.0/0' });
        const row = h('div', { class: 'rule' }, h('span', { class: 'muted' }, 'Custom TCP'), from, h('span', { class: 'muted' }, 'to'), to, src,
          h('button', { class: 'btn small', onclick: () => row.remove() }, 'Delete'));
        row.read = () => ({ from_port: from.value, to_port: to.value || from.value, cidr: src.value });
        rows.append(row);
      };
      g.inbound.filter((r) => r.ipProtocol !== '-1').forEach(add);
      const err = h('div', {});
      const save = h('button', { class: 'btn primary', onclick: async () => {
        save.disabled = true; err.replaceChildren();
        try {
          await api('/api/aws/sg-rules', { id: g.id, rules: [...rows.children].map((r) => r.read()) });
          flash('ok', 'Inbound security group rules successfully modified', `on security group ${g.id} (${g.name})`);
          await go('sg', { id: g.id });
        } catch (e) { err.replaceChildren(h('div', { class: 'flash bad' }, h('div', {}, h('b', {}, 'The rules could not be saved'), h('div', {}, e.message)))); save.disabled = false; }
      } }, 'Save rules');
      return page({ crumbs: [['EC2'], ['Security Groups', 'sgs'], [g.id, 'sg', { id: g.id }], ['Edit inbound rules']], title: 'Edit inbound rules' },
        box('Inbound rules', h('div', { class: 'form wide' },
          help('Inbound rules control the incoming traffic that is allowed to reach resources in this group. Port range and source are per rule.'),
          h('div', { class: 'rule rule-h' }, h('span', {}, 'Type'), h('span', {}, 'Port range'), h('span', {}), h('span', {}), h('span', {}, 'Source'), h('span', {})),
          rows, h('p', {}), h('button', { class: 'btn', onclick: () => add(null) }, 'Add rule'))),
        err, h('div', { class: 'row end' }, h('button', { class: 'btn', onclick: () => go('sg', { id: g.id }) }, 'Cancel'), save));
    },

    // ----- IAM -----
    async roles(user) {
      const d = await get('roles', {}, user);
      return page({ crumbs: [['IAM'], ['Roles']], title: 'Roles' },
        dataTable({ key: 'roles', title: 'Roles', heads: ['Role name', 'ARN'], empty: 'No roles',
          rows: d.roles.map((r) => ({ id: r.name, cells: [link(r.name, 'role', { name: r.name }), mono(r.arn)] })) }));
    },
    async role(user) {
      const tab = S.params.tab || 'permissions';
      const r = await get('role', { name: S.params.name }, user);
      const remove = async (p) => {
        if (!(await confirmDialog({ title: `Delete ${p.name}`, danger: true, confirm: 'Delete',
          body: h('p', {}, 'Remove the inline policy ', mono(p.name), ' from ', mono(r.name), '? Anything using this role loses these permissions.') }))) return;
        try { await api('/api/aws/role-policy-delete', { role: r.name, name: p.name }); flash('ok', `Policy ${p.name} deleted`, `from role ${r.name}`); }
        catch (e) { flash('bad', 'The policy could not be deleted', e.message); }
        await reload();
      };
      const body = tab === 'trust' ? box('Trusted entities', h('pre', { class: 'block' }, JSON.stringify(r.trust, null, 2)))
        : [dataTable({ key: 'role-policies', title: 'Permissions policies', select: true, heads: ['Policy name', 'Type'], empty: 'No permissions policies',
          rows: r.policies.map((p) => ({ id: p.name, cells: [p.name, p.type] })),
          actions: (sel) => { const p = sel && r.policies.find((x) => x.name === sel.id); const inline = p && p.type === 'Customer inline'; return [
            h('button', { class: 'btn', disabled: !inline, onclick: () => go('roleedit', { name: r.name, policy: p.name }) }, 'Edit'),
            h('button', { class: 'btn', disabled: !inline, onclick: () => remove(p) }, 'Remove'),
            h('button', { class: 'btn primary', onclick: () => go('roleedit', { name: r.name }) }, 'Add permissions: Create inline policy')]; } }),
        r.policies.filter((p) => p.document).map((p) => box(p.name, h('pre', { class: 'block' }, JSON.stringify(p.document, null, 2))))];
      return page({ crumbs: [['IAM'], ['Roles', 'roles'], [r.name]], title: r.name },
        box('Summary', fields(['ARN', mono(r.arn)], ['Creation date', when(r.createdAt)], ['Permissions policies', String(r.policies.length)])),
        tabs([['permissions', 'Permissions'], ['trust', 'Trust relationships']], tab, (t) => go('role', { name: r.name, tab: t })), body);
    },
    async roleedit() {
      const r = await get('role', { name: S.params.name }, '0');
      S.editing = true;
      const existing = r.policies.find((p) => p.name === S.params.policy);
      const name = h('input', { id: 'f-pname', type: 'text', value: existing ? existing.name : '', disabled: !!existing });
      const ta = h('textarea', { id: 'f-json', spellcheck: 'false', 'aria-label': 'Policy JSON' });
      ta.value = JSON.stringify(existing ? existing.document : { Version: '2012-10-17', Statement: [{ Effect: 'Allow', Action: [], Resource: [] }] }, null, 2);
      const err = h('div', {});
      const bad = (title, text) => err.replaceChildren(h('div', { class: 'flash bad' }, h('div', {}, h('b', {}, title), h('div', {}, text))));
      const save = h('button', { class: 'btn primary', onclick: async () => {
        let document;
        try { document = JSON.parse(ta.value); } catch (e) { bad('That is not valid JSON', e.message); return; }
        save.disabled = true;
        try {
          await api('/api/aws/role-policy', { role: r.name, name: name.value.trim(), document });
          flash('ok', `Policy ${name.value.trim()} ${existing ? 'updated' : 'created'}`, `on role ${r.name}. Running tasks pick the change up on their next request.`);
          await go('role', { name: r.name });
        } catch (e) { bad('The policy could not be saved', e.message); save.disabled = false; }
      } }, existing ? 'Save changes' : 'Create policy');
      return page({ crumbs: [['IAM'], ['Roles', 'roles'], [r.name, 'role', { name: r.name }], [existing ? 'Edit policy' : 'Create policy']],
        title: existing ? `Edit ${existing.name}` : 'Create inline policy' },
      box('Policy editor', h('div', { class: 'form wide' },
        h('label', { for: 'f-pname' }, 'Policy name'), name,
        h('label', { for: 'f-json' }, 'Policy document (JSON)'),
        help('Each statement allows or denies a list of actions on a list of resources. A resource is an ARN, for example arn:aws:s3:::bucket-name.'), ta)),
      err, h('div', { class: 'row end' }, h('button', { class: 'btn', onclick: () => go('role', { name: r.name }) }, 'Cancel'), save));
    },

    // ----- Secrets Manager -----
    async secrets(user) {
      const d = await get('secrets', {}, user);
      return page({ crumbs: [['AWS Secrets Manager'], ['Secrets']], title: 'Secrets' },
        dataTable({ key: 'secrets', title: 'Secrets', heads: ['Secret name', 'Last changed'], empty: 'No secrets',
          rows: d.secrets.map((s) => ({ id: s.name, cells: [link(s.name, 'secret', { name: s.name }), when(s.changedAt)] })) }));
    },
    async secret(user) {
      const reveal = S.params.reveal ? '1' : '';
      const s = await get('secret', { name: S.params.name, reveal }, user);
      const value = !reveal ? h('p', { class: 'muted pad' }, 'The value is not shown until you retrieve it.')
        : s.pairs ? plainTable(['Secret key', 'Secret value'], s.pairs.map((p) => [mono(p.key), mono(p.value)]))
          : h('pre', { class: 'block' }, s.value || '');
      return page({ crumbs: [['AWS Secrets Manager'], ['Secrets', 'secrets'], [s.name]], title: s.name },
        box('Secret details', fields(['Secret name', s.name], ['Secret ARN', mono(s.arn)], ['Last changed', when(s.changedAt)])),
        box(['Secret value', null, h('button', { class: 'btn', onclick: () => go('secret', { name: s.name, reveal: !reveal }) },
          reveal ? 'Close' : 'Retrieve secret value')], value));
    },

    // ----- Systems Manager Parameter Store -----
    async params(user) {
      const d = await get('parameters', {}, user);
      return page({ crumbs: [['AWS Systems Manager'], ['Parameter Store']], title: 'Parameter Store' },
        dataTable({ key: 'params', title: 'My parameters', heads: ['Name', 'Type', 'Last modified'], empty: 'No parameters',
          rows: d.parameters.map((p) => ({ id: p.name, cells: [link(mono(p.name), 'param', { name: p.name }), p.type, when(p.changedAt)] })) }));
    },
    async param(user) {
      const reveal = S.params.reveal ? '1' : '';
      const p = await get('parameter', { name: S.params.name, reveal }, user);
      return page({ crumbs: [['AWS Systems Manager'], ['Parameter Store', 'params'], [p.name]], title: p.name },
        box(['Overview', null, p.type === 'SecureString' ? h('button', { class: 'btn', onclick: () => go('param', { name: p.name, reveal: !reveal }) },
          reveal ? 'Hide decrypted value' : 'Show decrypted value') : null],
        fields(['Name', mono(p.name)], ['Type', p.type], ['ARN', p.arn ? mono(p.arn) : '-'], ['Last modified', when(p.changedAt)],
          ['Value', p.value == null ? '********' : mono(p.value)])));
    },

    // ----- CloudWatch Logs -----
    async loggroups(user) {
      const d = await get('loggroups', {}, user);
      return page({ crumbs: [['CloudWatch'], ['Log groups']], title: 'Log groups' },
        dataTable({ key: 'loggroups', title: 'Log groups', heads: ['Log group', 'Retention', 'Creation time'], empty: 'No log groups',
          rows: d.groups.map((g) => ({ id: g.name, cells: [link(g.name, 'loggroup', { group: g.name }),
            g.retention ? `${g.retention} days` : 'Never expire', when(g.createdAt)] })) }));
    },
    async loggroup(user) {
      const group = S.params.group || N().log_group;
      const d = await get('logs', { group }, user);
      return page({ crumbs: [['CloudWatch'], ['Log groups', 'loggroups'], [group]], title: group },
        d.error ? h('div', { class: 'flash warn' }, h('div', {}, d.error)) : null,
        tabs([['streams', 'Log streams']], 'streams', () => {}),
        dataTable({ key: 'streams', title: 'Log streams', heads: ['Log stream', 'Last event time'], empty: 'No log streams', find: 'Find log streams',
          rows: d.streams.map((s) => ({ id: s.name, cells: [link(mono(s.name), 'logstream', { group, stream: s.name }), when(s.lastEvent)] })),
          note: 'One stream per task, named prefix/container/task-id. The 50 most recent are listed.' }));
    },
    async logstream(user) {
      const group = S.params.group || N().log_group;
      const d = await get('logs', { group, stream: S.params.stream }, user);
      return page({ crumbs: [['CloudWatch'], ['Log groups', 'loggroups'], [group, 'loggroup', { group }], [d.stream.split('/').pop().slice(0, 12)]], title: 'Log events' },
        h('p', { class: 'muted mono small' }, d.stream), eventsTable('stream-events', d.events));
    },
  };

  // ---------- lab actions ----------
  function checkResult(r) {
    const kind = { passed: 'ok', forbidden: 'bad', partial: 'warn', failing: 'bad', not_started: 'warn' }[r.status] || '';
    const title = { passed: 'Fixed', forbidden: 'That is a shortcut, not a fix', partial: 'Not finished',
      failing: 'Not fixed yet', not_started: 'Nothing deployed yet' }[r.status] || r.status;
    const lines = r.messages.slice();   // the heading already says it; drop a first line that only repeats it
    if (lines.length > 1 && /^(Not fixed yet:|Nothing has been deployed yet\.)/.test(lines[0])) lines.shift();
    const out = [h('div', { class: 'flash ' + kind }, h('div', {}, h('b', {}, title), lines.length ? h('pre', { class: 'brief' }, lines.join('\n')) : null))];
    if (r.analysis) out.push(replayBox(r));
    else if (r.replay) out.push(box('Replay', h('pre', { class: 'block' }, r.replay)));
    if (r.teaches && r.teaches.length) out.push(box('What this lab teaches', h('ul', { class: 'plain pad' }, r.teaches.map((t) => h('li', {}, t)))));
    if (r.onAws && r.onAws.length) out.push(box('On real AWS', h('p', { class: 'muted pad', style: 'padding-bottom:0' },
      'Where the real service behaves differently from this lab, so nothing surprises you there.'),
    h('ul', { class: 'plain pad' }, r.onAws.map((t) => h('li', {}, t)))));
    if (r.status === 'passed') out.push(nextUp());
    return h('div', {}, out);
  }
  function replayBox(r) {
    const a = r.analysis;
    // Opening one page makes several read calls in the same second; show that as one step.
    const steps = [];
    for (const x of a.rows) {
      const plain = x.clicked && !x.kind && !x.notes.some((t) => !/^x \d+ calls/.test(t)) && !/^(Update|Register|Create|Delete|Deregister|Modify|Stop|Run|Put|Set|Authorize|Revoke)/.test(x.label);
      const last = steps[steps.length - 1];
      if (plain && last && last.page && last.at === x.at) { last.also.push(x.label); continue; }
      steps.push({ ...x, page: plain, also: [] });
    }
    const rows = steps.map((x) => h('li', { class: 'tl-r ' + (x.kind || '') },
      h('span', { class: 't' }, x.at), h('i', { class: 'dot', 'aria-hidden': 'true' }),
      h('div', { class: 'tl-b' }, h('span', { class: 'mono' }, x.label.replace(/\s+\(/, ' (')),
        x.count > 1 ? h('span', { class: 'chip' }, `${x.count} calls` + (x.spent >= 1 ? ` · ${dur(x.spent)}` : '')) : null,
        x.also.length ? h('span', { class: 'chip', title: x.also.join(', ') }, `+${x.also.length} more on the same page`) : null,
        x.notes.filter((t) => !/^x \d+ calls/.test(t)).map((t) => h('span', { class: 'chip ' + (x.kind ? 'good' : 'note') }, t)))));
    return box('Replay',
      fields(['Solved in', a.solved_in], ['AWS calls', String(a.calls)], ['Steps', String(steps.length)]),
      a.rows.length ? h('ol', { class: 'tl' }, rows)
        : h('p', { class: 'muted pad' }, 'No AWS calls were recorded, so there is no path to show.'),
      a.insights.length ? h('div', { class: 'pad' }, a.insights.map((t) => h('div', { class: 'insight' }, t))) : null,
      h('details', { class: 'pad' }, h('summary', {}, 'Replay as plain text'), h('pre', { class: 'block' }, r.replay)));
  }
  function solutionBox() {
    const holder = h('div', {});
    const btn = h('button', { class: 'btn', onclick: async () => {
      btn.disabled = true;
      try { const d = await api('/api/solution'); holder.replaceChildren(h('div', { class: 'pad' }, md(d.markdown))); btn.remove(); }
      catch (e) { holder.replaceChildren(errBox('The reference solution could not be loaded', e.message)); btn.disabled = false; }
    } }, 'Show the reference solution');
    return box(['Reference solution', null, btn], h('p', { class: 'muted pad' }, 'How the lab itself fixes this scenario, step by step. Compare it with what you did.'), holder);
  }

  // ---------- scenario picker ----------
  const TOPICS = ['Tasks that start and then die', 'Tasks that never start', 'Running but not serving', 'Dependencies', 'Security groups', 'IAM',
    'More than one thing wrong', 'Changes to make'];
  const KIND = { deploy: 'Deploy goes wrong', incident: 'Starts broken', task: 'Task to carry out' };
  const PICK = { q: '', hideSolved: false };
  const ordered = () => ((S.lab && S.lab.scenarios) || []).slice().sort((x, y) =>
    ((TOPICS.indexOf(x.topic) + 99) % 99) - ((TOPICS.indexOf(y.topic) + 99) % 99) || x.difficulty - y.difficulty || x.id.localeCompare(y.id));
  const recommended = () => {   // easiest unsolved first, so the path climbs
    const open = ((S.lab && S.lab.scenarios) || []).filter((x) => !x.solved);
    return open.sort((x, y) => x.difficulty - y.difficulty || x.id.localeCompare(y.id))[0] || null;
  };
  function nextUp() {
    const n = recommended();
    if (!n) return h('div', { class: 'flash ok' }, h('div', {}, h('b', {}, 'You have solved every scenario')));
    return h('section', { class: 'box scn next' }, h('div', { style: 'flex:1;min-width:0' }, h('div', { class: 'small muted' }, 'Next up'),
      h('strong', { style: 'font-size:16px' }, n.title), h('div', { class: 'small muted', style: 'margin-top:4px' }, stars(n.difficulty), ` · about ${n.minutes} min · ${n.topic}`)),
    h('button', { class: 'btn', onclick: () => go('scenarios', {}) }, 'All scenarios'),
    h('button', { class: 'btn primary', onclick: (e) => startScenario(n.id, e.target) }, 'Start'));
  }
  function tour() {
    if (store.get('ecslab-tour') === 'done') return null;
    const steps = [['Read the brief', 'Each scenario starts with a short task, written the way it would reach you at work.'],
      ['Look around', 'The sections on the left are laid out like the AWS consoles. The terminal at the bottom takes ordinary AWS CLI commands. Use either, or both.'],
      ['Change something', 'Update the service, register a task definition revision, edit a rule. Banners tell you what each action did.'],
      ['Check my fix', 'The lab tests the real state of the environment. Hiding the symptom does not pass. Hints unlock with time if you are stuck.'],
      ['Read your replay', 'After a pass you see the path you took, where the time went, and what real AWS would have shown you.']];
    const el = h('section', { class: 'box tour', 'aria-label': 'How the lab works' },
      h('div', { class: 'box-h' }, h('h2', {}, 'How the lab works'), h('span', { class: 'spacer' }),
        h('button', { class: 'btn small', onclick: () => { store.set('ecslab-tour', 'done'); el.remove(); } }, 'Got it')),
      h('ol', { class: 'steps' }, steps.map(([t, d]) => h('li', {}, h('b', {}, t), h('span', {}, d)))));
    return el;
  }
  function picker() {
    const all = ordered(), a = S.lab && S.lab.active;
    const solved = all.filter((x) => x.solved).length;
    const list = h('div', {});
    const draw = () => {
      const q = PICK.q.trim().toLowerCase();
      const shown = all.filter((x) => (!PICK.hideSolved || !x.solved) && (!q || `${x.title} ${x.id} ${x.topic} ${KIND[x.kind]}`.toLowerCase().includes(q)));
      const topics = [...new Set(shown.map((x) => x.topic))];
      list.replaceChildren(...(topics.length ? topics.map((t) => { const mine = shown.filter((x) => x.topic === t); return h('section', { class: 'topic' },
        h('h2', {}, t, h('span', { class: 'count' }, ` ${all.filter((x) => x.topic === t && x.solved).length} of ${all.filter((x) => x.topic === t).length} solved`)),
        mine.map((x) => { const running = a && a.id === x.id; return h('div', { class: 'box scn' + (x.solved ? ' done' : '') },
          h('div', { class: 'num' }, x.solved ? '✓' : x.id.split('-')[1]),
          h('div', { style: 'flex:1;min-width:0' }, h('strong', { style: 'font-size:16px' }, x.title), running ? h('span', { class: 'chip good' }, 'Running now') : null,
            h('div', { class: 'small muted', style: 'margin-top:4px' }, stars(x.difficulty), ` · about ${x.minutes} min · ${KIND[x.kind] || ''} · `, mono(x.id)),
            x.solved ? h('div', { class: 'small', style: 'margin-top:4px' }, ind('COMPLETED', `Solved, best time ${dur(x.bestSeconds)}`), ' ',
              link(`${x.attempts} attempt${x.attempts === 1 ? '' : 's'}`, 'history', { scenario: x.id })) : null),
          running ? h('button', { class: 'btn', onclick: () => go('lab', {}) }, 'Open brief') : null,
          h('button', { class: 'btn' + (x.solved ? '' : ' primary'), onclick: (e) => startScenario(x.id, e.target) }, running ? 'Restart' : x.solved ? 'Solve again' : 'Start')); })); })
        : [h('p', { class: 'muted' }, 'No scenario matches.')]));
    };
    draw();
    const n = recommended();
    return h('div', {}, h('div', { class: 'head' }, h('h1', {}, 'Scenarios'), h('span', { class: 'spacer' }),
      h('div', { class: 'prog' }, h('div', { class: 'bar' }, h('i', { style: `width:${all.length ? (solved / all.length) * 100 : 0}%` })), h('span', {}, `${solved} of ${all.length} solved`))),
    h('p', { class: 'muted' }, 'Each scenario hands you a deployment that is broken the way production breaks, or a change to make without breaking it. ' +
      'Diagnose it, fix it, and the lab checks whether you really fixed it.'),
    tour(),
    n && !a ? h('section', { class: 'box scn next' }, h('div', { style: 'flex:1;min-width:0' }, h('div', { class: 'small muted' }, solved ? 'Next up' : 'Start here'),
      h('strong', { style: 'font-size:16px' }, n.title), h('div', { class: 'small muted', style: 'margin-top:4px' }, stars(n.difficulty), ` · about ${n.minutes} min · ${n.topic}`)),
    h('button', { class: 'btn primary', onclick: (e) => startScenario(n.id, e.target) }, 'Start')) : null,
    h('div', { class: 'tools bare' }, h('input', { type: 'search', class: 'find', placeholder: 'Find scenarios', 'aria-label': 'Find scenarios', value: PICK.q,
      oninput: (e) => { PICK.q = e.target.value; draw(); } }),
    h('label', { class: 'inline check-inline' }, h('input', { type: 'checkbox', checked: PICK.hideSolved, onchange: (e) => { PICK.hideSolved = e.target.checked; draw(); } }), 'Hide solved'),
    h('span', { class: 'spacer' }), store.get('ecslab-tour') === 'done' ? h('button', { class: 'btn quiet small', onclick: () => { store.set('ecslab-tour', ''); go('scenarios', {}); } }, 'Show the tour') : null),
    list);
  }
  function clearSession() {
    S.lastCheck = null; S.trailSince = 0; $('trail').replaceChildren(); FLASHES.length = 0; drawFlashes();
    trailTotal = 0; trailGroup = null; $('trail-count').textContent = '0';
    for (const k of Object.keys(UI)) delete UI[k];
  }
  async function startScenario(id, btn) {
    const a = S.lab && S.lab.active, label = btn.textContent;
    if (a && !(await confirmDialog({ title: a.id === id ? 'Restart the scenario' : 'Start another scenario', danger: true, confirm: a.id === id ? 'Restart' : 'Start',
      body: [h('p', {}, a.id === id ? 'This rebuilds the environment from the beginning and restarts your timer and trail.'
        : `This replaces "${a.title}", which is running now. Its environment, trail and timer are thrown away.`),
      h('p', { class: 'muted' }, 'Scenarios you have already solved stay solved.')] }))) return;
    btn.disabled = true; btn.textContent = 'Setting up, about 30 seconds…';
    try { S.lab = await api('/api/run', { scenario: id }); clearSession(); renderHeader(); await go('lab', {}); }
    catch (e) { flash('bad', 'The scenario could not be started', e.message); btn.disabled = false; btn.textContent = label; }
  }
  const restartScenario = (a, btn) => startScenario(a.id, btn);
  $('btn-check').addEventListener('click', async () => {
    const b = $('btn-check'); b.disabled = true; b.textContent = 'Checking…';
    try {
      S.lastCheck = await api('/api/check', {});
      for (let i = FLASHES.length - 1; i >= 0; i--) if (FLASHES[i].kind !== 'bad') FLASHES.splice(i, 1);   // the result is the news now
      drawFlashes(); await refreshLab(); await go('lab', {});
    } catch (e) { flash('bad', 'The check could not run', e.message); }
    b.disabled = false; b.textContent = 'Check my fix';
  });
  $('btn-hint').addEventListener('click', async () => {
    try {
      const r = await api('/api/hint', {});
      S.lab = r; renderHeader();
      const [first, ...rest] = r.message.split('\n');
      flash('info', first, rest.join(' ').trim());
      if (S.view === 'lab') await load(false);
    } catch (e) { toast(e.message); }
  });
  $('btn-reset').addEventListener('click', async () => {
    if (!(await confirmDialog({ title: 'Reset the lab', danger: true, confirm: 'Reset',
      body: [h('p', {}, 'This throws away the whole environment: every task, your new task definition revisions, your trail and your timer.'),
        h('p', { class: 'muted' }, 'You can start the scenario again afterwards.')] }))) return;
    const b = $('btn-reset'); b.disabled = true; b.textContent = 'Resetting…';
    try { S.lab = await api('/api/reset', {}); clearSession(); renderHeader(); await go('lab', {}); } catch (e) { flash('bad', 'The lab could not be reset', e.message); }
    b.disabled = false; b.textContent = 'Reset';
  });

  // ---------- trail ----------
  // The trail is ECS Lab's own panel (AWS has nothing like it), so it stays out of the way unless asked for.
  let trailTotal = 0, trailGroup = null;
  function showTrail(open) {
    $('trail-panel').hidden = !open;
    document.querySelector('.body').classList.toggle('with-trail', open);
    $('btn-trail').setAttribute('aria-expanded', String(open));
    try { localStorage.setItem('ecslab-trail', open ? 'open' : 'closed'); } catch (e) { /* private mode */ }
    if (fit) fit.fit();
  }
  $('btn-trail').addEventListener('click', () => showTrail($('trail-panel').hidden));
  $('trail-close').addEventListener('click', () => showTrail(false));
  async function pollTrail() {
    try {
      const d = await api('/api/trail?since=' + S.trailSince);
      const list = $('trail');
      for (const c of d.calls) {
        S.trailSince = Math.max(S.trailSince, c.ts);
        const failed = c.status >= 400;
        const write = /^(Update|Register|Create|Delete|Deregister|Modify|Stop|Run|Put|Set|Authorize|Revoke)/.test(c.action || '');
        const label = c.action + (c.detail ? ` (${c.detail})` : '');
        const quiet = c.source === 'console' && !write && !failed;
        if (quiet && trailGroup && c.ts - trailGroup.ts < 1.5) {   // one page view makes several reads; show them as one entry
          trailGroup.names.push(label);
          trailGroup.more.textContent = ` +${trailGroup.names.length - 1} more`;
          trailGroup.li.title = trailGroup.names.join(', ');
          continue;
        }
        const more = h('span', { class: 'more' });
        const li = h('li', { class: failed ? 'err' : write ? 'write' : '' }, h('span', { class: 't' }, clock(c.ts - d.startedAt)),
          h('span', { class: 'a' }, label + (failed ? ` ${c.status}` : ''), more,
            h('span', { class: 'src' }, c.source === 'console' ? 'clicked' : 'typed')));
        list.prepend(li);
        trailGroup = quiet ? { ts: c.ts, names: [label], more, li } : null;
      }
      while (list.children.length > 200) list.lastChild.remove();
      $('trail-empty').hidden = list.children.length > 0;
      trailTotal += d.calls.length; $('trail-count').textContent = String(trailTotal);
    } catch (e) { /* keep polling */ }
  }

  // ---------- theme ----------
  const THEMES = ['auto', 'light', 'dark'];
  const termTheme = () => {
    const css = getComputedStyle(document.documentElement);
    return { background: css.getPropertyValue('--term-bg').trim(), foreground: css.getPropertyValue('--term-ink').trim(),
      cursor: '#2fd9c7', selectionBackground: '#3b3f6b' };
  };
  const currentTheme = () => document.documentElement.dataset.theme || 'auto';
  let term, fit, sock;
  function applyTheme(t) {
    if (t === 'auto') delete document.documentElement.dataset.theme; else document.documentElement.dataset.theme = t;
    try { if (t === 'auto') localStorage.removeItem('ecslab-theme'); else localStorage.setItem('ecslab-theme', t); } catch (e) { /* private mode */ }
    const b = $('btn-theme');
    b.replaceChildren(icon(t === 'light' ? 'sun' : t === 'dark' ? 'moon' : 'auto'));
    b.title = `Theme: ${{ auto: 'Matching your system', light: 'Light', dark: 'Dark' }[t]}. Click to switch.`;
    b.setAttribute('aria-label', b.title);
    if (term) term.options.theme = termTheme();
  }
  $('btn-theme').addEventListener('click', () => {
    const next = THEMES[(THEMES.indexOf(currentTheme()) + 1) % THEMES.length];
    applyTheme(next); toast('Theme: ' + { auto: 'matching your system', light: 'light', dark: 'dark' }[next]);
  });

  // ---------- terminal ----------
  function connectTerminal() {
    $('term-reconnect').hidden = true;
    sock = new WebSocket(`ws://${location.host}/ws/terminal`);
    const send = (o) => { if (sock.readyState === 1) sock.send(JSON.stringify(o)); };
    sock.onopen = () => { fit.fit(); send({ type: 'resize', cols: term.cols, rows: term.rows }); };
    sock.onmessage = (e) => term.write(e.data);
    sock.onclose = () => { term.write('\r\n\x1b[90m[terminal closed]\x1b[0m\r\n'); $('term-reconnect').hidden = false; };
    term.onData((data) => send({ type: 'input', data }));
    term.onResize(({ cols, rows }) => send({ type: 'resize', cols, rows }));
  }
  function startTerminal() {
    if (!window.Terminal) { $('term-note').textContent = 'The terminal could not load.'; return; }
    term = new window.Terminal({ fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace', fontSize: 13,
      cursorBlink: true, theme: termTheme(), scrollback: 5000 });
    fit = new window.FitAddon.FitAddon();
    term.loadAddon(fit); term.open($('xterm')); fit.fit();
    connectTerminal();
  }
  window.addEventListener('resize', () => { if (fit) fit.fit(); });
  $('term-reconnect').addEventListener('click', () => { if (term) term.dispose(); startTerminal(); });
  $('term-toggle').addEventListener('click', () => {
    const closed = $('term').classList.toggle('closed');
    $('term-toggle').textContent = closed ? 'Show' : 'Hide';
    $('term-toggle').setAttribute('aria-expanded', String(!closed));
    if (!closed && fit) { fit.fit(); term.focus(); }
  });

  // ---------- boot ----------
  (async () => {
    applyTheme(currentTheme());
    let trailPref = 'closed';
    try { trailPref = localStorage.getItem('ecslab-trail') || 'closed'; } catch (e) { /* private mode */ }
    showTrail(trailPref === 'open');
    try { await refreshLab(); } catch (e) { flash('bad', 'ECS Lab is not responding', e.message); }
    await go('lab', {}, false);
    startTerminal();
    setInterval(tick, 1000);
    setInterval(pollTrail, 2000); pollTrail();
    setInterval(() => {
      if (!S.editing && !['lab', 'scenarios', 'history'].includes(S.view) && S.lab && S.lab.active && !document.hidden && !typing() && !$('dlg').open) load(false);
    }, 5000);
    setInterval(() => { if (!document.hidden) refreshLab().catch(() => {}); }, 15000);
  })();
})();
