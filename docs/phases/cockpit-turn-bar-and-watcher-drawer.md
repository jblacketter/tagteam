# Phase 68: Cockpit turn bar and watcher drawer

## Status
- [ ] Planning
- [ ] Implementation: branch `phase/cockpit-turn-bar-and-watcher-drawer`
- [ ] Implementation Review
- [ ] Complete

## Summary
The arbiter's first ask for the cockpit (2026-09-20): "it should be very clear
at all times which agent is active. Right now I can't see the watcher, and
sometimes I need to see watcher status if something is off." Later: "[the
watcher chip] doesn't show the history, or whose turn it is, it just shows if
it's running", and the design he chose: no watcher lane — "a link to the
watcher at the top, that shows whose turn is running, and selecting that will
expand the view to see watcher history."

What is on screen today (live look, 2026-09-20; `renderNow`, `cockpit.js:149`):
a header of up to seven pills of equal weight — cycle, owed *or* in-flight,
paused, watcher, pre-check, notes. `watcher: on` is a label with a Start/Stop
button and nothing behind it. Phase 67 put the watcher's heartbeat and history
into `/api/now` (`watcher.beat`, `watcher.last_event`) and
`/api/watcher/events`; `cockpit.js` reads neither.

Split from the lanes work (now Phase 68b) so each half is reviewable.

## Diagnosis and principles
- **Visibility of system status** — "who has the ball" must be answerable at a
  glance, and "is the machinery that moves the ball alive" one click away.
- **Visual hierarchy / Von Restorff** — one dominant element in the header, not
  seven equals. The squint test should find the sentence, nothing else.
