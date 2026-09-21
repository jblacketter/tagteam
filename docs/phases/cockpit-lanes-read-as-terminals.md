# Phase 68b: Cockpit lanes read as terminals

## Status
- [x] Planning: approved round 2 (2026-09-21) at `1988723`
- [x] Implementation: branch `phase/cockpit-lanes-read-as-terminals`
- [ ] Implementation Review
- [ ] Complete

## Implementation notes — what was built, and what looking at it changed
**Seen before the fix (criterion 9).** `p68b-0-before-false-cancel-card.png`: a
scratch project whose gate runs `sleep 40`; 11 s into a healthy pre-check the bar
says "Pre-check running · round 1" while *Needs you* shows ATTENTION
"gatekeeper's process disappeared mid-turn" with a red **Cancel turn**; the
pre-check sits in codex's lane; codex's lane header says "its turn · the watcher
is off". Payload at that moment: `pid: None, pid_alive: False, liveness:
'no-child'`.

**Built as planned:** turn blocks in both lanes (`openBlock` / `foldBlock`, a
fold generation `rec.gen` that discards late SSE frames and late tail responses,
`BLOCK_CAP = 2000` for cycle, check and chat blocks with the honest note,
reopen = one consistent read); `applyBlockPolicy` (newest open, earlier folded,
a running block never folded by a refresh, the arbiter's own choice kept);
`CHECKS` store + checks strip; both lanes scoped to the cycle, one "Show last
session" switch shown in whichever lane has earlier rows; lanes and Needs-you
cards take `n.headline` (the watcher-off and stalled cards say `headline.text`;
the stalled card opens the drawer); `liveness === 'lost'` is the only thing that
offers Cancel turn; `pid_alive` appears nowhere in `cockpit.js` (guarded).
Chat turns are keyed blocks (`buildMsgRow` + a patching `fillMsgRow`,
`appendChatLine`); the signature no longer counts lines, so a poll never
rebuilds the stream; reply, error and continuity are kept.

**Approval note (shared STREAMS registry).** A reopen never resets another
consumer: if the key is already registered the block is emptied and takes the
registry's buffer replay from its start; otherwise a fresh connection starts at
0 (a fold clears the cursor). A conversation stream is filtered by turn. A
**replayed** `end` detaches the block but does not call `refreshAll` again.
Tests: reopen with the all-activity row still attached (its lines untouched, no
new connection, no duplicates); a replayed end → no refresh.

**What the live look changed (again — none of it caught by green tests):**
1. *Follow is the reader's intent, not geometry.* I first judged "at the
   bottom" by measuring; a block opening or a header patch changes the lane's
   height with no scroll event, so both lanes showed "new output ↓" to a reader
   who had never scrolled. `LANE_FOLLOW` now changes only in the scroll handler
   (`onLaneScroll`); `restick()` re-pins a following lane after opens, policy
   passes and chat renders.
2. *The lead lane had three lines of output.* The two-line "No messages yet…"
   prompt showed above the lead's turn blocks and the composer took ~150 px.
   The prompt now yields to turn blocks (a test that pinned the opposite —
   "prompt stays with cards" — is reversed on purpose and says why); the
   composer is one line until focused or typed in, its hint shown on focus.
3. *The Start card offered the thing already running* ("Next: clamp —
   implementation" beside "claude is implementing"), a consequence of 68a's
   automatic hand-off. It is not shown while a turn is in flight or the headline
   is working / starting / launching.
4. *A dollar figure in plain view.* The rendered turn log ended
   `… cost=$0.81859325 …`; the full-lane stream makes that prominent, and the
   arbiter's standing rule is no API-dollar figures (turns run on a
   subscription). **Scope addition, one line, server side:**
   `headless.render_event` no longer prints `cost=`; tokens, turns and duration
   stay; nothing stored changes. Test added against the real fixture.
5. The lead lane's "busy" line said a pre-check streams "in the reviewer
   lane"; it says "in the checks strip above the lanes".

Also from the Chromium test: trimming now loops to the cap (`while`), and the
hold anchor is kept in whole pixels — a fractional delta re-measured on every
append drifted 17 px → 16 px across a trim.

**Deviations.** (a) Chat turns are scoped by the conversation picker, not folded
by cycle time as the plan sketched — the page has no cycle start time and the
picker already scopes them; newest chat block open, earlier folded. (b) A gate
has no turn log, so its block says "no turn log for '<stem>'" (as the old card
did); showing the gate's own report there would be a server change — not done.
(c) The server-side change in 4 above.

**Guards changed on purpose** (`tests/test_cockpit_activity.py`): the lead
block must no longer contain a `<details>` "activity (N lines)" disclosure and
must use the keyed stream + cap; the reviewer-lane test now expects the
pre-check in `CHECKS` and a chip, not a lane row (counts 5/3/2 → 5/2/2); the
chat-merge test asserts the block's prompt / stream / closing line and that the
stream node survives a re-render; "prompt stays with cards" reversed (above).

### Criterion 9 — seen, not assumed (2026-09-21, `.playwright-mcp/p68b-*.png`, git-ignored)
| Shot | What it shows, produced for real (scratch project, headless watcher) |
|---|---|
| `p68b-0-before-false-cancel-card` | BEFORE the fix: the false "process disappeared — Cancel turn" card during a healthy pre-check |
| `p68b-1-checks-strip` | a finished pre-check as a chip above the lanes, its block opened; bar, card and reviewer lane saying one sentence (watcher off) |
| `p68b-2-reviewer-block-streaming` | the reviewer's turn as one block streaming into the full lane; lane header = the bar's sentence |
| `p68b-3-lead-block-streaming` | the lead's turn after the automatic hand-off — and findings 1–3 above, as first seen |
| `p68b-4-after-fixes` | **a pre-check running with NO false card**: bar "Pre-check running · round 1 · 33s", chip "running · 32s", Needs you calm; the lead's finished block full height, followed to the foot, one-line composer |
| `p68b-5-stalled-trio` | SIGSTOP-ped watcher: red bar, Needs-you card with **Open the watcher**, and codex's lane — one sentence; lead lane scoped, "Show last session" |
| `p68b-6-chat-block` | a real chat turn as a block: `you ▸ …`, its stream, `claude ▸ …` closing line (this shot still shows `cost=$…`: the scratch server had been started before the renderer change; that fix is verified by its test only) |

**Not seen:** an earlier block folding as a new turn starts *in the same cycle*
(both scratch cycles had one turn per lane — node-tested); the "new output ↓"
cue by hand; a chip for a bounced run; `turn-lost`; the watcher-stale
(delivered) card; a block past 2,000 lines. No browser pass was made after the
very last change (the trim loop and whole-pixel anchor), which the real-Chromium
tests exercise.

## Summary
The arbiter, 2026-09-20: the cockpit "should emulate the terminals to a certain
degree. A lane to the left is the lead, a lane to the right is the reviewer…
I want the lead window to show what I see in the lead tab in the CLI version.
It's OK if it starts with a fresh output on each turn." Only the lead lane has a
composer; the reviewer side needs "the running activity and status".

What the lanes are today (live looks of 2026-09-20/21; screenshots
`.playwright-mcp/trial-*.png`, `p68-*.png`):

| # | Seen | Where it comes from |
|---|---|---|
| 1 | Reviewer lane = a stack of cards, each with a log box `max-height: 16em` (~6 visible lines in a lane); lead lane = chat bubbles **and** cards. Two looks, neither a terminal. | `buildActRow` / `.act-lines` (css 322, 286); `fillMsgRow` |
| 2 | Pre-check runs sit in the reviewer's lane; `BOUNCED` reads as codex's verdict. | `inReviewerLane` accepts `role: gatekeeper` (js 1101) |
| 3 | The lead lane shows Phase 52 turn cards under the current cycle's header. | `loadActivity` → `if (inLeadLane(it)) upsertRow(LLANE, it)` — no cycle filter; the reviewer lane got one in Phase 58 (`applyLaneScope`). Open backlog item, `docs/roadmap.md`. |
| 4 | **A healthy turn is offered for cancellation.** The Needs-you card "*X*'s process disappeared mid-turn — **Cancel turn**" fires on `n.inflight.pid_alive === false` (js 578); the lanes' `gone` state and "process disappeared" text use the same test (js 995–1007). `pid_alive` is `False` for `pid: None` — the whole of every pre-check (a gate marker never has a child) and the start of every turn. Phase 68 fixed this for the headline (`inflight.liveness`); the card and the lanes still use the old flag. | read in the code; the payload shape is pinned by Phase 68's `("no-child", False)` test. **Not yet seen on screen** — to be captured before the fix (criterion 9). |
| 5 | With the bar saying **Stalled**, *Needs you* is calm ("Waiting on codex…") and the reviewer lane says "its turn · the watcher will start it". | `renderNeeds`, `laneText` derive their own story from `owed` / `watcher.running`; they do not read `n.headline`. |
| 6 | A lead turn started by the Start card runs as a chat (`YOU: /tagteam:handoff start … impl`). Since Phase 68a a headless watcher hands the plan over as a cycle turn, so this now happens only on a manual Start. | `launch` → lead conversation |

## Principles
- **Match the real world / mental model** — the thing being replaced is a
  terminal tab: one continuous, monospace, auto-following stream of what the
  agent is doing *now*. A turn is the unit the arbiter thinks in.
- **Visibility of status** — the active lane must be unmistakable, and must
  say the same thing as the turn bar (one story, told once: Phase 68).
- **Error prevention** — never offer a destructive action (Cancel turn) on a
  guess.
- **Consistency** — a turn looks the same whoever started it (watcher, Start
  card, chat) and whichever lane it is in.

## Proposed design — three choices that are the arbiter's
*(Plan review r1: no interjection from the arbiter; the reviewer agreed the recommended choices can proceed unless he says otherwise.)*

These are UX calls; the recommendation is marked. The arbiter can overrule any
of them with an interjection during plan review.

1. **Lane body = the newest turn, open and full height; earlier turns of this
   cycle folded to one line above it.** *(recommended)* — "fresh output on each
   turn", like a terminal after `clear`, with history one click away.
   Alternative: one continuous scroll of every turn's output.
2. **Pre-checks and panels move to a strip across the top of the lanes region**
   (`pre-checks  r1 ✗ bounced 7m52s · r2 ✗ 8m04s · r3 ✓ passed 7m48s`), each
   one opening its log under the strip. *(recommended)* Alternative: a third,
   narrow middle lane — but the arbiter ruled out a middle lane for the watcher
   and the same argument (looked at occasionally) applies.
3. **A chat turn is a turn**: rendered in the same stream style, opening with
   the arbiter's message as a prompt line (`you ▸ /tagteam:handoff start …`).
   *(recommended)* The composer and the conversation picker stay.

## Scope
**In**

### 1. One turn, one terminal block (both lanes)
- A **turn block** = a one-line header (`12:36:47  plan review · round 1 ·
  working · 0:41` → `done · 29s · APPROVED`) and a **stream**: the rendered
  turn log the server already serves (`/api/activity/log/<stem>/events`, SSE,
  replayed from the start for a finished turn; `/api/tail?stem=` for a closed
  one) — the same `→ Bash: …` / `← …` lines the terminal shows. Monospace,
  wraps, no inner max-height: the block's stream fills the lane and the **lane**
  scrolls.
- The newest block in a lane is open; earlier blocks of the cycle are folded to
  their header (click / Enter toggles; a block the arbiter opened stays open).
  A running block is always open and cannot be folded away by a refresh.
- **Follow like a terminal:** the lane follows new output only while already at
  the bottom; scrolling back holds the position (measured in the scroll
  container's coordinates — Phase 68's `rowTopIn`, not `offsetTop`) and shows a
  "new output ↓" cue until the arbiter returns to the bottom.
- The stream is `textContent` lines only. No `innerHTML` (the page holds the
  POST token; log lines are arbitrary text).
- Existing keyed-and-patched stores (`RLANE` / `LLANE`, `upsertRow`,
  `patchActRow`, `attachActStream`) are kept — rows are restyled and re-nested,
  not rebuilt on every refresh (a rebuild would drop the SSE cursor and the
  arbiter's scroll position).

### 2. Both lanes scoped to the current cycle
`applyLaneScope` / `LANE_CYCLE` / "Show last session" apply to `LLANE` exactly
as they do to `RLANE`. Chat turns of the selected conversation are shown in
time order with the cycle's lead turns; chat turns from before the current
cycle started fold under the same "earlier" disclosure. Closes the backlog
item; the roadmap entry is updated.

### 3. Pre-checks and panels leave the reviewer's lane
- `inReviewerLane` stops accepting `gatekeeper`; `gate`, `panel` and
  `panel_lens` items go to a new **checks strip** (`#lane-checks`) above the two
  lanes, scoped to the current cycle, newest right. Each chip: round, outcome
  word (the existing `VERDICT_WORD` / `OUTCOME_LABEL` vocabularies), duration;
  the running one pulses. Selecting a chip opens that run's log in a block under
  the strip (same block component); selecting it again closes it.
