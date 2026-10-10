#!/usr/bin/env python3
"""Command-line access to the People list (the network map), for both of you
and for agents. It lives in people.json, next to tasks.json in the data repo.

Standard library only. Runs on Windows, macOS and Linux with Python 3.8+.

It reaches the file the same three ways as tasks.py, with the same settings:
--file PATH, --clone DIR (or TASKS_CLONE), or the GitHub API (TASKS_REPO and
a token). TASKS_ME names you: people you add are yours unless the text says
otherwise, and log entries are signed with it.

Quick add, the same as the app:
  "Dana Levi, head baker at a bakery chain #baking +expert @gal"
  #tag adds an expertise tag, +role a role, @name who knows them. The first
  comma (or a spaced dash) splits the name from the one line about them.

Examples:
  people.py list
  people.py list --tag baking --role expert
  people.py find baker
  people.py due
  people.py add "Dana Levi, head baker at a bakery chain #baking +expert @gal"
  people.py add --bulk < names.txt
  people.py log dana "coffee: she'd pilot it on two fields"
  people.py set dana --every 3 --update
  people.py show dana
"""

import argparse
import json
import os
import re
import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))
import tasks as board  # noqa: E402  the shared backends, token and settings

BoardError = board.BoardError
_with_extras = board._with_extras
_js_string = board._js_string

# --- the list: mirrors people.js -------------------------------------------
# Same shape, same key order, same mutations. tests/test_people_cli.py checks
# that a file written here round-trips through the app byte for byte.

TIES = ["close", "acquaintance", "dormant", "unmet"]
TIE_LABELS = {"close": "Close", "acquaintance": "Acquaintance", "dormant": "Dormant", "unmet": "Not met yet"}
INTERVALS = [0, 1, 3, 6, 12]

DEFAULT_TEAM = [
    {"id": "gur", "name": "Gur"},
    {"id": "gal", "name": "Gal"},
]

DEFAULT_ROLES = [
    {"id": "champion", "title": "Champion"},
    {"id": "like-minded", "title": "Like-minded"},
    {"id": "expert", "title": "Expert"},
    {"id": "investor", "title": "Investor"},
    {"id": "customer", "title": "Customer"},
    {"id": "channel", "title": "Channel"},
    {"id": "design-partner", "title": "Design partner"},
    {"id": "supplier", "title": "Supplier"},
    {"id": "talent", "title": "Talent"},
]

DEFAULT_STAGES = [
    {"id": "search", "title": "Search", "roles": ["champion", "like-minded", "expert", "investor", "talent"]},
    {"id": "commit", "title": "Commit", "roles": ["champion", "expert", "talent"]},
    {"id": "fill", "title": "Fill in the square",
     "roles": ["champion", "customer", "channel", "design-partner", "investor", "talent"]},
    {"id": "validate", "title": "Hard-validate",
     "roles": ["champion", "design-partner", "customer", "supplier", "investor", "talent"]},
    {"id": "scale", "title": "Scale", "roles": ["champion", "channel", "customer", "supplier", "investor", "talent"]},
]

TOP_KEYS = ["version", "team", "roles", "stages", "stage", "contacts"]
DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
QUICK = re.compile(r"^([#+@])(.+?)([,.;:]*)$", re.S)
_UNSET = object()


def _copy(items):
    return [dict(x, **({"roles": list(x["roles"])} if x.get("roles") is not None and "roles" in x else {})) for x in items]


def empty_state():
    return {
        "version": 1,
        "team": _copy(DEFAULT_TEAM),
        "roles": _copy(DEFAULT_ROLES),
        "stages": _copy(DEFAULT_STAGES),
        "stage": "search",
        "contacts": [],
    }


def norm(s):
    # JS: String(s).trim().toLowerCase().replace(/[\s_]+/g, '-')
    return re.sub(r"[\s_]+", "-", str(s).strip().lower())


def _line(v):
    return v.strip() if isinstance(v, str) else ""


