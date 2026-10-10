// UI wiring. Holds no rules of its own: every change goes through a mutation
// from logic.js (tasks) or people.js (people) and out via sync.js. The two
// lists share the engine and the settings, but not a file.

import {
  grouped, counts, isStale, daysOld, ownerLabel, ownedBy,
  addMutation, toggleStateMutation, makeId, parseQuickAdd, effectiveTopic, resolveTopic,
} from './logic.js';
import * as P from './people.js';
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
  list: get('tasks.list') === 'people' ? 'people' : 'tasks',
  filters: read('tasks.filters', { flagged: true, normal: true, waiting: true }),
  collapsed: read('tasks.collapsed', {}),
  who: get('tasks.who') === 'mine' ? 'mine' : 'all',
  // People filters. The search box is left out on purpose: a search that is
  // still there next week reads as people having gone missing.
  pf: { ...P.NO_FILTERS, ...read('people.filters', {}), q: '' },
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

// The People list lives next to the tasks, in its own file.
const PEOPLE_PATH = 'people.json';
// Where the deeper notes on a person live: knowledge/people/ in this repo.
const NOTES_REPO = 'start-up';

function makeStore() {
  const c = config();
  if (c.owner && c.repo && c.token) return new GitHubStore(c);
  return new LocalStore();
}

function makePeopleStore() {
  const c = config();
  if (c.owner && c.repo && c.token) return new GitHubStore({ ...c, path: PEOPLE_PATH, codec: P.codec });
  return new LocalStore({ key: 'people.state', codec: P.codec });
}

const PEOPLE_KEYS = { mirror: 'people.mirror', queue: 'people.queue' };
const newPeopleSync = () => new Sync({ store: makePeopleStore(), storage: LS, codec: P.codec, keys: PEOPLE_KEYS });

let sync = new Sync({ store: makeStore(), storage: LS });
let psync = newPeopleSync();
let reordering = false;
let dragging = null;
let undoTimer = null;

const hasPeople = () => (sync.state.people || []).length > 0;
const ownerFilter = () => (view.who === 'mine' && me() ? me() : null);

// --- rendering -------------------------------------------------------------

function render() {
  if (dragging) return; // never rebuild the DOM out from under a finger
  const people = view.list === 'people';
  document.body.dataset.list = view.list;
  $('list-tasks').setAttribute('aria-pressed', String(!people));
  $('list-people').setAttribute('aria-pressed', String(people));
  for (const id of ['tfilters', 'list', 'add']) $(id).hidden = people;
  for (const id of ['pfilters', 'plist', 'padd']) $(id).hidden = !people;
  if (people) {
    $('empty').hidden = true;
    renderPeople();
  } else {
    $('pempty').hidden = true;
    renderTasks();
  }
  renderWho();
}

function renderTasks() {
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

  fillTopicSelect($('add-topic'), state, $('add-topic').value || 'inbox');
  fillOwnerSelect($('add-owner'), state, addOwner ?? me(), '—');
  $('add-owner').hidden = !hasPeople();
}

function renderWho() {
  const mine = view.who === 'mine' && !!me();
  $('who').hidden = !hasPeople();
  $('who').setAttribute('aria-label', view.list === 'people' ? 'Whose contacts' : 'Whose tasks');
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
  showUndoWith('Done', () => sync.apply({ op: 'restore', task, index: Math.max(0, index) }));
}

