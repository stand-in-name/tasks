#!/usr/bin/env python3
"""Command-line access to the shared task board, for both of you and for agents.

Standard library only. Runs on Windows, macOS and Linux with Python 3.8+.

Three ways to reach the board; the first one set wins:

  --file PATH    A tasks.json on disk. No syncing. For tests and quick looks.
  --clone DIR    A git clone of the data repo (or TASKS_CLONE). Each change
                 fetches, writes, commits and pushes; if the push is rejected
                 because the other person got there first, it re-applies the
                 change on the fresh copy and retries. This is the agent path:
                 an agent with git access to the data repo needs no token.
  (default)      The GitHub API, like the phone app. Set TASKS_REPO to
                 org/tasks-data and put a token in TASKS_TOKEN or in the token
                 file (see `tasks.py where`).

TASKS_ME names you (gur or gal): it is the default owner for `add` and what
`--mine` means. Flags win over the shortcuts in the text, which win over
TASKS_ME.

Examples:
  tasks.py list
  tasks.py list --mine --state flagged
  tasks.py add "call Dana about the workshop" --topic network --flag
  tasks.py add "@gal !search: read the market report"
  tasks.py own t_ab12cd34ef gal
  tasks.py done t_ab12
"""

import argparse
import base64
import json
import os
import random
import re
import string
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# --- the board: mirrors logic.js -------------------------------------------
# Same shape, same key order, same mutations. tests/test_cli.py checks that a
# file written here round-trips through the app byte for byte, and back.

STATES = ["normal", "flagged", "waiting"]

DEFAULT_TOPICS = [
    {"id": "inbox", "title": "Inbox"},
    {"id": "search", "title": "Search"},
    {"id": "network", "title": "Network"},
    {"id": "foundation", "title": "Foundation"},
    {"id": "tools", "title": "Tools"},
    {"id": "learning", "title": "Learning"},
    {"id": "admin", "title": "Admin"},
]

DEFAULT_PEOPLE = [
    {"id": "gur", "name": "Gur"},
    {"id": "gal", "name": "Gal"},
]

TOP_KEYS = ["version", "topics", "people", "tasks"]
GLYPH = {"flagged": "▲", "waiting": "◷", "normal": " "}


def empty_state():
    return {
        "version": 1,
        "topics": [dict(t) for t in DEFAULT_TOPICS],
        "people": [dict(p) for p in DEFAULT_PEOPLE],
        "tasks": [],
    }


def _with_extras(known, raw):
    for k, v in raw.items():
        if k not in known:
            known[k] = v
    return known


def _js_string(v):
    # String(x) in JS: integral numbers print without ".0".
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


def normalize_task(t):
    owner = t.get("owner")
    return _with_extras(
        {
            "id": _js_string(t["id"]),
            "text": t["text"],
            "topic": _js_string(t.get("topic") or "inbox"),
            "state": t.get("state") if t.get("state") in STATES else "normal",
            "owner": owner if isinstance(owner, str) and owner else None,
            "note": t.get("note") if isinstance(t.get("note"), str) else "",
            "created": t.get("created") or None,
        },
        t,
    )


def deserialize(text):
    try:
        raw = json.loads(text)
    except (ValueError, TypeError):
        return None
    if not isinstance(raw, dict):
        return None
    topics_raw = raw.get("topics")
    if isinstance(topics_raw, list) and topics_raw:
        topics = [
            _with_extras({"id": _js_string(t["id"]), "title": _js_string(t.get("title") or t["id"])}, t)
            for t in topics_raw
            if isinstance(t, dict) and t.get("id")
        ]
    else:
        topics = [dict(t) for t in DEFAULT_TOPICS]
    people_raw = raw.get("people")
    if isinstance(people_raw, list):
        people = [
            _with_extras({"id": _js_string(p["id"]), "name": _js_string(p.get("name") or p["id"])}, p)
            for p in people_raw
            if isinstance(p, dict) and p.get("id")
        ]
    else:
        people = [dict(p) for p in DEFAULT_PEOPLE]
    tasks_raw = raw.get("tasks") if isinstance(raw.get("tasks"), list) else []
    tasks = [
        normalize_task(t)
        for t in tasks_raw
        if isinstance(t, dict) and t.get("id") and isinstance(t.get("text"), str)
    ]
    version = raw.get("version")
    if isinstance(version, float) and version.is_integer():
        version = int(version)
    state = {
        "version": version if isinstance(version, (int, float)) and not isinstance(version, bool) else 1,
        "topics": topics,
        "people": people,
        "tasks": tasks,
    }
    for k, v in raw.items():
        if k not in TOP_KEYS:
            state[k] = v
    return state


