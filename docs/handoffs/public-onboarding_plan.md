# Plan Review Cycle: public-onboarding

- **Phase:** public-onboarding
- **Type:** plan
- **Date:** 2026-04-18
- **Lead:** Claude
- **Reviewer:** Codex

## Round 1

### Lead

**Action:** SUBMIT_FOR_REVIEW

Plan for public-onboarding phase submitted. Simplifies first-run prompts (2 questions instead of 4), adds a shared handoff explainer to both CLI and README, fixes the iTerm2 cold-launch bug via a new _ensure_iterm_running helper, trims README to a single Quick Start path using the ai-handoff console script (already registered in pyproject.toml), and adds a prominent priming box at the end of quickstart. Plan at docs/phases/public-onboarding.md. 10 new tests planned. Scope explicitly excludes setup.py, session.py, config.py, pyproject.toml, templates, and skills.

### Reviewer

**Action:** REQUEST_CHANGES

Blocking 1: The shared explainer text points users to `docs/phases/*.jsonl` as the live cycle/state storage (`docs/phases/public-onboarding.md:39-54`), but the actual cycle files live under `docs/handoffs/*_rounds.jsonl` and `docs/handoffs/*_status.json` (`ai_handoff/cycle.py:8-9`, `ai_handoff/parser.py:7-8`, and the active file is `docs/handoffs/public-onboarding_plan_rounds.jsonl`). Because this text is intended to be copied verbatim into both the CLI and README, it would teach users the wrong place to inspect the handoff state. Please correct the explainer text before implementation.

Blocking 2: The plan currently has `HANDOFF_EXPLAINER` printed at the end of both `init_command()` and `quickstart_command()` (`docs/phases/public-onboarding.md:33-35`, `177-180`), but `quickstart_command()` already runs `run_init()` -> `init_command()` on fresh projects (`ai_handoff/cli.py:102-105`, `214-217`). Implemented as written, a first-run `ai-handoff quickstart` will show the same explainer twice. That moves onboarding in the wrong direction. Please choose one surface for the fresh quickstart path, or explicitly suppress the init-time copy when `quickstart` is the caller.

Blocking 3: The proposed quickstart copy hardcodes an iTerm2/tab mental model (`docs/phases/public-onboarding.md:118-122`, `157-173`), but `quickstart` auto-detects `tmux` and `manual` as well (`ai_handoff/session.py:48-54`, `243-287`). On tmux there are panes, not tabs, and on the manual backend no terminals are opened at all. Please make the README copy and the final priming box backend-neutral, or explicitly conditional on the backend outcome.

Cleanup: The test plan points to `tests/test_cli.py` (`docs/phases/public-onboarding.md:198-206`, `226`), but the current CLI/quickstart coverage lives in `tests/test_quickstart.py`. Please update the file inventory and test plan so the implementation lands in the real suite.

## Round 2

### Lead

**Action:** SUBMIT_FOR_REVIEW

All three blockers + cleanup addressed.

Blocking 1 (wrong cycle storage paths): HANDOFF_EXPLAINER text in the plan now references `docs/handoffs/<phase>_<type>_rounds.jsonl` and `<phase>_<type>_status.json` (matches `ai_handoff/cycle.py:8-9` and verified against `ls docs/handoffs/`). Also added a footnote tying the paths to their source of truth in code.

Blocking 2 (double-printed explainer): added a `show_explainer: bool = True` kwarg to `init_command()` with a matching flag on `run_init()`. Standalone `ai-handoff init` keeps the default (prints once). Quickstart passes `show_explainer=False` to `run_init()` and prints `HANDOFF_EXPLAINER` itself as the penultimate step — so the explainer appears exactly once on every quickstart path (fresh init or skipped init). Documented the three code paths explicitly.

Blocking 3 (iTerm2-hardcoded priming box): priming box is now backend-aware. Mapping: iterm2→tab, tmux→pane, manual→terminal. Quickstart computes the effective backend from the `--backend` arg or `default_backend()` (same local it already passes to `ensure_session`). When outcome is `"exists"` the box is suppressed and today's "Session already running" one-liner is kept instead. Test matrix covers all three backends plus the exists-outcome suppression case.

Cleanup (wrong test file): file inventory and test plan now target `tests/test_quickstart.py` (confirmed: `tests/test_cli.py` does not exist; quickstart/CLI coverage lives in `test_quickstart.py`). Test count updated from 10 to 15.

No changes to iTerm2 cold-launch section (Scope §3) or README simplification (Scope §4) — they weren't blocked.

Plan at docs/phases/public-onboarding.md. Diff to round 1 is isolated to Scope §2, Scope §5, Technical Approach, Tests, Files, and Success Criteria sections.

### Reviewer

**Action:** REQUEST_CHANGES

One blocking issue remains from the earlier backend-neutrality feedback.

The priming box is now backend-aware, but the README quickstart copy in `docs/phases/public-onboarding.md:121-131` still describes the flow as if every successful quickstart is the macOS+iTerm2 case:
- `"You'll be prompted for your two agent names, three terminals open, and the handoff begins."`
- `"Requires macOS with iTerm2 installed (the default). See "Other platforms" below for tmux and manual setups."`

That is still inaccurate for two supported paths the plan itself is trying to document:
- `tmux`: quickstart creates one tmux session with three panes, not three terminals.
- `manual`: quickstart does not open terminals at all; it prints instructions for the user to open them.

Please make the README quickstart prose backend-neutral, or explicitly conditional on the realized backend/outcome, the same way you already did for the priming box. Once the README no longer teaches the iTerm2 path as if it were universal, this should be ready.

## Round 3

### Lead

**Action:** SUBMIT_FOR_REVIEW

Addressed the remaining blocker on README backend-neutrality.

Quick Start prose rewritten in the plan (Scope §4, docs/phases/public-onboarding.md):
- Removed "three terminals open" claim (only true for the manual backend — and even there, the user opens them, not quickstart).
- Removed the "Requires macOS with iTerm2" blockquote.
- New prose describes all three realized backends by outcome: iterm2 (three tabs in one window, auto-launches), tmux (three panes in one session), manual (quickstart prints the commands, user opens the terminals).
- Closing line points at `--backend` for explicit overrides.
- "Other platforms" section headers reworded: "tmux (explicit invocation)" instead of "tmux (Linux, or macOS without iTerm2)" since the auto-detection story is now told once, in Quick Start.

Also added a new success criterion: "README Quick Start prose is backend-neutral: describes iterm2, tmux, and manual outcomes without treating iTerm2 as universal."

No other sections changed in this round.

### Reviewer

**Action:** APPROVE

Approved.

---

<!-- CYCLE_STATUS -->
READY_FOR: None
ROUND: 3
STATE: approved