def _id_list(v):
    out = []
    if not isinstance(v, list):
        return out
    for x in v:
        if not isinstance(x, str):
            continue
        n = norm(x)
        if n and n not in out:
            out.append(n)
    return out


def _months(v):
    # JS: typeof v === 'number' && Number.isInteger(v) && v >= 0
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v if v >= 0 else None
    if isinstance(v, float) and v.is_integer() and v >= 0:
        return int(v)
    return None


def _is_date(v):
    return isinstance(v, str) and DATE.fullmatch(v) is not None


def normalize_entry(e):
    return _with_extras(
        {
            "id": _js_string(e.get("id") or ""),
            "date": e["date"] if isinstance(e.get("date"), str) else "",
            "by": e["by"] if isinstance(e.get("by"), str) and e["by"] else None,
            "text": e["text"] if isinstance(e.get("text"), str) else "",
        },
        e,
    )


def normalize_contact(c):
    every = _months(c.get("every"))
    log = c.get("log") if isinstance(c.get("log"), list) else []
    return _with_extras(
        {
            "id": _js_string(c["id"]),
            "name": _js_string(c["name"]).strip(),
            "line": _line(c.get("line")),
            "org": _line(c.get("org")),
            "email": _line(c.get("email")),
            "tags": _id_list(c.get("tags")),
            "roles": _id_list(c.get("roles")),
            "knownBy": _id_list(c.get("knownBy")),
            "introVia": _line(c.get("introVia")),
            "tie": c.get("tie") if c.get("tie") in TIES else "",
            "every": every if every is not None else 0,
            "update": c.get("update") is True,
            "next": _line(c.get("next")),
            "notes": c["notes"] if isinstance(c.get("notes"), str) else "",
            "file": _line(c.get("file")),
            "log": [normalize_entry(e) for e in log if isinstance(e, dict)],
            "created": c.get("created") or None,
        },
        c,
    )


# --- mutations ----------------------------------------------------------------


def _trimmed(v):
    return v.strip() if isinstance(v, str) else _UNSET


EDITABLE = {
    "name": lambda v: v.strip() if isinstance(v, str) and v.strip() else _UNSET,
    "line": _trimmed,
    "org": _trimmed,
    "email": _trimmed,
    "tags": lambda v: _id_list(v) if isinstance(v, list) else _UNSET,
    "roles": lambda v: _id_list(v) if isinstance(v, list) else _UNSET,
    "knownBy": lambda v: _id_list(v) if isinstance(v, list) else _UNSET,
    "introVia": _trimmed,
    "tie": lambda v: v if isinstance(v, str) and (v == "" or v in TIES) else _UNSET,
    "every": lambda v: _months(v) if _months(v) is not None else _UNSET,
    "update": lambda v: v if isinstance(v, bool) else _UNSET,
    "next": _trimmed,
    "notes": lambda v: v if isinstance(v, str) else _UNSET,
    "file": _trimmed,
}


def _apply_fields(c, fields):
    if not isinstance(fields, dict):
        return c
    out = dict(c)
    for k, check in EDITABLE.items():
        if k not in fields:
            continue
        v = check(fields[k])
        if v is not _UNSET:
            out[k] = v
    return out


def _insert_entry(log, entry):
    at = next((i for i, e in enumerate(log) if not (e["date"] > entry["date"])), len(log))
    return log[:at] + [entry] + log[at:]


def _map_contact(state, cid, fn):
    hit = False
    out = []
    for c in state["contacts"]:
        if c["id"] == cid:
            hit = True
            out.append(fn(c))
        else:
            out.append(c)
    return dict(state, contacts=out) if hit else state


