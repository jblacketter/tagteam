# Tagteam Windows Compatibility Notes

Date: 2026-04-01
Prepared from a Windows validation pass against `tagteam 0.1.0` and an active consumer project (`botastrophic`).

## Update: Track A Status

The pre-fix validation below is still useful context, but the current repo behavior is now better than this note originally described.

What now works in this repo:
- `python -m tagteam session start` no longer crashes on Windows when `tmux` is unavailable.
- Session backend selection is auto-detected instead of assuming `iterm2`.
- A `manual` session backend now prints the commands for a Windows-safe workflow.
- `python -m tagteam quickstart` can complete on Windows and hand off to the manual workflow.

What still does not work:
- There is still no native Windows Terminal / `wt.exe` backend.
- Desktop notifications are still macOS-only.
- Fully automated command injection still depends on `tmux` or iTerm2.

Follow-up review status:
- Claude validated the phase with 26 passing tests.
- `README.md` was clarified so quickstart and the manual Windows workflow are separate options rather than a redundant combined sequence.
- `session.py` received two small cleanups after review: one unnecessary f-string was removed and the dead `create_session = create_tmux_session` alias was deleted.
- Recommended next step: Track B, add a native Windows Terminal backend around `wt.exe` and PowerShell.

For Windows today, the recommended path is:
- `python -m tagteam quickstart --dir .`
- `python -m tagteam session start --dir . --backend manual`
- `python -m tagteam watch --mode notify`

## Summary

`tagteam` is only partially Windows-compatible today.

What works:
- State file reads and writes are Windows-safe.
- Core config/template/setup flows appear portable.
- The manual document/state workflow can be used on Windows.

What does not work yet:
- Automated session orchestration still assumes `tmux`.
- Desktop notifications still assume macOS `osascript`.
- `python -m tagteam session start` crashes on Windows instead of failing cleanly.
- There is no Windows terminal backend analogous to the prior iTerm2/mac usage.

## Evidence

### 1. State layer is Windows-safe

`tagteam/state.py` uses `pathlib` and atomic replace semantics:
- `tmp_path = Path(project_dir) / STATE_TMP`
- `tmp_path.replace(path)`

That is the right fix for Windows, where `rename()` fails when the target exists.

### 2. Session orchestration is still tmux-only

`tagteam/session.py`:
- shells out to `tmux` for `has-session`, `new-session`, `split-window`, `send-keys`, `attach`, and `kill-session`
- prints `Install: brew install tmux` on failure

This is not Windows-ready.

### 3. Watcher automation is still macOS/tmux-centric

`tagteam/watcher.py`:
- notifications are sent through `osascript`
- send-to-agent automation is implemented via `tmux send-keys`
- supported modes are only `notify` and `tmux`

On plain Windows, `notify` degrades to console logging because `osascript` does not exist, and `tmux` mode is not usable unless the user is running under WSL with tmux installed.

### 4. Runtime validation on Windows

From the consumer project environment on Windows:
- `python -m tagteam state` worked
- `python -m tagteam watch --help` worked
- `python -m tagteam session start` failed with `FileNotFoundError: [WinError 2]` because it attempted to execute `tmux`

## Recommendation

Treat Windows support as two separate tracks.

### Track A: make the current package fail safely on Windows

This should happen first because it is small and removes confusion immediately.

Recommended changes:
- Detect platform and terminal backend early.
- If `tmux` is unavailable, do not crash with `FileNotFoundError`.
- Replace the current behavior with a clear message such as:
  - `tmux session management is not available on this platform.`
  - `Use watch --mode notify for manual coordination, use WSL+tmux, or configure a Windows terminal backend.`
- Update CLI help and README so Windows limitations are explicit.

### Track B: add a Windows-native orchestration backend

This is the real feature work.

Recommended target:
- Add a Windows backend using `wt.exe` (Windows Terminal) and PowerShell.

Why:
- It is the closest equivalent to the current multi-terminal orchestration model.
- It allows launching distinct tabs/panes for lead, reviewer, and watcher.
- It is a better match for native Windows than trying to preserve tmux semantics directly.

Suggested scope:
- Add a backend abstraction for session management and command delivery.
- Keep `tmux` as one backend.
- Add `windows_terminal` as another backend.
- Optionally keep a `none` or `manual` backend for state-only workflows.

Likely responsibilities to split:
- `session.py`: create/attach/kill sessions by backend
- `watcher.py`: send commands by backend
- a small backend selector based on platform and installed tools

## Proposed implementation order

1. Add backend detection.
2. Make `session start` fail gracefully when the configured backend is unavailable.
3. Introduce a `manual` backend that never tries to inject commands, only prints what should happen next.
4. Add Windows notification support.
5. Add Windows Terminal session creation and command injection.
6. Add tests for backend selection and Windows-safe behavior.
7. Update docs with platform-specific setup instructions.

## Temporary guidance for Windows users

Until a native backend exists, the least risky options are:
- Use the manual workflow only.
- Run the watcher in a non-automation mode for console guidance.
- Use WSL + tmux if full automation is needed immediately.

Avoid representing the current package as fully Windows-compatible. The state layer is compatible; the orchestration layer is not.

## Notes from the consumer project pass

A consumer repo still had stale mac-specific session metadata from an earlier iTerm2 setup, including:
- backend: `iterm2`
- a macOS project path under `/Users/...`

That file did not appear to be read by `tagteam 0.1.0`, so it looks like stale session state rather than active configuration. Still, it is a reminder that session metadata should be clearly scoped, documented, and either regenerated or ignored when changing platforms.

## Suggested next-session goal

Open a session in this repo and implement Track A first:
- backend/platform detection
- graceful Windows behavior for `session start`
- explicit docs for manual mode and WSL fallback

If that lands cleanly, proceed to a Windows Terminal backend.
