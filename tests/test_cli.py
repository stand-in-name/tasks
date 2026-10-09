"""The command-line tool: the same board rules as the app, byte for byte, and
safe when two writers (people or agents) change the board at once."""

import base64
import hashlib
import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
CLI = ROOT / "tools" / "tasks.py"
sys.path.insert(0, str(ROOT / "tools"))
import tasks as T  # noqa: E402

T0 = "2026-01-01T00:00:00.000Z"


def board(**extra):
    s = T.empty_state()
    s["tasks"] = [
        {"id": "t_aaa111", "text": "map the field", "topic": "search", "state": "flagged",
         "owner": "gur", "note": "", "created": T0},
        {"id": "t_aab222", "text": "check the grant rules", "topic": "search", "state": "normal",
         "owner": "gal", "note": "ask about the deadline", "created": T0},
        {"id": "t_bbb333", "text": "call Dana", "topic": "network", "state": "waiting",
         "owner": None, "note": "", "created": T0},
    ]
    s.update(extra)
    return s


def run(*args, env=None, cwd=None, check=True):
    e = dict(os.environ)
    for k in list(e):
        if k.startswith("TASKS_"):
            del e[k]
    e.update(env or {})
    res = subprocess.run([sys.executable, str(CLI), *args], capture_output=True, text=True,
                         encoding="utf-8", env=e, cwd=cwd)
    if check and res.returncode != 0:
        raise AssertionError(f"tasks.py {' '.join(args)} failed:\n{res.stderr}")
    return res


@pytest.fixture
def boardfile(tmp_path):
    f = tmp_path / "tasks.json"
    f.write_text(T.serialize(board()), encoding="utf-8")
    return f


def load(f):
    return json.loads(Path(f).read_text(encoding="utf-8"))


# --- same rules as the app -------------------------------------------------------

TRICKY_FILES = [
    T.serialize(board()),
    json.dumps({"tasks": [{"id": "x", "text": "only text"}]}),
    json.dumps({"version": 1.0, "people": [], "tasks": [{"id": 7, "text": "numeric id", "state": "urgent"}]}),
    json.dumps({
        "topics": [{"id": "inbox", "title": "Inbox", "color": "teal"}, {"id": "q"}],
        "people": [{"id": "gur", "name": "Gur", "github": "gur-git"}, {"name": "no id"}],
        "tasks": [
            {"id": "h", "text": "שלום \"world\" \\ ok\ttab", "topic": "", "owner": "", "note": "line1\nline2\u0001",
             "created": "", "due": "2026-11-30", "labels": ["a", "b"]},
            {"id": "u", "text": "ex-member's task", "owner": "yossi", "note": 5},
            None, {"text": "no id"}, {"id": "y"},
        ],
        "sprints": [{"id": "s1", "goal": "ship the first draft"}],
    }, ensure_ascii=False),
]


@pytest.mark.parametrize("i", range(len(TRICKY_FILES)))
def test_python_and_js_normalize_files_identically(page, i):
    text = TRICKY_FILES[i]
    js = page.evaluate("async (t) => { const L = await import('/logic.js'); return L.serialize(L.deserialize(t)); }", text)
    assert T.serialize(T.deserialize(text)) == js


def test_a_file_written_by_either_writer_round_trips_through_the_other(page, boardfile):
    run("--file", str(boardfile), "add", "@gal !network: שאלה על הסדנה", env={"TASKS_ME": "gur"})
    text = boardfile.read_text(encoding="utf-8")
    js = page.evaluate("async (t) => { const L = await import('/logic.js'); return L.serialize(L.deserialize(t)); }", text)
    assert js == text, "the app would rewrite a file the CLI wrote"


QUICK_ADD = [
    "plain", "!flag it", "search: x", "@gal x", "@Gal x", "@gal !search: x", "search: @gal !x",
    "! search: @gal x", "@yossi hi", "note: keep", "!@gur network: שלום", "  spaced  ", "@gal",
    "search:", "Search: capital",
]


def test_quick_add_parses_the_same_in_python_and_js(page):
    js = page.evaluate(
        "async (inputs) => { const L = await import('/logic.js'); const s = L.emptyState();"
        " return inputs.map((i) => L.parseQuickAdd(i, s)); }", QUICK_ADD)
    py = [T.parse_quick_add(i, T.empty_state()) for i in QUICK_ADD]
    assert py == js


