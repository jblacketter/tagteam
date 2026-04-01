# Phase: Onboarding Polish

## Summary
Reduce the first-run CLI experience from 5 commands to 2 (`pip install` + one command). New users should go from install to a running handoff session with minimal friction.

## Current State
Today the CLI path requires:
```bash
pip install git+https://github.com/jblacketter/ai-handoff.git
cd ~/projects/myproject
python -m ai_handoff setup .        # copy skills, templates, docs
python -m ai_handoff init           # interactive agent config → ai-handoff.yaml
python -m ai_handoff session start --dir . --launch
```

That's 5 commands, and steps 3-4 must run in the right order. The TUI already unifies setup+init into one guided flow, but most users will use the CLI.

## Scope

### 1. New `quickstart` command
A single command that runs the full onboarding pipeline:

```bash
python -m ai_handoff quickstart [--dir PATH] [--backend iterm2|tmux]
```

Steps it performs:
1. **Setup** — runs `setup_main(project_dir)` if setup is incomplete (idempotent skip if already done)
2. **Init** — runs interactive agent config if `ai-handoff.yaml` doesn't exist (idempotent skip if already configured)
3. **Session start** — creates session with `--launch` (auto-starts agents + watcher)

Each step prints clear status and skips with a message if already complete.

**Session idempotency contract:**
- If a tmux session already exists: print "Session already exists — attaching." and attach to it (same as `session attach`). Do NOT fail.
- If an iTerm2 session file already exists: print "Session already exists — skipping session creation." and skip. Do NOT fail.
- Rationale: `quickstart` should be safe to re-run at any point. A user who ran it halfway, got interrupted, and runs it again should pick up where they left off.

### 2. Make `session start --launch` auto-run setup+init
If a user runs `session start --launch` in a project without `ai-handoff.yaml` or skills:
- Auto-run setup if needed
- Auto-run init if config is missing
- Then proceed with session creation + launch

**Non-interactive guard:** If stdin is not a TTY (e.g., scripted use), skip init and fail fast with: "No ai-handoff.yaml found. Run 'python -m ai_handoff init' interactively first." This prevents hangs in non-interactive contexts.

### 3. `needs_setup()` — robust "setup complete" contract
The guard for "is setup done?" must check more than just `.claude/skills/handoff/SKILL.md`. Setup provisions skills, templates, checklists, workflow docs, roadmap, and decision log.

**Contract:** Setup is considered complete when ALL of these exist:
- `.claude/skills/handoff/SKILL.md` (skill directory)
- `templates/` directory with at least one `.md` file
- `docs/checklists/` directory with at least one `.md` file

If any are missing, `needs_setup()` returns `True` and `quickstart`/`session start --launch` will run `setup_main()`. This catches partial installs without requiring every individual file to be checked.

Note: `setup_main()` itself is already idempotent — it creates directories with `exist_ok=True` and overwrites skills. So re-running setup on a partially initialized project is safe.

### 4. README and CLI output updates
All onboarding surfaces must be updated to reflect the new `quickstart` path:

- **`README.md`** — `quickstart` becomes the primary getting-started command. Existing granular commands remain as advanced options.
- **`cli.py` HELP_TEXT** — update the Workflow section to show `quickstart` as step 1. Keep `setup`/`init` listed in Commands but reframe as advanced.
- **`setup.py` "Next steps" output** — update the post-setup message to reference `quickstart` and `session start --launch` instead of the old multi-step flow.
- **`cli.py` init GETTING_STARTED text** — update to reference `session start --launch` or `quickstart` as the next step.

### 5. Better error messages and recovery
- If `init` is run without `setup`: warn and offer to run setup first
- If `session start` is run without config: point user to `quickstart` or `init`
- Clear "what to do next" guidance after each command completes

Note on `needs_setup()` scope: this intentionally does NOT check for workflow docs (`docs/workflows.md`), `docs/roadmap.md`, or `docs/decision_log.md`. Those are project-specific files that may be edited or removed by users. The 3-point check covers only framework scaffolding that `setup_main()` provisions and that the handoff workflow depends on.

## Technical Approach

### New `ensure_session()` helper (single integration point)
`quickstart` does NOT call `session_command()` (the CLI surface). Instead, both `quickstart` and `session start` share a new lower-level helper:

```python
def ensure_session(project_dir: str, backend: str, launch: bool) -> str:
    """Create or reuse a session. Returns outcome string.

    Returns one of:
      "created"  — new session created (and launched if launch=True)
      "exists"   — session already exists, printed guidance
      "error"    — session creation failed
    """
```

**Integration path:**
- `quickstart_command()` calls `ensure_session()` directly and branches on the return value:
  - `"created"` → print success
  - `"exists"` → print "Session already running — skipping." For tmux, also auto-attach.
  - `"error"` → print error, return non-zero
