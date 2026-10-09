"""Test fixtures: a static server over the repo root, and the two engines your
phones run: Chromium as a Pixel 7a and WebKit as an iPhone 14, both with touch.
Every browser test runs on both."""

import base64
import functools
import hashlib
import http.server
import json
import socketserver
import threading
import uuid
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent

PIXEL_7A = dict(
    viewport={"width": 412, "height": 915},
    device_scale_factor=2.625,
    is_mobile=True,
    has_touch=True,
    user_agent=(
        "Mozilla/5.0 (Linux; Android 14; Pixel 7a) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/141.0.0.0 Mobile Safari/537.36"
    ),
)


FAKES = {}  # id -> FakeGitHub, mounted at /fake/<id>/ on the test server


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):  # noqa: D102
        pass

    def _fake(self):
        parts = self.path.split("?", 1)[0].split("/")
        return FAKES.get(parts[2]) if len(parts) > 2 and parts[1] == "fake" else None

    def do_GET(self):
        fake = self._fake()
        return fake.serve(self) if fake else super().do_GET()

    def do_PUT(self):
        fake = self._fake()
        return fake.serve(self) if fake else self.send_error(405)

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def guess_type(self, path):
        # Module scripts and the manifest need correct types; the stdlib
        # handler does not know .webmanifest.
        if str(path).endswith(".webmanifest"):
            return "application/manifest+json"
        if str(path).endswith(".js"):
            return "text/javascript"
        return super().guess_type(path)


@pytest.fixture(scope="session")
def server():
    handler = functools.partial(QuietHandler, directory=str(ROOT))
    socketserver.TCPServer.allow_reuse_address = True
    httpd = socketserver.ThreadingTCPServer(("127.0.0.1", 0), handler)
    httpd.daemon_threads = True
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{port}"
    httpd.shutdown()
    httpd.server_close()


_browsers = {}


@pytest.fixture(scope="session")
def playwright_instance():
    with sync_playwright() as p:
        yield p
        for b in _browsers.values():
            b.close()


@pytest.fixture(scope="session", params=["chromium", "webkit"])
def engine(request, playwright_instance):
    name = request.param
    if name not in _browsers:
        _browsers[name] = getattr(playwright_instance, name).launch()
    yield name, _browsers[name], device_for(playwright_instance, name)


def device_for(p, name):
    if name == "webkit":
        d = dict(p.devices["iPhone 14"])
        d.pop("default_browser_type", None)
        return d
    return PIXEL_7A


class Pages:
    """Opens app pages, each in its own browser context (its own phone)."""

    def __init__(self, engine, server):
        self.name, self.browser, self.device = engine
        self.server = server
        self.contexts = []
        self.errors = []

    def open(self, storage=None, before_load=None):
        ctx = self.browser.new_context(**self.device)
        self.contexts.append(ctx)
        if storage:
            ctx.add_init_script(
                "(() => { if (sessionStorage.getItem('__seeded')) return;"
                " sessionStorage.setItem('__seeded', '1');"
                f" const s = {json.dumps(storage)};"
                " for (const [k, v] of Object.entries(s)) localStorage.setItem(k, v); })()"
            )
        pg = ctx.new_page()
        # Fail fast: a hung selector should surface as one quick failure, not
        # stall the whole suite behind Playwright's 30s default.
        pg.set_default_timeout(5000)
        pg.on("pageerror", lambda e: self.errors.append(f"{self.name}: {e}"))

        def on_console(m):
            # Offline and expired-token tests deliberately fail requests; the
            # browser logs those. Uncaught exceptions still come via pageerror.
            if m.type == "error" and "Failed to load resource" not in m.text:
                self.errors.append(f"{self.name}: {m.text}")

        pg.on("console", on_console)
        if before_load:
            before_load(pg)
        pg.goto(f"{self.server}/index.html")
        pg.wait_for_function("() => !!window.__tasks")
        return pg

    def close(self):
        for c in self.contexts:
            c.close()


@pytest.fixture
def pages(engine, server):
    p = Pages(engine, server)
    yield p
    p.close()
    # An uncaught exception anywhere in a flow is a failure even if the
    # assertions happened to pass.
    assert not p.errors, f"console/page errors: {p.errors}"


@pytest.fixture
def page(pages):
    """One phone, connected to nothing: the app runs on its local store."""
    return pages.open(storage={"tasks.me": "gur"})


@pytest.fixture
def engine_name(engine):
    return engine[0]


# --- a stand-in for GitHub's Contents API --------------------------------------


class FakeGitHub:
    """One file in one repo, shared by every phone pointed at it. It is served
    by the test server itself, same origin as the app, so it behaves the same
    in both engines. Rejects a write whose sha is stale, exactly like the real
    API, and can be told to serve the copy it replaced for a while (GitHub does
    that right after a write)."""

    def __init__(self, text=None, token="test-token"):
        self.id = uuid.uuid4().hex
        self.base = f"/fake/{self.id}"
        self.token = token
        self.text = text
        self.sha = self._sha(text) if text is not None else None
        self.commits = []
        self.prev = None
        self.stale_gets = 0
        self.lock = threading.Lock()
        FAKES[self.id] = self

    @staticmethod
    def _sha(text):
        return hashlib.sha1(text.encode("utf-8")).hexdigest()

    def state(self):
        with self.lock:
            return json.loads(self.text)

    @staticmethod
    def _send(h, status, body):
        data = json.dumps(body).encode("utf-8")
        h.send_response(status)
        h.send_header("Content-Type", "application/json")
        h.send_header("Content-Length", str(len(data)))
        h.end_headers()
        h.wfile.write(data)

    def serve(self, h):
        with self.lock:
            if h.headers.get("Authorization") != f"Bearer {self.token}":
                return self._send(h, 401, {"message": "Bad credentials"})
            if h.command == "GET":
                if self.stale_gets and self.prev:
                    self.stale_gets -= 1
                    sha, text = self.prev
                else:
                    sha, text = self.sha, self.text
                if text is None:
                    return self._send(h, 404, {"message": "Not Found"})
                return self._send(h, 200, {"sha": sha, "content": base64.b64encode(text.encode("utf-8")).decode("ascii")})
            body = json.loads(h.rfile.read(int(h.headers["Content-Length"])))
            if (body.get("sha") or None) != self.sha:
                return self._send(h, 409, {"message": "conflict"})
            self.prev = (self.sha, self.text)
            self.text = base64.b64decode(body["content"]).decode("utf-8")
            self.sha = self._sha(self.text + str(len(self.commits)))
            self.commits.append(body["message"])
            return self._send(h, 200, {"content": {"sha": self.sha}})


def connected(person, owner="stand-in-name", repo="tasks-data", token="test-token", api=None):
    """localStorage for a phone that is set up and connected."""
    s = {"tasks.me": person, "tasks.owner": owner, "tasks.repo": repo, "tasks.token": token}
    if api:
        s["tasks.api"] = api
    return s


def report(results):
    """Turn a JS results array into a readable pass/fail table + assertion."""
    lines, failed = [], []
    for r in results:
        mark = "PASS" if r["ok"] else "FAIL"
        lines.append(f"  [{mark}] {r['name']}" + ("" if r["ok"] else f"\n         {r.get('detail', '')}"))
        if not r["ok"]:
            failed.append(r["name"])
    print(f"\n{len(results) - len(failed)}/{len(results)} passed")
    print("\n".join(lines))
    assert not failed, f"{len(failed)} failed: {failed}"
