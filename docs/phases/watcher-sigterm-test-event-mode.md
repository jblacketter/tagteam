# Phase 58a: Watcher SIGTERM test in event mode

## Status
- [x] Planning (fix already implemented; impl cycle only)
- [x] Implementation: phase/cockpit-session-lifecycle (PR #42)
- [ ] Implementation Review
- [ ] Complete

## Summary
PR #42's CI (`pytest (ubuntu-latest)`, run 34984321449) failed one test:
`tests/test_watcher_lock.py::TestSigterm::test_sigterm_removes_pidfile_and_frees_lock`
asserted the log line `Watcher stopped.`. CI installs `.[event]`, so the watcher
runs the watchdog event loop, whose `watch_with_events` swallows
`KeyboardInterrupt` and returns without that line. The poll loop logs it. The
project venv has no `watchdog`, so the on-submit gate only exercised poll mode.

Not a product defect: in both modes SIGTERM gives exit 0, the pidfile is
removed and the lock is released (reproduced in a scratch venv with
`watchdog` installed: 1 failed before the fix).

## Scope
In: drop the log-line assertion; keep exit code, pidfile and lock assertions.
Out: changing the event loop's logging (pre-existing behaviour, unrelated to
Phase 58); installing `watchdog` in the project venv.

## Files
- `tests/test_watcher_lock.py`

## Success Criteria
1. `tests/test_watcher_lock.py` passes with and without `watchdog` installed.
2. Full suite passes (gate). PR #42's CI passes after push.