def apply_mutation(state, m):
    op = m.get("op") if isinstance(m, dict) else None
    if op in ("add", "restore"):
        c = m.get("contact")
        if not isinstance(c, dict) or not c.get("id") or not isinstance(c.get("name"), str) or not c["name"].strip():
            return state
        if any(x["id"] == _js_string(c["id"]) for x in state["contacts"]):
            return state
        return dict(state, contacts=state["contacts"] + [normalize_contact(c)])
    if op == "set":
        return _map_contact(state, m.get("id"), lambda c: _apply_fields(c, m.get("fields")))
    if op == "log":
        e = m.get("entry")
        if not isinstance(e, dict) or not e.get("id") or not _is_date(e.get("date")):
            return state
        eid = _js_string(e["id"])
        return _map_contact(state, m.get("id"), lambda c: c if any(x["id"] == eid for x in c["log"])
                            else dict(c, log=_insert_entry(c["log"], normalize_entry(e))))
    if op == "unlog":
        return _map_contact(state, m.get("id"), lambda c: dict(c, log=[e for e in c["log"] if e["id"] != m.get("entryId")]))
    if op == "delete":
        return dict(state, contacts=[c for c in state["contacts"] if c["id"] != m.get("id")])
    if op == "setStage":
        if any(s["id"] == m.get("stage") for s in state["stages"]):
            return dict(state, stage=m["stage"])
        return state
    return state


def apply_all(state, mutations):
    for m in mutations:
        state = apply_mutation(state, m)
    return state


# --- reading -------------------------------------------------------------------


def last_contact(c):
    last = ""
    for e in c["log"]:
        if _is_date(e["date"]) and e["date"] > last:
            last = e["date"]
    return last or None


def _days_in_month(y, m):
    if m == 2:
        return 29 if y % 4 == 0 and (y % 100 != 0 or y % 400 == 0) else 28
    return 30 if m in (4, 6, 9, 11) else 31


def add_months(day, n):
    y, m, d = (int(x) for x in day.split("-"))
    total = (m - 1) + n
    yy = y + total // 12
    mm = total % 12
    return f"{yy}-{mm + 1:02d}-{min(d, _days_in_month(yy, mm + 1)):02d}"


def due_date(c):
    if not c["every"]:
        return None
    last = last_contact(c)
    return add_months(last, c["every"]) if last else ""


def is_due(c, today):
    d = due_date(c)
    return d is not None and d <= today


def stage_roles(state):
    for s in state["stages"]:
        if s["id"] == state["stage"]:
            return s["roles"]
    return []


NO_FILTERS = {"q": "", "role": "", "tag": "", "tie": "", "knownBy": "", "update": False, "now": False}


def matches(state, c, f=None):
    f = dict(NO_FILTERS, **(f or {}))
    if f["q"]:
        hay = "\n".join([c["name"], c["line"], c["org"], c["email"], c["introVia"], c["next"], c["notes"], *c["tags"]]).lower()
        if f["q"].strip().lower() not in hay:
            return False
    if f["role"] and f["role"] not in c["roles"]:
        return False
    if f["tag"] and f["tag"] not in c["tags"]:
        return False
    if f["tie"] and c["tie"] != f["tie"]:
        return False
    if f["knownBy"] == "nobody" and c["knownBy"]:
        return False
    if f["knownBy"] and f["knownBy"] != "nobody" and f["knownBy"] not in c["knownBy"]:
        return False
    if f["update"] and not c["update"]:
        return False
    if f["now"] and not any(r in stage_roles(state) for r in c["roles"]):
        return False
    return True


def _by_name(c):
    return (c["name"].casefold(), c["id"])


def sections(state, f, today):
    hits = [c for c in state["contacts"] if matches(state, c, f)]
    due = sorted((c for c in hits if is_due(c, today)), key=lambda c: (due_date(c), _by_name(c)))
    due_ids = {c["id"] for c in due}
    rest = sorted((c for c in hits if c["id"] not in due_ids), key=_by_name)
    return due, rest


def all_tags(state):
    return sorted({t for c in state["contacts"] for t in c["tags"]})


# --- quick add -------------------------------------------------------------------


def _resolve(items, key, value):
    target = norm(value)
    for x in items:
        if norm(x["id"]) == target or norm(x[key]) == target:
            return x["id"]
    return None


