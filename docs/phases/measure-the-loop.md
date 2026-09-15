# Phase 55: Measure the Loop

## Status
- [x] Planning
- [ ] Approved
- [ ] Implementation
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
   gate time, per-role turn time, start-to-approve wall clock, and tokens
   where headless turns recorded them).
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
3. Schema v10: one nullable `usage.model_usage_json` column (additive
   `ALTER TABLE`, same pattern as v7's `kind`). `_usage_claude` stores the
   stream result's `modelUsage` there (token fields only; see below).
   The four `add_usage` writers (headless turn, panel lens, briefer, lead
   chat) pass it through.
4. `cli.py`: dispatch `report`; add `report` to `READ_ONLY_COMMANDS` and
   `_read_only_summary`; help text.
5. Cockpit and hub: remove the cost column from the usage table, the cost in
   the per-turn tooltip, and the `$` part of the hub burn chip.
6. `README.md` command list; `docs/workflows.md` / `tagteam/data/workflows.md`
   one paragraph: closing a phase includes `tagteam report --phase P`.

Out: reading any file outside the project (transcripts, Codex sessions,
`~/.codex/config.toml`); a rate-limit history table (the `rate_limits` table
keeps the latest row per provider+kind and stays that way); Codex model
detection (its `--json` stream carries no model name; rows stay `model`
null); estimating Codex cost; `tagteam grade`; any write on approval; the
review bench, model policy or subagent kit (Phases 56–58); a cockpit
"phase report" card; backfilling `model_usage_json` for old rows.

## Technical Approach

### Phase report (`tagteam report --phase P`)
Inputs, all already in the project:
- **Rounds:** `cycle.read_rounds(phase, "plan"|"impl")`, the same reader the
  CLI and gate use (JSONL, DB fallback). Entries carry `ts`, `role`,
  `action`, `round`.
- **Cycle status:** `state`, final `round`, `lead`, `reviewer`.
- **Gate runs:** `db.gates_for_cycle(conn, phase, type)` (`status`,
  `duration_s`, `attempt`, `round`).
- **Usage:** `db.get_usage(conn, phase=phase)`, grouped by type, role and
  model.
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
- **Tokens** (only when usage rows exist): by role and by model, input /
  output / cache read / cache write, turn count, failed turns. When no rows
  exist for a cycle: `tokens: not recorded (turns ran interactively)`.
  When some but not all reviewer/lead entries have a matching row, the block
  says `tokens: partial (N of M turns recorded)`; matching is by
  phase+type+round+role, which the existing rows already carry.

Missing data degrades per field, never the whole report: entries without
`ts` (legacy markdown cycles) → timing `unknown`; no DB → rounds and timing
from files only, tokens `not recorded`; no impl cycle yet → plan block only.
Unknown phase (no cycle files, no DB rows) → one error line, exit 1.

Text output is a compact block meant to paste into a phase doc's Closeout
section. Sketch (numbers illustrative):

```
Phase 53 legacy-diagnostics — approved (plan r2, impl r2)
  plan   2 rounds · 1 change request · 0 bounces
  impl   2 rounds · 1 change request · 0 bounces · gate 2 runs, 10.3 min
  time   start→approve 1h 04m · lead 31m · reviewer 4m · gate 10m (elapsed; includes relay wait)
  tokens not recorded (turns ran interactively)
```

`--json` returns the same figures, machine-shaped, with `null` for unknown.
No dollar field in either form. Read-only: allowed under
`TAGTEAM_READ_ONLY=1`, opens the DB read-only the way `usage` does
(`DatabaseMissing` → files only), creates nothing.

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
`SCHEMA_VERSION = 10`; migration adds `usage.model_usage_json TEXT` if the
column is missing, sets `user_version = 10`. `_USAGE_COLS` and `add_usage`'s
allowed fields include it. Read-only mode still never migrates (Phase 50):
a v9 DB read under `TAGTEAM_READ_ONLY=1` returns rows without the column,
and `get_usage` treats it as absent.

### Cockpit and hub
- `cockpit.js`: the usage bucket table loses the `cost` column; the per-turn
  tooltip drops `, $x`.
- `hub.js`: the burn chip drops `· $…`.
- `cockpit_api.py` / `hub_api.py` payloads are unchanged (they already carry
  `cost_usd`; the arbiter's rule is about what humans see).

## Files
- New: `tagteam/report.py`, `tests/test_report.py`.
- Modified: `tagteam/usage.py`, `tagteam/headless.py` (`_usage_claude`,
  `_record_usage`), `tagteam/panel.py`, `tagteam/briefer.py`,
  `tagteam/lead_chat.py` (pass `model_usage_json` through), `tagteam/db.py`
  (v10), `tagteam/cli.py` (dispatch, read-only allowlist, help),
  `tagteam/data/web/cockpit.js`, `tagteam/data/web/hub.js`, `README.md`,
  `tagteam/data/workflows.md` + `docs/workflows.md`, `docs/roadmap.md`, this
  plan; tests: `tests/test_usage.py` (new; the roll-ups have no dedicated
  file today), `tests/test_headless.py` (`_usage_claude`), `tests/test_db.py`
  (v10), `tests/test_cockpit_activity.py` (static-asset cost assertions).
- Not modified: cycle state machine, gate, contract (`SKILL.md`), watcher,
  templates.

## Success Criteria and Verification
- **Report, full data:** a temp project with plan + impl JSONL (timestamped
  submissions, one REQUEST_CHANGES, one GATE_BOUNCE, gate PASS entries,
  APPROVE), matching `gates` rows and usage rows → rounds, change requests,
  bounces, gate runs/minutes, per-role elapsed times and start→approve equal
  hand-computed values; tokens grouped by role and model.
- **Report, interactive phase:** same cycles, no usage rows →
  `tokens: not recorded`; every other figure still present.
- **Report, partial:** usage rows for 2 of 4 turns → `partial (2 of 4)`.
- **Report, degraded:** entries without `ts` → timing `unknown`, counts
  intact; no DB (`DatabaseMissing`) → files-only report, exit 0; plan only
  → plan block only; unknown phase → exit 1, one line.
- **Report, this repo:** `tagteam report --phase legacy-diagnostics` run
  once against the real Phase 53 cycles; the numbers are checked by hand
  against `cycle rounds` timestamps and pasted into the impl submission as
  evidence.
- **Read-only:** `report` and `usage --by model` succeed with
  `TAGTEAM_READ_ONLY=1`, create no `.tagteam/` and no DB; `report` appears
  in the read-only summary; the Phase 50 dispatch-coverage test still
  classifies every command.
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
- **Schema:** v9 DB upgrades to v10 with the column added and existing rows
  intact; v10 on a fresh DB; read-only open of a v9 DB does not migrate and
  `get_usage` works.

Focused tests while working; the on_submit gate provides the recorded full
suite for the impl submission. Plan revisions need document checks only.

## Risks and Review Focus
- **Elapsed is not effort.** In interactive mode the lead span includes the
  time before the arbiter relays the turn. The report labels it elapsed and
  does not try to subtract waits it cannot see. Is the label enough, or
  should per-role time be omitted when no usage row confirms the turn ran
  headless?
- **Model buckets double-count turns** by design (a turn that used two models
  counts in both). Totals stay row-based. Reviewer: is "turns using this
  model" clear enough?
- **Rate-limit "last signal" is thin.** It is only the latest row per kind.
  A history table is deliberately out of scope; the report must never imply
  a window percentage it does not have.
- **Command name `report`.** Alternative: `tagteam usage --phase P --report`.
  Proposed: a separate command, because most of the block (rounds, bounces,
  gate, timing) is not usage data and must work with no DB.
- **Removing cost from the default `usage` text** changes output someone may
  read by eye; `--json` keeps the field, so scripts are unaffected.

## Plan revision log
(none yet)
