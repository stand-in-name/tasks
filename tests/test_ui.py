"""UI flows on a Pixel 7a (Chromium) and an iPhone 14 (WebKit), touch enabled.

These exercise the real DOM: taps on real buttons, a real long-press, a real
drag, and two phones sharing one board through a stand-in for GitHub. What
they cannot do is tell us how a gesture *feels* on glass.
"""

import json
from pathlib import Path

import pytest

from conftest import ROOT, FakeGitHub, connected

SHOTS = Path(__file__).resolve().parent / "screenshots"
SHOTS.mkdir(exist_ok=True)


def task_count(page):
    return page.evaluate("() => window.__tasks.sync.state.tasks.length")


def add(page, text, topic="inbox", owner=None):
    before = task_count(page)
    page.select_option("#add-topic", topic)
    if owner is not None:
        page.select_option("#add-owner", owner)
    page.fill("#add-text", text)
    page.click("#add button[type=submit]")
    page.wait_for_function("n => window.__tasks.sync.state.tasks.length > n", arg=before)


def row(page, text):
    return page.locator(".task").filter(has_text=text).first


def texts(page):
    """Task texts on screen, without the owner badge and markers."""
    return page.locator(".task-text").evaluate_all(
        "els => els.filter(e => e.offsetParent !== null).map(e => e.firstChild.textContent.trim())")


def badges(page):
    return page.locator(".task .owner").all_inner_texts()


def set_filter(page, name, on):
    """Tap the chip, not the input: the checkbox itself is visually hidden."""
    if page.is_checked(f"#f-{name}") != on:
        page.click(f".chip-{name} .chip-face")
    assert page.is_checked(f"#f-{name}") is on


def topics_shown(page):
    return page.locator(".topic").evaluate_all("els => els.map(e => e.dataset.topic)")


def reload(page):
    page.reload()
    page.wait_for_function("() => !!window.__tasks")


def close_sheet(page, dialog):
    page.wait_for_function(f"() => !document.getElementById('{dialog}').open")


def press(page, dialog, button):
    """Tap a sheet's button and wait until the app has acted on it. The app's
    close handler runs a moment after the sheet closes; a listener added now
    runs right after the app's, so when it fires the change is made."""
    page.evaluate(f"""() => {{
        window.__closed = false;
        document.getElementById('{dialog}').addEventListener('close', () => {{ window.__closed = true; }}, {{ once: true }});
    }}""")
    page.click(button)
    page.wait_for_function("() => window.__closed === true")


# --- basics ----------------------------------------------------------------


def test_add_task_lands_in_its_topic(page):
    add(page, "call Dana", "network")
    assert topics_shown(page) == ["network"]
    assert texts(page) == ["call Dana"]


def test_topics_render_as_headed_sections_in_file_order(page):
    add(page, "a network thing", "network")
    add(page, "a search thing", "search")
    add(page, "an inbox thing", "inbox")
    assert topics_shown(page) == ["inbox", "search", "network"]
    heads = [h.replace("\n", " ").strip() for h in page.locator(".topic-head").all_inner_texts()]
    assert "INBOX" in heads[0] and heads[0].endswith("1")


def test_new_task_goes_to_the_top_of_its_topic(page):
    add(page, "first", "search")
    add(page, "second", "search")
    assert texts(page) == ["second", "first"]


def test_hebrew_task_renders_right_to_left(page):
    add(page, "לבדוק את המדידה", "search")
    el = page.locator(".task-text").first
    assert el.evaluate("e => getComputedStyle(e).direction") == "rtl"


# --- owners ----------------------------------------------------------------


def test_new_tasks_are_mine_by_default(page):
    add(page, "draft the weekly plan", "search")
    assert badges(page) == ["Gur"]
    assert row(page, "draft the weekly").locator(".owner-me").count() == 1