def parse_quick_add(text, state):
    tags, roles, known_by, kept = [], [], [], []
    for word in str(text).strip().split():
        m = QUICK.match(word)
        used = False
        if m:
            sym, body, trail = m.group(1), m.group(2), m.group(3)
            if sym == "#":
                t = norm(body)
                if t:
                    if t not in tags:
                        tags.append(t)
                    used = True
            elif sym == "+":
                r = _resolve(state["roles"], "title", body)
                if r:
                    if r not in roles:
                        roles.append(r)
                    used = True
            else:
                p = _resolve(state["team"], "name", body)
                if p:
                    if p not in known_by:
                        known_by.append(p)
                    used = True
            if used and "," in trail:
                kept.append(",")
        if not used and word:
            kept.append(word)
    joined = " ".join(kept).replace(" ,", ",")
    name, rest = joined, ""
    comma = joined.find(",")
    dash = re.search(" [—–-] ", joined)
    if comma >= 0:
        name, rest = joined[:comma], joined[comma + 1:]
    elif dash:
        name, rest = joined[:dash.start()], joined[dash.start() + 3:]
    return {"name": name.strip(), "line": rest.strip(), "tags": tags, "roles": roles, "knownBy": known_by}


def parse_many(text, state):
    parsed = [parse_quick_add(line, state) for line in re.split(r"\r?\n", str(text))]
    return [p for p in parsed if p["name"]]


# --- serialization ---------------------------------------------------------------


def _named(items, key, fallback):
    if isinstance(items, list) and items:
        return [
            _with_extras({"id": _js_string(x["id"]), key: _js_string(x.get(key) or x["id"])}, x)
            for x in items
            if isinstance(x, dict) and x.get("id")
        ]
    return _copy(fallback)


def serialize(state):
    out = _with_extras(
        {
            "version": state.get("version") or 1,
            "team": state["team"],
            "roles": state["roles"],
            "stages": state["stages"],
            "stage": state["stage"],
            "contacts": state["contacts"],
        },
        state,
    )
    return json.dumps(out, indent=2, ensure_ascii=False) + "\n"


def deserialize(text):
    try:
        raw = json.loads(text)
    except (ValueError, TypeError):
        return None
    if not isinstance(raw, dict):
        return None
    stages_raw = raw.get("stages")
    if isinstance(stages_raw, list) and stages_raw:
        stages = [
            _with_extras({"id": _js_string(s["id"]), "title": _js_string(s.get("title") or s["id"]),
                          "roles": _id_list(s.get("roles"))}, s)
            for s in stages_raw
            if isinstance(s, dict) and s.get("id")
        ]
    else:
        stages = _copy(DEFAULT_STAGES)
    version = raw.get("version")
    if isinstance(version, float) and version.is_integer():
        version = int(version)
    contacts_raw = raw.get("contacts") if isinstance(raw.get("contacts"), list) else []
    state = {
        "version": version if isinstance(version, (int, float)) and not isinstance(version, bool) else 1,
        "team": _named(raw.get("team"), "name", DEFAULT_TEAM),
        "roles": _named(raw.get("roles"), "title", DEFAULT_ROLES),
        "stages": stages,
        "stage": raw["stage"] if isinstance(raw.get("stage"), str) and raw.get("stage") else "search",
        "contacts": [
            normalize_contact(c)
            for c in contacts_raw
            if isinstance(c, dict) and c.get("id") and isinstance(c.get("name"), str) and c["name"].strip()
        ],
    }
    for k, v in raw.items():
        if k not in TOP_KEYS:
            state[k] = v
    return state


LABELS = {"add": "add", "restore": "undo", "set": "edit", "log": "log", "unlog": "unlog",
          "delete": "remove", "setStage": "stage"}


def commit_message(mutations):
    if not mutations:
        return "people: sync"
    if len(mutations) == 1:
        m = mutations[0]
        if "contact" in m:
            what = m["contact"]["name"]
        elif m.get("op") == "setStage":
            what = m.get("stage")
        else:
            what = m.get("name") or m.get("id")
        return f"people: {LABELS.get(m['op'], m['op'])} {str(what)[:60]}"
    return f"people: {len(mutations)} changes"


