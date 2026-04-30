# Phase: Cycle Baseline Snapshot for Impl Scope Audits

## Summary

Fix Issue #2 from `docs/handoff-cycle-issues-2026-04-24.md` (medium severity): when an impl cycle targets a project with pre-existing uncommitted drift, the reviewer's `git diff --name-only` audit conflates phase-attributable changes with that drift, causing extra REQUEST_CHANGES rounds spent on bookkeeping rather than substance.

The fix captures a **git baseline** at **plan-cycle init** time — both the resolved `HEAD` SHA and the working-tree drift snapshot (`git status --porcelain`) — and stores it in the plan cycle's status JSON. When the impl cycle is later initialized (after the lead has implemented), `init_cycle()` copies the baseline forward from the matching plan cycle's status JSON, so the impl cycle records the *pre-implementation* state. A new helper `tagteam cycle scope-diff` consumes the impl cycle's baseline and reports only the paths attributable to this phase, distinguishing committed phase work (always shown) from uncommitted edits (shown only if the path was clean at baseline).

This adopts option 2 ("capture working-tree snapshot on cycle init") from the bug report's suggested fixes, which the report's author identified as the highest-leverage option for this class of issue, with the timing correction that capture must happen at *plan* init (the actual pre-implementation moment in the handoff lifecycle).

## Scope

### In scope
- On `tagteam cycle init --type plan`, capture and persist into the plan cycle's `_status.json`:
  - `baseline.sha` — `git rev-parse HEAD` from the resolved project root, or `null` if not in a git repo / no commits yet.
  - `baseline.dirty_paths` — sorted list of porcelain entries (e.g. `" M docs/foo.md"`, `"?? scratch.txt"`) from `git status --porcelain` at init time. Empty list if clean. `null` if not in a git repo.
  - `baseline.captured_at` — ISO timestamp.
  - `baseline.source` — `"plan-init"` for plan cycles; `"copied-from-plan"` or `"impl-init-fallback"` for impl cycles (see below).
  - When `git` is unavailable or the project is not a git repo, capture is silent (`baseline = null`); cycle init must not fail.
