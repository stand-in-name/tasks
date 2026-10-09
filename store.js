// Storage. One implementation of the read/write/conflict protocol, used by the
// app; tools/tasks.py speaks the same GitHub Contents API against the same
// file, so there is exactly one storage contract to reason about.

import { deserialize, serialize, emptyState, applyAll } from './logic.js';

export const API = 'https://api.github.com';

// After our own write, GitHub can serve the copy we replaced for a second or
// two. Within this window, a read of a copy we replaced is treated as stale.
// Any other copy is the other person's newer write and shows at once.
export const STALE_WINDOW_MS = 15000;

// btoa() throws on anything outside Latin-1, which would break the moment a
// task is written in Hebrew. Encode UTF-8 bytes first.
export function toBase64(text) {
  const bytes = new TextEncoder().encode(text);
  let bin = '';
  for (let i = 0; i < bytes.length; i += 0x8000) {
    bin += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
  }
  return btoa(bin);
}

export function fromBase64(b64) {
  const bin = atob(String(b64).replace(/\s/g, ''));
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return new TextDecoder().decode(bytes);
}

export class AuthError extends Error {}
export class OfflineError extends Error {}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

export class GitHubStore {
  constructor({
    owner, repo, path = 'tasks.json', branch = 'main', token, fetchImpl,
    retryDelays = [400, 1000, 2000], now = () => Date.now(), api = API,
  }) {
    Object.assign(this, { owner, repo, path, branch, token, retryDelays, now, api });
    this.fetch = fetchImpl || ((...a) => fetch(...a));
    this.sha = null;
    this.replaced = new Map(); // sha of a copy we wrote over -> when
  }

  get url() {
    return `${this.api}/repos/${this.owner}/${this.repo}/contents/${this.path}`;
  }

  headers() {
    return {
      Authorization: `Bearer ${this.token}`,
      Accept: 'application/vnd.github+json',
      'X-GitHub-Api-Version': '2022-11-28',
    };
  }

  async _get() {
    let res;
    try {
      res = await this.fetch(`${this.url}?ref=${encodeURIComponent(this.branch)}&t=${this.now()}`, {
        headers: this.headers(),
        cache: 'no-store',
      });
    } catch (e) {
      throw new OfflineError(e.message);
    }
    if (res.status === 401 || res.status === 403) throw new AuthError(`GitHub rejected the token (${res.status})`);
    if (res.status === 404) return { sha: null, state: emptyState() }; // first run: no file yet
    if (!res.ok) throw new Error(`GitHub load failed: ${res.status}`);
    const body = await res.json();
    return { sha: body.sha, state: deserialize(fromBase64(body.content)) || emptyState() };
  }

  async load() {
    const { sha, state } = await this._get();
    this.sha = sha;
    return state;
  }

  // Polls for the other person's changes. Returns the new state, or null when
  // nothing changed or the copy GitHub served predates our own last write.
  async refresh() {
    const { sha, state } = await this._get();
    if (sha === this.sha) return null;
    const at = this.replaced.get(sha);
    if (at !== undefined && this.now() - at < STALE_WINDOW_MS) return null;
    this.sha = sha;
    return state;
  }

  // Writes `state`. If the remote moved underneath us (409/422 on a stale sha),
  // reload and replay `mutations` on top of the fresh state instead of
  // clobbering it, then retry. This is why every UI action is a mutation.
  async save(state, mutations = [], attempt = 0) {
    const payload = {
      message: commitMessage(mutations),
      content: toBase64(serialize(state)),
      branch: this.branch,
    };
    if (this.sha) payload.sha = this.sha;

    let res;
    try {
      res = await this.fetch(this.url, {
        method: 'PUT',
        headers: { ...this.headers(), 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
    } catch (e) {
      throw new OfflineError(e.message);
    }

    if (res.status === 401 || res.status === 403) throw new AuthError(`GitHub rejected the token (${res.status})`);

    if ((res.status === 409 || res.status === 422) && attempt < this.retryDelays.length) {
      // The other writer got there first. A read right after their write can
      // still be stale, hence the growing pause before each retry.
      await sleep(this.retryDelays[attempt]);
      const fresh = await this.load();
      const merged = applyAll(fresh, mutations);
      return this.save(merged, mutations, attempt + 1);
    }

    if (!res.ok) throw new Error(`GitHub save failed: ${res.status}`);
    const body = await res.json();
    const t = this.now();
    if (payload.sha) this.replaced.set(payload.sha, t);
    for (const [sha, at] of this.replaced) if (t - at >= STALE_WINDOW_MS) this.replaced.delete(sha);
    this.sha = body.content && body.content.sha;
    return deserialize(serialize(state));
  }
}

const LABELS = {
  add: 'add', delete: 'done', setState: 'mark', setOwner: 'own', move: 'move',
  setText: 'edit', setNote: 'note', restore: 'undo',
};

export function commitMessage(mutations) {
  if (!mutations.length) return 'tasks: sync';
  if (mutations.length === 1) {
    const m = mutations[0];
    const what = m.task ? m.task.text : m.text || m.id;
    const who = m.op === 'setOwner' ? ` → ${m.owner || 'nobody'}` : '';
    return `tasks: ${LABELS[m.op] || m.op} ${String(what).slice(0, 60)}${who}`;
  }
  return `tasks: ${mutations.length} changes`;
}

// A store backed by localStorage. Used for running the app (and its tests)
// with no network at all.
export class LocalStore {
  constructor({ key = 'tasks.state', storage } = {}) {
    this.key = key;
    this.storage = storage || (typeof localStorage !== 'undefined' ? localStorage : new MemoryStorage());
  }
  async load() {
    const raw = this.storage.getItem(this.key);
    return (raw && deserialize(raw)) || emptyState();
  }
  async refresh() {
    return null;
  }
  async save(state) {
    this.storage.setItem(this.key, serialize(state));
    return state;
  }
}

export class MemoryStorage {
  constructor() {
    this.map = new Map();
  }
  getItem(k) {
    return this.map.has(k) ? this.map.get(k) : null;
  }
  setItem(k, v) {
    this.map.set(k, String(v));
  }
  removeItem(k) {
    this.map.delete(k);
  }
}
