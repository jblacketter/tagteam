# Phase: Saloon Production Ready

## Summary
Polish the web dashboard and TUI to production quality. Fix error handling, add input validation, improve resilience, and remove the WIP banner from README.

## Scope

### 1. Web dashboard error handling (app.js)
- **User-visible error messages:** Replace silent `catch` blocks with UI feedback. Show a banner/toast when API calls fail instead of only logging to console.
- **Exponential backoff for polling:** Current 2s fixed interval polls forever even when server is down. Add backoff (2s → 4s → 8s → 30s max) with auto-recovery when connection restores.
- **Fetch error detail:** Replace bare `throw new Error()` with status-aware errors that include HTTP status and response body.

### 2. Input validation (server.py)
- **State POST validation:** Validate that incoming state updates only contain allowed fields (`turn`, `status`, `command`, `phase`, `type`, `round`, `result`). Reject unknown fields.
- **HTML escaping:** Ensure phase names and agent names are escaped in HTML output to prevent XSS if user-supplied values contain `<` or `>`.

### 3. Poll failure recovery (app.js + TUI)
- **app.js:** Distinguish network timeout vs 404 vs 500 in error handler. Show "Reconnecting..." for network errors, "Server error" for 5xx.
- **TUI:** Reset `_poll_failures` counter on any successful read (not just on state change).

### 4. Remove WIP banner
- Remove the "Work in Progress" blockquote from README's Saloon section after all above changes land.

## Technical Approach

### app.js changes
- Add `showError(msg)` / `clearError()` helper that shows/hides a fixed-position error banner
- Wrap all fetch calls in a `safeFetch()` helper that handles errors uniformly
- Replace `setInterval` polling with recursive `setTimeout` that adjusts delay based on success/failure

### server.py changes
- Add field whitelist to state POST handler
- Validate field types (turn must be string in valid set, round must be int, etc.)

### TUI changes (tui/app.py)
- Reset `_poll_failures = 0` when state is read successfully (not only when state changes)

## Files
- `ai_handoff/data/web/app.js` — error UI, backoff polling, fetch error detail
- `ai_handoff/server.py` — state POST validation
- `ai_handoff/tui/app.py` — poll failure counter reset
- `README.md` — remove WIP banner

## Success Criteria
- [ ] Network errors show user-visible message in web dashboard
- [ ] Polling uses exponential backoff with auto-recovery
- [ ] State POST rejects invalid/unknown fields
- [ ] TUI poll failures reset on successful read
- [ ] WIP banner removed from README
- [ ] All existing tests pass