- `session_command()` also calls `ensure_session()` internally (replaces current inline logic)
- The tmux attach happens INSIDE `ensure_session()` when `backend="tmux"` and session exists — not in `quickstart`. For iTerm, existing session just prints skip message (tabs are already visible).

**Location:** `ensure_session()` lives in `session.py` alongside existing session helpers.

### `quickstart` command
- New function `quickstart_command(args)` in `cli.py`
- Parses `--dir` (default `.`) and `--backend` (default `iterm2`)
- Calls shared helpers in order: `run_setup()` → `run_init()` → `ensure_session()`
- Each step has idempotent guards

### Auto-setup in `session start --launch`
- `_read_launch_commands()` in `session.py` already exists — extend it to call `run_setup()` and `run_init()` when prerequisites are missing
- Non-interactive guard: check `sys.stdin.isatty()` before running `run_init()`. If not TTY, print error and return `None` (launch aborts gracefully, session still created)

### Shared helpers
- `needs_setup(project_dir) -> bool` — in `setup.py`. Checks 3-point contract.
- `needs_init(project_dir) -> bool` — in `cli.py`. Checks `ai-handoff.yaml` existence.
- `run_setup(project_dir)` — in `setup.py`. Wraps `main()` with status prefix. Skips if `not needs_setup()`.
- `run_init(project_dir)` — in `cli.py`. Extracted from `init_command()`. Requires TTY. Skips if `not needs_init()`.

## Tests

### `tests/test_quickstart.py` (new file)
- **`test_quickstart_happy_path`** — tmp_path with no files → runs setup + init (mocked) + session (mocked). Verify all 3 steps called in order.
- **`test_quickstart_skip_setup_if_complete`** — pre-populate skills + templates + checklists → verify `setup_main` NOT called.
- **`test_quickstart_skip_init_if_config_exists`** — pre-populate `ai-handoff.yaml` → verify init NOT called.
- **`test_quickstart_existing_tmux_session`** — mock `session_exists()` → verify `ensure_session` returns `"exists"`, no error.
- **`test_quickstart_existing_iterm_session`** — pre-populate `.handoff-session.json` → verify skip, no error.
- **`test_quickstart_rerun_all_complete`** — all prerequisites + session exist → all steps skip gracefully, exit 0.

### `tests/test_setup.py` additions (or new file)
- **`test_needs_setup_all_present`** — skills + templates + checklists → returns False
- **`test_needs_setup_missing_skills`** — no skills dir → returns True
- **`test_needs_setup_missing_templates`** — skills present, no templates → returns True
- **`test_needs_setup_missing_checklists`** — skills + templates present, no checklists → returns True
- **`test_needs_setup_empty_templates`** — templates dir exists but empty → returns True

### `tests/test_cli.py` additions (or inline)
- **`test_run_init_skips_when_config_exists`** — verify skip behavior
- **`test_run_init_non_tty_fails_fast`** — mock `sys.stdin.isatty()` → verify error message, no prompt

### Existing test files
- `tests/test_iterm.py` — add test for `create_session` when session file already exists (returns success not error)
- `tests/test_config.py`, `tests/test_session.py` — existing tests unmodified, must still pass

## Files
- `ai_handoff/cli.py` — add `quickstart_command()`, extract `run_init()` / `needs_init()`, update HELP_TEXT and GETTING_STARTED
- `ai_handoff/setup.py` — add `needs_setup()` / `run_setup()`, update "Next steps" output
- `ai_handoff/session.py` — add `ensure_session()`, auto-setup+init in `--launch` path
- `ai_handoff/iterm.py` — session idempotency in `create_session()`
- `README.md` — update getting started to feature `quickstart`
- `tests/test_quickstart.py` — new: quickstart happy path, skip, re-entry, session exists
- `tests/test_setup.py` — new or extended: `needs_setup()` tests
- `tests/test_cli.py` — new or extended: `run_init()` tests
- `tests/test_iterm.py` — add existing-session idempotency test
- `docs/roadmap.md` — mark Phase 15 in progress

## Success Criteria
- [ ] `python -m ai_handoff quickstart --dir .` runs setup+init+session in one command
- [ ] Each step skips gracefully if already complete (including existing sessions)
- [ ] Re-running `quickstart` is always safe (no errors, picks up where it left off)
- [ ] `ensure_session()` returns structured outcome (`created`/`exists`/`error`)
- [ ] tmux existing session: auto-attaches. iTerm existing session: prints skip message.
- [ ] `needs_setup()` catches partial installs (skills + templates + checklists)
- [ ] `needs_setup()` intentionally excludes project-specific docs (roadmap, decision_log, workflows)
- [ ] `session start --launch` auto-runs setup+init if needed
- [ ] Non-interactive context: init skips with clear error instead of hanging
- [ ] HELP_TEXT, setup "Next steps", and GETTING_STARTED all reference `quickstart`
- [ ] README shows 2-command path (install + quickstart)
- [ ] Tests: quickstart (6), needs_setup (5), run_init (2), iterm idempotency (1)
- [ ] All existing tests pass
