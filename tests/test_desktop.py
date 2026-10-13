"""The desktop layout: a mouse and a wide window. Phones never see it; these
run in a plain desktop Chromium, not the phone engines in conftest."""

import pytest

from test_ui import add, row, texts, reload


@pytest.fixture
def desk(playwright_instance, server):
    b = playwright_instance.chromium.launch()
    ctx = b.new_context(viewport={"width": 1440, "height": 900})
    ctx.add_init_script("(() => { if (sessionStorage.getItem('__s')) return;"
                        " sessionStorage.setItem('__s', '1'); localStorage.setItem('tasks.me', 'gur'); })()")
    pg = ctx.new_page()
    pg.set_default_timeout(5000)
    errors = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto(f"{server}/index.html")
    pg.wait_for_function("() => !!window.__tasks")
    yield pg
    assert not errors, errors
    b.close()


def test_the_list_sits_in_a_centred_column(desk):
    add(desk, "one", "search")
    box = desk.locator("#list").bounding_box()
    assert box["width"] <= 762
    assert abs(box["x"] - (1440 - box["x"] - box["width"])) < 24


def test_flag_and_wait_show_on_hover_and_stay_when_set(desk):
    add(desk, "plain", "search")
    add(desk, "flagged", "search")
    row(desk, "flagged").hover()
    row(desk, "flagged").locator(".mark-flag").click()
    desk.mouse.move(5, 5)
    op = lambda t, m: float(row(desk, t).locator(m).evaluate("e => getComputedStyle(e).opacity"))
    assert op("plain", ".mark-flag") == 0
    assert op("flagged", ".mark-flag") == 1
    row(desk, "plain").hover()
    assert op("plain", ".mark-flag") > 0


def test_the_handle_drags_without_reorder_mode_and_the_order_saves(desk):
    for t in ["third", "second", "first"]:
        add(desk, t, "search")
    assert texts(desk)[:3] == ["first", "second", "third"]
    src = row(desk, "first")
    src.hover()
    grip = src.locator(".grip").bounding_box()
    dst = row(desk, "third").bounding_box()
    desk.mouse.move(grip["x"] + 6, grip["y"] + 9)
    desk.mouse.down()
    desk.mouse.move(grip["x"] + 6, dst["y"] + dst["height"] - 4, steps=8)
    desk.mouse.up()
    assert texts(desk)[:3] == ["second", "third", "first"]
    assert not desk.locator("#dragbar").is_visible()
    assert desk.evaluate("() => document.body.classList.contains('reordering')") is False
    reload(desk)
    assert texts(desk)[:3] == ["second", "third", "first"]


def test_n_jumps_to_the_add_box_but_not_while_typing(desk):
    desk.mouse.click(5, 400)
    desk.keyboard.press("n")
    assert desk.evaluate("() => document.activeElement.id") == "add-text"
    desk.keyboard.type("note to self")
    assert desk.input_value("#add-text") == "note to self"


def test_a_note_shows_its_first_line_on_hover(desk):
    add(desk, "with a note", "search")
    row(desk, "with a note").locator(".task-text").click()
    desk.fill("#d-note", "First line here\nsecond line")
    desk.click("#d-save")
    desk.wait_for_function("() => !document.getElementById('detail').open")
    assert row(desk, "with a note").locator(".has-note").get_attribute("title") == "First line here"
