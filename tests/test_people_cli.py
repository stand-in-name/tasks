"""The People command-line tool: the same rules as the app, byte for byte, and
safe when two writers change the list at once. The list sits next to the
tasks in the same data repo; neither may touch the other's file."""

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
CLI = ROOT / "tools" / "people.py"
sys.path.insert(0, str(ROOT / "tools"))
import people as P  # noqa: E402
import tasks as T  # noqa: E402

T0 = "2026-01-01T00:00:00.000Z"


def roster(**extra):
    s = P.empty_state()
    s["contacts"] = [
        P.normalize_contact({"id": "p_dana111", "name": "Dana Levi", "line": "head baker at a bakery chain",
                             "tags": ["baking"], "roles": ["expert"], "knownBy": ["gal"], "created": T0}),
        P.normalize_contact({"id": "p_dan222", "name": "Daniel Katz", "line": "tech recruiter",
                             "tags": ["hiring"], "roles": ["talent"], "knownBy": ["gur"], "every": 3, "created": T0,
                             "log": [{"id": "l_1", "date": "2026-07-01", "by": "gur", "text": "lunch"}]}),
        P.normalize_contact({"id": "p_shi333", "name": "Shira Tal", "line": "investor at an early-stage fund",
                             "roles": ["investor"], "introVia": "Yoni", "tie": "unmet", "created": T0}),
    ]
    s.update(extra)
    return s


def run(*args, env=None, stdin=None, check=True):
    e = dict(os.environ)
    for k in list(e):
        if k.startswith(("TASKS_", "PEOPLE_")):
            del e[k]
    e.update(env or {})
    res = subprocess.run([sys.executable, str(CLI), *args], capture_output=True, text=True,
                         encoding="utf-8", env=e, input=stdin)
    if check and res.returncode != 0:
        raise AssertionError(f"people.py {' '.join(args)} failed:\n{res.stderr}")
    return res


@pytest.fixture
def listfile(tmp_path):
    f = tmp_path / "people.json"
    f.write_text(P.serialize(roster()), encoding="utf-8")
    return f


def load(f):
    return json.loads(Path(f).read_text(encoding="utf-8"))


def contact(f, cid):
    return next(c for c in load(f)["contacts"] if c["id"] == cid)


# --- same rules as the app -----------------------------------------------------------

TRICKY = [
    P.serialize(roster()),
    json.dumps({"contacts": [{"id": "x", "name": "only a name"}]}),
    json.dumps({"version": 2.0, "stage": "", "team": [], "roles": [{"id": "x"}, {"title": "no id"}],
                "contacts": [{"id": 7, "name": "  numeric id  ", "every": 3.0, "update": "yes", "tie": "Close",
                              "tags": ["Machine Learning", "machine_learning", 5, ""], "knownBy": "gur"}]}),
    json.dumps({
        "stages": [{"id": "s", "title": "S", "roles": ["Expert", 3]}, {"title": "no id"}],
        "contacts": [
            {"id": "h", "name": "נועה \"כהן\"", "line": " אדריכלית\tנוף ", "notes": "line1\nline2\u0001",
             "every": -1, "log": [{"id": 5, "date": "2026-01-01", "by": "", "text": 3, "mood": "good"}, None, [1]],
             "phone": "050", "created": ""},
            {"id": "e", "name": "   "}, None, {"name": "no id"}, {"id": "n"},
        ],
        "groups": [{"id": "g1", "title": "ag people"}],
    }, ensure_ascii=False),
]


@pytest.mark.parametrize("i", range(len(TRICKY)))
def test_python_and_js_normalize_files_identically(page, i):
    js = page.evaluate("async (t) => { const P = await import('/people.js'); return P.serialize(P.deserialize(t)); }", TRICKY[i])
    assert P.serialize(P.deserialize(TRICKY[i])) == js


def test_a_file_written_by_the_tool_round_trips_through_the_app(page, listfile):
    run("--file", str(listfile), "add", "נועה כהן, אדריכלית נוף #baking +expert @gal", env={"TASKS_ME": "gur"})
    run("--file", str(listfile), "log", "Shira", "first call", "--date", "2026-10-01", env={"TASKS_ME": "gur"})
    text = listfile.read_text(encoding="utf-8")
    js = page.evaluate("async (t) => { const P = await import('/people.js'); return P.serialize(P.deserialize(t)); }", text)
    assert js == text, "the app would rewrite a file the tool wrote"


QUICK = [
    "Dana Levi, head baker #baking +expert @gal", "@gur Shira Tal - investor +investor",
    "Avi #retail, shop manager", "Noa, runs a C++ shop +wizard @yossi", "  just a name  ",
    "Name — em dash line", "#only-a-tag", "x,y,z", "Dana, #Bake Off, more", "Yoni +design-partner +Champion, prof",
    "נועה כהן, אדריכלות #אדריכלות", "",
]


