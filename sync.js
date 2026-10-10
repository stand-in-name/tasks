// Sync engine: local-first, debounced, offline-durable.
//
// Every UI action applies its mutation to memory immediately (so the phone never
// feels like it is waiting on a network) and appends it to a queue persisted in
// localStorage. The queue is flushed after a quiet period, so a burst of taps
// becomes one commit rather than six. With two people writing, the app also
// polls for the other person's changes (see refresh) and lays any unsent local
// changes on top of what it finds.

import { AuthError, OfflineError, TASKS_CODEC } from './store.js';

export const QUIET_MS = 1500;
export const KEYS = { mirror: 'tasks.mirror', queue: 'tasks.queue' };

export class Sync extends EventTarget {
  // `codec` and `keys` say which list this is; the defaults are the tasks.
  constructor({ store, storage, quietMs = QUIET_MS, codec = TASKS_CODEC, keys = KEYS }) {
    super();
    this.store = store;
    this.storage = storage || localStorage;
    this.quietMs = quietMs;
    this.codec = codec;
    this.keys = keys;
    this.state = codec.emptyState();
    this.pending = this._readQueue();
    // Starts as 'loading', not 'idle': an empty list that has not been fetched
    // yet must be distinguishable from an empty list that has.
    this.status = 'loading';
    this.loaded = false;
    this.lastError = null;
    this._timer = null;
    this._flushing = false;
    this._refreshing = false;
  }

  // --- lifecycle -----------------------------------------------------------

  async start() {
    const mirror = this.storage.getItem(this.keys.mirror);
    if (mirror) {
      const parsed = this.codec.deserialize(mirror);
      if (parsed) this.state = this.codec.applyAll(parsed, this.pending);
      this._emit();
    }
    await this._reload();
    return this.state;
  }

  async _reload() {
    try {
      const remote = await this.store.load();
      // Anything queued while we were away still wins locally until it lands.
      this.state = this.codec.applyAll(remote, this.pending);
      this.loaded = true;
      this._setStatus('idle');
      this._mirror();
      this._emit();
      if (this.pending.length) this.flushSoon(0);
    } catch (e) {
      this._handleError(e);
    }
  }

  // Picks up changes the other person made. Cheap to call often: it is one
  // read, and it never runs on top of a write in progress.
  async refresh() {
    if (this._flushing || this._refreshing) return false;
    this._refreshing = true;
    try {
      if (!this.loaded) {
        await this._reload();
        return this.loaded;
      }
      const remote = await this.store.refresh();
      if (this.status !== 'idle' && this.status !== 'saving') {
        // GitHub answered, so whatever stopped the last save may be over.
        this._setStatus('idle');
        if (this.pending.length) this.flushSoon(0);
      }
      if (!remote || this._flushing) return false;
      this.state = this.codec.applyAll(remote, this.pending);
      this._mirror();
      this._emit();
      return true;
    } catch (e) {
      this._handleError(e);
      return false;
    } finally {
      this._refreshing = false;
    }
  }

  // --- writes --------------------------------------------------------------

  apply(mutation) {
    this.state = this.codec.applyMutation(this.state, mutation);
    this.pending.push(mutation);
    this._writeQueue();
    this._mirror();
    this._emit();
    this.flushSoon();
    return this.state;
  }

  flushSoon(delay = this.quietMs) {
    if (this._timer) clearTimeout(this._timer);
    this._timer = setTimeout(() => {
      this._timer = null;
      this.flush();
    }, delay);
  }

  async flush() {
    if (this._flushing || !this.pending.length) return;
    this._flushing = true;
    const batch = this.pending.slice();
    this._setStatus('saving');
    try {
      const saved = await this.store.save(this.state, batch);
      // Drop exactly the mutations we sent; anything queued mid-flight survives.
      this.pending = this.pending.slice(batch.length);
      this._writeQueue();
      this.state = this.pending.length ? this.codec.applyAll(saved, this.pending) : saved;
      this.loaded = true;
      this._setStatus('idle');
      this._mirror();
      this._emit();
    } catch (e) {
      this._handleError(e);
    } finally {
      this._flushing = false;
    }
    if (this.pending.length && this.status === 'idle') this.flushSoon();
  }

  // --- status --------------------------------------------------------------

  _handleError(e) {
    this.lastError = e;
    if (e instanceof AuthError) {
      // Fine-grained tokens expire, so this is a matter of when, not if. It
      // must never look like a silent failure to save.
      this._setStatus('auth-error');
    } else if (e instanceof OfflineError) {
      this._setStatus('offline');
    } else {
      this._setStatus('error');
    }
    this._emit();
  }

  _setStatus(s) {
    this.status = s;
    this.dispatchEvent(new CustomEvent('status', { detail: { status: s, error: this.lastError, pending: this.pending.length } }));
  }

  _emit() {
    this.dispatchEvent(new CustomEvent('state', { detail: { state: this.state } }));
  }

  // --- durability ----------------------------------------------------------

  _mirror() {
    try {
      this.storage.setItem(this.keys.mirror, this.codec.serialize(this.state));
    } catch {
      /* quota — the remote is still the source of truth */
    }
  }

  _readQueue() {
    try {
      const raw = this.storage.getItem(this.keys.queue);
      const arr = raw ? JSON.parse(raw) : [];
      return Array.isArray(arr) ? arr : [];
    } catch {
      return [];
    }
  }

  _writeQueue() {
    try {
      this.storage.setItem(this.keys.queue, JSON.stringify(this.pending));
    } catch {
      /* ignore */
    }
  }
}
