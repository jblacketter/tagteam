# Phase 68: Cockpit turn bar and watcher drawer

## Status
- [x] Planning: approved round 2 (2026-09-21) at `a7dae2b`
- [x] Implementation: branch `phase/cockpit-turn-bar-and-watcher-drawer`
- [x] Implementation Review: approved round 3 (2026-09-21) at `1132e1f`; gate 2,275 passed, 5 skipped
- [x] Complete: PR #53 merged 2026-09-21 (rebase).

## Closeout
```
Phase report: cockpit-turn-bar-and-watcher-drawer — plan approved r2 · impl approved r3
  plan   2 rounds · 1 change request · 0 bounces
  impl   3 rounds · 2 change requests · 0 bounces · gate 3 runs, 23m 09s
  time   start→approve 58m 40s · implementation before first submit 20m 18s
         lead 7m 37s (3 spans, 2 unknown) · reviewer 7m 34s (5 spans) · gate 23m 11s (3 spans)  (elapsed; includes relay wait)
  usage  no usage rows stored under this phase
  turns  matched 0 of 10 · no token data 0 · unmatched 8 · unknown 2
```
Plan r1: three corrections (a gate's `pid: None` would have read as a lost turn; owed-turn rows not exhaustive; a read-only CLI path). Impl r1: story history sized before folding; drawer news detected by length. Impl r2: scroll anchor measured from the offsetParent — caught by the reviewer in a real browser, in the one place the lead had said the evidence was a node model only.

**Verification limits kept on the record.** Seen in a browser, for real: `approved`, `watcher-off`, `working` (reviewer and lead), `stalled` (SIGSTOP-ped watcher), the drawer with real history, this repo under its iTerm2 watcher. Seen in the CLI only (`tagteam watch status` on this repo while the reviewer held r1): `waiting` — "its turn was sent to its terminal". **Table tests only, never seen rendered:** `needs-you`, `turn-lost`, `paused`, `launching`, `watcher-stale`, `starting`, and the `start … impl` verb fix (made after its screenshot). The drawer's scroll anchoring has a real-Chromium regression; its sticky cue and content-based news detection are node-model tests. Nobody has used the drawer by hand since the r1 fixes.

## Implementation notes — what looking at it for real changed
The unit tests were green before the first look in a browser. Five things
then turned out wrong or missing, none of which a test had caught:

1. **The sentence was cut off.** Beside the phase chip the bar had ~400 px
   ("Approved — cockpit-turn-bar-and-watcher-d…"). It now has a full-width row
   of its own under the brand row; the chips stay small beside the brand.
2. **The drawer said "only routine lines so far"** on this repo although the
   watcher had dispatched ten turns. Cause: a bug of mine shipped in **3.14.5**
   — `/api/watcher/events` indexed `_qs()`'s already-scalar value, so `n=200`
   meant `2` and `n=37` meant `3`; Phase 67's endpoint test used `n=5`, which
   hides it. Fixed; the test now uses 37 and 200.
3. **Chatter buries the story.** Of 150 records in this repo's log, 89 were
   `info` (idle probes every 2–3 s). `watchlog.read(..., include_info=False)`
   / `?chatter=0` now drops `info` **before** taking the newest `n`; the
   drawer asks for `chatter=0` and re-asks with `chatter=1` for "show
   everything". (Plan: filter client-side. Wrong place — the window is
   counted server-side.)
4. **48 identical `turn` lines for one pre-check.** While a gate run holds the
   turn slot the watcher re-announces the owed turn on every tick. Story mode
   folds a run of the same `(kind, msg, seq)` into one record with `repeat` and
   `last_ts`; the drawer shows `(×46, until 06:57:50)`. A view only — the log
   on disk is untouched and the full listing is not folded.
5. **"claude is working on the plan" while it was implementing.** The marker
   of a `start <phase> impl` turn carries the *plan* cycle's type (the impl
   cycle does not exist until that turn creates it). The verb now follows the
   state's command for a lead turn. Test built from the real marker shape.

Also: the time column wrapped (`12:20:38` / `AM`) — widened, `nowrap`.
Deviation from the plan: while the drawer is open it re-reads `/api/now` and
the events every 5 s instead of waiting for the page's 30 s tick, because
"last look: 7s ago" is a sentence with an age in it and must not sit frozen.
Approval notes: `turn_delivered()` accepts only a terminal watcher's `sent`
with a non-null matching `seq`; a pause after delivery reads "… has its turn;
further hand-offs are held"; liveness `unknown` (no evidence) reads as plain
working, never as `finishing`.

