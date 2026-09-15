# Phase 55: Measure the Loop

## Status
- [x] Planning
- [x] Approved: plan round 3 (2026-09-14)
- [x] Implementation: phase/measure-the-loop
- [ ] Implementation Review
- [ ] Complete

## Summary
Tagteam records a lot about its own loop and summarizes almost none of it.
When a phase closes, nothing says what it took: how many rounds, how many gate
bounces, how long each role held the turn, what the headless turns consumed.
The roadmap status lines record "gate 1,894 passed" by hand and nothing else.
Every later decision in the 2026-09-14 research pack (review bench, model
policy by kind, subagent kit, reviewer effort) assumes this measurement exists.

This phase adds the measurement, read-only, from data tagteam already owns:

1. `tagteam report --phase P [--json]`: a phase cost block (rounds, bounces,
   gate time, per-role elapsed turn time, start-to-approve wall clock, the
   tokens stored under the phase, and how many workflow turns those rows can
   be matched to).
2. `tagteam usage --by role|cycle|model|kind`: new roll-ups, with the cost
   column removed from the default text view.
3. Per-model token capture for Claude turns (`modelUsage`), so a Haiku
   sub-call inside a Fable turn is counted under Haiku, not hidden in the
   parent row.
4. No dollar figures in any human-facing view (CLI text, cockpit, hub).
   `--json` keeps `cost_usd` for anyone on API keys.