- The header's `chip-gate` stays (last decision at a glance).

### 4. Lanes and Needs-you tell the headline's story
- `laneText` stops deriving: the lane whose role is `n.headline.role` shows
  `n.headline.text` with the ticking age, and takes the bar's tone
  (`working` → pulse in the role colour; `danger` / `attention` → the lane's
  border). The other lane shows the quiet counterpart it shows today
  (`waiting` / the cycle's outcome).
- **Liveness, not `pid_alive`:** the lanes' `gone` state and the Needs-you
  "process disappeared" card key on `n.inflight.liveness === 'lost'`. Nothing in
  `cockpit.js` reads `pid_alive` afterwards (guarded). The card's wording
  follows the headline ("…'s turn was abandoned — the process running it is
  gone"); Cancel turn keeps its confirm.
- New Needs-you card for `headline.state` `stalled` / `watcher-stale`: "The
  watcher has stopped looking" — what it means, `last look` from
  `watcher.beat.text`, and one action: **Open the watcher** (opens the Phase 68
  drawer, where Stop / Start live). No new endpoint.
- **The watcher-off card stops telling its own story** (plan review r1, point 1).
  Today it says "Nothing runs X's turn until the watcher is on" for *every*
  owed turn with no watcher — including a terminal turn that was already
  delivered, where the headline (correctly) says "its turn was sent; the watcher
  has since stopped, so the next hand-off will not happen". The card keeps its
  trigger (`headline.state === 'watcher-off'`) and its **Start the watcher**
  action, but its title and body are `headline.text`; delivery is never
  re-derived in JS. The stalled / watcher-stale card does the same, so the
  delivered case reads "…it has its turn; the watcher has stopped looking" and
  never implies the agent is stuck. Both delivered and undelivered variants of
  both cards are tested.

### 5. Tests
- Node-harness tests (the Phase 43/45 slice is already run under node): block
  build / patch, newest-open and earlier-folded, a running block is never
  folded, lead-lane cycle scoping incl. "Show last session", gate/panel items
  routed to the strip and not to the reviewer lane, chat turn rendered as a
  block with its prompt line, lane text = headline text for the headline's
  role, `liveness: 'lost'` → card, `pid_alive: false` with `liveness:
  'no-child' | 'starting'` → **no** card and no `gone` lane.
- Node harness, streams: the cap for a running cycle block **and** a running
  chat block (2,050 lines in → 2,000 kept, the note row shown with the log
  path); fold drops the stream DOM; a final SSE `line`/`end` frame and a
  delayed tail response arriving **after** the fold change nothing; reopen of a
  finished block renders the tail once (no duplicates after fold → open → fold
  → open); reopen of a running block starts from `after=0` into an empty
  stream; polling does not rebuild an open chat block's stream (node identity
  preserved across `renderLeadTimeline`).
- Node harness, cards: watcher-off delivered / undelivered and stalled /
  watcher-stale each show `headline.text`; no card text mentions "Nothing runs"
  for a delivered turn.
- **Real Chromium** (Phase 68's `_run_in_chromium`, shipped CSS + markup), for a
  cycle block **and** a chat block: follows at the bottom; holds the reader's
  line while lines arrive; **cap rollover** while scrolled back — the anchored
  line survives trimming of older lines, and when the anchor itself is trimmed
  the view rests on the oldest retained line with the cue shown; the block
  fills the lane (no 16em box, one scroller).
- String guards: updated deliberately and listed in the submission
  (`OUTCOME_LABEL` / `VERDICT_WORD` stay literal; `.act-lines` selector and the
  lane ids stay; `pid_alive` joins the must-not-appear list for `cockpit.js`).

### 6. Docs
`README.md` (cockpit section + the two screenshots' alt text if they change),
`CLAUDE.md`, `docs/cockpit-issues.md` (close the 68b items), `docs/roadmap.md`
(this phase; the lead-lane backlog item → done).

**Out**
- Any server or API change beyond what a test needs (the stream endpoints, the
  activity items and `headline` / `liveness` already exist).
- Typing into a running turn; a reviewer composer (arbiter: not now).
- The roadmap tab (69), rules (70/71), jobs strip (72), the Saloon, a light theme.
- Re-rendering ANSI colour or the interactive Claude Code / Codex TUI: the
  stream is tagteam's rendering of the turn, as agreed on 2026-09-20.
- README screenshots are regenerated only if the change makes them wrong.

## Technical approach
- **Restyle and re-nest, do not rewrite.** The stores, keys, SSE attachment and
  cursor logic of Phases 43/45/58 work and are tested; the block is the
  existing row with its log box promoted from a 16em inset to the lane's body,
  and with folding added. The chat bubble renderer (`fillMsgRow`) is the part
  that is replaced, by a block whose header is the prompt line. Its row
  signature includes the streamed line count today, so every poll **rebuilds**
  the chat body; the replacement keys the stream DOM and appends to it (the
  signature covers status / reply / error only), otherwise a chat block could
  not hold the reader's place. The recorded reply, the error text and the
  continuity note ("memory: resumed session · 2 messages") are kept: reply and
  error as the block's closing lines, continuity in the header.
- **One scroller per lane.** Today each card's log box scrolls inside a lane
  that also scrolls; nested scrollers are why a lane never feels like a
  terminal. The block's stream does not scroll; the lane does.
- **Bounded streams — one policy for every block** (plan review r1, point 2).
  There is no whole-log view to link to: `/api/tail` returns JSON clamped to
  `MAX_TAIL_LINES = 2000` and the old tail drawer is gone. So:
  - **Cap:** a block's stream holds at most the **newest 2,000 lines**, running
    or finished, cycle or chat. `appendActLine` already caps a cycle block;
    the chat stream (`LEAD.lines[cid][n]`, filled by `subscribeLead`) grows
    without limit today and gets the same cap.
  - **Honest label:** when lines were dropped from a running block, or a
    finished block's tail comes back with exactly the cap (the tail payload has
    no truncation flag — `{path, lines, …}` — so "2,000 lines returned" is the
    only signal, and the note says "may be longer"), the block's first row is a
    note: "showing the last 2,000 lines — the whole log: `<path>`", the path
    being the tail payload's `path` / the activity item's `log_path`
    (selectable text, not a link: the page serves no file route). No "show the
    whole log" link. No server change.
  - **Trim vs. the reader's anchor:** lines are trimmed from the top. While the
    reader is scrolled back, the anchor is the first visible *line* (Phase 68's
    event-anchor rule, `rowTopIn`); if trimming removes the anchored line, the
    view stays on the oldest retained line (scrollTop → the block's first
    line), never jumps to the bottom, and the "new output ↓" cue stays.
  - **Fold = no stream DOM, and it stays that way.** Folding detaches the SSE
    listener (`detachActStream` / the lead stream's handler), empties the
    stream and marks the block folded; a `line` or `end` frame already in
    flight, or a delayed `/api/tail` response, is **discarded** for a folded
    block (checked at delivery, by block key and a fold generation counter).
  - **Reopen = one consistent read.** A finished block re-reads
    `/api/tail?stem=…&lines=2000` and renders exactly that. A running block
    re-attaches the SSE stream **from the start** (`after=0`), renders into an
    empty stream, and the cap applies as lines arrive — no splice of "what I
    had" with "what arrives", so nothing is duplicated or omitted. The stores'
    SSE cursors are kept for blocks that stay open; a fold resets that block's.
  - A chat block follows the same rules through the lead stream
    (`/api/lead/<cid>/events`, `?after=`).
- **Risk — the test guards.** Same rule as Phase 68: every changed guard is
  changed on purpose and listed; none is deleted to get green.
- **Look first, then again.** Phase 68's three review rounds each turned on
  something only a real page showed. This phase's submission includes a browser
  pass over a real headless cycle *after* the last code change, not only before.

## Files
- Modified: `tagteam/data/web/cockpit.html`, `cockpit.js`, `cockpit.css`,
  `tests/test_cockpit_activity.py`, `README.md`, `CLAUDE.md`,
  `docs/cockpit-issues.md`, `docs/roadmap.md`

## Success criteria
1. In a headless cycle each lane shows the running turn as one full-lane,
   monospace, live stream; when the turn ends and the next begins, the lane
   shows the new turn's output with the earlier one folded above it.
2. Lead and reviewer blocks look the same; a chat turn is a block that opens
   with the arbiter's message.
3. Neither lane shows a turn from another cycle unless "Show last session" is
   on; the lead-lane backlog item is closed.
4. No pre-check or panel appears in the reviewer's lane; the checks strip shows
   them per round and opens a run's log.
4a. No block holds more than 2,000 stream lines, running or finished, cycle or
   chat; a truncated block says so and names the local log path; a folded block
   holds no stream DOM whatever arrives late; reopening neither duplicates nor
   omits output.
5. During a pre-check and at the start of a turn there is **no** "process
   disappeared" card and no `gone` lane; a marker whose owner is really gone
   still produces the card. `pid_alive` is not read anywhere in `cockpit.js`.
6. Under a stalled watcher and with the watcher off — delivered and not — the
   bar, the owed agent's lane and the Needs-you card say the same sentence; the
   stalled card opens the watcher drawer, the watcher-off card starts a watcher.
7. Follow / hold / cue behaviour passes in real Chromium with the shipped CSS.
8. Node-harness and guard tests pass; every changed guard is listed.
9. Seen, not assumed — screenshots (git-ignored `.playwright-mcp/`) of a real
   headless cycle: reviewer streaming, hand-off, lead streaming, earlier turn
   folded, a chat turn, the checks strip with a bounced and a passed run, the
   stalled trio (bar + lane + card); and, **before** the fix, the false
   "process disappeared" card during a real pre-check. A final pass after the
   last code change. States not seen are named in the closeout.
10. Full suite green via the gate; checkout clean afterwards.
