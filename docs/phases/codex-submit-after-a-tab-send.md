# Phase 74c: Codex submit after a tab send

## Status
- [x] Planning: plan approved round 3
- [x] Implementation
- [x] Implementation Review: approved round 1 (2026-09-25); gate: 2,699 passed, 6 skipped at `91026a9`
- [x] Complete (merged 2026-09-25, PR #65)

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
   finds no busy marker anywhere in the capture AND positively recognizes Codex's composer
   holding exactly the sent command. Recognition supplements the busy check; it never
   replaces it. The CR is logged
   with an existing `watchlog.KINDS` value (no vocabulary change) and sent at most once per
   send. There is no Claude Code recovery in this phase. The function returns False for every
   layout it does not positively recognize.

   **Capture.** The recovery read asks for `RECOVERY_CAPTURE_LINES = 16` lines, not the
   watchdog's `CAPTURE_LINES = 8`. The footer (up to 2 lines), the blank lines above it, and a
   command wrapped over 2–3 lines at 80 columns already fill most of 8 lines. 16 lines leave room
   for the activity area above the composer, where Codex's `• Working (…)` line and any dialog
   appear (`watcher.py` notes the same for the spinner: it sits above the input box).

   **Busy veto (first, over the whole capture).** If any `BUSY_PATTERNS` entry matches anywhere
   in the 16 lines → False. This includes a working line directly above a matching composer
   and footer. The veto can also fire on older history text (for example an earlier answer that
   says "thinking") or on a command that contains a busy word. That is a false negative: no
   recovery CR, which leaves things as they are today. It is accepted, because a missed recovery
   costs a manual Enter, and a wrong CR could submit something the arbiter did not intend.

   **Incomplete capture → False.** The composer's `›` line must not be the first line of the
   capture: at least two lines above it must be present, so the activity area was actually read.
   An empty or failed read, or a capture that starts inside the composer, is inconclusive →
   False.

   Recognition, from the bottom of the tail upward:
   - **Footer.** The last non-empty lines are Codex's status footer: an indented
     `<model> <effort> · <cwd>` line, optionally followed by a right-aligned `⚠ … · f2 to view`
     line. There is no footer → False.
   - **Composer block.** Directly above the footer, skipping only blank lines, is a block whose
     first line starts with `› ` and whose continuation lines are indented two spaces (Codex's
     wrap). No `›` block there → False.
   - **Line-preserving match.** Whitespace is NOT dropped globally. Each displayed line (the
     first after its `› ` prefix, each continuation after exactly two spaces of indent, each
     right-stripped of the terminal's trailing padding) must equal the next piece of the sent
     command character for character, internal whitespace included. The only freedom is at a
     boundary between two displayed lines, i.e. a real wrap point: there the command may have
     either nothing (Codex wrapped mid-token, as in `Code: /` + `tagteam:handoff`) or exactly
     one space (Codex wrapped at a space and dropped it). Both options are tried at each
     boundary; the match must consume the whole command, with nothing left over on either
     side. So `then act` edited to `thenact` inside a line fails, as does any added, removed or
     changed character, other text, or an empty composer showing a placeholder → False.
     The residual ambiguity is exactly one space at a wrap boundary, which the terminal itself
     does not display; nothing else is tolerated.
   - **Nothing in between.** No line between the block and the footer is anything other than
     blank. A working line (`• Working … esc to interrupt`), an approval or other dialog, or
     any unrecognized line → False.
   - **Submitted history.** A submitted `› …` prompt is followed by output, a working line, or
     a fresh composer, never directly by the footer. So the rule above already rejects it; a
     fixture pins that.
   - **Inconclusive capture.** See *Incomplete capture* above. This matches the watchdog's
     rule that an inconclusive capture is never a reason to re-send.
3. **Tests.**
   - `tests/test_iterm.py` and `tests/test_terminal.py`: the script uses the named gap, and the
     CR comes after the delay.
   - `tests/test_watcher*.py`: capture fixtures under `tests/fixtures/codex_tails/`, taken from
     a live codex-cli 0.157.0 pane (not hand-written), for each of these cases:
     - **positive:** a wrapped stuck command, the real prompt at 80 columns;
     - **negatives:** submitted history plus working; **a working line ABOVE a composer and
       footer that otherwise match exactly** (the busy veto); submitted history plus an answer
       and an empty composer with a placeholder; an approval dialog; the composer holding
       edited or unrelated text; **a whitespace edit inside one displayed line** (`then act` →
       `thenact`), next to the wrapped positive it is derived from; a capture that starts
       inside the composer (incomplete); a Claude Code pane; an empty capture.

     The live captures come from a real pane. Where a negative needs a specific edit (the
     whitespace edit), it is typed into a live composer and captured, not edited in the file.
     Pure unit tests of the matcher also cover the boundary rule directly: a wrap with the
     space dropped, a wrap mid-token, and an extra or missing space inside a line.

     `codex_stuck_composer` is True only for the positive. `send_tab_command` calls `submit`
     exactly once for the positive, and never for any negative or when `submit` fails.

   If a live capture shows the layout cannot be recognized reliably (for example, the footer
   or `›` rendering varies between runs, or the wrap indentation is not a fixed two spaces so
   the line-preserving match cannot tell a wrap from an edit), the recovery CR is dropped from
   this phase. The
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

## Implementation (impl round 1)
- `tagteam/tabs.py`: `SUBMIT_DELAY_S = 0.5`, the one named gap. `iterm.write_text_to_session`
  (`delay {SUBMIT_DELAY_S}`) and `terminal.write_text_to_session` both use it; the old
  `terminal._SUBMIT_DELAY_S` and the iTerm2 literal `0.05` are gone.
- `iterm.submit(session_id)` / `terminal.submit(session_id)`: one lone CR / one empty `do script`.
- `tagteam/watcher.py`: `RECOVERY_CAPTURE_LINES = 16`, `RECOVERY_WAIT_S = 1.5`,
  `codex_stuck_composer(tail, command)`, `_matches_displayed_lines(pieces, command)` and
  `_recover_stuck_codex()`, called once by `send_tab_command` after a successful write. The
  recovery CR is logged as `sent`; a failed one as `send-failed` (both existing `watchlog.KINDS`).
  Any exception in the recovery is swallowed: the send's result is unchanged.
- Two layout facts from the live captures, both handled fail-closed:
  - iTerm2's contents sometimes show `›` + TWO spaces before typed text (a composer holding
    text typed with no CR), one space otherwise. The first-line prefix accepts one or two spaces
    before a non-space. A command that starts or ends with whitespace, or contains a newline,
    is never matched.
  - While Codex works, its footer's hint line becomes `tab to queue message`, which is not a
    recognized hint, so a busy pane is rejected by the footer rule as well as by the busy veto.
    The test for the veto drops that line from the live capture (in the test, not the file) and
    shows the veto alone rejects it, and that without the working line it would match.

## Live results (2026-09-25, scratch iTerm2 window, 80×30, codex-cli 0.157.0, `/tmp/cx74c-live`)
Direct-driver timing experiment (cause). Counted as stuck when the message was still in the
composer 1.5 s after the send:

| text→CR gap | sends | submitted | stuck |
|---|---|---|---|
| 0 ms | 6 | 0 | 6 |
| 20 ms | 5 | 0 | 5 |
| 30 ms | 5 | 5 | 0 |
| 40 ms | 5 | 4 | 1 |
| 50 ms (`main`), short message, `iterm.write_text_to_session` | 10 | 10 | 0 |
| 50 ms (`main`), long wrapped message, `iterm.write_text_to_session` | 10 | 10 † | 0 † |
| 50 ms, long message, all 8 CPUs busy (`yes`) | 10 | 10 | 0 |
| 500 ms (branch), long message, `iterm.write_text_to_session` | 10 | 10 | 0 |

† This run's stuck check only looked at single lines, so it could not see a wrapped stuck
message. The pane showed an answer for each send, but this row rests on that reading, not on
the script's count.

So the mechanism is confirmed: an Enter within about 20 ms after the text is taken as a newline,
and 40 ms failed once in 5. The failure seen on Liminal with a 50 ms gap was NOT reproduced here
(0 stuck in the 20 sends at 50 ms that the script could count, plus the 10 † above). The cause stays a hypothesis for that case: scheduling
jitter of about 10–30 ms is enough to push a 50 ms gap into the window, but it wasn't observed.

End-to-end check through `watcher.send_iterm_command` (the fix), short prompts `reply with only:
ok-<n>`, branch code: **20 sends, 20 submitted by the first CR, 0 by the recovery CR, 0 not
submitted, 20 of 20 handled exactly once** (one `›` history entry, one `• ok-<n>` answer, empty
composer afterwards). The recovery did not fire, because the 0.5 s gap submitted every send.

Forced recovery exercise (separate from the 20; `iterm.SUBMIT_DELAY_S` set to 0 in the test
process only, the long wrapped message): **5 sends, 0 by the first CR, 5 by the recovery CR,
5 of 5 handled exactly once**.

Codex usage: about 80 tiny prompts. That is more than the ~30–40 estimated, because of the
threshold search.

## Out of scope
- tmux sending.
- Headless mode (it does not type into terminals).
- Changing the message text.