def test_mutations_give_the_same_file_in_python_and_js(page):
    muts = [
        {"op": "setState", "id": "t_aab222", "state": "flagged"},
        {"op": "setOwner", "id": "t_bbb333", "owner": "gal"},
        {"op": "setOwner", "id": "t_aaa111", "owner": None},
        {"op": "setOwner", "id": "t_aaa111", "owner": ["gur", "gal"]},
        {"op": "move", "id": "t_bbb333", "topic": "search", "index": 1},
        {"op": "add", "index": 0, "task": {"id": "t_new", "text": "new", "topic": "admin", "state": "normal",
                                           "owner": "gur", "note": "", "created": T0}},
        {"op": "setText", "id": "t_new", "text": "renamed"},
        {"op": "setNote", "id": "t_new", "note": "why"},
        {"op": "delete", "id": "t_aaa111"},
        {"op": "restore", "index": 0, "task": board()["tasks"][0]},
        {"op": "bogus", "id": "t_new"},
    ]
    start = T.serialize(board())
    js = page.evaluate(
        "async ([text, muts]) => { const L = await import('/logic.js');"
        " return L.serialize(L.applyAll(L.deserialize(text), muts)); }", [start, muts])
    assert T.serialize(T.apply_all(T.deserialize(start), muts)) == js


# --- the commands -----------------------------------------------------------------


def test_list_groups_by_topic_and_shows_owners(boardfile):
    out = run("--file", str(boardfile), "list").stdout
    assert out.index("SEARCH") < out.index("NETWORK")
    assert "map the field  [Gur]" in out
    assert "check the grant rules ¶  [Gal]" in out


def test_list_filters_by_owner_state_and_topic(boardfile):
    f = str(boardfile)
    assert "map the field" in run("--file", f, "list", "--mine", env={"TASKS_ME": "gur"}).stdout
    assert "grant" not in run("--file", f, "list", "--mine", env={"TASKS_ME": "gur"}).stdout
    assert "call Dana" in run("--file", f, "list", "--owner", "none").stdout
    assert "grant" not in run("--file", f, "list", "--state", "flagged").stdout
    assert "Dana" in run("--file", f, "list", "--topic", "Network").stdout


def test_list_json_is_the_raw_tasks(boardfile):
    tasks = json.loads(run("--file", str(boardfile), "list", "--json", "--owner", "gal").stdout)
    assert [t["id"] for t in tasks] == ["t_aab222"]


def test_add_defaults_to_me_and_lands_on_top_of_its_topic(boardfile):
    new_id = run("--file", str(boardfile), "add", "draft the weekly plan", "--topic", "search",
                 env={"TASKS_ME": "gur"}).stdout.strip()
    tasks = load(boardfile)["tasks"]
    assert tasks[0]["id"] == new_id and tasks[0]["owner"] == "gur" and tasks[0]["topic"] == "search"
    assert new_id.startswith("t_") and len(new_id) == 12


def test_add_shortcuts_and_flags(boardfile):
    f = str(boardfile)
    run("--file", f, "add", "@gal !network: ping Dana", env={"TASKS_ME": "gur"})
    t = load(boardfile)["tasks"][[x["text"] for x in load(boardfile)["tasks"]].index("ping Dana")]
    assert (t["owner"], t["state"], t["topic"]) == ("gal", "flagged", "network")
    # Flags beat the shortcuts in the text.
    run("--file", f, "add", "@gal x", "--owner", "none", "--wait")
    t = next(x for x in load(boardfile)["tasks"] if x["text"] == "x")
    assert (t["owner"], t["state"]) == (None, "waiting")


def test_state_owner_text_note_and_move_commands(boardfile):
    f = str(boardfile)
    run("--file", f, "flag", "t_bbb")
    run("--file", f, "own", "t_bbb", "Gal")
    run("--file", f, "edit", "t_bbb", "call Dana back")
    run("--file", f, "note", "t_bbb", "she asked about dates")
    run("--file", f, "move", "t_bbb", "search", "--index", "1")
    s = load(boardfile)
    t = next(x for x in s["tasks"] if x["id"] == "t_bbb333")
    assert (t["state"], t["owner"], t["text"], t["note"], t["topic"]) == (
        "flagged", "gal", "call Dana back", "she asked about dates", "search")
    assert [x["id"] for x in s["tasks"] if x["topic"] == "search"] == ["t_aaa111", "t_bbb333", "t_aab222"]
    run("--file", f, "normal", "t_bbb")
    run("--file", f, "own", "t_bbb", "none")
    t = next(x for x in load(boardfile)["tasks"] if x["id"] == "t_bbb333")
    assert (t["state"], t["owner"]) == ("normal", None)


