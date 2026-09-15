# Phase 58: Cockpit Session Lifecycle

## Status
- [ ] Planning
- [ ] Approved
- [ ] Implementation: phase/cockpit-session-lifecycle
- [ ] Implementation Review
- [ ] Complete

## Summary
Two arbiter reports from 2026-09-15 (roadmap backlog, now scheduled here):

1. **A stopped cockpit leaves its watcher running, and a second watcher can
   start beside it.** The arbiter stopped `tagteam serve` with Ctrl+C, then
   started a CLI/iTerm2 session. The headless watcher the cockpit had launched
   was still alive, so two watchers raced for one project. During Phase 52 impl
   round 4 that produced a forced `GATE_PASS` with no test result and the round
   had to be resubmitted.
2. **The reviewer lane shows the previous cycle's work as if it were current.**

Arbiter rulings (2026-09-15, before this plan):
- A second watcher for the same project is **refused**, not warned about.
- Stopping the cockpit means **Ctrl+C (or SIGTERM) on `tagteam serve`**. That
  stops the watchers this cockpit started; watchers started elsewhere are left
  alone.
- The reviewer lane is **blank by default for a new cycle**, with a
  **"Show last session"** button to see earlier rows. "Session" here means
  the cycle: a new phase or type is a new session.

### Verified facts this plan rests on
- `launch.start_watcher` already refuses when `cockpit_api.watcher_status`
  sees a watcher, but only the cockpit calls it. A `tagteam watch` started from
  a terminal or an iTerm2 session checks nothing, so the refusal must live in
  `watcher.watch` itself.
- `watcher_status` finds a watcher from the identity-checked pidfile, then a
  process scan (argv names the project, or cwd is the project), then the
  in-flight marker. Watchers without `--pidfile` are only visible to the scan.