def test_quick_add_parses_the_same_in_python_and_js(page):
    js = page.evaluate(
        "async (inputs) => { const P = await import('/people.js'); const s = P.emptyState();"
        " return inputs.map((i) => P.parseQuickAdd(i, s)); }", QUICK)
    assert [P.parse_quick_add(i, P.empty_state()) for i in QUICK] == js


def test_mutations_give_the_same_file_in_python_and_js(page):
    muts = [
        {"op": "set", "id": "p_dana111", "fields": {"every": 3, "update": True, "tie": "nope", "name": " ", "org": " Co-op ",
                                                    "tags": ["A B", "a_b"], "bogus": 1}},
        {"op": "set", "id": "p_shi333", "fields": {"knownBy": ["Gur"], "tie": "", "every": 1.0}},
        {"op": "log", "id": "p_dan222", "name": "Daniel Katz", "entry": {"id": "l_2", "date": "2026-09-01", "by": "gal", "text": "call"}},
        {"op": "log", "id": "p_dan222", "entry": {"id": "l_3", "date": "2026-07-01", "by": None, "text": "same day"}},
        {"op": "log", "id": "p_dan222", "entry": {"id": "l_2", "date": "2026-09-01", "text": "dup"}},
        {"op": "log", "id": "p_dan222", "entry": {"id": "l_4", "date": "soon"}},
        {"op": "unlog", "id": "p_dan222", "entryId": "l_1"},
        {"op": "add", "contact": {"id": "p_new", "name": "New Person", "tags": ["x"], "created": T0}},
        {"op": "add", "contact": {"id": "p_new", "name": "Duplicate"}},
        {"op": "add", "contact": {"id": "p_blank", "name": "  "}},
        {"op": "delete", "id": "p_shi333"},
        {"op": "restore", "contact": roster()["contacts"][2]},
        {"op": "setStage", "stage": "fill"},
        {"op": "setStage", "stage": "party"},
        {"op": "bogus", "id": "p_new"},
    ]
    start = P.serialize(roster())
    js = page.evaluate(
        "async ([text, muts]) => { const P = await import('/people.js');"
        " return P.serialize(P.applyAll(P.deserialize(text), muts)); }", [start, muts])
    assert P.serialize(P.apply_all(P.deserialize(start), muts)) == js


DATES = [("2026-01-31", 1), ("2028-01-31", 1), ("2026-11-15", 3), ("2026-12-31", 12), ("2026-03-31", 6), ("2026-08-31", 6)]


def test_due_dates_are_the_same_in_python_and_js(page):
    js = page.evaluate("async (pairs) => { const P = await import('/people.js'); return pairs.map(([d, n]) => P.addMonths(d, n)); }",
                       [list(x) for x in DATES])
    assert [P.add_months(d, n) for d, n in DATES] == js


def test_commit_messages_match_the_app(page):
    muts = [[{"op": "add", "contact": {"name": "Avi Mor"}}], [{"op": "log", "id": "p", "name": "Dana Levi"}],
            [{"op": "setStage", "stage": "commit"}], [{"op": "delete", "id": "a"}, {"op": "delete", "id": "b"}], []]
    js = page.evaluate("async (ms) => { const P = await import('/people.js'); return ms.map((m) => P.commitMessage(m)); }", muts)
    assert [P.commit_message(m) for m in muts] == js


# --- the commands ----------------------------------------------------------------------


def test_list_puts_who_is_due_first(listfile):
    out = run("--file", str(listfile), "list").stdout
    assert out.index("DUE TO REACH OUT") < out.index("Daniel Katz") < out.index("EVERYONE ELSE") < out.index("Dana Levi")
    assert "(due since 2026-10-01)" in out


def test_filters(listfile):
    f = ["--file", str(listfile)]
    assert "Dana Levi" in run(*f, "list", "--tag", "baking").stdout
    assert "Shira" not in run(*f, "list", "--tag", "baking").stdout
    assert "Shira Tal" in run(*f, "list", "--known-by", "nobody").stdout
    assert "Dana" not in run(*f, "list", "--known-by", "nobody").stdout
    assert "Daniel Katz" in run(*f, "list", "--role", "Talent").stdout
    assert "Dana Levi" in run(*f, "find", "BAKER").stdout
    now = run(*f, "list", "--now").stdout
    assert "Dana Levi" in now and "Shira Tal" in now and "Daniel Katz" in now
    assert "Daniel Katz" in run(*f, "due").stdout and "Dana" not in run(*f, "due").stdout


