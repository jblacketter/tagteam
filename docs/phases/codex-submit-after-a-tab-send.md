# Phase 74c: Codex submit after a tab send

## Status
- [ ] Planning: plan cycle round 2
- [ ] Implementation
- [ ] Implementation Review
- [ ] Complete

## Summary
In iTerm2 mode, the watcher's message to Codex sometimes stays in Codex's composer with an empty
line under it, and is not submitted. The handoff stalls until the arbiter switches to the
reviewer tab and presses Enter. Claude Code is not affected.

## Evidence
From Liminal's watcher log (`~/projects/journaling/Liminal/.tagteam/watcher-events.jsonl`) on
tagteam 3.14.10: iTerm2 mode, watcher pid 82193, codex-cli 0.157.0. Times are UTC; the arbiter's
report quotes PDT (13:21 PDT = 20:21 UTC).

```
2026-09-25T18:17:58 turn  >> codex's turn (atlas-and-chapters plan r1)
2026-09-25T18:18:11 sent  Sent to codex: Read the handoff contract …   → submitted on its own
2026-09-25T18:21:22 turn  >> codex's turn (atlas-and-chapters plan r2)
2026-09-25T18:21:23 sent  Sent to codex: Read the handoff contract …   → submitted on its own
2026-09-25T20:21:52 turn  >> codex's turn (atlas-and-chapters impl r1)
2026-09-25T20:21:54 sent  Sent to codex: Read the handoff contract …   → stuck in the composer
```

Same watcher, same message, same session. It works on some sends and fails on others.

## Cause (hypothesis, to be confirmed by the live check below)
`iterm.write_text_to_session` types the text with `write text … newline NO`, waits
`delay 0.05`, then sends a lone CR (`ASCII character 13`). `terminal.write_text_to_session`
has the same shape: `do script <text>`, `delay 0.05` (`_SUBMIT_DELAY_S`), then `do script ""`.

Codex's composer treats a fast burst of characters as a paste. An Enter that arrives within a
short window after that burst is inserted as a newline, not taken as submit. As far as I
remember Codex's source, that window is about 120 ms; this has not been confirmed for
codex-cli 0.157.0. A fixed 50 ms gap is inside that window. When the CR does submit, it is
because scheduling latency happened to stretch the gap. That explains the intermittency.

tmux (`send-keys -l` + `C-m`) is out of scope. It is not the arbiter's workflow, and it was not
observed failing.