def test_the_add_bar_can_give_a_task_to_the_other_person_or_to_nobody(page):
    add(page, "check the grant rules", "search", owner="gal")
    add(page, "decide together", "search", owner="")
    assert row(page, "check the grant").locator(".owner").inner_text() == "Gal"
    assert row(page, "decide together").locator(".owner").count() == 0
    # The picker goes back to "me" after each task, so a handover can't
    # quietly take the next task with it.
    assert page.input_value("#add-owner") == "gur"
    add(page, "mine again", "search")
    assert row(page, "mine again").locator(".owner").inner_text() == "Gur"


def test_at_name_in_the_text_assigns_it(page):
    add(page, "@gal !search: read the market report", "inbox")
    r = row(page, "read the market report")
    assert r.locator(".owner").inner_text() == "Gal"
    assert r.get_attribute("data-state") == "flagged"
    assert topics_shown(page) == ["search"]


def test_mine_shows_only_my_tasks_and_drops_the_badges(page):
    add(page, "mine one", "search")
    add(page, "theirs", "search", owner="gal")
    add(page, "nobody's", "network", owner="")
    page.click("#who-mine")
    assert texts(page) == ["mine one"]
    assert badges(page) == []
    assert page.locator("#c-normal").inner_text() == "1"
    page.click("#who-all")
    assert sorted(texts(page)) == ["mine one", "nobody's", "theirs"]


def test_mine_is_remembered_on_this_device(page):
    add(page, "mine one", "search")
    add(page, "theirs", "search", owner="gal")
    page.click("#who-mine")
    reload(page)
    assert page.get_attribute("#who-mine", "aria-pressed") == "true"
    assert texts(page) == ["mine one"]


def test_adding_for_someone_else_in_mine_says_where_it_went(page):
    page.click("#who-mine")
    add(page, "for Gal", "search", owner="gal")
    assert texts(page) == []
    assert page.locator("#snackbar").is_visible()
    assert page.locator("#snackbar-text").inner_text() == "Added for Gal"
    assert page.locator("#undo").is_hidden()


def test_mine_before_saying_who_you_are_asks_first(pages):
    page = pages.open()
    page.click("#who-mine")
    assert page.locator("#settings").is_visible()
    assert "Pick who you are" in page.locator("#s-msg").inner_text()
    page.select_option("#s-me", "gal")
    press(page, "settings", "#s-save")
    page.click("#who-mine")
    assert page.get_attribute("#who-mine", "aria-pressed") == "true"


def test_detail_sheet_hands_a_task_over_and_back_to_nobody(page):
    add(page, "hand me over", "search")
    page.click(".task-text")
    assert page.input_value("#d-owner") == "gur"
    page.select_option("#d-owner", "gal")
    press(page, "detail", "#d-save")
    assert badges(page) == ["Gal"]
    page.click(".task-text")
    page.select_option("#d-owner", "")
    press(page, "detail", "#d-save")
    assert badges(page) == []


def test_owners_survive_a_reload(page):
    add(page, "theirs", "search", owner="gal")
    reload(page)
    assert badges(page) == ["Gal"]


# --- states ----------------------------------------------------------------


def test_flag_toggles_on_and_off(page):
    add(page, "flag me", "search")
    r = row(page, "flag me")
    r.locator(".mark-flag").click()
    assert r.get_attribute("data-state") == "flagged"
    r.locator(".mark-flag").click()
    assert r.get_attribute("data-state") == "normal"


def test_waiting_toggles_and_greys_the_row(page):
    add(page, "wait on someone", "search")
    r = row(page, "wait on someone")
    r.locator(".mark-wait").click()
    assert r.get_attribute("data-state") == "waiting"
    normal = page.evaluate("getComputedStyle(document.documentElement).getPropertyValue('--text').trim()")
    greyed = r.locator(".task-text").evaluate("e => getComputedStyle(e).color")
    assert greyed != normal


def test_states_are_mutually_exclusive(page):
    add(page, "conflicted", "search")
    r = row(page, "conflicted")
    r.locator(".mark-wait").click()
    r.locator(".mark-flag").click()
    assert r.get_attribute("data-state") == "flagged"


# --- filters ---------------------------------------------------------------


