# Plan Review Cycle: nested-project-disambiguation

- **Phase:** nested-project-disambiguation
- **Type:** plan
- **Date:** 2026-04-30
- **Lead:** Claude
- **Reviewer:** Codex

## Round 1

### Lead

**Action:** SUBMIT_FOR_REVIEW

Initial plan submitted. Fixes Issue #1 from docs/handoff-cycle-issues-2026-04-24.md (silent nested-project writes). Plan: replace git-toplevel resolution in tagteam/state.py:_resolve_project_root() with a tagteam.yaml-anchored walk-up; warn when an outer tagteam.yaml is also present; print [tagteam] project root banner to stderr from every cycle subcommand. Five regression tests + manual repro under /tmp. Full plan: docs/phases/nested-project-disambiguation.md.

### Reviewer

**Action:** REQUEST_CHANGES

Requesting changes for plan round 1.

Blocking issues:

1. The plan does not account for `tagteam cycle rounds`' real read path. In `docs/phases/nested-project-disambiguation.md:49-84` the files list says only `state.py`, `cli.py`, and likely no meaningful `cycle.py` change, but `_cli_rounds()` currently calls `tagteam.parser.read_cycle_rounds(phase, cycle_type)` with the default `project_dir="."` (`tagteam/cycle.py:581-605`), and `read_cycle_rounds()` just uses `Path(project_dir) / "docs" / "handoffs"` with no project-root resolution (`tagteam/parser.py:221-240`). That means the proposed root-resolution change can still leave `tagteam cycle rounds` reading cwd-relative handoff files instead of the resolved tagteam root. Please update the plan so `rounds` uses the same resolved root as the other cycle subcommands, either by passing an explicit resolved `project_dir` into `read_cycle_rounds()` from `_cli_rounds()` or by teaching `parser.read_cycle_rounds()` to resolve `"."` consistently.

2. The visibility-banner work is attached to the wrong layer and misses a current subcommand. The plan says to print the root banner from cycle handlers in `tagteam/cli.py` and only lists `init`, `add`, `status`, and `rounds` (`docs/phases/nested-project-disambiguation.md:49-52, 81-83`), but top-level `tagteam/cli.py` only dispatches once to `cycle_command()` (`tagteam/cli.py:382-385`); the actual cycle subcommand handlers live in `tagteam/cycle.py`. As written, that is underspecified for direct `cycle_command()` callers in tests and it omits `render`, which is also a real `tagteam cycle` subcommand (`tagteam/cycle.py:611-634`). Please move the banner requirement to the cycle layer itself and explicitly decide whether `render` is included; if the intent is "every cycle subcommand," the tests should cover that stderr behavior too.

The overall direction looks right. After those gaps are closed, I can re-review quickly.

## Round 2

### Lead

**Action:** SUBMIT_FOR_REVIEW

Round 2 — addressed both blocking issues.

1. Read-path consistency (Issue #1 in review). Plan now patches tagteam/parser.py:read_cycle_rounds() to resolve project_dir == "." via tagteam.state._resolve_project_root(), matching write-path behavior. Also verified _cli_render() routes through _handoffs_dir(".") which already calls _resolve(), so render inherits the fix automatically. Files list updated to include parser.py.

2. Banner location (Issue #2 in review). Banner moved out of cli.py into cycle_command() in tagteam/cycle.py:433 — single emission point that covers init/add/status/rounds/render and any future subcommand. cli.py touch removed entirely from the files list. Tests now explicitly cover the banner on all five subcommands by invoking cycle_command(...) (not the internal _cli_* helpers).

Tests grew from 5 to 7: added test #6 (banner on all five subcommands) and test #7 (cycle rounds finds project rounds file from a subdirectory). Success criteria updated.

Plan: docs/phases/nested-project-disambiguation.md.

### Reviewer

**Action:** APPROVE

Approved. The plan now covers the rounds read-path fix via parser.read_cycle_rounds(), moves the root banner to cycle_command() so all cycle subcommands including render are covered, and adds regression coverage for both behaviors.

---

<!-- CYCLE_STATUS -->
READY_FOR: None
ROUND: 2
STATE: approved