function showUndoWith(text, undo) {
  const bar = $('snackbar');
  $('snackbar-text').textContent = text;
  $('undo').hidden = false;
  bar.hidden = false;
  if (undoTimer) clearTimeout(undoTimer);
  undoTimer = setTimeout(() => { bar.hidden = true; }, 6000);
  $('undo').onclick = () => {
    undo();
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

// --- people ----------------------------------------------------------------

const pad2 = (n) => String(n).padStart(2, '0');

// The phone's own date, not UTC: a contact logged at 1am is logged today.
function today() {
  const d = new Date();
  return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`;
}

function daysBetween(from, to) {
  return Math.round((Date.parse(to) - Date.parse(from)) / 86400000);
}

function peopleFilters() {
  const mine = view.who === 'mine' && me();
  return { ...view.pf, knownBy: mine ? me() : '' };
}

function renderPeople() {
  const state = psync.state;
  const f = peopleFilters();
  const day = today();
  const { due, rest, total } = P.sections(state, f, day);

  $('pf-now-label').textContent = `Now · ${P.stageTitle(state)}`;
  $('pf-now').checked = !!view.pf.now;
  $('pf-update').checked = !!view.pf.update;
  fillChoice($('pf-role'), 'Any role', state.roles.map((r) => [r.id, r.title]), view.pf.role);
  fillChoice($('pf-tag'), 'Any tag', P.allTags(state).map((t) => [t, `#${t}`]), view.pf.tag);
  fillChoice($('pf-tie'), 'Any tie', P.TIES.map((x) => [x, P.TIE_LABELS[x]]), view.pf.tie);
  for (const id of ['pf-role', 'pf-tag', 'pf-tie']) $(id).classList.toggle('on', !!$(id).value);

  const sections = [];
  if (due.length) sections.push(peopleSection('due', 'Due to reach out', due, day));
  if (rest.length) sections.push(peopleSection('all', due.length ? 'Everyone else' : 'People', rest, day));
  $('plist').replaceChildren(...sections);

  const empty = $('pempty');
  const shown = due.length + rest.length;
  if (shown) {
    empty.hidden = true;
  } else {
    empty.hidden = false;
    const cfg = config();
    if (!total && !(cfg.owner && cfg.repo && cfg.token)) {
      empty.textContent = 'Not connected to GitHub yet. Tap ⚙ above, pick who you are and paste your token.';
    } else if (!total) {
      empty.textContent = 'No one here yet. Add someone below, or tap + with the box empty to paste a list.';
    } else {
      empty.textContent = `Nobody matches. ${total} ${total === 1 ? 'person is' : 'people are'} hidden by the filters above.`;
    }
  }
}

function fillChoice(sel, anyLabel, pairs, selected) {
  const known = pairs.some(([v]) => v === selected);
  sel.replaceChildren(
    option('', anyLabel, !selected || !known),
    ...pairs.map(([v, label]) => option(v, label, v === selected)),
  );
}

function peopleSection(key, title, list, day) {
  const sec = document.createElement('section');
  sec.className = 'topic';
  sec.dataset.section = key;
  const head = document.createElement('h2');
  head.className = 'topic-head';
  head.innerHTML = `<span>${escapeHtml(title)}</span><span class="count">${list.length}</span>`;
  const ul = document.createElement('ul');
  ul.className = 'people';
  for (const c of list) ul.appendChild(personRow(c, key === 'due' ? day : null));
  sec.append(head, ul);
  return sec;
}

function personRow(c, day) {
  const li = document.createElement('li');
  li.className = 'person';
  li.dataset.id = c.id;
  const b = document.createElement('button');
  b.type = 'button';
  b.className = 'person-main';
  const top = document.createElement('span');
  top.className = 'pname';
  top.dir = 'auto';
  top.textContent = c.name;
  b.appendChild(top);
  const sub = [c.line, c.org && !c.line.includes(c.org) ? c.org : ''].filter(Boolean).join(' · ');
  if (sub) {
    const s = document.createElement('span');
    s.className = 'pline';
    s.dir = 'auto';
    s.textContent = sub;
    b.appendChild(s);
  }
  const meta = document.createElement('span');
  meta.className = 'pmeta';
  if (day) {
    const d = P.dueDate(c);
    const late = d ? daysBetween(d, day) : null;
    const tag = document.createElement('span');
    tag.className = 'due';
    tag.textContent = d === '' ? 'never' : late > 0 ? `${late}d late` : 'today';
    meta.appendChild(tag);
  }
  if (!(view.who === 'mine' && me())) {
    for (const id of c.knownBy) {
      const o = document.createElement('span');
      o.className = 'owner' + (id === me() ? ' owner-me' : '');
      o.textContent = P.teamName(psync.state, id);
      meta.appendChild(o);
    }
  }
  if (c.tie === 'unmet') {
    const u = document.createElement('span');
    u.className = 'unmet';
    u.textContent = 'not met';
    meta.appendChild(u);
  }
  b.appendChild(meta);
  b.addEventListener('click', () => openPerson(c.id));
  li.appendChild(b);
  return li;
}

function setList(list) {
  view.list = list;
  set('tasks.list', list);
  render();
}

$('list-tasks').addEventListener('click', () => setList('tasks'));
$('list-people').addEventListener('click', () => setList('people'));

$('p-q').addEventListener('input', () => {
  view.pf.q = $('p-q').value;
  renderPeople();
});

function savePeopleFilters() {
  const { q, ...keep } = view.pf;
  write('people.filters', keep);
  renderPeople();
}

$('pf-now').addEventListener('change', () => { view.pf.now = $('pf-now').checked; savePeopleFilters(); });
$('pf-update').addEventListener('change', () => { view.pf.update = $('pf-update').checked; savePeopleFilters(); });
for (const [id, key] of [['pf-role', 'role'], ['pf-tag', 'tag'], ['pf-tie', 'tie']]) {
  $(id).addEventListener('change', () => { view.pf[key] = $(id).value; savePeopleFilters(); });
}

// Who knows someone you add: what the text says (@gal), or else you.
function defaultKnownBy(parsed) {
  return parsed.knownBy.length ? parsed.knownBy : me() ? [me()] : [];
}

function addPerson(parsed) {
  const m = P.addMutation({
    name: parsed.name, line: parsed.line, tags: parsed.tags, roles: parsed.roles, knownBy: defaultKnownBy(parsed),
  }, { now: new Date().toISOString(), id: P.makeId() });
  psync.apply(m);
  return m.contact;
}

$('padd').addEventListener('submit', (e) => {
  e.preventDefault();
  const input = $('padd-text');
  const raw = input.value.trim();
  if (!raw) {
    openMany();
    return;
  }
  const parsed = P.parseQuickAdd(raw, psync.state);
  if (!parsed.name) return;
  const c = addPerson(parsed);
  input.value = '';
  render();
  if (!P.matches(psync.state, c, peopleFilters())) showNote(`Added ${c.name}, but hidden by the filters above`);
  else showNote(`Added ${c.name}`);
});

function openMany() {
  $('pm-text').value = '';
  $('pmany').showModal();
}

$('pmany').addEventListener('close', () => {
  if ($('pmany').returnValue !== 'add') return;
  const list = P.parseMany($('pm-text').value, psync.state);
  for (const parsed of list) addPerson(parsed);
  render();
  if (list.length) showNote(`Added ${list.length} ${list.length === 1 ? 'person' : 'people'}`);
});

// --- person sheet ----------------------------------------------------------

let detailPerson = null; // the card's id while its sheet is open

const contact = (id) => psync.state.contacts.find((c) => c.id === id) || null;

function checkChips(box, name, pairs, selected) {
  box.replaceChildren(...pairs.map(([value, label]) => {
    const l = document.createElement('label');
    l.className = 'chip';
    const i = document.createElement('input');
    i.type = 'checkbox';
    i.name = name;
    i.value = value;
    i.checked = selected.includes(value);
    const s = document.createElement('span');
    s.className = 'chip-face';
    s.textContent = label;
    l.append(i, s);
    return l;
  }));
}

const checked = (box) => [...box.querySelectorAll('input:checked')].map((i) => i.value);

function notesUrl(path) {
  const owner = config().owner;
  return owner && path ? `https://github.com/${owner}/${NOTES_REPO}/blob/main/${path.replace(/^\/+/, '')}` : '';
}

function openPerson(id) {
  const c = contact(id);
  if (!c) return;
  const s = psync.state;
  detailPerson = id;
  $('pd-name').value = c.name;
  $('pd-line').value = c.line;
  $('pd-org').value = c.org;
  $('pd-email').value = c.email;
  $('pd-tags').value = c.tags.join(', ');
  checkChips($('pd-roles'), 'role', s.roles.map((r) => [r.id, r.title]), c.roles);
  checkChips($('pd-known'), 'known', s.team.map((p) => [p.id, p.name]), c.knownBy);
  $('pd-intro').value = c.introVia;
  fillChoice($('pd-tie'), 'Not set', P.TIES.map((x) => [x, P.TIE_LABELS[x]]), c.tie);
  $('pd-every').replaceChildren(...P.INTERVALS.map((n) =>
    option(String(n), n === 0 ? 'No' : n === 1 ? 'Every month' : n === 12 ? 'Every year' : `Every ${n} months`, n === c.every)));
  $('pd-update').checked = c.update;
  $('pd-next').value = c.next;
  $('pd-notes').value = c.notes;
  $('pd-file').value = c.file;
  $('pd-log-date').value = today();
  $('pd-log-text').value = '';
  fillOwnerSelect($('pd-task-owner'), sync.state, me(), 'Nobody');
  $('pd-task-add').textContent = `Add “Talk to ${c.name}” to the board`;
  $('pd-task-msg').textContent = '';
  renderPersonExtras();
  $('pdetail').showModal();
}

// The parts of the sheet that change while it is open: the log and the link.
function renderPersonExtras() {
  const c = contact(detailPerson);
  if (!c) return;
  const url = notesUrl($('pd-file').value.trim());
  $('pd-file-link').hidden = !url;
  $('pd-file-link').href = url || '#';
  const last = P.lastContact(c);
  const d = P.dueDate(c);
  const bits = [last ? `Last contact ${last}` : 'No contact logged yet'];
  if (d !== null) bits.push(d === '' || d <= today() ? 'due now' : `next by ${d}`);
  $('pd-last').textContent = bits.join(' · ');
  $('pd-log').replaceChildren(...c.log.map((e) => {
    const li = document.createElement('li');
    const when = document.createElement('span');
    when.className = 'when';
    when.textContent = e.date + (e.by ? ` · ${P.teamName(psync.state, e.by)}` : '');
    const text = document.createElement('span');
    text.className = 'what';
    text.dir = 'auto';
    text.textContent = e.text || '—';
    const x = document.createElement('button');
    x.type = 'button';
    x.className = 'unlog';
    x.textContent = '×';
    x.setAttribute('aria-label', 'Remove this entry');
    x.addEventListener('click', () => {
      psync.apply({ op: 'unlog', id: c.id, name: c.name, entryId: e.id });
      renderPersonExtras();
    });
    li.append(when, text, x);
    return li;
  }));
}

$('pd-file').addEventListener('input', renderPersonExtras);

$('pd-log-add').addEventListener('click', () => {
  const c = contact(detailPerson);
  const date = $('pd-log-date').value || today();
  if (!c) return;
  psync.apply(P.logMutation(c, { date, by: me() || null, text: $('pd-log-text').value.trim() }));
  $('pd-log-text').value = '';
  renderPersonExtras();
});

$('pd-task-add').addEventListener('click', () => {
  const c = contact(detailPerson);
  if (!c) return;
  const owner = $('pd-task-owner').value || null;
  sync.apply(addMutation({
    text: `Talk to ${c.name}`,
    topic: resolveTopic(sync.state, 'network') || 'inbox',
    owner,
    note: [c.line, c.next].filter(Boolean).join('\n'),
    now: new Date().toISOString(),
    id: makeId(),
  }));
  $('pd-task-msg').textContent = `Added to the board for ${ownerLabel(sync.state, owner) || 'nobody'}.`;
});

$('pdetail').addEventListener('close', () => {
  const dlg = $('pdetail');
  const c = contact(detailPerson);
  detailPerson = null;
  if (!c) return;
  if (dlg.returnValue === 'remove') {
    psync.apply({ op: 'delete', id: c.id, name: c.name });
    render();
    showUndoWith(`Removed ${c.name}`, () => psync.apply({ op: 'restore', contact: c, name: c.name }));
    return;
  }
  if (dlg.returnValue !== 'save') return;
  const next = {
    name: $('pd-name').value.trim(),
    line: $('pd-line').value.trim(),
    org: $('pd-org').value.trim(),
    email: $('pd-email').value.trim(),
    tags: $('pd-tags').value.split(',').map((t) => t.trim()).filter(Boolean),
    roles: checked($('pd-roles')),
    knownBy: checked($('pd-known')),
    introVia: $('pd-intro').value.trim(),
    tie: $('pd-tie').value,
    every: Number($('pd-every').value),
    update: $('pd-update').checked,
    next: $('pd-next').value.trim(),
    notes: $('pd-notes').value,
    file: $('pd-file').value.trim(),
  };
  if (!next.name) delete next.name;
  // Only what changed goes in the mutation, so two people editing different
  // fields of one card at once both keep their change.
  const probe = P.normalizeContact({ ...c, ...next });
  const fields = {};
  for (const k of Object.keys(next)) {
    if (JSON.stringify(probe[k]) !== JSON.stringify(c[k])) fields[k] = next[k];
  }
  if (Object.keys(fields).length) psync.apply(P.setMutation(c, fields));
  render();
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
  $('s-stage').replaceChildren(...psync.state.stages.map((s) => option(s.id, s.title, s.id === psync.state.stage)));
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
  const stage = $('s-stage').value;
  await restart();
  if (stage && stage !== psync.state.stage) {
    psync.apply({ op: 'setStage', stage });
    render();
  }
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
  const ppending = psync.pending;
  sync = new Sync({ store: makeStore(), storage: LS });
  sync.pending = pending;
  psync = newPeopleSync();
  psync.pending = ppending;
  wireSync();
  await Promise.all([sync.start(), psync.start()]);
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

// One status for both lists: the most serious one wins.
const STATUS_RANK = ['idle', 'loading', 'saving', 'offline', 'error', 'auth-error'];

function combinedStatus() {
  const a = STATUS_RANK.indexOf(sync.status);
  const b = STATUS_RANK.indexOf(psync.status);
  return STATUS_RANK[Math.max(a, b)] || 'idle';
}

function wireSync() {
  for (const s of [sync, psync]) {
    s.addEventListener('state', render);
    s.addEventListener('status', onStatus);
  }
}

function onStatus() {
  const status = combinedStatus();
  $('status').dataset.status = status;
  const banner = $('banner');
  if (status === 'auth-error') {
    // Both lists use the same token, so both report a rejected one. Build the
    // banner once, or each report would stack another button.
    if (banner.dataset.kind === 'auth' && !banner.hidden) return;
    banner.dataset.kind = 'auth';
    banner.hidden = false;
    banner.innerHTML = '<b>GitHub rejected the token.</b><br>Tokens expire. Your changes are saved on this device and will sync once the token is replaced.<br>';
    const b = document.createElement('button');
    b.className = 'primary';
    b.textContent = 'Replace token';
    b.onclick = () => openSettings();
    banner.appendChild(b);
  } else if (status !== 'error') {
    banner.hidden = true;
    banner.dataset.kind = '';
  }
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
render();
Promise.all([sync.start(), psync.start()]).then(() => {
  checkTokenAge();
  render();
});

// The other person's changes arrive by polling: on return to the app, when
// the network comes back, and once a minute while the app is on screen.
const both = (fn) => { for (const s of [sync, psync]) fn(s); };
window.addEventListener('online', () => both((s) => { s.refresh(); s.flushSoon(0); }));
document.addEventListener('visibilitychange', () => {
  if (!document.hidden) both((s) => { s.refresh(); s.flushSoon(0); });
});
setInterval(() => { if (!document.hidden) both((s) => s.refresh()); }, POLL_MS);

if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => navigator.serviceWorker.register('sw.js').catch(() => {}));
}

// Exposed for the test harness only.
window.__tasks = { get sync() { return sync; }, get psync() { return psync; }, render, view };