def test_add_is_yours_unless_the_text_says_otherwise(listfile):
    f = ["--file", str(listfile)]
    mine = run(*f, "add", "Avi Mor, shop manager #retail +expert", env={"TASKS_ME": "gur"}).stdout.split()[0]
    gals = run(*f, "add", "Noa, pastry chef @gal", env={"TASKS_ME": "gur"}).stdout.split()[0]
    flagged = run(*f, "add", "Tom", "--known-by", "gal", "--every", "6", "--update", "--tie", "acquaintance",
                  "--org", "Harbor Co", env={"TASKS_ME": "gur"}).stdout.split()[0]
    a, n, t = contact(listfile, mine), contact(listfile, gals), contact(listfile, flagged)
    assert (a["knownBy"], a["tags"], a["roles"], a["line"]) == (["gur"], ["retail"], ["expert"], "shop manager")
    assert n["knownBy"] == ["gal"]
    assert (t["knownBy"], t["every"], t["update"], t["tie"], t["org"]) == (["gal"], 6, True, "acquaintance", "Harbor Co")


def test_bulk_add_reads_one_person_per_line(listfile):
    out = run("--file", str(listfile), "add", "--bulk", env={"TASKS_ME": "gal"},
              stdin="Yoni Ben-David, history professor +champion\n\nנועה כהן, אדריכלית נוף #baking\n").stdout
    assert len(out.strip().splitlines()) == 2
    names = [c["name"] for c in load(listfile)["contacts"]]
    assert "Yoni Ben-David" in names and "נועה כהן" in names


def test_set_log_show_and_remove(listfile):
    f = ["--file", str(listfile)]
    run(*f, "set", "shira", "--every", "1", "--update", "--tags", "fintech, Deep Tech", "--known-by", "gur,gal",
        "--next", "ask for an intro to her partner")
    s = contact(listfile, "p_shi333")
    assert (s["every"], s["update"], s["tags"], s["knownBy"]) == (1, True, ["fintech", "deep-tech"], ["gur", "gal"])
    run(*f, "log", "p_shi", "first coffee", "--date", "2026-10-02", env={"TASKS_ME": "gal"})
    entry = contact(listfile, "p_shi333")["log"][0]
    assert (entry["date"], entry["by"], entry["text"]) == ("2026-10-02", "gal", "first coffee")
    shown = run(*f, "show", "Shira Tal").stdout
    assert "first coffee" in shown and "ask for an intro" in shown and "last contact:" in shown
    run(*f, "unlog", "shira", entry["id"])
    assert contact(listfile, "p_shi333")["log"] == []
    run(*f, "set", "shira", "--no-update")
    assert contact(listfile, "p_shi333")["update"] is False
    run(*f, "remove", "Shira")
    assert "p_shi333" not in [c["id"] for c in load(listfile)["contacts"]]


def test_stage(listfile):
    f = ["--file", str(listfile)]
    assert run(*f, "stage").stdout.strip() == "search"
    run(*f, "stage", "fill")
    assert load(listfile)["stage"] == "fill"
    assert "unknown stage" in run(*f, "stage", "party", check=False).stderr


def test_clear_errors(listfile):
    f = ["--file", str(listfile)]
    assert "matches 2 people" in run(*f, "show", "dan", check=False).stderr
    assert "no one matches" in run(*f, "show", "zzz", check=False).stderr
    assert "unknown role" in run(*f, "add", "X", "--role", "wizard", check=False).stderr
    assert "YYYY-MM-DD" in run(*f, "log", "shira", "x", "--date", "tomorrow", check=False).stderr
    assert "nothing to change" in run(*f, "set", "shira", check=False).stderr


# --- next to the tasks, through git (the agent path) -------------------------------------


def git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True).stdout


@pytest.fixture
def remote(tmp_path):
    bare = tmp_path / "tasks-data.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(bare)], check=True)
    seed = tmp_path / "seed"
    subprocess.run(["git", "clone", "-q", str(bare), str(seed)], check=True, capture_output=True)
    (seed / "tasks.json").write_text(T.serialize(T.empty_state()), encoding="utf-8")
    (seed / "people.json").write_text(P.serialize(roster()), encoding="utf-8")
    git(seed, "add", ".")
    git(seed, "-c", "user.name=seed", "-c", "user.email=seed@example.com", "commit", "-q", "-m", "seed")
    git(seed, "push", "-q", "origin", "HEAD:main")
    return bare


def clone(remote, where):
    subprocess.run(["git", "clone", "-q", str(remote), str(where)], check=True, capture_output=True)
    return where


