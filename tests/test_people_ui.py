"""The People list on a Pixel 7a (Chromium) and an iPhone 14 (WebKit), touch
enabled: switching lists, adding one person or many, finding them, keeping
them warm, a card's sheet, a task from a person, and two phones sharing one
list next to the board."""

import json

import pytest

from conftest import FakeGitHub, connected
from test_ui import SHOTS, press, reload, come_back_to_the_app


def people_view(page):
    page.click("#list-people")
    page.wait_for_function("() => document.body.dataset.list === 'people'")


def count(page):
    return page.evaluate("() => window.__tasks.psync.state.contacts.length")


def padd(page, text):
    before = count(page)
    page.fill("#padd-text", text)
    page.click("#padd button[type=submit]")
    page.wait_for_function("n => window.__tasks.psync.state.contacts.length > n", arg=before)


def names(page, section=None):
    sel = f"[data-section='{section}'] .pname" if section else "#plist .pname"
    return page.locator(sel).all_inner_texts()


def card(page, name):
    return page.evaluate("n => window.__tasks.psync.state.contacts.find(c => c.name === n)", name)


def open_card(page, name):
    page.locator(".person-main").filter(has_text=name).first.click()
    page.wait_for_function("() => document.getElementById('pdetail').open")


def seed_people(page):
    people_view(page)
    page.click("#padd button[type=submit]")  # empty box: the paste-many sheet
    page.fill("#pm-text", "\n".join([
        "Dana Levi, head baker at a bakery chain #baking +expert @gal",
        "Yoni Ben-David, history professor #history +champion",
        "נועה כהן, אדריכלית נוף #baking",
        "Shira Tal - investor at an early-stage fund +investor",
    ]))
    press(page, "pmany", "#pm-add")
    page.wait_for_function("() => window.__tasks.psync.state.contacts.length === 4")


# --- the two lists ----------------------------------------------------------------


def test_people_is_a_separate_list_and_the_choice_is_remembered(page):
    assert page.is_visible("#add") and not page.is_visible("#padd")
    people_view(page)
    assert page.is_visible("#padd") and page.is_visible("#p-q")
    assert not page.is_visible("#add") and not page.is_visible("#tfilters")
    reload(page)
    assert page.evaluate("() => document.body.dataset.list") == "people"
    page.click("#list-tasks")
    assert page.is_visible("#add")


def test_unconfigured_people_list_says_so(page):
    people_view(page)
    assert "Not connected to GitHub" in page.inner_text("#pempty")


# --- adding ----------------------------------------------------------------------------


def test_quick_add_reads_name_line_tags_roles_and_who_knows_them(page):
    people_view(page)
    padd(page, "Dana Levi, head baker at a bakery chain #baking +expert")
    padd(page, "Avi Mor, shop manager @gal")
    d, a = card(page, "Dana Levi"), card(page, "Avi Mor")
    assert (d["line"], d["tags"], d["roles"], d["knownBy"]) == ("head baker at a bakery chain", ["baking"], ["expert"], ["gur"])
    assert a["knownBy"] == ["gal"]
    assert names(page) == ["Avi Mor", "Dana Levi"]
    row = page.locator(".person").filter(has_text="Avi Mor")
    assert row.locator(".pline").inner_text() == "shop manager"
    assert row.locator(".owner").inner_text() == "Gal"


def test_the_paste_many_sheet_adds_one_person_per_line(page):
    seed_people(page)
    assert sorted(names(page)) == sorted(["Dana Levi", "Yoni Ben-David", "נועה כהן", "Shira Tal"])
    assert "Added 4 people" in page.inner_text("#snackbar")


def test_hebrew_names_read_right_to_left(page):
    seed_people(page)
    assert page.locator(".pname").filter(has_text="נועה").evaluate("e => getComputedStyle(e).direction") == "rtl"


# --- finding ------------------------------------------------------------------------------


def test_search_finds_names_lines_and_tags(page):
    seed_people(page)
    page.fill("#p-q", "BAKER")
    assert names(page) == ["Dana Levi"]
    page.fill("#p-q", "baking")
    assert sorted(names(page)) == ["Dana Levi", "נועה כהן"]
    page.fill("#p-q", "nobody like this")
    assert "hidden by the filters" in page.inner_text("#pempty")
    page.fill("#p-q", "")
    assert len(names(page)) == 4


