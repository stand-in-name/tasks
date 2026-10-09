// Browser-side test suites. Run inside the real engines the phones use, so
// module semantics, TextEncoder, atob/btoa and fetch all behave as in production.

import * as L from '../logic.js';
import { GitHubStore, LocalStore, MemoryStorage, toBase64, fromBase64, AuthError, OfflineError, commitMessage } from '../store.js';
import { Sync } from '../sync.js';

const results = [];

function check(name, fn) {
  try {
    fn();
    results.push({ name, ok: true });
  } catch (e) {
    results.push({ name, ok: false, detail: e && e.message ? e.message : String(e) });
  }
}

async function checkAsync(name, fn) {
  try {
    await fn();
    results.push({ name, ok: true });
  } catch (e) {
    results.push({ name, ok: false, detail: e && e.message ? e.message : String(e) });
  }
}

function eq(actual, expected, msg = '') {
  const a = JSON.stringify(actual);
  const b = JSON.stringify(expected);
  if (a !== b) throw new Error(`${msg} expected ${b}, got ${a}`);
}
function ok(cond, msg) {
  if (!cond) throw new Error(msg || 'expected truthy');
}

const T0 = '2026-01-01T00:00:00.000Z';

function seed() {
  let s = L.emptyState();
  const add = (id, text, topic, state, owner = null) =>
    (s = L.applyMutation(s, { op: 'add', index: 999, task: { id, text, topic, state, owner, note: '', created: T0 } }));
  add('a', 'net one', 'network', 'normal', 'gur');
  add('b', 'net two', 'network', 'flagged', 'gal');
  add('c', 'net three', 'network', 'waiting');
  add('d', 'search one', 'search', 'normal', 'gur');
  return s;
}
const ids = (s, topic) => s.tasks.filter((t) => t.topic === topic).map((t) => t.id);
const find = (s, id) => s.tasks.find((t) => t.id === id);

// --- logic -----------------------------------------------------------------