def test_filter_chips_hide_and_show_states(page):
    add(page, "plain one", "search")
    add(page, "flagged one", "search")
    row(page, "flagged one").locator(".mark-flag").click()
    set_filter(page, "normal", False)
    assert texts(page) == ["flagged one"]
    set_filter(page, "flagged", False)
    set_filter(page, "normal", True)
    assert texts(page) == ["plain one"]
    set_filter(page, "flagged", True)
    assert sorted(texts(page)) == ["flagged one", "plain one"]


def test_filters_persist_across_a_reload(page):
    add(page, "plain one", "search")
    set_filter(page, "normal", False)
    reload(page)
    assert page.is_checked("#f-normal") is False


def test_hiding_everything_explains_itself(page):
    add(page, "plain one", "search")
    set_filter(page, "normal", False)
    assert "hidden by the filters" in page.locator("#empty").inner_text()


def test_counts_track_each_state(page):
    add(page, "one", "search")
    add(page, "two", "search")
    row(page, "two").locator(".mark-flag").click()
    assert page.locator("#c-flagged").inner_text() == "1"
    assert page.locator("#c-normal").inner_text() == "1"


# --- topics ----------------------------------------------------------------


def test_topic_collapses_and_expands_and_stays_collapsed(page):
    add(page, "hide me", "search")
    page.click(".topic-head")
    assert texts(page) == []
    reload(page)
    assert page.locator(".topic").first.get_attribute("data-collapsed") == "true"
    page.click(".topic-head")
    assert texts(page) == ["hide me"]


# --- done + undo -----------------------------------------------------------


def test_done_removes_the_task_and_undo_restores_it_in_place(page):
    add(page, "third", "search")
    add(page, "second", "search", owner="gal")
    add(page, "first", "search")
    row(page, "second").locator(".done-btn").click()
    assert texts(page) == ["first", "third"]
    assert page.locator("#undo").is_visible()
    page.click("#undo")
    assert texts(page) == ["first", "second", "third"]
    assert row(page, "second").locator(".owner").inner_text() == "Gal"


# --- detail sheet ----------------------------------------------------------


def test_detail_sheet_edits_text_note_and_topic(page):
    add(page, "rough draft", "inbox")
    page.click(".task-text")
    page.fill("#d-text", "sharpened")
    page.fill("#d-note", "the long explanation that stays off the list")
    page.select_option("#d-topic", "search")
    press(page, "detail", "#d-save")
    assert topics_shown(page) == ["search"]
    assert texts(page) == ["sharpened"]
    assert page.locator(".has-note").count() == 1
    assert "long explanation" not in page.locator("#list").inner_text()


def test_cancelling_the_sheet_changes_nothing(page):
    add(page, "unchanged", "inbox")
    page.click(".task-text")
    page.fill("#d-text", "should not stick")
    page.select_option("#d-owner", "gal")
    press(page, "detail", "#detail button[value=cancel]")
    assert texts(page) == ["unchanged"]
    assert badges(page) == ["Gur"]


# --- reorder ---------------------------------------------------------------


def long_press(page, locator, ms=700):
    box = locator.bounding_box()
    page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    page.mouse.down()
    page.wait_for_timeout(ms)
    page.mouse.up()


def drag(page, src_text, dst_text, frac=0.9):
    src = row(page, src_text).bounding_box()
    dst = row(page, dst_text).bounding_box()
    page.mouse.move(src["x"] + 40, src["y"] + src["height"] / 2)
    page.mouse.down()
    page.mouse.move(dst["x"] + 40, dst["y"] + dst["height"] * frac, steps=12)
    page.mouse.up()


def test_order_does_not_change_without_entering_reorder_mode(page):
    add(page, "bottom", "search")
    add(page, "top", "search")
    before = texts(page)
    box = row(page, "top").bounding_box()
    page.mouse.move(box["x"] + 40, box["y"] + box["height"] / 2)
    page.mouse.down()
    page.mouse.move(box["x"] + 40, box["y"] + box["height"] * 2, steps=10)
    page.mouse.up()
    assert texts(page) == before
    assert page.locator("#dragbar").is_hidden()


