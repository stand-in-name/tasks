# Tasks

Our shared task list, and next to it our People list: one board by topic and owner, and the people we want to talk to. It's a web app you add to your phone's home screen; the tasks and the people themselves live in a private repo.

**App:** https://stand-in-name.github.io/tasks/ · **Data:** `stand-in-name/tasks-data` → `tasks.json` and `people.json` (private)

## How it works

1. This repo holds the app: a few static files. GitHub Pages serves them at the address above.
2. Your phone's browser downloads them and runs the app. Nothing of ours runs on a server.
3. The app uses your token to read `tasks.json` and `people.json` from the private repo, and saves each change back as a commit. The Git history is the record of who changed what.
4. When you both change the board at once, GitHub rejects the second save. The second phone then re-reads the board, re-applies its change and saves again, so nothing is overwritten.
5. The app picks up the other person's changes when you return to it, and once a minute while it's open.

The code and the tasks sit in separate repos because the token is stored in your phone's browser. Limited to `tasks-data`, the worst a leaked token can do is change the task list. In a shared repo, the same token could rewrite the app itself.

## A task

| Field | |
|---|---|
| `text` | One line. |
| `topic` | The section it sits under. A task whose topic no longer exists shows under Inbox. |
| `state` | `normal`, `flagged` or `waiting`; one at a time. |
| `owner` | One person or nobody, never both: a task two people own is owned by no one. |
| `note` | Optional detail, kept off the list. |
| `created` | Shows an age marker after 30 days. |

Done means deleted; the Git history is the archive. The board's topics and people are listed in `tasks.json`. Edit them there.

## The People list

Tap **People** at the top. It's a separate list in its own file, `people.json`, sharing the app's token, settings and sync. Tasks about people, such as "Talk to Dana", stay on the board.

| Field | |
|---|---|
| `name`, `line` | Who they are, and one line on what they do. |
| `org`, `email` | Optional. Phone numbers stay on your phones. |
| `tags` | Expertise, for filtering: `baking`, `history`. |
| `roles` | Champion, like-minded, expert, investor, customer, channel, design partner, supplier, talent. |
| `knownBy` | Which of you knows them; empty means neither, and `introVia` says who could introduce you. |
| `tie` | Close, acquaintance, dormant, or not met yet. |
| `every` | Keep in touch every 1, 3, 6 or 12 months; 0 means only when you need them. |
| `update` | Gets your periodic update. |
| `next`, `notes` | The next step, and free notes. |
| `file` | Path of their notes file in the `start-up` repo, where what you know about them in depth lives. |
| `log` | Dated contacts: a call, a coffee, an update. |

- **Adding:** type `Dana Levi, head baker at a bakery chain #baking +expert @gal` and tap **+**. `#` adds a tag, `+` a role, `@` who knows them; without an `@` they're yours. Tap **+** with the box empty to paste many people, one per line.
- **Finding:** search, plus filters for role, tag, tie and "gets update". **Mine** shows the people you know. **Now** shows the roles your current stage needs; the stage is in ⚙ and shared by both of you.
- **Keeping warm:** anyone with a keep-in-touch interval goes under **Due to reach out** once it has passed since the last logged contact, or at once if there is none. Log a contact from their card.
- **A card:** tap a person to edit them, log a contact, or put "Talk to …" on the board for one of you.

## Set up your phone

Once per person.

### Make your token

1. On GitHub: your picture → **Settings** → **Developer settings** → **Personal access tokens** → **Fine-grained tokens** → **Generate new token**.
2. **Resource owner:** `stand-in-name`. **Repository access:** Only select repositories → `tasks-data`. **Permissions:** Repository permissions → **Contents: Read and write**. **Expiration:** up to a year; note the date.
3. Generate it and copy it.
4. If GitHub says the organization must approve the token, either of you approves it as an org owner: the org's **Settings** → **Personal access tokens** → pending requests.

### Open the app

1. Open https://stand-in-name.github.io/tasks/ in Chrome (Android) or Safari (iPhone).
2. Tap ⚙. Under **This is me**, pick your name, paste the token, enter its expiry date, and save.
3. Add it to the home screen. Chrome: menu → **Add to Home screen**. Safari: **Share** → **Add to Home Screen**.

Thirty days before the token expires, the app adds a flagged task to renew it, owned by you.

## Using it

- **Adding:** pick a topic and an owner, type, tap **+**. The owner starts as you and goes back to you after each task.
- **Shortcuts in the text:** `!` flags, `search:` files under a topic, `@gal` gives it to Gal. For example: `@gal !search: read the market report`.
- **A task:** tap it to edit its text, topic, owner or note. ▲ flags it, ◷ marks it waiting, ○ finishes it (with undo).
- **All | Mine** at the top, and the state chips under it, choose what you see. Both are remembered on that device.
- **Reordering:** long-press a row, drag, then tap **Done**.
- **Offline:** changes wait on the phone and sync when you're back online.

## From a computer, or for an agent

`tools/tasks.py` and `tools/people.py` do the same from a terminal. They need Python 3.8 or later and nothing else.

```
python tools/tasks.py list --mine
python tools/tasks.py add "@gal !search: read the market report"
python tools/tasks.py own t_ab12cd34ef gal
python tools/tasks.py done t_ab12

python tools/people.py list --tag baking
python tools/people.py add "Dana Levi, head baker at a bakery chain #baking +expert @gal"
python tools/people.py add --bulk < names.txt
python tools/people.py log dana "coffee: would try it in two shops"
python tools/people.py due
```

Both reach the data in one of two ways, with the same settings:

- **With a token, like the phone.** Set `TASKS_REPO=stand-in-name/tasks-data` and `TASKS_ME` to your id (`gur` or `gal`). Put the token in `TASKS_TOKEN`, or in the token file; `tasks.py where` shows its path.
- **Through a Git clone of `tasks-data`.** Pass `--clone path/to/tasks-data`, or set `TASKS_CLONE`. Git access is all it needs, which suits agents. Each change pulls, commits and pushes, and if the other side pushed first, it re-applies the change on top and pushes again.

The app and the tools write each file identically, so the history shows changes, not reformatting. Edit them through either rather than by hand.

## Hosting

GitHub Pages, from this repo's `main` branch, root folder: **Settings** → **Pages** → **Deploy from a branch**.

## Tests

```
pip install pytest playwright
python -m playwright install chromium webkit
python -m pytest tests -q
```

The suite runs the app's logic, storage and sync checks inside Chromium as a Pixel 7a and WebKit as an iPhone 14. It drives the UI with real taps, long-presses and drags, including two phones sharing one board and one People list through a stand-in for GitHub. It also tests both command-line tools, including two writers colliding through Git and through the API, and checks that the Python and JavaScript sides write identical files.

What it can't cover: how the gestures feel on a real phone, and the one-time token and install.

## Layout

```
index.html  app.js      UI wiring; holds no rules of its own
            logic.js    the task rules: mutations, filters, quick add, file format
            people.js   the People rules: the same, plus who is due
            store.js    GitHub's Contents API, and saving without overwriting
            sync.js     offline queue, debounced saves, polling for changes
            sw.js       caches the app itself, never the data
tools/      tasks.py    the task tool; mirrors logic.js, and holds the shared backends
            people.py   the People tool; mirrors people.js
            make_icons.py
tests/      suite.js    logic, storage and sync checks, run in the browser
            test_ui.py, test_people_ui.py    UI flows on both phones
            test_cli.py, test_people_cli.py  the command-line tools
```
