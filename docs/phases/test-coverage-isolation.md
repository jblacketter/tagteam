# Phase: Test Coverage & Isolation

## Summary
Fix pre-existing test failures, add graceful TUI test skipping, and improve test reliability.

## Scope

### 1. Fix pre-existing migrate test failure
`test_migrate.py::test_stops_after_finding_both` fails because `detect_agent_names()` in `migrate.py` doesn't stop after finding both names in the first file — it continues iterating and overwrites with names from later files.

**Root cause:** `glob()` iteration order is non-deterministic, and the break condition doesn't prevent processing subsequent files.

**Fix:** Add `lead_found`/`reviewer_found` flags. Use `sorted()` on glob results for deterministic ordering. Break only when both are set.

### 2. Graceful TUI test skipping
`test_review_dialogue.py` and `test_state_watcher.py` fail at collection time because they import from `ai_handoff.tui`, which unconditionally imports `textual`.

**Fix:** Add `pytest.importorskip("textual")` at the top of both test files, before the TUI imports. Tests skip gracefully with a clear reason when `textual` isn't installed.

### 3. Add pytest configuration
Add `[tool.pytest.ini_options]` to `pyproject.toml` with `testpaths`, standard options.

## Technical Approach

### migrate.py fix
- Add `sorted()` to `glob("*.md")` call for deterministic file ordering
- Add `lead_found`/`reviewer_found` tracking flags
- Break when both found — prevents overwrite from later files

### TUI test isolation
- Add `pytest.importorskip("textual")` at module level in both test files
- This causes pytest to skip the entire module (not error) when textual is missing

### pyproject.toml
- Add `[tool.pytest.ini_options]` section

## Files
- `ai_handoff/migrate.py` — fix `detect_agent_names()` iteration
- `tests/test_review_dialogue.py` — add importorskip
- `tests/test_state_watcher.py` — add importorskip
- `pyproject.toml` — add pytest config

## Success Criteria
- [ ] `test_stops_after_finding_both` passes
- [ ] TUI tests skip cleanly without textual (not error)
- [ ] Full test suite: 0 failures, 0 errors
- [ ] pytest config in pyproject.toml
