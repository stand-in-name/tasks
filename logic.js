// Pure task logic. No DOM, no network, no globals.
// Every UI action becomes a mutation; mutations are what get replayed on top of
// fresh remote state when a write conflicts. Keeping this file pure is what makes
// both the unit tests and the conflict replay honest. tools/tasks.py mirrors it.

export const STATES = ['normal', 'flagged', 'waiting'];

// Used only when the board file has no topics or people of its own. The board
// file (tasks.json in the data repo) is the real source: edit them there.
export const DEFAULT_TOPICS = [
  { id: 'inbox', title: 'Inbox' },
  { id: 'search', title: 'Search' },
  { id: 'network', title: 'Network' },
  { id: 'foundation', title: 'Foundation' },
  { id: 'tools', title: 'Tools' },
  { id: 'learning', title: 'Learning' },
  { id: 'admin', title: 'Admin' },
];

export const DEFAULT_PEOPLE = [
  { id: 'gur', name: 'Gur' },
  { id: 'gal', name: 'Gal' },
];

export const STALE_DAYS = 30;

const copy = (list) => list.map((x) => ({ ...x }));

export function emptyState(topics = DEFAULT_TOPICS, people = DEFAULT_PEOPLE) {
  return { version: 1, topics: copy(topics), people: copy(people), nextNum: 1, tasks: [] };
}

// --- helpers ---------------------------------------------------------------

export function makeId(rand = Math.random) {
  // Not security-sensitive; just needs to not collide within one list.
  let s = '';
  for (let i = 0; i < 10; i++) s += 'abcdefghijklmnopqrstuvwxyz0123456789'[Math.floor(rand() * 36)];
  return 't_' + s;
}

// --- numbers ---------------------------------------------------------------
// Every task has a number that is never reused, and a reference made of its
// topic's letter and that number: S-12. The number alone is the key; the letter
// follows the topic, so a task moved from Inbox to Search turns I-12 into S-12
// and 12 still finds it. nextNum is the counter, so a finished task's number
// doesn't come back.

const isNum = (n) => Number.isInteger(n) && n > 0;

export function nextNumber(state) {
  let next = isNum(state.nextNum) ? state.nextNum : 1;
  for (const t of state.tasks) if (isNum(t.num) && t.num >= next) next = t.num + 1;
  return next;
}

// Gives every task without a number one, oldest first, so a board from before
// numbers, or a hand-edited task, numbers the same way in every copy.
export function withNumbers(state) {
  let next = nextNumber(state);
  const missing = state.tasks
    .filter((t) => !isNum(t.num))
    .sort((a, b) => String(a.created || '').localeCompare(String(b.created || '')) || a.id.localeCompare(b.id));
  if (!missing.length && state.nextNum === next) return state;
  const given = new Map(missing.map((t) => [t.id, next++]));
  const tasks = state.tasks.map((t) => (given.has(t.id) ? normalizeTask({ ...t, num: given.get(t.id) }) : t));
  return { ...state, nextNum: next, tasks };
}

// A topic's own `letter`, if the board file gives one; otherwise the first
// letter of its title.
export function topicLetter(topic) {
  if (!topic) return '?';
  const own = typeof topic.letter === 'string' && topic.letter.trim();
  return (own || String(topic.title || topic.id).trim().charAt(0) || '?').toUpperCase();
}

export function taskRef(state, task) {
  const topic = state.topics.find((t) => t.id === effectiveTopic(state, task));
  return `${topicLetter(topic)}-${task.num}`;
}

// What tapping a task's reference copies, for pasting into a chat.
export function refLine(state, task) {
  return `${taskRef(state, task)} ${task.text}`;
}

export function topicIds(state) {
  return state.topics.map((t) => t.id);
}

// A task whose topic no longer exists must never become invisible.
export function effectiveTopic(state, task) {
  return topicIds(state).includes(task.topic) ? task.topic : 'inbox';
}

export function person(state, id) {
  return (state.people || []).find((p) => p.id === id) || null;
}

// What the row badge shows. An owner who has left the people list still shows,
// by id, rather than silently looking unowned.
export function ownerLabel(state, id) {
  if (!id) return '';
  const p = person(state, id);
  return p ? p.name : id;
}

// Every task belongs to someone: a task with no owner belongs to all of us.
// "Both" while the board has two people, "Everyone" once it has more.
export function sharedLabel(state) {
  return (state.people || []).length > 2 ? 'Everyone' : 'Both';
}

function indicesOfTopic(tasks, topicId) {
  const out = [];
  tasks.forEach((t, i) => {
    if (t.topic === topicId) out.push(i);
  });
  return out;
}

