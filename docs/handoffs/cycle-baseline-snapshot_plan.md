# Plan Review Cycle: cycle-baseline-snapshot

- **Phase:** cycle-baseline-snapshot
- **Type:** plan
- **Date:** 2026-04-30
- **Lead:** Claude
- **Reviewer:** Codex

## Round 1

### Lead

**Action:** SUBMIT_FOR_REVIEW

Initial plan submitted. Fixes Issue #2 from docs/handoff-cycle-issues-2026-04-24.md (impl-cycle scope audits conflate phase changes with pre-existing git drift). Adopts option 2 from the bug report: capture git HEAD SHA + working-tree drift snapshot at cycle init, expose a new 'tagteam cycle scope-diff' subcommand that returns the path list attributable to the phase only. All git calls wrapped in try/except with timeouts; legacy cycles without baseline error cleanly. Seven new tests + manual /tmp repro. Full plan: docs/phases/cycle-baseline-snapshot.md.

### Reviewer

**Action:** REQUEST_CHANGES

Requesting changes for plan round 1.

Blocking issues:

1. The proposed baseline capture point is too late for impl-cycle audits in the actual handoff workflow. The plan repeatedly anchors capture to `tagteam cycle init` (`docs/phases/cycle-baseline-snapshot.md:7,14-18,41-50`), but the workflow starts the impl cycle only after the lead has already finished the implementation: `/handoff start [phase] impl` creates the implementation-review cycle via `tagteam cycle init ...` (`.claude/skills/handoff/SKILL.md:94-102`), and the lifecycle section says plan approval is followed by "lead implements and runs `/handoff start [phase] impl`" (`.claude/skills/handoff/SKILL.md:130-133`). Current `init_cycle()` also creates the cycle together with the lead's first submission immediately (`tagteam/cycle.py:100-144`). That means, for impl reviews, the proposed `baseline.sha` and `baseline.dirty_paths` would be captured after the phase's changes already exist, so `scope-diff` would be comparing against a post-implementation snapshot rather than the pre-implementation world the review needs. Please redesign the baseline timing/source around the real lifecycle: for example capture it before implementation begins and carry it into the impl cycle, or add an explicit pre-implementation snapshot step. As written, the main feature cannot solve the stated bug.

2. The set-subtraction algorithm would hide legitimate phase work on files that were already dirty at baseline, and the plan's own risk note contradicts the algorithm. The core rule says to take committed-since-baseline plus current-dirty paths and subtract any path present in `baseline.dirty_paths` (`docs/phases/cycle-baseline-snapshot.md:19-22,56-64`). That permanently removes those paths from the output, even if the phase later makes committed changes to them. But the risk section says committed changes to previously dirty files will still surface (`docs/phases/cycle-baseline-snapshot.md:145-147`), which is not true under the proposed subtraction rule. This is more than a wording issue: if a phase legitimately touches a file that was dirty at init, the reviewer loses visibility into a real attributable change. Please revise the algorithm and tests so committed phase work on previously dirty files is still representable, even if you also want to distinguish it from pre-existing drift.

The overall direction is useful, but these two points affect whether the feature can answer the audit question at all.

## Round 2

### Lead

**Action:** SUBMIT_FOR_REVIEW

Round 2 — addressed both blocking issues.

Issue #1 (capture timing). The plan now captures the baseline at PLAN cycle init (the actual pre-implementation moment in the handoff lifecycle), not impl init. On 'tagteam cycle init --type impl', init_cycle() looks for the matching {phase}_plan_status.json and copies its baseline forward (source = copied-from-plan), preserving the original sha/dirty_paths/captured_at. If no plan cycle is present, falls back to capturing fresh with source = impl-init-fallback and prints a stderr warning that the snapshot is post-implementation. This makes the feature actually solve the bug: by the time the reviewer runs scope-diff on the impl cycle, the baseline reflects the world before any phase code existed.

Issue #2 (lossy set subtraction). The scope-diff algorithm now distinguishes committed phase work from uncommitted edits:
  attributable_uncommitted = current_dirty_paths - baseline_dirty_paths
  attributable = sorted(committed_since_baseline ∪ attributable_uncommitted)
Anything committed since baseline is provably phase work (git records it against baseline.sha) and is NEVER subtracted, even if the path was dirty at baseline. Only uncommitted current edits are filtered against baseline_dirty. This restores reviewer visibility into legitimate phase work on previously-dirty files. The risk note is rewritten to match: now the only known limitation is uncommitted modifications to baseline-dirty files, which the lead can surface by committing.

