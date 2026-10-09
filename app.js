// UI wiring. Holds no task rules of its own — every change goes through a
// mutation from logic.js and out via sync.js.

import {
  grouped, counts, isStale, daysOld, ownerLabel, ownedBy,
  addMutation, toggleStateMutation, makeId, parseQuickAdd, effectiveTopic,
} from './logic.js';
import { GitHubStore, LocalStore } from './store.js';
import { Sync } from './sync.js';

const $ = (id) => document.getElementById(id);
const LS = window.localStorage;
const POLL_MS = 60000;
const DEFAULT_REPO = 'tasks-data';

function get(key) {
  try { return LS.getItem(key); } catch { return null; }
}
function set(key, val) {
  try { LS.setItem(key, val); } catch { /* storage blocked: the remote still has it */ }
}
function read(key, fallback) {
  try {
    const v = JSON.parse(get(key));
    return v && typeof v === 'object' ? { ...fallback, ...v } : fallback;
  } catch {
    return fallback;
  }
}
function write(key, val) {
  set(key, JSON.stringify(val));
}

// Device-local view state. Deliberately NOT in tasks.json: what you are looking
// at is a property of the moment, not of the data, and syncing it would mean a
// commit every time you tap a chip.
const view = {
  filters: read('tasks.filters', { flagged: true, normal: true, waiting: true }),
  collapsed: read('tasks.collapsed', {}),
  who: get('tasks.who') === 'mine' ? 'mine' : 'all',
};

// The owner picker in the add bar. It goes back to "me" after every task, so
// one task given to the other person can't quietly take the next few with it.
let addOwner = null;

function me() {
  return get('tasks.me') || '';
}

// Served from <org>.github.io/tasks/, the org is in the address itself, so a
// new phone needs only a token.
function defaultOwner() {
  const h = location.hostname;
  return h.endsWith('.github.io') ? h.slice(0, -'.github.io'.length) : '';
}

function config() {
  return {
    owner: get('tasks.owner') || defaultOwner(),
    repo: get('tasks.repo') || DEFAULT_REPO,
    path: get('tasks.path') || 'tasks.json',
    token: get('tasks.token') || '',
    // Only the tests set this, to point the app at a stand-in for GitHub.
    api: get('tasks.api') || undefined,
  };
}

function makeStore() {
  const c = config();
  if (c.owner && c.repo && c.token) return new GitHubStore(c);
  return new LocalStore();
}

let sync = new Sync({ store: makeStore(), storage: LS });
let reordering = false;
let dragging = null;
let undoTimer = null;

const hasPeople = () => (sync.state.people || []).length > 0;
const ownerFilter = () => (view.who === 'mine' && me() ? me() : null);

// --- rendering -------------------------------------------------------------

function render() {
  if (dragging) return; // never rebuild the DOM out from under a finger
  const state = sync.state;
  const owner = ownerFilter();
  const groups = grouped(state, view.filters, owner);
  const list = $('list');
  const now = new Date().toISOString();

  list.replaceChildren(...groups.map((g) => topicSection(g, now, owner)));

  const c = counts(state, owner);
  $('c-flagged').textContent = c.flagged;
  $('c-normal').textContent = c.normal;
  $('c-waiting').textContent = c.waiting;

  const hidden = c.total - groups.reduce((n, g) => n + g.tasks.length, 0);
  const empty = $('empty');
  if (groups.length === 0) {
    empty.hidden = false;
    const cfg = config();
    const connected = cfg.owner && cfg.repo && cfg.token;
    if (state.tasks.length === 0 && !connected) {
      // Never let "not connected" look like "there are no tasks".
      empty.textContent = 'Not connected to GitHub yet. Tap ⚙ above, pick who you are and paste your token.';
    } else if (c.total === 0 && owner) {
      empty.textContent = 'Nothing of yours here. Tap All to see everyone’s.';
    } else if (c.total === 0) {
      empty.textContent = 'Nothing here. Add something below.';
    } else {
      empty.textContent = `Nothing matches. ${hidden} task${hidden === 1 ? '' : 's'} hidden by the filters above.`;
    }
  } else {
    empty.hidden = true;
  }

  renderWho();
  fillTopicSelect($('add-topic'), state, $('add-topic').value || 'inbox');
  fillOwnerSelect($('add-owner'), state, addOwner ?? me(), '—');
  $('add-owner').hidden = !hasPeople();
}

