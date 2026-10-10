// Pure logic for the People list, the network map. No DOM, no network.
// Same discipline as logic.js: every change is a mutation, unknown ops and ids
// are no-ops, and the file has one canonical shape that tools/people.py
// shares byte for byte.

export const TIES = ['close', 'acquaintance', 'dormant', 'unmet'];
export const TIE_LABELS = { close: 'Close', acquaintance: 'Acquaintance', dormant: 'Dormant', unmet: 'Not met yet' };

// Keep-in-touch intervals, in months. 0 means none: an on-demand contact.
export const INTERVALS = [0, 1, 3, 6, 12];

// Used only when the file has no lists of its own. people.json in the data
// repo is the real source: edit them there.
export const DEFAULT_TEAM = [
  { id: 'gur', name: 'Gur' },
  { id: 'gal', name: 'Gal' },
];

export const DEFAULT_ROLES = [
  { id: 'champion', title: 'Champion' },
  { id: 'like-minded', title: 'Like-minded' },
  { id: 'expert', title: 'Expert' },
  { id: 'investor', title: 'Investor' },
  { id: 'customer', title: 'Customer' },
  { id: 'channel', title: 'Channel' },
  { id: 'design-partner', title: 'Design partner' },
  { id: 'supplier', title: 'Supplier' },
  { id: 'talent', title: 'Talent' },
];

// Who matters at each stage, from the roadmap's network track. Champions and
// talent run through every stage.
export const DEFAULT_STAGES = [
  { id: 'search', title: 'Search', roles: ['champion', 'like-minded', 'expert', 'investor', 'talent'] },
  { id: 'commit', title: 'Commit', roles: ['champion', 'expert', 'talent'] },
  { id: 'fill', title: 'Fill in the square', roles: ['champion', 'customer', 'channel', 'design-partner', 'investor', 'talent'] },
  { id: 'validate', title: 'Hard-validate', roles: ['champion', 'design-partner', 'customer', 'supplier', 'investor', 'talent'] },
  { id: 'scale', title: 'Scale', roles: ['champion', 'channel', 'customer', 'supplier', 'investor', 'talent'] },
];

const copy = (list) => list.map((x) => ({ ...x, ...(x.roles ? { roles: x.roles.slice() } : {}) }));

export function emptyState() {
  return {
    version: 1,
    team: copy(DEFAULT_TEAM),
    roles: copy(DEFAULT_ROLES),
    stages: copy(DEFAULT_STAGES),
    stage: 'search',
    contacts: [],
  };
}

// --- helpers ---------------------------------------------------------------

export function makeId(prefix = 'p_', rand = Math.random) {
  let s = '';
  for (let i = 0; i < 10; i++) s += 'abcdefghijklmnopqrstuvwxyz0123456789'[Math.floor(rand() * 36)];
  return prefix + s;
}

export const norm = (s) => String(s).trim().toLowerCase().replace(/[\s_]+/g, '-');

function withExtras(known, raw) {
  for (const [k, v] of Object.entries(raw)) if (!(k in known)) known[k] = v;
  return known;
}

const isStr = (v) => typeof v === 'string';
const line = (v) => (isStr(v) ? v.trim() : '');

// A list of ids or tags: strings only, normalized, no blanks, no repeats.
function idList(v) {
  const out = [];
  if (!Array.isArray(v)) return out;
  for (const x of v) {
    if (!isStr(x)) continue;
    const n = norm(x);
    if (n && !out.includes(n)) out.push(n);
  }
  return out;
}

function months(v) {
  return typeof v === 'number' && Number.isInteger(v) && v >= 0 ? v : null;
}

const DATE = /^\d{4}-\d{2}-\d{2}$/;

function normalizeEntry(e) {
  return withExtras({
    id: String(e.id || ''),
    date: isStr(e.date) ? e.date : '',
    by: isStr(e.by) && e.by ? e.by : null,
    text: isStr(e.text) ? e.text : '',
  }, e);
}