export function runLogic() {
  results.length = 0;

  check('add places a new task at the top of its topic', () => {
    const s = L.applyMutation(seed(), L.addMutation({ text: 'fresh', topic: 'network', now: T0, id: 'z' }));
    eq(ids(s, 'network'), ['z', 'a', 'b', 'c']);
  });

  check('add does not disturb other topics', () => {
    const s = L.applyMutation(seed(), L.addMutation({ text: 'fresh', topic: 'network', now: T0, id: 'z' }));
    eq(ids(s, 'search'), ['d']);
  });

  check('add into an empty topic works', () => {
    const s = L.applyMutation(seed(), L.addMutation({ text: 'x', topic: 'admin', now: T0, id: 'z' }));
    eq(ids(s, 'admin'), ['z']);
  });

  check('add records the owner, and no owner as null', () => {
    let s = L.applyMutation(seed(), L.addMutation({ text: 'mine', owner: 'gal', now: T0, id: 'z1' }));
    s = L.applyMutation(s, L.addMutation({ text: 'nobody', now: T0, id: 'z2' }));
    eq(find(s, 'z1').owner, 'gal');
    eq(find(s, 'z2').owner, null);
  });

  check('flag toggle sets flagged, and twice returns to normal', () => {
    let s = seed();
    s = L.applyMutation(s, L.toggleStateMutation(find(s, 'a'), 'flagged'));
    eq(find(s, 'a').state, 'flagged');
    s = L.applyMutation(s, L.toggleStateMutation(find(s, 'a'), 'flagged'));
    eq(find(s, 'a').state, 'normal');
  });

  check('states are mutually exclusive: flagging a waiting task makes it flagged', () => {
    const s = seed();
    const s2 = L.applyMutation(s, L.toggleStateMutation(find(s, 'c'), 'flagged'));
    eq(find(s2, 'c').state, 'flagged');
  });

  check('setOwner hands a task over, and null clears it', () => {
    let s = L.applyMutation(seed(), { op: 'setOwner', id: 'a', owner: 'gal' });
    eq(find(s, 'a').owner, 'gal');
    s = L.applyMutation(s, { op: 'setOwner', id: 'a', owner: null });
    eq(find(s, 'a').owner, null);
  });

  check('setOwner rejects anything but a name or null (one owner, never a list)', () => {
    const s = seed();
    eq(L.applyMutation(s, { op: 'setOwner', id: 'a', owner: ['gur', 'gal'] }), s);
  });

  check('delete removes the task', () => {
    const s = L.applyMutation(seed(), { op: 'delete', id: 'b' });
    eq(ids(s, 'network'), ['a', 'c']);
  });

  check('restore puts the task back at its original index, owner included', () => {
    const before = seed();
    const task = find(before, 'b');
    const idx = ids(before, 'network').indexOf('b');
    let s = L.applyMutation(before, { op: 'delete', id: 'b' });
    s = L.applyMutation(s, { op: 'restore', task, index: idx });
    eq(ids(s, 'network'), ['a', 'b', 'c']);
    eq(find(s, 'b').owner, 'gal');
  });

  check('move reorders within a topic', () => {
    const s = L.applyMutation(seed(), { op: 'move', id: 'c', topic: 'network', index: 0 });
    eq(ids(s, 'network'), ['c', 'a', 'b']);
  });

  check('move to the end of a topic', () => {
    const s = L.applyMutation(seed(), { op: 'move', id: 'a', topic: 'network', index: 2 });
    eq(ids(s, 'network'), ['b', 'c', 'a']);
  });

  check('move across topics keeps the owner', () => {
    const s = L.applyMutation(seed(), { op: 'move', id: 'a', topic: 'search', index: 0 });
    eq(ids(s, 'network'), ['b', 'c']);
    eq(ids(s, 'search'), ['a', 'd']);
    eq(find(s, 'a').owner, 'gur');
  });

  check('grouped hides filtered-out states', () => {
    const g = L.grouped(seed(), { flagged: true, normal: false, waiting: false });
    eq(g.map((x) => x.topic.id), ['network']);
    eq(g[0].tasks.map((t) => t.id), ['b']);
  });

  check('grouped with an owner shows only that person\'s tasks', () => {
    const g = L.grouped(seed(), L.ALL_VISIBLE, 'gur');
    eq(g.map((x) => [x.topic.id, x.tasks.map((t) => t.id)]), [['search', ['d']], ['network', ['a']]]);
  });

  check('grouped follows the topic order in the file, not insertion order', () => {
    // Seeded network first, but the file lists search before network.
    eq(L.grouped(seed(), L.ALL_VISIBLE).map((x) => x.topic.id), ['search', 'network']);
  });

  check('a task in an unknown topic still shows, under Inbox', () => {
    let s = seed();
    s = L.applyMutation(s, { op: 'add', index: 0, task: { id: 'orphan', text: 'lost', topic: 'nope', state: 'normal', note: '', created: T0 } });
    eq(L.effectiveTopic(s, find(s, 'orphan')), 'inbox');
    const g = L.grouped(s, L.ALL_VISIBLE);
    ok(g.find((x) => x.topic.id === 'inbox').tasks.some((t) => t.id === 'orphan'), 'orphan not shown under inbox');
  });

  check('counts report per state, for everyone or for one person', () => {
    eq(L.counts(seed()), { flagged: 1, normal: 2, waiting: 1, total: 4 });
    eq(L.counts(seed(), 'gur'), { flagged: 0, normal: 2, waiting: 0, total: 2 });
    eq(L.counts(seed(), 'gal'), { flagged: 1, normal: 0, waiting: 0, total: 1 });
  });

  check('stale marker is off at 29 days and on at 30', () => {
    const task = { created: '2026-01-01T00:00:00.000Z' };
    eq(L.isStale(task, '2026-01-30T00:00:00.000Z'), false);
    eq(L.isStale(task, '2026-01-31T00:00:00.000Z'), true);
    eq(L.daysOld(task, '2026-01-31T00:00:00.000Z'), 30);
  });

  check('a task with no created date is never stale', () => {
    eq(L.isStale({ created: null }, '2027-01-01T00:00:00.000Z'), false);
  });

  check('ownerLabel shows the name, falls back to the id, and is empty for nobody', () => {
    const s = seed();
    eq(L.ownerLabel(s, 'gal'), 'Gal');
    eq(L.ownerLabel(s, 'ex-member'), 'ex-member');
    eq(L.ownerLabel(s, null), '');
  });

  // quick add
  const q = (text) => L.parseQuickAdd(text, L.emptyState());

  check('quick add: leading ! flags', () => eq(q('!buy milk'), { text: 'buy milk', topic: null, state: 'flagged', owner: null }));
  check('quick add: topic: prefix files the task', () => eq(q('search: map the field').topic, 'search'));
  check('quick add: @name assigns, by id or by name', () => {
    eq(q('@gal check the rules'), { text: 'check the rules', topic: null, state: 'normal', owner: 'gal' });
    eq(q('@Gur call Dana').owner, 'gur');
  });
  check('quick add: shortcuts combine in any order', () => {
    for (const s of ['@gal !search: x', '!@gal search: x', 'search: @gal !x', '! search: @gal x']) {
      eq(q(s), { text: 'x', topic: 'search', state: 'flagged', owner: 'gal' }, s);
    }
  });
  check('quick add: an unknown @name or prefix stays part of the text', () => {
    eq(q('@yossi said hi').text, '@yossi said hi');
    eq(q('note: call the bank'), { text: 'note: call the bank', topic: null, state: 'normal', owner: null });
  });
  check('quick add: topic title matches case-insensitively', () => {
    const s = { ...L.emptyState(), topics: [{ id: 'inbox', title: 'Inbox' }, { id: 'ai-native', title: 'AI Native' }] };
    eq(L.parseQuickAdd('AI Native: ship it', s).topic, 'ai-native');
  });
  check('quick add: Hebrew text survives intact', () => {
    eq(q('!@gal search: לבדוק את המדידה'), { text: 'לבדוק את המדידה', topic: 'search', state: 'flagged', owner: 'gal' });
  });

  // serialization
  check('serialize/deserialize round-trips', () => {
    const s = seed();
    eq(L.deserialize(L.serialize(s)), s);
  });

  check('serialize writes keys in one fixed order', () => {
    const t = L.deserialize(L.serialize(seed())).tasks[0];
    eq(Object.keys(t), ['id', 'text', 'topic', 'state', 'owner', 'note', 'created']);
    eq(Object.keys(JSON.parse(L.serialize(seed()))), ['version', 'topics', 'people', 'tasks']);
  });

  check('fields this version does not know survive a round trip', () => {
    const file = {
      version: 1,
      topics: [{ id: 'inbox', title: 'Inbox', color: 'teal' }],
      people: [{ id: 'gur', name: 'Gur', github: 'gur-git' }],
      tasks: [{ id: 'x', text: 'hi', topic: 'inbox', state: 'normal', owner: 'gur', note: '', created: T0, due: '2026-11-30' }],
      sprints: [{ id: 's1' }],
    };
    let s = L.deserialize(JSON.stringify(file));
    s = L.applyMutation(s, { op: 'setState', id: 'x', state: 'flagged' });
    const back = JSON.parse(L.serialize(s));
    eq(back.tasks[0].due, '2026-11-30');
    eq(back.topics[0].color, 'teal');
    eq(back.people[0].github, 'gur-git');
    eq(back.sprints, [{ id: 's1' }]);
  });

  check('deserialize survives Hebrew and quotes', () => {
    let s = L.emptyState();
    s = L.applyMutation(s, L.addMutation({ text: 'שלום "world" \\ ok', topic: 'inbox', now: T0, id: 'h' }));
    eq(L.deserialize(L.serialize(s)).tasks[0].text, 'שלום "world" \\ ok');
  });

  check('deserialize returns null on non-JSON', () => {
    eq(L.deserialize('not json at all'), null);
    eq(L.deserialize('[1,2]'), null);
  });

  check('deserialize drops malformed tasks but keeps the good ones', () => {
    const s = L.deserialize(JSON.stringify({ tasks: [{ id: 'x', text: 'fine' }, { id: 'y' }, null, { text: 'no id' }] }));
    eq(s.tasks.map((t) => t.id), ['x']);
    eq([s.tasks[0].state, s.tasks[0].owner], ['normal', null]);
  });

  check('a file with no topics or people gets the defaults; an empty people list stays empty', () => {
    const s = L.deserialize('{}');
    eq(s.topics.length, L.DEFAULT_TOPICS.length);
    eq(s.people.map((p) => p.id), ['gur', 'gal']);
    eq(L.deserialize('{"people": []}').people, []);
  });

  check('an unknown mutation op is a no-op', () => {
    const s = seed();
    eq(L.applyMutation(s, { op: 'nonsense', id: 'a' }), s);
  });

  check('mutating an unknown id is a no-op', () => {
    const s = seed();
    eq(L.applyMutation(s, { op: 'setOwner', id: 'ghost', owner: 'gal' }), s);
  });

  check('an invalid state is rejected', () => {
    const s = seed();
    eq(L.applyMutation(s, { op: 'setState', id: 'a', state: 'urgent' }), s);
  });

  check('adding a task that already exists is a no-op (replay safety)', () => {
    const s = seed();
    const m = { op: 'add', index: 0, task: { id: 'a', text: 'dup', topic: 'network', state: 'normal', owner: null, note: '', created: T0 } };
    eq(L.applyAll(s, [m, m]), s);
  });

  check('applyAll replays a queue in order', () => {
    const out = L.applyAll(seed(), [
      { op: 'setState', id: 'a', state: 'flagged' },
      { op: 'delete', id: 'b' },
      { op: 'move', id: 'c', topic: 'network', index: 0 },
      { op: 'setOwner', id: 'c', owner: 'gal' },
    ]);
    eq(ids(out, 'network'), ['c', 'a']);
    eq(find(out, 'a').state, 'flagged');
    eq(find(out, 'c').owner, 'gal');
  });

  return results;
}

