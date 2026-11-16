# Phase 78: Review criteria for AI-output hazards

## Status
- [x] Planning (approved round 1, 2026-11-16)
- [x] Implementation
- [ ] Implementation Review
- [ ] Complete

## Summary
Tagteam's reviewers are not told to look for two failures that are specific to AI-written work:
references that do not exist, and tests that are skipped or left out with no stated reason.
Source: `docs/tagteam-issue-review-criteria-ai-output-2026-11-15.md` (from the Aegis session,
3.14.11), which compared the checklists of Aegis's retired `unified` experiment with what tagteam
feeds reviewers today.

That doc proposed more than this phase takes. Arbiter's ruling (2026-11-16): **split it.**

- **Into tagteam (this phase):** the items that are true of AI output in any project.
- **Stays in Aegis:** the items that are one project's review policy (dependency justification,
  migration or rollback paths, security and performance risks named in every plan, secrets in
  tests and fixtures). The handoff contract ships to every registered project; it does not carry
  one project's standards.

This phase is lens prompt text only. No contract change, no engine change.

## Scope

### 1. Correctness lens
`tagteam/data/panels/correctness.md` gains one "Look for" item: references that do not exist: a
called function, imported module, config key, CLI flag, file path or package API that is not in
the tree or the installed dependency (check, do not assume).

### 2. Verification lens
`tagteam/data/panels/verification.md` gains one "Check" item: a promised behaviour left untested,
or a test skipped or marked xfail, carries a stated reason in the submission or the code (an
unexplained gap is a finding).

`scope.md` is unchanged. Both items are taken verbatim from the issue doc's patch; the patch's
other hunks (the dependency and secrets items, the contract sentence) are not applied.

## Out of scope
- **Any contract change.** No plan-review or impl-review sentence in `SKILL.md`, so no hash
  bookkeeping in this phase. Consequence, stated plainly: the two new items reach only paneled
  reviews. A project without `panel.enabled` gets nothing from this phase.
- **Aegis's policy items.** They belong in Aegis's own files. Tagteam already gives a project two
  places for them, with no tagteam change needed:
  - `AGENTS.md` / `CLAUDE.md`, read by both agents on every review (plan and impl);
  - a project lens brief at `.tagteam/panels/lenses/<name>.md`, or a `{name, brief}` entry in
    `panel.lenses` (`_resolve_brief`, covered by `test_resolve_builtin_override_config_and_missing`).
  Two limits Aegis should know: an override **replaces** the built-in brief, so it stops
  inheriting tagteam's updates to that lens (including this phase's), and `panel.lenses` allows
  2 to 3 lenses, so a fourth "policy" lens is not possible; it would have to take a slot.
  The Aegis-side change is Aegis's work and is not tracked here.
- **`tagteam upgrade` observations** from the same doc (the preview covers every registered
  project; nested `packages/*/templates` copies are invisible). Unrelated; proposed as a separate
  backlog entry.
- New lenses, verdict merging, gate checks, `tagteam/data/checklists/*.md` (frozen), `bench`.

## Technical Approach
- Edit the two lens files by hand (the issue doc's patch cannot be applied whole any more).
- Tests: none found that pin the lens "Look for" text (grep over `tests/` and `tagteam/*.py`);
  add assertions on the packaged files and on `panel preview` output.
- No version bump; ships with the next release.

## Files
- `tagteam/data/panels/correctness.md`, `tagteam/data/panels/verification.md`
- `tests/test_panel.py`
- `docs/roadmap.md`, `docs/phases/review-criteria-ai-output.md`
- `docs/tagteam-issue-review-criteria-ai-output-2026-11-15.md` (committed as the source record,
  with a note at the top recording the split)

## Success Criteria
1. The shipped `correctness.md` names nonexistent references and `verification.md` names
   unexplained skips, xfails and untested promised behaviour (tests on the packaged files).
2. Neither lens file mentions dependencies or secrets, and both `SKILL.md` copies are unchanged
   from the Phase 77 contract (test: the hash file needs no new entry).
3. `tagteam panel preview --lens correctness` and `--lens verification` on a paneled scratch
   project print the new items (test, existing preview path).
4. A project lens override still wins over the built-in brief (existing test, unchanged).
5. Full suite green through the gate.

## What this does not prove
Prompt text changes reviewer instructions, not reviewer behaviour. Nothing here measures whether
a lens catches a planted hallucinated reference. If that matters, the follow-up is a
planted-defect bench run (deferred with Phase 57), not more text.