export function normalizeContact(c) {
  return withExtras({
    id: String(c.id),
    name: String(c.name).trim(),
    line: line(c.line),
    org: line(c.org),
    email: line(c.email),
    tags: idList(c.tags),
    roles: idList(c.roles),
    knownBy: idList(c.knownBy),
    introVia: line(c.introVia),
    tie: TIES.includes(c.tie) ? c.tie : '',
    every: months(c.every) ?? 0,
    update: c.update === true,
    next: line(c.next),
    notes: isStr(c.notes) ? c.notes : '',
    file: line(c.file),
    log: (Array.isArray(c.log) ? c.log : []).filter((e) => e && typeof e === 'object' && !Array.isArray(e)).map(normalizeEntry),
    created: c.created || null,
  }, c);
}

// --- mutations -------------------------------------------------------------

// What a 'set' may change, and how each value is checked. Anything else in
// `fields` is ignored, so a stale or hand-made mutation can't corrupt a card.
const EDITABLE = {
  name: (v) => (isStr(v) && v.trim() ? v.trim() : undefined),
  line: (v) => (isStr(v) ? v.trim() : undefined),
  org: (v) => (isStr(v) ? v.trim() : undefined),
  email: (v) => (isStr(v) ? v.trim() : undefined),
  tags: (v) => (Array.isArray(v) ? idList(v) : undefined),
  roles: (v) => (Array.isArray(v) ? idList(v) : undefined),
  knownBy: (v) => (Array.isArray(v) ? idList(v) : undefined),
  introVia: (v) => (isStr(v) ? v.trim() : undefined),
  tie: (v) => (v === '' || TIES.includes(v) ? v : undefined),
  every: (v) => months(v) ?? undefined,
  update: (v) => (typeof v === 'boolean' ? v : undefined),
  next: (v) => (isStr(v) ? v.trim() : undefined),
  notes: (v) => (isStr(v) ? v : undefined),
  file: (v) => (isStr(v) ? v.trim() : undefined),
};

function applyFields(c, fields) {
  if (!fields || typeof fields !== 'object') return c;
  const out = { ...c };
  for (const [k, check] of Object.entries(EDITABLE)) {
    if (!(k in fields)) continue;
    const v = check(fields[k]);
    if (v !== undefined) out[k] = v;
  }
  return out;
}

// Newest first. A new entry goes above older dates and above entries of the
// same date, so the last thing logged reads first.
function insertEntry(log, entry) {
  const at = log.findIndex((e) => !(e.date > entry.date));
  const next = log.slice();
  next.splice(at < 0 ? log.length : at, 0, entry);
  return next;
}

function mapContact(state, id, fn) {
  let hit = false;
  const contacts = state.contacts.map((c) => {
    if (c.id !== id) return c;
    hit = true;
    return fn(c);
  });
  return hit ? { ...state, contacts } : state;
}

export function applyMutation(state, m) {
  switch (m && m.op) {
    case 'add':
    case 'restore': {
      const c = m.contact;
      if (!c || !c.id || !isStr(c.name) || !c.name.trim()) return state;
      if (state.contacts.some((x) => x.id === String(c.id))) return state;
      return { ...state, contacts: [...state.contacts, normalizeContact(c)] };
    }
    case 'set':
      return mapContact(state, m.id, (c) => applyFields(c, m.fields));
    case 'log': {
      const e = m.entry;
      if (!e || !e.id || !isStr(e.date) || !DATE.test(e.date)) return state;
      return mapContact(state, m.id, (c) =>
        (c.log.some((x) => x.id === String(e.id)) ? c : { ...c, log: insertEntry(c.log, normalizeEntry(e)) }));
    }
    case 'unlog':
      return mapContact(state, m.id, (c) => ({ ...c, log: c.log.filter((e) => e.id !== m.entryId) }));
    case 'delete':
      return { ...state, contacts: state.contacts.filter((c) => c.id !== m.id) };
    case 'setStage':
      return state.stages.some((s) => s.id === m.stage) ? { ...state, stage: m.stage } : state;
    default:
      return state;
  }
}

export function applyAll(state, mutations) {
  return mutations.reduce(applyMutation, state);
}

// --- mutation builders -----------------------------------------------------

export function addMutation(fields, { now, id } = {}) {
  return {
    op: 'add',
    contact: normalizeContact({ ...fields, id: id || makeId(), created: now || null }),
  };
}

// Carries the name so the commit says who, not an id.
export function setMutation(contact, fields) {
  return { op: 'set', id: contact.id, name: contact.name, fields };
}

