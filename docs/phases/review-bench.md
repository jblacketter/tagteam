# Phase 56: Review Bench

## Status
- [x] Planning
- [x] Approved: plan round 2 (2026-09-14)
- [x] Implementation: phase/review-bench
- [x] Implementation Review: approved round 2 (2026-09-14)
- [x] Complete: approved implementation; PR delivery, merge pending

## Summary
"Should the reviewer run on Sonnet?" and "is medium effort enough?" are
guesses today. Every reviewer verdict in `docs/handoffs/` is a labeled
example of a review this project actually needed. This phase turns that
history into a bench: replay a recorded submission against a reviewer
**cell** (provider × model × effort), in an isolated replay repository,
under a verdict-file contract that never writes the cycle, and tabulate how
each cell's verdict compares with the recorded one, plus the tokens and
seconds it used.

It has two parts:

1. **Submission snapshots (engine, from now on).** Every lead submission
   and every AMEND records the exact working tree **at that submission**
   (not proof of what the reviewer saw after later edits), as a commit
   pinned under `refs/tagteam/snapshots/…`, together with the diff base.
   No model, no tokens: a temporary index and `git write-tree`.
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
- AMEND appends a lead entry to the active round without changing the round,
  the turn or state `seq` (`cycle.add_round`), so round + seq does not
  identify the submitted version.
- Cycle history is readable from four places (`parser.read_cycle_rounds`,
  `cycle.read_rounds`): `docs/handoffs/P_T_rounds.jsonl` (+ `_status.json`,
  the rendered `P_T.md`), `.tagteam/legacy/P_T_rounds.jsonl` (+ status;
  tracked in this repo), legacy `docs/handoffs/P_T_cycle.md`, and the DB.
- `panel.verify_verdict` already validates a verdict file (verdict, summary,
  findings with blocker/major/minor severity); `panel.run_lens` already spawns
  a read-only reviewer child with `TAGTEAM_READ_ONLY=1` and records a usage
  row; `headless.validate_user_args` / `build_argv` already accept `--model`
  and `--effort` for Claude.
- A reviewer turn here averages ~595k input tokens (mostly cache reads), so
  one cell × one round costs about one reviewer turn of window.

## Scope
In:
1. `tagteam/snapshots.py` (new): `capture_submission_snapshot(project_root,
   phase, type, round, entry_ts, action)` → dict | None. Best effort, never
   raises into the submit.
2. Schema v11 (additive): tables `submission_snapshots`, `bench_results`.
3. CLI write paths `cycle init`, `cycle add … SUBMIT_FOR_REVIEW` and
   `cycle add … AMEND`: capture after the entry is recorded (and before the
   on-submit gate for submissions).
4. `tagteam/bench.py` (new): round selection, cell parsing, replay-repo
   materialization with history scrub, prompt composition, spawn, result
   recording, table.
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
rounds; pushing `refs/tagteam/*` anywhere; scrubbing the phase doc's Status
section or roadmap status lines of asserted trees.

## Technical Approach

### 1. Submission snapshots
After a lead entry (`SUBMIT_FOR_REVIEW` from `cycle init` or `cycle add`, or
`AMEND`) for round N of phase P, type T is recorded:

```
tmp_index = <mkstemp>
GIT_INDEX_FILE=tmp_index git read-tree HEAD        # (empty tree when no HEAD)
GIT_INDEX_FILE=tmp_index git add -A                # tracked + untracked, honours .gitignore
tree = GIT_INDEX_FILE=tmp_index git write-tree
commit = git commit-tree <tree> [-p HEAD] -m "tagteam snapshot P/T/rN <entry_ts>"
git update-ref refs/tagteam/snapshots/P/T/rN/<entry_ts-compact> <commit>
```

- **Identity is the lead entry, not the round.** The entry's `ts` (read back
  from the entry just written) is the submission version: a round with a
  submit and two AMENDs has three snapshots. Round re-entry after a ruling
  also gets distinct rows.
- Row in `submission_snapshots`: `phase, type, round, entry_ts, action
  (SUBMIT_FOR_REVIEW|AMEND), commit_sha, tree_sha, head_sha, base_sha, ref,
  captured_at`, unique on `(phase, type, round, entry_ts)`.