def test_git_add_touches_only_people_json(remote, tmp_path):
    a = clone(remote, tmp_path / "a")
    tasks_before = (a / "tasks.json").read_text(encoding="utf-8")
    run("--clone", str(a), "add", "Avi Mor, shop manager", env={"TASKS_ME": "gur"})
    check = clone(remote, tmp_path / "check")
    assert "Avi Mor" in [c["name"] for c in load(check / "people.json")["contacts"]]
    assert (check / "tasks.json").read_text(encoding="utf-8") == tasks_before
    assert git(check, "log", "-1", "--format=%s").strip() == "people: add Avi Mor"
    assert git(check, "show", "--stat", "--format=", "HEAD").split()[0] == "people.json"


def test_git_writers_who_collide_both_land(remote, tmp_path):
    a = clone(remote, tmp_path / "a")
    b = clone(remote, tmp_path / "b")
    run("--clone", str(a), "set", "dana", "--every", "3")
    run("--clone", str(b), "set", "dana", "--next", "send the one-pager")
    run("--clone", str(b), "add", "From B, a second person")
    check = clone(remote, tmp_path / "check")
    d = next(c for c in load(check / "people.json")["contacts"] if c["id"] == "p_dana111")
    assert (d["every"], d["next"]) == (3, "send the one-pager")
    assert "From B" in [c["name"] for c in load(check / "people.json")["contacts"]]


def test_a_task_change_and_a_people_change_at_once_both_land(remote, tmp_path):
    a = clone(remote, tmp_path / "a")
    b = clone(remote, tmp_path / "b")
    subprocess.run([sys.executable, str(ROOT / "tools" / "tasks.py"), "--clone", str(a), "add", "talk to Dana"],
                   check=True, capture_output=True)
    run("--clone", str(b), "log", "dana", "call", "--date", "2026-10-03")
    check = clone(remote, tmp_path / "check")
    assert "talk to Dana" in [t["text"] for t in load(check / "tasks.json")["tasks"]]
    assert contact(check / "people.json", "p_dana111")["log"][0]["text"] == "call"


# --- through the GitHub API ---------------------------------------------------------------


class FakeApi(BaseHTTPRequestHandler):
    files = None  # path -> [text, sha], set per test

    def log_message(self, *a):
        pass

    def _send(self, code, body):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _path(self):
        return self.path.split("?", 1)[0].split("/contents/", 1)[1]

    def do_GET(self):
        if self.headers.get("Authorization") != "Bearer good":
            return self._send(401, {})
        f = self.files.get(self._path())
        if not f:
            return self._send(404, {})
        self._send(200, {"sha": f[1], "content": base64.b64encode(f[0].encode()).decode()})

    def do_PUT(self):
        path = self._path()
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        f = self.files.get(path, [None, None])
        if self.files.get("race_once") and path == "people.json":
            # The other phone saves a change between our read and our write.
            del self.files["race_once"]
            other = P.apply_mutation(P.deserialize(f[0]), {"op": "set", "id": "p_dana111", "fields": {"next": "from the phone"}})
            f = [P.serialize(other), "phone"]
            self.files[path] = f
        if body.get("sha") != f[1]:
            return self._send(409, {"message": "conflict"})
        text = base64.b64decode(body["content"]).decode()
        self.files[path] = [text, hashlib.sha1(text.encode()).hexdigest()]
        self.files.setdefault("messages", []).append(body["message"])
        self._send(200, {"content": {"sha": self.files[path][1]}})


@pytest.fixture
def api(tmp_path):
    text = P.serialize(roster())
    FakeApi.files = {"people.json": [text, "s0"]}
    srv = ThreadingHTTPServer(("127.0.0.1", 0), FakeApi)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    env = {"TASKS_API": f"http://127.0.0.1:{srv.server_address[1]}", "TASKS_REPO": "stand-in-name/tasks-data",
           "TASKS_TOKEN": "good", "XDG_CONFIG_HOME": str(tmp_path / "cfg"), "APPDATA": str(tmp_path / "cfg")}
    yield FakeApi.files, env
    srv.shutdown()


def test_api_write_that_collides_keeps_both_changes(api):
    files, env = api
    files["race_once"] = True
    run("set", "dana", "--every", "3", env=env)
    d = next(c for c in P.deserialize(files["people.json"][0])["contacts"] if c["id"] == "p_dana111")
    assert (d["every"], d["next"]) == (3, "from the phone")
    assert files["messages"] == ["people: edit Dana Levi"]


def test_api_reads_people_json_not_tasks_json(api):
    files, env = api
    assert "Dana Levi" in run("list", env=env).stdout
    del files["people.json"]
    assert "no one on the list yet" in run("list", env=env).stdout
