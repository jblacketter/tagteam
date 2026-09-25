# Phase 74c: Codex submit after a tab send

## Status
- [ ] Planning: plan cycle round 1
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
1. **Longer gap.** Raise the text→CR gap in both tab drivers (iTerm2's AppleScript `delay`
   and `terminal._SUBMIT_DELAY_S`) to 0.5 s: comfortably past the paste window, and still short
   next to a turn. Both drivers use one named constant instead of a literal.
2. **Check, then submit once more.** After a successful write, `send_tab_command` waits
   briefly (about 1.5 s) and reads the tab's tail with the driver's `get_session_contents`. If
   the agent does not look busy (the watcher's existing busy patterns) **and** the sent text is
   still on the tail's last lines (the composer), it sends one lone CR through a new driver
   function, `submit(session_id)`, and logs it. It does this at most once per send.
   - The extra CR is harmless in the cases it can wrongly fire: an empty Enter in Claude Code
     is a no-op (measured 2026-08-17, `terminal.py` docstring), and so is one in Codex's empty
     composer.
   - An inconclusive capture (empty or failed read) never triggers it, which matches the
     watchdog's rule that an inconclusive capture is never a reason to re-send.
   - The log line uses an existing `watchlog.KINDS` value. No vocabulary change.
3. **Tests.**
   - `tests/test_iterm.py` and `tests/test_terminal.py`: the script uses the named gap, and
     the CR comes after the delay.
   - `tests/test_watcher*.py`: the check sends one CR when the text is still in the composer
     and the agent is idle. It sends nothing when the agent is busy, when the text is gone, or
     when the capture is empty. It never sends twice.

## Verification
- The gate's full-suite run on submit (the run on the record).
- **Live check (iTerm2 + codex-cli 0.157.0).** A scratch tab running `codex`, driven through
  `iterm.write_text_to_session`, with a trivial prompt.
  - On `main` (50 ms): count how often the message is left unsubmitted, which reproduces the
    defect.
  - On this branch: 20 sends, 20 submitted without help, with how many of those needed the
    extra CR.
  - Both results are reported verbatim, including any failures. If `main` never reproduces it,
    the submission says so and the cause stays a hypothesis.
- Terminal.app is covered by unit tests only, unless the arbiter wants a live run there too.

## Out of scope
- tmux sending.
- Headless mode (it does not type into terminals).
- Changing the message text.