- **Diff base** (`base_sha`): the cycle status `baseline.sha` when present
  (the base the gate's scope check and `collect_change_surface` use, i.e.
  what the real reviewer's change surface was computed from); else
  `head_sha`. Both are stored; the replay uses `base_sha`.
- The user's real index, working tree, stash and branches are untouched
  (`git stash create` is not used: it drops untracked files). The ref keeps
  the objects from `git gc`; refs are local and nothing pushes them.
- Failure (not a git repo, git error, 30 s timeout) → one
  `[tagteam] note: submission snapshot not captured (…)` line on stderr and a
  diagnostics entry; the entry, state and gate proceed unchanged.
- Cost note: `git add -A` hashes changed files into the object store once per
  submission. Capture wall time on this repo is measured and reported in the
  impl submission.

### 2. Benchable rounds and the reviewed version (`tagteam bench select`)
A round is benchable when its cycle log has a **reviewer** entry for round N
with action `APPROVE`, `REQUEST_CHANGES`, `ESCALATE` or `NEED_HUMAN` (panel
entries count; a round that ended in `GATE_BOUNCE` without review does not),
preceded in that round by at least one lead `SUBMIT_FOR_REVIEW`. The
recorded verdict is that entry's action.

The **reviewed version** is the **last lead entry (submit or AMEND) of round
N before the verdict**; its `ts` is the version identity. The replay uses
that version's snapshot and a prompt tail that includes every entry up to it
(so amendments before the verdict are in context).

```
tagteam bench select [--phase P] [--type plan|impl] [--verdict APPROVE|REQUEST_CHANGES]
                     [--provenance snapshot|any] [--limit N] [--json]
```

Prints `P:T:N  recorded=REQUEST_CHANGES  version=<ts> (amended 1)
provenance=snapshot|none`. Read-only (`db.connect_for_read`).

**Provenance** of a round's tree:
- `snapshot`: a `submission_snapshots` row exists **for the reviewed version**
  (exact submission tree + recorded base). A round whose latest pre-verdict
  version has no snapshot (e.g. the AMEND capture failed) is `none`, even if
  the original submission has one.
- `asserted`: the operator names the trees: `--round P:T:N@BASE..REV`. Both
  are required for `impl` (the bench never infers that a closeout commit is a
  single-round diff, and never uses `REV^` as a base silently); for `plan`,
  `@REV` alone is accepted and the base is recorded as none (plan review reads
  the plan doc, not a diff). Results are labelled `asserted`.
- no snapshot and no `@…` → `run` refuses that round.

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
tagteam bench run --round P:T:N[@[BASE..]REV] ... --cell C ...
                  [--max-turns 12] [--timeout-minutes M] [--keep] [--yes]
```

- **Dry run is the default.** Without `--yes` it prints the round × cell
  grid, the provenance and version of each round, which pairs are already
  done (skipped), and a token estimate: `≈ <turns> × <mean input tokens of
  this project's recorded reviewer usage rows> (proxy: past reviewer turns,
  not bench turns)`, or `estimate unknown (no reviewer usage rows)`. No dollar
  figure anywhere. Nothing spawned, nothing written.
- **Cap:** pairs still to run must be ≤ `--max-turns` (default 12, the agreed
  6 × 2); otherwise refuse with the count and the flag to raise it.
- **Resume identity** of a pair: `(phase, type, round, version_ts, provenance,
  cell, commit_sha, base_sha)`. A pair is done when `bench_results` has an
  `ok` row with that identity; done pairs are skipped, failed ones retried.
  An asserted run at the same commit never suppresses a snapshot run, and a
  new AMEND version is a new pair.
- Pairs run **sequentially** (one reviewer-sized turn at a time).

Per pair:

1. **Isolated replay repository** (not a `git worktree`, which would share
   the project's refs and object history, including later commits and the
   future snapshot refs). In a system temp directory prefixed
   `tagteam-bench-` (outside the project, so project-root resolution cannot
   walk up into the live project):
   ```
   git init
   ls-tree -r <base_sha>   + cat-file --batch → fast-import commit "base"      (skipped when no base)
   ls-tree -r <commit_sha> + cat-file --batch → fast-import commit "submitted" (parent: base)
   .git/info/attributes: * -text -filter -ident -working-tree-encoding; reset --hard
   ```
   *(Impl review round 1: `git archive` was replaced by raw tree objects —
   archive applies `export-ignore` / `export-subst`, so it is not a faithful
   tree export. Replay tree objects equal the submitted tree's outside the
   scrub paths; submodule gitlinks are not materialised and the prompt lists
   them.)*
   The replay repo's only history is base → submitted, so `git diff HEAD~1`
   (or `git diff base`) shows exactly the submitted change set, tracked and
   formerly-untracked files alike, and nothing after the submission is
   reachable. Ignored files (virtualenvs, the DB) are not present; the
   contract says so. Removed afterwards unless `--keep`. Each directory
   carries an owner file (project, run, attempt, `keep`, bench pid + process
   identity); `run` reclaims only this project's directories whose owner
   process is gone and that were not kept — live and kept ones are never
   touched. Each pair attempt gets its own artifact directory (version,
   provenance, cell, commit, base and a random attempt id) and run ids carry a
   random suffix, so no attempt can read another's verdict file; identical
   requested pairs are run and budgeted once.
2. **Scrub the held-out outcome, in both trees, before committing** (so it is
   in neither the files nor the history):
   - `docs/handoffs/P_T_rounds.jsonl` and `.tagteam/legacy/P_T_rounds.jsonl`:
     the canonical copy in the replay is `docs/handoffs/P_T_rounds.jsonl`,
     rewritten to the entries **before the recorded verdict** (built from the
     live history, not from the tree); the legacy JSONL copy is removed.
   - removed: `P_T_status.json` in both locations, `docs/handoffs/P_T.md`,
     `docs/handoffs/P_T_cycle.md`.
   - no DB exists in the replay (ignored file), so readers fall back to the
     scrubbed JSONL; `TAGTEAM_READ_ONLY=1` means none is created.
   - **Known, stated leak for `asserted` trees:** a closeout commit can still
     carry the outcome in the phase doc's Status section, the roadmap status
     line and commit-message-shaped prose in other docs. These are not
     scrubbed; asserted rows are tabulated separately. Snapshot trees predate
     the verdict, so they cannot contain it.
3. **Prompt** (`compose_bench_prompt`): the bench contract
   (`data/bench/contract.md`, verdict path substituted); a
   `=== SUBMITTED CHANGE ===` block: base and submitted commit ids in the
   replay repo, the instruction `git diff HEAD~1` / `git diff base..HEAD`
   (ordinary `git diff` against the working tree is empty, and the contract
   says so), and a precomputed `git diff --name-status --find-renames` list;
   for a plan with no base, `no base: review the plan document and the tree`;
   the project's review checklist for the type (`docs/checklists/code_review.md`
   / `plan_review.md` from the tree when present); the recorded gate entry for
   round N if any (tests already ran: do not run the full suite); the round
   tail up to and including the reviewed version, AMENDs included (bounded
   by the watcher's default tail); the plan text read from the replay tree.
   Same section headers as the panel lens prompt.
4. **Spawn:** `headless.run_process` with the cell's argv, cwd = replay repo,
   env with `TAGTEAM_READ_ONLY=1`, `TAGTEAM_BENCH=1`, and `CLAUDECODE` /
   `CLAUDE_CODE_ENTRYPOINT` removed.
5. **Verdict:** `panel.verify_verdict`. Outcome `ok` / `failed` with the
   reason (timeout, spawn failure, no or invalid verdict file).
6. **Usage:** one `usage` row, `kind="bench"`, `role="reviewer"`, model and
   tokens from the stream as for any turn, and **`phase`, `type`, `round` and
   `target_*` null**, so `tagteam report --phase P` never counts bench spend
   as that phase's work. `tagteam usage --by kind` shows it.
7. **Result row** in `bench_results`: `run_id, phase, type, round,
   version_ts, amended (bool), provenance, cell, commit_sha, base_sha,
   recorded_verdict, verdict, n_blocker, n_major, n_minor, findings_json,
   outcome, reason, usage_row_id, duration_ms, ts`.

Nothing in the bench touches the live cycle, state, turn slot or watcher.
Running it during a live cycle only costs window.

### 5. Table (`tagteam bench table`)
```
tagteam bench table [--run RUN_ID] [--json]
```
One block per provenance (`snapshot`, then `asserted`; never merged), one
line per cell, over the latest `ok` row per (phase, type, round, version)
within that provenance and cell — several asserted tree choices for one
version count once (resume identity stays the full pair identity):

```
provenance snapshot
agreement is with the recorded reviewer, not with ground truth
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