Source of scope: `docs/research/2026-09-14-better-tagteam/` (index "Phase 55",
`02-agents-and-models.html` §6 step 1, `03-features.html` #2). Arbiter rulings
on 2026-09-14, before this plan:
- **Data source:** only data tagteam already stores. No reading of Claude
  Code transcripts (`~/.claude/projects/`) or Codex sessions
  (`~/.codex/sessions/`); that would be its own phase.
- **Split:** `tagteam grade` (review-quality labels) is its own later phase.
- **Report is read-only:** printed by a command, not written into the phase
  doc or decision log on approval. The lead pastes it into the closeout
  commit, the way roadmap status lines are written today.
- **Step 0** (reviewer `--effort medium`) is skipped: the reviewer is Codex
  and turns currently run interactively, so headless args do not apply.

## Scope
In:
1. `tagteam/report.py` (new): pure `phase_report(project_root, phase)` →
   dict, plus text rendering; `tagteam report --phase P [--json]`.
2. `tagteam/usage.py`: `--by role|cycle|model|kind` (repeatable; default
   stays role + cycle), cost column and `cost=` summary dropped from text
   output, `cost_usd` / `cost_known_turns` kept in `--json`.
3. Schema v10, additive nullable `usage` columns (same `ALTER TABLE` pattern
   as v7's `kind`): `model_usage_json` (Claude `modelUsage`, token fields
   only) and `target_phase` / `target_type` / `target_round` (the cycle entry
   the turn was dispatched to produce). The four `add_usage` writers
   (headless turn, panel lens, briefer, lead chat) pass what they have.
   **Scope addition (plan round 2):** target identity is new capture, needed
   because the stored `phase/type/round` is the owed state at dispatch, not
   the submission (see "Turn coverage"). Old rows are not reinterpreted.
4. One read path that never creates or migrates: `db.connect_for_read`, and a
   keyword-only `conn=` on `cycle.read_status` / `read_rounds` so the report
   and `usage` read through that one connection (see "Read path").
5. `cli.py`: dispatch `report`; add `report` to `READ_ONLY_COMMANDS` and
   `_read_only_summary`; help text.
6. Cockpit and hub: remove the cost column from the usage table, the cost in
   the per-turn tooltip, and the `$` part of the hub burn chip.
7. `README.md` command list; `docs/workflows.md` / `tagteam/data/workflows.md`
   one paragraph: closing a phase includes `tagteam report --phase P`.

Out: reading any file outside the project (transcripts, Codex sessions,
`~/.codex/config.toml`); a rate-limit history table (the `rate_limits` table
keeps the latest row per provider+kind and stays that way); Codex model
detection (its `--json` stream carries no model name; rows stay `model`
null); estimating Codex cost; backfilling target identity or inferring it
for old start-command turns; `tagteam grade`; any write on approval; the
review bench, model policy or subagent kit (Phases 56–58); a cockpit
"phase report" card; backfilling `model_usage_json` for old rows.

## Technical Approach

### Phase report (`tagteam report --phase P`)
Inputs, all already in the project:
- **Rounds:** `cycle.read_rounds(phase, "plan"|"impl", conn=…)`, the same
  DB-first / file-fallback reader the CLI and gate use, given the report's
  read connection. Entries carry `ts`, `role`, `action`, `round`,
  `updated_by`.
- **Cycle status:** `cycle.read_status(…, conn=…)`: `state`, final `round`,
  `lead`, `reviewer`.
- **Gate runs:** `db.gates_for_cycle(conn, phase, type)` (`status`,
  `duration_s`, `attempt`, `round`).
- **Usage:** two selections from one connection, merged by row `id` so a
  row picked by both is held once: `db.get_usage(conn, phase=P)` (stored
  phase, for consumption) and `db.get_usage(conn, target_phase=P)` (new
  keyword; empty when the column is absent, for coverage of turns another
  phase's state dispatched). Consumption uses only the first; coverage uses
  the rule-appropriate candidates below.
- **Rate limits:** `db.latest_rate_limits(conn)`, shown only for rows whose
  `ts` falls inside the phase span, labelled "last signal", never
  "consumed" (the table is a snapshot, not a history).

Derived figures, per cycle type and for the phase:
- `rounds` (final round), `outcome` (approved / escalated / open / aborted),
  `reviewer_requests` (REQUEST_CHANGES count), `gate_bounces`
  (GATE_BOUNCE entries), `gate_runs` and `gate_minutes` (sum of
  `duration_s` over decided runs), `amendments`, `escalations`,
  `interjections` where present.
- **Turn time by role**, from consecutive entry timestamps: a lead span ends
  at each lead submission and starts at the previous entry that handed the
  turn to the lead (reviewer verdict, gate bounce, or, for round 1, nothing:
  round 1 authoring time is unknown and reported as such). A gate span runs
  from submission to the gate entry. A reviewer span runs from the entry that
  handed it the turn (gate pass, or submission when there is no gate) to the
  verdict. These are **elapsed** times and include waiting for a human to
  relay the turn; the text output says so.
- **Wall clock:** first plan entry → impl APPROVE (or last entry if not
  approved), plus the idle gap between plan approval and the first impl
  submission, shown separately as "implementation before first submit".
- **Stored consumption** and **turn coverage** are reported separately, as
  defined in the next two sections. Neither ever says why data is missing:
  no rows means `no usage rows`, not "interactive".

Missing data degrades per field, never the whole report: entries without
`ts` (legacy markdown cycles) → timing `unknown`; no DB → rounds and timing
from files only, usage sections `no database`; no impl cycle yet → plan block only.
Unknown phase (no cycle files, no DB rows) → one error line, exit 1.

Text output is a compact block meant to paste into a phase doc's Closeout
section. Sketch (numbers illustrative):

```
Phase 53 legacy-diagnostics — approved (plan r2, impl r2)
  plan   2 rounds · 1 change request · 0 bounces
  impl   2 rounds · 1 change request · 0 bounces · gate 2 runs, 10.3 min
  time   start→approve 1h 04m · lead 31m · reviewer 4m · gate 10m (elapsed; includes relay wait)
  usage  no usage rows · turns matched 0 of 8 (unmatched 8)
```

`--json` returns the same figures, machine-shaped, with `null` for unknown.
No dollar field in either form. Allowed under `TAGTEAM_READ_ONLY=1`; in
either mode it creates, migrates and writes nothing (see "Read path").

### Stored consumption
Rows with `usage.phase == P` (the stored, owed-state phase), grouped by
stored type, role, kind and model. Token sums include only rows whose token
fields are non-null; rows with all four token fields null (a turn whose
stream could not be parsed, `headless_usage_unparsed`) and failed or
cancelled rows are counted separately: `rows 14 · with tokens 11 · without
token data 3 (failed 1)`. The label says "stored under this phase" because
a start-command turn is stored under the *previous* cycle's phase/type
(see below); the report does not move those rows.

### Turn coverage
The denominator is the set of **workflow turns** in the phase's cycles: each
lead `SUBMIT_FOR_REVIEW` entry and each reviewer verdict entry
(`APPROVE`/`REQUEST_CHANGES`/`ESCALATE`/`NEED_HUMAN`). Gate entries,
amendments and arbiter rulings are not turns. Each turn gets exactly one
status, so the parts always sum to the denominator. The status is about
**token coverage only**; execution outcome is a separate flag:

- `matched`: at least one attributable row has non-null tokens, whatever
  that row's `status` (a process can write its cycle entry and then exit
  non-zero; its tokens were still recorded).
- `matched, no token data`: attributable rows exist, none has tokens.
- `unmatched`: the rule can be applied and finds no row.
- `unknown`: no exact rule exists for this turn's historical rows.

Independently, a turn with any attributable row whose `status` is not `ok`
(`nonzero_exit`, `cancelled`, `timeout`, anything else) carries
`non_ok_rows: K`; the text adds `· turns with non-ok rows J`. This count is
not part of the partition and can overlap any status except `unmatched` and
`unknown`.

A turn is one unit however many rows match it: retries (several rows for one
dispatch) and panel lenses (one row per lens) never raise the count above
one, and extra rows are listed as `rows per matched turn` for context.

Attribution rules, in order:
1. **Target identity (rows written from v10 on).** A row with non-null
   `target_phase/type/round` matches the turn with that phase, type, round
   and role, and only that turn: rule 2 never applies to it, so a start row
   stored under phase A with target B cannot match a submission in A. Panel lens rows (`kind = panel:*`) match the reviewer turn of
   their round; a reviewer entry with `updated_by` ending in ` panel` is
   matched only by panel rows, any other reviewer entry only by `kind` null
   rows.
2. **Owed-state identity (rows with null target columns only).** Stored identity
   is the state at dispatch (`headless.snapshot_identity`): a reviewer row
   `(P, T, N, reviewer)` → the reviewer verdict of round N; a lead row
   `(P, T, N, lead)` → the lead submission of round **N+1** (a lead owed a
   turn after a verdict or gate bounce at N submits N+1).
3. **Round-1 lead submissions no target row matches → `unknown`.** Round 1 is
   created by a start command, whose row is stored under whatever cycle the
   state named at dispatch (the previous phase, or the plan cycle for an
   impl start). The report cannot tell that row from an ordinary lead turn
   on that cycle, so it neither claims nor steals it. For the same reason a
   lead row under rule 2 whose round N+1 has no lead submission in that
   cycle is shown as `unattributed lead rows: K`, not dropped silently.

Text: `turns matched 5 of 8 · no token data 1 · unmatched 1 · unknown 1 ·
turns with non-ok rows 2`.
JSON: per turn `{type, round, role, status, row_ids, non_ok_rows}` plus the
counts.

### Read path
Today `usage_command` calls `db.connect`, which creates `.tagteam/`, the DB
and migrates it in ordinary mode; `cycle.read_status` / `read_rounds` each
open their own `db.connect`. Under `TAGTEAM_READ_ONLY=1`, `db.connect`
routes to `read_only_connect(require_current_schema=True)`, which would
reject a v9 DB after this phase's bump. The report and `usage` get one
scoped path instead:

- `db.connect_for_read(project_dir) -> (conn | None, note | None)`: calls
  `read_only_connect(project_dir, require_current_schema=False)` in **both**
  modes (the hub already reads other projects this way, `hub_api.py`).
  `DatabaseMissing` → `(None, "no database")`; `WalWithoutIndex` or another
  `ReadOnlyError` → `(None, <its detail>)`; nothing is created, migrated or
  checkpointed. `db.connect` and its guard are unchanged for every other
  command.
- `cycle.read_status` / `read_rounds` gain keyword-only `conn=None`. When
  given, `_read_status_from_db` / `_read_rounds_from_db` use it and do not
  close it; the DB-first / file-fallback / `db_invalid` logic is otherwise
  identical. When the report has no connection it calls them with a
  sentinel that skips the DB step (files only), never with `conn=None`,
  which would open `db.connect`. Default callers are untouched.
- Older schemas: `get_usage` selects only the columns present
  (`PRAGMA table_info(usage)`), returning `None` for absent ones; a missing
  `usage` / `gates` / `rate_limits` table → that section reports
  `not available in this database (schema vN)`. Rounds and status readers
  need tables present since schema v1.
- `usage_command` switches to `connect_for_read`. Behaviour change, stated:
  an ordinary `tagteam usage` no longer creates or migrates a DB (it prints
  the no-rows message plus the note).

### Usage roll-ups (`tagteam usage --by …`)
`aggregate(rows, by=("role", "cycle"))` gains two keys:
- `model`: a row with `model_usage_json` contributes one bucket per model
  entry in it, using that entry's token counts; a row without it contributes
  its row totals under `row.model`, or `<provider> (model not reported)` when
  `model` is null (all Codex rows today). A turn is counted once per model it
  used, so model buckets' `turns` can exceed the row count; totals still come
  from rows, and the text says "turns using this model".
- `kind`: `row.kind` or `turn` when null (`conversation`, `panel:<lens>`
  already exist; briefer rows are `role=briefer`, kind null → `turn`).

`--by` is repeatable and replaces the default blocks when given. Invalid
value → usage error, exit 1. The per-turn table and the summary lines drop
`cost`; `--json` is unchanged apart from the new `by_model` / `by_kind` keys
(present only when requested, so existing JSON consumers see the same
shape by default).

### `modelUsage` capture
`_usage_claude` adds `model_usage` = `{model: {input_tokens, output_tokens,
cache_read_tokens, cache_write_tokens}}` mapped from `inputTokens`,
`outputTokens`, `cacheReadInputTokens`, `cacheCreationInputTokens`. Only
those four integer fields are kept (no `costUSD`, no other keys), integers
only, at most 16 model entries, model names capped at 128 characters;
anything else is dropped rather than stored. Serialized to
`model_usage_json` by `add_usage`; `get_usage` returns it parsed (`None`
when absent or unparseable). Codex: no change (`model_usage` absent).

Verified against a real stream in this repo
(`.tagteam/turns/safe-framework-migration_impl_r3_lead_…events.jsonl`): a
Fable lead turn's `modelUsage` has two entries, `claude-fable-5-1` and
`claude-haiku-4-5-20251001`; today the row records only the first model.

### Schema v10
`SCHEMA_VERSION = 10`; migration adds `model_usage_json TEXT`,
`target_phase TEXT`, `target_type TEXT`, `target_round INTEGER` to `usage`
when missing, sets `user_version = 10`. `_USAGE_COLS` and `add_usage`'s
allowed fields include them. Writers: the headless turn engine passes
`ident.target_phase/type/round` (already computed for verification); panel
lenses pass their submission's phase/type/round (a panel turn is the
reviewer turn of that round); the briefer and lead chat leave target null
(not workflow turns). Read commands still never migrate: a v9 DB is read
through `connect_for_read` with the new columns absent.

