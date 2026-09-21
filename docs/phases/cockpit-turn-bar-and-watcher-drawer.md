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
`cockpit_api.headline(now)` (pure function of the `now` payload; no I/O) and
`now_payload()["headline"]`:

```
{"state": <below>, "tone": "working|waiting|attention|danger|idle|ok",
 "text": "<one sentence, no age>", "age_s": <float|null>,
 "role": "lead|reviewer|gatekeeper|you|null", "agent": "<name>|null"}
```

First match wins:

| # | state | when | text (examples) | tone |
|---|---|---|---|---|
| 1 | `needs-you` | cycle escalated / needs-human, or a roadmap pause reason | "Waiting on you — escalated" · "Waiting on you — codex has a question" · "Waiting on you — roadmap paused: blocked: …" | attention |
| 2 | `turn-lost` | in-flight marker whose process is gone (`pid_alive is False`) | "claude's turn stopped unexpectedly" | danger |
| 3 | `working` | an in-flight turn | "codex is reviewing · round 2" · "claude is implementing · round 1" · "Pre-check running · round 3" · "Review panel running · round 2" · "claude is answering you" · "Writing the escalation brief" | working |
| 3a | `launching` | a Start launch is pending (`now.launch`) and no turn is in flight yet | "Starting greeting-script, implementation — claude is on it" | working |
| 4 | `paused` | a held pause marker and a turn owed | "Paused by jack — codex's turn is held" | attention |
| 5 | `watcher-off` | a turn owed, watcher not running | "Waiting on codex — the watcher is off, nothing will start it" | attention |
| 6 | `stalled` | a turn owed, watcher running, `watcher.beat.state == "stale"` | "Stalled: codex is owed a turn — the watcher last looked 6m ago" (age from the beat) | danger |
| 7 | `starting` | a turn owed, headless watcher running and fresh | "Starting codex's turn…" | working |
| 8 | `waiting-terminal` | a turn owed, tab/tmux/notify watcher running | "Waiting on codex — in its terminal" (`attention` once owed for more than 15 min — the threshold `chip-owed` uses today — else `waiting`) | waiting |
| 9 | `approved` / `aborted` | cycle done | "Approved — watcher-event-log, implementation" | ok / idle |
| 10 | `idle` | nothing in progress | "Nothing in progress" | idle |

- Inputs are only what `now_payload()` already assembles (`state`, `cycle`,
  `owed`, `inflight`, `turn_kind`, `launch`, `last_turn`, `paused`, `watcher`
  incl. `beat`, `agents`) — no new reads. `age_s` is the age the sentence is about (in-flight turn, owed turn, pause,
  beat). The browser appends ` · 1m02s` with its existing 1 Hz ticker
  (`renderNowAges`), so the server text stays stable between polls.
- Verbs come from the existing turn vocabulary (`inflightKind`, `kindLabel`):
  the same words the lanes use — one term per concept.
- `watcher.beat` gains `text`: `watchlog.describe_beat()`'s sentence, so the
  drawer and `tagteam watch status` cannot word it differently.
- `tagteam watch status` prints the headline as its first line (`now: …`).
  It already assembles the same inputs; one extra line, still a read.

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
  Refreshed on the cockpit's existing refresh tick while the drawer is open;
  no new SSE stream, nothing fetched while it is closed.
- Empty state: "No watcher history yet — it is recorded once a watcher runs
  here (tagteam 3.14.5+)."
- Built with `createElement` / `textContent` only (the lanes' no-`innerHTML`
  rule; event messages are arbitrary text).

### 4. Tests
- `tests/test_cockpit_api.py`: a table test for `headline()` — every row above,
  the priority order (e.g. in-flight beats paused; turn-lost beats working;
  `stale` beat with a turn in flight is not `stalled`), role/agent naming from
  config, `beat.text`; existing `now` keys unchanged.
- `tests/test_watchlog.py`: `watch status` first line.
- `tests/test_cockpit_activity.py`: the drawer's row builder and the bar's
  renderer live in their own banner-delimited slice of `cockpit.js`, run under
  node by the existing harness technique (`_DOM_STUB`): events → rows, `info`
  hidden unless toggled, empty state, tone class per state, age appended
  locally, `aria-expanded` toggling. String guards updated **deliberately**:
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
1. `headline()` table test passes for every row and the stated priorities;
   `now_payload()` keeps every existing key.
2. Header: one dominant bar; `chip-owed` / `chip-inflight` / `chip-paused` /
   `chip-watcher` gone; cycle, pre-check and notes still shown; Pause works.
3. Drawer: opens from the bar and by keyboard, shows facts + history, hides
   `info` by default, fetches nothing while closed, remembers its state.
4. The notify-mode confirm and the drawer name the reasons headless is
   unavailable; a terminal-mode watcher gets the "agents are in their
   terminals" note.
5. `tagteam watch status` prints the same headline the cockpit shows.
6. Node-harness tests for the new slice pass; every changed guard is listed.
7. Seen, not assumed — screenshots attached to the submission (Playwright,
   `.playwright-mcp/`, git-ignored) of: a headless run in a scratch project
   (`starting` → `working` for each role → `approved`), the drawer open with
   real history, `watcher-off`, and this repo under its iTerm2 watcher
   (`waiting-terminal` + the terminals note). A `stalled` state is produced
   for real by SIGSTOP-ing a scratch watcher, not by faking the payload.
8. Full suite green via the gate; checkout clean afterwards.
