# Phase 28 Dual-Write Design Note

**Status:** Draft for review (rev 3 — addresses second-round review).
**Predecessor:** `docs/phases/sqlite-spike-findings.md` (spike + go decision).
**Successor:** Implementation PR — not yet open.

This note describes how `tagteam.cycle` and `tagteam.state` evolve from
"files are canonical" to "SQLite is canonical." It is a design pass,
not an implementation. The goal is to surface the load-bearing
decisions for review *before* code lands.

The first review caught real defects in rev 1: an undercounted writer
inventory, an invalid status name in the gating expression, and a
"self-healing" failure-mode story that would silently leave the shadow
DB stale. Rev 2 addressed those plus several gaps (concurrent writers,
AMEND specifics, file-internal atomicity, backward compatibility).

Rev 3 (this revision) addresses the second-round review: tightens the
`db_invalid` gate to apply to all DB readers (not just the divergence
detector); makes repair semantics concrete (same writer lock, bounded
backoff, sentinel-not-cleared-on-failure); fills out the reader
inventory with `tui/handoff_reader.py` and `tui/review_replay.py`;
classifies file vs. DB read race windows instead of asserting
equivalence; operationalizes the file-side atomicity caveat into the
divergence detector; strengthens Step C gates to two projects with
complex history; resolves the `update_state`/`write_state` layering
trap; flags the `SKILL.md` agent-facing instructions; adds a sentinel
authority rule; specifies the `--reverse` export-from-DB path; and
locks in a deterministic CI recorder script.

## Goal

Land the SQLite store as the canonical runtime store of handoff
state, with the existing JSON/JSONL files retained only as an
auto-rendered, git-committable export. Achieve this without a
flag-day big-bang flip.

## Non-goals

- Changing the agent-facing CLI surface (`tagteam cycle add`, `tagteam
  cycle rounds`, `tagteam state set`, `/handoff` SKILL flow).
- Performance optimization beyond what falls out of having one source
  of truth. The spike showed storage cost is in the noise vs.
  subprocess startup; this work is about *operational shape*, not
  speed.
- Schema changes beyond what's needed to reach parity with the file
  layout. New columns (e.g. for absorbed Phases 21/22) come after.

## Approach: three sub-steps, not two

The findings doc framed this as two stages (dual-write, then DB-only).
On reflection, the read-path flip is enough of a behavior change to
deserve its own checkpoint. Three steps:

| Step | Writes | Reads | Files | DB | Risk |
|---|---|---|---|---|---|
| **A — Dual-write** | both | files | canonical | shadow | low — file path unchanged |
| **B — Read-flip** | both | DB | shadow (auto-export) | canonical | medium — first time DB drives behavior |
| **C — File removal** | DB only | DB | render-only export | canonical | high — no fallback |

Each step is its own PR with its own soak window. A regression
caught in B can be fixed without touching A's already-shipped writers;
a regression caught in C means the export contract drifted and is
recoverable by reverting C.

## Step A — Dual-write

### Which writers are dualized

Every code path that mutates files in `docs/handoffs/`,
`handoff-state.json`, or `handoff-diagnostics.jsonl` needs a paired
DB write. The full inventory:

1. **`tagteam.cycle.init_cycle`** — creates a cycle.
   Writes: `_status.json`, `_rounds.jsonl` (round 1), state derive.
   DB: `db.upsert_cycle` + `db.add_round` + `db.set_state` (+ baseline
   capture as `cycles.baseline_json`) inside one transaction.

2. **`tagteam.cycle.add_round`** — appends a round, updates status,
   derives top-level state, runs stale-round detection.
   DB: `db.add_round` + `db.upsert_cycle` (status fields only) +
   `db.set_state` inside one transaction. **Exception:** AMEND — see
   "AMEND specifics" below.

3. **`tagteam.state.update_state`** — direct state mutations from the
   watcher, server, and `tagteam state set` CLI. The server
   (`/api/state`) and watcher already route through here, so they are
   covered transitively.
   DB: `db.set_state` + `db.add_history_entry` for the new history row.