### Cockpit and hub
- `cockpit.js`: the usage bucket table loses the `cost` column; the per-turn
  tooltip drops `, $x`.
- `hub.js`: the burn chip drops `· $…`.
- `cockpit_api.py` / `hub_api.py` payloads are unchanged (they already carry
  `cost_usd`; the arbiter's rule is about what humans see).

## Files
- New: `tagteam/report.py`, `tests/test_report.py`.
- Modified: `tagteam/usage.py`, `tagteam/headless.py` (`_usage_claude`,
  `_record_usage` target identity), `tagteam/panel.py` (target identity),
  `tagteam/briefer.py`, `tagteam/lead_chat.py` (pass `model_usage_json`
  through), `tagteam/db.py` (v10, `connect_for_read`, column-tolerant
  `get_usage`), `tagteam/cycle.py` (keyword-only `conn` on `read_status` /
  `read_rounds` and their DB helpers), `tagteam/cli.py` (dispatch, read-only allowlist, help),
  `tagteam/data/web/cockpit.js`, `tagteam/data/web/hub.js`, `README.md`,
  `tagteam/data/workflows.md` + `docs/workflows.md`, `docs/roadmap.md`, this
  plan; tests: `tests/test_usage.py` (new; the roll-ups have no dedicated
  file today), `tests/test_headless.py` (`_usage_claude`), `tests/test_db.py`
  (v10), `tests/test_cycle.py` (`conn=` reads), `tests/test_panel.py`,
  `tests/test_cockpit_activity.py` (static-asset cost assertions).
- Not modified: cycle state machine and write paths, `db.connect`, gate, contract (`SKILL.md`), watcher,
  templates.

## Success Criteria and Verification
- **Report, full data:** a temp project with plan + impl JSONL (timestamped
  submissions, one REQUEST_CHANGES, one GATE_BOUNCE, gate PASS entries,
  APPROVE), matching `gates` rows and usage rows → rounds, change requests,
  bounces, gate runs/minutes, per-role elapsed times and start→approve equal
  hand-computed values; stored tokens grouped by role and model.
- **Coverage, owed-state rows (pre-v10 shape):** reviewer row `(P, impl, 2,
  reviewer)` matches the round-2 verdict; lead row `(P, impl, 1, lead)`
  matches the round-**2** submission and not round 1; round-1 lead
  submission → `unknown`; a lead row stored under the plan cycle at the
  impl start is counted as stored consumption of the plan cycle and does
  not match any impl turn.
- **Coverage, target rows (v10 shape):** a start-command row with target
  `(P, impl, 1)` stored under `(P, plan, 2, lead)` matches impl round 1
  only.
- **Coverage, cross-phase start:** a row stored under `(A, impl, 3, lead)`
  with target `(B, plan, 1)`, and phase A has an impl round-4 lead
  submission. `report --phase B` loads it through `target_phase` and B's
  round-1 submission is `matched`; `report --phase A` counts it in A's
  stored consumption and A's round-4 submission is `unmatched` (no fallback
  for a row with a target); a row selected by both queries is held once.