def serialize(state):
    out = _with_extras(
        {
            "version": state.get("version") or 1,
            "topics": state["topics"],
            "people": state.get("people") or [],
            "tasks": state["tasks"],
        },
        state,
    )
    # Matches JSON.stringify(out, null, 2): two-space indent, ", " never used,
    # non-ASCII (Hebrew) left as is.
    return json.dumps(out, indent=2, ensure_ascii=False) + "\n"


def _insert_into_topic(tasks, task, index):
    idxs = [i for i, t in enumerate(tasks) if t["topic"] == task["topic"]]
    clamped = max(0, min(index, len(idxs)))
    if not idxs:
        at = len(tasks)
    elif clamped == len(idxs):
        at = idxs[-1] + 1
    else:
        at = idxs[clamped]
    return tasks[:at] + [task] + tasks[at:]


def _map_task(state, task_id, fn):
    hit = False
    tasks = []
    for t in state["tasks"]:
        if t["id"] == task_id:
            hit = True
            tasks.append(fn(dict(t)))
        else:
            tasks.append(t)
    return dict(state, tasks=tasks) if hit else state


def apply_mutation(state, m):
    op = m.get("op")
    if op in ("add", "restore"):
        if any(t["id"] == m["task"]["id"] for t in state["tasks"]):
            return state
        index = m.get("index")
        return dict(state, tasks=_insert_into_topic(state["tasks"], normalize_task(m["task"]), 0 if index is None else index))
    if op == "setState":
        if m.get("state") not in STATES:
            return state
        return _map_task(state, m["id"], lambda t: dict(t, state=m["state"]))
    if op == "setOwner":
        owner = m.get("owner")
        if owner is not None and not isinstance(owner, str):
            return state
        return _map_task(state, m["id"], lambda t: dict(t, owner=owner or None))
    if op == "setText":
        return _map_task(state, m["id"], lambda t: dict(t, text=m["text"]))
    if op == "setNote":
        return _map_task(state, m["id"], lambda t: dict(t, note=m["note"]))
    if op == "delete":
        return dict(state, tasks=[t for t in state["tasks"] if t["id"] != m["id"]])
    if op == "move":
        tasks = list(state["tasks"])
        idx = next((i for i, t in enumerate(tasks) if t["id"] == m["id"]), -1)
        if idx < 0:
            return state
        task = dict(tasks.pop(idx), topic=m["topic"])
        return dict(state, tasks=_insert_into_topic(tasks, task, m.get("index") or 0))
    return state


def apply_all(state, mutations):
    for m in mutations:
        state = apply_mutation(state, m)
    return state


def now_iso():
    # Same format as JS new Date().toISOString().
    d = datetime.now(timezone.utc)
    return d.strftime("%Y-%m-%dT%H:%M:%S.") + f"{d.microsecond // 1000:03d}Z"


def make_id():
    alphabet = string.ascii_lowercase + string.digits
    return "t_" + "".join(random.SystemRandom().choice(alphabet) for _ in range(10))


def _norm(s):
    # JS: String(s).trim().toLowerCase().replace(/[\s_]+/g, '-')
    return re.sub(r"[\s_]+", "-", str(s).strip().lower())


def resolve_topic(state, name):
    target = _norm(name)
    for t in state["topics"]:
        if _norm(t["id"]) == target or _norm(t["title"]) == target:
            return t["id"]
    return None


