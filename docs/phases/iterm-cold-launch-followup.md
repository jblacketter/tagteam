# Phase: iTerm2 Cold-Launch Followup

## Summary

Phase 19's cold-launch fix did not actually resolve the bug. Manual verification on 2026-04-18 (user ran `ai-handoff quickstart` with iTerm2 fully quit) produced:

```
Error creating iTerm2 session: osascript failed: 50:56: syntax error:
Expected end of line, etc. but found class name. (-2741)
```

This phase diagnoses the real root cause and replaces the process-existence poll in `_ensure_iterm_running()` with a scripting-subsystem readiness probe.

## Current State

`ai_handoff/iterm.py:_ensure_iterm_running()` (shipped in commit `ace4854`) polls `iterm_is_running()` — which checks the System Events process list — and adds a 0.5s grace sleep once the process appears. It then executes the main AppleScript block in `create_session()`.

The reported error is a **compile-time** AppleScript error ("syntax error: found class name"), not a runtime failure. The character range 50:56 falls inside the cold-launch branch's AppleScript near `(count of windows)` / `set name to`.

## Root Cause Hypothesis

When iTerm2 has just launched:

1. The iTerm2 process exists → `iterm_is_running()` returns True
2. But iTerm2 has not yet registered its scripting dictionary (sdef) with macOS
3. When osascript tries to compile the main script under `tell application "iTerm2"`, it cannot resolve `windows`, `name`, `sessions`, etc. as iTerm2 terms
4. AppleScript falls back to generic parsing where `name` and `windows` are recognized as class tokens, not properties — compile fails with "found class name"

The 0.5s grace sleep is not the correct signal. Process existence ≠ scripting subsystem ready.

## Scope

1. Replace the process-existence poll in `_ensure_iterm_running()` with a scripting-readiness probe: execute a trivial iTerm2-scripted command in a retry loop until it compiles and runs successfully. Only then return.
2. Keep the timeout at 5s by default but make it configurable via the existing `_ITERM_LAUNCH_TIMEOUT_S` constant (bump to 10s for safety margin on slower/older Macs).
3. Rename helper to `_ensure_iterm_ready()` to reflect the new semantics (scripting-ready, not just process-alive). Update the one caller in `create_session()`.
4. On timeout, raise `RuntimeError` with the last osascript error attached so `create_session()`'s catch block can surface a useful message.
5. Preserve the existing fallback path: on `RuntimeError`, `create_session()` still prints tmux / manual alternatives.
6. No changes to the main AppleScript block itself — the script is correct; it just needs iTerm2's dictionary to be loaded before osascript compiles it.

## Technical Approach

### `ai_handoff/iterm.py`

Replace the existing `_ensure_iterm_running()` body:

```python
_ITERM_LAUNCH_TIMEOUT_S = 10.0   # was 5.0
_ITERM_POLL_INTERVAL_S = 0.2
_ITERM_READY_PROBE = 'tell application "iTerm2" to count windows'


def _ensure_iterm_ready() -> None:
    """Ensure iTerm2 is running AND its scripting dictionary is loaded.

    Checking process existence is not sufficient on cold launch: iTerm2
    registers its AppleScript dictionary with macOS after the process
    appears, and compiling a script against iTerm2's terms will fail
    until the dictionary is registered. We poll with a trivial scripted
    command that exercises the dictionary; once it compiles and runs,
    the main script will too.
    """
    if not iterm_is_running():
        try:
            _osascript('tell application "iTerm2" to launch')
        except RuntimeError:
            # launch itself failed — fall through to the readiness loop
            # so we emit a consistent timeout error.
            pass

    deadline = time.monotonic() + _ITERM_LAUNCH_TIMEOUT_S
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            _osascript(_ITERM_READY_PROBE)
            return
        except RuntimeError as e:
            last_error = e
            time.sleep(_ITERM_POLL_INTERVAL_S)
    raise RuntimeError(
        f"iTerm2 scripting did not become ready within "
        f"{_ITERM_LAUNCH_TIMEOUT_S:.0f}s: {last_error}"
    )
```

Update the call site in `create_session()` to call `_ensure_iterm_ready()` instead of `_ensure_iterm_running()`. Keep the existing try/except that catches `RuntimeError` and prints the backend-fallback guidance.

Keep the existing `_ensure_iterm_running()` name as a module-private alias pointing at the new function only if any test or caller references it. A grep shows the only in-repo caller is `create_session()` itself plus tests — both will be updated, so the old name is removed cleanly.

### Why the probe `count windows`

- `windows` is a term defined in iTerm2's scripting dictionary. If the dictionary isn't loaded, osascript will fail to compile the probe with the same "class name" category of error we see today.
- It is side-effect-free: `count windows` on a cold-launched iTerm2 returns `0` or `1` depending on whether iTerm2 has finished opening its default window. Either result is fine — we only care that the probe compiled and ran.
- It runs in <10ms once iTerm2 is ready, so the retry loop exits quickly.

