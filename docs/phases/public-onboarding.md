# Phase: Public Onboarding

## Summary
Make ai-handoff ready for others to use. Simplify the first-run prompts, add a short "how handoff works" explainer to both the CLI and README, fix the iTerm2 cold-launch bug (session creation currently errors when iTerm2 is not already running), and trim the README to a single Quick Start path using the existing `ai-handoff` console script.

## Current State

**Friction points reported by the project owner (who has been through the setup many times):**

1. **Init asks too many questions.** Current flow (`cli.py:108-170`) prompts: Agent 1 name → Agent 1 role (lead/reviewer) → Agent 2 name → Agent 2 role, with a retry loop to enforce distinct roles. New users have to mentally map "which one is which" twice.
2. **No explanation of what the framework does at install time.** A first-time user runs `ai-handoff quickstart`, gets terminals, and is told to read two files. Nothing explains the lead/reviewer/arbiter cycle, the role of `docs/roadmap.md`, or the fact that handoffs are phase-driven.
3. **iTerm2 does not launch when closed.** The AppleScript in `iterm.py:112-138` starts with `tell application "iTerm2" \n activate` — on paper, `activate` should launch iTerm2, but in practice (confirmed empirically by project owner) the script fails with an error when iTerm2 is not already running. Exact error text will be captured during implementation.
4. **README has multiple "Getting Started" paths.** The current README (README.md:11-62) shows `pip install` → `quickstart` → then three follow-on sections ("Windows", "Usage", "Advanced Setup"). Each references the verbose `python -m ai_handoff …` invocation even though `pyproject.toml:30` already exposes the `ai-handoff` console script.
5. **Priming message gets lost.** After `quickstart` finishes, the message telling each agent what to read is printed once as an indented line (`cli.py:226-227`), easily missed in a wall of setup output.

## Scope

### 1. Simplified init prompts
Replace the 4-question flow with a 2-question flow:

```
Lead agent name: ___
Reviewer agent name: ___
```

- First answer becomes the lead, second becomes the reviewer — no role prompt at all.
- Both names are required; empty input re-prompts (existing `prompt_input()` helper already does this).
- Names stored as-typed (not lowercased) in `ai-handoff.yaml` — matches existing behavior.
- Existing "Overwrite? (y/n)" flow when config already exists is preserved.

### 2. Handoff explainer — two surfaces

**Surface A: CLI.** New string constant `HANDOFF_EXPLAINER` in `cli.py`. To avoid double-printing when `quickstart` internally calls `run_init()`:

- `init_command(show_explainer: bool = True)` — new keyword argument. When True (standalone `ai-handoff init` invocation), prints `HANDOFF_EXPLAINER` just before the existing `GETTING_STARTED` block. When False, suppresses it.
- `run_init(project_dir, show_explainer=False)` — passes `show_explainer=False` when called from `quickstart`, so init does not print the explainer inline.
- `quickstart_command()` — prints `HANDOFF_EXPLAINER` itself as the next-to-last step (before the final priming box). This ensures the explainer appears exactly once on every quickstart path, whether init ran or was skipped because config already existed.

Net effect:
- Standalone `ai-handoff init` → explainer printed once (by init).
- `ai-handoff quickstart` (fresh project) → init runs but with `show_explainer=False`; quickstart prints explainer once at the end.
- `ai-handoff quickstart` (config already exists) → init skipped via `needs_init()`; quickstart prints explainer once at the end.

**Surface B: README.** New section titled "How it works" placed directly after the top-level one-line description, before "Installation".

**Content (shared between the two surfaces, verbatim):**

```
How the handoff works:

• Lead (one AI agent) plans each phase and implements the approved plan.
• Reviewer (a second AI agent) reviews both the plan and the implementation.
• Arbiter (you, the human) breaks ties and approves phases.

Work progresses phase-by-phase. Each phase is listed in docs/roadmap.md and
goes through two review cycles: plan → implementation. If the two agents
can't make progress in 5 rounds, control escalates to the human arbiter.

State is tracked in handoff-state.json (current turn) and
docs/handoffs/<phase>_<type>_rounds.jsonl + <phase>_<type>_status.json
(per-cycle rounds). Either agent can pick up where the other left off at
any time.
```