export function logMutation(contact, { date, by = null, text = '', id }) {
  return { op: 'log', id: contact.id, name: contact.name, entry: { id: id || makeId('l_'), date, by, text } };
}

// --- reading ---------------------------------------------------------------

export function lastContact(c) {
  let last = '';
  for (const e of c.log) if (DATE.test(e.date) && e.date > last) last = e.date;
  return last || null;
}

const pad = (n) => String(n).padStart(2, '0');

// Calendar months, clamped to the month's end: 31 Jan + 1 month is 28 Feb.
export function addMonths(date, n) {
  const [y, m, d] = date.split('-').map(Number);
  const total = (m - 1) + n;
  const yy = y + Math.floor(total / 12);
  const mm = ((total % 12) + 12) % 12;
  const dim = new Date(Date.UTC(yy, mm + 1, 0)).getUTCDate();
  return `${yy}-${pad(mm + 1)}-${pad(Math.min(d, dim))}`;
}

// null: not kept warm. '': never contacted, so due now. Otherwise the date the
// next touch falls due.
export function dueDate(c) {
  if (!c.every) return null;
  const last = lastContact(c);
  return last ? addMonths(last, c.every) : '';
}

export function isDue(c, today) {
  const d = dueDate(c);
  return d !== null && d <= today;
}

export function stageRoles(state) {
  const s = state.stages.find((x) => x.id === state.stage);
  return s ? s.roles : [];
}

export function stageTitle(state) {
  const s = state.stages.find((x) => x.id === state.stage);
  return s ? s.title : state.stage;
}

export const NO_FILTERS = { q: '', role: '', tag: '', tie: '', knownBy: '', update: false, now: false };

export function matches(state, c, f = NO_FILTERS) {
  if (f.q) {
    const hay = [c.name, c.line, c.org, c.email, c.introVia, c.next, c.notes, ...c.tags].join('\n').toLowerCase();
    if (!hay.includes(f.q.trim().toLowerCase())) return false;
  }
  if (f.role && !c.roles.includes(f.role)) return false;
  if (f.tag && !c.tags.includes(f.tag)) return false;
  if (f.tie && c.tie !== f.tie) return false;
  if (f.knownBy === 'nobody' && c.knownBy.length) return false;
  if (f.knownBy && f.knownBy !== 'nobody' && !c.knownBy.includes(f.knownBy)) return false;
  if (f.update && !c.update) return false;
  if (f.now) {
    const roles = stageRoles(state);
    if (!c.roles.some((r) => roles.includes(r))) return false;
  }
  return true;
}

const byName = (a, b) => a.name.localeCompare(b.name) || a.id.localeCompare(b.id);

// The People screen: who is due to be reached, most overdue first, and
// everyone else by name. Nobody shows twice.
export function sections(state, f, today) {
  const hits = state.contacts.filter((c) => matches(state, c, f));
  const due = hits.filter((c) => isDue(c, today))
    .sort((a, b) => dueDate(a).localeCompare(dueDate(b)) || byName(a, b));
  const dueIds = new Set(due.map((c) => c.id));
  const rest = hits.filter((c) => !dueIds.has(c.id)).sort(byName);
  return { due, rest, total: state.contacts.length };
}

export function allTags(state) {
  const seen = new Set();
  for (const c of state.contacts) for (const t of c.tags) seen.add(t);
  return [...seen].sort();
}

export function roleTitle(state, id) {
  const r = state.roles.find((x) => x.id === id);
  return r ? r.title : id;
}

export function teamName(state, id) {
  const p = state.team.find((x) => x.id === id);
  return p ? p.name : id;
}

// --- quick add -------------------------------------------------------------
// "Dana Levi, head baker at a bakery chain #baking +expert @gal"
// #tag, +role and @person can go anywhere. The first comma (or a spaced dash)
// splits the name from the one line about them. A +role or @person this list
// doesn't know stays in the text, so nothing typed is lost.

function resolve(list, key, value) {
  const target = norm(value);
  const hit = list.find((x) => norm(x.id) === target || norm(x[key]) === target);
  return hit ? hit.id : null;
}