def resolve_person(state, name):
    target = _norm(name)
    for p in state.get("people") or []:
        if _norm(p["id"]) == target or _norm(p["name"]) == target:
            return p["id"]
    return None


def _run_of(text, start, allowed):
    end = start
    while end < len(text) and allowed(text[end]):
        end += 1
    return end


def _skip_space(text, i):
    while i < len(text) and text[i].isspace():
        i += 1
    return i


def _name_char(ch):
    # JS [\p{L}\d._-] with the u flag: any letter, ASCII digits, . _ -
    return ch.isalpha() or ch in "0123456789._-"


def _topic_char(ch):
    # JS [\p{L}\d _-]: any letter, ASCII digits, space, _ -
    return ch.isalpha() or ch in "0123456789 _-"


def parse_quick_add(text, state):
    """`!` flags, `topic:` files, `@name` assigns; in any order at the start.
    Scans by hand so it matches the regexes in logic.js character for
    character; `topic` and `owner` are None when the text doesn't name them."""
    rest = str(text)
    flagged, topic, owner = False, None, None
    while True:
        before = rest
        rest = rest.lstrip()
        if rest.startswith("!"):
            flagged = True
            rest = rest[1:]
        if owner is None and rest.startswith("@"):
            end = _run_of(rest, 1, _name_char)
            if end > 1:
                found = resolve_person(state, rest[1:end])
                if found:
                    owner = found
                    rest = rest[_skip_space(rest, end):]
        if topic is None and rest[:1].isalpha():
            end = _run_of(rest, 1, _topic_char)
            if end < len(rest) and rest[end] == ":":
                found = resolve_topic(state, rest[:end])
                if found:
                    topic = found
                    rest = rest[_skip_space(rest, end + 1):]
        if rest == before:
            break
    return {"text": rest.strip(), "topic": topic, "state": "flagged" if flagged else "normal", "owner": owner}


LABELS = {
    "add": "add", "delete": "done", "setState": "mark", "setOwner": "own", "move": "move",
    "setText": "edit", "setNote": "note", "restore": "undo",
}


def commit_message(mutations):
    if not mutations:
        return "tasks: sync"
    if len(mutations) == 1:
        m = mutations[0]
        what = m["task"]["text"] if "task" in m else m.get("text") or m.get("id")
        who = f" → {m.get('owner') or 'nobody'}" if m.get("op") == "setOwner" else ""
        return f"tasks: {LABELS.get(m['op'], m['op'])} {str(what)[:60]}{who}"
    return f"tasks: {len(mutations)} changes"


# --- where things are kept ---------------------------------------------------


def config_dir():
    if os.name == "nt":
        return Path(os.environ.get("APPDATA", Path.home())) / "tasks"
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "tasks"


class BoardError(Exception):
    pass


# --- backends -----------------------------------------------------------------
# Each has load() -> state and update(mutations) -> state. update() is where
# the two-writer safety lives: the change is always re-applied to the freshest
# copy, never written over it.


class FileBackend:
    def __init__(self, path):
        self.path = Path(path)

    def load(self):
        if not self.path.exists():
            return empty_state()
        state = deserialize(self.path.read_text(encoding="utf-8"))
        if state is None:
            raise BoardError(f"{self.path} is not a valid board file")
        return state

    def update(self, mutations):
        before = self.load()
        after = apply_all(before, mutations)
        if after != before:
            self.path.write_text(serialize(after), encoding="utf-8", newline="\n")
        return after