### Failure handling

If the probe keeps failing for the full 10s window, `_ensure_iterm_ready()` raises `RuntimeError` with the last error message appended. `create_session()` already catches `RuntimeError` from this helper and prints the tmux/manual alternatives. No changes needed to the catch site.

## Files

- `ai_handoff/iterm.py` — replace `_ensure_iterm_running()` with `_ensure_iterm_ready()`; bump timeout to 10s; add probe constant; update call site.
- `tests/test_iterm.py` — update existing cold-launch tests to target the new function; add probe-retry test.

**Files deliberately NOT touched:** the main AppleScript in `create_session()`, `session.py`, `setup.py`, anything outside `iterm.py` and its tests. This is a surgical fix to the readiness check.

## Tests

### `tests/test_iterm.py` changes

- **Update** `test_ensure_iterm_running_noop_when_already_running` → `test_ensure_iterm_ready_returns_after_probe_succeeds`: mock `_osascript` so the probe succeeds on first call; assert no additional calls.
- **Update** `test_ensure_iterm_running_launches_when_not_running` → `test_ensure_iterm_ready_launches_then_polls`: mock `iterm_is_running()` → False, then let the probe succeed on the 3rd call; assert `launch` was called exactly once AND the probe retried at least twice.
- **Update** `test_ensure_iterm_running_times_out` → `test_ensure_iterm_ready_times_out_on_probe_failure`: mock `_osascript` to always raise `RuntimeError`; assert `_ensure_iterm_ready()` raises after the timeout, and the final error includes the last probe error text.
- **Keep** `test_create_session_catches_iterm_launch_failure` — still valid because `create_session()` still catches `RuntimeError` from the helper.
- **New** `test_ensure_iterm_ready_cold_path_retries_until_probe_compiles`: mock `iterm_is_running()` → False; mock `_osascript` to succeed on the `launch` call, then raise `RuntimeError` 5 times, then succeed on the 6th probe call. Assert `_ensure_iterm_ready()` returns without raising and made **exactly 7** `_osascript` calls in this order: `(1) launch, (2-6) five failed probes, (7) one successful probe`.
- **New** `test_ensure_iterm_ready_warm_path_retries_until_probe_compiles`: mock `iterm_is_running()` → True (so `launch` is skipped); mock `_osascript` to raise 5 times then succeed on the 6th call. Assert `_ensure_iterm_ready()` returns without raising and made **exactly 6** `_osascript` calls: `(1-5) five failed probes, (6) one successful probe`. The `launch` string must not appear in any call.
- **New** `test_ensure_iterm_ready_continues_when_launch_raises`: pins the intentional swallow-and-retry behavior. Mock `iterm_is_running()` → False; mock `_osascript` so the first call (the `launch`) raises `RuntimeError("launch boom")`, then the next probe call succeeds. Assert `_ensure_iterm_ready()` returns without raising (the launch error is intentionally not surfaced) and the second `_osascript` call was the probe. This test documents that a failed `launch` is not fatal — some macOS configurations start iTerm2 via LaunchServices even when direct AppleScript `launch` errors, so we keep polling.

All `time.sleep` calls in the retry loop must be mockable (use `monkeypatch.setattr("time.sleep", ...)` in tests) so the suite stays fast.

### Manual verification (human arbiter step)

Same protocol as Phase 19:
1. `ai-handoff session kill` (if any live session)
2. Cmd+Q in iTerm2, confirm quit-all-tabs
3. From Terminal.app: `ai-handoff quickstart` (or `ai-handoff session start`) in a fresh project
4. Expected: iTerm2 launches, one window with three labeled tabs, no error
5. If the probe timed out, capture the exact error and feed it back to Codex

### Existing tests

All previously-passing tests must still pass. No changes to behavior when iTerm2 is already running (warm path) — the probe succeeds on first call and `_ensure_iterm_ready()` returns just as fast as the old helper did.

## Success Criteria

- [ ] `_ensure_iterm_ready()` probes iTerm2's scripting dictionary with `tell application "iTerm2" to count windows` in a retry loop
- [ ] Timeout constant raised to 10s; tests confirm timeout behavior
- [ ] On timeout, raised `RuntimeError` includes the last probe error for diagnostics
- [ ] `create_session()` calls `_ensure_iterm_ready()` and surfaces its `RuntimeError` via the existing tmux/manual fallback message
- [ ] Manual cold-launch verification: user quits iTerm2 and runs `ai-handoff quickstart` successfully with no AppleScript error
- [ ] All existing tests pass; 3 new tests added (`test_ensure_iterm_ready_cold_path_retries_until_probe_compiles`, `test_ensure_iterm_ready_warm_path_retries_until_probe_compiles`, `test_ensure_iterm_ready_continues_when_launch_raises`); 3 existing tests updated to match new function name + semantics