- The watcher has **no SIGTERM handler**. `kill <pid>` (which is what
  `launch.stop_watcher` and the cockpit's watcher Stop send) skips the
  `finally` in `watch()`: the pidfile stays behind. `headless.run_process`
  already kills its turn's process tree on `KeyboardInterrupt`, and `tagteam
  serve` already maps SIGTERM to `KeyboardInterrupt`
  (`_install_sigterm_as_interrupt`).
- The server does not track the watchers it launched; they are spawned
  detached (`start_new_session=True`) from `/api/watch/start` and from a Start
  launch (`launch._attempt`, which records `watcher_pid`/`watcher_ident` on
  the `launches` row), and outlive `serve`.
- The cockpit has no session concept. The reviewer lane (`cockpit.js`
  `RLANE`) is fed by `/api/activity`, the newest 50 rows project-wide, and
  never drops finished rows. Its verdict chips look up `ROUNDS_BY_N[round]`,
  built from the *current* cycle only, so an old cycle's round-1 row can show
  the current cycle's round-1 verdict (latent bug). Activity items already
  carry `phase`, `type` and `round`.

## Scope
In:
1. **One watcher per project** (`watcher.py`): an exclusive, non-blocking
   lock on `.tagteam/watcher.lock` held for the watcher's lifetime, in every
   mode, whether or not `--pidfile` is set; plus a refusal when
   `watcher_status` identifies another live watcher (covers watchers from
   releases without the lock). Refusal exits non-zero with one message.
2. **Graceful SIGTERM for the watcher** (`watcher.py`): SIGTERM becomes
   `KeyboardInterrupt` in the main thread, so the existing `finally` runs
   (pidfile removed, lock released) and an in-flight headless turn is killed
   by `run_process`'s existing interrupt path.
3. **`tagteam serve` stops the watchers it started** (`server.py`,
   `launch.py`): a per-server owner records each watcher child at `Popen`
   (both `/api/watch/start` and Start launches, independent of readiness);
   shutdown refuses new starts, waits for starts in progress, then terminates,
   reaps and reports each owned child.
4. **Reviewer lane per cycle** (`cockpit.js`, `cockpit.html`, `cockpit.css`,
   `cockpit_api.py`): rows from other cycles are hidden, an empty state names
   the current cycle, a "Show last session" toggle reveals the earlier rows
   (labelled with their cycle), and verdict chips match on phase + type +
   round.
5. `launch.start_watcher`: when the spawned watcher exits because of the new
   refusal, report "a watcher is already running …" rather than "rejected its
   configuration".
6. Docs: one paragraph in `docs/workflows.md` / `tagteam/data/workflows.md`
   (one watcher per project; stopping `serve` stops the watchers it started).
   Roadmap: the two backlog entries point here.
7. Tests (below).

Out: a "stop session" button in the cockpit; stopping a watcher when the
browser tab closes; stopping watchers `serve` did not start; recovering from
`kill -9` of `serve` (the lock then makes the leftover watcher's pid visible
in the next refusal); the lead lane's cycle-turn cards (same staleness; noted
as a backlog item); multi-host locking; the iTerm2 `cd` issue.

## Technical Approach

### 1. Watcher lock and refusal
New in `watcher.py`:

```
acquire_watcher_lock(project_root) -> WatcherLock | None   # non-blocking
WatcherLock.release()
```

- POSIX: `os.open(.tagteam/watcher.lock, O_RDWR|O_CREAT)` + `fcntl.flock(fd,
  LOCK_EX | LOCK_NB)`; `BlockingIOError` → None. Windows: `msvcrt.locking`
  `LK_NBLCK` on the same far offset `dualwrite` uses. The fd is
  non-inheritable (Python default), so an agent turn spawned by the watcher
  never keeps the lock after the watcher dies; the OS releases it if the
  watcher crashes.
- The holder writes `{pid, ident, mode, started_at}` into the lock file after
  acquiring (for the refusal message only; the lock, not the content, is the
  authority).

In `watch()`, **before** `_build_processor` (so a refused watcher validates,
spawns and writes nothing):
1. `watcher_status(root)` with its `pid != os.getpid()`: if it reports a live
   watcher from `pidfile` or `process-scan`, refuse.
2. `acquire_watcher_lock(root)`; `None` → refuse, using the lock file's
   recorded pid/mode when readable.
3. Hold the lock until the `finally` that already removes the pidfile.

Refusal (stderr, exit 1 from `watch_command`, like other startup failures):

```
[tagteam] refused: another watcher is already running for this project (pid 4242, headless, started 14:02).
  Stop it first: kill 4242   (or the cockpit's watcher Stop)
```

When no pid can be named: `… another watcher holds .tagteam/watcher.lock`.

### 2. SIGTERM → KeyboardInterrupt in the watcher
`watch_command` installs the same handler `serve` uses (main thread only,
never raises). Effects, all through existing code:
- `_run_poll_loop` / the event loop exit on `KeyboardInterrupt` ("Watcher
  stopped.");
- `watch()`'s `finally` removes the pidfile and now releases the lock;
- a headless turn in `run_process` is killed as on Ctrl+C and recorded the
  way an interrupted turn already is. (The impl submission states what the
  record looks like, verified by test, not assumed.)

### 3. `serve` stops the watchers it started
*(Revised in plan round 2: ownership is recorded at spawn, not on success,
and shutdown is synchronized with starts that are still in progress.)*

**Ownership object** (`launch.py`, `WatcherOwner`), one per `serve` process,
guarded by one `threading.Lock` + `Condition`:

```
owner.begin_spawn()          -> True | False   # False once closing: the start is refused
owner.spawned(proc, source)  # right after a successful Popen, before readiness is known
owner.end_spawn()            # in a finally, always
owner.close(wait_s)          -> list[report lines]
```

- `start_watcher(root, …, owner=None)`: when `owner` is given it calls
  `begin_spawn()` before anything else (refusing with
  `the cockpit is shutting down — not starting a watcher` when it returns
  False), `spawned(proc, source)` immediately after `Popen` returns, and
  `end_spawn()` in a `finally`. Readiness is no longer what registers a
  watcher: a start that times out (`started_unverified`) or whose child exits
  early is still recorded, and `close` handles each honestly.
- Both entry points pass the server's owner: `/api/watch/start`
  (`source="watch-start"`) and a Start launch, via
  `launch.launch(…, watcher_owner=owner)` → `_attempt` → `start_watcher`
  (`source="launch"`). A **reused** watcher (the launch row's recorded live
  pid) and an **already running** one (`start_watcher` refused before
  spawning) are never recorded, so they are never stopped.
- The owner keeps the `Popen` handle. Because `serve` is the parent and has
  not reaped the child, the pid cannot be reused while `proc.poll() is None`,
  so signalling through the handle is safe; the recorded
  `procs.identity(pid)` is kept for the report.

**Shutdown protocol** (`serve_command`'s `finally`, before `lease.release()`;
`daemon_threads=True` means request threads may still be mid-start):
1. Under the lock: `closing = True`. From here `begin_spawn()` returns False.
2. Wait on the condition until no start is between `begin_spawn` and
   `end_spawn`, bounded by `WATCHER_READY_WAIT_S + 5 s`.
3. Under the lock: take the snapshot and set `closed = True`. If a start
   overran the bound and calls `spawned()` after this, that call terminates
   and reaps the new child immediately and records a report line (the child
   never escapes).
4. For each recorded child, outside the lock:
   - `proc.poll()` not None → `Watcher pid N (started by this cockpit) had already exited (code C).`
   - else `proc.terminate()` (SIGTERM; the watcher's §2 handler runs its
     `finally`), `proc.wait(10)` →
     `Stopped watcher pid N (started by this cockpit).`
   - timeout → `Watcher pid N did not exit within 10 s — stop it with: kill N`
     (no SIGKILL: an in-flight turn's cleanup may still be running).
   - pidfile still naming a gone pid → `remove_pidfile(root, pid)`.
5. If step 2 hit its bound: `A watcher start was still in progress at shutdown; see the lines above.`
6. `watcher_status(root)` still reporting a live watcher that is not in the
   snapshot → `Watcher pid N was not started by this cockpit — left running.`

Only a clean shutdown (Ctrl+C / SIGTERM) runs this. `kill -9` of `serve`
cannot; §1 then makes the leftover visible and refusable.

### 4. Reviewer lane per cycle
**API** (`cockpit_api._activity_from_db`): usage-backed items prefer the v10
`target_phase` / `target_type` / `target_round` when present (the cycle entry
the turn was dispatched to produce), falling back to the stored
`phase`/`type`/`round`; column-tolerant for older DBs. Gate and panel items
already carry their own cycle. No new endpoint.

**UI** (`cockpit.js`). New code goes in the Phase 43 Cycle region + Activity
log section, so the existing node harness can test it.
*(Revised in plan round 2: the verdict cache is bound to the cycle of its
response, and the key format matches `CYCLE_ID`.)*
- `cycleKey(it)` = `it.phase + '_' + it.type`, the same composition as
  `CYCLE_ID` (`phase + '_' + type`). `type` is always `plan` or `impl`, so two
  different cycles cannot produce the same key.
- **Verdict cache bound to a cycle.** `ROUNDS_BY_N` becomes
  `ROUNDS = {cycle, byN}`, managed by two functions in the harness slice:
  - `setVerdictCycle(cycle)`: when `cycle !== ROUNDS.cycle`, replaces the cache
    with `{cycle, byN: {}}` and calls `refreshVerdicts()`, so chips clear the
    moment the cycle changes. Called wherever `CYCLE_ID` is (re)derived from
    state.
  - `applyRounds(requestedCycle, rounds)`: ignores the response unless
    `requestedCycle === CYCLE_ID && requestedCycle === ROUNDS.cycle`;
    otherwise builds `byN` and refreshes.
  - `loadFeed` captures `var requested = CYCLE_ID` before its fetch and calls
    `applyRounds(requested, rounds)`, so a slow response for A that lands after
    switching to B is dropped, and B rows show no chip until B's response
    arrives.
  - `verdictFor(it)` returns a chip only when
    `cycleKey(it) === CYCLE_ID && ROUNDS.cycle === CYCLE_ID`.
- `RLANE` rows whose `cycleKey` differs from `CYCLE_ID` get a `.other-cycle`
  class and are hidden unless `SHOW_LAST_SESSION` is true. Rows are still kept
  in the store, so revealing them needs no refetch.
- Empty state (`#reviewer-empty`): `No reviews yet for <phase> · <type>.`
  when no current-cycle rows are visible.
- Button `#btn-reviewer-last` in the reviewer lane header: label
  **Show last session** / **Hide last session**, `hidden` when there are no
  other-cycle rows. Revealed rows are dimmed and show a small cycle label
  (`<phase> · <type>`). The toggle resets to hidden when the cycle changes.
  Not persisted.
- Lane status text (`renderLanes`) is unchanged: it already describes the
  current state.

## Files
- `tagteam/watcher.py`: lock, refusal, SIGTERM handler
- `tagteam/launch.py`: `WatcherOwner`; `start_watcher(owner=)` and `launch(watcher_owner=)` → `_attempt`; refusal wording in `start_watcher`
- `tagteam/server.py`: one `WatcherOwner` per server, passed to `/api/watch/start` and the launch path; `owner.close()` on shutdown with its report lines printed
- `tagteam/cockpit_api.py`: target identity on activity items
- `tagteam/data/web/cockpit.js`, `cockpit.html`, `cockpit.css`: per-cycle reviewer lane, toggle, verdict keying
- `docs/workflows.md`, `tagteam/data/workflows.md`, `docs/roadmap.md`
- Tests: `tests/test_watcher_lock.py` (new), `tests/test_launchpad.py`, `tests/test_server_cockpit.py`, `tests/test_cockpit_activity.py`

## Success Criteria
1. With a watcher holding the lock (a real `tagteam watch --mode notify` child
   process in a temp project), a second `watch()` for that project returns
   without building a processor, prints the refusal naming the first pid, and
   `watch_command` exits 1. A watcher for a **different** project starts.
2. A live watcher found only by `watcher_status`'s process scan or pidfile
   (no lock held; simulates an older release) also causes a refusal.
3. After the lock holder exits, including by `kill -9`, a new watcher
   acquires the lock and starts.
4. SIGTERM to a running `tagteam watch --pidfile` child: it exits, the
   pidfile is gone, and the lock is free. With a fake in-flight headless turn,
   the turn's process is killed and recorded as the existing interrupt path
   records it.
5. `serve` shutdown (`WatcherOwner` with real child processes):
   - a watcher started through `/api/watch/start`, and one started through a
     Start launch, are each terminated, reaped and reported stopped;
   - a start whose readiness wait **times out** (`started_unverified`) is still
     owned and stopped at shutdown, for both entry points;
   - **shutdown during an in-flight start**, for both entry points: `close`
     waits for the start, which then records its child, and that child is
     stopped; a start that calls `begin_spawn` after `closing` is refused
     without spawning; a `spawned()` arriving after `closed` terminates its
     child immediately;
   - a child that already exited is reported as exited, not signalled;
   - a child that ignores SIGTERM is reported with the `kill N` line after the
     bounded wait;
   - a reused watcher (launch row's live pid) and an already-running external
     watcher are never recorded, never signalled, and the external one is
     reported as left running.
6. `start_watcher` against a project whose watcher refuses reports
   "a watcher is already running" with the pid, not "rejected its
   configuration".
7. Reviewer lane (node harness): with rows from cycles A and B and
   `CYCLE_ID = B`, only B's rows are visible; the empty state names B
   when B has none; "Show last session" reveals A's rows with their cycle label
   and flips to "Hide last session"; changing the cycle hides them again;
   an A round-1 row never shows B's round-1 verdict. The button is hidden when
   there are no other-cycle rows.
7a. Verdict cache ordering (node harness, `setVerdictCycle` / `applyRounds`):
   with A's rounds applied and the cycle then switched to B, B's round-1 row
   shows **no** chip before B's response; a **late A response** applied after
   the switch is ignored (B still has no chip, A's cache is gone); applying B's
   response shows B's verdict; responses applied out of order (B, then a stale A)
   leave B's verdicts in place.
8. Activity items for usage rows with v10 target identity carry the target
   cycle; older rows and older schemas fall back to the stored columns.
9. Full suite passes (on the record via the on-submit gate).