def test_done_removes_the_task(boardfile):
    run("--file", str(boardfile), "done", "t_aab222")
    assert "t_aab222" not in [t["id"] for t in load(boardfile)["tasks"]]


def test_show_prints_the_note(boardfile):
    out = run("--file", str(boardfile), "show", "t_aab").stdout
    assert "ask about the deadline" in out and "owner: Gal" in out


def test_clear_errors(boardfile):
    f = str(boardfile)
    assert "matches 2 tasks" in run("--file", f, "done", "t_aa", check=False).stderr
    assert "no task with id" in run("--file", f, "done", "t_zzz", check=False).stderr
    assert "unknown person 'yossi'" in run("--file", f, "own", "t_bbb", "yossi", check=False).stderr
    assert "TASKS_ME" in run("--file", f, "own", "t_bbb", "me", check=False).stderr
    assert "unknown topic" in run("--file", f, "move", "t_bbb", "nowhere", check=False).stderr
    res = run("list", check=False)
    assert res.returncode == 1 and "TASKS_REPO" in res.stderr
    assert load(boardfile) == json.loads(T.serialize(board())), "a failed command must not touch the file"


# --- two writers through git (the agent path) ---------------------------------------


def git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True).stdout


@pytest.fixture
def remote(tmp_path):
    bare = tmp_path / "tasks-data.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(bare)], check=True)
    seed = tmp_path / "seed"
    subprocess.run(["git", "clone", "-q", str(bare), str(seed)], check=True, capture_output=True)
    (seed / "tasks.json").write_text(T.serialize(board()), encoding="utf-8")
    git(seed, "add", "tasks.json")
    git(seed, "-c", "user.name=seed", "-c", "user.email=seed@example.com", "commit", "-q", "-m", "seed")
    git(seed, "push", "-q", "origin", "HEAD:main")
    return bare


def clone(remote, where):
    subprocess.run(["git", "clone", "-q", str(remote), str(where)], check=True, capture_output=True)
    return where


def test_git_add_commits_and_pushes(remote, tmp_path):
    a = clone(remote, tmp_path / "a")
    new_id = run("--clone", str(a), "add", "from an agent", "--owner", "gal").stdout.strip()
    check = clone(remote, tmp_path / "check")
    assert new_id in [t["id"] for t in load(check / "tasks.json")["tasks"]]
    assert "tasks: add from an agent" in git(check, "log", "-1", "--format=%s")


def test_git_writers_who_collide_both_land(remote, tmp_path):
    a = clone(remote, tmp_path / "a")
    b = clone(remote, tmp_path / "b")   # b is now behind whatever a pushes
    run("--clone", str(a), "add", "first, from A")
    run("--clone", str(b), "own", "t_bbb333", "gal")
    run("--clone", str(b), "add", "second, from B")
    check = clone(remote, tmp_path / "check")
    s = load(check / "tasks.json")
    texts = [t["text"] for t in s["tasks"]]
    assert "first, from A" in texts and "second, from B" in texts
    assert next(t for t in s["tasks"] if t["id"] == "t_bbb333")["owner"] == "gal"


def test_git_push_rejected_mid_write_is_replayed_on_the_fresh_copy(remote, tmp_path, monkeypatch):
    """B reads, A pushes in between, B's push is rejected: B must re-apply its
    change on A's version rather than give up or overwrite it."""
    a = clone(remote, tmp_path / "a")
    b = clone(remote, tmp_path / "b")
    backend = T.GitBackend(b)
    original_git = backend.git
    raced = {"done": False}

    def git_with_race(*args, check=True):
        if args[:1] == ("push",) and not raced["done"]:
            raced["done"] = True
            run("--clone", str(a), "add", "sneaked in by A")
        return original_git(*args, check=check)

    monkeypatch.setattr(backend, "git", git_with_race)
    backend.update([{"op": "setState", "id": "t_aab222", "state": "flagged"}])
    check = clone(remote, tmp_path / "check")
    s = load(check / "tasks.json")
    assert "sneaked in by A" in [t["text"] for t in s["tasks"]]
    assert next(t for t in s["tasks"] if t["id"] == "t_aab222")["state"] == "flagged"