// --- store -----------------------------------------------------------------

function mockRes(status, body) {
  return { ok: status >= 200 && status < 300, status, json: async () => body };
}

const b64json = (obj) => toBase64(JSON.stringify(obj));
const NO_WAIT = [0, 0, 0];

// A tiny stand-in for the Contents API: one file, one sha, rejects stale writes.
function fakeRemote(initial) {
  const r = { text: initial ? L.serialize(initial) : null, n: 0, puts: 0, staleGets: 0, prev: null };
  r.sha = r.text ? 'S0' : null;
  r.fetch = async (url, init) => {
    if (!init || !init.method || init.method === 'GET') {
      if (r.staleGets > 0 && r.prev) { r.staleGets--; return mockRes(200, { sha: r.prev.sha, content: toBase64(r.prev.text) }); }
      return r.text === null ? mockRes(404, {}) : mockRes(200, { sha: r.sha, content: toBase64(r.text) });
    }
    r.puts++;
    const body = JSON.parse(init.body);
    if ((body.sha || null) !== r.sha) return mockRes(409, {});
    r.prev = { sha: r.sha, text: r.text };
    r.text = fromBase64(body.content);
    r.sha = `S${++r.n}`;
    return mockRes(200, { content: { sha: r.sha } });
  };
  r.state = () => L.deserialize(r.text);
  // Someone else's write, straight into the remote.
  r.external = (mutation) => {
    r.prev = { sha: r.sha, text: r.text };
    r.text = L.serialize(L.applyMutation(L.deserialize(r.text), mutation));
    r.sha = `S${++r.n}`;
  };
  return r;
}

