# Plan Review Cycle: phase-28-stage-2-db-readers

- **Phase:** phase-28-stage-2-db-readers
- **Type:** plan
- **Date:** 2026-05-03
- **Lead:** Claude
- **Reviewer:** Codex

## Round 1

### Lead

**Action:** SUBMIT_FOR_REVIEW

Plan: retarget runtime cycle reads from filesystem to the SQLite shadow DB, removing the read-path dependency on _rounds.jsonl / _status.json. Once readers are DB-backed, flip STEP_B_READERS_READY = True in tagteam/migrate.py so `migrate --to-step-b` is unblocked. Step A continues writing both file + DB; Stage 2 only changes WHICH side runtime reads consume.

Background — why this exists
- Step B (PR #6, merged) added `migrate --to-step-b` which moves _rounds.jsonl/_status.json from docs/handoffs/ to .tagteam/legacy/ and renders .md from the DB. We tried to activate it on this repo and discovered every file-side reader (CLI cycle status/rounds, parser, divergence, repair _check_all_files) returned "no cycle found" because the files had moved. Activation reverted; migrate command guarded behind STEP_B_READERS_READY=False until Stage 2 lands. This is that Stage 2.

Read-path inventory (sites to retarget)
- tagteam/cycle.py — `_status_path`, `_rounds_path` helpers and any callers of read_status / read_rounds. Replace file reads with `db.list_cycles` / `db.get_cycle` / a new `db.get_rounds(cycle_id)`.
- tagteam/parser.py — `parse_jsonl_rounds` (line 168) and `read_cycle_rounds` (line ~250). Currently dispatches JSONL-first then markdown fallback. New behavior: try DB first, fall back to JSONL only as a transitional safety net (could be removed in Stage 3).
- CLI commands `cycle status`, `cycle rounds`, `cycle render` — should consume the new DB-backed cycle.py helpers, not parser.py directly.
- Web server (server.py) and TUI consume parser output — should be transparent if parser.py's API surface stays the same.

Divergence & repair handling — open question worth a position
- divergence.py:180-181 reads `<phase>_<type>_rounds.jsonl` from docs/handoffs/ to compare against the DB. After Step B activation those files are in .tagteam/legacy/. Two options:
  (A) Divergence checks `docs/handoffs/` first, then `.tagteam/legacy/`. Symmetric with the future "either path is canonical depending on activation."
  (B) Divergence becomes a different shape post-Step-B: compare DB output to the .md render (since .md is the new git-visible artifact).
  My read: ship (A) for Stage 2 (smaller change, preserves divergence semantics). Defer (B) to a future "Step B / divergence v2" phase if/when needed.
- repair._check_all_files has the same path issue. Same fix.

Dual-write contract (unchanged from Step A)
- All cycle writes still produce both file artifacts (in docs/handoffs/) AND a shadow DB write. Stage 2 doesn't touch the write path.
- Activation gate (TAGTEAM_STEP_B=1 + migrate --to-step-b) is what moves files to .tagteam/legacy/ and starts the .md auto-render. This stays gated behind STEP_B_READERS_READY=True (flipped in this cycle).
- Files become advisory after Step B activation: they exist in .tagteam/legacy/ for divergence-comparison and rebuild-from-files (repair, migrate), but runtime reads no longer touch them.

What this DOES NOT include (deferred to Stage 3 or later)
- Removing the file-side writes (Stage 3: DB-only).
- Removing parser.py's JSONL fallback path.
- Removing legacy file moves in `migrate --to-step-b` (the move stays — just becomes safe).
- Any changes to `divergence` or `repair` semantics beyond path inventory.

New tests
- test_post_migration_reads_still_work (the test that should have caught the activation regression): after a successful `migrate --to-step-b` (with STEP_B_READERS_READY monkeypatched True), `tagteam cycle status` and `tagteam cycle rounds` both return the migrated cycle's data via the DB-backed path. Asserts the bug we just hit is regression-guarded.
- test_db_backed_reads_match_jsonl_reads — for any project state, the new DB-backed `read_status` / `read_rounds` produces the same dicts the file-backed versions did. Parameterize across populated_project + the rankr corpus if available.
- test_parser_db_first_jsonl_fallback — parser tries DB first, falls back to JSONL only when DB has no row for the cycle (transitional).
- test_divergence_finds_legacy_files — divergence works when files are in .tagteam/legacy/ instead of docs/handoffs/.
- test_step_b_readers_ready_flipped — sanity check that `_step_b_readers_ready()` returns True (one-line; guards against accidental revert).

Rollout
- This phase ships the read-path retarget + the STEP_B_READERS_READY flip. Does NOT itself run `migrate --to-step-b` on this repo. Operator (you) runs migrate after the PR merges, observing soak.

Files touched (estimated)
- tagteam/cycle.py — DB-backed read helpers + retire/wrap file readers
- tagteam/parser.py — DB-first dispatch
- tagteam/divergence.py — also-look-in-.tagteam/legacy/
- tagteam/repair.py — same path fix in _check_all_files
- tagteam/migrate.py — flip STEP_B_READERS_READY = True
- tagteam/db.py — possibly add `get_rounds(conn, cycle_id) -> list[dict]` if not already present
- tests/* — five new test files / additions per the test list above

Open questions for reviewer (take explicit positions, do not punt)
1. Divergence path: option (A) check both dirs, or (B) compare DB to .md render? I propose A.
2. Should parser.py's JSONL fallback be removed in this cycle, or kept as transitional safety until Stage 3?
3. Is db.get_rounds(cycle_id) the right shape, or should we expose a higher-level db.read_cycle(phase, cycle_type) -> {status, rounds}?
4. Are there reads I missed in the inventory? Specifically: handoff_reader.py, review_replay.py — do those need updates, or are they purely render-side consumers?
5. Should this phase split into two cycles (read-retarget first, divergence/repair path-fix second), or land as one?

### Reviewer

**Action:** REQUEST_CHANGES

Requesting changes for plan round 1.

Blocking issues:

1. The DB-backed reader plan needs an explicit `db_invalid` policy before flipping runtime reads. Today Step A file writes are canonical and shadow-DB failures are swallowed while setting `.tagteam/DB_INVALID` (`cycle._shadow_db_after_cycle_write`, `_shadow_db_after_amend`; `dualwrite.is_db_invalid` is explicitly the reader gate). If `read_status`, `read_rounds`, `parser.read_cycle_rounds`, or CLI status/rounds/render become DB-first without checking that sentinel, a transient DB failure can make runtime reads stale or empty even though the file-side write succeeded. Please define and test the behavior. My recommended contract for Stage 2: when `db_invalid` is set, runtime readers either fall back to the legacy file source if present, or fail loudly with a DB-invalid diagnostic if the legacy files have already been moved. Do not silently trust DB content while the sentinel is set.

2. Divergence/repair cannot keep calling a retargeted `cycle.render_cycle` as their file-side renderer. `divergence.check_cycle_divergence` currently compares `cycle_mod.render_cycle(...)` to `db_mod.render_cycle(...)`; `repair._run_parity_unchecked` does the same. If this phase changes `cycle.render_cycle` to DB-backed for runtime/CLI render, those checks become DB-vs-DB and stop detecting file/DB drift. The plan says to make divergence also look in `.tagteam/legacy/`, but it must also preserve an explicit file-side render/read path for parity checks, independent of the new runtime DB-backed helpers. Same concern applies to repair verification before clearing `DB_INVALID`.

3. The impl-baseline propagation path must be included explicitly. `_resolve_baseline_for_cycle` reads the matching plan status through `_status_path(phase, "plan", project_dir)` directly. After `migrate --to-step-b` moves `_status.json` to `.tagteam/legacy/`, starting an impl cycle can lose the original plan baseline and fall back to a fresh impl baseline. Please retarget this through the DB-backed status helper or a DB read, and add a regression test for `init_cycle(..., "impl", ...)` after Step B migration preserving the plan baseline.

Reviewer positions on the open questions:
- Divergence path: choose A for Stage 2, but implement it through a dedicated file-side renderer that can read `docs/handoffs/` first and `.tagteam/legacy/` second. Defer DB-vs-md-render semantics to a later divergence-v2 phase.
- Parser JSONL fallback: keep it during Stage 2, gated by the `db_invalid`/missing-DB transitional policy. Remove it later when Stage 3 removes file writes.
- DB API shape: the existing `db.get_rounds(conn, phase, cycle_type)` is already a better public shape than `cycle_id`; add a higher-level DB-to-parser-shape adapter if needed so parser/TUI do not duplicate grouping logic.
- Additional reads: yes, include `tui/handoff_reader.py`, `tui/review_replay.py`, `server.py`, `state.diagnose` cycle sync, `cycle scope-diff`, and `_resolve_baseline_for_cycle` in the inventory. Even when some consume parser APIs indirectly, the tests should prove they still work after files move.
- Split: keep this as one plan cycle if the above contracts are added. Splitting divergence/repair would leave `migrate --to-step-b` unsafe, because repair/parity is part of trusting the DB.

## Round 2

### Lead

**Action:** SUBMIT_FOR_REVIEW

Round 2 plan: addresses all three blockers and accepts all five reviewer positions.

## Blocker 1 — db_invalid reader policy (was blocking)

New explicit contract for every DB-backed runtime reader (`read_status`, `read_rounds`, `parser.read_cycle_rounds`, CLI `cycle status/rounds/render`):

```
def read_status(phase, cycle_type, project_dir):
    if dualwrite.is_db_invalid(project_dir):
        # File side is canonical when sentinel is set.
        legacy_status = _legacy_status_path(phase, cycle_type, project_dir)
        if legacy_status.exists():
            return _read_status_from_file(legacy_status)
        raise CycleReadError(
            "DB_INVALID and no legacy status file. Run `tagteam state repair-db`."
        )
    # Sentinel clear — DB is authoritative.
    return _read_status_from_db(...)
```

`_legacy_status_path` checks `docs/handoffs/` first, then `.tagteam/legacy/` (covers both pre- and post-Step-B-activation states). Same pattern for `_legacy_rounds_path`.

`CycleReadError` is a new exception with a clear operator message. CLI catches it and prints a friendly error with the recovery command instead of a traceback.

Test: `test_db_invalid_falls_back_to_files` (sentinel set + legacy files present → reads from files), `test_db_invalid_no_legacy_raises` (sentinel set + legacy files absent → CycleReadError with operator message).

## Blocker 2 — preserve a dedicated file-side renderer for divergence/repair (was blocking)

Splitting the current `cycle.render_cycle` (which today reads files and renders) into two distinct functions:

- `cycle.render_cycle_from_files(phase, cycle_type, project_dir)` — file-side renderer, used ONLY by `divergence.check_cycle_divergence` and `repair._run_parity_unchecked`. Reads `docs/handoffs/` first, then `.tagteam/legacy/`. Never touches the DB. This preserves the file-vs-DB parity contract that motivated divergence in the first place.
- `cycle.render_cycle(phase, cycle_type, project_dir)` — runtime renderer used by CLI `cycle render`, web/TUI display. Now DB-backed (via the new `read_status` / `read_rounds` helpers, which honor the db_invalid contract).

Divergence keeps comparing file-side render to DB-side render — both renderers stay distinct, so the comparison stays meaningful. Repair parity check same.

Test: `test_divergence_still_detects_drift_after_stage_2` (mutate the file side without touching the DB → divergence reports drift, just as it did pre-Stage-2).

## Blocker 3 — _resolve_baseline_for_cycle plan-baseline propagation (was blocking)

`_resolve_baseline_for_cycle` currently reads `_status_path(phase, \"plan\", project_dir)` directly. Retargeting through the new `read_status` helper means it automatically honors the db_invalid contract AND finds the plan status whether files are in `docs/handoffs/` or `.tagteam/legacy/`.

Specific change: `_resolve_baseline_for_cycle` calls `read_status(phase, \"plan\", project_dir)` and pulls baseline from the returned dict. Same shape as before; just goes through the new helper.

Test: `test_impl_init_preserves_plan_baseline_after_step_b_migrate` — set up project, init plan cycle, get its baseline, run `migrate --to-step-b` (with STEP_B_READERS_READY=True monkeypatch), init impl cycle for same phase, assert impl's baseline equals the plan's baseline (NOT a fresh capture).

## Reviewer positions accepted (all five)

1. Divergence: option A with dedicated file-side renderer — incorporated into Blocker 2 fix.
2. Parser JSONL fallback: keep during Stage 2, gated by db_invalid — incorporated into Blocker 1 contract.
3. DB API: use existing `db.get_rounds(conn, phase, cycle_type)` (verified exists). No new `db.get_rounds(cycle_id)` needed; just expose a parser-shape adapter as a private helper inside parser.py if grouping logic would otherwise duplicate.
4. Additional reads: expanded inventory below.
5. Single cycle, not split.

## Expanded read-path inventory (per reviewer)

Original list plus:
- `tagteam/tui/handoff_reader.py` — TUI cycle list
- `tagteam/tui/review_replay.py` — TUI review playback
- `tagteam/server.py` — web dashboard JSON endpoints
- `tagteam/state.py:diagnose_state` — the cycle-sync section reads cycle status
- `tagteam/cycle.py:_cli_scope_diff` — reads baseline + current state
- `tagteam/cycle.py:_resolve_baseline_for_cycle` — addressed in Blocker 3

Each gets its file-read call swapped for `read_status` / `read_rounds`. Web/TUI tests prove they still render after Step B move (parameterized: with files in docs/handoffs/, with files in .tagteam/legacy/, with sentinel set + files absent).

## Files touched (revised)

- `tagteam/cycle.py` — new `read_status`, `read_rounds`, `render_cycle_from_files` (split out), refactored `render_cycle` (DB-backed), `_resolve_baseline_for_cycle` retargeted, `_cli_scope_diff` retargeted
- `tagteam/parser.py` — `read_cycle_rounds` delegates to `cycle.read_rounds`; JSONL parsing helper kept for transitional fallback
- `tagteam/divergence.py` — switch to `cycle.render_cycle_from_files`
- `tagteam/repair.py` — `_check_all_files` looks in `docs/handoffs/` then `.tagteam/legacy/`; `_run_parity_unchecked` uses `cycle.render_cycle_from_files`
- `tagteam/migrate.py` — flip `STEP_B_READERS_READY = True`
- `tagteam/state.py` — diagnose's cycle-sync section uses new readers; new `CycleReadError` exception + friendly CLI handling
- `tagteam/server.py` — switch to new readers
- `tagteam/tui/handoff_reader.py`, `tagteam/tui/review_replay.py` — switch to new readers
- Tests: 8 new test scenarios per the lists above; all five existing test files extended

## What's still NOT in scope (deferred)

- Removing parser.py JSONL fallback — Stage 3.
- Removing file-side writes — Stage 3.
- `divergence v2` (DB-vs-md-render) — separate future phase if/when needed.
- New iTerm2 send-keys watcher mode — separate roadmap entry (being added in parallel; not part of this cycle).

## Open questions for round-2 review

Just one — am I missing any subtle interaction between the new `CycleReadError` and the watcher daemon? Watcher reads handoff-state.json, not cycle status, so I think it's unaffected. Confirm?

### Reviewer

**Action:** APPROVE

Approved. Round 2 closes the prior blockers: DB-backed readers now have an explicit db_invalid fallback/refusal contract, divergence and repair keep a dedicated file-side renderer so parity remains file-vs-DB, and impl baseline propagation is retargeted through the new status reader with regression coverage. The expanded inventory covers the TUI, web, diagnose, scope-diff, and baseline paths, and keeping this as one implementation cycle is appropriate because migrate safety depends on repair/parity landing with the read migration. Watcher interaction looks unaffected because the watcher reads handoff-state.json rather than cycle status.

---

<!-- CYCLE_STATUS -->
READY_FOR: None
ROUND: 2
STATE: approved