PEOPLE = SimpleNamespace(
    label="people", empty_state=empty_state, deserialize=deserialize, serialize=serialize,
    apply_all=apply_all, commit_message=commit_message,
)


# --- commands ---------------------------------------------------------------------


def make_backend(args):
    path = os.environ.get("PEOPLE_PATH", "people.json")
    branch = os.environ.get("TASKS_BRANCH", "main")
    if args.file:
        return board.FileBackend(args.file, codec=PEOPLE)
    clone = args.clone or os.environ.get("TASKS_CLONE")
    if clone:
        return board.GitBackend(clone, path=path, branch=branch, codec=PEOPLE)
    return board.ApiBackend(os.environ.get("TASKS_REPO", ""), board.read_token(), path=path, branch=branch, codec=PEOPLE)


def today():
    return date.today().isoformat()


def make_id(prefix="p_"):
    return prefix + board.make_id()[2:]


def team_name(state, pid):
    for p in state["team"]:
        if p["id"] == pid:
            return p["name"]
    return pid


def resolve_member(state, name):
    """'me', 'nobody', or a team member's id or name."""
    low = str(name).strip().lower()
    if low in ("none", "nobody", "-"):
        return None
    if low == "me":
        me = board.me()
        if not me:
            raise BoardError("'me' needs TASKS_ME set to your id (gur or gal)")
        name = me
    found = _resolve(state["team"], "name", name)
    if not found:
        raise BoardError(f"unknown team member '{name}' (team: {', '.join(p['id'] for p in state['team'])})")
    return found


def resolve_role(state, name):
    found = _resolve(state["roles"], "title", name)
    if not found:
        raise BoardError(f"unknown role '{name}' (roles: {', '.join(r['id'] for r in state['roles'])})")
    return found


def find_contact(state, ref):
    contacts = state["contacts"]
    exact = [c for c in contacts if c["id"] == ref]
    if exact:
        return exact[0]
    by_id = [c for c in contacts if c["id"].startswith(ref)]
    if len(by_id) == 1:
        return by_id[0]
    low = ref.strip().casefold()
    named = [c for c in contacts if c["name"].casefold() == low]
    if len(named) == 1:
        return named[0]
    part = [c for c in contacts if low in c["name"].casefold()]
    if len(part) == 1:
        return part[0]
    hits = named or part or by_id
    if not hits:
        raise BoardError(f"no one matches '{ref}'")
    raise BoardError(f"'{ref}' matches {len(hits)} people: " + ", ".join(f"{c['name']} ({c['id']})" for c in hits[:8]))


def describe(state, c, due_day=None):
    bits = [f"{c['id']}  {c['name']}" + (f" — {c['line']}" if c["line"] else "")]
    extra = []
    if c["org"] and c["org"] not in c["line"]:
        extra.append(c["org"])
    extra += [f"#{t}" for t in c["tags"]] + [f"+{r}" for r in c["roles"]]
    if c["knownBy"]:
        extra.append("[" + ", ".join(team_name(state, p) for p in c["knownBy"]) + "]")
    elif c["introVia"]:
        extra.append(f"[via {c['introVia']}]")
    if c["tie"] == "unmet":
        extra.append("(not met)")
    if due_day is not None:
        d = due_date(c)
        extra.append("(never contacted)" if d == "" else f"(due since {d})")
    return bits[0] + ("   " + "  ".join(extra) if extra else "")


def _filters(args):
    known = args.known_by
    return {
        "q": args.search or "",
        "role": "",
        "tag": norm(args.tag) if args.tag else "",
        "tie": args.tie or "",
        "knownBy": known,
        "update": bool(args.update),
        "now": bool(args.now),
    }


def cmd_list(args, backend):
    state = backend.load()
    f = _filters(args)
    if args.role:
        f["role"] = resolve_role(state, args.role)
    if f["knownBy"]:
        f["knownBy"] = resolve_member(state, f["knownBy"]) or "nobody"
    day = today()
    due, rest = sections(state, f, day)
    if args.due:
        rest = []
    if args.json:
        print(json.dumps(due + rest, indent=2, ensure_ascii=False))
        return
    if not due and not rest:
        print("no one matches" if state["contacts"] else "no one on the list yet")
        return
    if due:
        print("DUE TO REACH OUT")
        for c in due:
            print("  " + describe(state, c, day))
    if rest:
        print("EVERYONE ELSE" if due else "PEOPLE")
        for c in rest:
            print("  " + describe(state, c))