function renderWho() {
  const mine = view.who === 'mine' && !!me();
  $('who').hidden = !hasPeople();
  $('who-all').setAttribute('aria-pressed', String(!mine));
  $('who-mine').setAttribute('aria-pressed', String(mine));
}

function topicSection({ topic, tasks }, now, owner) {
  const sec = document.createElement('section');
  sec.className = 'topic';
  sec.dataset.topic = topic.id;
  sec.dataset.collapsed = String(!!view.collapsed[topic.id]);

  const head = document.createElement('button');
  head.type = 'button';
  head.className = 'topic-head';
  head.setAttribute('aria-expanded', String(!view.collapsed[topic.id]));
  head.innerHTML = `<span class="caret">▼</span><span>${escapeHtml(topic.title)}</span><span class="count">${tasks.length}</span>`;
  head.addEventListener('click', () => {
    view.collapsed[topic.id] = !view.collapsed[topic.id];
    write('tasks.collapsed', view.collapsed);
    render();
  });

  const ul = document.createElement('ul');
  ul.className = 'tasks';
  ul.dataset.topic = topic.id;
  tasks.forEach((t) => ul.appendChild(taskRow(t, now, owner)));

  sec.append(head, ul);
  return sec;
}

function taskRow(task, now, ownerShown) {
  const li = document.createElement('li');
  li.className = 'task';
  li.dataset.id = task.id;
  li.dataset.state = task.state;
  li.dataset.owner = task.owner || '';

  const done = document.createElement('button');
  done.type = 'button';
  done.className = 'done-btn';
  done.setAttribute('aria-label', 'Done');
  done.addEventListener('click', () => completeTask(task));

  const text = document.createElement('button');
  text.type = 'button';
  text.className = 'task-text';
  text.dir = 'auto';
  text.append(document.createTextNode(task.text));
  // In the Mine view every badge would say the same name, so they drop out.
  if (task.owner && !ownerShown) {
    const o = document.createElement('span');
    o.className = 'owner' + (task.owner === me() ? ' owner-me' : '');
    o.textContent = ownerLabel(sync.state, task.owner);
    text.appendChild(o);
  }
  if (task.note) {
    const n = document.createElement('span');
    n.className = 'has-note';
    n.textContent = '¶';
    n.title = 'has a note';
    text.appendChild(n);
  }
  if (isStale(task, now)) {
    const age = document.createElement('span');
    age.className = 'age';
    age.textContent = `${daysOld(task, now)}d`;
    age.title = 'sitting here a while';
    text.appendChild(age);
  }
  // A long-press ends with a click on whatever was under the finger. Without
  // this guard, arming reorder mode also opens the edit sheet.
  text.addEventListener('click', () => { if (!reordering) openDetail(task); });

  const flag = markButton('mark-flag', '▲', 'Flag as critical', () =>
    sync.apply(toggleStateMutation(task, 'flagged')) && render());
  const wait = markButton('mark-wait', '◷', 'Mark as waiting', () =>
    sync.apply(toggleStateMutation(task, 'waiting')) && render());

  li.append(done, text, flag, wait);
  attachLongPress(li);
  return li;
}

function markButton(cls, glyph, label, onClick) {
  const b = document.createElement('button');
  b.type = 'button';
  b.className = `mark ${cls}`;
  b.textContent = glyph;
  b.setAttribute('aria-label', label);
  b.addEventListener('click', onClick);
  return b;
}

function fillTopicSelect(sel, state, selected) {
  sel.replaceChildren(...state.topics.map((t) => option(t.id, t.title, t.id === selected)));
}

function fillOwnerSelect(sel, state, selected, noneLabel) {
  sel.replaceChildren(
    ...(state.people || []).map((p) => option(p.id, p.name, p.id === selected)),
    option('', noneLabel, !selected),
  );
}

