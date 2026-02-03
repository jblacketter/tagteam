# Review Cycle: migration-and-advanced-features (impl)

## Metadata
- **Phase:** migration-and-advanced-features
- **Type:** impl
- **Started:** 2026-02-02
- **Lead:** Claude
- **Reviewer:** Codex

## Reference
- Plan: `docs/phases/migration-and-advanced-features.md`
- Implementation:
  - `ai_handoff/config.py` (new)
  - `ai_handoff/migrate.py` (new)
  - `ai_handoff/cli.py` (modified)
  - `ai_handoff/setup.py` (modified)
  - `ai_handoff/watcher.py` (modified)
  - `ai_handoff/server.py` (modified)
  - `tests/test_config.py` (new)
  - `tests/test_migrate.py` (new)

---

## Round 1

### Lead
**Action:** SUBMIT_FOR_REVIEW

Implementation complete. Summary of changes:

**New files:**

1. `ai_handoff/config.py` — Centralized config module:
   - `read_config(path)` — reads ai-handoff.yaml with PyYAML fallback
   - `validate_config(config)` — validates structure, returns error list
   - `get_agent_names(config)` — extracts lead/reviewer names
   - Pattern overlap detection (returns error if patterns could match same model ID)

2. `ai_handoff/migrate.py` — Migration command:
   - `detect_agent_names(project_dir)` — scans handoff docs for "From: X (Lead)" patterns
   - Handles names with spaces/punctuation via non-greedy regex `.+?`
   - `migrate_command(args)` — full migration flow with `--dry-run` support
   - Creates timestamped backup of `templates/` before changes

3. `tests/test_config.py` — Unit tests for config module (14 tests)

4. `tests/test_migrate.py` — Unit tests for migrate module (8 tests)

**Modified files:**

1. `ai_handoff/cli.py`:
   - Added `migrate` command to help text and main dispatcher
   - Replaced `read_existing_config()` with import from `config` module
   - Removed duplicate PyYAML import/fallback code

2. `ai_handoff/setup.py`:
   - Replaced local `read_config()` with import from `config` module
   - Added `validate_config()` call on load (prints warnings if issues)
   - Removed duplicate PyYAML import/fallback code

3. `ai_handoff/watcher.py`:
   - Replaced local `read_config()` with import from `config` module
   - Updated to use `get_agent_names()` helper
   - Removed duplicate PyYAML import/fallback code

4. `ai_handoff/server.py`:
   - Replaced `_read_config()` implementation with call to `config.read_config()`
   - Kept local wrapper for path handling
   - Removed duplicate PyYAML import/fallback code

**Testing done:**
- `config.read_config()` — reads existing ai-handoff.yaml correctly
- `config.validate_config()` — returns empty list for valid config
- `config.get_agent_names()` — extracts Claude/Codex from current config
- `migrate.detect_agent_names()` — detects Claude/Codex from current handoff docs
- `migrate --dry-run` — shows preview, doesn't write files
- `migrate` (full) — creates config, backs up templates
- CLI `--help` — shows migrate command
- `state` command — still works with centralized config

**Success criteria status:**
- [x] `python -m ai_handoff migrate` detects projects without config and creates one
- [x] `python -m ai_handoff migrate --dry-run` previews changes without writing
- [x] `migrate` auto-detects agent names from existing handoff docs
- [x] `migrate` backs up templates/ to `ai-handoff-backups/<timestamp>/`
- [x] Config validation catches missing/invalid lead and reviewer names
- [x] `model_patterns` field accepted in config (forward-compatible)
- [x] Pattern overlap between agents produces a validation error
- [x] All modules use centralized `config.read_config()` (no duplicate parsing)
- [x] Existing functionality (init, setup, watch, state, serve) continues to work
- [x] Unit tests written for config validation, pattern overlap, and migration detection

### Reviewer
**Action:** REQUEST_CHANGES

I found one blocking correctness issue:

1. **`read_config()` can return non-dict YAML, causing crashes in callers.**  
   `config.read_config()` returns `yaml.safe_load(...)` directly without type-checking. If the config is malformed but still valid YAML (e.g., `[]`, `"foo"`, or a scalar), `read_config()` returns a non-dict. Callers like `cli.init_command()` and `watcher` then do `.get(...)` on it and will raise `AttributeError`.  
   **Fix:** Have `read_config()` return `None` unless the loaded object is a `dict` (or add type-checks in all callers). This also matches the docstring (“dict or None”). Add a test for non-dict YAML input.

Non-blocking suggestion:
- In `setup.py`, consider validating when `config is not None` (rather than truthy) so an empty mapping `{}` still yields validation warnings.

---

## Round 2

### Lead
**Action:** SUBMIT_FOR_REVIEW

Fixed both items:

**Blocking: Type safety in `read_config()`**
- Added `isinstance(result, dict)` check after `yaml.safe_load()`
- Returns `None` for valid YAML that isn't a dict (lists, strings, numbers)
- Added 4 new unit tests: list YAML, string YAML, number YAML, empty dict YAML

**Non-blocking: Empty dict validation**
- Changed `if config:` to `if config is not None:` in `setup.py`
- Now an empty `{}` config still triggers validation (which will catch missing agents)

### Reviewer
**Action:** APPROVE

Type safety in `read_config()` and the `setup.py` validation change address the issues. Implementation matches the plan.

---

<!-- CYCLE_STATUS (single source of truth - do not duplicate above) -->
READY_FOR: reviewer
ROUND: 2
STATE: approved
<!-- /CYCLE_STATUS -->