## Plan
1. **Longer gap (both drivers, both agents).** Raise the text→CR gap in both tab drivers
   (iTerm2's AppleScript `delay` and `terminal._SUBMIT_DELAY_S`) to 0.5 s: comfortably past
   the paste window, and still short next to a turn. Both drivers use one named constant
   instead of a literal. This is the fix; step 2 is a guarded fallback for Codex only.
2. **Recovery CR, only for a positively recognized stuck Codex composer.** After a successful
   write, `send_tab_command` waits about 1.5 s, reads the tab's last 20 lines with the
   driver's `get_session_contents`, and passes them to a pure function
   `codex_composer_holds(capture, command) -> bool` in `watcher.py`. Only if it returns True
   does it send ONE lone CR through a new driver function `submit(session_id)`, at most once
   per send, and log it (existing `watchlog.KINDS` value, no vocabulary change).
   `codex_composer_holds` returns True only when ALL of these hold; anything else is False:
   - **Not busy:** no `BUSY_PATTERNS` match in the capture (necessary, not sufficient).
   - **Composer found:** the LAST line starting with `›` (U+203A, Codex's prompt glyph) opens a
     block of: that line, then only continuation lines indented by exactly two spaces, then
     only blank lines, then Codex's composer footer — the `<model> · <directory>` status line
     (a line containing ` · ` and a path starting `~/` or `/`). No footer after the block →
     unknown layout → False. Because it is the LAST `›` block and it must be followed by the
     footer, a submitted prompt still visible in history (always above the live composer) is
     never the block examined.
   - **Contents are exactly the command:** the block's text (the `› ` prefix and the two-space
     indents stripped, lines joined) equals the sent command with ALL whitespace removed on
     both sides. Removing whitespace makes the match independent of where Codex wrapped (it
     wraps inside `/tagteam:handoff` with no space, see the capture below); anything added,
     removed or edited fails the equality.
   - **Stuck, not just typed:** at least TWO blank lines sit between the block and the footer.
     Codex always puts one blank line there (idle placeholder, and text typed but not yet
     entered); the second is the newline the paste window swallowed. A composer holding the
     text over a single blank line is text not yet at its CR, and is left alone.
   Claude Code (`❯` prompt, boxed input) never matches, so its behaviour is unchanged. If the
   live check cannot confirm the recognition on real screens, step 2 is dropped and only step 1
   ships.

   Layouts captured 2026-09-25 from codex-cli 0.157.0 in an 80×30 tmux pane (text typed with
   `send-keys -l`, never submitted). Stuck (text, then the swallowed newline):
   ```
   › Read the handoff contract (`tagteam contract`; in Claude Code: /
     tagteam:handoff) and handoff-state.json, then act on your turn


     GPT-6-Astra medium · ~/projects/tagteam-74c
                                                          ⚠ 1 warning · f2 to view
   ```
   The same text typed without the newline has one blank line there, not two; the idle composer is
   `› Ask Codex to do anything` (placeholder) over the same footer. The folder-trust dialog also
   uses `›` (`› 1. Trust and continue`) but is followed by `enter continue · esc back`, not the
   footer, and its text is not the command.
3. **Tests.**
   - `tests/test_iterm.py`, `tests/test_terminal.py`: the script uses the named gap and the CR
     comes after it; `submit()` sends a lone CR only.
   - `tests/fixtures/codex_screens/*.txt` (verbatim captures, plus hand-made variants marked as
     such in a README line): stuck wrapped command (True); stuck one-line command (True);
     typed without the blank line (False); idle placeholder (False); submitted prompt in
     history above an empty/placeholder composer (False); composer holding the command plus an
     edit (False); composer holding unrelated text (False); trust dialog (False); busy screen
     with `Working (` (False); Claude Code idle and Claude Code holding the command (False);
     no footer / truncated capture (False); empty capture (False).
   - `tests/test_watcher*.py`: `send_tab_command` calls `submit` exactly once when the fake
     driver's capture is the stuck fixture, and never for the others; a failed or empty read
     never triggers it; the recovery is logged.

## Verification
- The gate's full-suite run on submit (the run on the record).
- **Live checks (iTerm2 + codex-cli 0.157.0), reported verbatim and kept separate:**
  - *Direct-driver timing experiment* (evidence for the cause, not for the recovery):
    `iterm.write_text_to_session` into a scratch Codex tab with a harmless prompt ("Reply with
    only: ok"), 20 sends on `main` (50 ms) and 20 with the new gap; count sends left in the
    composer. If `main` never reproduces the defect, the submission says so and the cause stays
    a hypothesis.
  - *End-to-end recovery check* (evidence for the shipped path): 20 sends through
    `watcher.send_iterm_command` on this branch into the same kind of tab. Per send, record:
    submitted or not; whether the recovery CR fired (watcher log line); and how many user
    messages Codex recorded for it (the session transcript under `~/.codex/sessions/`) —
    exactly-once means one user message and one reply per send, never two. A capture of the
    screen before any recovery CR is kept for each firing, so a firing can be checked against
    the recognition rule.
  - If the recovery never fires in 20 sends (likely, with the longer gap), that is reported as
    "not exercised live"; its evidence is then the fixtures, which are real captures.
- Terminal.app is covered by unit tests only, unless the arbiter wants a live run there too.

## Out of scope
- tmux sending.
- Headless mode (it does not type into terminals).
- Changing the message text.