**Impl review r1 — two history defects, both reproduced by the reviewer, both fixed.**
(a) Story mode decided whether to read `.1` on the *pre-fold* count: 201
identical live lines folded to one row and an older `sent` in `.1` was never
read. It now reads both generations (the byte cap bounds that), filters,
folds — including a run that straddles the rotation — and cuts to `n` last.
(b) The drawer detected news by list **length** and kept a **pixel** offset,
but the API returns a rolling 200-row window: at capacity the length never
changes (no cue, ever), the same pixel becomes another event as old rows drop
out, and a folded row can grow in place. It now compares content signatures,
anchors the reader to the first visible *event* (+ offset into it) while that
event is retained, and the "new events" cue is sticky until the reader reaches
the bottom or clicks it — an unchanged refresh no longer clears it (the earlier
test expected that; revised on purpose). 4 new regressions fail on the r1 code.
Also fixed here, from the live sentence on this repo after the r1 submission
("its turn was sent to its terminal · 7m", seconds after a 7½-minute pre-check):
a *delivered* turn's age counts from the dispatch (`age_of: "sent"`), not from
the submission.

**Impl review r2 — the scroll anchor measured from the wrong origin.** `topAnchor` compared
`row.offsetTop` with `box.scrollTop`; under the shipped CSS a row's offsetParent is `.wd-history`
(`position: relative`), not the scroll box, so the offset included the heading and the box's border and
padding — the reader was anchored to the wrong event. The node stub assumed rows start at the scroll box
and could not see it; I had disclosed that the scrolling was verified under that stub only, and that is
where it was wrong. Positions are now taken in the scroll container's own coordinates from bounding
rectangles (`rowTopIn`). The stub's `offsetTop` now throws, and a **real-layout regression** runs the
unchanged Phase 68 slice in headless Chromium with the shipped CSS and the shipped header markup
(`TestDrawerScrollInARealBrowser`; no new dependency — `--dump-dom` on a Chromium found in the Playwright
cache, a Chrome install or `TAGTEAM_TEST_CHROME`, skipped when there is none): wrapped rows of different
heights, the heading above the box, 30 px into event 1, event 0 drops out → still event 1, same offset,
`scrollTop` 30, cue shown. It asserts the trap itself (`offsetParent` is `.wd-history`) and fails on the r2
code (`anchor {seq: 0, delta: 15}`).

### Criterion 7 — seen, not assumed (2026-09-21, `.playwright-mcp/p68-*.png`, git-ignored)
| Shot | State, produced for real |
|---|---|
| `p68-2-this-repo-drawer` | this repo under its iTerm2 watcher: `Approved — …, plan`; drawer: `running — iterm2, pid 35417`, the terminals note, 16 story lines for the night (150 raw) |
| `p68-3-watcher-off` | scratch project, plan waiting: "Waiting on codex — the watcher is off, nothing will start its turn · 10s", empty-history state |
| `p68-4-reviewer-working` | watcher started **from the drawer** (headless): "codex is reviewing · round 1 · 1s" in the reviewer's colour; drawer filling live |
| `p68-5-lead-working` | after the automatic hand-off: the lead's turn (this shot still shows finding 5's wrong verb; fixed after it was taken, verified by test only) |
| `p68-6-approved-with-history` | "Approved — slugify, implementation"; `python3 test_slug.py` → 7 passed (run by hand) |
| `p68-7-stalled` | the scratch watcher **SIGSTOP-ped**, a new turn owed: red "Stalled: codex is owed a turn and the watcher has stopped looking · 36s"; drawer `STALE: 34s ago, expected every 10s…`; `tagteam watch status` printed the same sentence |

Not seen: `waiting` under a terminal watcher with a turn actually owed (this
repo's cycle was `done` during the session — it will be visible while the
reviewer holds this submission), `needs-you`, `turn-lost`, `paused`,
`launching` (table-tested only). Seen and left for Phase 68b: *Needs you*
stays calm while the bar says Stalled; the reviewer lane still says "the
watcher will start it".

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