class GitBackend:
    def __init__(self, clone, path="tasks.json", branch="main", attempts=5):
        self.dir = Path(clone)
        self.path = path
        self.branch = branch
        self.attempts = attempts
        if not (self.dir / ".git").exists():
            raise BoardError(f"{self.dir} is not a git clone of the data repo")

    def git(self, *args, check=True):
        cmd = ["git", "-C", str(self.dir)]
        if not self._has_identity():
            cmd += ["-c", "user.name=tasks agent", "-c", "user.email=tasks-agent@users.noreply.github.com"]
        res = subprocess.run(cmd + list(args), capture_output=True, text=True)
        if check and res.returncode != 0:
            raise BoardError(f"git {' '.join(args)} failed: {res.stderr.strip() or res.stdout.strip()}")
        return res

    def _has_identity(self):
        if not hasattr(self, "_identity"):
            res = subprocess.run(["git", "-C", str(self.dir), "config", "user.email"], capture_output=True, text=True)
            self._identity = res.returncode == 0 and bool(res.stdout.strip())
        return self._identity

    def _fetch(self):
        # An explicit refspec: a clone made while the repo was still empty has
        # no fetch rule, and a plain fetch would leave origin/<branch> missing.
        self.git("fetch", "-q", "origin", f"+refs/heads/{self.branch}:refs/remotes/origin/{self.branch}")

    def _sync_to_remote(self):
        """Bring the clone to the remote's latest commit, refusing to discard
        anything that was never pushed."""
        self._fetch()
        remote = f"origin/{self.branch}"
        if self.git("status", "--porcelain").stdout.strip():
            raise BoardError(f"{self.dir} has uncommitted changes; commit and push them, or discard them, first")
        ahead = self.git("rev-list", "--count", f"{remote}..HEAD", check=False)
        if ahead.returncode == 0 and ahead.stdout.strip() not in ("", "0"):
            raise BoardError(f"{self.dir} has commits that were never pushed; push or drop them first")
        self.git("checkout", "-q", "-B", self.branch, remote)

    def _read(self):
        f = self.dir / self.path
        if not f.exists():
            return empty_state()
        state = deserialize(f.read_text(encoding="utf-8"))
        if state is None:
            raise BoardError(f"{f} is not a valid board file")
        return state

    def load(self):
        self._sync_to_remote()
        return self._read()

    def update(self, mutations):
        self._sync_to_remote()
        for attempt in range(self.attempts):
            before = self._read()
            after = apply_all(before, mutations)
            if after == before:
                return after
            (self.dir / self.path).write_text(serialize(after), encoding="utf-8", newline="\n")
            self.git("add", self.path)
            self.git("commit", "-q", "-m", commit_message(mutations))
            if self.git("push", "-q", "origin", f"HEAD:{self.branch}", check=False).returncode == 0:
                return after
            # The other writer pushed first. Drop our commit, take theirs, and
            # re-apply the same change on top.
            time.sleep(0.5 * (attempt + 1))
            self._fetch()
            self.git("reset", "-q", "--hard", f"origin/{self.branch}")
        raise BoardError("gave up after repeated push conflicts; nothing was saved")