def test_git_refuses_to_discard_local_work(remote, tmp_path):
    a = clone(remote, tmp_path / "a")
    (a / "tasks.json").write_text("{}", encoding="utf-8")
    assert "uncommitted changes" in run("--clone", str(a), "list", check=False).stderr
    git(a, "-c", "user.name=x", "-c", "user.email=x@example.com", "commit", "-qam", "local only")
    assert "never pushed" in run("--clone", str(a), "list", check=False).stderr


# --- two writers through the GitHub API ---------------------------------------------


class FakeApi(BaseHTTPRequestHandler):
    state = None  # set per test

    def log_message(self, *a):
        pass

    def _send(self, code, body):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        s = self.state
        if self.headers.get("Authorization") != "Bearer good":
            return self._send(401, {})
        s["gets"] += 1
        sha, text = s["prev"] if s["stale_gets"] and s["prev"] else (s["sha"], s["text"])
        if s["stale_gets"] and s["prev"]:
            s["stale_gets"] -= 1
        self._send(200, {"sha": sha, "content": base64.b64encode(text.encode()).decode()})

    def do_PUT(self):
        s = self.state
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if s.get("race_once"):
            # Someone else writes between our read and our write.
            s.pop("race_once")
            other = T.apply_mutation(T.deserialize(s["text"]), {
                "op": "add", "index": 0, "task": {"id": "t_phone", "text": "added on the phone", "topic": "inbox",
                                                  "state": "normal", "owner": "gal", "note": "", "created": T0}})
            s["text"] = T.serialize(other)
            s["sha"] = hashlib.sha1(s["text"].encode()).hexdigest()
        if body.get("sha") != s["sha"]:
            return self._send(409, {"message": "conflict"})
        s["prev"] = (s["sha"], s["text"])
        s["text"] = base64.b64decode(body["content"]).decode()
        s["sha"] = hashlib.sha1((s["text"] + "x").encode()).hexdigest()
        s["stale_gets"] = s.get("stale_after_write", 0)
        s["messages"].append(body["message"])
        self._send(200, {"content": {"sha": s["sha"]}})


@pytest.fixture
def api(tmp_path):
    text = T.serialize(board())
    FakeApi.state = {"text": text, "sha": hashlib.sha1(text.encode()).hexdigest(), "gets": 0,
                     "prev": None, "stale_gets": 0, "messages": []}
    srv = ThreadingHTTPServer(("127.0.0.1", 0), FakeApi)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    env = {"TASKS_API": f"http://127.0.0.1:{srv.server_address[1]}", "TASKS_REPO": "stand-in-name/tasks-data",
           "TASKS_TOKEN": "good", "XDG_CONFIG_HOME": str(tmp_path / "cfg"), "APPDATA": str(tmp_path / "cfg")}
    yield FakeApi.state, env
    srv.shutdown()


def test_api_write_that_collides_keeps_both_changes(api):
    state, env = api
    state["race_once"] = True
    run("own", "t_bbb333", "gur", env=env)
    s = T.deserialize(state["text"])
    assert "t_phone" in [t["id"] for t in s["tasks"]], "the phone's task was overwritten"
    assert next(t for t in s["tasks"] if t["id"] == "t_bbb333")["owner"] == "gur"
    assert state["messages"] == ["tasks: own t_bbb333 → gur"]


def test_api_list_right_after_a_write_waits_for_github_to_catch_up(api):
    state, env = api
    state["stale_after_write"] = 2
    run("done", "t_aaa111", env=env)
    out = run("list", env=env).stdout
    assert "map the field" not in out, "list showed a task that was just finished"


def test_api_rejected_token_says_so(api):
    _, env = api
    res = run("list", env=dict(env, TASKS_TOKEN="expired"), check=False)
    assert res.returncode == 1 and "rejected the token" in res.stderr


def test_api_token_can_come_from_the_token_file(api, tmp_path):
    _, env = api
    env = dict(env)
    del env["TASKS_TOKEN"]
    (tmp_path / "cfg" / "tasks").mkdir(parents=True)
    (tmp_path / "cfg" / "tasks" / "token").write_text("good\n", encoding="utf-8")
    assert "call Dana" in run("list", env=env).stdout