function option(value, label, selected) {
  const o = document.createElement('option');
  o.value = value;
  o.textContent = label;
  if (selected) o.selected = true;
  return o;
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (ch) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]));
}

// --- actions ---------------------------------------------------------------

function completeTask(task) {
  const index = sync.state.tasks.filter((t) => t.topic === task.topic).findIndex((t) => t.id === task.id);
  sync.apply({ op: 'delete', id: task.id });
  render();
  showUndo(task, index);
}

function showUndo(task, index) {
  const bar = $('snackbar');
  $('snackbar-text').textContent = 'Done';
  $('undo').hidden = false;
  bar.hidden = false;
  if (undoTimer) clearTimeout(undoTimer);
  undoTimer = setTimeout(() => { bar.hidden = true; }, 6000);
  $('undo').onclick = () => {
    sync.apply({ op: 'restore', task, index: Math.max(0, index) });
    bar.hidden = true;
    render();
  };
}

function showNote(text) {
  const bar = $('snackbar');
  $('snackbar-text').textContent = text;
  $('undo').hidden = true;
  bar.hidden = false;
  if (undoTimer) clearTimeout(undoTimer);
  undoTimer = setTimeout(() => { bar.hidden = true; $('undo').hidden = false; }, 4000);
}

$('add').addEventListener('submit', (e) => {
  e.preventDefault();
  const input = $('add-text');
  const raw = input.value.trim();
  if (!raw) return;
  // The pickers win unless the text names a topic (`search:`) or a person (`@gal`).
  const parsed = parseQuickAdd(raw, sync.state);
  const m = addMutation({
    text: parsed.text || raw,
    topic: parsed.topic || $('add-topic').value || 'inbox',
    owner: parsed.owner || (hasPeople() ? $('add-owner').value : '') || null,
    state: parsed.state,
    now: new Date().toISOString(),
    id: makeId(),
  });
  sync.apply(m);
  input.value = '';
  addOwner = null;
  render();
  // A task added for the other person in the Mine view, or into a hidden
  // state, would otherwise seem to vanish on the spot.
  const t = m.task;
  if (!ownedBy(t, ownerFilter())) showNote(`Added for ${ownerLabel(sync.state, t.owner) || 'nobody'}`);
  else if (!view.filters[t.state]) showNote('Added, but hidden by the filters above');
});

$('add-owner').addEventListener('change', () => {
  addOwner = $('add-owner').value;
});

for (const [id, key] of [['f-flagged', 'flagged'], ['f-normal', 'normal'], ['f-waiting', 'waiting']]) {
  const el = $(id);
  el.checked = view.filters[key];
  el.addEventListener('change', () => {
    view.filters[key] = el.checked;
    write('tasks.filters', view.filters);
    render();
  });
}

function setWho(who) {
  view.who = who;
  set('tasks.who', who);
  render();
}

$('who-all').addEventListener('click', () => setWho('all'));
$('who-mine').addEventListener('click', () => {
  if (!me()) {
    openSettings('Pick who you are, and Mine will show your tasks.');
    return;
  }
  setWho('mine');
});

// --- detail sheet ----------------------------------------------------------

let detailTask = null;

function openDetail(task) {
  detailTask = task;
  $('d-text').value = task.text;
  $('d-note').value = task.note || '';
  fillTopicSelect($('d-topic'), sync.state, effectiveTopic(sync.state, task));
  fillOwnerSelect($('d-owner'), sync.state, task.owner || '', 'Nobody');
  $('d-owner-field').hidden = !hasPeople() && !task.owner;
  $('d-age').textContent = task.created
    ? `Added ${new Date(task.created).toLocaleDateString()} — ${daysOld(task, new Date().toISOString())} days ago`
    : '';
  $('detail').showModal();
}

