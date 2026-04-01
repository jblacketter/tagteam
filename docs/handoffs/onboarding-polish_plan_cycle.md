# Handoff Cycle: onboarding-polish (Plan Review)

- **Phase:** onboarding-polish
- **Type:** plan
- **Date:** 2026-03-31
- **Lead:** Claude
- **Reviewer:** Codex

**Plan document:** `docs/phases/onboarding-polish.md`

---

## Round 1

### Lead

**Action:** SUBMIT_FOR_REVIEW

Submitting the plan for Phase 15: Onboarding Polish. Goal is to reduce the first-run experience from 5 commands to 2 (`pip install` + `quickstart`).

Key design decisions:

1. **`quickstart` command** — single command that runs setup → init → session start (with `--launch`). Each step is idempotent: skips with a message if already complete.
2. **Auto-setup in `session start --launch`** — if config/skills are missing, auto-runs setup+init before launching. Makes `session start --launch` a valid single-command onboarding path too.
3. **Extracted helpers** — `needs_setup()`, `needs_init()`, `run_init()` are shared between `quickstart` and `session start --launch` to avoid duplication.
4. **README** — `quickstart` becomes the primary getting-started path. Existing granular commands remain documented as advanced options.

Please review the plan at `docs/phases/onboarding-polish.md`.

### Reviewer

**Action:** REQUEST_CHANGES

Three blocking gaps to resolve before implementation:

1. **The plan promises idempotent `quickstart`, but it does not define what happens when a session already exists.**
   - In current code, `session start` is not idempotent: `ai_handoff/session.py` hard-fails if the tmux session already exists, and iTerm session creation also refuses to proceed when its session file already exists.
   - Since the phase explicitly says each onboarding step should "skip gracefully if already complete", you need to define the session-step contract too: should `quickstart` treat an existing session as success/skip, attach to it, or exit with a specific recovery message?
   - Without that, rerunning the headline onboarding command violates the phase's main UX promise.

2. **`needs_setup()` is underspecified and too weak as written.**
   - The plan says setup should run if `.claude/skills/handoff/SKILL.md` is missing, but current `setup.py` provisions much more than that: skills, templates, checklists, workflow docs, `docs/roadmap.md`, and `docs/decision_log.md`.
   - A partially initialized project could have the skill directory present but still be missing the rest of the scaffolding, causing `quickstart` / `session start --launch` to skip setup and leave onboarding incomplete.
   - Please define a concrete "setup complete" contract (or explicitly narrow what quickstart guarantees) so the guard cannot silently accept broken partial installs.

3. **The doc update scope misses the product's actual onboarding surfaces.**
   - The plan only lists `README.md`, but current onboarding guidance is also emitted by `ai_handoff/cli.py` help text and `ai_handoff/setup.py` "Next steps" output.
   - If those are not updated, users will still see the old setup → init → session flow in the CLI itself, which directly contradicts the phase goal of a 2-command onboarding path.
   - Please add those surfaces to scope/files/success criteria so the new onboarding story is consistent both in docs and in-command output.

Non-blocking note:
- Consider defining the non-interactive behavior for auto-running `init` inside `session start --launch` (for example, fail fast with guidance when stdin is not a TTY) so this doesn't unexpectedly hang scripted use.

---

## Round 2

### Lead

**Action:** SUBMIT_FOR_REVIEW

Addressed all 3 blocking gaps plus the non-blocking note:

**1. Session idempotency (RESOLVED)**
- Defined explicit contract: tmux existing session → attach instead of fail. iTerm existing session → skip with message. Both return boolean for `quickstart` to adjust output.
- `quickstart` is now explicitly safe to re-run at any point — it picks up where it left off.

**2. `needs_setup()` robustness (RESOLVED)**
- Changed from single-file check to a 3-point contract: `.claude/skills/handoff/SKILL.md` + `templates/` with at least one `.md` + `docs/checklists/` with at least one `.md`.
- Catches partial installs. `setup_main()` is already idempotent (creates dirs with `exist_ok=True`, overwrites skills) so re-running on partial state is safe.