4. **`tagteam.state.write_state`** — direct state writes that bypass
   the merge logic in `update_state`. Less common, but used by some
   admin paths. Same DB call as `update_state` minus the history append.

5. **`tagteam.state.clear_state`** — deletes `handoff-state.json`.
   DB: `DELETE FROM state WHERE id=1` plus an entry in `state_history`
   recording the clear. Worth modeling explicitly because resetting
   state is a real workflow (e.g. after a crashed cycle).

6. **`tagteam.state._log_seq_mismatch`** — appends to
   `handoff-diagnostics.jsonl` directly. Not routed through any other
   writer.
   DB: `db.add_diagnostic(kind="seq_mismatch", ...)`.

7. **`tagteam.state.clear_diagnostics_log`** — truncates the
   diagnostics file.
   DB: `DELETE FROM diagnostics`.

The corollary: the dual-write integration cannot happen in just one
place. Each of these gets its own dual-write wrapper, sharing the
ordering/locking/repair logic via a small helper module
(`tagteam/dualwrite.py`).

**Layering ownership for `update_state` and `write_state`.**
`state.update_state` calls `state.write_state` internally to perform
the actual file write (state.py:140-204). Naively wrapping both with
dual-write would issue two DB writes per `update_state` call and
double-append to `state_history`. The rule:

- `update_state` owns the DB state-history append. Its dual-write
  wrapper does `db.set_state` + `db.add_history_entry`.
- `write_state`'s dual-write wrapper does `db.set_state` only — no
  history. This is the right behavior for direct callers of
  `write_state` (e.g. admin replace paths) which intentionally bypass
  the history-tracking merge.
- The `update_state` → `write_state` internal call is special-cased:
  the inner `write_state` invocation skips its own dual-write hook to
  avoid double-writing. Implemented via a thread-local flag set by
  `update_state` before calling `write_state`.

This is the only place in the design where the dual-write helper
needs to know about call-site context. Worth a comment in the
implementation pointing here.

### Ordering and failure modes

The asymmetry is deliberate: a DB-write failure must not break the
existing file path during Step A. But the rev-1 "auto-corrected on
next write" story was wrong — a DB write that fails leaves the shadow
DB in an unknown state, and any subsequent divergence check that
compares its content as if it were meaningful will produce noise or
miss real drift.

Revised ordering:

```
acquire_writer_lock(project_root)
try:
    file_write_succeeded = perform_file_write(...)
    if not file_write_succeeded:
        raise   # existing failure mode, unchanged

    try:
        perform_db_write(...)
    except Exception as e:
        mark_db_invalid(project_root, reason=str(e))
        log_diagnostic("db_write_failed", e)
        # Do not raise — file path is canonical during Step A.
finally:
    release_writer_lock(project_root)

# Repair runs out-of-band, not synchronously, and acquires its own lock.
```

`mark_db_invalid` writes a sentinel field in the `state` table
(`db_invalid_since` timestamp) plus a `.tagteam/DB_INVALID` flag file.
**Authority rule:** the flag file is authoritative. The DB column is
informational and serves observability — when SQLite is unavailable
or corrupt, the column may be unreadable, but the flag file always
works. Any code path checking the sentinel reads the flag file first;
the DB column is only consulted when the flag file is present and
the writer wants the original failure timestamp.

**Universal gate.** While the sentinel is set, **all DB reads outside
the repair path** must refuse and return a `db_invalid` indication
(either an exception or a sentinel value, depending on call site).
This applies to: the divergence detector, the Saloon dashboard, any
diagnostic CLI that reads cycle/state from the DB, and Step B+
readers. The single exception is the repair path itself, which
intentionally reads the DB to produce a delta against the file
truth. Practically: every `db.connect()`-using reader checks the
sentinel before issuing its first SELECT and short-circuits if set.

**Repair semantics.** Repair must:

1. Acquire the **same project writer lock** that excludes file-side
   writers (not a separate lock). Otherwise repair could import
   while `cycle.add_round` is mid-write on the file side, capturing
   a torn state.
2. Run `db.import_from_files` with `--force` semantics (rebuild the
   shadow DB from the canonical files).
