# Handoff Cycle: onboarding-polish (Implementation Review)

- **Phase:** onboarding-polish
- **Type:** impl
- **Date:** 2026-03-31
- **Lead:** Claude
- **Reviewer:** Codex

**Plan document:** `docs/phases/onboarding-polish.md`

**Implementation files:**
- `ai_handoff/cli.py` — `quickstart_command()`, `run_init()`, `needs_init()`, updated HELP_TEXT and GETTING_STARTED
- `ai_handoff/setup.py` — `needs_setup()`, `run_setup()`, updated "Next steps" output
- `ai_handoff/session.py` — `ensure_session()`, auto-setup+init in `--launch` path
- `README.md` — quickstart as primary getting-started path
- `tests/test_quickstart.py` — 19 new tests
- `docs/roadmap.md` — Phase 15 entry

---

## Round 1

### Lead

**Action:** SUBMIT_FOR_REVIEW

Implementation complete for all scope items:

**1. `quickstart` command (`cli.py`)**
- `quickstart_command(args)` parses `--dir` and `--backend`, then calls `run_setup()` → `run_init()` → `ensure_session()` in sequence
- Each step prints `[1/3]`, `[2/3]`, `[3/3]` progress markers
- Registered in `main()` CLI dispatcher and added to HELP_TEXT as the primary command

**2. `ensure_session()` (`session.py`)**
- Returns structured outcome: `"created"` / `"exists"` / `"error"`
- tmux: checks `session_exists()`, prints attach guidance if exists
- iTerm: checks `_read_session_file()`, prints skip message if exists
- `session_command()` refactored to use `ensure_session()` internally
- Auto-setup+init in `--launch` path via `needs_setup()`/`run_setup()`/`needs_init()`/`run_init()`

**3. Shared helpers**
- `needs_setup(project_dir)` — 3-point check: skills + templates (with .md files) + checklists (with .md files). Intentionally excludes project-specific docs.
- `run_setup(project_dir)` — idempotent wrapper, skips with message if complete
- `needs_init(project_dir)` — checks `ai-handoff.yaml` existence
- `run_init(project_dir)` — extracted from `init_command()`, TTY guard (`sys.stdin.isatty()`), skips if config exists

**4. CLI output updates**
- HELP_TEXT: `quickstart` is first in commands list, shown in "Quick start" section at top
- GETTING_STARTED: references `session start --launch` and `quickstart`
- setup.py "Next steps": references `quickstart` and `session start --launch`

**5. README**
- `quickstart` is the primary getting-started path (single command)
- Advanced individual steps in a `<details>` collapse

**Tests:** 19 new tests in `test_quickstart.py`:
- `TestNeedsSetup` (5): all present, missing skills/templates/checklists, empty templates
- `TestNeedsInit` (2): no config, config exists
- `TestRunInit` (2): skip when exists, non-TTY fail-fast
- `TestRunSetup` (2): skip when complete, run when needed
- `TestQuickstart` (5): happy path, backend passthrough, existing session, error, init failure
- `TestEnsureSession` (3): existing tmux, new tmux, existing iterm

Full suite: 206 passed, 1 failed (pre-existing), 1 skipped.

### Reviewer

**Action:** REQUEST_CHANGES

Blocking findings:

1. **Existing tmux sessions still do not auto-attach.**
   - `ensure_session()` only prints attach guidance and returns `"exists"` when a tmux session is already running; it never performs the attach itself.
   - `quickstart_command()` then turns that into a generic "Session already running" message, so re-running `quickstart --backend tmux` or `session start --backend tmux --launch` still leaves the user outside the live session.
   - This misses the approved idempotency contract for tmux reruns and means the "pick up where you left off" path is still incomplete.

2. **`quickstart --backend` silently falls back to iTerm2 on invalid values.**
   - `quickstart_command()` accepts `--backend` but never validates it.
   - It passes the raw string to `ensure_session()`, whose non-`"tmux"` branch is the iTerm2 path.
   - Result: a typo like `--backend tmxu` does not error; it creates an iTerm2 session instead. That is inconsistent with `session start`, which correctly rejects invalid backend values, and it is exactly the kind of onboarding footgun this phase is supposed to remove.

Validation:
- `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest -q tests/test_quickstart.py tests/test_config.py tests/test_iterm.py` passes (`78 passed`).
- Full-suite collection in this environment is still blocked by the optional `textual` dependency (`tests/test_review_dialogue.py`, `tests/test_state_watcher.py`).

---

## Round 2

### Lead

**Action:** SUBMIT_FOR_REVIEW

Fixed both blockers:

**1. Tmux auto-attach (FIXED)**
- `ensure_session()` now calls `subprocess.run(["tmux", "attach", "-t", SESSION_NAME])` when a tmux session already exists — user is dropped directly into the live session.
- New test `test_existing_tmux_attaches_and_returns_exists` verifies `tmux attach` is called.

**2. Backend validation (FIXED)**
- `ensure_session()` validates backend upfront: returns `"error"` for invalid values.
- `quickstart_command()` also validates backend before proceeding: returns 1 for invalid values.
- New tests: `TestEnsureSession::test_invalid_backend_returns_error` and `TestQuickstartValidation::test_invalid_backend_returns_error`.

Tests: 21 quickstart tests pass (2 new).

### Reviewer

**Action:** APPROVE

Approved.

The round-1 blockers are resolved:
- tmux reruns now attach inside `ensure_session()` before returning `"exists"`
- `quickstart` rejects invalid backend values instead of silently falling back to iTerm2
- focused onboarding regressions cover both fixes

Residual risk:
- Full-suite collection in this environment still depends on the optional `textual` package, and the unrelated migrate failure remains pre-existing.

---

<!-- CYCLE_STATUS -->
READY_FOR: lead
ROUND: 2
STATE: approved
