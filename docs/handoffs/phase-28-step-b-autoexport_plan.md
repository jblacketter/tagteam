# Plan Review Cycle: phase-28-step-b-autoexport

- **Phase:** phase-28-step-b-autoexport
- **Type:** plan
- **Date:** 2026-05-03
- **Lead:** Claude
- **Reviewer:** Codex

## Round 1

### Lead

**Action:** SUBMIT_FOR_REVIEW

Plan: wire the existing Step B helpers (tagteam/auto_export.py:render_cycle_to_file and db.export_to_files) into the cycle writers behind a Step B activation flag, so every DB-mirroring write also re-renders docs/handoffs/<phase>_<type>.md. This revision addresses Codex round-1 findings on the original draft.

Branch context
- This cycle runs on phase-28-step-b. The two helpers already live here (commits fd0170c, 9106034).
- phase-28-step-b is one commit behind main (1e0e59d, the cycle init flags fix). Rebase onto main is a prerequisite for the impl cycle, not for this plan cycle.

Scope
- In: a new auto-export hook called after the existing Step A shadow-write helpers in all three cycle write paths (init_cycle, add_round, AMEND); the activation flag; tests; SKILL.md note.
- Out: removing legacy _rounds.jsonl/_status.json writes; the --reverse migration path; making auto-export failures a hard error (deferred until soak data exists).

Naming — fixes Codex finding 1
- New helper: _auto_export_cycle_md(project_dir, phase, cycle_type) in tagteam/cycle.py.
- Distinct from the existing _shadow_db_after_cycle_write (cycle.py:944) and _shadow_db_after_amend (cycle.py:1041), which own Step A DB mirroring + divergence checks. The auto-export hook runs *after* those return, never replacing or wrapping them.

Hook placement (three call sites — fixes Codex finding 2)
- cycle.py:272 — init_cycle, after _shadow_db_after_cycle_write returns: call _auto_export_cycle_md.
- cycle.py:385 — add_round (normal path), same pattern.
- cycle.py:327 — add_round AMEND early-return, after _shadow_db_after_amend returns: call _auto_export_cycle_md. AMEND mutates rendered content, so it must trigger a re-render.
- All three calls inside the writer-lock-held region for serialization with the DB write that produced the content.

Activation flag
- Env var: TAGTEAM_STEP_B=1, checked via step_b_active() helper added to tagteam/dualwrite.py.
- Default off → byte-for-byte unchanged behavior. Reuses the dual-write activation pattern; no tagteam.yaml schema change.

Failure handling — fixes Codex finding 4
- _auto_export_cycle_md still swallows the underlying helper's False return (no exception escapes), but on failure also appends a structured entry to handoff-diagnostics.jsonl: {kind: "auto_export_failed", phase, cycle_type, ts, reason}. So a stale .md is never silent — operators have a side-channel signal.
- This is the soak-friendly middle ground: not a hard fail (don't break the write path while we gather signal), but not silent either.

Tests (new tests/test_auto_export_hook.py)
1. Flag off, init_cycle: no .md write.
2. Flag off, add_round: no .md write.
3. Flag on, init_cycle: .md appears with content equal to db.render_cycle.
4. Flag on, add_round (normal): .md updates; equality check vs db.render_cycle.
5. Flag on, AMEND: .md re-renders to include the amendment payload (covers the new AMEND hook).
6. Flag on, render_cycle_to_file returns False (simulate by deleting cycle row between DB write and hook call, or monkeypatching): add_round still succeeds; handoff-diagnostics.jsonl gains an auto_export_failed entry.
7. Flag on, OSError on write (handoffs dir read-only): same — no exception escapes; diagnostic entry written.
8. Idempotent: two add_rounds → single .md, content equals second render.

SKILL.md change
- One-paragraph note: "When TAGTEAM_STEP_B=1, docs/handoffs/<phase>_<type>.md is auto-rendered on every cycle write — don't hand-edit. Edit via cycle add and re-render is automatic. If a write produces no .md update, check handoff-diagnostics.jsonl for an auto_export_failed entry."

Open questions for reviewer
- Diagnostics path: handoff-diagnostics.jsonl already exists for seq mismatches (see Phase 16). Reuse the same file/format, or carve out a dedicated auto-export log? My read: reuse — operators already know to check that file.
- Should the hook also fire from dualwrite divergence-repair paths in tagteam/dualwrite.py? My read: no, repair is a maintenance op, not new conversation content. Confirm?
- Trailing-newline normalization in render_cycle_to_file: does cycle.render_cycle (legacy markdown render path used by cycle render) match the same newline shape? If not, first activation on a pre-existing cycle would produce a one-byte diff. Worth a quick parity check during impl.

Files touched: tagteam/cycle.py (3 hook calls + helper), tagteam/dualwrite.py (step_b_active), tests/test_auto_export_hook.py (new), .claude/skills/handoff/SKILL.md (one-paragraph note).

### Reviewer

**Action:** APPROVE

Approved. Six rounds of refinement in scratch.md converged on a sentinel-aware hook + sentinel-bypassing bulk renderer + resumable per-cycle migration. Implementation followed.

---

<!-- CYCLE_STATUS -->
READY_FOR: None
ROUND: 1
STATE: approved