Read-only (`db.connect_for_read`); no DB → `no bench results`.

### Read-only mode
`bench` is not added to `READ_ONLY_COMMANDS`: `run` spawns and writes the DB.
Under `TAGTEAM_READ_ONLY=1` the whole command is refused like any other
unlisted command. Snapshot capture is part of a submit/AMEND, which
read-only mode already refuses.

## Files
- `tagteam/snapshots.py` (new)
- `tagteam/bench.py` (new)
- `tagteam/data/bench/contract.md` (new)
- `tagteam/db.py`: schema v11, `add_submission_snapshot`,
  `submission_snapshot_for`, `add_bench_result`, `bench_results`
- `tagteam/cycle.py`: capture call in the CLI submit/AMEND paths (`cycle
  init`, `cycle add`)
- `tagteam/cli.py`: `bench` dispatch + help
- `pyproject.toml`: `data/bench/*.md`
- `README.md`, `docs/workflows.md`, `tagteam/data/workflows.md`
- `tests/test_snapshots.py`, `tests/test_bench.py` (new); schema-version
  assertions in existing DB tests updated

## Success Criteria
1. A lead submission in a git project records a `submission_snapshots` row
   and a `refs/tagteam/snapshots/…` ref whose tree equals the working tree
   (tracked changes and untracked, non-ignored files) at submit time, with
   `base_sha` = the cycle baseline sha; the user's index, stash and HEAD are
   unchanged. A capture failure prints one note and the entry, state and gate
   behave exactly as before.
