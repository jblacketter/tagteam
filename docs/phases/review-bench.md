# Phase 56: Review Bench

## Status
- [ ] Planning
- [ ] Approved
- [ ] Implementation: phase/review-bench
- [ ] Implementation Review
- [ ] Complete

## Summary
"Should the reviewer run on Sonnet?" and "is medium effort enough?" are
guesses today. Every reviewer verdict in `docs/handoffs/` is a labeled
example of a review this project actually needed. This phase turns that
history into a bench: replay a recorded submission against a reviewer
**cell** (provider × model × effort), in a disposable worktree, under a
verdict-file contract that never writes the cycle, and tabulate how each
cell's verdict compares with the recorded one, plus the tokens and seconds
it used.

It has two parts:

1. **Round snapshots (engine, from now on).** Every lead submission records
   the exact tree the reviewer saw, as a git tree object pinned under
   `refs/tagteam/…`. No model, no tokens: a temporary index and
   `git write-tree`. This is what makes a future replay exact.
2. **`tagteam bench`.** `select` / `run` / `table` over rounds, with a
   dry-run by default, a turn cap and resumable results.

Source of scope: `docs/research/2026-09-14-better-tagteam/`
(`03-features.html` #3, `02-agents-and-models.html` §6 step 2). Arbiter
rulings on 2026-09-14, before this plan:
- **Tree provenance:** capture snapshots from now on; historical rounds are
  best effort and labelled as such, never mixed with exact rows.
- **Ground truth:** verdict agreement only. No manual labelling step; blocker
  matching waits for `tagteam grade` (a later phase), so the bench never
  depends on a human process between versions.
- **Planted-defect control:** a follow-up phase.
- **Budget:** subscription window, never dollars. First real run is 6 rounds
  × 2 cells; a 20-round run is decided after seeing what that one used.

### Verified facts this plan rests on
- This repo: 48 cycle logs, 133 reviewer verdicts (plan 49 RC / 24 APPROVE,
  impl 35 RC / 25 APPROVE).
- Reviews run on the **uncommitted** working tree. Per-round commits are
  written at closeout (Phase 55's plan and impl commits share one committer
  timestamp), and some phases have one commit for several rounds. A gate
  entry's `HEAD <sha>` is the base, not the reviewed tree. So no historical
  round has a provably exact tree; that is why part 1 exists.
- `panel.verify_verdict` already validates a verdict file (verdict, summary,
  findings with blocker/major/minor severity); `panel.run_lens` already spawns
  a read-only reviewer child with `TAGTEAM_READ_ONLY=1` and records a usage
  row; `headless.validate_user_args` / `build_argv` already accept `--model`
  and `--effort` for Claude.
- A reviewer turn here averages ~595k input tokens (mostly cache reads), so
  one cell × one round costs about one reviewer turn of window.

## Scope
In:
1. `tagteam/snapshots.py` (new): `capture_round_snapshot(project_root, phase,
   type, round, submission_seq)` → dict | None. Best effort, never raises into
   the submit.
2. Schema v11 (additive): tables `round_snapshots`, `bench_results`.
3. `cycle.py` CLI submit paths (`cycle add … SUBMIT_FOR_REVIEW` and
   `cycle init`): capture after the round is recorded, before the on-submit
   gate.
4. `tagteam/bench.py` (new): round selection, cell parsing, worktree
   materialization, prompt composition, spawn, result recording, table.
5. `tagteam/data/bench/contract.md` (new, package data): the bench reviewer
   contract (full review, all axes, write `verdict.json`, no cycle writes).
   `pyproject.toml` gains `data/bench/*.md`.
6. `cli.py`: dispatch `bench`; help text.
7. `README.md` command list; `docs/workflows.md` / `tagteam/data/workflows.md`:
   one paragraph on the bench and its cost.
8. Tests: `tests/test_snapshots.py`, `tests/test_bench.py`.

Out: Codex cells (v1 refuses them with a message; added once `codex exec`'s
effort flag is confirmed); panel-lens and briefer replay; blocker matching,
LLM judging or any labelling UI; planted defects; cross-project benches;
model policy (Phase 57); a cockpit bench view; backfilling snapshots for old
rounds; pushing `refs/tagteam/*` anywhere.

## Technical Approach

### 1. Round snapshots
At each lead submission (round N of phase P, type T):

```
tmp_index = <mkstemp>
GIT_INDEX_FILE=tmp_index git read-tree HEAD        # (empty tree when no HEAD)
GIT_INDEX_FILE=tmp_index git add -A                # tracked + untracked, honours .gitignore
tree = GIT_INDEX_FILE=tmp_index git write-tree
commit = git commit-tree <tree> [-p HEAD] -m "tagteam snapshot P/T/rN"
git update-ref refs/tagteam/snapshots/P/T/rN-<seq> <commit>
```

- The user's real index, working tree, stash and branches are untouched
  (`git stash create` is not used: it drops untracked files).
- The ref keeps the object from `git gc`. Refs are local; nothing pushes them.
- Row in `round_snapshots`: `phase, type, round, submission_seq, commit_sha,
  tree_sha, head_sha, ref, captured_at`, unique on
  `(phase, type, round, submission_seq)`.
- Failure (not a git repo, git error, timeout of 30 s) → one
  `[tagteam] note: round snapshot not captured (…)` line on stderr and a
  diagnostics entry; the submission and gate proceed unchanged.
- Cost note: `git add -A` hashes changed files into the object store. On this
  repo that is the diff since HEAD, small; a large untracked binary would be
  stored once per submission. Measured in the impl submission (wall time of
  capture on this repo).

### 2. Benchable rounds (`tagteam bench select`)
A round is benchable when its cycle log has a lead `SUBMIT_FOR_REVIEW` for
round N followed by a **reviewer** entry for round N with action `APPROVE`,
`REQUEST_CHANGES`, `ESCALATE` or `NEED_HUMAN` (panel entries count; a round
that ended in `GATE_BOUNCE` and was never reviewed does not). The recorded
verdict is that entry's action.

```
tagteam bench select [--phase P] [--type plan|impl] [--verdict APPROVE|REQUEST_CHANGES]
                     [--provenance snapshot|any] [--limit N] [--json]
```

Prints `phase:type:N  recorded=REQUEST_CHANGES  provenance=snapshot|none`.
Read-only (reads cycles and the DB through `db.connect_for_read`).

**Provenance** of a round's tree:
- `snapshot`: a `round_snapshots` row exists (exact).
- `asserted`: the operator names a revision: `--round P:T:N@REV`. The bench
  records it and labels every result from it `asserted`; it cannot prove the
  tree matches.
- no snapshot and no `@REV` → `run` refuses that round.

### 3. Cells
`--cell claude:<model>:<effort>`, e.g. `claude:opus:medium`,
`claude:sonnet:high`. Model and effort go through
`headless.validate_user_args` as `--model <model> --effort <effort>`, so a
value the headless adapter rejects is rejected here with the same message.
`codex:*` cells → refused in v1 (`codex cells are not supported yet`). The
executable is `agents.<role>.headless.executable` for whichever role is
configured with the `claude` provider, else `claude` on `PATH`.

### 4. Run (`tagteam bench run`)
```
tagteam bench run --round P:T:N[@REV] ... | --from-select <select args>
                  --cell C ... [--max-turns 12] [--timeout-minutes M] [--keep] [--yes]
```

- **Dry run is the default.** Without `--yes` it prints the round × cell
  grid, which pairs are already done (skipped), and a token estimate:
  `≈ <turns> × <mean input tokens of this project's recorded reviewer usage
  rows> (proxy: past reviewer turns, not bench turns)`, or `estimate unknown
  (no reviewer usage rows)`. No dollar figure anywhere. Exit 0, nothing
  spawned, nothing written.
- **Cap:** the number of pairs still to run must be ≤ `--max-turns`
  (default 12, i.e. the agreed 6 × 2); otherwise refuse with the count and
  the flag to raise it.
- **Resumable:** a pair is done when `bench_results` has an `ok` row for
  `(phase, type, round, cell, tree_sha)`; done pairs are skipped. Failed rows
  are retried on the next run.
- Pairs run **sequentially** (one reviewer-sized turn at a time).

Per pair:
1. **Worktree:** `git worktree add --detach <tmp> <commit>` in the system temp
   directory (outside the project, so project-root resolution cannot walk up
   into the live project). Removed afterwards unless `--keep`; a crash leaves
   it listed by `git worktree list`, and `bench run` prunes stale bench
   worktrees it created (path prefix `tagteam-bench-`) before starting.
2. **No future in the tree:** in the worktree, rewrite the benched cycle's
   `docs/handoffs/P_T_rounds.jsonl` to the entries before the recorded
   verdict, and delete `P_T_status.json` and `P_T.md`. For a snapshot tree
   this is a no-op in practice; for an `asserted` closeout commit it removes
   the recorded verdict. Other leaks an asserted commit can carry (the phase
   doc's Status checkboxes, the roadmap status line) are **not** scrubbed;
   the table labels asserted rows so they are read with that in mind.
3. **Prompt** (`compose_bench_prompt`): the bench contract
   (`data/bench/contract.md`, with the verdict path substituted), the
   project's review checklist for the type (`docs/checklists/code_review.md`
   / `plan_review.md` from the tree when present), the recorded gate entry
   for round N if any (the tests already ran: the contract says do not run
   the full suite), the round tail up to and including the submission
   (bounded by the watcher's default tail), and the plan text read **from the
   worktree**. Same section headers as the panel lens prompt.
4. **Spawn:** `headless.run_process` with the cell's argv, cwd = worktree,
   env with `TAGTEAM_READ_ONLY=1`, `CLAUDECODE` / `CLAUDE_CODE_ENTRYPOINT`
   removed, and `TAGTEAM_BENCH=1`.
5. **Verdict:** `panel.verify_verdict`. Outcome `ok` / `failed` with the
   reason (timeout, spawn failure, no or invalid verdict file).
6. **Usage:** one `usage` row, `kind="bench"`, `role="reviewer"`, model
   and tokens from the stream as for any turn, and **`phase`, `type`, `round`
   and `target_*` left null**, so `tagteam report --phase P` never counts
   bench spend as that phase's work. `tagteam usage --by kind` shows it.
7. **Result row** in `bench_results`: `run_id` (timestamp stem), `phase,
   type, round, cell, provenance, rev, commit_sha, tree_sha,
   recorded_verdict, verdict, n_blocker, n_major, n_minor, findings_json,
   outcome, reason, usage_row_id, duration_ms, ts`.

Nothing in the bench touches the live cycle, state, turn slot or watcher. It
does not take the turn slot; running it during a live cycle only costs
window.

### 5. Table (`tagteam bench table`)
```
tagteam bench table [--run RUN_ID] [--json]
```
One block per provenance (`snapshot`, then `asserted`), one line per cell,
over its latest `ok` row per round:

```
provenance snapshot
cell                  rounds  agree  missed-RC  extra-RC  blk/maj per RC  in-tok  out-tok  sec
claude:opus:medium        6   5/6         0/3       1/3        1.3 / 0.7    512k     4.1k  141
claude:sonnet:high        6   4/6         2/3       0/3        0.7 / 1.0    488k     3.2k   96
failed: claude:sonnet:high 1 (timeout)
```

- `agree`: cell verdict == recorded verdict.
- `missed-RC`: recorded REQUEST_CHANGES, cell APPROVE (the expensive miss).
- `extra-RC`: recorded APPROVE, cell REQUEST_CHANGES.
- Token and seconds columns are means over rows with token data; rows
  without are counted, not averaged in.
- Text says under the header: `agreement is with the recorded reviewer,
  not with ground truth`.

Read-only (`db.connect_for_read`); no DB → `no bench results`.

### Read-only mode
`bench` is not added to `READ_ONLY_COMMANDS`: `run` spawns and writes the DB.
Under `TAGTEAM_READ_ONLY=1` the whole command is refused like any other
unlisted command (splitting subcommands is not worth a special case now).
Snapshot capture is part of a submit, which read-only mode already refuses.

## Files
- `tagteam/snapshots.py` (new)
- `tagteam/bench.py` (new)
- `tagteam/data/bench/contract.md` (new)
- `tagteam/db.py`: schema v11, `add_round_snapshot`, `get_round_snapshot`,
  `add_bench_result`, `bench_results`
- `tagteam/cycle.py`: capture call in the CLI submit paths
- `tagteam/cli.py`: `bench` dispatch + help
- `pyproject.toml`: `data/bench/*.md`
- `README.md`, `docs/workflows.md`, `tagteam/data/workflows.md`
- `tests/test_snapshots.py`, `tests/test_bench.py` (new); schema-version
  assertions in existing DB tests updated

## Success Criteria
1. A lead submission in a git project records a `round_snapshots` row and a
   `refs/tagteam/snapshots/…` ref whose tree equals the working tree
   (tracked changes and untracked, non-ignored files) at submit time; the
   user's index, stash and HEAD are unchanged. A capture failure prints one
   note and the submission, state and gate behave exactly as before.
2. `bench select` lists benchable rounds with recorded verdict and
   provenance, and excludes rounds without a reviewer verdict.
3. `bench run` without `--yes` spawns nothing and writes nothing; it prints
   the grid, skipped pairs and a proxy-labelled estimate, with no dollar
   figure. Above `--max-turns` it refuses.
4. With `--yes` (tests use a fake `claude` executable, as `test_panel.py`
   does): each pair runs in a detached worktree outside the project, the
   child has `TAGTEAM_READ_ONLY=1`, the benched cycle's files in the worktree
   contain no entry at or after the recorded verdict, a valid verdict file
   yields an `ok` result, a missing/invalid one a `failed` result with the
   reason, the usage row has `kind=bench` and null phase/target, and the
   worktree is removed. The live cycle log, state and `seq` are byte-identical
   before and after.
5. A second `run` over the same grid skips the `ok` pairs and retries failed
   ones.
6. A round with no snapshot and no `@REV` is refused; `@REV` results are
   labelled `asserted` and tabulated separately from `snapshot` results.
7. `bench table` reports agree / missed-RC / extra-RC / severity counts /
   tokens / seconds per cell per provenance, with the "not ground truth"
   line; `tagteam report --phase P` totals are unchanged by bench rows.
8. `codex:` cells and invalid model/effort values are refused before any
   spawn.
9. Full suite passes (on the record via the on-submit gate).

## Measurement after merge (not part of review)
First real run: 6 rounds × 2 cells, chosen from recent phases with
`@REV` round commits (asserted) until enough snapshot rounds exist. Record
what it used (`tagteam usage --by kind`) and the table; decide on the
20-round run from that.