def cmd_find(args, backend):
    args.search = args.query
    cmd_list(args, backend)


def cmd_due(args, backend):
    args.due = True
    cmd_list(args, backend)


def _contact_from(args, state, text):
    parsed = parse_quick_add(text, state)
    if not parsed["name"]:
        raise BoardError(f"no name in '{text}'")
    # Who knows them: what the text and the flags say, or else you.
    known = list(parsed["knownBy"])
    for k in args.known_by or []:
        p = resolve_member(state, k)
        if p and p not in known:
            known.append(p)
    if not known and board.me():
        known = [resolve_member(state, "me")]
    fields = {
        "name": parsed["name"],
        "line": args.line if args.line is not None else parsed["line"],
        "org": args.org or "",
        "email": args.email or "",
        "tags": parsed["tags"] + list(args.tag or []),
        "roles": parsed["roles"] + [resolve_role(state, r) for r in args.role or []],
        "knownBy": known,
        "introVia": args.intro or "",
        "tie": "" if args.tie in (None, "none") else args.tie,
        "every": args.every or 0,
        "update": bool(args.update),
        "next": args.next or "",
        "notes": args.notes or "",
        "file": args.notes_file or "",
    }
    return {"op": "add", "contact": normalize_contact(dict(fields, id=make_id(), created=board.now_iso()))}


def cmd_add(args, backend):
    state = backend.load()
    if args.bulk:
        if args.text:
            raise BoardError("use either --bulk (reading stdin) or one person's text, not both")
        lines = [line for line in re.split(r"\r?\n", sys.stdin.read()) if parse_quick_add(line, state)["name"]]
        muts = [_contact_from(args, state, line) for line in lines]
    else:
        if not args.text:
            raise BoardError("give the person's text, or --bulk to read many from stdin")
        muts = [_contact_from(args, state, args.text)]
    if not muts:
        print("nothing to add")
        return
    backend.update(muts)
    for m in muts:
        print(f"{m['contact']['id']}  {m['contact']['name']}")


def cmd_show(args, backend):
    state = backend.load()
    c = find_contact(state, args.ref)
    if args.json:
        print(json.dumps(c, indent=2, ensure_ascii=False))
        return
    print(c["name"] + (f" — {c['line']}" if c["line"] else ""))
    rows = [
        ("id", c["id"]),
        ("organization", c["org"]),
        ("email", c["email"]),
        ("tags", " ".join(f"#{t}" for t in c["tags"])),
        ("roles", ", ".join(c["roles"])),
        ("known by", ", ".join(team_name(state, p) for p in c["knownBy"]) or "nobody yet"),
        ("intro via", c["introVia"]),
        ("tie", TIE_LABELS.get(c["tie"], "")),
        ("keep in touch", f"every {c['every']} month{'s' if c['every'] != 1 else ''}" if c["every"] else ""),
        ("gets update", "yes" if c["update"] else ""),
        ("next step", c["next"]),
        ("notes file", c["file"]),
        ("last contact", last_contact(c) or "never"),
    ]
    for k, v in rows:
        if v:
            print(f"  {k + ':':<15} {v}")
    if c["notes"]:
        print("\n" + c["notes"])
    if c["log"]:
        print("\nLOG")
        for e in c["log"]:
            who = f" ({team_name(state, e['by'])})" if e["by"] else ""
            print(f"  {e['date']}{who}  {e['text']}   [{e['id']}]")


