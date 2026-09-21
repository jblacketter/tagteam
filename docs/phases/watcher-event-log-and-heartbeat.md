# Phase 67: Watcher event log and heartbeat

## Status
- [ ] Planning
- [ ] Implementation: branch `phase/watcher-event-log-and-heartbeat`
- [ ] Implementation Review
- [ ] Complete

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
   - `append(root, record)` — one `os.write` of one line on an `O_APPEND`
     descriptor (whole-line atomicity for records this small); best-effort:
     any `OSError` is swallowed. **The log must never be able to stop or slow
     a dispatch.**
   - Bounded: when the file exceeds 512 KB, `append` rotates it to
     `watcher-events.jsonl.1` (replacing any previous `.1`) before writing.
     At most ~1 MB per project, no configuration.
   - `read(root, n=50)` → the newest `n` records, oldest first, reading `.1`
     only when the live file has fewer than `n`. Malformed lines are skipped,
     not fatal. Reads go through `safe_read.read_bounded` (Phase 65) so a
     symlinked or FIFO-swapped path cannot hang a reader.
   - Heartbeat: `.tagteam/watcher-beat.json`
     `{"pid", "mode", "ts", "every_s", "seq", "status", "turn"}`, written atomically
     (temp file + `os.replace`), throttled to once per 5 s. `read_beat(root)`.
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
   - `remove_pidfile()`'s caller also removes the beat file on clean exit. The
     event log is never deleted by the watcher: history outlives the process.
3. **CLI** — `tagteam watch status` and `tagteam watch log [-n N] [--json]`.
   - `status` prints: running / not (pid, mode, started — from the existing
     `cockpit_api.watcher_status`), `last look: 4s ago` (or `never` /
     `stale: 6m ago, no turn in flight`), the newest `turn` / `sent` /
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
   - `now_payload()["watcher"]` gains `beat_age_s` (float or null) and
     `last_event` (the newest non-`info` record or null). Existing keys
     unchanged.
5. **Docs** — `CLAUDE.md` (watcher paragraph), `README.md` and `HELP_TEXT`
   (the two subcommands), `tagteam/data/workflows.md` + `docs/workflows.md`
   only if they list watcher commands (checked during implementation).

**Out**
- Any cockpit HTML / JS / CSS — Phase 68.
- A "stalled" verdict. This phase exposes the facts (`beat_age_s`, in-flight,
  last dispatch); Phase 68 decides the sentence shown to the arbiter.
- Surfacing busy-terminal detection beyond the lines `_log` already emits.
- The per-user OS lock (`~/.tagteam/watchers/<hash>.lock`) in the API.
- `doctor` findings, notifications, the saloon, the legacy
  `/api/watcher/status` and `/api/watcher/logs`.
- Reading or migrating the existing `.tagteam/watcher-*.log` text files.

## Decision for the reviewer and arbiter: who writes the log
`watcher.json` is opt-in (`pidfile_enabled()`: `--pidfile`, or
`serve.theme: cockpit`) because of a 3.0-arc rule: with the flag off,
`tagteam watch` writes nothing new into the project. The event log and beat
meet the same rule head-on. Options:

- **A (recommended): every watcher writes them, no flag.** The CLI is half
  the point — `tagteam watch log` after an iTerm2 tab has closed — and iTerm2
  watchers are started without `--pidfile`. `.tagteam/` has been tagteam's
  runtime directory in every project since Phase 28 (the DB, `turns/`,
  `gates/`), so this adds two bounded files to a directory tagteam already
  owns; nothing appears at the project's top level. tagteam does not write a
  project's `.gitignore`: where `.tagteam/` is ignored the files are invisible,
  where it is not they sit under the `?? .tagteam/` entry the DB already causes. The 3.0 rule was
  a parity guarantee for that arc's rollout, six minor releases ago.
  If `.tagteam/` does not exist the watcher does not create it for this
  purpose (append is best-effort and simply fails).
- **B: same gate as the pidfile.** Zero behaviour change for a bare
  `tagteam watch`, but the arbiter's normal iTerm2 workflow would record
  nothing and `tagteam watch log` would be empty exactly where it is wanted.

The plan below assumes A. Under B only the sink-installation condition
changes.

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
- **Concurrency:** one watcher per project is already guaranteed by the
  Phase 58 lock, so there is one writer; readers tolerate a torn last line.
- In this repo `.tagteam/*` is git-ignored; the implementation confirms both
  files are outside scope-diff and the gate's fingerprint (a test, not an
  assumption) so a running watcher cannot change a submission's scope.
- Windows: `O_APPEND` and `os.replace` are portable; no new platform branch.

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
3. `append` swallows an unwritable directory and a full disk (`OSError`
   injected) — the processor's dispatch still happens; asserted.
4. Rotation: writing past 512 KB leaves a live file and one `.1`, never more;
   `read(n)` spans both and returns oldest-first; a malformed line is skipped.
5. `read` on a symlink or FIFO at the log path returns no events and does not
   block (deterministic swap tests, as in Phase 65).
6. The poll loop and the event loop both refresh the beat; the beat is
   throttled (two iterations inside 5 s → one write); clean exit removes it
   and leaves the event log.
7. `tagteam watch status` and `tagteam watch log` run under
   `TAGTEAM_READ_ONLY=1`; `tagteam watch` and `tagteam watch --mode notify`
   are still refused; every existing `watch` option still parses.
8. `GET /api/watcher/events` returns the records; `/api/now`'s `watcher`
   block has `beat_age_s` and `last_event` and keeps its existing keys.
9. Full suite green via the gate; checkout clean afterwards.
10. Manual, on this repo: run the iTerm2 watcher through one real handoff
    round and confirm `tagteam watch log` matches what the watcher tab showed.
    Reported as manual, with the output, not as a test.
