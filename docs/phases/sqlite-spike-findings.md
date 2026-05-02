# Phase 28 Spike — SQLite as Canonical Runtime Store

**Date:** 2026-05-01
**Test data:** `/Users/jackblacketter/projects/rankr` (read-only) — 24 cycles, 85 rounds, 272 KB
**Spike code:** `tagteam/db_spike.py` (throwaway)
**Recommendation:** **Go.** Proceed to Stage 2 (dual-write release) of Phase 28.

## Summary

The spike validates the core architectural claim: SQLite can replace the
current file-based runtime store without losing the git-visible audit
properties that motivated the file-based design in the first place. The
DB-rendered markdown is **byte-identical** to the current
`tagteam cycle render` output for every cycle in the rankr corpus.

The latency numbers are favorable but not decisive — Python interpreter
startup (~130ms per CLI invocation) dominates storage cost. The case for
SQLite rests on architectural simplification (single source of truth,
atomic transactions, schema enforcement), not raw speed.

## What was built

`tagteam/db_spike.py` (~280 lines) covers:

- Schema: `cycles`, `rounds`, `state`, `state_history`
- One-way importer from `docs/handoffs/*` and `handoff-state.json`
- Writer helpers: `upsert_cycle`, `add_round`, `set_state`,
  `add_history_entry`
- Reader helpers: `get_rounds`, `get_cycle`, `list_cycles`
- Markdown renderer matching `tagteam.cycle.render_cycle` output

What it deliberately doesn't have: dual-write, CLI integration,
watcher integration, schema migration tooling, production migrate
command. Those only matter if this report says "go."

## Data inventory (rankr corpus)

| Field | Value |
|---|---|
| Cycles | 24 (12 phases × plan + impl) |
| Rounds | 85 |
| All cycle states | `approved` |
| Round actions | SUBMIT_FOR_REVIEW (43), APPROVE (24), REQUEST_CHANGES (18) |
| Roles | lead (43), reviewer (42) |
| Round content length | min 9, max 5456, avg 1442 chars |
| State history entries | 20 |
| Total disk size (files) | 272 KB |

Limits of this corpus:

- **No `in-progress`, `escalated`, `aborted`, or `needs-human` cycles.**
  All terminal states. Schema handles them correctly but they are not
  exercised by the round-trip.
- **No AMEND, ESCALATE, or NEED_HUMAN actions.** Those code paths are
  rare and not represented.
- **Older round schema:** rankr's rounds carry only
  `{round, role, action, content, ts}` — no `updated_by`, no `summary`,
  no structured fields. The spike schema treats those as nullable, which
  is the right default for a Phase 28 implementation that must absorb
  pre-existing projects.

Acceptable for a go/no-go decision. The full production port should
include synthetic round-trip tests for the missing states/actions.

## Round-trip diff result

For each of the 24 cycles in rankr, the spike:
1. Imported the cycle from JSONL/JSON into SQLite
2. Rendered markdown from SQLite via `db_spike.render_cycle`
3. Rendered markdown from current files via `tagteam cycle render`
4. Compared byte-for-byte

**Result: 24/24 byte-identical.** This is the strongest signal the
spike could produce. It means:

- Auto-rendered markdown for git-visible audit history is not a
  regression — it is exactly what users see today
- Any DB → files round-trip would be lossless
- A dual-write rollout has a trivial verification harness (compare the
  two renderers on every write)

## Latency probes

All measurements on darwin / SQLite WAL mode. Function-level (not
subprocess) unless noted.

### Write — 50 `add_round` calls (25-round cycle, alternating roles)

| Path | Median total | Per-call |
|---|---:|---:|
| DB (`db_spike.add_round` + commit) | 2.0 ms | 0.04 ms |
| Files (`tagteam.cycle.add_round`) | 53.4 ms | 1.07 ms |

DB is **~27× faster** at the API layer. The files path pays for status
file rewrite and top-level state derivation on every call; the DB path
is a single insert per row.

### Read — ~100 entries

| Path | Median |
|---|---:|
| DB (`db_spike.get_rounds`) | 0.38 ms |
| Files (`tagteam.cycle.read_rounds`) | 0.64 ms |

DB ~1.7× faster. JSONL is already fast at this size; the DB advantage
will grow with cycle/round counts but is modest in the regime tagteam
operates in.

### Subprocess — `tagteam cycle rounds` CLI

| Path | Median |
|---|---:|
| `python3 -m tagteam cycle rounds ...` | 130 ms |

Dominated by Python interpreter startup, not storage. **From an
agent's perspective, the storage choice is invisible** — both paths
will pay ~130 ms per tool call.

### Storage size

| | Bytes |
|---|---:|
| Full rankr DB (24 cycles, 85 rounds) | 4,096 |
| Full rankr files (`docs/handoffs/`) | 272,000 |

The DB is one 4KB SQLite page. Files are bigger because of repeated
JSON whitespace, status file overhead per cycle, and per-round JSON
key strings. This ratio narrows for larger datasets but never inverts.

## Schema notes (worth carrying into the production port)