$('detail').addEventListener('close', () => {
  const dlg = $('detail');
  if (dlg.returnValue !== 'save' || !detailTask) { detailTask = null; return; }
  const t = detailTask;
  const text = $('d-text').value.trim();
  const note = $('d-note').value;
  const topic = $('d-topic').value;
  const owner = $('d-owner').value || null;
  if (text && text !== t.text) sync.apply({ op: 'setText', id: t.id, text });
  if (note !== (t.note || '')) sync.apply({ op: 'setNote', id: t.id, note });
  if (owner !== (t.owner || null)) sync.apply({ op: 'setOwner', id: t.id, owner });
  if (topic !== t.topic) sync.apply({ op: 'move', id: t.id, topic, index: 0 });
  detailTask = null;
  render();
});

// --- settings --------------------------------------------------------------

function tokenHelpUrl(owner) {
  return owner ? `https://github.com/${owner}/tasks#make-your-token` : 'https://github.com/settings/personal-access-tokens/new';
}

function openSettings(message) {
  const c = config();
  fillOwnerSelect($('s-me'), sync.state, me(), '—');
  $('s-me-field').hidden = !hasPeople();
  // Real values, not placeholders. Greyed placeholder text reads as "already
  // filled in", so leaving these blank silently produced a local-only list.
  $('s-owner').value = c.owner;
  $('s-repo').value = c.repo;
  $('s-path').value = c.path;
  $('s-token').value = c.token;
  $('s-expires').value = (get('tasks.tokenExpires') || '').slice(0, 10);
  $('s-token-help').href = tokenHelpUrl(c.owner);
  $('s-msg').textContent = message || (c.token ? 'A token is saved on this device.' : 'No token yet: changes stay on this device.');
  $('settings').showModal();
}

$('settings-btn').addEventListener('click', () => openSettings());

$('settings').addEventListener('close', async () => {
  if ($('settings').returnValue !== 'save') return;
  set('tasks.me', $('s-me').value);
  set('tasks.owner', $('s-owner').value.trim());
  set('tasks.repo', $('s-repo').value.trim());
  set('tasks.path', $('s-path').value.trim() || 'tasks.json');
  set('tasks.token', $('s-token').value.trim());
  set('tasks.tokenExpires', $('s-expires').value || '');
  addOwner = null;
  await restart();
  // A token with no repo to point at is the most likely way to misconfigure
  // this, and it fails silently otherwise.
  const c2 = config();
  if (c2.token && (!c2.owner || !c2.repo)) {
    const banner = $('banner');
    banner.hidden = false;
    banner.textContent = 'A token is saved, but the GitHub account or data repo is empty, so nothing is being synced. Tap ⚙ and fill them in under "Where the tasks live".';
  }
});

async function restart() {
  const pending = sync.pending;
  sync = new Sync({ store: makeStore(), storage: LS });
  sync.pending = pending;
  wireSync();
  await sync.start();
  render();
}

// --- reorder mode ----------------------------------------------------------
// Long-press arms it; nothing reorders until then, and leaving the mode disarms
// it again, so the order can't shift under an accidental swipe.

function attachLongPress(li) {
  let timer = null;
  let startY = 0;
  li.addEventListener('pointerdown', (e) => {
    if (reordering) { beginDrag(e, li); return; }
    startY = e.clientY;
    timer = setTimeout(() => { timer = null; enterReorder(); }, 500);
  });
  const cancel = (e) => {
    if (timer && e && e.type === 'pointermove' && Math.abs(e.clientY - startY) < 8) return;
    if (timer) { clearTimeout(timer); timer = null; }
  };
  li.addEventListener('pointermove', cancel);
  li.addEventListener('pointerup', cancel);
  li.addEventListener('pointercancel', cancel);
}

function enterReorder() {
  reordering = true;
  document.body.classList.add('reordering');
  $('dragbar').hidden = false;
  if (navigator.vibrate) navigator.vibrate(15);
}

function exitReorder() {
  reordering = false;
  dragging = null;
  document.body.classList.remove('reordering');
  $('dragbar').hidden = true;
  render();
}

$('dragdone').addEventListener('click', exitReorder);