def cmd_set(args, backend):
    state = backend.load()
    c = find_contact(state, args.ref)
    fields = {}
    for key, attr in [("name", "name"), ("line", "line"), ("org", "org"), ("email", "email"),
                      ("introVia", "intro"), ("next", "next"), ("notes", "notes"), ("file", "notes_file")]:
        v = getattr(args, attr)
        if v is not None:
            fields[key] = v
    if args.tags is not None:
        fields["tags"] = [t for t in args.tags.split(",") if t.strip()]
    if args.roles is not None:
        fields["roles"] = [resolve_role(state, r) for r in args.roles.split(",") if r.strip()]
    if args.known_by is not None:
        fields["knownBy"] = [p for p in (resolve_member(state, k) for k in args.known_by.split(",") if k.strip()) if p]
    if args.tie is not None:
        fields["tie"] = "" if args.tie == "none" else args.tie
    if args.every is not None:
        fields["every"] = args.every
    if args.update is not None:
        fields["update"] = args.update
    if not fields:
        raise BoardError("nothing to change: give at least one field, for example --every 3")
    m = {"op": "set", "id": c["id"], "name": c["name"], "fields": fields}
    backend.update([m])
    print(f"{c['id']}  edited {c['name']}: {', '.join(fields)}")


def cmd_log(args, backend):
    state = backend.load()
    c = find_contact(state, args.ref)
    day = args.date or today()
    if not _is_date(day):
        raise BoardError(f"dates are YYYY-MM-DD, not '{day}'")
    by = resolve_member(state, args.by) if args.by else (resolve_member(state, "me") if board.me() else None)
    m = {"op": "log", "id": c["id"], "name": c["name"],
         "entry": {"id": make_id("l_"), "date": day, "by": by, "text": args.text.strip()}}
    backend.update([m])
    print(f"{c['id']}  logged {day} for {c['name']}")


def cmd_unlog(args, backend):
    state = backend.load()
    c = find_contact(state, args.ref)
    if not any(e["id"] == args.entry for e in c["log"]):
        raise BoardError(f"{c['name']} has no log entry {args.entry}")
    backend.update([{"op": "unlog", "id": c["id"], "name": c["name"], "entryId": args.entry}])
    print(f"{c['id']}  removed entry {args.entry}")


def cmd_remove(args, backend):
    state = backend.load()
    c = find_contact(state, args.ref)
    backend.update([{"op": "delete", "id": c["id"], "name": c["name"]}])
    print(f"{c['id']}  removed {c['name']}")


def cmd_roles(args, backend):
    for r in backend.load()["roles"]:
        print(f"{r['id']:<16} {r['title']}")


def cmd_tags(args, backend):
    state = backend.load()
    counts = {}
    for c in state["contacts"]:
        for t in c["tags"]:
            counts[t] = counts.get(t, 0) + 1
    for t in all_tags(state):
        print(f"{t:<24} {counts[t]}")


def cmd_stages(args, backend):
    state = backend.load()
    for s in state["stages"]:
        mark = "*" if s["id"] == state["stage"] else " "
        print(f"{mark} {s['id']:<10} {s['title']:<20} {', '.join(s['roles'])}")


def cmd_stage(args, backend):
    state = backend.load()
    if not args.id:
        print(state["stage"])
        return
    if not any(s["id"] == args.id for s in state["stages"]):
        raise BoardError(f"unknown stage '{args.id}' (stages: {', '.join(s['id'] for s in state['stages'])})")
    backend.update([{"op": "setStage", "stage": args.id}])
    print(f"stage: {args.id}")