// Insert `task` so that it lands at position `index` *among that topic's tasks*,
// leaving every other topic's relative order untouched.
function insertIntoTopic(tasks, task, index) {
  const idxs = indicesOfTopic(tasks, task.topic);
  const clamped = Math.max(0, Math.min(index, idxs.length));
  let at;
  if (idxs.length === 0) at = tasks.length;
  else if (clamped === idxs.length) at = idxs[idxs.length - 1] + 1;
  else at = idxs[clamped];
  const next = tasks.slice();
  next.splice(at, 0, task);
  return next;
}

// --- mutations -------------------------------------------------------------
// Each returns a NEW state. Unknown ops and unknown ids are no-ops rather than
// throwing, so replaying a stale queue can never wedge the app.

export function applyMutation(state, m) {
  switch (m.op) {
    case 'add':
    case 'restore': {
      if (state.tasks.some((t) => t.id === m.task.id)) return state;
      // The number is given here, not when the mutation is built, so a change
      // replayed on a fresher board takes the next free number there. An undone
      // finish keeps its own number, unless another task holds it.
      const next = nextNumber(state);
      const own = m.task.num;
      const num = isNum(own) && !state.tasks.some((t) => t.num === own) ? own : next;
      const task = normalizeTask({ ...m.task, num });
      const nextNum = Math.max(next, task.num + 1);
      return { ...state, nextNum, tasks: insertIntoTopic(state.tasks, task, m.index ?? 0) };
    }
    case 'setState': {
      if (!STATES.includes(m.state)) return state;
      return mapTask(state, m.id, (t) => ({ ...t, state: m.state }));
    }
    case 'setOwner': {
      // One owner, or none, which means the task belongs to all of us. Never a
      // list: a task for some of us is handed to one of them.
      if (m.owner !== null && typeof m.owner !== 'string') return state;
      return mapTask(state, m.id, (t) => ({ ...t, owner: m.owner || null }));
    }
    case 'setText':
      return mapTask(state, m.id, (t) => ({ ...t, text: m.text }));
    case 'setNote':
      return mapTask(state, m.id, (t) => ({ ...t, note: m.note }));
    case 'delete':
      return { ...state, tasks: state.tasks.filter((t) => t.id !== m.id) };
    case 'move': {
      const from = state.tasks.findIndex((t) => t.id === m.id);
      if (from < 0) return state;
      const tasks = state.tasks.slice();
      const [task] = tasks.splice(from, 1);
      return { ...state, tasks: insertIntoTopic(tasks, { ...task, topic: m.topic }, m.index) };
    }
    default:
      return state;
  }
}

export function applyAll(state, mutations) {
  return mutations.reduce(applyMutation, state);
}

function mapTask(state, id, fn) {
  let hit = false;
  const tasks = state.tasks.map((t) => {
    if (t.id !== id) return t;
    hit = true;
    return fn(t);
  });
  return hit ? { ...state, tasks } : state;
}

// --- mutation builders -----------------------------------------------------

export function addMutation({ text, topic = 'inbox', state = 'normal', owner = null, note = '', now, id }) {
  return {
    op: 'add',
    index: 0, // new tasks land at the top of their topic
    task: { id: id || makeId(), text: text.trim(), topic, state, owner: owner || null, note, created: now },
  };
}

// The flag and the waiting marker are toggles: pressing the state a task
// already has returns it to normal. States are mutually exclusive.
export function toggleStateMutation(task, target) {
  return { op: 'setState', id: task.id, state: task.state === target ? 'normal' : target };
}

// --- views -----------------------------------------------------------------

export const ALL_VISIBLE = { flagged: true, normal: true, waiting: true };

// `owner` null means everyone's tasks; otherwise that person's, plus the tasks
// with no owner, which belong to all of us.
export function ownedBy(task, owner) {
  return !owner || !task.owner || task.owner === owner;
}

export function isStale(task, now, days = STALE_DAYS) {
  if (!task.created) return false;
  const ageMs = Date.parse(now) - Date.parse(task.created);
  if (Number.isNaN(ageMs)) return false;
  return ageMs >= days * 86400000;
}

export function daysOld(task, now) {
  if (!task.created) return 0;
  const ageMs = Date.parse(now) - Date.parse(task.created);
  return Number.isNaN(ageMs) ? 0 : Math.floor(ageMs / 86400000);
}

// Returns [{ topic, tasks }] in topic order, skipping topics with nothing to show.
export function grouped(state, filters = ALL_VISIBLE, owner = null) {
  const byTopic = new Map(state.topics.map((t) => [t.id, []]));
  for (const task of state.tasks) {
    if (!filters[task.state] || !ownedBy(task, owner)) continue;
    const key = effectiveTopic(state, task);
    if (byTopic.has(key)) byTopic.get(key).push(task);
  }
  return state.topics
    .map((topic) => ({ topic, tasks: byTopic.get(topic.id) }))
    .filter((g) => g.tasks.length > 0);
}