**3. CLI output surfaces (RESOLVED)**
- Added all 4 onboarding surfaces to scope/files/success criteria:
  - `README.md` — quickstart as primary path
  - `cli.py HELP_TEXT` — workflow section updated
  - `setup.py "Next steps"` — post-setup message updated
  - `cli.py GETTING_STARTED` — post-init message updated

**Non-blocking (RESOLVED)**
- Added non-interactive guard: `sys.stdin.isatty()` check before running `init`. Non-TTY contexts get a clear error: "No ai-handoff.yaml found. Run 'python -m ai_handoff init' interactively first."

Updated plan at `docs/phases/onboarding-polish.md`.

### Reviewer

**Action:** REQUEST_CHANGES

Two blocking gaps remain:

1. **The session-step integration contract is still internally inconsistent.**
   - In the Technical Approach, `quickstart` is described as calling `session_command()` directly.
   - But the Session idempotency section then relies on lower-level session helpers returning "new vs existing session" booleans so `quickstart` can decide what to do, and the Scope section says tmux reruns should actively attach.
   - Those pieces do not line up yet. `session_command()` is a CLI surface, while the plan's idempotency behavior depends on structured return semantics from lower-level helpers. Please specify one concrete integration path:
     - either `quickstart` calls a new/helper API that returns a structured session outcome (`created`, `attached`, `skipped_existing`, `error`), or
     - `session_command()` itself is refactored to expose that outcome in a way `quickstart` can use.
   - Also state explicitly where the tmux attach happens: inside `quickstart`, inside `session start`, or in a shared helper. Right now the user-facing contract says "attach", but the technical path only says "print attach guidance" / boolean return.

2. **The plan still has no explicit test strategy for the new onboarding behavior.**
   - This phase changes CLI flow, session behavior, TTY handling, and setup detection. "All existing tests pass" is not enough for a new top-level onboarding command.
   - Please add the intended test coverage to the plan/files/success criteria. At minimum I would expect targeted tests for:
     - `quickstart` happy path and skip/re-entry behavior
     - existing tmux session vs existing iTerm session file behavior
     - non-TTY init guard
     - `needs_setup()` partial-install detection
     - updated CLI/help output where user guidance changes materially

Non-blocking note:
- The narrowed `needs_setup()` contract is much clearer than before. If you intentionally do not require workflow docs / roadmap / decision-log presence for onboarding completeness, say that explicitly so the reduced guarantee is clear.

---

## Round 3

### Lead

**Action:** SUBMIT_FOR_REVIEW

Addressed both blockers:

**1. Session integration path (RESOLVED)**
- Replaced hand-wavy `session_command()` call with a concrete new helper: `ensure_session(project_dir, backend, launch) -> str` returning `"created"` / `"exists"` / `"error"`.
- `quickstart` calls `ensure_session()` directly and branches on the outcome.
- `session_command()` also refactored to use `ensure_session()` internally.
- tmux attach happens INSIDE `ensure_session()` (not in quickstart). iTerm existing session prints skip (tabs already visible).
- Single integration point — no ambiguity about where behavior lives.

**2. Test strategy (RESOLVED)**
- Added full test plan with 14 targeted tests across 4 files:
  - `test_quickstart.py` (6 tests): happy path, skip-setup, skip-init, existing tmux, existing iterm, full re-run
  - `test_setup.py` (5 tests): `needs_setup()` — all present, missing skills, missing templates, missing checklists, empty templates
  - `test_cli.py` (2 tests): `run_init()` skip when config exists, non-TTY fail-fast
  - `test_iterm.py` (1 test): existing-session idempotency

**Non-blocking (RESOLVED)**
- Added explicit note: `needs_setup()` intentionally excludes project-specific docs (roadmap, decision_log, workflows) — only framework scaffolding is checked.

Updated plan at `docs/phases/onboarding-polish.md`.

### Reviewer

**Action:** APPROVE

The remaining blockers are resolved. The plan now has a single concrete session integration path via `ensure_session()`, explicitly locates tmux attach behavior inside that helper, and adds targeted coverage for quickstart flow, setup detection, TTY handling, and existing-session behavior. No blocking plan issues remain.

---

<!-- CYCLE_STATUS -->
READY_FOR: lead
ROUND: 3
STATE: approved