def test_long_press_then_drag_reorders_and_survives_a_reload(page):
    add(page, "third", "search")
    add(page, "second", "search")
    add(page, "first", "search")
    long_press(page, row(page, "first"))
    assert page.locator("#dragbar").is_visible()
    drag(page, "first", "third")
    page.click("#dragdone")
    assert texts(page) == ["second", "third", "first"]
    assert page.locator("#dragbar").is_hidden()
    reload(page)
    assert texts(page) == ["second", "third", "first"]


def test_reordering_inside_mine_keeps_the_other_persons_tasks_in_place(page):
    """The drop position is computed among ALL of the topic's tasks, not just
    the visible rows, so hidden tasks don't get shuffled."""
    add(page, "mine B", "search")
    add(page, "mine A", "search")
    add(page, "Gal's", "search", owner="gal")
    # File order in search: Gal's, mine A, mine B.
    page.click("#who-mine")
    assert texts(page) == ["mine A", "mine B"]
    long_press(page, row(page, "mine A"))
    drag(page, "mine A", "mine B")
    page.click("#dragdone")
    assert texts(page) == ["mine B", "mine A"]
    order = page.evaluate("() => window.__tasks.sync.state.tasks.filter(t => t.topic === 'search').map(t => t.text)")
    assert order == ["Gal's", "mine B", "mine A"]


# --- durability and settings -------------------------------------------------


def test_tasks_survive_a_reload(page):
    add(page, "persist me", "search")
    row(page, "persist me").locator(".mark-flag").click()
    reload(page)
    assert texts(page) == ["persist me"]
    assert row(page, "persist me").get_attribute("data-state") == "flagged"


def test_edits_made_offline_queue_and_survive_a_reload(pages):
    """With a token configured but the network down, work must not be lost."""
    page = pages.open(storage=connected("gur"),
                      before_load=lambda pg: pg.route("https://api.github.com/**", lambda r: r.abort()))
    add(page, "written on the train", "search")
    page.wait_for_function("() => window.__tasks.sync.status === 'offline'")
    assert page.locator("#status").get_attribute("data-status") == "offline"
    reload(page)
    assert texts(page) == ["written on the train"]
    assert page.evaluate("() => window.__tasks.sync.pending.length") >= 1


def test_unconfigured_app_says_so_rather_than_looking_empty(page):
    assert "Not connected" in page.locator("#empty").inner_text()


def test_settings_prefills_real_values(page):
    page.click("#settings-btn")
    assert page.input_value("#s-me") == "gur"
    assert page.input_value("#s-repo") == "tasks-data"
    assert page.input_value("#s-path") == "tasks.json"


def test_on_github_pages_the_org_comes_from_the_address(pages):
    """Served from stand-in-name.github.io/tasks/, a new phone needs only a token."""
    def serve(route):
        rel = route.request.url.split("/tasks/", 1)[1].split("?")[0] or "index.html"
        f = ROOT / rel
        if not f.is_file():
            return route.fulfill(status=404, body="")
        ctype = {".js": "text/javascript", ".css": "text/css", ".html": "text/html",
                 ".webmanifest": "application/manifest+json", ".png": "image/png"}.get(f.suffix, "text/plain")
        route.fulfill(status=200, body=f.read_bytes(), headers={"Content-Type": ctype})

    ctx = pages.browser.new_context(**pages.device)
    pages.contexts.append(ctx)
    pg = ctx.new_page()
    pg.route("https://stand-in-name.github.io/tasks/**", serve)
    pg.route("https://api.github.com/**", lambda r: r.abort())
    pg.goto("https://stand-in-name.github.io/tasks/")
    pg.wait_for_function("() => !!window.__tasks")
    pg.click("#settings-btn")
    assert pg.input_value("#s-owner") == "stand-in-name"
    assert pg.get_attribute("#s-token-help", "href") == "https://github.com/stand-in-name/tasks#make-your-token"