export function parseQuickAdd(input, state) {
  const tags = [];
  const roles = [];
  const knownBy = [];
  const kept = [];
  for (const word of String(input).trim().split(/\s+/)) {
    const m = word.match(/^([#+@])(.+?)([,.;:]*)$/su);
    let used = false;
    if (m) {
      const [, sym, body, trail] = m;
      if (sym === '#') {
        const t = norm(body);
        if (t) { if (!tags.includes(t)) tags.push(t); used = true; }
      } else if (sym === '+') {
        const r = resolve(state.roles, 'title', body);
        if (r) { if (!roles.includes(r)) roles.push(r); used = true; }
      } else {
        const p = resolve(state.team, 'name', body);
        if (p) { if (!knownBy.includes(p)) knownBy.push(p); used = true; }
      }
      // A comma right after a shortcut is still the name/line split.
      if (used && trail.includes(',')) kept.push(',');
    }
    if (!used && word) kept.push(word);
  }
  const text = kept.join(' ').replace(/ ,/g, ',');
  let name = text;
  let rest = '';
  const comma = text.indexOf(',');
  const dash = text.search(/ [—–-] /);
  if (comma >= 0) {
    name = text.slice(0, comma);
    rest = text.slice(comma + 1);
  } else if (dash >= 0) {
    name = text.slice(0, dash);
    rest = text.slice(dash + 3);
  }
  return { name: name.trim(), line: rest.trim(), tags, roles, knownBy };
}

// One person per non-empty line, for the first dump.
export function parseMany(text, state) {
  return String(text).split(/\r?\n/).map((l) => parseQuickAdd(l, state)).filter((p) => p.name);
}

// --- serialization ---------------------------------------------------------

const TOP_KEYS = ['version', 'team', 'roles', 'stages', 'stage', 'contacts'];

function named(list, key, fallback) {
  return Array.isArray(list) && list.length
    ? list.filter((x) => x && typeof x === 'object' && x.id)
      .map((x) => withExtras({ id: String(x.id), [key]: String(x[key] || x.id) }, x))
    : copy(fallback);
}

export function serialize(state) {
  const out = withExtras({
    version: state.version || 1,
    team: state.team,
    roles: state.roles,
    stages: state.stages,
    stage: state.stage,
    contacts: state.contacts,
  }, state);
  return JSON.stringify(out, null, 2) + '\n';
}

// Tolerant on purpose: a hand-edited file with a missing field degrades
// rather than blanking the list.
export function deserialize(text) {
  let raw;
  try {
    raw = JSON.parse(text);
  } catch {
    return null;
  }
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return null;
  const stages = Array.isArray(raw.stages) && raw.stages.length
    ? raw.stages.filter((s) => s && typeof s === 'object' && s.id)
      .map((s) => withExtras({ id: String(s.id), title: String(s.title || s.id), roles: idList(s.roles) }, s))
    : copy(DEFAULT_STAGES);
  const state = {
    version: typeof raw.version === 'number' ? raw.version : 1,
    team: named(raw.team, 'name', DEFAULT_TEAM),
    roles: named(raw.roles, 'title', DEFAULT_ROLES),
    stages,
    stage: isStr(raw.stage) && raw.stage ? raw.stage : 'search',
    contacts: (Array.isArray(raw.contacts) ? raw.contacts : [])
      .filter((c) => c && typeof c === 'object' && c.id && isStr(c.name) && c.name.trim())
      .map(normalizeContact),
  };
  for (const [k, v] of Object.entries(raw)) if (!TOP_KEYS.includes(k)) state[k] = v;
  return state;
}

// --- commits ---------------------------------------------------------------

const LABELS = { add: 'add', restore: 'undo', set: 'edit', log: 'log', unlog: 'unlog', delete: 'remove', setStage: 'stage' };

export function commitMessage(mutations) {
  if (!mutations.length) return 'people: sync';
  if (mutations.length === 1) {
    const m = mutations[0];
    const what = m.contact ? m.contact.name : m.op === 'setStage' ? m.stage : m.name || m.id;
    return `people: ${LABELS[m.op] || m.op} ${String(what).slice(0, 60)}`;
  }
  return `people: ${mutations.length} changes`;
}

// What store.js and sync.js need to handle this list.
export const codec = { emptyState, serialize, deserialize, applyMutation, applyAll, commitMessage };