Tests grew from 7 to 9: added test #5 (impl init copies baseline from sibling plan), test #6 (impl init falls back with warning when no plan cycle), and test #7 (Codex's correctness case — committed change to a baseline-dirty file appears in scope-diff output). Schema gained a 'source' field on baseline ('plan-init' / 'copied-from-plan' / 'impl-init-fallback'). Manual /tmp repro updated to exercise both fixes: a file dirty at plan-init that's later committed during the phase ends up in scope-diff output alongside a fresh new file, while still-uncommitted pre-existing dirt is excluded.

Plan: docs/phases/cycle-baseline-snapshot.md.

### Reviewer

**Action:** REQUEST_CHANGES

Requesting changes for plan round 2.

Blocking issues:

1. The revised algorithm still drops committed phase work when the baseline was captured in a git repo with no commits. The plan explicitly allows `baseline.sha = null` for plan-init in a no-commit repo (`docs/phases/cycle-baseline-snapshot.md:25,37,77-79,129`), but `scope-diff` then defines `committed_since_baseline = empty set` whenever `baseline.sha` is null (`docs/phases/cycle-baseline-snapshot.md:25,77-79`). If the phase later makes the repo's first commit and the working tree is clean by review time, `scope-diff` returns nothing even though there is real attributable committed work. The current test matrix also stops at "plan init in a git repo with no commits" and never exercises scope-diff after the first commit (`docs/phases/cycle-baseline-snapshot.md:37,129-134`). Please decide the intended behavior for no-commit baselines and encode it in the algorithm/tests. Two viable directions are: compare against the empty tree / first reachable commit so committed phase work still surfaces, or explicitly declare `scope-diff` unsupported when the baseline had no SHA and make that a tested error path. As written, the plan still under-reports real phase work in an allowed repo state.

2. The plan is internally inconsistent about what happens when the plan cycle exists but its baseline is `null`. In scope and technical approach, impl init falls back to a fresh post-implementation capture when the plan status is missing *or has `baseline == null`* (`docs/phases/cycle-baseline-snapshot.md:20-23,65-66`). But the risks section says a non-git plan baseline is copied forward as `null` and `scope-diff` exits 1 (`docs/phases/cycle-baseline-snapshot.md:197`). Those are materially different behaviors and will lead to different tests and implementation branches. Please reconcile the intended semantics and add an explicit test for the `plan exists but baseline is null` case.

The timing fix and the committed-vs-uncommitted distinction are both good. After the no-commit case and null-baseline semantics are nailed down, I can re-review quickly.

## Round 3

### Lead

**Action:** SUBMIT_FOR_REVIEW

Round 3 — addressed both blocking issues from round 2.

Issue #1 (no-commit baseline drops committed phase work).
scope-diff now handles baseline.sha == null correctly: it compares against git's empty-tree object (well-known SHA 4b825dc642cb6eb9a060e54bf8d69288fbee4904), which lists every path in HEAD. So if the plan was init'd in a fresh git init (no commits) and the phase later makes the first commit(s), scope-diff surfaces those committed paths. Algorithm also explicitly handles the case where HEAD still does not resolve at scope-diff time (no commits ever) — committed_since_baseline degrades to empty, only uncommitted-minus-baseline-dirty surfaces. New test #10 exercises this end-to-end: plan init no-commit → commit during phase → impl scope-diff includes the committed file.

Issue #2 (plan-exists-but-null-baseline inconsistency).
Reconciled in favor of the scope-text behavior: if plan status exists but baseline == None (the entire block, meaning plan ran outside a git repo), impl init falls back to fresh capture with the impl-init-fallback warning. Crucially, this is gated on the *block* being None, not on baseline.sha being null inside an otherwise-valid block. A non-null baseline with null sha (no-commit-repo case) is COPIED forward, preserving the dirty_paths and feeding into the empty-tree branch above. Risk note rewritten to match. New test #7 covers the plan-exists-but-null-baseline path.

Test count grew from 9 to 11 (added #7 plan-exists-but-null and #10 no-commit-then-commit). Documented the precise propagation rule in the technical approach: 'if the parsed dict has a baseline key whose value is a non-null dict, deep-copy it; only fall back if the block itself is None.'

Plan: docs/phases/cycle-baseline-snapshot.md.

### Reviewer

**Action:** APPROVE

Approved. The plan now captures the baseline at plan init, propagates non-null baseline blocks into impl cycles, handles null-sha no-commit repos via the empty-tree diff branch, reconciles the null-baseline fallback semantics, and expands test coverage to the previously missing lifecycle cases.

---

<!-- CYCLE_STATUS -->
READY_FOR: None
ROUND: 3
STATE: approved