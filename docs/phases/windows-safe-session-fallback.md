# Phase: windows-safe-session-fallback

## Summary

Make `ai-handoff` fail gracefully on Windows instead of crashing when no automation backend (tmux/iTerm2) is available. Add a `manual` session backend and auto-detection logic.

## Scope

- Backend auto-detection: iterm2 → tmux → manual
- Backend validation with platform-specific error messages
- `manual` backend that prints a 3-terminal workflow
- `session_exists()` catches `FileNotFoundError` instead of crashing
- `ensure_session()` returns `"manual"` as a valid outcome
- `quickstart` handles manual fallback as success
- README and CLI help updated with Windows guidance
- Test coverage for backend detection, manual fallback, and validation

## Technical Approach

- Add `default_backend()` to `session.py` with platform/tool detection
- Add `_validate_backend()` for clear error messages when a backend isn't available
- Add `create_manual_session()` that prints commands instead of automating
- Change `ensure_session()` default from `"iterm2"` to `None` (auto-detect)
- Change `_parse_backend()` to return `None` instead of hardcoding iterm2

## Files

- `ai_handoff/session.py` — core backend detection, validation, manual backend
- `ai_handoff/cli.py` — quickstart manual outcome, help text, getting-started
- `ai_handoff/setup.py` — post-setup output with Windows fallback guidance
- `README.md` — Windows section with supported/unsupported features
- `docs/windows-compatibility-notes.md` — Track A status update
- `tests/test_quickstart.py` — new tests for backend detection and manual fallback

## Success Criteria

- `python -m ai_handoff session start --dir .` prints manual workflow on Windows (no crash)
- `python -m ai_handoff session start --dir . --backend tmux` exits with guidance on Windows
- `python -m ai_handoff quickstart --dir .` completes on Windows via manual fallback
- All existing tests pass
- `python -m compileall ai_handoff` passes