def test_a_token_with_no_account_is_called_out(page):
    page.click("#settings-btn")
    page.click(".advanced summary")
    page.fill("#s-owner", "")
    page.fill("#s-token", "some-token")
    page.click("#s-save")
    page.wait_for_selector("#banner:not([hidden])")
    assert "GitHub account or data repo is empty" in page.locator("#banner").inner_text()


def test_an_expired_token_says_so_instead_of_failing_quietly(pages):
    def reject(route):
        route.fulfill(status=401, headers={"Access-Control-Allow-Origin": "*",
                                           "Access-Control-Allow-Headers": "Authorization, Accept, X-GitHub-Api-Version, Content-Type"},
                      content_type="application/json", body="{}")
    page = pages.open(storage=connected("gur", token="expired"),
                      before_load=lambda pg: pg.route("https://api.github.com/**", reject))
    page.wait_for_selector("#banner:not([hidden])")
    assert "rejected the token" in page.locator("#banner").inner_text()


# --- two phones, one board ---------------------------------------------------


def seeded_remote():
    board = {
        "version": 1,
        "topics": [{"id": "inbox", "title": "Inbox"}, {"id": "search", "title": "Search"},
                   {"id": "network", "title": "Network"}],
        "people": [{"id": "gur", "name": "Gur"}, {"id": "gal", "name": "Gal"}],
        "tasks": [{"id": "t_seed", "text": "send the meeting notes", "topic": "search", "state": "flagged",
                   "owner": "gur", "note": "", "created": "2026-10-09T10:00:00.000Z"}],
    }
    return FakeGitHub(json.dumps(board, indent=2) + "\n")


def phone(pages, remote, person):
    pg = pages.open(storage=connected(person, api=remote.base))
    pg.wait_for_function("() => window.__tasks.sync.loaded")
    pg.evaluate("() => { window.__tasks.sync.quietMs = 50; }")
    return pg


def saved(page):
    page.wait_for_function("() => window.__tasks.sync.pending.length === 0 && window.__tasks.sync.status === 'idle'")


def committed(page, remote, n):
    """Wait until the stand-in GitHub has received n commits. A sheet's close
    handler runs a moment after the sheet closes, so the queue alone can look
    empty before the change is even made."""
    for _ in range(100):
        if len(remote.commits) >= n:
            return saved(page)
        page.wait_for_timeout(50)
    raise AssertionError(f"expected {n} commits, saw {remote.commits}")


def come_back_to_the_app(page):
    """What the phone does when you switch back to it."""
    page.evaluate("() => document.dispatchEvent(new Event('visibilitychange'))")


def test_each_phone_sees_the_others_changes_when_you_come_back_to_it(pages):
    remote = seeded_remote()
    gur, gal = phone(pages, remote, "gur"), phone(pages, remote, "gal")
    add(gur, "call the accountant back", "network")
    committed(gur, remote, 1)
    assert remote.commits == ["tasks: add call the accountant back"]
    come_back_to_the_app(gal)
    gal.wait_for_function("() => window.__tasks.sync.state.tasks.some(t => t.text === 'call the accountant back')")
    assert row(gal, "call the accountant").locator(".owner").inner_text() == "Gur"


def test_both_writing_at_once_loses_nothing(pages):
    remote = seeded_remote()
    gur, gal = phone(pages, remote, "gur"), phone(pages, remote, "gal")
    # Neither phone has sent anything yet when both make a change.
    for pg in (gur, gal):
        pg.evaluate("() => { window.__tasks.sync.quietMs = 600000; }")
    add(gur, "from Gur", "search")
    add(gal, "from Gal", "network")
    gal.click("#list .task[data-id='t_seed'] .task-text")  # Gal takes the seeded task
    gal.select_option("#d-owner", "gal")
    press(gal, "detail", "#d-save")
    gur.evaluate("() => window.__tasks.sync.flush()")
    saved(gur)
    gal.evaluate("() => window.__tasks.sync.flush()")  # its sha is stale now: 409, reload, replay
    saved(gal)
    final = remote.state()
    assert sorted(t["text"] for t in final["tasks"]) == ["from Gal", "from Gur", "send the meeting notes"]
    assert next(t for t in final["tasks"] if t["id"] == "t_seed")["owner"] == "gal"
    come_back_to_the_app(gur)
    gur.wait_for_function("() => window.__tasks.sync.state.tasks.length === 3")
    assert row(gur, "send the meeting notes").locator(".owner").inner_text() == "Gal"


