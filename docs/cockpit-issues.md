# Cockpit issues (arbiter notes for a later UX phase)

Running list of problems seen while using the cockpit for real. Kept as evidence for the
cockpit-hardening / UX phase that will follow the current run of engine phases; the cockpit is
not being used for day-to-day work until then (the CLI loop is).

## 2026-08-16 — a running handoff is not legible as a handoff — RESOLVED by Phase 43 (3.7)

*Resolution (2026-08-17, `docs/phases/cockpit-hardening.md`):* the Watch zone opens with a **Cycle**
region — Lead / Reviewer lanes with the turn token on the owed side, the running agent's lane pulsing
and its kind named (`cycle turn r2` vs `lead conversation` vs `gate` …) — over a persistent **Activity**
log of every agent turn (both roles, gates, panel lenses, briefs, conversations) whose running row
streams its log and whose finished rows *stay* with a named outcome (`finished · cancelled · failed ·
timed out · process gone · orphaned`); rows are patched, never rebuilt. The Lead panel keeps a finished
turn's streamed lines under a collapsed disclosure; the Start card is replaced by the region's
*starting* state within one refresh; the Now strip names the in-flight kind and the last outcome; the
SSE signal covers conversation turns, launches and log growth. The tail drawer is gone (the running row
is the tail). Original report kept below as evidence.


Reported by Jack after starting a handoff from the cockpit:

- When a handoff starts it is not clear that a **handoff cycle** is in progress, as opposed to
  the lead merely working on its own. Activity was sometimes visible in a window *inside* the
  Lead panel, but that window would sometimes collapse, leaving only the previous conversation
  visible.
  1. Once it collapsed there was no way to tell whether the handoff was **still running or
     cancelled**.
  2. There was **no visible activity from Codex** (the reviewer) at all, which made it harder
     still to tell that the handoff was in process.
- Need: when a cycle starts, the cockpit must make it unmistakable that a **cycle** is running
  (not just the lead), and give a persistent **window into the activity** (both agents' turns,
  in-flight state, what happened last) that does not collapse away.

Related surfaces to look at when this is addressed: Now strip (in-flight chip), the Lead panel's
turn view vs. the cycle turn view (they are different things and should look different), the
Feed tab, `tagteam tail` equivalence in the browser, and the Needs-you/Start card transition
from "start" to "running".

Also parked for the same phase: `docs/saloon-rethink.md` (archetype cast & theme packs).

## 2026-08-17 — first real session on 3.7.0: convoluted screen, unclear labels (arbiter walk-through) — ADDRESSED on `cockpit-ux-pass` (3.7.1)

*Resolution (2026-08-17, arbiter + Claude with the ux-design-guide, no handoff cycle):* one column in
attention order (strip → Needs-you banner → Cycle → tabs); the project first in the strip and the tab
title, with the version; four chips in the arbiter's words (phase · who is working / who we wait on ·
`watcher: on/off` with one action · connection); **one Start** on the card that says what it does
(terminals left the page — `tagteam session start` stays in the CLI); the red badge only for what
truly needs a human, the Start card an invitation, a quiet one-liner otherwise; "waiting on X but the
watcher is off" card immediately, with **Start the watcher**; the glossary — turn · review · chat ·
pre-check · review lens · decision brief; working · done · cancelled · failed · timed out · process
disappeared · no result recorded; leave a note; Rounds — everywhere; the lead's tab named after the
lead agent; engine errors translated (`slot busy` → "the lead was already working on something else").
Kept for later consideration: **"Auto-run"** as the on-screen name for the watcher (Jack: keep
"watcher" for now). Original report kept below as evidence.


Reported by Jack starting a new handoff on `github-profile` from the cockpit, with Claude at his side:

1. **Three things say "Start".** The Start card offers *Start headless* and *Launch terminals*; the watcher chip offers *Start*. "Why would I want headless? The purpose here is to have a UI. Keep headless as an option, but that would be launched from the start — I'm not sure why we offer that choice here." Headless is the cockpit's *engine*, not a user choice; terminals is the terminal user's path, not the cockpit's.
2. **Nothing said which project the page was for** — a `tagteam serve` run from the wrong directory served the wrong project and nothing on screen made that obvious (small mono path in the strip; the banner in the terminal is easy to miss).
3. **The red "Needs you" badge lit for the Start card** — "what needs me?" Nothing did; the card is an invitation.
4. **Jargon on the primary path**: *in flight*, *owed*, *no watcher*, *watcher headless pid 43052*, *slot busy* (a failed launch's reason), *headless*, *interject*, *dispatch*, *process gone*, *orphaned*, *cycle turn* vs *conversation*. The arbiter's words are "who is working", "waiting on Codex", "auto-run", "chat".
5. **"Isn't there a place to see the reviewer activity?"** — on 3.5.1 there was not (that is Phase 43); on 3.7.0 the Cycle region answers it, but the question shows the region needs to be the first thing the eye lands on when a cycle runs — it is not, when *Needs you* (often an empty box) takes the left column.
6. **"Will it restart the watcher when it needs it?"** — the model of *watcher* vs *cycle* is not on screen; the user expects the thing that runs turns to be one switch, on or off, that the page tells you about when it is off and a turn is waiting.
7. Two installs on one machine (`uv tool` vs pip) → an old cockpit served for a while; the page/banner should show its version.
8. Stopping the watcher from the chip while a turn was about to be handed over left the cycle waiting with no runner; the *nothing is dispatching* card appears only after 2 minutes.

Design pass opened on branch `cockpit-ux-pass` (no handoff cycle; arbiter + Claude), evidence above.

## 2026-08-17 (later) — a cancelled chat turn read as a failure with no story

Jack cancelled the chat turn that ran `/handoff start githubio-showcase` (78 s in); the lead had already
opened the plan cycle, and once the watcher was started Codex reviewed and approved it — but the chat
showed `cancelled — cancelled by web:jackblacketter log: /…/2.log` and `resumed session`, nothing about
what stood. Fixed on `cockpit-ux-2`: "Cancelled by you (web:…) at <time>. No reply came — the activity
below shows what Claude did before that." (log path in the tooltip); "same session"; the watcher's
button reads **Start the watcher** (card) / **Start** (chip), as Jack asked.
- *Lanes (Phase 45, `docs/phases/cockpit-lanes.md`, 3.8):* "awkward to scroll up … Lead (Claude) and Reviewer (Codex)
  should be clearly labeled … since we start with the lead, it's unexpected to have the reviewer up above" →
  two panes in loop order, `claude — lead` (the chat + its turns) | `codex — reviewer` (pre-check → review →
  verdict, streaming in place), newest at the foot; the flat list becomes the *all activity* disclosure.
- *Same session, later:* the turn was not stopped on purpose — the row's red `cancel` link sat beside
  `hide`, and the confirm's buttons read **Cancel / Run** (two readings for a cancel). Fixed on
  `cockpit-ux-2`: a separated red **stop turn** button, confirm buttons **Stop the turn / Keep going**;
  the chat's button is **Stop turn**. Also "it wasn't clear to me that it was running something" after
  sending a chat message → a working banner with spinner + elapsed under the composer, the reply bubble
  pulses. Wanted: "when it goes to the reviewer, so I can see it" — the Cycle region does that (the
  reviewer lane pulses; the review row streams).

## 2026-08-17 (3.8.0) — the chat is below the fold

"When I talk to the lead, that needs to be the first thing, up top. I still have to scroll down to get
to the lead chat, then scroll back up to see the activity after it starts." Vertical budget, not order:
strip + Start card + lane header + a 46 vh timeline + composer > 800 px. Fixed on `cockpit-ux-3`
(3.8.1): the lanes block fits the viewport (timelines scroll inside their lane; the composer and both
streams share the first screen; tabs below the fold) and the Start card is compact (one row).

## 2026-09-20 — "I can't see the watcher", and which agent is active — ADDRESSED by Phases 67 / 68a / 68
Arbiter: "it should be very clear at all times which agent is active. right now I can't see the watcher…
it doesn't show the history, or whose turn it is, it just shows if it's running." Live look the same night
(screens and a full findings table in `docs/phases/headless-watcher-hands-an-approved-plan-to-the-lead.md`):
up to seven equal pills; `watcher: on` with nothing behind it; the notify-mode fallback giving no reason.
- Phase 67 (3.14.5): the watcher keeps a history and a heartbeat.
- Phase 68a (3.14.6): a headless cycle runs through by itself.
- Phase 68: the turn bar (one server-derived sentence) and the watcher drawer.

Still open, for **Phase 68b**: the lanes (reviewer = a card with a small log box, lead = chat bubbles; gate
runs under the reviewer's name; the lead lane not scoped to the current cycle), and two things the Phase 68
live look showed — *Needs you* stays calm while the bar says **Stalled**, and the reviewer lane still says
"the watcher will start it" under a stalled watcher. For **Phase 69**: the Start card proposes one phase when
several are startable. Separate: codex's idle prompt is not recognised by the terminal watcher's idle
patterns, so every dispatch to it waits the full 10 s (visible in `tagteam watch log`).