def test_role_tag_and_mine_filters(page):
    seed_people(page)
    page.select_option("#pf-role", "investor")
    assert names(page) == ["Shira Tal"]
    page.select_option("#pf-role", "")
    page.select_option("#pf-tag", "history")
    assert names(page) == ["Yoni Ben-David"]
    page.select_option("#pf-tag", "")
    page.click("#who-mine")
    assert "Dana Levi" not in names(page) and len(names(page)) == 3
    assert page.locator("#plist .owner").count() == 0, "in Mine every badge would say the same name"
    reload(page)
    page.click("#who-all")
    assert len(names(page)) == 4


def test_now_shows_the_roles_the_current_stage_needs(page):
    seed_people(page)
    assert page.inner_text("#pf-now-label") == "Now · Search"
    page.click(".chip-now .chip-face")
    assert sorted(names(page)) == ["Dana Levi", "Shira Tal", "Yoni Ben-David"], "Noa has no role yet"


def test_settings_change_the_stage_for_both_of_you(page):
    seed_people(page)
    page.click("#settings-btn")
    page.select_option("#s-stage", "fill")
    press(page, "settings", "#s-save")
    page.wait_for_function("() => window.__tasks.psync.state.stage === 'fill'")
    assert page.inner_text("#pf-now-label") == "Now · Fill in the square"


# --- a card --------------------------------------------------------------------------------


def test_the_sheet_edits_a_card(page):
    seed_people(page)
    open_card(page, "Shira Tal")
    page.fill("#pd-org", "Early Fund")
    page.fill("#pd-tags", "fintech, Deep Tech")
    page.locator("#pd-known .chip-face").filter(has_text="Gal").click()
    page.select_option("#pd-tie", "unmet")
    page.select_option("#pd-every", "3")
    page.check("#pd-update")
    page.fill("#pd-next", "ask Yoni for an intro")
    press(page, "pdetail", "#pd-save")
    s = card(page, "Shira Tal")
    assert (s["org"], s["tags"], s["knownBy"], s["tie"], s["every"], s["update"], s["next"]) == (
        "Early Fund", ["fintech", "deep-tech"], ["gur", "gal"], "unmet", 3, True, "ask Yoni for an intro")
    assert page.locator(".person").filter(has_text="Shira").locator(".unmet").count() == 1


def test_saving_sends_only_what_changed(page):
    seed_people(page)
    before = page.evaluate("() => window.__tasks.psync.pending.length")
    open_card(page, "Dana Levi")
    page.select_option("#pd-every", "1")
    press(page, "pdetail", "#pd-save")
    pending = page.evaluate("() => window.__tasks.psync.pending")
    assert len(pending) == before + 1
    assert pending[-1]["op"] == "set" and pending[-1]["fields"] == {"every": 1}


def test_cancel_changes_nothing(page):
    seed_people(page)
    before = card(page, "Dana Levi")
    open_card(page, "Dana Levi")
    page.fill("#pd-name", "Someone Else")
    press(page, "pdetail", "#pdetail button[value=cancel]")
    assert card(page, "Dana Levi") == before


def test_keep_in_touch_puts_them_in_due_until_you_log_a_contact(page):
    seed_people(page)
    open_card(page, "Yoni Ben-David")
    page.select_option("#pd-every", "1")
    press(page, "pdetail", "#pd-save")
    assert names(page, "due") == ["Yoni Ben-David"]
    assert page.locator("[data-section='due'] .due").inner_text() == "never"
    assert "Yoni Ben-David" not in names(page, "all"), "nobody shows twice"
    open_card(page, "Yoni Ben-David")
    page.fill("#pd-log-text", "coffee at the lab")
    page.click("#pd-log-add")
    page.wait_for_function("() => document.querySelectorAll('#pd-log li').length === 1")
    assert "Last contact" in page.inner_text("#pd-last")
    press(page, "pdetail", "#pdetail button[value=cancel]")
    assert names(page, "due") == []
    entry = card(page, "Yoni Ben-David")["log"][0]
    assert (entry["by"], entry["text"]) == ("gur", "coffee at the lab")


