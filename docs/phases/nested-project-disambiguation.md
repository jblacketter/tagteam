# Phase: Nested-Project Disambiguation for `tagteam cycle`

## Summary

Fix the high-severity bug from `docs/handoff-cycle-issues-2026-04-24.md` (Issue #1): when the user runs `tagteam cycle init` (or any `tagteam cycle` command) from a subdirectory that is itself a git repo with its own `tagteam.yaml`, tagteam silently writes cycle/state files into the nested project rather than the surrounding tagteam project the user intended. The watcher then never sees the change and the cycle is invisible.

The root cause is `tagteam/state.py:_resolve_project_root()`, which uses `git rev-parse --show-toplevel` as the source of truth. In nested-git scenarios, that returns the inner repo. This phase replaces the resolution rule with a `tagteam.yaml`-anchored walk-up, prints the resolved root on every cycle command, and warns when an outer `tagteam.yaml` exists too.

## Scope

### In scope
- Replace `_resolve_project_root()`'s primary discovery rule: walk up from cwd looking for the nearest `tagteam.yaml`. Fall back to `git rev-parse --show-toplevel`, then `.`, only if no `tagteam.yaml` is found.
- Detect and warn when a *parent* directory above the resolved root also contains `tagteam.yaml`. Print the warning to stderr; do not fail.
- Make `tagteam cycle rounds` resolve the project root the same way the write paths do. Currently `_cli_rounds()` (`tagteam/cycle.py:581-608`) calls `tagteam.parser.read_cycle_rounds(phase, cycle_type)` which uses `Path(project_dir) / "docs" / "handoffs"` with no resolution (`tagteam/parser.py:221-240`). Fix by teaching `parser.read_cycle_rounds()` to resolve `project_dir == "."` via `_resolve_project_root()` (and keep explicit paths honored). Verify `_cli_render()` (`tagteam/cycle.py:611-634`) — its `_handoffs_dir(".")` already routes through `_resolve()` so it inherits the new resolution; the only outstanding gap there is its legacy-markdown branch which also calls `_handoffs_dir(".")` and is therefore covered.
- Print `[tagteam] project root: <abs path>` to stderr at the start of every `tagteam cycle` subcommand (init, add, status, rounds, render). Implement this once inside `cycle_command()` (`tagteam/cycle.py:433`) right before dispatch so every subcommand — including future ones — picks it up automatically. Direct callers of `_cli_*` from tests are expected to bypass the banner; tests that want to assert on banner stderr should call `cycle_command()`.
- Add regression tests for: nested-tagteam disambiguation, no-tagteam-yaml fallback to git, ambient warning when parent also has tagteam.yaml, banner stderr emitted by every cycle subcommand (including `render`), and `cycle rounds` resolving to the right project root from a subdirectory.
- Preserve the existing module-level cache (`_cached_project_root`) — but be aware of test isolation needs.

### Out of scope
- An explicit `--root` / `--project-dir` flag on cycle commands (Issue #1 fix suggestion #2). Could be added later; the walk-up + warning covers the silent-failure mode without a flag.
- Lockfile-style cross-call disambiguation (suggestion #3).
- Issues #2–5 from the same doc.
- Changes to `tagteam state` / `tagteam roadmap` resolution semantics. The bug is scoped to cycle writes; reusing the same `_resolve_project_root()` is fine because the same walk-up logic produces a *more* correct answer for state too, but no behavior change for callers that pass an explicit `project_dir`.

## Technical Approach

### 1. Resolution rule (`tagteam/state.py`)

Replace `_resolve_project_root()` with a layered lookup:

```
def _resolve_project_root() -> str:
    1. cache hit → return cached
    2. walk up from Path.cwd():
         for each ancestor, if (ancestor / "tagteam.yaml").exists(): root = ancestor; break
    3. if no tagteam.yaml found: try git rev-parse --show-toplevel (existing fallback)
    4. else: cwd
    5. cache and return
```

Add a sibling helper `_warn_outer_tagteam(root: Path) -> None` that, when `root` has a `tagteam.yaml` AND any ancestor of `root` also has `tagteam.yaml`, prints once to stderr:

```
[tagteam] warning: resolved project root <root> is nested inside another tagteam project at <outer>.
          Cycle/state writes will go to <root>. cd to <outer> if that is wrong.
```

Use a module-level `_warned_outer` flag so the warning fires at most once per process.

### 2. Visibility banner

Inside `cycle_command()` in `tagteam/cycle.py` (the single entry point that dispatches to every `_cli_*` handler), call `_resolve_project_root()` and print `[tagteam] project root: <abs path>` to stderr exactly once before dispatch. This automatically covers `init`, `add`, `status`, `rounds`, `render`, and any subcommand added later. Stderr keeps stdout machine-parseable for consumers piping cycle output. Direct callers of internal `_cli_*` functions from tests bypass the banner intentionally; banner-stderr assertions go through `cycle_command()`.

### 2b. Read-path consistency

In `tagteam/parser.py:read_cycle_rounds()`, when `project_dir == "."`, call `tagteam.state._resolve_project_root()` to get the resolved root (matching the write-path behavior). When `project_dir` is anything else, honor it verbatim. Update the docstring to reflect this. No change to `parse_jsonl_rounds`/`extract_all_rounds` — they take explicit paths and shouldn't change.

### 3. Tests (`tests/test_state_sync.py` or new `tests/test_project_root.py`)

Important: `_resolve_project_root` caches at module level. Tests must reset `tagteam.state._cached_project_root = None` (and `_warned_outer`) in setup/teardown. Use `monkeypatch.chdir(...)` plus `tmp_path` for filesystem fixtures.

Test cases:
1. **Walk-up finds tagteam.yaml.** `tmp/outer/tagteam.yaml`; cwd = `tmp/outer/sub/sub2/`. Resolved root = `tmp/outer`.
2. **Nested tagteam.yaml wins over parent for resolution, but warning fires.** `tmp/outer/tagteam.yaml` AND `tmp/outer/inner/tagteam.yaml`; cwd = `tmp/outer/inner/sub/`. Resolved root = `tmp/outer/inner`. Captured stderr contains "nested inside another tagteam project".
3. **No tagteam.yaml anywhere → git fallback.** Use `monkeypatch` to mock `subprocess.run` returning a fake toplevel, or just create a `.git` dir and let real git run.
4. **No tagteam.yaml and no git → cwd fallback** ("." resolution preserved).
5. **Cache behavior:** second call without resetting cache returns the same path even after chdir. (Existing behavior; pin it.)
6. **Banner emitted on every cycle subcommand.** Invoke `cycle_command(["init", ...])`, `cycle_command(["add", ...])`, `cycle_command(["status", ...])`, `cycle_command(["rounds", ...])`, `cycle_command(["render", ...])` against a tmp tagteam project. For each, capture stderr and assert it contains `[tagteam] project root:` followed by the resolved root.
7. **`tagteam cycle rounds` resolves project root from a subdirectory.** Set up `tmp/proj/tagteam.yaml` and a JSONL rounds file at `tmp/proj/docs/handoffs/<phase>_<type>_rounds.jsonl`. Chdir into `tmp/proj/sub/`. Call `read_cycle_rounds(phase, type)` (default `project_dir="."`) and assert it returns the rounds (i.e. it walked up to `tmp/proj` instead of looking in `tmp/proj/sub/docs/handoffs/`).

### 4. Manual verification

Reproduce the original repro from the bug doc inside `/tmp` (don't pollute the real project tree):

```
mkdir -p /tmp/outer/inner && cd /tmp/outer && git init && touch tagteam.yaml
cd inner && git init && touch tagteam.yaml
tagteam cycle init --phase test --type plan --lead Claude --reviewer Codex \
  --updated-by Claude --content "test"
# Expected: cycle written under /tmp/outer/inner/docs/handoffs/, banner shows
# project root = /tmp/outer/inner, AND warning about parent /tmp/outer.
```

Then test that `cd /tmp/outer && tagteam cycle init ...` (no inner cwd) writes to `/tmp/outer` with no warning.

## Files

- `tagteam/state.py` — Replace `_resolve_project_root()`; add `_warn_outer_tagteam()` and `_warned_outer` flag.
- `tagteam/cycle.py` — In `cycle_command()`, print the resolved-root banner to stderr exactly once before dispatch. (No change to `_resolve()`; it already calls `_resolve_project_root()`, which now does walk-up.)
- `tagteam/parser.py` — In `read_cycle_rounds()`, when `project_dir == "."`, resolve via `tagteam.state._resolve_project_root()`. Honor explicit paths verbatim.
- `tagteam/cli.py` — No change; top-level dispatch already routes to `cycle_command()`.
- `tests/test_project_root.py` (new) or extension of `tests/test_state_sync.py` — Seven regression tests above.

## Success Criteria

- [ ] Running `tagteam cycle init` from a nested tagteam project writes to the nested project AND prints a warning naming the outer tagteam project.
- [ ] Running `tagteam cycle init` from a plain subdir of a single tagteam project resolves up to the project root and writes there (no warning).
- [ ] Every `tagteam cycle` subcommand (init, add, status, rounds, render) prints `[tagteam] project root: <path>` to stderr.
- [ ] `tagteam cycle rounds` invoked from a subdirectory of a tagteam project finds the project's rounds file (read path matches write path).
- [ ] All seven new tests pass.
- [ ] Full existing test suite still passes (`pytest tests/`).
- [ ] No behavior change for callers that pass explicit `project_dir != "."`.

## Open Questions

- Should the banner go to stdout or stderr? Going with stderr to keep stdout clean for piping; flag if reviewer prefers stdout.
- Should the warning be promoted to a hard error behind an env var (e.g., `TAGTEAM_STRICT_ROOT=1`) for CI? Out of scope for this phase but a candidate follow-up.

## Risks

- **Cache interaction across tests.** Module-level cache is a known gotcha — tests must reset it explicitly. Mitigation: pytest fixture in the new test file that clears `_cached_project_root` and `_warned_outer` before each test.
- **Behavior change for users in non-tagteam-yaml repos.** If a user previously relied on git-toplevel resolution from inside a repo without `tagteam.yaml`, the new code falls back to git anyway, so behavior is preserved. No breaking change expected.
