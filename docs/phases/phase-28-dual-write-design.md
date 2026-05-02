# Phase 28 Dual-Write Design Note

**Status:** Draft for review.
**Predecessor:** `docs/phases/sqlite-spike-findings.md` (spike + go decision).
**Successor:** Implementation PR — not yet open.

This note describes how `tagteam.cycle` and `tagteam.state` evolve from
"files are canonical" to "SQLite is canonical." It is a design pass,
not an implementation. The goal is to surface the load-bearing
decisions for review *before* code lands.

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

Three entry points cover all live state mutation:

1. **`tagteam.cycle.init_cycle`** — creates a cycle.
   Writes: `_status.json`, `_rounds.jsonl` (round 1), state derive.
   DB equivalent: `db.upsert_cycle` + `db.add_round` + `db.set_state`
   inside one transaction.

2. **`tagteam.cycle.add_round`** — appends a round, updates status,
   derives top-level state, runs stale-round detection.
   DB equivalent: `db.add_round` + `db.upsert_cycle` (status fields
   only) + `db.set_state` inside one transaction.

3. **`tagteam.state.update_state`** — direct state mutations from the
   watcher and CLI (`tagteam state set`).
   DB equivalent: `db.set_state` + `db.add_history_entry` for the new
   history row.

Diagnostics writes (`handoff-diagnostics.jsonl`) get dualized via
`db.add_diagnostic`. Baseline capture lives inside `init_cycle` and
follows along.

### Ordering and failure modes

```
file_write_succeeded = perform_file_write(...)
if not file_write_succeeded:
    raise   # existing failure mode, unchanged
try:
    perform_db_write(...)
except Exception as e:
    log_divergence("db_write_failed", e)
    # Do not raise. File path is canonical; DB is shadow.
```

The asymmetry is deliberate: a DB-write failure must not break the
existing file path. During Step A, DB writes are observability — if
they fail repeatedly, that's a signal to investigate, not to halt the
running cycle.

### What we measure during Step A

After every dual-write, run a divergence check:

```
db_md = db.render_cycle(conn, phase, type)
file_md = cycle.render_cycle(phase, type, project_dir)
if db_md != file_md:
    log_divergence("render_mismatch", phase, type, diff_summary)
```

Divergences go to a `dual_write_divergences` table and to
`handoff-diagnostics.jsonl`. We do *not* fail the write — we observe.
The success criterion for moving from A → B is: a soak window of
N days with zero divergence events across the test corpus and at
least one real project (likely tagteam-on-tagteam itself).

### Rollback for Step A

`rm -rf .tagteam/`. The file path is untouched.

## Step B — Read-flip

### What changes

`cycle.read_status`, `cycle.read_rounds`, `cycle.render_cycle`,
`state.read_state`, and the parser used by the Saloon dashboard all
switch to reading from the DB. The auto-export to markdown begins.

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
  corpus (24/24) and parameterized cycle states (Phase 28 spike PR).
- **Committed:** yes. The whole point.

### `git status` noise during in-progress cycles

A round-by-round re-render generates one diff per round, which is
fine for a 5-round cycle but accumulates if a cycle hits the 10-round
limit. Two options:

- **Option 1 (chosen):** render on every write. The `.md` evolves in
  step with the cycle. PR reviewers see the full conversation.
- **Option 2 (rejected):** render only at cycle close. Simpler diffs
  but in-progress cycles vanish from `git status`. Bad for the
  cross-day-resumption use case that motivated this whole effort.

The trade is correct — the user wants to come back to a half-finished
cycle and see exactly where it stands. Round-by-round renders give
that for free.

### Migration of legacy cycles

Existing projects already have `_rounds.jsonl` + `_status.json` pairs.
On Step B activation:

- The existing files are deleted (or moved to `.tagteam/legacy/` for
  one release as a safety net).
- The new `<phase>_<type>.md` is rendered fresh from the DB.
- Anyone with PRs based on the old file format will need to rebase.
  This is a single-time disruption per project.

`tagteam migrate --to-sqlite-step-b` runs this transition. Refuses to
run if any cycle is in non-terminal state (`in-progress`, `escalated`
unresolved, `needs-human` unresolved).

### Rollback for Step B

Add a `TAGTEAM_READ_FROM_FILES=1` environment variable that keeps
read paths on the file readers. Combined with the legacy backup at
`.tagteam/legacy/`, the rollback is set the env var, restore from
`.tagteam/legacy/`, restart watcher. Out of scope for Step C.

## Step C — File removal

The `cycle.read_status` / `cycle.read_rounds` / `state.read_state`
file readers are deleted. The `_status.json` / `_rounds.jsonl` writers
are deleted. `tagteam migrate --to-sqlite-step-b` no longer ships.
The `TAGTEAM_READ_FROM_FILES=1` escape hatch is removed.

### When is Step C safe to land

After Step B has shipped at least one tagged release with no
divergence events from the dual-write monitoring, plus visible Saloon
operation, plus at least one real handoff cycle running end-to-end
on it.

### Rollback for Step C

There isn't one cleanly. By Step C the files have been gone for a
release; reverting Step C means regenerating them from the DB and
re-introducing dual-write code. We accept this — Step C is the
"trust the DB" commitment.

## Divergence detection — concrete plan

Two layers:

### Runtime (Step A only)

Every dual-write call ends with a render-and-compare:

```python
def _check_divergence(conn, phase, type):
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

### CI (all steps)

A pytest test that loads a corpus of cycle fixtures (rankr's 24
cycles + the synthetic states-and-actions fixtures from
`tests/test_db.py`), runs both renderers, asserts byte-identity.
Runs on every push. If a parity bug regresses, CI catches it before
merge.

The fixtures should be checked-in JSON, not generated, so the test
doesn't depend on `tagteam.cycle.add_round`'s state machine.

## Migration gating

`tagteam migrate --to-sqlite-step-b` (and any future step-c migrate)
must refuse to run on a project whose `state.status` is not in
`{"done", "approved"}`. The error message says: "wait for the cycle
to complete (or `tagteam state set --status done` if you know it's
quiescent), then re-run."

This protects against the worst-case scenario of swapping the
storage layer mid-cycle and producing inconsistent state.

## Watcher considerations

The watcher's polling loop reads `state.json`'s `seq` field to
detect changes. During Step A, no change. During Step B, the
watcher reads `seq` from the DB instead of the file. The
`tagteam.state.read_state` swap covers this automatically — the
watcher does not import file paths directly.

The event-driven watcher (Phase 24, still independent of 28) becomes
trivial in Step B+: SQLite's `update_hook` or a polling
`SELECT seq FROM state` is cheap. Could ship as part of Step C or
as a follow-on.

## Testing plan

### Step A

- All existing tests still pass (file path unchanged).
- New: `tests/test_dual_write.py` — for each existing
  `cycle.add_round` / `init_cycle` test, assert DB state matches
  expected after the call.
- New: `tests/test_divergence.py` — fixture suite of cycle shapes,
  assert renderers match byte-for-byte.
- New: divergence-on-purpose test — manually corrupt the DB after
  a write, confirm `_check_divergence` logs a diagnostic and the
  caller does not raise.

### Step B

- Add a parametrized fixture across `read_from=[files, db]` for the
  full read-path test suite — proves both paths return equivalent
  shape.
- After Step B lands, the file-side fixture is removed (it tested
  code that no longer exists).

### Step C

- Tests for the deleted file paths get deleted.
- Sanity test: the package no longer imports `json` for cycle/state
  reads (a regression guard for accidental re-introduction of file
  reading).

## Open questions for the review

1. **Dual-write transaction scope.** The DB writes per call are
   logically "in one transaction." But the file write is *not* in
   that transaction. Should we wrap the whole pair in a try/commit
   such that DB rollback happens if file write fails post-DB? Today
   the doc above says file-first, DB-after, no DB rollback on file
   failure — but the DB write *also* updates `state.seq`, and if the
   subsequent file write fails the DB has stale state pointing at a
   round the files don't have. **Proposed answer:** keep file-first,
   db-second; if file fails, db doesn't run. If db fails (rare), log
   and accept the small drift — it's auto-corrected on the next
   successful write. But this is the place I'd most want pushback.

2. **Should the auto-export `.md` files replace `_rounds.jsonl` and
   `_status.json` immediately at Step B**, or coexist for a release?
   Coexisting means the file readers still work for one tag, which
   is real escape-hatch value. But the user-facing `git status` noise
   is doubled. **Proposed answer:** delete the legacy files at Step B
   activation, keep the env-var rollback as the escape hatch for a
   release, then remove the env var at Step C.

3. **Saloon dashboard read path.** The dashboard reads `state.json`
   and parses cycle files via `tagteam.parser`. Does the parser need
   any change for Step B beyond "read from DB instead of files"?
   Probably yes — its current API is path-based. Worth a small
   refactor in Step A so Step B is just a backend swap.

4. **What state goes in `state_history` table during dual-write?**
   The current `handoff-state.json` keeps an unbounded `history[]`
   array. Do we mirror it row-for-row, or condense, or cap? The
   spike imported all 20 entries from rankr's state. **Proposed
   answer:** mirror unbounded for now; address truncation policy
   only if growth becomes a problem in practice.

5. **Backfill for dual-read.** A project that ran on Step A for a
   while will have a populated DB. When Step B activates, do we
   trust the DB, or re-run the importer once to ensure consistency?
   **Proposed answer:** re-run import (idempotent for the spike's
   case via `--force` semantics; might need refinement for partial
   data) as a one-time consistency check. Cheap insurance.

6. **CI parity fixtures.** Where do they live and how are they
   generated? Options:
   (a) hand-written JSON fixtures committed to `tests/fixtures/`,
   (b) imported from a real project (rankr) checked in,
   (c) generated synthetically at test time.
   **Proposed answer:** (a) for the round-shape parity matrix
   (covers all action × state × edge-case combinations explicitly),
   plus the existing in-test-file synthetic fixtures from
   `tests/test_db.py::TestRenderParity`. Avoid (b) — couples this
   project's test suite to another repo's contents.

## Estimated scope

- Step A: ~300 LOC of new code + tests, mostly in `tagteam.cycle`.
  About a day of focused work after the design freezes.
- Step B: ~150 LOC of read-path swaps + auto-export hook + the
  step-b migrate command. Half a day.
- Step C: ~100 LOC of deletions + a regression-guard test. An hour.

The risk distribution is heavily front-loaded into Step A's
divergence-detection plumbing. Step B and C are mostly mechanical
once Step A is solid.