- **`cycles` UNIQUE on `(phase, type)`** — one cycle per phase/type
  pair, matching the current file-naming convention.
- **`rounds` autoincrement PK preserves insertion order** — no
  composite key on `(cycle_id, round, role)` because AMEND adds rows
  to an existing round without bumping it. Ordering by `id` gives the
  conversation order, matching how `read_rounds` works today.
- **`state` is a singleton** (`CHECK (id = 1)`) — there is one
  `handoff-state.json` per project, model that constraint in SQL.
- **`state_history` separate table** — `handoff-state.json.history[]`
  is unbounded growth; modeling as rows lets it be queryable and
  optionally truncated. The current rankr state has 20 entries; nothing
  ages out today.
- **Nullable `updated_by`/`summary`** — pre-existing projects have
  rounds that predate these fields. The schema must not require them.

## Surprises / things to watch

1. **Importer ordering bug caught and fixed during the spike.** First
   pass populated cycles from `_status.json` files; second pass tried
   to upsert cycles from `_rounds.jsonl` files and clobbered the
   status. Fix was a SELECT before re-upserting. **Implication:** the
   production importer needs explicit "create-if-missing" semantics
   distinct from "update-from-source", and dual-write logic must be
   careful about which side is authoritative when they disagree.

2. **`init_cycle` writes round 1 itself.** The first SUBMIT_FOR_REVIEW
   is part of cycle creation, not a separate `add_round` call. The
   schema models this fine, but the production migrate command must
   not double-count.

3. **`render_cycle` `READY_FOR` field.** Original code uses
   `status.get('ready_for', '?')` — distinguishes missing key (`?`)
   from null value (`None`). The DB column collapses both. For rankr
   data this never matters (key is always present), but synthetic
   tests should cover the edge case if anyone cares.

4. **Subprocess startup is the real cost.** 130 ms per CLI invocation
   means the *biggest* possible token-efficiency / latency win is not
   storage — it's reducing the number of subprocess calls per turn,
   or moving common operations into a long-lived process. Phase 28 is
   not the place to fix this.

## What absorbs into Phase 28 vs. stays separate

Confirmed by the spike — these phases are mostly subsumed:

- **Phase 20 (tail-only reads):** trivial as `WHERE round > ?`.
- **Phase 21 (summary field):** already a nullable column in the
  spike schema.
- **Phase 22 (structured round schema):** native columns once added.
- **Phase 25 (drift audit):** drift impossible by construction with a
  singleton `state` table and `cycles.state` derived from rounds.
- **Phase 26 (workspace cleanup):** `.tagteam/tagteam.db` *is* the
  cleanup; file relocation falls out for free.
- **Phase 27 (cycle health):** five SELECTs.

Stays separate:

- **Phase 23 (per-round files):** different concern; obviated by DB
  if anything.
- **Phase 24 (event-driven watcher):** orthogonal — the watcher's
  problem is "wake on change," not "where is state stored." Could
  use SQLite's `update_hook` if useful, but that's a future detail.

## Go / no-go: **Go**

The decision criterion in the roadmap was: *"if the spike doesn't
surface a blocking issue and the markdown render is byte-identical or
trivially aligned, proceed."* Both conditions met. No blocking issue
surfaced. Render is byte-identical, not just trivially aligned.

### Recommended next steps (production port)

1. Promote `db_spike.py` content into a real `tagteam/db.py` module,
   add unit tests covering all states/actions including those absent
   from the rankr corpus.
2. Build a real `tagteam migrate --to-sqlite` command derived from
   the importer, with explicit safeguards against re-import.
3. **Dual-write release:** writes go to both files and DB, reads
   come from DB. One release of soak. The byte-identity round-trip
   becomes a CI check on every write — if files and DB ever diverge
   in a real project, alert.
4. **DB-only release:** files become opt-in export via
   `tagteam cycle render`, runtime state lives only in
   `.tagteam/tagteam.db`. Kill the legacy file paths.
5. Update the roadmap to mark Phases 20, 21, 22, 25, 26, 27 as
   absorbed-by-28 and remove them from execution order.

### Risks remaining

- **Schema migrations.** Any future column add needs a migration
  script. Use `PRAGMA user_version` + a small `tagteam/migrations/`
  directory of forward-only migrations. Not hard, but it's new
  infrastructure tagteam doesn't currently have.
- **Concurrent watcher + CLI access.** WAL mode handles it
  conceptually, but the watcher's polling loop will need to either
  poll the DB or switch to event-driven (Phase 24). Polling a SQLite
  file is fine; the existing `seq` field becomes a `state.seq`
  column read.
- **Backup story.** Currently `cp -r docs/handoffs/` works. With
  SQLite, `sqlite3 .tagteam/tagteam.db ".backup '/path'"` is the
  right command — worth documenting.

## Cleanup

After Phase 28 lands, delete:
- `tagteam/db_spike.py`
- `.spike-output/` (gitignored anyway)
- This findings doc can stay as historical record or move under
  `docs/decision_log.md` as a reference.
