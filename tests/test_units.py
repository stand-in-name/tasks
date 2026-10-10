"""Logic, storage and sync suites, executed inside the engines that will run
the app. Each JS check is reported individually."""

from conftest import report


def _run(page, fn):
    return page.evaluate(f"async () => {{ const m = await import('/tests/suite.js'); return await m.{fn}(); }}")


def test_logic(page):
    report(_run(page, "runLogic"))


def test_store(page):
    report(_run(page, "runStore"))


def test_sync(page):
    report(_run(page, "runSync"))


def test_people(page):
    report(_run(page, "runPeople"))


def test_people_sync(page):
    report(_run(page, "runPeopleSync"))