2. **Amendment:** submit → change a file → AMEND → verdict. Two snapshot rows
   exist; `select` reports the AMEND version as the reviewed version with
   provenance `snapshot`; the replay tree contains the changed file and the
   prompt tail contains the AMEND entry. With the AMEND snapshot missing, the
   round's provenance is `none`.
3. `bench select` lists benchable rounds with recorded verdict, reviewed
   version and provenance, and excludes rounds without a reviewer verdict.
4. `bench run` without `--yes` spawns nothing and writes nothing; it prints
   the grid, skipped pairs and a proxy-labelled estimate, with no dollar
   figure. Above `--max-turns` it refuses. An `impl` round with `@REV` but no
   `BASE..` is refused.
5. **Replay isolation and diff exposure** (fake `claude` executable, as
   `test_panel.py` does): the replay repo is outside the project, its history
   is exactly base → submitted, `git status` / `git diff` are clean while
   `git diff HEAD~1 --name-status` lists the submitted tracked and untracked
   changes, and the prompt contains that list and the `HEAD~1` instruction.
   No project commit after the submission, and no `refs/tagteam/*`, is
   reachable from the replay repo.
6. **Scrub:** fixtures holding the target cycle's held-out verdict and later
   entries in `docs/handoffs/P_T_rounds.jsonl`, `.tagteam/legacy/P_T_rounds.jsonl`
   (+ both status files), `docs/handoffs/P_T.md` and a legacy
   `docs/handoffs/P_T_cycle.md`: in the replay repo no file, and no commit in
   its history, contains the verdict entry or any later entry; the canonical
   JSONL holds exactly the pre-verdict entries.
7. With `--yes`: the child has `TAGTEAM_READ_ONLY=1`; a valid verdict file
   yields an `ok` result, a missing/invalid one `failed` with the reason; the
   usage row has `kind=bench` and null phase/target; the replay repo is
   removed. The live cycle log, state and `seq` are byte-identical before and
   after.
8. **Resume identity:** a second `run` over the same grid skips the `ok`
   pairs and retries failed ones. The same round and commit requested once as
   `@BASE..REV` (asserted) and once via its snapshot produce two results in
   two provenance blocks; neither suppresses the other.
9. `bench table` reports agree / missed-RC / extra-RC / severity counts /
   tokens / seconds per cell per provenance, with the "not ground truth"
   line; `tagteam report --phase P` totals are unchanged by bench rows.
10. `codex:` cells and invalid model/effort values are refused before any
    spawn.
11. Full suite passes (on the record via the on-submit gate).

## Measurement after merge (not part of review)
First real run: 6 rounds × 2 cells, chosen from recent phases with
`@BASE..REV` round commits (asserted) until enough snapshot rounds exist.
Record what it used (`tagteam usage --by kind`) and the table; decide on the
20-round run from that.

## Closeout
Implementation approved round 2; gate 1,976 passed, 5 skipped at `211a1b3`.
Round 1 review changed the replay from `git archive` to raw tree objects,
made artifacts per attempt, made replay cleanup liveness/keep-aware and
aggregated the table per reviewed version. `tagteam report --phase
review-bench`:

```
Phase report: review-bench — plan approved r2 · impl approved r2
  plan   2 rounds · 1 change request · 0 bounces
  impl   2 rounds · 1 change request · 0 bounces · gate 2 runs, 11m 18s
  time   start→approve 36m 29s · implementation before first submit 13m 11s
         lead 6m 46s (2 spans, 2 unknown) · reviewer 5m 13s (4 spans) · gate 11m 19s (2 spans)  (elapsed; includes relay wait)
  usage  no usage rows stored under this phase
  turns  matched 0 of 8 · no token data 0 · unmatched 6 · unknown 2
```

Not yet done: the first real bench run (6 rounds × 2 cells). The impl round 1
and round 2 submissions of this phase are the first rounds with snapshots.
