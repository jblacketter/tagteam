# Phase: Stale Handoff Diagnostics

## Summary
Add a `state diagnose` command that analyzes handoff state and provides actionable recovery guidance. Make it easy to understand why a handoff is stuck and how to fix it.

## Scope

### 1. New `state diagnose` command
Reads state file, cycle docs, and optionally checks agent responsiveness. Outputs a diagnostic report.

**Checks performed:**
- **Stuck in ready** — If `status == "ready"` and `updated_at` is > 5 min ago, warn. > 30 min, flag as stuck.
- **Stale completion metadata** — If `status != "done"` but `result` is set (e.g., leftover `"approved"` from a previous cycle), flag as stale overlay.
- **Cycle-state sync** — Look up the cycle status file addressed by `state["phase"]` and `state["type"]` from `handoff-state.json`, then compare `round` and `ready_for` fields. This explicitly checks "does the cycle file the state points to agree with the state?" — it does NOT claim to find a cycle file independently of state. If the state's phase/type are themselves wrong, the stuck-in-ready and stale-metadata checks catch that instead.
- **History anomalies** — Check `history` for unusual patterns: rapid oscillation, repeated escalations.
- **Session health** (optional, with `--check-agents`) — Discovers session via `.handoff-session.json` (iTerm) or tmux session name `ai-handoff` (tmux). Checks agent responsiveness via `is_agent_idle_iterm()` or `is_agent_idle()` from watcher.py. If neither session type is found, prints "No session found — cannot check agent health."

**Output format:**
```
Handoff Diagnostic Report
=========================
Phase: my-phase | Type: plan | Round: 3 | Turn: reviewer | Status: ready

[OK]  State file readable
[OK]  Seq: 42 (monotonic)
[WARN] State has been "ready" for 12 minutes — agent may have missed command
[OK]  Cycle doc (my-phase/plan) matches state (round: 3, ready_for: reviewer)
[WARN] Stale metadata: result="approved" persists but status is "ready"
       Fix: python -m ai_handoff state set --result "" --phase my-phase

Recommendation: Re-send command to reviewer, or run /handoff status in their terminal.
```

### 2. Richer history in state.py
Extend `history` entries to include `phase`, `round`, and `updated_by` alongside existing `turn`, `status`, `timestamp`. This enables better post-mortem analysis without increasing history size (still bounded at 20).

### 3. Log seq mismatches to side-channel
When `update_state()` rejects a write due to seq mismatch, append a JSONL line to `handoff-diagnostics.jsonl` (separate from the state file). The main state file is NOT modified — no `seq` bump, no `updated_at` change, no watcher retrigger. `state diagnose` reads this log and reports recent mismatches.

Note on cycle-sync limitation: if `handoff-state.json` has the wrong `phase` or `type`, the cycle-sync check will look at the wrong file (or find no file). This is a known limitation — the diagnostic reports it as "cycle file not found" rather than claiming to detect the root cause. The stuck-in-ready check is more likely to catch such a scenario in practice.

## Technical Approach

### `state diagnose` command
- New `diagnose_state(project_dir)` function in `state.py`
- Reads state, reads cycle status file (via `cycle.py` `read_status()`), compares fields
- Timestamp arithmetic for staleness checks
- Optional agent health check via watcher's idle detection functions
- Registered as `state diagnose` subcommand in `state_command()`

### History enrichment
- Modify `update_state()` to include `phase`, `round`, `updated_by` in history entries
- Backward compatible — old history entries without these fields are handled gracefully

### Seq mismatch logging — side-channel approach
Seq mismatch events are written to a **separate log file** (`handoff-diagnostics.jsonl`) rather than appended to the state's `history` array. This avoids the problem where logging a rejected write would itself bump `seq` / `updated_at` and retrigger watcher logic.

**Semantics:**
- On seq mismatch in `update_state()`: append one JSONL line to `handoff-diagnostics.jsonl` with `{"event": "seq_mismatch", "expected": N, "actual": M, "caller": updated_by, "timestamp": ...}`
- The main state file is NOT modified — `update_state()` still returns `None` with no side effects on the live state
- `state diagnose` reads this log file and reports recent mismatches
- Log file is append-only, bounded by `state diagnose --clean` which truncates it (or manual deletion)
- Log location: same directory as `handoff-state.json` (project root)

## Tests

### `tests/test_state_diagnose.py`
**Diagnostic checks:**
- `test_diagnose_stuck_in_ready` — state with old `updated_at` and `status=ready` → warns
- `test_diagnose_not_stuck` — recent `updated_at` with `status=ready` → OK
- `test_diagnose_stale_result` — `status=ready` but `result=approved` → warns
- `test_diagnose_no_stale_result` — `status=done` with `result=approved` → OK (expected)
- `test_diagnose_cycle_sync_match` — cycle status agrees with state → OK
- `test_diagnose_cycle_sync_mismatch` — cycle round differs from state round → warns
- `test_diagnose_cycle_not_found` — cycle file missing → reports "not found"
- `test_diagnose_no_state` — no state file → reports "no state"

**Enriched history:**
- `test_history_includes_phase_round_updated_by` — after `update_state()`, history entry has `phase`, `round`, `updated_by`
- `test_history_backward_compat` — old entries without new fields handled gracefully by `diagnose`

**Seq mismatch side-channel:**
- `test_seq_mismatch_writes_diagnostics_log` — rejected write appends to `handoff-diagnostics.jsonl`
- `test_seq_mismatch_does_not_modify_state` — after rejected write, `seq`, `updated_at`, and all state fields are unchanged
- `test_seq_mismatch_log_content` — log entry has `event`, `expected`, `actual`, `caller`, `timestamp`
- `test_diagnose_reads_mismatch_log` — `state diagnose` reports recent mismatches from log
- `test_diagnose_clean_truncates_log` — `--clean` empties the diagnostics log

## Files
- `ai_handoff/state.py` — `diagnose_state()`, enriched history, seq mismatch side-channel logging
- `ai_handoff/cli.py` — (no change needed, `state diagnose` routes through `state_command`)
- `tests/test_state_diagnose.py` — tests as listed above

## Success Criteria
- [ ] `python -m ai_handoff state diagnose` produces diagnostic report
- [ ] Detects stuck-in-ready states with time threshold
- [ ] Detects stale completion metadata
- [ ] Detects cycle-state sync mismatches (scoped to file state points to)
- [ ] Cycle-sync reports "not found" when cycle file is missing (known limitation)
- [ ] History entries include phase, round, updated_by
- [ ] Seq mismatches logged to `handoff-diagnostics.jsonl` (side-channel, not live state)
- [ ] Seq mismatch logging does NOT modify `seq`, `updated_at`, or any live state field
- [ ] `--clean` truncates diagnostics log
- [ ] Tests: 15 targeted tests covering diagnostics, enriched history, side-channel
- [ ] All existing tests pass
