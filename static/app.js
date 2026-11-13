const $ = s => document.querySelector(s);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const label = s => String(s).replace('_', ' ').replace(/^\w/, c => c.toUpperCase());
const S = { me: null, agents: [], view: 'all', timer: null };
const VIEWS = {
  all: ['All tickets', 'Every ticket across the team'],
  active: ['Active tickets', 'Open and in progress'],
  breached: ['SLA breached', 'Past their response deadline'],
  unassigned: ['Unassigned', 'Waiting for an agent'],
  mine: ['Assigned to me', 'Your queue'],
};

async function api(path, opts = {}) {
  const r = await fetch('/api' + path, {
    method: opts.method || 'GET', credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  const d = await r.json().catch(() => ({}));
  if (!r.ok) { const e = new Error(d.error || 'Request failed'); e.fields = d.fields; e.status = r.status; throw e; }
  return d;
}
function toast(msg, bad) {
  const t = $('#toast'); t.textContent = msg; t.className = 'toast show' + (bad ? ' bad' : '');
  clearTimeout(S.timer); S.timer = setTimeout(() => t.className = 'toast', 2800);
}
const fail = e => { if (e.status === 401) showLogin(); else toast(e.message, true); };
function ago(iso) {
  const m = Math.max(0, (Date.now() - new Date(iso)) / 60000);
  if (m < 60) return Math.round(m) + 'm ago';
  if (m < 1440) return Math.round(m / 60) + 'h ago';
  return Math.round(m / 1440) + 'd ago';
}
function slaText(t) {
  if (t.sla === 'met') return 'SLA met';
  if (t.sla === 'breached') return t.resolved_at ? 'SLA breached' : Math.abs(t.hours_left) + 'h overdue';
  return t.hours_left + 'h left';
}
const slaBadge = t => `<span class="badge l-${t.sla}">${esc(slaText(t))}</span>`;

function showLogin() { S.me = null; $('#app').hidden = true; $('#login').hidden = false; }
function showApp() { $('#login').hidden = true; $('#app').hidden = false; $('#meName').textContent = S.me.name; $('#meRole').textContent = S.me.role; }

async function boot() {
  try {
    const { user } = await api('/me');
    if (!user) return showLogin();
    S.me = user; showApp(); await init();
  } catch (e) { showLogin(); }
}
async function init() {
  const { agents } = await api('/agents'); S.agents = agents;
  $('#fAgent').innerHTML = '<option value="">Any agent</option><option value="unassigned">Unassigned</option>' +
    agents.map(a => `<option value="${a.id}">${esc(a.name)}</option>`).join('');
  await refresh();
}
async function refresh() {
  try { await Promise.all([loadStats(), loadTickets()]); } catch (e) { fail(e); }
}
async function loadStats() {
  const s = await api('/stats');
  const cards = [
    ['Active tickets', s.active, ''], ['SLA breached', s.breached, 'red'], ['At risk', s.at_risk, 'amber'],
    ['Unassigned', s.unassigned, 'blue'], ['SLA compliance', s.sla_compliance + '%', ''], ['Avg resolution', s.avg_resolution_hours + 'h', ''],
  ];
  $('#stats').innerHTML = cards.map(([l, v, c]) => `<div class="stat ${c}"><b>${esc(v)}</b><span>${l}</span></div>`).join('');
}
async function loadTickets() {
  const p = new URLSearchParams();
  const v = S.view, st = $('#fStatus').value, pr = $('#fPriority').value;
  let ag = $('#fAgent').value;
  if (v === 'active') p.set('status', 'active');
  if (v === 'breached') { p.set('sla', 'breached'); p.set('status', 'active'); }
  if (v === 'unassigned') { ag = 'unassigned'; p.set('status', 'active'); }
  if (v === 'mine') ag = S.me.id;
  if (st) p.set('status', st);
  if (pr) p.set('priority', pr);
  if (ag) p.set('assignee', ag);
  if ($('#search').value.trim()) p.set('q', $('#search').value.trim());
  const { tickets, count } = await api('/tickets?' + p);
  $('#count').textContent = count + (count === 1 ? ' ticket' : ' tickets');
  $('#rows').innerHTML = tickets.length ? tickets.map(t => `
    <tr data-id="${t.id}" tabindex="0">
      <td><div class="t">#${t.id} ${esc(t.title)}</div><div class="s">${esc(label(t.category))} · ${ago(t.created_at)}</div></td>
      <td>${esc(t.customer_name)}<div class="s">${esc(t.customer_email)}</div></td>
      <td><span class="badge p-${t.priority}">${label(t.priority)}</span></td>
      <td><span class="badge s-${t.status}">${label(t.status)}</span></td>
      <td>${t.assignee ? esc(t.assignee.name) : '<span class="s">Unassigned</span>'}</td>
      <td>${slaBadge(t)}</td></tr>`).join('')
    : '<tr><td colspan="6" class="empty">No tickets match these filters. Clear a filter or create a new ticket.</td></tr>';
}

async function openTicket(id) {
  try {
    const { ticket: t, audit } = await api('/tickets/' + id);
    const opt = (list, cur) => list.map(x => `<option value="${x}" ${x === cur ? 'selected' : ''}>${label(x)}</option>`).join('');
    $('#drawer').innerHTML = `
      <button class="x" id="closeD" aria-label="Close">×</button>
      <small class="s">Ticket #${t.id} · ${esc(label(t.category))}</small>
      <h2>${esc(t.title)}</h2>
      <div class="meta"><span class="badge p-${t.priority}">${label(t.priority)}</span><span class="badge s-${t.status}">${label(t.status)}</span>${slaBadge(t)}</div>
      <p>${esc(t.description)}</p>
      <p><b>${esc(t.customer_name)}</b><br><span class="s">${esc(t.customer_email)}</span></p>
      <div class="controls">
        <label>Status<select data-k="status">${opt(['open','in_progress','resolved','closed'], t.status)}</select></label>
        <label>Priority<select data-k="priority">${opt(['low','medium','high','critical'], t.priority)}</select></label>
        <label>Agent<select data-k="assignee_id"><option value="">Unassigned</option>${S.agents.map(a => `<option value="${a.id}" ${t.assignee && t.assignee.id === a.id ? 'selected' : ''}>${esc(a.name)}</option>`).join('')}</select></label>
      </div>
      <div class="note"><input id="noteIn" maxlength="300" placeholder="Add an internal note"><button class="btn primary" id="noteBtn">Add note</button></div>
      <h3>Audit trail</h3>
      <ul class="tl">${audit.map(a => `<li class="${a.action === 'note' ? 'note-i' : ''}"><b>${esc(a.actor)}</b> ${esc(a.detail)}<small>${new Date(a.created_at).toLocaleString()}</small></li>`).join('')}</ul>`;
    $('#drawerWrap').hidden = false;
    $('#closeD').onclick = closeDrawer;
    $('#drawer').querySelectorAll('select').forEach(sel => sel.onchange = async () => {
      try { await api('/tickets/' + id, { method: 'PATCH', body: { [sel.dataset.k]: sel.value || null } }); toast('Ticket updated'); await openTicket(id); refresh(); } catch (e) { fail(e); }
    });
    $('#noteBtn').onclick = async () => {
      const text = $('#noteIn').value.trim(); if (!text) return;
      try { await api(`/tickets/${id}/comments`, { method: 'POST', body: { text } }); toast('Note added'); openTicket(id); } catch (e) { fail(e); }
    };
  } catch (e) { fail(e); }
}
function closeDrawer() { $('#drawerWrap').hidden = true; }

let signup = false;
$('#switchBtn').onclick = () => {
  signup = !signup; $('#loginErr').textContent = '';
  $('#nameWrap').hidden = !signup; $('#name').required = signup;
  $('#authTitle').textContent = signup ? 'Create account' : 'Sign in';
  $('#authBtn').textContent = signup ? 'Create account' : 'Sign in';
  $('#switchBtn').textContent = signup ? 'Already have an account? Sign in' : 'New here? Create an account';
  $('#password').autocomplete = signup ? 'new-password' : 'current-password';
};
$('#loginForm').onsubmit = async ev => {
  ev.preventDefault(); $('#loginErr').textContent = '';
  const body = { email: $('#email').value, password: $('#password').value };
  if (signup) body.name = $('#name').value;
  try { const { user } = await api(signup ? '/signup' : '/login', { method: 'POST', body }); S.me = user; showApp(); await init(); if (signup) toast('Welcome, ' + user.name); }
  catch (e) { $('#loginErr').textContent = e.message; }
};
$('#demoBtn').onclick = () => { $('#email').value = 'admin@demo.com'; $('#password').value = 'admin123'; };
$('#logout').onclick = async () => { await api('/logout', { method: 'POST' }); showLogin(); };
document.querySelectorAll('.nav').forEach(b => b.onclick = () => {
  document.querySelectorAll('.nav').forEach(n => n.classList.remove('on')); b.classList.add('on');
  S.view = b.dataset.view; $('#viewTitle').textContent = VIEWS[S.view][0]; $('#viewSub').textContent = VIEWS[S.view][1]; refresh();
});
['#fStatus', '#fPriority', '#fAgent'].forEach(s => $(s).onchange = () => loadTickets().catch(fail));
let st; $('#search').oninput = () => { clearTimeout(st); st = setTimeout(() => loadTickets().catch(fail), 250); };
$('#rows').onclick = e => { const r = e.target.closest('tr[data-id]'); if (r) openTicket(r.dataset.id); };
$('#rows').onkeydown = e => { if (e.key === 'Enter') { const r = e.target.closest('tr[data-id]'); if (r) openTicket(r.dataset.id); } };
$('#drawerWrap').onclick = e => { if (e.target.id === 'drawerWrap') closeDrawer(); };
document.addEventListener('keydown', e => { if (e.key === 'Escape') { closeDrawer(); $('#modal').hidden = true; } });
$('#newBtn').onclick = () => { $('#newForm').reset(); document.querySelectorAll('[data-f]').forEach(x => x.textContent = ''); $('#modal').hidden = false; $('#newForm').elements.title.focus(); };
$('#cancelNew').onclick = () => $('#modal').hidden = true;
$('#newForm').onsubmit = async ev => {
  ev.preventDefault();
  document.querySelectorAll('[data-f]').forEach(x => x.textContent = '');
  const body = Object.fromEntries(new FormData(ev.target));
  try { const { ticket } = await api('/tickets', { method: 'POST', body }); $('#modal').hidden = true; toast(`Ticket #${ticket.id} created`); refresh(); }
  catch (e) {
    if (e.fields) Object.entries(e.fields).forEach(([k, m]) => { const el = document.querySelector(`[data-f="${k}"]`); if (el) el.textContent = m; });
    else fail(e);
  }
};
setInterval(() => { if (S.me && $('#drawerWrap').hidden && $('#modal').hidden) refresh(); }, 60000);
boot();