def test_a_log_entry_can_be_taken_back(page):
    seed_people(page)
    open_card(page, "Dana Levi")
    page.fill("#pd-log-text", "wrong person")
    page.click("#pd-log-add")
    page.wait_for_function("() => document.querySelectorAll('#pd-log li').length === 1")
    page.click("#pd-log .unlog")
    page.wait_for_function("() => document.querySelectorAll('#pd-log li').length === 0")
    press(page, "pdetail", "#pdetail button[value=cancel]")
    assert card(page, "Dana Levi")["log"] == []


def test_a_person_becomes_a_task_on_the_board(page):
    seed_people(page)
    open_card(page, "Dana Levi")
    page.select_option("#pd-task-owner", "gal")
    page.click("#pd-task-add")
    assert "Added to the board for Gal" in page.inner_text("#pd-task-msg")
    press(page, "pdetail", "#pdetail button[value=cancel]")
    task = page.evaluate("() => window.__tasks.sync.state.tasks[0]")
    assert (task["text"], task["topic"], task["owner"]) == ("Talk to Dana Levi", "network", "gal")
    page.click("#list-tasks")
    assert page.locator(".task-text").first.inner_text().startswith("Talk to Dana Levi")


def test_remove_and_undo(page):
    seed_people(page)
    before = card(page, "Shira Tal")
    open_card(page, "Shira Tal")
    press(page, "pdetail", "#pd-remove")
    assert "Shira Tal" not in names(page)
    page.click("#undo")
    page.wait_for_function("() => window.__tasks.psync.state.contacts.some(c => c.name === 'Shira Tal')")
    assert card(page, "Shira Tal") == before


def test_the_notes_file_links_to_the_start_up_repo(pages):
    roster = {"contacts": [{"id": "p_x", "name": "Dana Levi", "file": "knowledge/people/dana-levi.md"}]}
    remote = FakeGitHub(files={"people.json": json.dumps(roster)})
    pg = pages.open(storage={**connected("gur", api=remote.base), "tasks.list": "people"})
    pg.wait_for_function("() => window.__tasks.psync.state.contacts.length === 1")
    open_card(pg, "Dana Levi")
    assert pg.get_attribute("#pd-file-link", "href") == "https://github.com/stand-in-name/start-up/blob/main/knowledge/people/dana-levi.md"
    assert pg.is_visible("#pd-file-link")


# --- two phones, one list -----------------------------------------------------------------


def seeded():
    board = {"version": 1, "topics": [{"id": "inbox", "title": "Inbox"}, {"id": "network", "title": "Network"}],
             "people": [{"id": "gur", "name": "Gur"}, {"id": "gal", "name": "Gal"}], "tasks": []}
    roster = {"contacts": [{"id": "p_dana", "name": "Dana Levi", "line": "head baker", "knownBy": ["gal"],
                            "created": "2026-10-09T10:00:00.000Z"}]}
    return FakeGitHub(json.dumps(board, indent=2) + "\n", files={"people.json": json.dumps(roster)})


def phone(pages, remote, person):
    pg = pages.open(storage={**connected(person, api=remote.base), "tasks.list": "people"})
    pg.wait_for_function("() => window.__tasks.psync.loaded && window.__tasks.sync.loaded")
    pg.evaluate("() => { window.__tasks.psync.quietMs = 50; window.__tasks.sync.quietMs = 50; }")
    return pg


def committed(page, remote, n):
    for _ in range(100):
        if len(remote.commits) >= n:
            page.wait_for_function("() => window.__tasks.psync.pending.length === 0 && window.__tasks.psync.status === 'idle'")
            return
        page.wait_for_timeout(50)
    raise AssertionError(f"expected {n} commits, saw {remote.commits}")