function beginDrag(e, li) {
  dragging = { li, ul: li.parentElement, pointerId: e.pointerId };
  li.classList.add('dragging');
  // Listeners go on the document, NOT the row. Repositioning the row is a
  // remove-and-reinsert, which releases pointer capture — after the first
  // reposition a row-bound handler would simply stop receiving moves.
  document.addEventListener('pointermove', onDragMove, true);
  document.addEventListener('pointerup', endDrag, true);
  document.addEventListener('pointercancel', endDrag, true);
}

function onDragMove(e) {
  if (!dragging) return;
  e.preventDefault();
  const { li, ul } = dragging;
  const y = e.clientY;
  let before = null;
  for (const sib of ul.children) {
    if (sib === li) continue;
    const r = sib.getBoundingClientRect();
    if (y < r.top + r.height / 2) { before = sib; break; }
  }
  // Skip no-op moves; every DOM move costs a reflow and drops capture.
  if (before) {
    if (li.nextElementSibling !== before) ul.insertBefore(li, before);
  } else if (ul.lastElementChild !== li) {
    ul.appendChild(li);
  }
}

function endDrag() {
  if (!dragging) return;
  const { li, ul } = dragging;
  li.classList.remove('dragging');
  document.removeEventListener('pointermove', onDragMove, true);
  document.removeEventListener('pointerup', endDrag, true);
  document.removeEventListener('pointercancel', endDrag, true);
  // The DOM index counts only the rows on screen; filters and the Mine view
  // hide some. Translate it into a position among ALL of the topic's tasks by
  // anchoring on the visible neighbour above.
  const id = li.dataset.id;
  const topic = ul.dataset.topic;
  const prev = li.previousElementSibling;
  const all = sync.state.tasks.filter((t) => t.topic === topic && t.id !== id).map((t) => t.id);
  const index = prev ? all.indexOf(prev.dataset.id) + 1 : 0;
  dragging = null;
  sync.apply({ op: 'move', id, topic, index });
}

// --- sync status -----------------------------------------------------------

function wireSync() {
  sync.addEventListener('state', render);
  sync.addEventListener('status', (e) => {
    const { status } = e.detail;
    $('status').dataset.status = status;
    const banner = $('banner');
    if (status === 'auth-error') {
      banner.hidden = false;
      banner.innerHTML = '<b>GitHub rejected the token.</b><br>Tokens expire. Your changes are saved on this device and will sync once the token is replaced.<br>';
      const b = document.createElement('button');
      b.className = 'primary';
      b.textContent = 'Replace token';
      b.onclick = () => openSettings();
      banner.appendChild(b);
    } else if (status !== 'error') {
      banner.hidden = true;
    }
  });
}

// Only relevant when the token actually expires. A token with no recorded
// expiry never triggers this; the rejected-token banner still covers it.
function checkTokenAge() {
  const expires = get('tasks.tokenExpires');
  if (!expires || !config().token) return;
  const daysLeft = (Date.parse(expires) - Date.now()) / 86400000;
  if (Number.isNaN(daysLeft) || daysLeft > 30) return;
  const mine = me();
  const text = `Renew ${mine ? `${ownerLabel(sync.state, mine)}'s` : 'a'} tasks token`;
  if (sync.state.tasks.some((t) => t.text === text)) return;
  sync.apply(addMutation({
    text,
    topic: 'inbox',
    state: 'flagged',
    owner: mine || null,
    note: 'Make a new fine-grained token for the tasks-data repo only (Contents: read and write), paste it into Settings on each device, and record its expiry date there.',
    now: new Date().toISOString(),
    id: makeId(),
  }));
}

// --- boot ------------------------------------------------------------------

wireSync();
sync.start().then(() => {
  checkTokenAge();
  render();
});

// The other person's changes arrive by polling: on return to the app, when
// the network comes back, and once a minute while the app is on screen.
window.addEventListener('online', () => { sync.refresh(); sync.flushSoon(0); });
document.addEventListener('visibilitychange', () => {
  if (!document.hidden) { sync.refresh(); sync.flushSoon(0); }
});
setInterval(() => { if (!document.hidden) sync.refresh(); }, POLL_MS);

if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => navigator.serviceWorker.register('sw.js').catch(() => {}));
}

// Exposed for the test harness only.
window.__tasks = { get sync() { return sync; }, render, view };
