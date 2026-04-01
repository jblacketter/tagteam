# Handoff Cycle: saloon-production-ready (Implementation Review)

- **Phase:** saloon-production-ready
- **Type:** impl
- **Date:** 2026-03-31
- **Lead:** Claude
- **Reviewer:** Codex

**Plan document:** `docs/phases/saloon-production-ready.md`

**Implementation files:**
- `ai_handoff/data/web/app.js` — error banner, backoff polling, postState error handling
- `ai_handoff/server.py` — state POST field validation
- `README.md` — WIP banner removed

---

## Round 1

### Lead

**Action:** SUBMIT_FOR_REVIEW

All scope items implemented:

**1. Web dashboard error handling (app.js)**
- Added `showError(msg)` / `clearError()` — fixed-position red banner at top of page for failed API calls
- `postState()` now shows error banner with HTTP status on failure, network error message on exception
- Errors auto-clear on next successful operation

**2. Exponential backoff polling (app.js)**
- Replaced `setInterval(poll, 2000)` with recursive `setTimeout` via `schedulePoll()`
- On success: reset delay to 2s. On failure: double delay (2s → 4s → 8s → 16s → 30s max)
- `setDisconnected()` now shows specific message ("Server error (500)" vs "Reconnecting...")
- Auto-recovers: on next successful poll, resets delay and clears error

**3. State POST validation (server.py)**
- Added field whitelist: `{turn, status, command, phase, type, round, result, reason, updated_by, run_mode, roadmap}`
- Unknown fields return 400 with specific error message listing the invalid fields

**4. TUI poll failure counter**
- Investigated and confirmed: the existing code at `tui/app.py:137-138` already resets `_poll_failures = 0` on any successful read (not just on state change). No fix needed.

**5. WIP banner removed from README**
- Removed the "Work in Progress" blockquote from the Saloon section

**Tests:** Full suite: 228 passed, 3 skipped, 0 failed.

### Reviewer

**Action:** REQUEST_CHANGES

Blocking finding:

1. **`/api/state` still accepts malformed values and writes them through unchanged.**
   - [server.py](/Users/jackblacketter/projects/ai-handoff/ai_handoff/server.py#L359) only whitelists field names before calling `update_state()`. It does not validate types or allowed values for fields like `turn`, `status`, or `round`, even though the approved phase called for rejecting invalid state payloads.
   - I verified this directly: posting `{"round":"1","turn":["lead"]}` to `/api/state` returned `200` and persisted `round` as a string plus `turn` as a list instead of rejecting the request.
   - That malformed state can leak into client logic immediately. For example, [app.js](/Users/jackblacketter/projects/ai-handoff/ai_handoff/data/web/app.js#L980) assumes `currentState.round` is numeric when it computes the next round, so a string round can produce the wrong value on subsequent transitions.
   - This needs real field/value validation plus targeted coverage for the POST handler.

Validation:
- `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest -q` passes (`228 passed, 3 skipped`).
- Direct API probe confirmed the malformed POST above is currently accepted and written to `handoff-state.json`.

---

## Round 2

### Lead

**Action:** SUBMIT_FOR_REVIEW

Added full field/value validation for state POST:

**`_validate_state_post()` in server.py**
- Field name whitelist (unchanged)
- **Type validation:** Each field checked against expected type (`str`, `int`, `dict`). `round: "1"` → rejected with "must be int". `turn: ["lead"]` → rejected with "must be str".
- **Value validation:** Fields with constrained values (`turn`, `status`, `type`, `run_mode`) checked against allowed sets. `status: "banana"` → rejected with specific error listing valid values. Free-text fields (`command`, `reason`, `result`, `updated_by`) accept any string.
- Validation table: `_STATE_FIELD_VALIDATORS` dict maps field → (type, allowed_values_or_None)

**12 new tests in `test_server_validation.py`:**
- Valid updates pass, unknown fields rejected
- Wrong types: turn as list, round as string → rejected
- Invalid values: turn="nobody", status="banana" → rejected
- Free-text fields accept any string
- Roadmap must be dict, run_mode constrained

Full suite: 240 passed, 3 skipped, 0 failed.

### Reviewer

**Action:** APPROVE

Approved.

The round-1 blocker is resolved:
- `/api/state` now validates field names, types, and constrained values before calling `update_state()`
- the previously accepted malformed payload `{"round":"1","turn":["lead"]}` now returns `400` and leaves the state file unchanged
- targeted validation coverage landed alongside the handler change

Validation:
- `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest -q tests/test_server_validation.py` passes (`12 passed`)
- `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest -q` passes (`240 passed, 3 skipped`)
- End-to-end POST probe confirms malformed state is rejected with `400`

---

<!-- CYCLE_STATUS -->
READY_FOR: lead
ROUND: 2
STATE: approved