class ApiBackend:
    # TASKS_API exists for the tests, which point it at a local stand-in.
    API = os.environ.get("TASKS_API", "https://api.github.com")
    STALE_WINDOW = 15  # seconds; see store.js

    def __init__(self, repo, token, path="tasks.json", branch="main", attempts=4):
        if not repo or "/" not in repo:
            raise BoardError("set TASKS_REPO to org/repo (for example stand-in-name/tasks-data), or use --clone or --file")
        if not token:
            raise BoardError(f"no token: set TASKS_TOKEN, or put it in {token_file()}")
        self.repo, self.token, self.path, self.branch, self.attempts = repo, token, path, branch, attempts
        self.marker = config_dir() / "lastwrite.json"

    @property
    def url(self):
        return f"{self.API}/repos/{self.repo}/contents/{self.path}"

    def _request(self, method, url, body=None):
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers={
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "Cache-Control": "no-cache",
            "Content-Type": "application/json",
            "User-Agent": "tasks-cli",
        })
        try:
            with urllib.request.urlopen(req, timeout=30) as res:
                return res.status, json.loads(res.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                raise BoardError(f"GitHub rejected the token ({e.code}). Tokens expire; make a new one.") from None
            return e.code, {}
        except urllib.error.URLError as e:
            raise BoardError(f"can't reach GitHub: {e.reason}") from None

    def _last_write(self):
        try:
            return json.loads(self.marker.read_text(encoding="utf-8")).get(f"{self.repo}/{self.path}")
        except (OSError, ValueError):
            return None

    def _remember_write(self, sha, replaced):
        try:
            data = json.loads(self.marker.read_text(encoding="utf-8")) if self.marker.exists() else {}
        except (OSError, ValueError):
            data = {}
        data[f"{self.repo}/{self.path}"] = {"sha": sha, "replaced": replaced, "at": time.time()}
        try:
            self.marker.parent.mkdir(parents=True, exist_ok=True)
            self.marker.write_text(json.dumps(data), encoding="utf-8")
        except OSError:
            pass

    def _get(self):
        # Right after our own write GitHub can still serve the copy we wrote
        # over. Writes stay safe regardless (a stale read carries a stale sha,
        # so the PUT is rejected), but a `list` that shows a task you just
        # finished is not trustworthy, so briefly wait that copy out. Any
        # other copy is newer, possibly the other person's, and is used at once.
        mark = self._last_write()
        stale = mark.get("replaced") if mark and time.time() - mark["at"] < self.STALE_WINDOW else None
        deadline = time.time() + 8
        while True:
            status, body = self._request("GET", f"{self.url}?ref={self.branch}&t={time.time()}")
            if status == 404:
                return empty_state(), None
            if status != 200:
                raise BoardError(f"GitHub read failed ({status})")
            if not stale or body.get("sha") != stale or time.time() > deadline:
                break
            time.sleep(0.4)
        text = base64.b64decode(body["content"]).decode("utf-8")
        return deserialize(text) or empty_state(), body.get("sha")

    def load(self):
        return self._get()[0]

    def update(self, mutations):
        for attempt in range(self.attempts):
            before, sha = self._get()
            after = apply_all(before, mutations)
            if after == before:
                return after
            payload = {
                "message": commit_message(mutations),
                "content": base64.b64encode(serialize(after).encode("utf-8")).decode("ascii"),
                "branch": self.branch,
            }
            if sha:
                payload["sha"] = sha
            status, body = self._request("PUT", self.url, payload)
            if status in (200, 201):
                self._remember_write(body.get("content", {}).get("sha"), sha)
                return after
            if status not in (409, 422):
                raise BoardError(f"GitHub write failed ({status})")
            time.sleep(0.5 * (attempt + 1))
        raise BoardError("gave up after repeated write conflicts; nothing was saved")


def token_file():
    return Path(os.environ.get("TASKS_TOKEN_FILE") or config_dir() / "token")


def read_token():
    if os.environ.get("TASKS_TOKEN"):
        return os.environ["TASKS_TOKEN"].strip()
    try:
        return token_file().read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def make_backend(args):
    path = os.environ.get("TASKS_PATH", "tasks.json")
    branch = os.environ.get("TASKS_BRANCH", "main")
    if args.file:
        return FileBackend(args.file)
    clone = args.clone or os.environ.get("TASKS_CLONE")
    if clone:
        return GitBackend(clone, path=path, branch=branch)
    return ApiBackend(os.environ.get("TASKS_REPO", ""), read_token(), path=path, branch=branch)


# --- commands -------------------------------------------------------------------


def me():
    return os.environ.get("TASKS_ME", "").strip() or None


def resolve_owner(state, name):
    """'me', 'none'/'nobody', or a person's id or name."""
    low = str(name).strip().lower()
    if low in ("none", "nobody", "-"):
        return None
    if low == "me":
        if not me():
            raise BoardError("'me' needs TASKS_ME set to your id (gur or gal)")
        return resolve_person(state, me()) or me()
    found = resolve_person(state, name)
    if not found:
        known = ", ".join(p["id"] for p in state.get("people") or []) or "none listed"
        raise BoardError(f"unknown person '{name}' (people on this board: {known})")
    return found


def find_task(state, ref):
    exact = [t for t in state["tasks"] if t["id"] == ref]
    if exact:
        return exact[0]
    hits = [t for t in state["tasks"] if t["id"].startswith(ref)]
    if len(hits) == 1:
        return hits[0]
    if not hits:
        raise BoardError(f"no task with id {ref}")
    raise BoardError(f"{ref} matches {len(hits)} tasks; use more of the id")


def owner_label(state, owner_id):
    if not owner_id:
        return ""
    for p in state.get("people") or []:
        if p["id"] == owner_id:
            return p["name"]
    return owner_id


def print_tasks(state, tasks):
    by_topic = {}
    known = {t["id"] for t in state["topics"]}
    for t in tasks:
        by_topic.setdefault(t["topic"] if t["topic"] in known else "inbox", []).append(t)
    for topic in state["topics"]:
        rows = by_topic.get(topic["id"])
        if not rows:
            continue
        print(topic["title"].upper())
        for t in rows:
            owner = owner_label(state, t["owner"])
            note = " ¶" if t["note"] else ""
            print(f"  {t['id']}  {GLYPH[t['state']]}  {t['text']}{note}" + (f"  [{owner}]" if owner else ""))


def cmd_list(args, backend):
    state = backend.load()
    tasks = state["tasks"]
    if args.topic:
        topic = resolve_topic(state, args.topic)
        if not topic:
            raise BoardError(f"unknown topic '{args.topic}'")
        tasks = [t for t in tasks if t["topic"] == topic]
    if args.state:
        tasks = [t for t in tasks if t["state"] == args.state]
    owner = "me" if args.mine else args.owner
    if owner:
        wanted = resolve_owner(state, owner)
        tasks = [t for t in tasks if t["owner"] == wanted]
    if args.json:
        print(json.dumps(tasks, indent=2, ensure_ascii=False))
    elif tasks:
        print_tasks(state, tasks)
    else:
        print("no tasks match")


def cmd_add(args, backend):
    state = backend.load()
    parsed = parse_quick_add(args.text, state)
    topic = resolve_topic(state, args.topic) if args.topic else parsed["topic"]
    if args.topic and not topic:
        raise BoardError(f"unknown topic '{args.topic}'")
    if args.owner is not None:
        owner = resolve_owner(state, args.owner)
    elif parsed["owner"]:
        owner = parsed["owner"]
    else:
        owner = resolve_person(state, me()) if me() else None
    task_state = "flagged" if args.flag else "waiting" if args.wait else parsed["state"]
    m = {
        "op": "add",
        "index": 0,
        "task": {
            "id": make_id(), "text": (parsed["text"] or args.text).strip(), "topic": topic or "inbox",
            "state": task_state, "owner": owner, "note": args.note or "", "created": now_iso(),
        },
    }
    backend.update([m])
    print(m["task"]["id"])


def _single(op_builder):
    def run(args, backend):
        state = backend.load()
        task = find_task(state, args.id)
        m = op_builder(args, state, task)
        backend.update([m])
        print(f"{task['id']}  {commit_message([m])[len('tasks: '):]}")
    return run


cmd_flag = _single(lambda a, s, t: {"op": "setState", "id": t["id"], "state": "flagged"})
cmd_wait = _single(lambda a, s, t: {"op": "setState", "id": t["id"], "state": "waiting"})
cmd_normal = _single(lambda a, s, t: {"op": "setState", "id": t["id"], "state": "normal"})
cmd_own = _single(lambda a, s, t: {"op": "setOwner", "id": t["id"], "owner": resolve_owner(s, a.person)})
cmd_note = _single(lambda a, s, t: {"op": "setNote", "id": t["id"], "note": a.note})
cmd_edit = _single(lambda a, s, t: {"op": "setText", "id": t["id"], "text": a.text.strip()})
cmd_done = _single(lambda a, s, t: {"op": "delete", "id": t["id"], "text": t["text"]})


def _move(a, s, t):
    topic = resolve_topic(s, a.topic)
    if not topic:
        raise BoardError(f"unknown topic '{a.topic}'")
    return {"op": "move", "id": t["id"], "topic": topic, "index": a.index}


cmd_move = _single(_move)


def cmd_show(args, backend):
    state = backend.load()
    t = find_task(state, args.id)
    if args.json:
        print(json.dumps(t, indent=2, ensure_ascii=False))
        return
    print(t["text"])
    print(f"  id: {t['id']}   topic: {t['topic']}   state: {t['state']}   owner: {owner_label(state, t['owner']) or 'nobody'}")
    print(f"  added: {t['created'] or 'unknown'}")
    if t["note"]:
        print("\n" + t["note"])


def cmd_topics(args, backend):
    for t in backend.load()["topics"]:
        print(f"{t['id']:<14} {t['title']}")


def cmd_people(args, backend):
    for p in backend.load()["people"]:
        print(f"{p['id']:<14} {p['name']}")


def cmd_where(args, backend=None):
    print(f"token file:   {token_file()}")
    print(f"TASKS_REPO:   {os.environ.get('TASKS_REPO', '(not set)')}")
    print(f"TASKS_CLONE:  {os.environ.get('TASKS_CLONE', '(not set)')}")
    print(f"TASKS_ME:     {me() or '(not set)'}")


def build_parser():
    p = argparse.ArgumentParser(prog="tasks.py", description="The shared task board, from the command line.",
                                formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__.split("\n\n", 1)[1])
    p.add_argument("--file", help="work on this tasks.json directly (no syncing)")
    p.add_argument("--clone", help="work through this git clone of the data repo")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("list", help="show tasks")
    s.add_argument("--topic")
    s.add_argument("--state", choices=STATES)
    s.add_argument("--owner", help="a person, 'me' or 'none'")
    s.add_argument("--mine", action="store_true", help="only TASKS_ME's tasks")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_list)

    s = sub.add_parser("add", help="add a task (shortcuts: !, topic:, @name)")
    s.add_argument("text")
    s.add_argument("--topic")
    s.add_argument("--owner", help="a person, 'me' or 'none' (default: TASKS_ME)")
    g = s.add_mutually_exclusive_group()
    g.add_argument("--flag", action="store_true")
    g.add_argument("--wait", action="store_true")
    s.add_argument("--note")
    s.set_defaults(fn=cmd_add)

    for name, fn, help_ in [("flag", cmd_flag, "mark as flagged"), ("wait", cmd_wait, "mark as waiting"),
                            ("normal", cmd_normal, "clear flagged or waiting"), ("done", cmd_done, "finish (removes it)")]:
        s = sub.add_parser(name, help=help_)
        s.add_argument("id")
        s.set_defaults(fn=fn)

    s = sub.add_parser("own", help="set the owner")
    s.add_argument("id")
    s.add_argument("person", help="a person, 'me' or 'none'")
    s.set_defaults(fn=cmd_own)

    s = sub.add_parser("note", help="replace the note")
    s.add_argument("id")
    s.add_argument("note")
    s.set_defaults(fn=cmd_note)

    s = sub.add_parser("edit", help="replace the text")
    s.add_argument("id")
    s.add_argument("text")
    s.set_defaults(fn=cmd_edit)

    s = sub.add_parser("move", help="move to another topic")
    s.add_argument("id")
    s.add_argument("topic")
    s.add_argument("--index", type=int, default=0, help="position in the topic (0 = top)")
    s.set_defaults(fn=cmd_move)

    s = sub.add_parser("show", help="show one task with its note")
    s.add_argument("id")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_show)

    sub.add_parser("topics", help="list topics").set_defaults(fn=cmd_topics)
    sub.add_parser("people", help="list people").set_defaults(fn=cmd_people)
    sub.add_parser("where", help="show where the token and settings come from").set_defaults(fn=cmd_where, local=True)
    return p


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # Windows consoles
        except (AttributeError, ValueError):
            pass
    args = build_parser().parse_args(argv)
    try:
        if getattr(args, "local", False):
            args.fn(args)
        else:
            args.fn(args, make_backend(args))
    except BoardError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