- **Coverage, retries and panels:** three rows (two failed, one ok) for one
  reviewer turn → one `matched` turn, `rows per matched turn` 3,
  `non_ok_rows` 2; three
  `panel:*` rows for a panel verdict → one turn; a panel verdict with only
  `kind` null rows → `unmatched`.
- **Coverage, null tokens:** a row with all token fields null → `matched, no
  token data`, excluded from token sums, counted in `without token data`.
- **Coverage, failed rows:** a turn whose only attributable row is
  `nonzero_exit` with tokens → `matched`, `non_ok_rows` 1; a turn with one
  `cancelled` row with tokens plus one `ok` row with null tokens →
  `matched`, `non_ok_rows` 1; a turn with only a `cancelled` null-token row
  → `matched, no token data`, `non_ok_rows` 1.
- **Coverage invariant:** for every fixture, matched + no-token + unmatched
  + unknown == number of workflow turns, matched ≤ turns, and turns with
  non-ok rows ≤ matched + no-token.
- **Report, no rows:** same cycles, no usage rows → `no usage rows` (no
  "interactive"); every other figure still present.
- **Report, degraded:** entries without `ts` → timing `unknown`, counts
  intact; no DB (`DatabaseMissing`) → files-only report, exit 0; plan only
  → plan block only; unknown phase → exit 1, one line.