Keep the tone neutral and factual — this is first-contact documentation. Paths match actual storage in `ai_handoff/cycle.py` (`DIR = "docs/handoffs"`) and `ai_handoff/parser.py`.

### 3. iTerm2 cold-launch fix

**Hypothesis:** `tell application "iTerm2" \n activate` is sufficient to *start* iTerm2, but `create window with default profile` fires before iTerm2 has finished initializing its scripting environment. Result: AppleScript errors out before the three tabs can be created.

**Fix:** split the script into two AppleScript blocks:

```python
def _ensure_iterm_running() -> None:
    """If iTerm2 is not running, launch it and wait for it to be ready."""
    if iterm_is_running():
        return
    _osascript('tell application "iTerm2" to launch')
    # Poll up to 5s for iTerm2 to appear in process list
    for _ in range(25):
        if iterm_is_running():
            # Give AppleScript environment a beat to finish booting
            time.sleep(0.5)
            return
        time.sleep(0.2)
    raise RuntimeError("iTerm2 did not start within 5s")
```

Call `_ensure_iterm_running()` at the top of `create_session()` before the main AppleScript block. Keep the `activate` inside the main block so the window comes to front.

**Edge case — iTerm2's auto-created default window:** when iTerm2 cold-starts, it normally opens an empty default window. Rather than `create window with default profile` (which would give us a second window and stranded the first), the fixed script should:

```applescript
if (count of windows) = 0 then
    create window with default profile
end if
tell current window
    -- existing tab creation logic
end tell
```

The existing "Lead" tab becomes the current tab of the existing window. The two subsequent `create tab` calls remain unchanged.

**Verification plan:** manual test during implementation — quit iTerm2 fully (`Cmd+Q`, confirm quit), then run `ai-handoff session start` from a clean project. Expected: iTerm2 launches, one window opens with three labeled tabs, no error. Record the original error text (pre-fix) in the phase's impl cycle for the record.

### 4. README simplification + `ai-handoff` console script

The `ai-handoff` and `ai-handoff-setup` scripts are already registered in `pyproject.toml` — no code change needed. This item is purely documentation.

**Replace every `python -m ai_handoff <cmd>` in README.md with `ai-handoff <cmd>`.**

**New README structure:**

```
# AI Handoff Framework
[one-line pitch]

## How it works
[the explainer block from scope item 2]

## Quick Start

    pip install git+https://github.com/jblacketter/ai-handoff.git
    cd ~/projects/myproject
    ai-handoff quickstart

You'll be prompted for your two agent names, then quickstart sets up the
workspace and starts a handoff session. It auto-detects the best terminal
backend available on your machine:

- **iTerm2** (macOS, default when iTerm2 is installed) — opens three labeled
  tabs in a single window, auto-launching iTerm2 if it isn't already running.
- **tmux** (Linux, WSL, or macOS without iTerm2) — creates one `tmux` session
  with three labeled panes.
- **manual** (anywhere else, including Windows without WSL) — prints the
  three commands for you to run in terminals you open yourself.

When quickstart finishes it prints what to paste into the Lead and Reviewer
agents to kick off the first handoff. Override the auto-detection with
`--backend iterm2|tmux|manual` if you need a specific one.

## Other platforms
<details>
<summary>tmux (explicit invocation)</summary>
...
</details>

<details>
<summary>Windows / manual fallback</summary>
...
</details>

<details>
<summary>Advanced setup (run each step yourself)</summary>
...
</details>

## CLI Reference
[unchanged command table, but using `ai-handoff` prefix]

## The Saloon
[unchanged, but `ai-handoff serve`]

## Configuration
[unchanged]

## License
MIT
```

The "Usage" section describing `/handoff start my-phase` stays, but moves under "How it works" or becomes a subsection called "Running a handoff".

### 5. Prominent priming message after quickstart

After a successful `quickstart`, print a boxed message that stands out. The copy is **backend-aware** — quickstart already knows which backend was selected (either from `--backend` or from `default_backend()`) and which outcome `ensure_session()` returned.

**Backend → terminology mapping:**
- `iterm2` → "tab" (e.g. "In the Lead tab")
- `tmux` → "pane" (e.g. "In the Lead pane")
- `manual` → "terminal" (e.g. "In your Lead terminal")