def test_a_handover_lands_in_the_other_persons_mine(pages):
    remote = seeded_remote()
    gur, gal = phone(pages, remote, "gur"), phone(pages, remote, "gal")
    gal.click("#who-mine")
    assert texts(gal) == []
    gur.click(".task-text")
    gur.select_option("#d-owner", "gal")
    press(gur, "detail", "#d-save")
    committed(gur, remote, 1)
    assert remote.commits == ["tasks: own t_seed → gal"]
    come_back_to_the_app(gal)
    gal.wait_for_function("() => document.querySelectorAll('.task').length === 1")
    assert texts(gal) == ["send the meeting notes"]


def test_github_serving_an_old_copy_right_after_a_write_does_not_undo_it_on_screen(pages):
    remote = seeded_remote()
    gur = phone(pages, remote, "gur")
    gur.locator(".task .done-btn").first.click()
    committed(gur, remote, 1)
    remote.stale_gets = 3
    come_back_to_the_app(gur)
    gur.wait_for_timeout(300)
    assert texts(gur) == [], "the finished task came back from a stale read"


# --- appearance ------------------------------------------------------------


def fill_board(page):
    add(page, "sketch the weekly review", "search")
    add(page, "renew the domain and the email", "search", owner="gal")
    add(page, "send the meeting notes", "search")
    add(page, "call the accountant back", "network", owner="gal")
    add(page, "set up the tasks app", "inbox", owner="")
    row(page, "send the meeting notes").locator(".mark-flag").click()
    row(page, "call the accountant").locator(".mark-wait").click()


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_screenshot_board(page, engine_name, scheme):
    page.emulate_media(color_scheme=scheme)
    fill_board(page)
    page.screenshot(path=str(SHOTS / f"board-{scheme}-{engine_name}.png"), full_page=True)


def test_screenshot_mine_and_sheets(page, engine_name):
    page.emulate_media(color_scheme="dark")
    fill_board(page)
    page.click("#who-mine")
    page.screenshot(path=str(SHOTS / f"mine-{engine_name}.png"), full_page=True)
    page.click("#who-all")
    row(page, "renew the domain").locator(".task-text").click()
    page.screenshot(path=str(SHOTS / f"task-sheet-{engine_name}.png"))
    press(page, "detail", "#detail button[value=cancel]")
    page.click("#settings-btn")
    page.screenshot(path=str(SHOTS / f"settings-{engine_name}.png"))


def test_no_horizontal_overflow_with_a_long_task(page):
    add(page, "a task with a very long title " * 6, "search", owner="gal")
    overflow = page.evaluate("() => document.documentElement.scrollWidth - document.documentElement.clientWidth")
    assert overflow <= 0, f"page scrolls horizontally by {overflow}px"


def test_tap_targets_are_thumb_sized(page):
    add(page, "measure me", "search")
    small = page.evaluate("""() => {
        const sel = ['.done-btn', '.mark', '.topic-head', '#add button', '#add input', '#add select', '.seg-btn'];
        const bad = [];
        for (const s of sel) for (const el of document.querySelectorAll(s)) {
            const r = el.getBoundingClientRect();
            if (r.height < 26 || r.width < 26) bad.push(s + ' ' + Math.round(r.width) + 'x' + Math.round(r.height));
        }
        return bad;
    }""")
    assert small == [], f"targets too small: {small}"


def test_the_add_bar_leaves_room_to_type(page):
    width = page.locator("#add-text").bounding_box()["width"]
    assert width >= 110, f"only {width:.0f}px left for typing"
