# Phase 67: Watcher event log and heartbeat

## Status
- [x] Planning: approved round 2 (2026-09-20) at `dcf4f2e`
- [x] Implementation: branch `phase/watcher-event-log-and-heartbeat`
- [x] Implementation Review: approved round 4 (2026-09-20) at `b0ee363`; gate 2,202 passed, 5 skipped
- [x] Complete: PR #51 merged 2026-09-20 (rebase).
- [ ] **Criterion 10 on this repo — PARTLY DONE.** 2026-09-20 23:15 PDT, after the merge and a watcher restart: `watch status` → `running (pid 35417, mode iterm2)`, `last look: 9s ago`; `watch log` holds the nine start-up lines (`start`, sessions OK, poll trigger, `Current state: done`). No turn has been dispatched since, so the dispatch kinds are still unobserved here. Original note: The iTerm2 watcher running here predates the change. After the merge: restart the watcher tab, run one real handoff round, compare `tagteam watch log` with the tab. The reviewer's approval explicitly does not cover this.

## Closeout
```
Phase report: watcher-event-log-and-heartbeat — plan approved r2 · impl approved r4
  plan   2 rounds · 1 change request · 0 bounces
  impl   4 rounds · 1 change request · 2 bounces · gate 4 runs, 31m 31s
  time   start→approve 53m 56s · implementation before first submit 9m 16s
         lead 8m 28s (4 spans, 2 unknown) · reviewer 4m 40s (4 spans) · gate 31m 32s (4 spans)  (elapsed; includes relay wait)
  usage  no usage rows stored under this phase
  turns  matched 0 of 10 · no token data 0 · unmatched 8 · unknown 2
```
Bounces: r1 two test stand-ins for `watcher._log` could not take `kind=` (real); r2 the issue-10 watcher-lock
flake, whose cause was found and fixed here (the test's lock probe raced the child). Review r3 found two real
defects (a 400-record window in `last_event`; no `stop` on the event loop's normal shutdown path).

## Implementation notes (where the code differs from the plan text below)
- **`in-turn` is wider than planned, on purpose.** The plan exempted only a
  headless *cycle* turn. Reading the code showed the watcher loop also blocks
  inside a gate run (minutes — this repo's suite is ~8), a panel and a brief,
  each of which claims the turn slot with `watcher_pid` = the watcher. The rule
  implemented is the accurate one: **the in-flight record's runner is the
  beat's own watcher** (`watcher_pid` equal, `watcher_ident` not contradicting).
  A lead conversation is run by the server (another pid) and never matches; a
  marker from another or an earlier watcher never matches; a dead runner makes
  the beat `previous` before the question arises. The child `pid` is not
  required to be alive: a gate's marker carries `pid: None` throughout.
- **Effective cadence** (approval note 1): staleness uses
  `max(every_s, 5 s throttle)`, so `--interval 1` is judged against 15 s, not
  3 s; a missing / non-positive `every_s` falls back to 30 s.
- **Leaf `lstat` kept** (approval note 2): `_open_regular` refuses an existing
  non-regular leaf by `lstat` before opening, then `fstat`s the descriptor;
  `O_NOFOLLOW` is additional, not the only guard. The module docstring says
  what this is not: a defence against someone replacing the project's
  directories while the watcher runs.
- **`slot-busy` dropped from the vocabulary.** That line is printed by the
  headless engine through the `log=` callable it is handed; tagging it would
  mean changing the engine's logging signature, which the plan ruled out. It
  arrives as `info`. 18 kinds remain, pinned by a test against the source.
- **`docs/workflows.md` left alone.** It lists watcher *modes*, not commands,
  and it is a managed framework file: a two-line addition would refresh it in
  every registered project on the next upgrade. README, `HELP_TEXT` and
  `CLAUDE.md` carry the two commands.
- `tagteam watch status` truncates the `last dispatch` message to 100
  characters (a participant-mismatch refusal is ~330); `watch log` never does.
- **Impl review r3, two fixes.** `last_event()` searched only the newest 400
  records, so 401 lines of chatter hid a dispatch still on disk: it now walks
  the live file, then `.1`, bounded by the files' byte cap. The event loop
  recorded no `stop` on the path a real Ctrl-C / SIGTERM takes
  (`watch_with_events` swallows the interrupt and returns normally): the
  clean-exit line moved after the call, so both loops print and record
  exactly one `Watcher stopped.`; an event-loop *failure* stays `error` and
  falls back to polling. Event mode previously exited silently (Phase 58a),
  so that line is new on stdout there.
- Criterion 10 was done against a real watcher *process* in a scratch project
  (`tagteam watch --mode notify --poll --interval 2`: `watch status` showed
  `running (pid …)`, `last look: 2s ago`, the `refused` dispatch; after SIGTERM
  the beat was gone, the log ended in `stop  Watcher stopped.` and matched the
  process's stdout line for line). **Not done on this repo:** the iTerm2
  watcher running here was started before this change and holds the old code;
  it records nothing until the arbiter restarts that tab.

## Summary
First phase of the cockpit arc (Phases 67–74, agreed with the arbiter on
2026-09-20). The cockpit is meant to replace the three iTerm2 tabs — lead,
watcher, reviewer. Its watcher chip says `running` / `stopped` and nothing
else. The arbiter, 2026-09-20: "It doesn't show the history, or whose turn it
is, it just shows if it's running."

The history cannot be shown because it is not kept. The watcher narrates
everything it does through one function, `watcher._log()` (116 call sites:
`>> codex's turn`, `Sent to …`, `FAILED: Could not send`, `headless PAUSED`,
`Resumed — dispatching the still-owed turn`, `Watchdog: … re-sending`, gate and
panel outcomes, `Watcher stopped.`). `_log()` prints to stdout. In an iTerm2
tab that is the third tab; started from the cockpit it lands in
`.tagteam/watcher-<mode>.log` as unstructured text that nothing reads (the
legacy `/api/watcher/logs` tails session panes through the session backend,
not this file); when the tab
closes it is gone. Separately, nothing records *when the watcher last looked*:
a live pid with a wedged loop (a hung AppleScript send, a blocked read) is
indistinguishable from a healthy idle watcher.

This phase makes the watcher's narration and liveness durable, structured and
readable — by the CLI now, by the cockpit in Phase 68. **No UI in this phase.**

## Scope
**In**

1. **`tagteam/watchlog.py`** (new; stdlib only, imports nothing from
   `watcher` so `cockpit_api` and the CLI can read without loading it).
   - Event log: `.tagteam/watcher-events.jsonl`, one JSON object per line:
     `{"ts": <UTC ISO-8601>, "pid": int, "mode": str, "kind": str, "msg": str}`
     plus, when the caller knows them, `phase`, `type`, `round`, `turn`, `seq`.
     `msg` is capped at 500 characters.
   - **Guarded writes** (plan review r1, point 1). Every write path uses the
     discipline `safe_read` uses for reads, in one private helper
     `_open_regular(root, rel, flags)`: the parent chain (`.tagteam`) is
     checked with `safe_read._dir_chain` — a symlinked or non-directory parent
     refuses; the leaf is opened `O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC` and
     `fstat`-verified `S_ISREG` before a byte is written (a FIFO, device,
     socket or symlink at the path → refuse, close, no write, no block).
     `.tagteam/` is never created for this purpose.
   - `append(root, record)` — `_open_regular(..., O_WRONLY|O_APPEND|O_CREAT)`
     then one `os.write` of one line. Best-effort: refusal or any `OSError`
     returns `False` and is otherwise silent.
   - Bounded: when the live file exceeds 512 KB, `append` rotates first —
     `os.replace(live, live + ".1")`. `os.replace` renames the directory entry
     and never follows a symlink at either name, so a link planted at `.1` is
     replaced, not written through; the size check uses the `fstat` of the
     verified descriptor, not a path `stat`. At most ~1 MB per project.
   - Heartbeat temp file: `watcher-beat.json.<pid>.tmp` opened
     `O_WRONLY|O_CREAT|O_EXCL|O_NOFOLLOW` (a pre-existing name — file, link or
     FIFO — is unlinked once and retried; failure → no beat), then
     `os.replace` onto `watcher-beat.json`.
   - **The guarantee, stated honestly:** logging is synchronous, bounded and
     best-effort — no open can block, no write goes through a link, every
     failure is swallowed, and each record is one small write to a regular
     file. It is *not* a promise that filesystem I/O can never delay a
     dispatch (a stalled disk delays the watcher's own state reads first).
     Asynchronous logging is not planned.
   - **Serialized within the process** (point 2). In event mode
     `watch_with_events` calls `on_change` from the watchdog observer thread
     *and* from the main startup / heartbeat loop, so two threads can log and
     beat at once. A `Sink` object owns one `threading.Lock` held around
     (size check + rotation + append) and around (throttle check + temp write +
     replace). `O_APPEND` alone does not serialize rotation. The lock covers
     only this new logging state — the processor's control flow is untouched.
   - `read(root, n=50)` → the newest `n` records, oldest first, reading `.1`
     only when the live file has fewer than `n`. Malformed lines are skipped,
     not fatal. Reads go through `safe_read.read_bounded` (Phase 65) so a
     symlinked or FIFO-swapped path cannot hang a reader.
   - Heartbeat: `.tagteam/watcher-beat.json`
     `{"pid", "ident", "mode", "started_at", "ts", "every_s", "seq", "status",
     "turn"}` — `ident` is `procs.identity(pid)`, the same creation identity
     the pidfile records — throttled to once per 5 s. `read_beat(root)` returns
     the record or `None` for a missing, malformed, non-object or non-regular
     file.
   - `beat_view(root, watcher, inflight, now)` → the one reader contract the
     CLI and the API share (point 3):
     `{"state", "age_s", "every_s", "stale_after_s"}` with `state` one of
     - `none` — no readable beat;
     - `previous` — the beat's `pid` is not alive, or its `ident` differs from
       the live process's identity, or (when a watcher is known running) its
       `pid` differs from `watcher["pid"]`. An identity that cannot be read
       (`procs.identity` → `None`) does not by itself make a beat `previous`
       — the same rule `watcher_status()` applies to the pidfile. A beat that survived an unclean
       exit is therefore never shown as a new watcher's last look; `age_s` is
       still given so the UI can say "previous watcher, 3h ago";
     - `fresh` — belongs to the running watcher and `age_s ≤ stale_after_s`,
       where `stale_after_s = 3 × every_s` from the beat itself (so
       `--interval 60` is judged against 180 s, event mode against 90 s;
       a missing / non-positive `every_s` falls back to 30);
     - `in-turn` — older than that, but the loop is legitimately blocked:
       the in-flight record is a **cycle** turn (`kind` absent or `cycle`),
       its `watcher_pid` (and `watcher_ident` when recorded) match the beat's
       watcher, and its runner `pid` is alive. A conversational or briefer
       in-flight, a leftover record whose runner is dead, or one owned by a
       different watcher does **not** exempt;
     - `stale` — older than `stale_after_s` with no such exemption.
2. **`tagteam/watcher.py`**
   - `_log(msg, kind="info", **ctx)` keeps printing exactly what it prints
     today and additionally hands the record to a module-level sink. The sink
     is installed in `_watch_locked()` once the project root is known and the
     lock is held, and removed in its `finally`. With no sink installed (tests,
     library callers) `_log` behaves byte-for-byte as now.
   - Tag the dispatch-relevant call sites with a `kind` from a closed
     vocabulary; every other call stays `info`:
     `start`, `stop`, `turn` (`>> X's turn`), `sent`, `send-failed`,
     `paused`, `resumed`, `slot-busy`, `watchdog`, `gate`, `panel`, `done`,
     `escalated`, `aborted`, `refused`, `stuck` (the no-state-change warning),
     `advance` (full-roadmap auto-advance), `error`.
     `_handle_ready` and `_dispatch` pass `phase` / `round` / `turn` / `seq`
     from the state they already hold.
   - The gate, panel, briefer and headless engine already receive `log=_log`;
     their lines arrive as `info` with no change to those modules.
   - Heartbeat: written at the top of each `_run_poll_loop` iteration and in
     the event loop's `on_change` (which `watch_with_events` already calls on
     every filesystem event **and** every `heartbeat_s` = 30 s — no change to
     `watcher_events.py`). The cadence differs by loop (poll: `--interval`,
     default 10 s; event: 30 s), so the beat carries `every_s` and readers
     judge staleness against it (stale = older than 3 × `every_s`).
     A headless turn blocks the loop inside `engine.run_owed_turn()`, so the
     beat goes quiet for the length of the turn — by design; readers treat a
     stale beat as a problem **only when no turn is in flight**.
   - Beat cleanup is tied to the sink, not the pidfile: the `finally` in
     `_watch_locked()` that removes the sink also removes the beat — but only
     a beat whose `pid` is this process — whether or not `keep_pidfile` is
     true. The event log is never deleted by the watcher: history outlives
     the process.
   - The comments that state the 3.0-arc rule (the pidfile block's "Otherwise
     `tagteam watch` writes nothing new", `WatcherLock`'s "a bare
     `tagteam watch` must write no new file there") are updated to name the
     exception the reviewer approved: since Phase 67 every watcher writes the
     event log and beat into an **existing** `.tagteam/`; the pidfile stays
     opt-in and the lock stays outside the project.
3. **CLI** — `tagteam watch status` and `tagteam watch log [-n N] [--json]`.
   - `status` prints: running / not (pid, mode, started — from the existing
     `cockpit_api.watcher_status`), a `last look:` line rendered from
     `beat_view` (`4s ago` · `never` · `previous watcher, 3h ago` ·
     `2m ago — blocked in claude's turn` · `STALE: 6m ago, expected every 10s,
     no turn in flight`), the newest `turn` / `sent` /
     `send-failed` / `paused` event as `last dispatch: …`, and the existing
     `dispatch:` paused line. Exit 0 always; it is a report, not a check.
   - `log` prints the newest N events (default 30) as
     `HH:MM:SS  kind        msg` in local time, or raw records with `--json`.
   - Both are reads: `READ_ONLY_COMMANDS["watch"]` allows exactly
     `status` and `log`; `tagteam watch` with anything else stays refused
     under `TAGTEAM_READ_ONLY`. `watch_command` dispatches on `args[0]` before
     its option loop, so every existing `tagteam watch --…` invocation is
     untouched.
4. **API** (read-only, cockpit mode; no token, like every other GET)
   - `GET /api/watcher/events?n=50` → `{"events": [...]}` (n clamped 1–500).
   - `now_payload()["watcher"]` gains `beat` (the `beat_view` dict above — the
     derived `state` plus `age_s` / `every_s` / `stale_after_s`, so no
     consumer re-derives staleness) and `last_event` (the newest non-`info`
     record or null). Existing keys unchanged.
5. **Docs** — `CLAUDE.md` (watcher paragraph), `README.md` and `HELP_TEXT`
   (the two subcommands), `tagteam/data/workflows.md` + `docs/workflows.md`
   only if they list watcher commands (checked during implementation).

**Out**
- Any cockpit HTML / JS / CSS — Phase 68.
- A "stalled" verdict. This phase exposes the facts (`beat.state`, in-flight,
  last dispatch); Phase 68 decides the sentence shown to the arbiter.
- Surfacing busy-terminal detection beyond the lines `_log` already emits.
- The per-user OS lock (`~/.tagteam/watchers/<hash>.lock`) in the API.
- `doctor` findings, notifications, the saloon, the legacy
  `/api/watcher/status` and `/api/watcher/logs`.
- Reading or migrating the existing `.tagteam/watcher-*.log` text files.

## Decided in plan review (round 1): every watcher writes the log
`watcher.json` is opt-in (`pidfile_enabled()`) under a 3.0-arc rule — with the
flag off, `tagteam watch` writes nothing new into the project. The reviewer
approved the exception as a scoped product choice, no arbiter ruling needed:
**every watcher records the event log and beat when `.tagteam/` already
exists**; the pidfile opt-in is unchanged. Reasons: the CLI is half the point
(`tagteam watch log` after an iTerm2 tab has closed) and iTerm2 watchers run
without `--pidfile`; `.tagteam/` has been tagteam's runtime directory since
Phase 28 (the DB, `turns/`, `gates/`). tagteam does not write a project's
`.gitignore`: where `.tagteam/` is ignored the files are invisible, where it is
not they sit under the `?? .tagteam/` entry the DB already causes. The
rejected alternative gated both files like the pidfile, which would have
recorded nothing in the arbiter's normal workflow.

## Technical approach
- **Why tap `_log` instead of adding event calls:** the narration already
  exists, is already what the arbiter reads in the iTerm2 tab, and is already
  passed into the gate / panel / briefer / engine. One sink means the log and
  the tab can never disagree, and the diff in `watcher.py` is a signature
  change plus `kind=` on ~25 call sites — no control flow moves.
- **Why a closed `kind` vocabulary rather than parsing prefixes (`>>`, `!!`,
  `FAILED:`):** Phase 68 has to pick "the last dispatch" and colour failures.
  Matching on message text would make every wording change a UI bug.
- **Why a separate beat file, not the pidfile:** `.tagteam/watcher.json` is an
  identity record (`pid` + creation `ident`) that `watcher_status()` and the
  stale-pidfile logic trust; rewriting it every few seconds would put a race
  under that logic. **Why not the event log:** a beat every poll would evict
  the real history from a bounded file.
- **Concurrency:** the Phase 58 lock guarantees one watcher *process* per
  project, not one thread — hence the `Sink` lock above. Readers tolerate a
  torn last line.
- In this repo `.tagteam/*` is git-ignored; the implementation confirms both
  files are outside scope-diff and the gate's fingerprint (a test, not an
  assumption) so a running watcher cannot change a submission's scope.
- Windows: `O_NOFOLLOW` / `O_NONBLOCK` are taken with `getattr(os, …, 0)` as
  `safe_read` does; FIFOs do not exist there and the `fstat` regular-file
  check still applies. The FIFO tests skip on `win32`, as Phase 65's do.

## Files
- New: `tagteam/watchlog.py`, `tests/test_watchlog.py`
- Modified: `tagteam/watcher.py`, `tagteam/cli.py` (read-only table, help), `tagteam/cockpit_api.py`,
  `tagteam/server.py` (one GET), `tests/test_watcher*.py`,
  `tests/test_cockpit_api.py` / `tests/test_server*.py` (whichever hold the
  `now` and GET-route tests), `tests/test_read_only*.py`, `CLAUDE.md`,
  `README.md`, `docs/roadmap.md`

## Success criteria
1. With a sink installed, a scripted `_StateProcessor` run (ready → sent,
   paused → resumed, send failure, done) produces records whose `kind`
   sequence is asserted exactly, each carrying `phase` / `round` / `turn`.
2. With no sink installed, `_log` output is identical to today's
   (existing watcher tests pass unchanged).
3. Writer safety, deterministic (no timing): with a FIFO at the event-log
   path, at the beat path and at the beat temp name, and with a symlink at
   each of those and at `.tagteam` itself, a scripted dispatch still happens,
   the call returns without blocking (the test would hang otherwise — run
   under a hard alarm), and the link's target is byte-identical afterwards.
   Injected `OSError` on open / write / replace is swallowed the same way.
4. Rotation: writing past 512 KB leaves a live file and one `.1`, never more;
   `read(n)` spans both and returns oldest-first; a malformed line is skipped.
5. `read` on a symlink or FIFO at the log path returns no events and does not
   block (deterministic swap tests, as in Phase 65).
6. The poll loop and the event loop both refresh the beat; the beat is
   throttled (two iterations inside 5 s → one write); clean exit removes it
   — with `keep_pidfile` false as well as true — leaves the event log, and
   does not remove a beat owned by another pid.
6a. Concurrent callers: N threads logging through one `Sink` across a
   rotation boundary produce only whole, parseable lines, exactly one `.1`,
   and no lost record beyond the rotated-out file; two threads beating inside
   the throttle window produce one write.
6b. `beat_view` table test: no beat · malformed / non-object beat · beat from
   a dead pid · beat whose pid is alive but whose `ident` differs · beat from
   a pid other than the running watcher · fresh at `--interval 60` (150 s old
   → `fresh`) and stale at the default (40 s old, `every_s` 10 → `stale`) ·
   stale + live matching cycle in-flight → `in-turn` · stale + in-flight with
   a dead runner, with `kind: conversation`, or with another watcher's pid →
   `stale`.
7. `tagteam watch status` and `tagteam watch log` run under
   `TAGTEAM_READ_ONLY=1`; `tagteam watch` and `tagteam watch --mode notify`
   are still refused; every existing `watch` option still parses.
8. `GET /api/watcher/events` returns the records; `/api/now`'s `watcher`
   block has `beat` (the `beat_view` dict) and `last_event` and keeps its
   existing keys.
9. Full suite green via the gate; checkout clean afterwards.
10. Manual, on this repo: run the iTerm2 watcher through one real handoff
    round and confirm `tagteam watch log` matches what the watcher tab showed.
    Reported as manual, with the output, not as a test.