def test_each_phone_sees_the_other_ones_people(pages):
    remote = seeded()
    gur, gal = phone(pages, remote, "gur"), phone(pages, remote, "gal")
    tasks_before = remote.files["tasks.json"][0]
    padd(gur, "Yoni Ben-David, history professor +champion")
    committed(gur, remote, 1)
    assert remote.commits == ["people: add Yoni Ben-David"]
    assert remote.files["tasks.json"][0] == tasks_before, "a people change must not touch the board"
    come_back_to_the_app(gal)
    gal.wait_for_function("() => window.__tasks.psync.state.contacts.length === 2")
    assert sorted(names(gal)) == ["Dana Levi", "Yoni Ben-David"]


def test_both_editing_one_card_at_once_keeps_both_changes(pages):
    remote = seeded()
    gur, gal = phone(pages, remote, "gur"), phone(pages, remote, "gal")
    for pg in (gur, gal):
        pg.evaluate("() => { window.__tasks.psync.quietMs = 600000; }")
    open_card(gur, "Dana Levi")
    gur.select_option("#pd-every", "3")
    press(gur, "pdetail", "#pd-save")
    open_card(gal, "Dana Levi")
    gal.fill("#pd-next", "send her the one-pager")
    press(gal, "pdetail", "#pd-save")
    gur.evaluate("() => window.__tasks.psync.flush()")
    gur.wait_for_function("() => window.__tasks.psync.pending.length === 0")
    gal.evaluate("() => window.__tasks.psync.flush()")  # stale sha: 409, reload, replay
    gal.wait_for_function("() => window.__tasks.psync.pending.length === 0")
    d = remote.state("people.json")["contacts"][0]
    assert (d["every"], d["next"]) == (3, "send her the one-pager")


def test_a_task_from_a_person_lands_on_the_shared_board(pages):
    remote = seeded()
    gur = phone(pages, remote, "gur")
    open_card(gur, "Dana Levi")
    gur.click("#pd-task-add")
    press(gur, "pdetail", "#pdetail button[value=cancel]")
    for _ in range(100):
        if remote.commits:
            break
        gur.wait_for_timeout(50)
    assert remote.commits == ["tasks: add Talk to Dana Levi"]
    assert remote.state()["tasks"][0]["owner"] == "gur"


# --- appearance ------------------------------------------------------------------------------


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_screenshot_people(page, engine_name, scheme):
    page.emulate_media(color_scheme=scheme)
    seed_people(page)
    open_card(page, "Shira Tal")
    page.select_option("#pd-every", "1")
    press(page, "pdetail", "#pd-save")
    page.screenshot(path=str(SHOTS / f"people-{scheme}-{engine_name}.png"), full_page=True)
    open_card(page, "Dana Levi")
    page.screenshot(path=str(SHOTS / f"person-sheet-{scheme}-{engine_name}.png"))


def test_no_horizontal_overflow_with_long_names_and_a_saving_status(page):
    people_view(page)
    padd(page, "Alexandra Konstantinopolous-Wolfeschlegelstein, chief technology officer at a very long named company " * 2)
    page.evaluate("() => { document.getElementById('status').dataset.status = 'saving'; }")
    overflow = page.evaluate("() => document.documentElement.scrollWidth - document.documentElement.clientWidth")
    assert overflow <= 0, f"page scrolls horizontally by {overflow}px"
    bar = page.evaluate("() => document.querySelector('.bar').scrollWidth - document.querySelector('.bar').clientWidth")
    assert bar <= 0, f"the top bar overflows by {bar}px"


def test_people_tap_targets_are_thumb_sized(page):
    seed_people(page)
    small = page.evaluate("""() => {
        const sel = ['.person-main', '#padd button', '#padd input', '#p-q', '.chip-select', '#lists .seg-btn'];
        const bad = [];
        for (const s of sel) for (const el of document.querySelectorAll(s)) {
            const r = el.getBoundingClientRect();
            if (r.height < 36 || r.width < 36) bad.push(s + ' ' + Math.round(r.width) + 'x' + Math.round(r.height));
        }
        return bad;
    }""")
    assert small == [], f"targets too small: {small}"


def test_opening_a_card_does_not_raise_the_keyboard(page):
    seed_people(page)
    open_card(page, "Dana Levi")
    assert page.evaluate("() => document.activeElement.tagName") == "H2"