3. After the import succeeds, run a parity check across all cycles.
   If any `render_mismatch` survives, repair has not actually
   recovered — leave the sentinel set, log a `repair_failed`
   diagnostic, schedule a retry.
4. Only clear the sentinel after both import and parity check
   succeed.

**Repair failure handling.** If repair itself fails (DB write error,
parity mismatch, etc.):

- Sentinel remains set.
- Log `repair_failed` diagnostic with the failure reason.
- Schedule the next retry with bounded exponential backoff: 1 min,
  2 min, 4 min, 8 min, capped at 1 hour. Do not retry indefinitely
  silently.
- After 24 hours of continuous repair failure, surface a louder
  signal (Saloon dashboard banner, watcher log line at WARN). This
  is the "operator must intervene" threshold.
- Manual override: `tagteam state repair-db --force-clear` clears
  the sentinel without running repair. Documented as last-resort
  with the explicit caveat that it makes the DB officially trusted
  without parity verification.

**Triggers for repair attempts:**

- A periodic check in the watcher (poll every minute for a stale
  sentinel; subject to the backoff above).
- `tagteam state repair-db` CLI for explicit invocation.
- A retry-on-next-write hook (best-effort, single attempt before
  falling back to "file-write succeeded, DB write failed again").

For Step B+, this changes: DB write failures must raise, because the
DB is canonical and silently degraded reads are not acceptable.

### Concurrent writers and locking

Today the file-based system gets accidental ordering from
filesystem operations: each writer opens/writes/closes its file, and
two concurrent writers might race but produce locally-consistent (if
last-write-wins) state. SQLite WAL handles intra-DB concurrency, but
the dual write straddles two storage layers. Two writers interleaving
file-DB-file-DB can produce a divergence detector false positive
(comparing writer A's DB state against writer B's file state).

Mitigation: a process-level **writer lock** acquired around the
entire dual-write critical section. Implementation: a POSIX
`fcntl.flock` (or Windows `msvcrt.locking`) on a sentinel file at
`.tagteam/.write.lock`. Held for the duration of the file write +
DB write + divergence check.

Reads do **not** acquire the lock — they're allowed to race against
in-flight writes. The race semantics are not uniform across storage
backends, however; rev 2's "today's behavior is the same" was too
broad. Classification:

| Read source | Atomicity | Race window |
|---|---|---|
| `handoff-state.json` | tmp+replace via `Path.replace` (atomic on POSIX/NTFS) | reader sees pre-write or post-write, never torn |
| `_status.json` | direct `write_text` | small torn-write window during rewrite |
| `_rounds.jsonl` | append | new line might be partially visible mid-append; existing lines stable |
| SQLite reads | WAL transactional consistency | reader sees a consistent snapshot, may lag latest commit |

During Step A (file authority), readers maintain today's mixed
semantics. During Step B+ (DB authority), readers gain
serializability for free — a quiet improvement, but worth knowing
that pre-Step-B test cases observing torn `_status.json` reads
will not reproduce against the DB.

The lock is per-project. Multiple tagteam projects on the same
machine don't contend.

Out of scope: multi-host concurrency. The fcntl lock is
per-machine; if a project lives on a network drive accessed from
two hosts, the lock does not serialize. Tagteam doesn't support
this configuration today and the design doesn't add it.

### AMEND specifics

`AMEND` is a lead-only mid-review action that appends a round
without bumping the round number, updating the status, or deriving
top-level state (see `cycle.py:287-298`). Its file-side write is just
an append to `_rounds.jsonl`. Its DB-side write is just
`db.add_round`. No `set_state`, no `upsert_cycle` for status.

The divergence check for AMEND compares only the cycle's rendered
markdown (round log + footer), not the top-level state. The state is
deliberately unchanged.

### File-side atomicity caveat (pre-existing, flagged)

`cycle.add_round`'s file-side write is itself multi-step:
1. Append a line to `_rounds.jsonl`.
2. Rewrite `_status.json` with the updated `state` / `ready_for` /
   `round`.
3. Derive `handoff-state.json` (turn, status, command).