export async function runStore() {
  results.length = 0;

  check('base64 round-trips Hebrew (btoa would throw here)', () => {
    const s = 'לבדוק את המדידה — 100% ✓';
    eq(fromBase64(toBase64(s)), s);
  });

  check('base64 round-trips a large payload', () => {
    const s = 'x'.repeat(200000) + 'שלום';
    eq(fromBase64(toBase64(s)).length, s.length);
  });

  await checkAsync('load returns an empty state when the file does not exist yet', async () => {
    const store = new GitHubStore({ owner: 'o', repo: 'r', token: 't', fetchImpl: async () => mockRes(404, {}) });
    const s = await store.load();
    eq(s.tasks, []);
    eq(s.people.length, 2);
  });

  await checkAsync('load parses the remote file', async () => {
    const remote = { ...L.emptyState(), tasks: [{ id: 'a', text: 'hi', topic: 'inbox', state: 'flagged', owner: 'gal', note: '', created: T0 }] };
    const store = new GitHubStore({ owner: 'o', repo: 'r', token: 't', fetchImpl: async () => mockRes(200, { sha: 'S1', content: b64json(remote) }) });
    const s = await store.load();
    eq(s.tasks.map((t) => [t.id, t.owner]), [['a', 'gal']]);
    eq(store.sha, 'S1');
  });

  await checkAsync('a 401 raises AuthError, not a generic failure', async () => {
    const store = new GitHubStore({ owner: 'o', repo: 'r', token: 'bad', fetchImpl: async () => mockRes(401, {}) });
    let caught = null;
    try { await store.load(); } catch (e) { caught = e; }
    ok(caught instanceof AuthError, `expected AuthError, got ${caught}`);
  });

  await checkAsync('a network failure raises OfflineError', async () => {
    const store = new GitHubStore({ owner: 'o', repo: 'r', token: 't', fetchImpl: async () => { throw new TypeError('Failed to fetch'); } });
    let caught = null;
    try { await store.load(); } catch (e) { caught = e; }
    ok(caught instanceof OfflineError, `expected OfflineError, got ${caught}`);
  });

  await checkAsync('save sends the known sha so GitHub can reject a stale write', async () => {
    const remote = fakeRemote(L.emptyState());
    const store = new GitHubStore({ owner: 'o', repo: 'r', token: 't', fetchImpl: remote.fetch, retryDelays: NO_WAIT });
    await store.load();
    await store.save(L.emptyState(), []);
    eq(remote.puts, 1);
    eq(store.sha, 'S1');
  });

  await checkAsync('a conflicting save reloads, replays the mutations, and keeps both sides', async () => {
    const remote = fakeRemote(L.emptyState());
    const store = new GitHubStore({ owner: 'o', repo: 'r', token: 't', fetchImpl: remote.fetch, retryDelays: NO_WAIT });
    await store.load();
    // Gal adds a task while Gur's change is still on his phone.
    remote.external(L.addMutation({ text: 'from Gal', owner: 'gal', now: T0, id: 'theirs' }));
    const mine = L.addMutation({ text: 'from Gur', owner: 'gur', now: T0, id: 'mine' });
    await store.save(L.applyMutation(L.emptyState(), mine), [mine]);
    eq(remote.puts, 2, 'should have retried exactly once');
    eq(remote.state().tasks.map((t) => t.id).sort(), ['mine', 'theirs'], 'neither side should be lost');
  });

  await checkAsync('a save that keeps conflicting gives up rather than looping', async () => {
    let puts = 0;
    const store = new GitHubStore({
      owner: 'o', repo: 'r', token: 't', retryDelays: NO_WAIT,
      fetchImpl: async (url, init) => {
        if (!init || init.method !== 'PUT') return mockRes(200, { sha: 'S', content: b64json(L.emptyState()) });
        puts++;
        return mockRes(409, {});
      },
    });
    let threw = false;
    try { await store.save(L.emptyState(), []); } catch { threw = true; }
    ok(threw, 'expected the save to fail');
    eq(puts, 4, 'one try plus three retries');
  });

  await checkAsync('a 403 on save raises AuthError so the UI can show the token banner', async () => {
    const store = new GitHubStore({
      owner: 'o', repo: 'r', token: 't',
      fetchImpl: async (url, init) => (init && init.method === 'PUT' ? mockRes(403, {}) : mockRes(200, { sha: 'S', content: b64json(L.emptyState()) })),
    });
    let caught = null;
    try { await store.save(L.emptyState(), []); } catch (e) { caught = e; }
    ok(caught instanceof AuthError, `expected AuthError, got ${caught}`);
  });

  check('commit messages describe a single change, including handovers', () => {
    ok(/call the bank/.test(commitMessage([L.addMutation({ text: 'call the bank', now: T0, id: 'q' })])));
    eq(commitMessage([{ op: 'setOwner', id: 't_1', owner: 'gal' }]), 'tasks: own t_1 → gal');
    eq(commitMessage([{ op: 'delete', id: 'a' }, { op: 'delete', id: 'b' }]), 'tasks: 2 changes');
  });

  await checkAsync('refresh returns nothing when the file has not changed', async () => {
    const remote = fakeRemote(seed());
    const store = new GitHubStore({ owner: 'o', repo: 'r', token: 't', fetchImpl: remote.fetch });
    await store.load();
    eq(await store.refresh(), null);
  });

  await checkAsync('refresh returns the other person\'s change', async () => {
    const remote = fakeRemote(seed());
    const store = new GitHubStore({ owner: 'o', repo: 'r', token: 't', fetchImpl: remote.fetch });
    await store.load();
    remote.external({ op: 'setOwner', id: 'c', owner: 'gal' });
    const s = await store.refresh();
    ok(s, 'expected a new state');
    eq(find(s, 'c').owner, 'gal');
  });

  await checkAsync('right after our own write, a stale copy is ignored, not shown', async () => {
    let clock = 1000;
    const remote = fakeRemote(seed());
    const store = new GitHubStore({ owner: 'o', repo: 'r', token: 't', fetchImpl: remote.fetch, now: () => clock });
    await store.load();
    await store.save(L.applyMutation(remote.state(), { op: 'delete', id: 'a' }), [{ op: 'delete', id: 'a' }]);
    remote.staleGets = 1; // GitHub still serving the pre-write copy
    eq(await store.refresh(), null, 'stale copy must not come back');
    clock += 20000;
    remote.external({ op: 'setOwner', id: 'c', owner: 'gal' });
    const s = await store.refresh();
    ok(s && find(s, 'c').owner === 'gal', 'a real change after the window must arrive');
    ok(!find(s, 'a'), 'our own delete must still hold');
  });

  await checkAsync('the other person\'s write right after ours shows at once', async () => {
    let clock = 1000;
    const remote = fakeRemote(seed());
    const store = new GitHubStore({ owner: 'o', repo: 'r', token: 't', fetchImpl: remote.fetch, now: () => clock });
    await store.load();
    await store.save(L.applyMutation(remote.state(), { op: 'delete', id: 'a' }), [{ op: 'delete', id: 'a' }]);
    clock += 2000;
    remote.external({ op: 'setOwner', id: 'c', owner: 'gal' });
    const s = await store.refresh();
    ok(s && find(s, 'c').owner === 'gal', 'Gal\'s newer write was held back');
  });

  return results;
}