- **Report, this repo:** `tagteam report --phase legacy-diagnostics` run
  once against the real Phase 53 cycles; the numbers are checked by hand
  against `cycle rounds` timestamps and pasted into the impl submission as
  evidence.
- **Creates nothing, ordinary mode:** `report` on a files-only project (no
  `.tagteam/`) → report from files, and afterwards no `.tagteam/`, no DB, no
  sidecars; `report` and `usage` on a v9 project → correct output, and the
  DB's `user_version`, columns, file bytes and directory listing are
  unchanged (the no-`-wal` fixture is checked byte-for-byte).
- **Creates nothing, read-only mode:** the same two projects with
  `TAGTEAM_READ_ONLY=1` → same results; `report` appears in the read-only
  summary; the Phase 50 dispatch-coverage test still classifies every
  command; `cycle rounds` under read-only on a v10 DB behaves as before.
- **Readers unchanged by default:** `read_status` / `read_rounds` without
  `conn` pass the existing cycle tests; with `conn`, the connection is still
  open afterwards; with the files-only sentinel, no `db.connect` call
  (patched to raise).
- **No dollars:** default `usage` text and `report` text/JSON contain no
  `$` and no `cost`; `usage --json` still contains `cost_usd`; cockpit and
  hub JS no longer render cost (asserted by reading the shipped assets).
- **Roll-ups:** `--by model` splits a two-model `model_usage_json` row into
  two buckets with the right token counts; a row without it falls back to
  `row.model`; a Codex row lands in `codex (model not reported)`; `--by kind`
  maps null → `turn`; invalid `--by` → exit 1; default JSON shape unchanged.