A failure between (1) and (2) leaves the file side internally
inconsistent — a round exists in the JSONL with no corresponding
status update. This pre-dates Phase 28 and is **not solved by**
dual-write; the dual write inherits the same atomicity gap on the
file side.

To distinguish "file side was already inconsistent" from
"dual-write divergence" during debugging, the divergence detector
runs a **file-side sanity pre-check** before declaring
`render_mismatch`:

1. `_rounds.jsonl` parseable as line-delimited JSON (every line a
   valid object).
2. `_status.json` parseable, has the required fields (`state`,
   `round`, `phase`, `type`, `lead`, `reviewer`).
3. `_status.json.round == max(round) in _rounds.jsonl`.
4. Top-level `handoff-state.json.phase` and `.type` reference a
   cycle that exists in `docs/handoffs/`.

If any check fails, the diagnostic is classified as
`file_inconsistent` with the failing check named — **not**
`render_mismatch`. This prevents pre-existing file hazards from
producing false positives in dual-write monitoring. Operators
investigating `file_inconsistent` events look at file recovery
first, not dual-write logic.

The flip to DB-canonical (Step C) actually closes the underlying
gap — DB writes within `cycle.add_round` happen in a single
transaction.

### What we measure during Step A

After every dual-write, run a divergence check unless the DB is
marked invalid:

```python
def _check_divergence(conn, phase, type):
    if db_is_invalid():
        return   # no comparison while shadow is stale
    db_md = db.render_cycle(conn, phase, type)
    file_md = cycle.render_cycle(phase, type)
    if db_md != file_md:
        db.add_diagnostic(conn, "render_mismatch", {
            "phase": phase, "type": type,
            "ndiff_lines": _count_diff_lines(db_md, file_md),
            "db_sha": _hash(db_md), "file_sha": _hash(file_md),
        }, datetime.now(UTC).isoformat())
```

Stored sample: hashes only by default, full diff opt-in via
`TAGTEAM_DIVERGENCE_FULL_DIFF=1` for debugging. This avoids logging
sensitive cycle content by default.

The success criterion for moving from A → B is: a soak window of
N days in real use with **zero `render_mismatch` diagnostics** plus
**zero `db_invalid` events** that weren't manually triggered. The
test corpus (CI parity fixtures) must also be clean continuously.

### Rollback for Step A

`rm -rf .tagteam/`. The file path is untouched.

## Step B — Read-flip

### What changes

Reads switch to the DB. The auto-export to markdown begins. Code
paths that swap:

- `cycle.read_status`, `cycle.read_rounds`, `cycle.render_cycle`
- `state.read_state`
- The Saloon parser (`tagteam.parser`) — its current API is
  path-based; it needs a backend abstraction so the swap is local.
  Likely a small refactor in Step A so Step B is "swap the backend,"
  not "rewrite the parser."
- **`tagteam.tui.state_watcher`** — currently does its own
  `path.read_text` of `handoff-state.json` (tui/state_watcher.py:93).
  Must be migrated to share a reader with `state.read_state`.
