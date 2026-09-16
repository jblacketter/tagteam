# Phase 59: Roadmap phase-number suffixes

## Status
- [x] Planning: approved round 1 (2026-09-15)
- [x] Implementation: branch `phase/roadmap-phase-number-suffixes`
- [x] Implementation Review: **skipped** — closed by arbiter decision
- [x] Complete: merged to main at `8e12e8b`

## Summary
`### Phase 58a: Watcher SIGTERM test in event mode` is invisible to the roadmap
parser. `_PHASE_HEADING_RE` requires `Phase[ \t]+(\d+):` — on `Phase 58a:` the
`\d+` matches `58`, the pattern then demands `:` and finds `a`, so the heading
never matches and the phase is not parsed at all.

Reproduced in both directions:

- This repo: `docs/roadmap.md` has 59 `### Phase` headings; `parse_roadmap`
  returns 58. Phase 58a — approved and merged two commits ago — cannot be
  queued, started, or named as a dependency. Silent: nothing depends on it, so
  `validate_graph` reports no problem.
- `~/projects/bugalizer`: 8 headings, 7 parsed. `Phase 5b` is dropped, so the
  legitimate reference at `docs/roadmap.md:96` fails as
  `bugalizer-revive-b0: unknown dependency 'Phase 5b'` and full-roadmap mode
  refuses the roadmap.

The bugalizer roadmap is written correctly; the parser is wrong. Renaming the
heading would only hide the defect, and would renumber a phase to do it.

## Scope
**In** — `tagteam/roadmap.py` only:
1. `_PHASE_HEADING_RE` and `_PHASE_HEADING_LENIENT_RE` accept an optional
   single-letter suffix: `Phase[ \t]+(\d+)([A-Za-z]?):`, normalized to
   lowercase.
2. `RoadmapPhase` gains `suffix: str = ""`. `number` stays `int` — it is read
   in exactly two places repo-wide (`roadmap.py:162` and one test assertion),
   so widening its type is unnecessary churn and would break both.
3. `_PHASE_NUM_REF_RE` accepts a suffix and resolves on the `(number, suffix)`
   pair, so `Phase 5` and `Phase 5b` are distinct references and neither
   resolves to the other. The existing "only when unique" rule is unchanged.
4. `_PHASE_NUM_SLUG_REF_RE` (`phase-N-slug` state format) accepts a suffixed
   number.
5. `validate_identities` keys duplicate detection on `(number, suffix)` and
   labels problems `Phase 5b`, not `Phase 5`.

**Out:**
- Multi-letter (`5ab`), dotted (`5.1`) or roman suffixes. One letter covers
  both observed conventions; more is speculation.
- Queue ordering changes. Order stays document order / stable topological —
  `number` is not a sort key today and must not become one here.
- Renumbering any existing roadmap, in this repo or bugalizer.
- bugalizer's roadmap. It is correct as written and needs no edit once the
  parser is fixed.

## Technical Approach
The identity key is the crux. Capturing the suffix but dropping it from the key
is worse than today's bug: `validate_identities` keys `numbers` on `int`, so a
roadmap holding both `Phase 5` and `Phase 5b` would report a spurious
`duplicate phase number 5` and refuse a valid roadmap. The suffix must travel
with the number through parsing, reference resolution and duplicate detection —
that is the whole change, and each of the five points above is one site where
the pair replaces the bare int.

Ordering is deliberately untouched. `dependency_graph` and the queue derive
order from document position, not `number`, so suffixed phases sort where the
author put them and no tie-break rule is needed.

## Reviewer notes (plan approved round 1)
- Append `suffix` after `depends_on` so positional construction of the earlier
  fields keeps working. Done.
- Cover uppercase/lowercase suffix equivalence, suffixed state-format
  references, empty suffixed heading diagnostics and unchanged queue ordering.
  Done — `TestSuffixedPhaseNumbers`.
- Success criterion 1 refreshed against the heading count (above).

## Files
- `tagteam/roadmap.py`
- `tests/test_roadmap.py`

## Success Criteria
1. `parse_roadmap` on this repo's `docs/roadmap.md` returns one phase per
   `### Phase` heading — compared against the heading count, not a fixed
   number — and includes `58a`; `tagteam roadmap check` stays clean.
   (Reviewer note, plan round 1: the round-1 figures, 60 headings / 59
   parsed, already counted the newly added Phase 59, so the fixed parser
   returns 60 here; a fixed count would be brittle either way.)
2. A fixture reproducing bugalizer's shape (`Phase 5`, `Phase 5b`, a dependant
   naming `Phase 5b`) parses all phases and `validate_graph` returns `[]`.
3. `Phase 5` resolves to the unsuffixed phase and `Phase 5b` to the suffixed
   one; neither cross-resolves.
4. A roadmap with both `Phase 5` and `Phase 5b` produces no duplicate-number
   problem; two `Phase 5b` headings still do.
5. Unsuffixed roadmaps parse exactly as before (no regression in the existing
   `test_roadmap.py` assertions, including `[p.number for p in phases] ==
   [1, 2, 3, 4]`).
6. Full suite green at submission.

## Closeout
Plan approved by codex round 1. Implementation submitted round 2 and passed
the on-submit gate: 2,006 passed, 5 skipped at `9a9b0cc`. Round 1 bounced on
three failures that were environmental, not Phase 59 — a `pyproject.toml`
version bump made for an unrelated local wheel test, and a stale
`tagteam.egg-info/` left in the checkout by `uv build`; both were reverted
or removed and each was isolated before re-submitting.

The implementation review round was **not** run. The arbiter closed the phase
directly and merged (`8e12e8b`), so no reviewer verdict exists for the impl
cycle — `docs/handoffs/roadmap-phase-number-suffixes_impl_*` records the
submission and the gate result only.

Result on this repo's own roadmap: 60 of 60 `### Phase` headings parse
(58 of 59 before), `58a` is present, and `Phase 58` / `Phase 58a` resolve to
different phases. Bugalizer's `- **Depends on:** Phase 5b` resolves without
any edit to its roadmap.
