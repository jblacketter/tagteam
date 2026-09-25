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

The layout, observed on 2026-09-25 at 13:40 PDT. A detached tmux session (`cx74c`, 80×30,
codex-cli 0.157.0) was running Codex in this worktree, and the same message sat unsubmitted
in its composer. It is not known what sent the text there, so this is evidence of the layout
only, not of the iTerm2 path:

```
› Read the handoff contract (`tagteam contract`; in Claude Code: /
  tagteam:handoff) and handoff-state.json, then act on your turn


  GPT-6-Astra medium · ~/projects/tagteam-74c
                                                       ⚠ 1 warning · f2 to view
```

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
1. **Longer gap (both drivers, every agent).** Raise the text→CR gap in both tab drivers
   (iTerm2's AppleScript `delay` and `terminal._SUBMIT_DELAY_S`) to 0.5 s. That is comfortably
   past the paste window, and still short next to a turn. Both drivers use one named constant
   instead of a literal.
2. **Recovery CR, Codex composer only, fail-closed.** After a successful write,
   `send_tab_command` waits briefly (about 1.5 s) and reads the tab's tail with the driver's
   `get_session_contents`. It sends one lone CR, through a new driver function
   `submit(session_id)`, only when a new pure function, `codex_stuck_composer(tail, command)`,
   positively recognizes Codex's composer holding exactly the sent command. The CR is logged
   with an existing `watchlog.KINDS` value (no vocabulary change) and sent at most once per
   send. There is no Claude Code recovery in this phase. The function returns False for every
   layout it does not positively recognize.

   Recognition, from the bottom of the tail upward:
   - **Footer.** The last non-empty lines are Codex's status footer: an indented
     `<model> <effort> · <cwd>` line, optionally followed by a right-aligned `⚠ … · f2 to view`
     line. There is no footer → False.
   - **Composer block.** Directly above the footer, skipping only blank lines, is a block whose
     first line starts with `› ` and whose continuation lines are indented two spaces (Codex's
     wrap). No `›` block there → False.
   - **Exact match.** The block's text, with the `› ` prefix and the wrap indentation removed
     and all whitespace dropped, equals the sent command with all whitespace dropped. Dropping
     whitespace makes the comparison independent of where the terminal wrapped. Codex wraps
     `Code: /` + `tagteam:handoff` with no space, so joining lines with a space would not
     match. Any difference (edited input, other text, an empty composer showing a placeholder)
     → False.
   - **Nothing in between.** No line between the block and the footer is anything other than
     blank. A working line (`• Working … esc to interrupt`), an approval or other dialog, or
     any unrecognized line → False.
   - **Submitted history.** A submitted `› …` prompt is followed by output, a working line, or
     a fresh composer, never directly by the footer. So the rule above already rejects it; a
     fixture pins that.
   - **Inconclusive capture.** An empty or failed read → False. This matches the watchdog's
     rule that an inconclusive capture is never a reason to re-send.
3. **Tests.**
   - `tests/test_iterm.py` and `tests/test_terminal.py`: the script uses the named gap, and the
     CR comes after the delay.
   - `tests/test_watcher*.py`: capture fixtures under `tests/fixtures/codex_tails/`, taken from
     a live codex-cli 0.157.0 pane (not hand-written), for each of these cases:
     - **positive:** a wrapped stuck command, the real prompt at 80 columns;
     - **negatives:** submitted history plus working; submitted history plus an answer and an
       empty composer with a placeholder; an approval dialog; the composer holding edited or
       unrelated text; a Claude Code pane; an empty capture.

     `codex_stuck_composer` is True only for the positive. `send_tab_command` calls `submit`
     exactly once for the positive, and never for any negative or when `submit` fails.

   If a live capture shows the layout cannot be recognized reliably (for example, the footer
   or `›` rendering varies between runs), the recovery CR is dropped from this phase. The
   delay fix ships alone, and the submission says so, as the review allows.

## Verification
- The gate's full-suite run on submit (the run on the record).
- **Two kinds of live check, reported separately.** Both use a scratch iTerm2 tab running
  codex-cli 0.157.0, with prompts that make each request identifiable: `reply with only:
  ok-<n>`.
  - **Direct-driver timing experiment (cause).** `iterm.write_text_to_session` on `main`'s
    50 ms gap and on the branch's 0.5 s gap. Each send is counted as submitted or left in the
    composer. This confirms or refutes the hypothesis. If `main` never reproduces it, that is
    reported as is, and the cause stays a hypothesis.
  - **End-to-end recovery check (the fix).** 20 sends through `watcher.send_iterm_command`,
    the watcher's own send path, including the recovery. For each send the record is:
    - submitted by the first CR, submitted by the recovery CR, or not submitted;
    - whether the prompt was handled **exactly once**: one `ok-<n>` answer, and no duplicate
      `›` entry in the history.

    Pass = 20 of 20 submitted, and every one handled exactly once.
- Results are reported verbatim, including failures.
- Codex usage: the live checks cost about 30–40 tiny prompts of the arbiter's Codex quota.
- Terminal.app is covered by unit tests only, unless the arbiter wants a live run there too.

## Out of scope
- tmux sending.
- Headless mode (it does not type into terminals).
- Changing the message text.