- **`tagteam.tui.handoff_reader`** — locates and reads cycle round
  files directly (tui/handoff_reader.py:25 constructs
  `f"{phase}_{step_type}_rounds.jsonl"` paths). At Step B these files
  no longer exist in `docs/handoffs/`. Migrate to read via DB or via
  the auto-rendered markdown (depending on which makes more sense
  for the TUI's structured-data needs — likely DB).
- **`tagteam.tui.review_replay`** — depends on `handoff_reader`
  (tui/review_replay.py:10) plus a direct call to
  `parser.read_cycle_rounds`. Both transitively path-bound; both need
  the Step B migration.

**Agent-facing instructions.**
`.claude/skills/handoff/SKILL.md` (and the packaged copy at
`tagteam/data/.claude/skills/handoff/SKILL.md`) tell agents to "Read
`handoff-state.json`" directly (lines 12, 30, 39, 149+). After Step B
that file is gone (moved to `.tagteam/legacy/`); after Step C it is
not maintained. Update both copies during Step B activation:

- Replace "Read `handoff-state.json`" with "Run `tagteam state`"
  (the existing bare-state subcommand prints the current state to
  stdout). Reads from canonical store via `state.read_state`, so the
  Step B reader-flip carries it along automatically.
- Update the `--command` text in `/handoff start --roadmap` examples
  similarly.
- The packaged copy is shipped to user projects via `tagteam setup`
  — the change reaches existing users only after they re-run setup
  or upgrade.

The "reader inventory" lives next to the writer inventory above —
both must be tracked through the migration.

### Auto-export contract

This is the load-bearing claim of the whole Phase 28 design — that
git-visible audit history is preserved. Specific contract:

- **Trigger:** every cycle write triggers a re-render of that cycle's
  markdown. State writes do not trigger a render (state file is not
  the audit artifact).
- **Path:** `docs/handoffs/<phase>_<type>.md` (a single `.md` file
  per cycle, replacing the `_rounds.jsonl` + `_status.json` pair).
- **Content:** `db.render_cycle` output verbatim — already proven
  byte-identical to today's `tagteam cycle render` on the rankr
  corpus (24/24) and parameterized cycle states (Phase 28 storage PR).
- **Committed:** yes. The whole point.

### `git status` noise during in-progress cycles

Round-by-round re-renders are accepted as part of the product:
in-progress visibility for cross-day resumption is exactly the use
case that motivated this phase. Rejected alternative: render only at
cycle close. Bad — vanishes in-progress cycles from PR review.

### Migration of legacy cycles

Existing projects already have `_rounds.jsonl` + `_status.json` pairs.
On Step B activation:

- The existing files are **moved** (not deleted) to
  `.tagteam/legacy/<phase>_<type>_{rounds.jsonl,status.json}`. They
  remain available for one tagged release as a deterministic restore
  point if the env-var rollback (below) is invoked.
- The new `<phase>_<type>.md` is rendered fresh from the DB.
- Anyone with PRs based on the old file format will need to rebase.
  This is a single-time disruption per project.

`tagteam migrate --to-sqlite-step-b` runs this transition. Refuses
to run unless the migration gating below passes.

### Backfill before the read flip

A project that ran on Step A for a while will have a populated DB —
but Step A's failure mode means the DB might be stale (in a
`db_invalid` state) at the moment of the flip. Don't trust the
Step A DB blindly:

1. Acquire the migration lock.
2. Run `tagteam state repair-db` — re-imports from files, clears
   `db_invalid` if set.
3. Run a full parity check across all cycles. Any `render_mismatch`
   blocks the migration with an actionable message.
4. Only after parity is clean, perform the file move and switch
   reads to DB.

This is cheap insurance: the DB is *probably* fine but
"probably" is wrong here.

### Rollback for Step B

The `TAGTEAM_READ_FROM_FILES=1` environment variable keeps read paths
on the file readers. Combined with the `.tagteam/legacy/` snapshot,
the rollback is:

1. `export TAGTEAM_READ_FROM_FILES=1`
2. Restore files: `mv .tagteam/legacy/* docs/handoffs/`
3. Restart any running watcher / Saloon / TUI.

Documented in the Step B release notes. Removed at Step C.

### Backward compatibility for cloned projects

A user who clones a Step-B-era project on a machine running an
**older tagteam version**:
- Older tagteam reads files from `docs/handoffs/`.
- Step B has rendered `<phase>_<type>.md` files there but no
  `_rounds.jsonl` / `_status.json` (those moved to `.tagteam/legacy/`,
  which is gitignored).
- Older tagteam's `cycle.read_status` returns `None` for every cycle.

This is a one-way upgrade: once a project is on Step B, downgrading
tagteam without a re-import is not supported. Document explicitly in
release notes. The safety valve: `tagteam migrate --to-sqlite-step-b
--reverse` (added in Step B) restores files from the DB for users
who need to roll back at the project level.