**Outcome-conditional rendering:**

When `outcome == "created"` (or `"manual"`), show the boxed priming message:

```
╔══════════════════════════════════════════════════════════╗
║  SESSION READY                                           ║
║                                                          ║
║  In the Lead {surface}, tell {lead_name}:                ║
║    "Read ai-handoff.yaml and .claude/skills/handoff/     ║
║     SKILL.md, then type /handoff"                        ║
║                                                          ║
║  Do the same in the Reviewer {surface}.                  ║
╚══════════════════════════════════════════════════════════╝
```

Where `{surface}` is `tab` / `pane` / `terminal` per the mapping above, and `{lead_name}` / `{reviewer_name}` come from the config just written or read from `ai-handoff.yaml`.

When `outcome == "exists"`, skip the priming box — agents are presumably already primed from the earlier run — and instead print a one-liner: `"Session already running. Switch to it to continue."` (unchanged from today's behavior).

**How quickstart knows the backend:** `quickstart_command()` already parses `--backend` into a local `backend` variable (`cli.py:193-195`). It computes the effective backend by falling back to `default_backend()` if `backend is None`, then passes it to `ensure_session()`. The priming-box renderer reads this same local.

The box is the last thing printed, so it remains on screen.

## Technical Approach

### `cli.py` changes
- **`init_command(show_explainer: bool = True)`:** delete the two role-prompt lines and the distinct-roles retry loop. Replace with two `prompt_input()` calls: `"Lead agent name: "` and `"Reviewer agent name: "`, both with `lowercase=False`. Pass results straight to `write_config()`. When `show_explainer` is True, print `HANDOFF_EXPLAINER` just before `GETTING_STARTED`.
- **`run_init(project_dir, show_explainer=False)`:** new keyword arg plumbed through to `init_command()`. Default False so that callers other than the standalone CLI dispatcher don't duplicate the explainer.
- **CLI dispatcher in `main()`:** when `command == "init"`, call `init_command()` without arguments (keeps default `show_explainer=True`). Unchanged surface for standalone users.
- **`HANDOFF_EXPLAINER`** constant: new module-level string.
- **`quickstart_command()`:** after the existing "Ready!" branch, read the config via `read_config()` to get agent names, determine the effective backend (`backend or default_backend()`), then print `HANDOFF_EXPLAINER`, then the backend-aware priming box (only when outcome is `"created"` or `"manual"`). For `"exists"` outcome, keep today's one-line message.

### `iterm.py` changes
- New private helper `_ensure_iterm_running()` as described in Scope §3.
- `create_session()` calls `_ensure_iterm_running()` as its first step (before the existing session-file check).
- Main AppleScript script: replace `create window with default profile` with the `if (count of windows) = 0` guard described above.
- If `_ensure_iterm_running()` raises `RuntimeError`, `create_session()` catches and returns `False` with a user-readable message: "iTerm2 failed to launch. Is it installed? (Alternatives: `ai-handoff session start --backend tmux` or `--backend manual`)"

### README changes
- Rewrite top-to-bottom per the new structure in Scope §4.
- Every `python -m ai_handoff` replaced with `ai-handoff`.
- Collapsible `<details>` blocks for platform alternatives (GitHub renders these natively).

### Roadmap update
- Add Phase 19: Public Onboarding entry to `docs/roadmap.md` with status "In Progress" and link back to this plan.

## Tests

### `tests/test_quickstart.py` additions (existing file — home for CLI/quickstart coverage)
- **`test_init_prompts_lead_then_reviewer`** — mock `builtins.input` to return `["alice", "bob"]`, verify `ai-handoff.yaml` has `lead.name: alice` and `reviewer.name: bob`. Assert no role prompt was asked (check number of prompts).
- **`test_init_rejects_empty_agent_name`** — mock input to return `["", "alice", "bob"]`, verify the empty one is re-prompted and final config uses `alice` / `bob`.
- **`test_init_preserves_casing`** — input `["ClaudeCode", "CodexCLI"]` → config stores those exact strings (no lowercasing of names).
- **`test_init_overwrite_confirm`** — existing config + mock input `["n"]` → early return, file unchanged. Pins existing behavior.
- **`test_init_shows_explainer_by_default`** — run `init_command()` standalone, capture stdout, assert explainer key phrases ("Lead", "Reviewer", "Arbiter", "docs/roadmap.md") present.
- **`test_init_suppresses_explainer_when_flag_false`** — run `init_command(show_explainer=False)`, assert explainer phrases absent.
- **`test_quickstart_prints_explainer_once`** — fresh `tmp_path` project, mock session backend, run `quickstart_command([])`, assert "How the handoff works" appears exactly once in stdout (not twice).
- **`test_quickstart_priming_box_iterm2`** — mock backend to `iterm2`, outcome `"created"`, assert priming box contains "Lead tab" and the configured lead name.
- **`test_quickstart_priming_box_tmux`** — same with backend `tmux`, assert "Lead pane".
- **`test_quickstart_priming_box_manual`** — same with backend `manual` / outcome `"manual"`, assert "Lead terminal".
- **`test_quickstart_no_priming_box_when_exists`** — mock outcome `"exists"`, assert priming box is NOT printed; the one-liner "Session already running" is.

### `tests/test_iterm.py` additions
- **`test_ensure_iterm_running_noop_when_already_running`** — mock `iterm_is_running()` → True → `_ensure_iterm_running()` does not call osascript.
- **`test_ensure_iterm_running_launches_when_not_running`** — mock `iterm_is_running()` to return False once, then True → assert `_osascript('tell application "iTerm2" to launch')` was called exactly once.
- **`test_ensure_iterm_running_times_out`** — mock `iterm_is_running()` → always False → raises `RuntimeError` after the polling window.
- **`test_create_session_catches_iterm_launch_failure`** — force `_ensure_iterm_running()` to raise → `create_session()` returns False and prints the fallback guidance.

### Manual verification (not automated)
- Cold-launch test on a real Mac: quit iTerm2, run `ai-handoff session start`, confirm clean launch. Record the pre-fix error text in the impl cycle.

### Existing tests
- All 228 existing passing tests must still pass. No changes to session.py, setup.py, or config.py logic — only wiring and text changes.

## Files

- `ai_handoff/cli.py` — simplified init, `show_explainer` plumbing, `HANDOFF_EXPLAINER`, quickstart explainer + backend-aware priming box
- `ai_handoff/iterm.py` — `_ensure_iterm_running()`, cold-launch handling, AppleScript window-count guard
- `README.md` — full rewrite per Scope §4 structure, `ai-handoff` everywhere
- `docs/roadmap.md` — add Phase 19: Public Onboarding
- `tests/test_quickstart.py` — init + explainer + priming box tests (11 new)
- `tests/test_iterm.py` — cold-launch tests (4 new)

**Files deliberately NOT touched:** `setup.py`, `session.py`, `config.py`, `pyproject.toml`, templates, skills. The console script and template-variable substitution machinery already do the right thing.

## Success Criteria
- [ ] `ai-handoff init` asks exactly two questions: lead name, reviewer name
- [ ] Init correctly writes `ai-handoff.yaml` with first-answer-as-lead, preserves name casing
- [ ] `HANDOFF_EXPLAINER` text is printed at the end of both `init` and `quickstart`
- [ ] README has a "How it works" section containing the same explainer
- [ ] README's Quick Start is 3 shell commands (`pip install`, `cd`, `ai-handoff quickstart`) — no other top-level path
- [ ] README Quick Start prose is backend-neutral: describes iterm2, tmux, and manual outcomes without treating iTerm2 as universal
- [ ] All `python -m ai_handoff` invocations in README replaced with `ai-handoff`
- [ ] Platform alternatives (tmux, Windows, advanced) collapsed into `<details>` blocks
- [ ] `ai-handoff session start` launches iTerm2 from a fully-quit state without error (manual verification)
- [ ] `_ensure_iterm_running()` polls for iTerm2 readiness with a 5s timeout and returns a clear error on timeout
- [ ] Quickstart ends with a visually distinct priming box showing the configured agent names
- [ ] Priming box terminology matches the active backend (tab/pane/terminal)
- [ ] Explainer prints exactly once on every quickstart path (fresh and re-run)
- [ ] All 15 new tests pass; all 228 existing tests pass
- [ ] Roadmap updated with Phase 19 entry pointing to this plan