- On `tagteam cycle init --type impl`, propagate the baseline from the matching plan cycle:
  - Look for `{phase}_plan_status.json`. If it exists and has a non-null `baseline` *block* (the dict itself, regardless of whether `baseline.sha` inside is null), copy that block into the impl status with `source = "copied-from-plan"`. Preserve the original `sha`, `dirty_paths`, and `captured_at` — do not re-capture. A null `sha` inside an otherwise valid baseline (no-commit-repo case) is preserved and handled by `scope-diff`'s empty-tree branch.
  - If the plan status is missing entirely, OR exists but has `baseline == None` (plan was init'd outside any git repo), fall back to capturing fresh at impl-init time with `source = "impl-init-fallback"`. Print a stderr warning explaining that this baseline reflects post-implementation state and should be treated as a degraded record.
  - Rationale: the typical lifecycle (plan → implement → impl-cycle) means the plan-init moment is the right pre-implementation snapshot. The fallback exists for impl-only cycles or projects where plan cycles weren't run through tagteam.
- New CLI subcommand `tagteam cycle scope-diff --phase X --type impl` that prints the path list attributable to this phase. Algorithm:
  - **Compute `committed_since_baseline`:**
    - If `baseline.sha` is non-null: `git -C <root> diff --name-only <baseline.sha> HEAD`. If `HEAD` does not resolve (still no commits at scope-diff time), use empty set.
    - If `baseline.sha` is null (plan init happened in a no-commit repo): compare against the git empty-tree object (well-known SHA `4b825dc642cb6eb9a060e54bf8d69288fbee4904`). `git diff --name-only 4b825dc... HEAD` lists every path in HEAD. If `HEAD` still does not resolve at scope-diff time, use empty set. This ensures phase work that introduced the repo's first commit(s) surfaces in `scope-diff`.
  - `current_dirty_paths = paths parsed from git status --porcelain now` (porcelain prefix stripped, rename target side used for `R `).
  - `baseline_dirty_paths = paths parsed from baseline.dirty_paths` (porcelain prefix stripped).
  - `attributable_uncommitted = current_dirty_paths − baseline_dirty_paths`
  - `attributable = sorted(committed_since_baseline ∪ attributable_uncommitted)`
  - Critically: `committed_since_baseline` is **not** filtered against `baseline_dirty_paths`. A path that was dirty at baseline but later committed during the phase IS legitimate phase work and must surface. Only uncommitted current edits are subject to the dirty-baseline filter (because we cannot tell if they are "the same dirt that was there before" or "new dirt").
  - Output one path per line on stdout. Exit 0 even if list is empty. Exit 1 only on error (no cycle, no baseline, etc.).
- Banner from the previous phase remains in `cycle_command()`; `scope-diff` inherits it automatically.
- Tests (now 11):
  1. `init --type plan` with clean repo: records `baseline.sha`, `dirty_paths == []`, `source == "plan-init"`.
  2. `init --type plan` with pre-existing drift: records porcelain entries; `sha` is HEAD.
  3. `init --type plan` outside a git repo: `baseline = null`, no error.
  4. `init --type plan` in a git repo with no commits yet: `baseline.sha = null`, `dirty_paths` reflects current state.
  5. `init --type impl` with a sibling plan-cycle status containing a baseline: impl status carries forward the same `sha`/`dirty_paths`/`captured_at`; `source == "copied-from-plan"`.
  6. `init --type impl` with no plan cycle present: captures fresh, `source == "impl-init-fallback"`, warning printed to stderr.
  7. `init --type impl` with a sibling plan-cycle status whose `baseline is null`: behaves the same as test #6 (fresh capture + stderr warning + `source == "impl-init-fallback"`). Reconciles the plan-exists-but-null-baseline case.
  8. `scope-diff` returns committed phase work even when the touched path was in `baseline.dirty_paths` (Codex round-1 concern).
  9. `scope-diff` filters pre-existing dirty paths from the *uncommitted* portion of the audit.
  10. **No-commit baseline + first-commit phase work (Codex round-2 concern):** plan-init in a `git init`'d repo with no commits, baseline.sha is null, then phase makes the first commit (one new file). Run `scope-diff` on the impl cycle (after copying the null-sha baseline forward — note: this exercises the `null sha → empty-tree` branch, not the null-baseline-fallback branch). Expected output includes the new committed file. This requires propagating the impl baseline correctly when plan.baseline is non-null but plan.baseline.sha is null — we copy the block forward as `source = "copied-from-plan"`, only fall back to fresh capture if the *block itself* is null.
  11. `scope-diff` exits 1 with a clear error when the cycle has no baseline (legacy cycles created before this phase).

### Out of scope
- Auto-running `scope-diff` from the reviewer side. The reviewer continues to invoke it manually as part of their audit workflow; tighter integration belongs in a future Phase E (event-driven watcher) or 2.0 work.
- Modifying impl-submission semantics (no "declare attributable scope" field on `add`). The baseline is a passive artifact — it describes the world at init time, not the lead's claims.
- Migrating legacy cycles. Existing `_status.json` files without a `baseline` block remain valid; `scope-diff` simply errors on them.
- Issue #5 (`AMEND` action) — separate phase.

## Technical Approach

### 1. Capture / propagate at `init_cycle()` (`tagteam/cycle.py`)

Add a private helper `_capture_baseline(project_dir: str, source: str) -> dict | None` that runs:

- `git -C <project_dir> rev-parse HEAD` (capture stdout, treat returncode != 0 as "no commits / not a repo")
- `git -C <project_dir> status --porcelain` (capture stdout; parse into a sorted list of `"XY path"` strings, preserving the porcelain status prefix so reviewers can see staged/unstaged distinctions)
- Return `{"sha": "...", "dirty_paths": [...], "captured_at": "...", "source": source}` or `None` if the directory is not a git repo (`rev-parse` fails AND `status` fails).
- Use `subprocess.run(..., timeout=5)` with the same exception swallowing as `_resolve_project_root` — never raise. On exception, return `None`.

Modify `init_cycle()` to set the baseline based on cycle type:

- For `cycle_type == "plan"`: `baseline = _capture_baseline(root, source="plan-init")`.
- For `cycle_type == "impl"`:
  1. Read `_status_path(phase, "plan", project_dir)`. If present, parse JSON; if the parsed dict has a `"baseline"` key whose value is a non-null dict, deep-copy that dict, overwrite its `source` to `"copied-from-plan"`, use as the impl baseline. The internal `sha` may itself be null (no-commit case) and is preserved.
  2. Otherwise (plan status missing, or `baseline` key absent, or `baseline is None`), call `_capture_baseline(root, source="impl-init-fallback")` and emit a stderr warning: `[tagteam] warning: no plan-cycle baseline for phase '{phase}'; capturing impl baseline from current state. This may include changes already made during implementation.`

Always include the `baseline` key in `status` (value can be `None`); this keeps schemas predictable.

### 2. New `scope-diff` subcommand (`tagteam/cycle.py`)

Add `_cli_scope_diff(args: list[str]) -> int`:

1. Parse `--phase` and `--type` (required; reuse `_parse_args`).
2. Read `_status_path(...)`. If missing → "No cycle found" → exit 1.
3. Read `baseline` block. If `None` or missing key → "Cycle has no baseline (created before phase: cycle-baseline-snapshot)" → exit 1.
4. Compute `committed_since_baseline`:
   - Determine `target = HEAD` if HEAD resolves (`git rev-parse --verify HEAD` exit 0), else target is unresolvable → `committed_since_baseline = set()` and skip the diff.
   - If `baseline.sha` is non-null: `git -C <root> diff --name-only <baseline.sha> <target>` → set of paths.
   - If `baseline.sha` is null: `git -C <root> diff --name-only 4b825dc642cb6eb9a060e54bf8d69288fbee4904 <target>` → set of paths (empty-tree comparison; lists every path in HEAD).
5. Compute `current_dirty_paths`: parse `git -C <root> status --porcelain` into a set of paths (strip the 3-char status prefix; for rename entries `R  old -> new`, take the new-path side).
6. Compute `baseline_dirty_paths`: parse `baseline.dirty_paths` the same way.
7. **Algorithm (Codex's correctness fix):**
   - `attributable_uncommitted = current_dirty_paths - baseline_dirty_paths`
   - `attributable = sorted(committed_since_baseline | attributable_uncommitted)`
   - Do NOT subtract `baseline_dirty_paths` from `committed_since_baseline`. Anything committed since baseline is provably phase work, even if the path was dirty at baseline.
8. Print one path per line. Exit 0.

Wire into `cycle_command()`:
```python
elif subcmd == "scope-diff":
    return _cli_scope_diff(args[1:])
```

Also update the usage string and the unknown-subcommand fallthrough text.

### 3. Status schema

Cycle `_status.json` after this phase has a new `baseline` field at the top level:
```json
{
  "state": "in-progress",
  "ready_for": "reviewer",
  "round": 1,
  "phase": "...",
  "type": "impl",
  "baseline": {
    "sha": "abc1234...",
    "dirty_paths": [" M docs/foo.md", "?? scratch.txt"],
    "captured_at": "2026-04-30T...",
    "source": "copied-from-plan"
  }
}
```

`source` values: `"plan-init"` (captured at plan cycle init), `"copied-from-plan"` (impl cycle propagated from plan), `"impl-init-fallback"` (impl cycle had no plan baseline available).

Or `"baseline": null` when not in a git repo. This is additive — no consumers today read this field.

### 4. Tests (`tests/test_cycle_baseline.py`, new)

Use the existing `tmp_path`/`monkeypatch` pattern. Each test sets up a temporary git repo with `git init`, configures `user.email`/`user.name` (commits won't go through without these in a clean env), and chdirs in. Reset the project-root cache in the autouse fixture as the existing test_project_root.py does.

Helper: `_seed_repo(path: Path, *, dirty: list[str] = None)` — `git init`, write a single committed file, optionally add untracked/modified paths.

Cases (matching the eleven listed under "In scope"):
1. Plan init, clean repo → `status["baseline"]["sha"]` is a 40-char hex, `dirty_paths == []`, `source == "plan-init"`.
2. Plan init with pre-existing drift → `dirty_paths` contains porcelain entries; `sha` is HEAD.
3. Plan init outside a git repo → `status["baseline"] is None`.
4. Plan init in a git repo with no commits → `sha is None`, `dirty_paths` reflects current state.
5. Impl init with sibling plan status containing a baseline → impl status has the same `sha`/`dirty_paths`/`captured_at`; `source == "copied-from-plan"`.
6. Impl init with no plan cycle present → captures fresh, `source == "impl-init-fallback"`, capsys captures the warning on stderr.
7. Impl init with sibling plan status whose `baseline is None` (plan ran outside a git repo) → captures fresh, `source == "impl-init-fallback"`, warning printed (same behavior as test 6, different setup). Reconciles the plan-exists-but-null-baseline case.
8. **Codex round-1 correctness case:** plan init with `a.py` already dirty, then commit a real change to `a.py` during the phase, then `scope-diff` on the impl cycle → output **includes** `a.py` (the committed phase work). Tests the algorithm fix.
9. Plan init with `b.py` already dirty, no further commits, current dirty = `b.py` (still) plus new `c.py` uncommitted → `scope-diff` output is `c.py` only.
10. **Codex round-2 correctness case:** plan init in a `git init`'d but un-committed repo (`baseline.sha = null`), then `git add x.py && git commit -m phase` during the phase, impl init copies the null-sha baseline forward, then `scope-diff` on the impl cycle → output **includes** `x.py`. Tests the empty-tree branch.
11. Legacy `_status.json` (manually written without the `baseline` key) → `scope-diff` exits 1 with the expected message.

### 5. Manual verification (under `/tmp`)

```
mkdir -p /tmp/baseline-test && cd /tmp/baseline-test && git init -q
git config user.email t@t && git config user.name t
touch tagteam.yaml committed.txt && git add . && git commit -q -m init

# Pre-existing drift, before the plan cycle starts:
echo dirt > pre-existing-dirt.txt
echo more >> committed.txt   # makes committed.txt dirty too

# Plan-cycle init: captures baseline.
tagteam cycle init --phase manual --type plan --lead L --reviewer R --updated-by L --content "plan"
cat docs/handoffs/manual_plan_status.json | python3 -m json.tool | grep -A6 baseline
# Expect: source=plan-init, dirty_paths includes pre-existing-dirt.txt and committed.txt.

# Lead "implements": commits a real change to a previously-dirty file plus a new file.
git add committed.txt new-from-phase.txt 2>/dev/null
echo new > new-from-phase.txt
git add committed.txt new-from-phase.txt
git commit -q -m "phase work"

# Impl-cycle init: should COPY the plan baseline forward, NOT recapture.
tagteam cycle init --phase manual --type impl --lead L --reviewer R --updated-by L --content "impl"
cat docs/handoffs/manual_impl_status.json | python3 -m json.tool | grep -A6 baseline
# Expect: source=copied-from-plan, same sha/dirty_paths as the plan status.

# scope-diff: should include both committed.txt and new-from-phase.txt
# (committed.txt was dirty at baseline but later committed → real phase work)
# but NOT pre-existing-dirt.txt (still uncommitted, still in baseline_dirty).
tagteam cycle scope-diff --phase manual --type impl
```

## Files

- `tagteam/cycle.py` — Add `_capture_baseline()`, wire into `init_cycle()`, add `_cli_scope_diff()`, register `scope-diff` in `cycle_command()`, update usage text.
- `tests/test_cycle_baseline.py` — New file with the 7 tests above plus a `_seed_repo` helper.

No changes to `tagteam/state.py`, `tagteam/parser.py`, or `tagteam/cli.py`.

## Success Criteria

- [ ] `tagteam cycle init --type plan` records a `baseline` block (or `null` outside a git repo) without ever failing the init.
- [ ] `tagteam cycle init --type impl` copies the baseline from the matching plan cycle when present (`source == "copied-from-plan"`); falls back to fresh capture with stderr warning otherwise (`source == "impl-init-fallback"`).
- [ ] `tagteam cycle scope-diff --phase X --type impl` prints paths attributable to the phase. Committed-since-baseline paths always appear (even if dirty at baseline). Uncommitted current paths appear only if they were not dirty at baseline.
- [ ] All 11 new tests pass.
- [ ] Full existing test suite still passes.
- [ ] Manual `/tmp` repro: `scope-diff` output includes `committed.txt` (dirty-at-baseline-but-committed) and `new-from-phase.txt`; excludes `pre-existing-dirt.txt`.
- [ ] Banner from the previous phase still emits; `scope-diff` is covered.

## Open Questions

- Should the porcelain prefix (e.g. `" M "`, `"?? "`) be preserved in `baseline.dirty_paths`? Plan: yes — preserves staged/unstaged distinction for human inspection, even though `scope-diff` strips it for set comparison. Flag if reviewer prefers bare paths.
- Should `scope-diff` accept `--json` to emit a structured payload? Plan: defer. Plain stdout per-line is enough for human review and `xargs`-style piping; JSON can be added when a consumer needs it.

## Risks

- **Subprocess timeout / git absent.** All git calls wrapped in try/except with timeouts; failures degrade to `baseline: null` rather than aborting cycle init.
- **Path comparison edge cases.** `git status --porcelain` paths can include rename arrows (`R  old -> new`) and quoted paths with special characters. Mitigation: store raw porcelain lines verbatim; for set comparison, parse off the leading 3-character status prefix and unquote if needed. Document the parser in `_cli_scope_diff` and add a test for at least the rename case.
- **Uncommitted modifications to baseline-dirty files.** If the lead modifies a file that was already dirty at baseline AND does not commit it, `scope-diff` will not flag it (the path stays in `baseline_dirty_paths` and is filtered out of the uncommitted set). The lead must commit such changes for them to surface — once committed, they appear in `committed_since_baseline` and are NOT filtered. This is the desired behavior: the cycle should record explicit phase intent (a commit), not silently absorb uncommitted edits to baseline-dirty files. Documented in `_cli_scope_diff`'s help text.
- **Stale plan baseline.** If the plan cycle's baseline is many days old, the impl cycle inherits a snapshot that may be unhelpfully ancient. We accept this — the alternative (re-capturing on impl) is exactly the bug we are fixing. Source field (`copied-from-plan`) makes the provenance explicit.
- **Plan cycle ran without git.** If the project was non-git at plan init, plan baseline is `None` (the whole block). When the impl cycle later inits, the propagation rule treats that `None` as "no baseline available" and falls back to fresh capture (with the impl-init-fallback warning). If the project is *still* non-git at impl-init time, fresh capture also returns `None`, the impl baseline is `None`, and `scope-diff` exits 1 with the legacy error. If git was added between plan and impl, the impl cycle gets a useful (though post-implementation) baseline, the same way an impl-only cycle would.
- **No-commit baseline + scope-diff.** Plan init in `git init`'d but un-committed repo records `baseline.sha = null` and a non-null `dirty_paths`. The impl cycle copies that block forward. `scope-diff` then compares against git's empty-tree object, so any commits introduced during the phase still surface. If the repo *still* has no commits at scope-diff time, `HEAD` does not resolve and `committed_since_baseline` is empty — only uncommitted-minus-baseline-dirty paths surface, which is the correct degenerate behavior.