**`--reverse` implementation.** Older versions of tagteam can't
auto-detect a Step-B-era project (they don't know what to look for),
so the reverse path is an explicit user-run command. It needs:

1. A new helper `db.export_to_files(conn, project_dir)` that walks
   the DB and writes `_rounds.jsonl` + `_status.json` per cycle plus
   `handoff-state.json`. Output must round-trip (re-import gives the
   same DB).
2. After export, run a parity check: re-import the just-exported
   files into a fresh DB, render both, assert byte-identity. Refuse
   to declare success if parity fails.
3. After parity passes, restore `.tagteam/legacy/` files if they
   exist (tie-breaker: prefer the freshly exported files; legacy is
   the older snapshot from before any Step A activity).
4. Print a summary of cycles exported plus a note that the user
   should `pip install tagteam==<older-version>` if they want to
   actually downgrade.

## Step C — File removal

The `cycle.read_status` / `cycle.read_rounds` / `state.read_state`
file readers are deleted. The `_status.json` / `_rounds.jsonl` writers
are deleted. `tagteam migrate --to-sqlite-step-b` no longer ships.
The `TAGTEAM_READ_FROM_FILES=1` escape hatch is removed. The
`.tagteam/legacy/` directory's contents are no longer maintained
(but old contents are not auto-deleted — users may keep them).

### When is Step C safe to land

All of the following must hold:

1. **At least one tagged release** of Step B has shipped.
2. **Zero divergence events** in real use during the soak window —
   from the Saloon dashboard's diagnostics view across the projects
   that adopted Step B early.
3. **Successful downgrade rehearsal across at least two projects.**
   At least one of those projects must exercise *complex history*:
   AMEND rounds, ESCALATE / aborted cycles, NEED_HUMAN if applicable,
   non-ASCII content, empty content, AND cycles imported from older
   versions of tagteam. A simple-cycle-only rehearsal does not
   satisfy this gate. Each rehearsal: run `--reverse`, verify the
   restored files match parity, run an actual handoff against the
   restored state, confirm no regressions.
4. **Documented backup/restore path** in `docs/` covering: how to
   back up `.tagteam/tagteam.db` (`sqlite3 .backup`), how to restore
   from a corrupted DB, how to run `db.export_to_files` against a
   backup, how to clear `db_invalid` sentinels. Tested as part of
   the rehearsal — the docs are exercised, not just written.

These are not polite suggestions — Step C is gated on all four,
verified by the maintainer at release time.

### Rollback for Step C

There isn't one without recreating the writers. By Step C the
file-side code is gone; reverting Step C means re-introducing
dual-write code and rebuilding files from the DB. Acceptable trade
because Step C is the explicit "trust the DB" commitment, gated by
the criteria above.

## Divergence detection — concrete plan

Two layers:

### Runtime (Step A only)

`_check_divergence` (sketched in "What we measure during Step A").
Skips when the DB is marked invalid. Hash-only by default; full diff
under env var. Logs to `diagnostics` table and to
`handoff-diagnostics.jsonl` (since file path is canonical during
Step A, the file diagnostics log remains authoritative).

### CI (all steps)

A pytest test covering two corpora:

1. **Recorded synthetic project corpus** — a small project's
   `docs/handoffs/` and `handoff-state.json` checked into
   `tests/fixtures/recorded/`, generated by a deterministic recorder
   script (`tests/fixtures/recorder.py`) that drives `tagteam.cycle`
   through a defined matrix of action × state combinations.
2. **Hand-written edge fixtures** — short JSON files for cases the
   recording can't easily produce: missing keys, empty content,
   non-ASCII, malformed-but-recoverable rounds.

Hand-written-only is rejected as too self-confirming — the recording
catches bugs the author didn't anticipate. External-repo imports
(originally proposed) are also rejected — couples this project's
test suite to another repo's contents.

**Recorder script requirements:**

- Checked into the repo (`tests/fixtures/recorder.py`), not run ad
  hoc by maintainers.
- Deterministic output: fixed timestamps (e.g.
  `2026-01-01T00:00:00+00:00` + offsets per round), fixed agent
  names (Lead/Reviewer), no system entropy.
- Re-runnable: `python tests/fixtures/recorder.py
  tests/fixtures/recorded/` regenerates the corpus byte-identically
  given the same tagteam version. CI can run it as a smoke check
  to confirm no drift.
- Documented matrix at the top of the script: which cycle states
  and actions are covered, which intentionally aren't.
- The corpus is committed; re-recording is an explicit author
  action that produces a reviewable diff. This means a renderer
  bug fix that changes output legitimately requires re-recording —
  the diff is the proof.

The CI test runs both renderers against every fixture and asserts
byte-identity. Failure blocks merge.

## Migration gating

`tagteam migrate --to-sqlite-step-b` (and any future step-c migrate)
must pass **all** of the following before running:

1. **Top-level state status not active.** `state.status not in
   {"ready", "working"}`. (Note: `state.VALID_STATUSES` is `{"ready",
   "working", "done", "escalated", "aborted"}`. The rev-1 doc's
   "approved" was wrong — `approved` is a *cycle* state, not a
   top-level state.)
2. **All cycles in terminal state.** Every cycle in `docs/handoffs/`
   must have `state.json.state in {"approved", "escalated",
   "aborted"}` or be explicitly marked done by the user.
3. **No recent writer activity.** The most recent
   `state.updated_at` is at least N seconds ago (default 30s). This
   is a coarse proxy for "no in-flight write." Combined with the
   writer lock below, sufficient.
4. **Migration lock acquired.** Create a sentinel file
   `.tagteam/MIGRATION_IN_PROGRESS` with a PID + timestamp at the
   start of the migration. Other migrate / dual-write paths refuse
   to run while this exists. Stale-lock handling:

   - **PID liveness check.** On encountering the lock, attempt
     `os.kill(pid, 0)` (signal 0). If the process no longer exists,
     the lock is stale and may be cleared automatically with a log
     line.
   - **Timeout.** Lock files older than 1 hour are considered stale
     regardless of PID liveness, on the assumption that a real
     migration completes well under that.
   - **Forced abort.** `tagteam migrate --abort` removes the lock
     after a confirmation prompt ("Are you sure no migration is in
     progress? [y/N]"). Useful for stuck-process recovery on
     non-POSIX systems where signal-0 is unreliable.
   - Removed automatically on migration success.
5. **Clean parity check.** Run the divergence detector across
   every cycle. Any `render_mismatch` blocks with an actionable
   message ("run `tagteam state repair-db` first").

Failure of any check exits non-zero with a message describing
which check failed and how to fix it.

## Watcher considerations

The watcher's polling loop reads `state.json`'s `seq` field to
detect changes. During Step A, no change. During Step B,
`state.read_state` reads from the DB — the watcher's call goes
through it transparently.

`tagteam.tui.state_watcher` does **not** use `state.read_state` —
it has its own `read_handoff_state` path-based reader (see
`tui/state_watcher.py:93`). Migrate it during Step B by adding a
shared reader function in `state.py` that both call.

The event-driven watcher (Phase 24, still independent of 28) becomes
trivial in Step B+: SQLite's `update_hook` or a polling
`SELECT seq FROM state` is cheap. Could ship as part of Step C or
as a follow-on.

## state_history specifics

The current `handoff-state.json` keeps an unbounded `history[]`
array (rankr's state file has 20 entries; older projects have more).
Rev 1 proposed "mirror unbounded for now." Refined for this rev:

- **Step A:** mirror row-for-row to `state_history` table. The file
  side keeps writing the same array. Both sides remain in lockstep.
  Verified by parity check.
- **Step B+:** the DB is canonical. The file side is gone (only
  cycle markdown remains as auto-export; state has no file
  representation). The unbounded growth is now a DB concern only.
- **Truncation policy:** deferred. Add a `--retain-history-days N`
  flag to a future cleanup command if and when growth becomes a
  measurable problem. Not on the Phase 28 critical path.

## Testing plan

### Step A

- All existing tests still pass (file path unchanged).
- New: `tests/test_dual_write.py` — for each existing
  `cycle.add_round` / `init_cycle` / `state.update_state` test,
  assert DB state matches expected after the call.
- New: `tests/test_divergence.py` — recorded + hand-written corpus,
  assert renderers match byte-for-byte.
- New: divergence-on-purpose test — manually corrupt the DB after
  a write, confirm `_check_divergence` logs `db_invalid` and the
  caller does not raise.
- New: locking test — two threads attempting dual-write
  concurrently, confirm no false-positive divergence and no lost
  writes.
- New: AMEND-specific test — confirm DB-side mutation is
  rounds-only, no state derive, divergence check passes.
- New: `db_invalid` sentinel test — set the flag file, attempt a
  read via every reader path (cycle.read_status,
  state.read_state, db.render_cycle, the divergence detector),
  confirm each refuses or returns `db_invalid`. Then run repair,
  confirm sentinel clears and reads resume.
- New: repair-failure backoff test — inject a persistent DB error,
  confirm repair retries with exponential backoff up to 1 hour,
  sentinel never clears prematurely, the 24-hour louder-signal
  threshold fires.
- New: `update_state`/`write_state` no-double-write test — call
  `update_state`, confirm exactly one DB state-history entry is
  added (not two from the inner `write_state` call).

### Step B

- Add a parametrized fixture across `read_from=[files, db]` for the
  full read-path test suite — proves both paths return equivalent
  shape during the migration window.
- Test for `--reverse` migration: after Step B, run reverse, confirm
  rounds.jsonl / status.json restored byte-equal to pre-migration,
  and the post-export parity check passes.
- Tests for `tui/state_watcher`, `tui/handoff_reader`, and
  `tui/review_replay` reading via the shared backend (covers the
  full TUI reader inventory, not just the state watcher).
- **Migration gate tests.** One test per gate:
  1. `state.status == "ready"` blocks the migration.
  2. A non-terminal cycle (`in-progress`) blocks the migration.
  3. A recent `state.updated_at` (< 30s) blocks the migration.
  4. Stale lock: PID alive blocks; PID dead clears automatically.
  5. Stale lock: file > 1 hour old clears automatically.
  6. `tagteam migrate --abort` clears the lock after confirmation.
  7. A `render_mismatch` divergence diagnostic blocks the
     migration with a "run repair-db first" message.
- SKILL.md update test (in `tests/test_setup.py`): after
  `tagteam setup`, the installed SKILL.md does not contain
  "Read `handoff-state.json`" as user-facing instruction.

### Step C

- Tests for the deleted file paths get deleted.
- Sanity test: the package no longer imports `json` for cycle/state
  reads (a regression guard for accidental re-introduction of file
  reading).

## Open questions (remaining after rev 3)

Rev 2's first open question (repair scheduling cadence) resolved
into the body — the answer is "both periodic poll and
retry-on-next-write, with bounded exponential backoff capped at 1
hour and a 24-hour louder-signal threshold."

One question remains genuinely open:

1. **Step B rollout cadence vs. soak time.** The doc says "soak in
   real use" without naming a duration. Two weeks? One release
   cycle? The right answer depends on usage volume — if only
   `tagteam-on-tagteam` adopts Step B initially, even a month might
   not produce enough divergence-check coverage. Open question for
   release planning, not for design freeze. **Provisional answer:**
   minimum 14 days of continuous use across at least one project,
   with no `render_mismatch` events, before Step C even enters the
   gate-checking phase. Maintainer can extend if usage volume is
   too low to be confident.

## Estimated scope

- Step A: ~600 LOC of new code + tests (rev 1: ~300; rev 2: ~500;
  rev 3: +100 for sentinel hard-gate plumbing across all readers,
  the repair-failure backoff state machine, and the
  `update_state`/`write_state` ownership special case). About 2 days
  of focused work after design freeze.
- Step B: ~250 LOC of read-path swaps (now including
  `tui/handoff_reader` and `tui/review_replay`) + auto-export hook +
  the step-b migrate command + the `--reverse` path with parity
  check + SKILL.md updates. About 1.5 days.
- Step C: ~100 LOC of deletions + a regression-guard test. An hour.

The risk distribution remains heavily front-loaded into Step A's
divergence-detection plumbing. Step B and C are mostly mechanical
once Step A is solid.