- **Capture:** `_usage_claude` on a fixture stream with two `modelUsage`
  entries → four token fields per model, no `costUSD`; oversized / wrong-type
  / too many entries → dropped, turn row still written.
- **Schema:** v9 DB upgrades to v10 (on a writing command) with the four
  columns added and existing rows intact; fresh DB is v10; `get_usage` on a
  v9 and a v6 (no `kind`) connection returns rows with absent columns as
  `None`; a headless lead turn fixture writes target identity from
  `TurnIdentity`, a panel lens writes its round.

Focused tests while working; the on_submit gate provides the recorded full
suite for the impl submission. Plan revisions need document checks only.

## Risks and Review Focus
- **Elapsed is not effort** (accepted in round 1): the label stays.
- **Model buckets count a turn once per model** (accepted in round 1).
- **Target identity is new capture.** It makes coverage exact from v10 on
  and is the identity the Phase 56 bench will replay. Historical start
  turns stay `unknown`; the report shows how many.
- **`usage` stops creating/migrating the DB** in ordinary mode. Nothing
  depends on `usage` for creation (every write path opens `db.connect`).
- **Rate-limit "last signal" is thin.** It is only the latest row per kind.
  A history table is deliberately out of scope; the report must never imply
  a window percentage it does not have.
- **Command name `report`.** Alternative: `tagteam usage --phase P --report`.
  Proposed: a separate command, because most of the block (rounds, bounces,
  gate, timing) is not usage data and must work with no DB.
- **Removing cost from the default `usage` text** changes output someone may
  read by eye; `--json` keeps the field, so scripts are unaffected.

## Plan revision log
Round 2 (reviewer round 1): (1) token coverage rewritten around the stored
owed-state identity — stored consumption separated from turn coverage;
four-way turn status with a sum invariant; retries and panel rows count one
turn; null-token rows never count as recorded; round-1 lead turns without
target identity `unknown`; "interactive" wording removed; explicit scope
addition of v10 `target_phase/type/round` capture, old rows not
reinterpreted. (2) `db.connect_for_read` + keyword-only `conn` on the cycle
readers so report and usage never create or migrate in either mode and read
v9 (and older) schemas; ordinary-mode files-only and v9 checks added;
Files list updated (`cycle.py`, `db.py`, `panel.py`).
Round 3 (reviewer round 2): (1) coverage candidates also selected by
`target_phase` (new `get_usage` keyword), merged by id with the stored-phase
selection; consumption stays stored-phase only; rows with a target never
fall back to owed-state matching; cross-phase start fixture. (2) status
partition is token coverage only (`matched` no longer requires `ok`);
execution outcome is a separate `non_ok_rows` flag outside the invariant;
failed-row fixtures.

## Implementation notes
- `tagteam/report.py`: `phase_report` + `render_text` + `report_command`.
  Durations print as `Ns` / `Mm SSs` / `Hh MMm`. Interjection counts from the
  plan's derived-figures list are not included: interjections live in their
  own table, not in the round entries, and nothing else in the block needs
  them.
- `usage` refuses (exit 2, one line) when rows exist but the DB cannot be
  read without writing (a WAL without its index), instead of printing an
  empty table; no DB at all is still "No usage rows yet", exit 0. The Phase 50
  snapshot matrix now includes `report` and expects this for `usage`.
- `_usage_claude` now ignores a non-dict `modelUsage` rather than losing the
  whole usage record (found by the malformed-stream test).
- `db.get_usage` returns every `_USAGE_COLS` key (None when absent) plus a
  parsed `model_usage`.
- Check against the real Phase 53 cycles (`tagteam report --phase
  legacy-diagnostics`, hand-computed from `docs/handoffs/legacy-diagnostics_*`
  and the `gates` rows): start→approve 30m 01s (03:10:21→03:40:22),
  implementation before first submit 10m 31s, lead 4m 00s (95 s + 145 s, two
  round-1 spans unknown), reviewer 4m 54s (72 + 40 + 120 + 63 s), gate 10m 35s
  (316.9 + 318.0 s), turns 8: unknown 2, unmatched 6 (no usage rows). This
  repo's DB stayed at `user_version` 9 after `report` and `usage`.
