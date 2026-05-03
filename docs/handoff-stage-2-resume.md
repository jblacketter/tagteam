# Handoff: resuming Stage 2 work in a fresh tmux session

**Written 2026-05-03 by Claude (Opus 4.7) at the end of a long iTerm-based session.** The user wants to switch to tmux backend so the watcher can actually send-keys between agents (no more manual relay). This doc captures everything the next Claude session needs to pick up cleanly.

## The 30-second summary

- **Step B (PR #6)** is merged on main. Auto-export hook + repair sweep + `migrate --to-step-b` all shipped.
- **Step B activation was attempted on this repo and reverted** because read-side wasn't ready. Activation commit + revert both on main (commits `be83a6b` + `b57f26a`). Codex added a guard (`STEP_B_READERS_READY = False`, commit `de17ee6`) that refuses to run `migrate --to-step-b` until Stage 2 lands.
- **Stage 2 plan APPROVED** — two rounds of review with Codex on this branch (`phase-28-stage-2`). Plan artifacts committed in `docs/handoffs/phase-28-stage-2-db-readers_plan_*`. Read the round-2 lead submission for the canonical spec; that's the implementation contract.
- **Stage 2 impl HASN'T STARTED.** Next session should kick it off.
- **Phase 29 added to roadmap** (commit `143202e` on main): iTerm2 send-keys for watcher. Fix for the manual-relay problem this whole context-handoff is working around. Worth doing soon-ish so future iTerm users don't hit the same wall.

## Where things are right now

- **Branch:** `phase-28-stage-2` (this branch, just created). Not pushed yet — push if you want the new session to clone fresh.
- **Plan cycle:** complete. Round 1 + 2 reviewed and approved. Cycle status `done`.
- **Impl cycle:** NOT initialized. Previous attempt was a workflow misuse (see "Workflow gotcha" below) and the kickoff artifacts were deleted before this commit.
- **handoff-state.json** (gitignored) currently shows the closed plan cycle as `done`. Safe to start a fresh impl cycle from here.

## What the next session should do

### Step 1: Read the approved plan

```bash
tagteam cycle rounds --phase phase-28-stage-2-db-readers --type plan
```

Round 2 lead submission is the canonical spec. It addresses three blockers from round 1:
1. **db_invalid reader policy** — runtime DB-backed readers must check the sentinel; if set, fall back to legacy file source if present, raise `CycleReadError` if not.
2. **File-side renderer split** — divergence/repair need a dedicated `cycle.render_cycle_from_files` that stays file-side, separate from the new DB-backed `cycle.render_cycle` used at runtime. Otherwise divergence becomes DB-vs-DB and stops detecting drift.
3. **`_resolve_baseline_for_cycle` retargeted** through the new `read_status` helper so plan→impl baseline propagation works after Step B activation.

Plus 5 reviewer positions to honor (see round 1 reviewer text).

### Step 2: Kick off the impl cycle (with the role-swap workaround)

```bash
tagteam cycle init --phase phase-28-stage-2-db-readers --type impl \
  --lead Codex --reviewer Claude \
  --updated-by Codex \
  --content "Implementation per the approved Stage 2 plan. See plan round-2 lead for spec."
```

**Important:** make Codex (in the reviewer pane) run this, not Claude. The init content gets recorded as round-1 lead submission, and the lead in this impl cycle is Codex (per the Step B precedent that Codex implements / Claude reviews). If Claude inits, the role attribution is wrong.

Then Codex implements per the plan, runs `pytest`, submits via `cycle add ... --action SUBMIT_FOR_REVIEW --updated-by Codex`. Claude reviews via `cycle add ... --action APPROVE` or `REQUEST_CHANGES`.

### Step 3 (eventually): activate Step B on this repo

After Stage 2 merges to main:

```bash
TAGTEAM_STEP_B=1 tagteam migrate --to-step-b
```

Should now succeed with reads still working (because `STEP_B_READERS_READY = True` is part of the Stage 2 impl). Test reads: `tagteam cycle status --phase lead-amend-action --type impl` should still return data after activation.

## Workflow gotchas discovered this session

These belong on the roadmap eventually but aren't in there yet:

1. **`cycle init --type impl` with role swap is awkward.** It treats init content as round-1 lead submission, so init author = lead. If you swap lead/reviewer in the flags, the init has to be run by the new lead (Codex) or the attribution is wrong. Cleaner UX would be: `cycle init --type impl` with no content defaults to `ready_for: lead` (waiting for lead to submit), and the kickoff is metadata, not a round.

2. **Watcher iTerm2 mode only notifies, doesn't send-keys.** That's why this whole tmux-switch is happening. Phase 29 fixes it.

3. **Step B activation is irreversible without effort.** Once `migrate --to-step-b` moves files to `.tagteam/legacy/` and you discover reads are broken, you have to manually `mv` the files back. The guard (`STEP_B_READERS_READY = False`) prevents this from ever happening again, but the activation command should probably also support `--dry-run` for confidence-building.

## Uncommitted worktree state to ignore

Pre-existing user worktree cruft, not part of this work:

- `.idea/ai-handoff.iml` (deleted, IDE settings)
- `.idea/modules.xml` (modified, IDE settings)
- `docs/handoff-cycle-issues-2026-04-24.md` (untracked, the older issues doc referenced in CLAUDE.md)
- `scratch.md` (gitignored, the Codex-shuttle scratch file from the iTerm session)

Don't commit any of these as part of Stage 2 impl.

## Memory pointers (already saved)

- `feedback_scratch_md_shuttle.md` — when reading Codex reply from scratch.md, write reply back into scratch.md (overwrite). ONLY relevant if not in tmux mode.
- `feedback_skip_handoff_for_mechanical_work.md` — skip cycle for release/infra work.
- `project_phase_28_progress.md` — outdated as of this session; should be refreshed by the next Claude with current state (Step B merged + Stage 2 plan approved + Stage 2 impl pending).

## Commits worth knowing about

```
de17ee6 Guard Step B migration until DB readers land            (main)
143202e Add Phase 29: iTerm2 send-keys for watcher              (main)
b57f26a Revert "Activate Phase 28 Step B on this repo"          (main)
be83a6b Activate Phase 28 Step B on this repo                   (main, reverted)
afbdd49 Wire Phase 28 Step B auto-export of cycle markdown      (main, was PR #6)
<this branch's plan-artifacts commit>                           (phase-28-stage-2)
```
