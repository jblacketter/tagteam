# Phase 79: A stop signal during watcher startup exits cleanly

## Status
- [ ] Planning
- [ ] Implementation
- [ ] Implementation Review
- [ ] Complete

## Summary
`tagteam watch` turns SIGTERM into `KeyboardInterrupt` (`_install_sigterm_as_interrupt`, called
in `watch_command` before `watch()`), but only the two loops catch it (`_run_poll_loop`,
`_run_event_loop`). Everything the watcher does between installing the handler and entering a
loop is unprotected. A stop signal that lands there kills the process with an uncaught
`KeyboardInterrupt`: exit by SIGINT (`-2` to a parent), a traceback on stderr, and no `stop`
event in the watcher log. The `finally` blocks still run, so the pidfile and the lock are
released.

The widest part of that window is after `write_pidfile()` (`tagteam/watcher.py:1936`) and before
the loop: importing `watcher_events` and probing watchdog. That matters because the pidfile is
what the cockpit, `serve` shutdown and the test wait for before they send SIGTERM. Whoever stops
a watcher the moment it appears is aiming at the window.

Evidence:
- CI, PR #68, 2026-11-16, two attempts in a row:
  `tests/test_watcher_lock.py::TestSigterm::test_sigterm_removes_pidfile_and_frees_lock` failed
  with `assert -2 == 0` (2,732 other tests passed each time). PR #68 changes lens text only.
- Reproduced on demand: with a 1 s sleep injected into `watcher_events.is_available()` (a
  `sitecustomize` shim, scratch venv with `.[event]`), the same test fails with exactly
  `-2 == 0`.
- Without the delay: 25 of 25 runs passed on the arbiter's Mac in the same venv. The window is
  real and narrow; a loaded CI runner hits it, this machine does not by chance.

## Scope

### 1. One clean stop for the whole run
`_watch_sinked()` catches `KeyboardInterrupt` around its whole body (building the processor, the
banner, the pidfile write, the trigger probe, and the loop calls), logs the same
`Watcher stopped.` line with `kind="stop"`, and returns `True`. The catch sits inside
`_watch_locked`'s `try`, so the sink is still open and the `stop` event is recorded.

The loops keep their own handlers unchanged. An interrupt inside a loop is consumed there, so the
outer catch never sees it and the log still has exactly one `stop`.

### 2. The pidfile write is inside its own cleanup (reviewer, round 1)
Today `write_pidfile()` is called *before* the `try/finally` that removes the pidfile. With only
the outer catch added, a stop during the write, or between its return and the `try`, would exit
0 and leave `watcher.json` behind. So:

- The write moves inside the cleanup-protected region: `try: write … loops … finally: remove`.
- `write_pidfile()` is not atomic (`Path.write_text`), so an interrupt inside it can leave a
  truncated file. `remove_pidfile()` only unlinks a file whose `pid` is ours, and a truncated
  file parses to nothing, so it would survive. The cleanup therefore also unlinks a pidfile that
  exists but cannot be parsed. That is safe here and only here: this process holds the exclusive
  watcher lock for the project, so no other watcher can own the file. A readable pidfile naming
  another pid is still left alone, as today.
- This is one stop request cleaned up fully. A second signal during the cleanup stays out of
  scope.

### 3. Before the sink exists
A signal between installing the handler and `_watch_sinked` (the other-watcher check, taking the
lock, opening the sink) has no log to write to. `watch_command` wraps both
`_install_sigterm_as_interrupt()` and the `watch(...)` call in one `try`, catches
`KeyboardInterrupt`, and returns 0 with no traceback (reviewer's boundary note: the stop
immediately after the handler is installed is covered too). `watch()`'s `finally` has already
released the lock if it was taken.

### 4. Tests that are synchronized, not timed (reviewer, round 1)
A fixed sleep is not a handshake, and a delay in `watcher_events.is_available` is bypassed by
`--poll`. The subprocess tests use a test-only `sitecustomize.py` written into the test's temp
directory and put on `PYTHONPATH`. Driven by environment variables, it wraps one named startup
function; the wrapper calls the real function, then creates a **ready marker file**, then blocks
(bounded, 30 s) so the process is provably parked inside the window. The test waits for the
marker (bounded), sends SIGTERM, and waits for exit (bounded); the child is reaped in `finally`
with the file's existing `_reap`.

Windows held open this way:

| Hold point | Trigger | What it proves |
|---|---|---|
| after the real `write_pidfile`, before it returns to `_watch_sinked` | `--poll` | shared startup point, reached by every trigger; the reviewer's "after the write, before the wrapper returns" regression |
| same | event (watchdog, when installed) | the same point on the CI trigger |
| after the real `watcher_events.is_available` | event | the window CI actually hit (pidfile written, probe in progress) |

Each asserts: exit code 0, no `Traceback` in the output, pidfile removed, lock free, exactly one
`stop` event and it is the last, no heartbeat left. The event-trigger rows skip when watchdog is
not importable (the project `.venv` has none; CI installs `.[event]`).

Two in-process tests cover what a subprocess cannot hold reliably:
- **Truncated pidfile:** `write_pidfile` patched to write half a JSON document and raise
  `KeyboardInterrupt`; `watch()` returns `True`, the pidfile is gone, the lock is free, one `stop`.
- **Pre-sink:** `watch` patched to raise `KeyboardInterrupt`; `watch_command` returns 0; and the
  same with `_install_sigterm_as_interrupt` patched to raise.

Pre-fix evidence: the subprocess tests are run against the unfixed `watcher.py` first and the
failures (expected `-2`) are quoted in the impl submission.

## Out of scope
- **A second signal during cleanup.** One stop request is honoured cleanly; a second one arriving
  inside a `finally` is not handled.
- **The other red run on `main`** (2026-11-16, attempt 1):
  `tests/test_launchpad.py::TestServerEndpoints::test_watch_session_and_launch_endpoints` failed
  at `assert conv["slot"]["held"]`, and passed on re-run. It also failed once on 2026-09-25.
  Different code (the cockpit's launch composite and the turn slot), different cause, and Phase
  74a already narrowed it once. Not investigated here; recorded as a backlog entry.
- Changing what SIGTERM does inside a loop, the headless in-flight turn kill, or the pidfile and
  lock formats.
- Windows (no SIGTERM handler semantics to change; the handler install already tolerates it).

## Technical Approach
- `tagteam/watcher.py`, `_watch_sinked`:

      try:
          processor = _build_processor(…)          # was unprotected
          …banner…
          keep_pidfile = pidfile_enabled(…)
          try:
              if keep_pidfile: write_pidfile(…)     # now inside the cleanup
              …trigger probe + loops (unchanged)…
          finally:
              if keep_pidfile: _discard_own_pidfile(pidfile_root)
      except KeyboardInterrupt:
          _log("Watcher stopped.", kind="stop"); return True

  `_discard_own_pidfile` = `remove_pidfile()` plus the unparseable-file unlink described in
  Scope 2. On the outer-catch path the pidfile is removed before the stop line is written; on
  the loop path the stop line comes first, as today. The tests assert the end state, not the order.
- `watch_command`: one `try/except KeyboardInterrupt: return 0` around the handler install and
  `watch(...)`.
- No new module, no new dependency, no production test seam (reviewer accepted the shim), no
  change to the event vocabulary (`stop` exists).

## Files
- `tagteam/watcher.py`
- `tests/test_watcher_lock.py` (new tests beside `TestSigterm`; the `sitecustomize` shim is
  written into each test's temp directory, not committed as a file)
- `docs/roadmap.md` (Phase 79 entry, backlog entry for the launchpad flake),
  `docs/phases/watcher-stop-during-startup.md`

## Success Criteria
1. With the process parked after the real pidfile write (marker handshake), SIGTERM gives exit 0,
   no traceback, pidfile removed, lock free, one `stop` event last, no heartbeat: on `--poll`,
   and on the event trigger where watchdog is installed.
2. The same with the process parked after the watchdog probe (the window CI hit).
3. Those tests fail on the pre-fix code; the failing output is quoted in the submission.
4. A stop that interrupts the pidfile write itself leaves no pidfile, truncated or whole, and the
   lock is free (in-process test).
5. A readable pidfile naming another pid is not removed by the new cleanup (test).
6. The existing `TestSigterm` tests pass unchanged, including the one-`stop` assertion.
7. A stop before the sink exists, including one raised by the handler install itself, makes
   `watch_command` return 0 with no traceback (in-process tests).
8. Every wait in the new tests is bounded and every child is reaped in `finally`.
9. Full suite green through the gate; CI green on the PR in the `.[event]` environment, where the
   failure was seen.