// --- sync ------------------------------------------------------------------

export async function runSync() {
  results.length = 0;

  await checkAsync('refresh lays unsent local changes on top of the other person\'s', async () => {
    const remote = fakeRemote(seed());
    const store = new GitHubStore({ owner: 'o', repo: 'r', token: 't', fetchImpl: remote.fetch, retryDelays: NO_WAIT });
    const sync = new Sync({ store, storage: new MemoryStorage(), quietMs: 60000 });
    await sync.start();
    sync.apply(L.addMutation({ text: 'not sent yet', owner: 'gur', now: T0, id: 'local' }));
    remote.external(L.addMutation({ text: 'from Gal', owner: 'gal', now: T0, id: 'remote' }));
    ok(await sync.refresh(), 'refresh should report a change');
    ok(find(sync.state, 'local') && find(sync.state, 'remote'), 'both tasks should be on screen');
    eq(sync.pending.length, 1, 'the local change is still queued');
    await sync.flush();
    eq(remote.state().tasks.map((t) => t.id).sort(), ['a', 'b', 'c', 'd', 'local', 'remote']);
  });

  await checkAsync('a refresh never runs on top of a write in progress', async () => {
    const remote = fakeRemote(seed());
    const store = new GitHubStore({ owner: 'o', repo: 'r', token: 't', fetchImpl: remote.fetch, retryDelays: NO_WAIT });
    const sync = new Sync({ store, storage: new MemoryStorage(), quietMs: 60000 });
    await sync.start();
    sync.apply({ op: 'delete', id: 'a' });
    const flushing = sync.flush();
    eq(await sync.refresh(), false);
    await flushing;
    ok(!find(sync.state, 'a'));
  });

  await checkAsync('the local-only store has nothing to refresh', async () => {
    const sync = new Sync({ store: new LocalStore({ storage: new MemoryStorage() }), storage: new MemoryStorage() });
    await sync.start();
    eq(await sync.refresh(), false);
  });

  return results;
}
