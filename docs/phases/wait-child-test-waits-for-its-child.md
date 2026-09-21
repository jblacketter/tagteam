# Phase 68c: Wait-child test waits for its child

## Status
- [x] Planning: none — a fix made first; impl cycle opened directly (arbiter's rule for small already-made fixes)
- [x] Implementation: branch `phase/wait-child-test-waits-for-its-child`
- [x] Implementation Review: approved round 1 (2026-09-21) at `638558e`; gate 2,275 passed, 5 skipped (scope not enforced — no plan boundary; the reviewer read the whole commit)
- [x] Complete: PR #54 merged 2026-09-21 (rebase).

Closeout: at the reviewer's note, the test's comment now calls the starved-child race the *demonstrated mechanism and likely but unproven cause*, as this document does — a comment-only edit after approval.

## Summary
Issue 11 in `docs/tagteam-issues-release-and-venv-upgrade-2026-09-20.md`. On
2026-09-21 the 3.14.7 release-tree suite failed once — 1 failed / 2,274 passed,
`tests/test_watcher_lock.py::test_wait_child_terminates_and_reports_a_child_still_running`
— with the 1-minute load average near 48, and passed (2,275) on an immediate
rerun of the same tree. The release went out on the rerun. The failure message
was not captured (the run had been tailed to two lines).

The test is one of three Phase 65 tests of the helpers `_wait_child` /
`_child_output` in that file. It exercises no product code.

## What was established, and how
- **Mechanism, demonstrated:** the test spawns `python -c "print('started,
  waiting', flush=True); sleep"`, gives it `timeout=0.5`, lets `_wait_child`
  terminate it, and asserts the text is in the dump. A child that has not
  printed when SIGTERM arrives leaves an empty dump. Replaying the test's steps
  outside pytest: 0.5 s budget on a calm machine → text captured 15/15; a
  5 ms budget (a starved child) → empty output 15/15.
- **Ruled out:** `os.killpg` racing the child's `setsid` — 0 failures in 30
  replays; `Popen` returns after the exec, by which time the session exists.
- **Not established:** that this is what happened in the release run. There is
  no message to compare with. It is the one mechanism found that fits, and it
  is a real race whether or not it was the one that fired.
- **Not explained:** two of five solo runs of the old test took ~5 s instead
  of ~0.6 s. The slow step is not in the test's own flow (no slow step in 30
  timed replays).

## Scope
**In** — `tests/test_watcher_lock.py`, one test: the child creates a marker
file in `tmp_path` **after** its flushed print; the test waits for the marker
(`_wait(ready.exists, timeout=60)`) and only then starts the 0.5 s clock.
Assertions unchanged.

A first attempt read the child's first line from the pipe, as the sibling test
`…kills_a_child_that_ignores_sigterm` does. It failed deterministically
(`output: <none>`): the buffered reader swallows the rest of the pipe, which is
exactly the text `_child_output` must find through `communicate()`. The sibling
gets away with it because it asserts nothing about the output text. Hence a
signal that does not touch the pipe.

**Out** — `_wait_child` / `_child_output` themselves; the other two helper
tests (one waits for the child's exit first, the other reads its line first
and asserts no output text — neither has this race); any product code.

## Verification
- The three helper tests: 4 passed (incl. a parametrized sibling), six
  consecutive runs.
- Against a **slow-starting child** (`sleep(2)` before its print — a starved
  interpreter, made deterministic): old flow → text captured 0/3; new flow →
  3/3.
- Full suite: the gate's run.

## Files
- Modified: `tests/test_watcher_lock.py`, `docs/roadmap.md`,
  `docs/tagteam-issues-release-and-venv-upgrade-2026-09-20.md`
