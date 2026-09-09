# Phase 54: Role Neutrality Polish

## Status
- [x] Planning
- [x] Approved: plan round 1 (2026-09-09)
- [x] Implementation: fix/role-neutrality-review-nits
- [x] Implementation Review: approved round 2 (2026-09-09)
- [x] Complete: approved implementation; PR delivery, merge pending

## Summary
Address Phase 51's four non-blocking review nits in a separate PR before
publishing 3.12.0. Branch: `fix/role-neutrality-review-nits`, based on merged
PR #33 (`eeb3190`). Depends on Phase 51. Roles come from `tagteam.yaml`.

## Scope and Technical Approach
1. Remove duplicated trailing role labels in shipped handoff_plan.md and
   handoff_impl.md. Keep the instruction to consult current configuration.
2. Make gatekeeper/panel console next-step hints provider-neutral through the
   shared contract how-to text. Preserve dispatch, exit codes and structured
   state commands; these changes affect human-facing hints only.
3. Build the direct-headless guard test with original parsed config, capture its
   log and assert the participant-mismatch reason. Add a separate cached-config
   refusal test if needed. Source shows participant checking precedes config
   comparison; strengthen the test without assuming the reviewer's diagnosis
   of the exercised branch is established.
4. Stop computing unused role substitutions and printing misleading rendering
   claims during shipped framework setup: no packaged artifacts consume those
   placeholders. Retain renderer/get_template_variables as documented
   compatibility helpers for older/custom callers, with existing helper tests.
   Cosmetic cleanup must not remove a callable API unnecessarily.

No migration work, new role system, automated watcher restart, version increment,
tag, publication or downstream upgrade. Version remains the unpublished 3.12.0
candidate; Phase 52 is still required before downstream migration.

## Files
- `tagteam/data/templates/handoff_plan.md` and `handoff_impl.md`
- `tagteam/gatekeeper.py`, `tagteam/panel.py`, contract helper if needed
- `tagteam/setup.py`, `tagteam/templates.py`
- Focused participant/template/setup/plugin/gatekeeper/panel tests
- Phase plan and roadmap status

## Success Criteria and Verification
- Each handoff role label appears once, retaining current-config guidance.
- Console next-step hints explain shell and Claude entry points for either
  assignment; structured start commands remain unchanged.
- Direct-headless test asserts the intended refusal reason and no process call.
- Setup no longer computes unused substitutions; compatibility helpers work.
- Focused tests cover changes. The on_submit gate supplies the recorded full
  suite for impl submission. Plan-only changes require document checks only.
- Reviewer approval precedes separate PR delivery; publication is the arbiter's
  decision.

## Risks
Avoid API compatibility breaks and changes to structured headless commands while
cleaning up wording. No human clarification is required for plan review.


## Implementation notes
All four approved items are implemented without changing the 3.12.0 candidate
version or structured state commands. Console tests exercise on-submit gate,
manual gate and panel fallback output with both reviewer names. The headless
tests separately assert participant mismatch and cached-config change refusal.
Setup copies shipped templates verbatim; explicit-variable copy_md_file calls
and the generic template helpers retain compatibility.

Focused verification: 119 passed across templates, participants, setup and plugin
checks. The on_submit gate supplies the recorded full-suite result for review.


## Closeout
Reviewer approved all four fixes at impl round 2. Recorded on-submit gate at
`8d854b3`: 1,789 passed, 5 skipped; scope and plan-document checks passed.
Closeout only updates documentation and commits the cycle records; no duplicate
suite run. Version remains 3.12.0, unpublished. Phase 52 is still required before
downstream migration.
