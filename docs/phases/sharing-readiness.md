# Phase: Sharing Readiness

## Summary
Make ai-handoff ready for wider sharing by simplifying documentation, marking WIP features, improving handoff robustness, and adding auto-launch to session start.

## Scope

### 1. README Simplification
- **Remove** the entire "Manual Handoff" section (#1 of "Choose Your Workflow")
- **Promote** Automated Handoff as the primary (and only detailed) workflow
- Relocate any uniquely useful content from the manual section (e.g., the command table) into the automated section or CLI Reference if not already covered
- Add a **brief note** at the bottom of the Automated section: "You can also run this manually by pasting the `/handoff` command results between agents without the watcher."
- Renumber sections: Automated becomes §1, Saloon becomes §2

### 2. Saloon WIP Notice
- Add a clear **"Work in Progress"** banner at the top of the Saloon section
- Note that the web dashboard and TUI are functional but under active development and not yet production-ready
- Keep the existing feature descriptions but set expectations

### 3. Robustness Improvements (Stale Handoff Prevention)
Investigation findings from code review:

**Identified risks:**
- **Idle detection false positives** (watcher.py): Pattern-based idle detection can misfire if agent output contains idle-like patterns mid-action. Could cause premature command injection.
- **Command send failure → stuck state** (watcher.py): If `send_tmux_keys()` or `write_text_to_session()` fails, the watcher logs + notifies but doesn't retry the state transition. State stays `ready` but command was never delivered.
- **No file-level locking on state** (state.py): Atomic rename prevents partial writes, and `expected_seq` prevents stale overwrites, but concurrent writers could still race between read and write. Low risk in practice (only watcher + agent write).
- **Timestamp collision** (state.py/watcher.py): Two state updates within the same millisecond share a timestamp, so the watcher's `updated_at` comparison would see only one change.

**Proposed improvements:**
- Add a **retry loop** in watcher for failed command sends (retry the send, not the state transition) — currently only logs failure
- Add a **state heartbeat/watchdog**: if state has been `ready` for longer than a configurable timeout (e.g., 5 min) with no `updated_at` change, re-send the command as a recovery mechanism
- Add **monotonic sequence check** in watcher alongside timestamp comparison — use `seq` field (already exists) as primary change detector instead of `updated_at`
- Log skipped transitions (seq mismatch) to history for debugging

### 4. Session Auto-Launch (`session start` Enhancement)
Currently `session start` creates panes/tabs but requires manual agent startup.

**Proposed:**
- Add `--launch` flag to `session start` that auto-starts agents and watcher:
  - **Pane 1 (Lead):** Runs `claude` (or configured lead command)
  - **Pane 2 (Watcher):** Runs `python -m ai_handoff watch --mode [backend]`
  - **Pane 3 (Reviewer):** Runs `codex` (or configured reviewer command)
- Agent launch commands come from `ai-handoff.yaml` (new optional field `agents.lead.command` / `agents.reviewer.command`, defaulting to agent name)
- Without `--launch`, behavior unchanged (backward compatible)
- For tmux: change `""` to `"Enter"` on send-keys for watcher pane; add send-keys for agent panes
- For iTerm2: add `write text` commands after `cd` in AppleScript

### 5. Roadmap Update
- Add Phase 14: Sharing Readiness to `docs/roadmap.md`

## Technical Approach

### README changes
Pure documentation edit — restructure sections, no code changes.

### Robustness
- `watcher.py`: Add retry loop around send failures, add seq-based change detection, add re-send watchdog
- `state.py`: Add seq-mismatch logging to history

### Session auto-launch — Config layer
Launch commands are managed through the centralized config module (`ai_handoff/config.py`):

- **Schema:** New optional `command` field on each agent in `ai-handoff.yaml`:
  ```yaml
  agents:
    lead:
      name: Claude
      command: claude          # optional, defaults to lowercase agent name
    reviewer:
      name: Codex
      command: codex           # optional, defaults to lowercase agent name
  ```
- **`config.py` changes:**
  - `validate_config()`: Accept `command` as an optional string field per agent. Warn (not error) if the field is present but empty.
  - New `get_launch_commands(config) -> tuple[str, str]` helper: Returns `(lead_command, reviewer_command)`. Falls back to `agent_name.lower()` when `command` is absent.
  - No-PyYAML fallback: Extend the line-by-line parser to also extract `command:` lines following `lead:` / `reviewer:`. Without this, `--launch` would silently use defaults when PyYAML is missing — acceptable behavior, but the fallback should at least attempt to parse it.
- **`session.py` / `iterm.py`:** Import `read_config` + `get_launch_commands` from `config.py`. On `--launch`:
  1. Read config, call `get_launch_commands()`
  2. Send lead command to pane 0 / tab 1
  3. Auto-start watcher in pane 1 / tab 2
  4. Send reviewer command to pane 2 / tab 3
- **Failure behavior:**
  - If `ai-handoff.yaml` is missing or unreadable: print error, abort `--launch` (session still created, panes usable manually)
  - If an agent command fails to start (e.g., `command not found`): the pane shows the shell error. Session remains intact — user can manually fix and restart. Watcher continues independently.
  - This is documented in the `--launch` help text and README.

### Tests
- `tests/test_config.py`: Add tests for `get_launch_commands()` — with command fields, without (defaults), with empty command, with no-PyYAML fallback
- `tests/test_session.py` (new if needed): Test `--launch` flag parsing and that config is read correctly

## Files
- `README.md` — restructure workflow sections
- `ai_handoff/config.py` — `get_launch_commands()` helper, validate `command` field, extend fallback parser
- `ai_handoff/watcher.py` — retry loop, seq-based detection, re-send watchdog
- `ai_handoff/state.py` — seq-mismatch history logging
- `ai_handoff/session.py` — `--launch` flag, tmux auto-start via config
- `ai_handoff/iterm.py` — iTerm2 auto-start via config
- `tests/test_config.py` — tests for new launch command helpers
- `docs/roadmap.md` — add Phase 14

## Success Criteria
- [ ] README has one workflow section (Automated) with manual mentioned in one sentence
- [ ] Saloon section clearly marked as WIP
- [ ] Watcher retries failed command sends (configurable retries)
- [ ] Watcher uses `seq` field as primary change detector
- [ ] Watcher re-sends command if state stuck in `ready` beyond timeout
- [ ] `session start --launch` auto-starts agents and watcher in all panes
- [ ] Agent launch commands configurable via `ai-handoff.yaml` and parsed through `config.py`
- [ ] `get_launch_commands()` defaults to lowercase agent name when `command` absent
- [ ] No-PyYAML fallback parses `command` fields
- [ ] `--launch` fails gracefully (session created, error printed) if config missing
- [ ] Tests for new config helpers and launch behavior
- [ ] All existing tests pass