def build_parser():
    p = argparse.ArgumentParser(prog="people.py", description="The People list, from the command line.",
                                formatter_class=argparse.RawDescriptionHelpFormatter,
                                epilog=__doc__.split("\n\n", 1)[1])
    p.add_argument("--file", help="work on this people.json directly (no syncing)")
    p.add_argument("--clone", help="work through this git clone of the data repo")
    sub = p.add_subparsers(dest="cmd", required=True)

    def filters(s):
        s.add_argument("--role")
        s.add_argument("--tag")
        s.add_argument("--tie", choices=TIES)
        s.add_argument("--known-by", help="a team member, 'me' or 'nobody'")
        s.add_argument("--update", action="store_true", help="only people who get our periodic update")
        s.add_argument("--now", action="store_true", help="only the roles the current stage needs")
        s.add_argument("--json", action="store_true")

    s = sub.add_parser("list", help="show people: who is due first, then everyone else")
    filters(s)
    s.add_argument("--search")
    s.add_argument("--due", action="store_true", help="only people due to be contacted")
    s.set_defaults(fn=cmd_list)

    s = sub.add_parser("find", help="search names, lines, tags and notes")
    s.add_argument("query")
    filters(s)
    s.set_defaults(fn=cmd_find, due=False)

    s = sub.add_parser("due", help="who is due to be contacted")
    filters(s)
    s.set_defaults(fn=cmd_due, search=None)

    s = sub.add_parser("add", help="add a person (quick add: Name, line #tag +role @name)")
    s.add_argument("text", nargs="?")
    s.add_argument("--bulk", action="store_true", help="read one person per line from stdin")
    s.add_argument("--line")
    s.add_argument("--org")
    s.add_argument("--email")
    s.add_argument("--tag", action="append")
    s.add_argument("--role", action="append")
    s.add_argument("--known-by", action="append", help="a team member or 'me'; repeatable")
    s.add_argument("--intro", help="who could introduce you")
    s.add_argument("--tie", choices=TIES + ["none"])
    s.add_argument("--every", type=int, help="keep in touch every N months")
    s.add_argument("--update", action="store_true", help="gets our periodic update")
    s.add_argument("--next")
    s.add_argument("--notes")
    s.add_argument("--notes-file", help="path of their notes file in the start-up repo")
    s.set_defaults(fn=cmd_add)

    s = sub.add_parser("show", help="one person, with notes and log")
    s.add_argument("ref", help="an id, the start of one, or a name")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_show)

    s = sub.add_parser("set", help="change fields")
    s.add_argument("ref")
    s.add_argument("--name")
    s.add_argument("--line")
    s.add_argument("--org")
    s.add_argument("--email")
    s.add_argument("--tags", help="comma separated; replaces the tags")
    s.add_argument("--roles", help="comma separated; replaces the roles")
    s.add_argument("--known-by", help="comma separated team members; replaces the list")
    s.add_argument("--intro")
    s.add_argument("--tie", choices=TIES + ["none"])
    s.add_argument("--every", type=int)
    g = s.add_mutually_exclusive_group()
    g.add_argument("--update", dest="update", action="store_true", default=None)
    g.add_argument("--no-update", dest="update", action="store_false")
    s.add_argument("--next")
    s.add_argument("--notes")
    s.add_argument("--notes-file")
    s.set_defaults(fn=cmd_set)

    s = sub.add_parser("log", help="log a contact: a call, a meeting, an update")
    s.add_argument("ref")
    s.add_argument("text")
    s.add_argument("--date", help="YYYY-MM-DD (default: today)")
    s.add_argument("--by", help="who made the contact (default: TASKS_ME)")
    s.set_defaults(fn=cmd_log)

    s = sub.add_parser("unlog", help="remove a log entry")
    s.add_argument("ref")
    s.add_argument("entry", help="the entry's id, as `show` prints it")
    s.set_defaults(fn=cmd_unlog)

    s = sub.add_parser("remove", help="take someone off the list")
    s.add_argument("ref")
    s.set_defaults(fn=cmd_remove)

    sub.add_parser("roles", help="list roles").set_defaults(fn=cmd_roles)
    sub.add_parser("tags", help="list tags in use, with counts").set_defaults(fn=cmd_tags)
    sub.add_parser("stages", help="list stages and the roles each needs").set_defaults(fn=cmd_stages)
    s = sub.add_parser("stage", help="show the current stage, or set it")
    s.add_argument("id", nargs="?")
    s.set_defaults(fn=cmd_stage)
    sub.add_parser("where", help="show where the token and settings come from").set_defaults(fn=board.cmd_where, local=True)
    return p


def main(argv=None):
    for stream in (sys.stdout, sys.stderr, sys.stdin):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
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
