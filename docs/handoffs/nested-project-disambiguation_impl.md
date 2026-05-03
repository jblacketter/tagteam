# Implementation Review Cycle: nested-project-disambiguation

- **Phase:** nested-project-disambiguation
- **Type:** impl
- **Date:** 2026-04-30
- **Lead:** Claude
- **Reviewer:** Codex

## Round 1

### Lead

**Action:** SUBMIT_FOR_REVIEW

Implementation submitted for review.

Changes:

1. tagteam/state.py — Replaced _resolve_project_root() with a tagteam.yaml-anchored walk-up. Discovery order: walk up from cwd looking for tagteam.yaml; if found, use that ancestor; else fall back to git rev-parse --show-toplevel; else cwd. Added _warn_outer_tagteam(root) helper and module-level _warned_outer flag — emits a one-time stderr warning when an ancestor of the resolved root also has tagteam.yaml.

2. tagteam/parser.py — read_cycle_rounds() now resolves project_dir == "." via tagteam.state._resolve_project_root() so the read path matches the write path. Explicit paths still honored verbatim. Updated the docstring.

3. tagteam/cycle.py — In cycle_command(), print '[tagteam] project root: <abs path>' to stderr exactly once before subcommand dispatch. Single emission point covers init/add/status/rounds/render automatically.

4. tests/test_project_root.py — New file with all 7 regression tests from the plan, plus an autouse fixture that resets _cached_project_root and _warned_outer between tests:
   - walk-up to tagteam.yaml
   - nested wins + warning fires (asserts stderr contains 'nested inside another tagteam project' and outer path)
   - no tagteam.yaml -> git fallback (mocked subprocess.run)
   - no tagteam.yaml + no git -> '.' fallback
   - cache pins first resolution across chdir
   - banner emitted on all 5 cycle subcommands
   - read_cycle_rounds with default project_dir resolves project root from a subdirectory

Test results: 7/7 new tests pass. Full suite: 284 passed, 3 skipped (test_cycle.py's existing skip + 2 collection-time skips). No regressions.

Manual /tmp repro of Issue #1:
- /tmp/tt-repro/outer (tagteam.yaml + git) and /tmp/tt-repro/outer/inner (tagteam.yaml + git). From inner: warning fires naming outer, banner shows resolved root = inner, cycle written under inner/docs/handoffs (preserving original semantics that the inner project owns the cycle when it has tagteam.yaml — but the user is now visibly warned).
- /tmp/tt-repro2/proj (only outer tagteam.yaml). From proj/some/sub: banner shows resolved root = proj (walked up), no warning, cycle written under proj/docs/handoffs.

Files: tagteam/state.py, tagteam/parser.py, tagteam/cycle.py, tests/test_project_root.py (new). docs/phases/nested-project-disambiguation.md unchanged from approved round 2.

### Reviewer

**Action:** APPROVE

Approved. The implementation matches the approved plan: root discovery now walks up via tagteam.yaml with nested-project warning, cycle_command emits the project-root banner across cycle subcommands, parser.read_cycle_rounds() now resolves '.' consistently with write paths, the 7 regression tests are present, and local verification is clean (targeted cycle/parser tests plus full pytest: 284 passed, 3 skipped).

---

<!-- CYCLE_STATUS -->
READY_FOR: None
ROUND: 1
STATE: approved