export function counts(state, owner = null) {
  const c = { flagged: 0, normal: 0, waiting: 0, total: 0 };
  for (const t of state.tasks) {
    if (!ownedBy(t, owner)) continue;
    c.total++;
    if (c[t.state] !== undefined) c[t.state]++;
  }
  return c;
}

// --- quick add -------------------------------------------------------------
// Three shortcuts, in any order at the start: `!` flags, `topic:` files, and
// `@name` assigns. Everything else is the task text verbatim. On the phone the
// pickers do the same job; this exists so the CLI and the app share one input
// format. `topic` and `owner` come back null when the text doesn't name them,
// so the caller's picker or default decides.

export function parseQuickAdd(input, state) {
  let rest = String(input);
  let flagged = false;
  let topic = null;
  let owner = null;

  for (;;) {
    const before = rest;
    rest = rest.replace(/^\s+/, '');
    if (rest.startsWith('!')) {
      flagged = true;
      rest = rest.slice(1);
    }
    if (owner === null) {
      const m = rest.match(/^@([\p{L}\d._-]+)\s*/u);
      if (m) {
        const found = resolvePerson(state, m[1]);
        if (found) {
          owner = found;
          rest = rest.slice(m[0].length);
        }
      }
    }
    if (topic === null) {
      const m = rest.match(/^([\p{L}][\p{L}\d _-]*):\s*/u);
      if (m) {
        const found = resolveTopic(state, m[1]);
        if (found) {
          topic = found;
          rest = rest.slice(m[0].length);
        }
      }
    }
    if (rest === before) break;
  }

  return { text: rest.trim(), topic, state: flagged ? 'flagged' : 'normal', owner };
}

const norm = (s) => String(s).trim().toLowerCase().replace(/[\s_]+/g, '-');

export function resolveTopic(state, name) {
  const target = norm(name);
  const hit = state.topics.find((t) => norm(t.id) === target || norm(t.title) === target);
  return hit ? hit.id : null;
}

export function resolvePerson(state, name) {
  const target = norm(name);
  const hit = (state.people || []).find((p) => norm(p.id) === target || norm(p.name) === target);
  return hit ? hit.id : null;
}

// --- serialization ---------------------------------------------------------
// One canonical shape and key order, shared with tools/tasks.py, so that every
// writer produces byte-identical files and the git history shows the change,
// not a reformat. Fields this version doesn't know are kept, after the known
// ones: an older copy of the app must never strip what a newer writer added.

const TOP_KEYS = ['version', 'topics', 'people', 'nextNum', 'tasks'];

function withExtras(known, raw) {
  for (const [k, v] of Object.entries(raw)) if (!(k in known)) known[k] = v;
  return known;
}

function normalizeTask(t) {
  const known = { id: String(t.id) };
  if (isNum(t.num)) known.num = t.num;
  return withExtras(Object.assign(known, {
    text: String(t.text),
    topic: String(t.topic || 'inbox'),
    state: STATES.includes(t.state) ? t.state : 'normal',
    owner: typeof t.owner === 'string' && t.owner ? t.owner : null,
    note: typeof t.note === 'string' ? t.note : '',
    created: t.created || null,
  }), t);
}

export function serialize(state) {
  const out = withExtras({
    version: state.version || 1,
    topics: state.topics,
    people: state.people || [],
    nextNum: nextNumber(state),
    tasks: state.tasks,
  }, state);
  return JSON.stringify(out, null, 2) + '\n';
}

// Tolerant on purpose: a hand-edited file with a missing field should degrade,
// not blank the list.
export function deserialize(text) {
  let raw;
  try {
    raw = JSON.parse(text);
  } catch {
    return null;
  }
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return null;
  const topics = Array.isArray(raw.topics) && raw.topics.length
    ? raw.topics.filter((t) => t && t.id).map((t) => withExtras({ id: String(t.id), title: String(t.title || t.id) }, t))
    : copy(DEFAULT_TOPICS);
  const people = Array.isArray(raw.people)
    ? raw.people.filter((p) => p && p.id).map((p) => withExtras({ id: String(p.id), name: String(p.name || p.id) }, p))
    : copy(DEFAULT_PEOPLE);
  const tasks = (Array.isArray(raw.tasks) ? raw.tasks : [])
    .filter((t) => t && t.id && typeof t.text === 'string')
    .map(normalizeTask);
  const state = {
    version: typeof raw.version === 'number' ? raw.version : 1,
    topics,
    people,
    nextNum: raw.nextNum,
    tasks,
  };
  for (const [k, v] of Object.entries(raw)) if (!TOP_KEYS.includes(k)) state[k] = v;
  return withNumbers(state);
}