- **Progressive disclosure** — the watcher is looked at occasionally (the
  arbiter's words): collapsed by default, its chatter behind a second toggle.
- **Tesler** — the system works out the state; the arbiter does not assemble
  it from four pills. Derived once, server-side, so every surface agrees.

## Scope
**In**

### 1. The headline — derived server-side
Two pure pieces in `cockpit_api.py`, no I/O in either:

- `headline(facts)` → `{"state", "tone", "text", "age_s", "age_of", "role",
  "agent"}`. `text` never contains an age; `age_s` is the one age the sentence
  is about and `age_of` names it (`turn` | `owed` | `pause` | `beat`). The
  browser appends ` · 1m02s` with its existing 1 Hz ticker (`renderNowAges`)
  and the CLI appends it once — a single age representation, never one frozen
  in the text and another ticking beside it.
- `inflight_liveness(marker, *, child_alive, owner_gone)` → one of
  `starting | running | no-child | finishing | lost` (below).

**Inputs — side-effect-free by construction** (plan review r1, point 3).
`status_facts(root)` collects exactly what the headline needs from reads that
never open the database: `handoff-state.json` (`read_state`), `tagteam.yaml`
(agent names), the in-flight marker (`headless.read_inflight` +
`headless.slot_owner_gone`), the pause marker, `watcher_status()`, and
`watchlog.beat_view()` / `last_event()`. The cycle condition comes from the
state file's own `status` / `result` / `roadmap.pause_reason`
(`escalated`, `needs-human`, `done`, `aborted`) — not from `cycle.read_status`,
which may open `db.connect` and so create or migrate `.tagteam/tagteam.db`.
- `now_payload()` calls `status_facts()` for these facts (it already gathers
  the same ones; the gathering moves into the shared function), then adds the
  one DB-derived input the cockpit has and the CLI does not: a pending Start
  `launch`. It passes `facts + launch` to `headline()`.
- `tagteam watch status` calls `status_facts()` + `headline()` and nothing
  else. It therefore cannot show the `launching` row; every other row is
  identical by construction. Tested on a project **without** a database, with
  and without `TAGTEAM_READ_ONLY`: the tree (incl. `.tagteam/`) is
  byte-identical before and after.

**In-flight liveness** (point 1). `now.inflight.pid_alive` is `False` for
`pid: None` as well as for a dead child, and a gate marker carries `pid: None`
for its whole run (`gatekeeper.py:702`); cycle and panel markers start that
way too. The old chip's predicate must not be promoted into the headline.
`pid_alive` stays as it is (compatibility); `inflight.liveness` is added:

| liveness | when | reads as |
|---|---|---|
| `lost` | the marker's **owner** is definitively gone (`slot_owner_gone`: runner pid dead, or a recorded identity that mismatches) — or a legacy marker with no runner pid whose recorded child pid is dead | turn-lost |
| `no-child` | live owner, kind `gate` (no child by design) | working |
| `starting` | live owner, `pid is None`, any other kind (not spawned yet) | working |
| `running` | live owner, recorded child pid alive | working |
| `finishing` | live owner, recorded child pid dead — the runner is verifying and recording the result | working, worded "…'s turn has ended — recording the result" |

An owner that is alive but whose identity cannot be read is **not** gone
(`slot_owner_gone` fails closed) → never `lost` on a guess.

**Was the owed turn delivered?** (point 2: dispatch machinery ≠ agent
execution.) `watcher` gains `last_dispatch` — `watchlog.last_event(root,
DISPATCH_KINDS)`. `delivered` = its kind is `sent` and its `seq` equals the
state's `seq`. A terminal agent that already has its command may be working
whatever has since happened to the watcher; the sentence must not call that
turn stalled or unstartable.

First match wins:

| # | state | when | text | tone |
|---|---|---|---|---|
| 1 | `needs-you` | state `escalated` / `needs-human`, or a roadmap pause reason | "Waiting on you — escalated" · "Waiting on you — a question from codex" · "Waiting on you — roadmap paused: blocked: …" | attention |
| 2 | `turn-lost` | in-flight, liveness `lost` | "claude's turn was abandoned — the process running it is gone" | danger |
| 3 | `working` | in-flight, any other liveness | "codex is reviewing · round 2" · "claude is implementing · round 1" · "Pre-check running · round 3" · "Review panel running · round 2" · "claude is answering you" · "Writing the escalation brief" · (`finishing`) "codex's turn has ended — recording the result" | working |
| 3a | `launching` | cockpit only: a Start launch pending, nothing in flight | "Starting greeting-script, implementation — claude is on it" | working |
| 4 | `paused` | a turn owed and a held pause marker | "Paused by jack — codex's turn is held" | attention |
| 5a | `watcher-off` | a turn owed, no watcher, **not delivered** | "Waiting on codex — the watcher is off, nothing will start its turn" | attention |
| 5b | `watcher-off` | a turn owed, no watcher, **delivered** | "Waiting on codex — its turn was sent; the watcher has since stopped, so the next hand-off will not happen" | attention |
| 6a | `stalled` | a turn owed, watcher running, beat `stale`, not delivered | "Stalled: codex is owed a turn and the watcher has stopped looking" (`age_of: beat`) | danger |
| 6b | `watcher-stale` | same, but delivered | "Waiting on codex — it has its turn; the watcher has stopped looking" (`age_of: beat`) | attention |
| 7 | `starting` | a turn owed, **headless** watcher, beat `fresh` or `in-turn` | "Starting codex's turn…" | working |
| 8 | `waiting` | a turn owed, watcher running, anything else — wording by what is actually known: | | waiting (`attention` once owed > 15 min, `chip-owed`'s threshold today) |
| | | · tab / tmux, delivered | "Waiting on codex — its turn was sent to its terminal" | |
| | | · tab / tmux, not delivered | "Waiting on codex — the watcher will send its turn to its terminal" | |
| | | · notify | "Waiting on codex — the watcher only notifies you; run the turn in codex's session" | |
| | | · headless with beat `none` / `previous` | "Waiting on codex — a headless watcher is running but has not reported in" | |
| | | · mode unknown (process scan only) | "Waiting on codex — a watcher is running" | |
| 9 | `approved` / `aborted` | state `done` / `aborted` | "Approved — watcher-event-log, implementation" | ok / idle |
| 10 | `idle` | nothing owed, nothing in flight | "Nothing in progress" | idle |

- **Exhaustive for an owed turn:** rows 4–8 cover every combination of
  {paused} × {watcher off / running} × {beat none, previous, fresh, in-turn,
  stale} × {mode headless, tab, tmux, notify, unknown} × {delivered or not};
  row 8 is the catch-all, so an owed agent can never fall through to
  "Nothing in progress". The table test enumerates the product and asserts no
  owed combination yields `idle`, and that the owed agent's name is in every
  one of their sentences.
- **Order:** `working` outranks `paused` (a pause holds future dispatch; it
  does not stop a running turn). `watcher-off` outranks the beat: an old beat
  file is not evidence about a process that is not running — rows 6a/6b
  require `watcher.running`.
- Verbs come from the existing turn vocabulary (`inflightKind`, `kindLabel`):
  one term per concept across bar and lanes.
- `watcher.beat` gains `text` (`watchlog.describe_beat()`), so the drawer and
  `tagteam watch status` word "last look" identically.
- `tagteam watch status` prints the headline as its first line (`now: …`).

### 2. Turn bar (cockpit.html / .js / .css)
- The headline is the header's dominant element: a `<button id="turn-bar"
  aria-expanded aria-controls="watcher-drawer">` with a tone dot (pulsing only
  for `working`), the sentence + ticking age, and a small `watcher ▾` cue.
  Tone colours reuse the existing custom properties (`--ok`, `--warn`,
  `--danger`, `--lead`, `--reviewer`); the dot takes the role colour when a
  role is working so the bar and the active lane match.
- What it absorbs: `chip-owed`, `chip-inflight`, `chip-paused`,
  `chip-watcher`. **Removed** as pills; their facts live in the sentence and
  the drawer.
- What stays, demoted to small secondary items after the bar: `chip-cycle`
  (phase · type · round — context, not status), `chip-gate`, `chip-notes`,
  `#btn-pause`, connection, Saloon link.
- The Needs-you cards are unchanged (they are the *action* surface; the bar is
  the *status* surface) — including "Waiting on X, but the watcher is off" and
  its Start button.

### 3. Watcher drawer
`<section id="watcher-drawer" hidden>` directly under the header, pushing the
page down (not a modal: the arbiter keeps watching the lanes while it is open).
Opened by the turn bar; Esc and a second click close it; open/closed state is
remembered in `localStorage` like the lead conversation is.

- **Facts column:** running / not (pid, mode, started) · last look
  (`beat.text`) · dispatch paused or not · the Start / Stop control (moved here
  from the pill; same endpoints, same confirm dialogs).
- **Why the cockpit cannot run turns**, when that is so:
  - headless unavailable → the list in `START.headless.errors`, in the drawer
    **and** in the "Start the watcher?" confirm body, which today says only
    "this page cannot run turns for both agents" (trial finding 6);
  - a terminal watcher is running → "Agents are running in their terminals
    (iterm2). The lanes show only turns the cockpit runs itself."
- **History column:** `GET /api/watcher/events?n=200`, oldest first like a
  terminal, auto-scrolled to the newest, monospace, `HH:MM:SS  kind  message`,
  kinds coloured by family (dispatch · gate/panel · pause · problem). `info`
  lines are hidden behind a "show everything" toggle (off by default) — the
  eighteen tagged kinds are the story, the chatter is for debugging.
  Follows new rows **only while already scrolled to the bottom**; once the
  arbiter scrolls back, their position is kept and a small "new events ↓" cue
  appears instead.
  Refreshed on the cockpit's existing refresh tick while the drawer is open;
  no new SSE stream, nothing fetched while it is closed.
- Empty state: "No watcher history yet — it is recorded once a watcher runs
  here (tagteam 3.14.5+)."
- Built with `createElement` / `textContent` only (the lanes' no-`innerHTML`
  rule; event messages are arbitrary text).

### 4. Tests
- `tests/test_cockpit_api.py`:
  - `inflight_liveness` over **real marker shapes** (built by the marker
    writers, not hand-typed dicts): a live gate owner with `pid: None`; a
    cycle and a panel marker before the child is spawned; a live child; a
    confirmed-dead child under a live owner (`finishing`); a dead owner; a
    legacy marker without a runner pid; an owner whose identity is unreadable.
  - `headline()` table: every row; the enumerated owed-turn product (never
    `idle`, agent always named); delivered vs not for watcher-off, stale and
    terminal modes; notify wording; headless with beat `none` / `previous`;
    unknown mode; `working` over `paused`; `watcher-off` over a stale beat
    file; `text` contains no age for any row; existing `now` keys unchanged.
  - `status_facts()` opens no database: asserted by running it on a DB-less
    project with `db.connect` patched to fail the test if called.
- `tests/test_watchlog.py`: `watch status` first line; on a DB-less project,
  with **and without** `TAGTEAM_READ_ONLY`, the project tree is identical
  before and after (no `.tagteam/tagteam.db`, no sidecars).
- `tests/test_cockpit_activity.py`: the drawer's row builder and the bar's
  renderer live in their own banner-delimited slice of `cockpit.js`, run under
  node by the existing harness technique (`_DOM_STUB`): events → rows, `info`
  hidden unless toggled, empty state, tone class per state, age appended
  locally and exactly once, `aria-expanded` toggling, scroll-follow only at
  the bottom. String guards updated **deliberately**:
  `chip-owed`, `chip-inflight`, `chip-watcher` leave the required-id list and
  join the must-stay-absent list; `turn-bar`, `watcher-drawer` join the
  required list; no `innerHTML` in the new slice.
- `tests/test_gatekeeper.py`'s `chip-gate` guard and `test_server_cockpit.py`'s
  page-structure guard keep passing unchanged.

### 5. Docs
`README.md` (cockpit section: the bar and the drawer), `CLAUDE.md` (where the
headline is derived and that the front end must not re-derive it),
`docs/cockpit-issues.md` (the 2026-09-20 findings this phase resolves).

**Out**
- The lanes — Phase 68b (terminal-style streams, cycle scoping of the lead
  lane, gate runs out of the reviewer lane).
- Roadmap tab and the tab regrouping (69), rules (70/71), jobs strip (72).
- A new SSE stream for watcher events; a light theme; the Saloon.
- Changing `/api/now`'s existing keys or the Needs-you cards.
- The hub's portfolio page (it can adopt `headline` later; the field is there).

## Technical approach
- **Why server-side derivation.** The state rules are the product's logic, not
  presentation: they need the config (agent names), the beat semantics
  (`in-turn` vs `stale`) and the watcher mode. In Python they get a real table
  test; in the browser they would be re-implemented by the CLI, the hub and,
  per Phase 74, other dashboards. The browser keeps only what is presentation:
  the ticking age, colour, open/closed.
- **Why remove pills rather than add a bar above them.** Seven pills plus a
  bar is eight things; the hierarchy problem is solved by subtraction.
- **Why the drawer is not a tab.** Tabs are the bottom half's navigation for
  the cycle's content; the watcher is machinery status, wanted while looking
  at the lanes, and the arbiter asked for it at the top.
- **Risk — tests that pin ids and literals.** The front-end guards are
  intentional tripwires. Every guard this phase changes is changed on purpose
  and listed in the submission; none is deleted to get green.
- **Risk — a wrong sentence is worse than seven honest pills.** Hence the
  table test, the priority rules written down above, and a fall-through to
  plain facts (`idle` / `waiting-terminal`) rather than a clever guess.

## Files
- Modified: `tagteam/cockpit_api.py`, `tagteam/watchlog.py`,
  `tagteam/data/web/cockpit.html`, `cockpit.js`, `cockpit.css`,
  `tests/test_cockpit_api.py`, `tests/test_cockpit_activity.py`,
  `tests/test_watchlog.py`, `README.md`, `CLAUDE.md`, `docs/cockpit-issues.md`,
  `docs/roadmap.md`

## Success criteria
1. `headline()` and `inflight_liveness()` table tests pass for every row, the
   stated priorities and the enumerated owed-turn product; a running gate is
   `working`, never `turn-lost`; `now_payload()` keeps every existing key.
2. Header: one dominant bar; `chip-owed` / `chip-inflight` / `chip-paused` /
   `chip-watcher` gone; cycle, pre-check and notes still shown; Pause works.
3. Drawer: opens from the bar and by keyboard, shows facts + history, hides
   `info` by default, fetches nothing while closed, remembers its state.
4. The notify-mode confirm and the drawer name the reasons headless is
   unavailable; a terminal-mode watcher gets the "agents are in their
   terminals" note.
5. `tagteam watch status` prints the same headline the cockpit shows (every
   row but the cockpit-only `launching`), and creates or changes no file on a
   DB-less project, with or without `TAGTEAM_READ_ONLY`.
6. Node-harness tests for the new slice pass; every changed guard is listed.
7. Seen, not assumed — screenshots attached to the submission (Playwright,
   `.playwright-mcp/`, git-ignored) of: a headless run in a scratch project
   (`starting` → `working` for each role → `approved`), the drawer open with
   real history, `watcher-off`, and this repo under its iTerm2 watcher
   (`waiting-terminal` + the terminals note). A `stalled` state is produced
   for real by SIGSTOP-ing a scratch watcher, not by faking the payload.
8. Full suite green via the gate; checkout clean afterwards.
