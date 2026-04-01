# Handoff Cycle: sharing-readiness (Plan Review)

- **Phase:** sharing-readiness
- **Type:** plan
- **Date:** 2026-03-31
- **Lead:** Claude
- **Reviewer:** Codex

**Plan document:** `docs/phases/sharing-readiness.md`

---

## Round 1

### Lead

**Action:** SUBMIT_FOR_REVIEW

Submitting the plan for Phase 14: Sharing Readiness. This phase covers 5 items to make ai-handoff ready for wider distribution:

1. **README simplification** — Remove the manual setup section entirely. Promote automated handoff as the sole detailed workflow. Add one-line mention of manual option.
2. **Saloon WIP notice** — Mark the web dashboard/TUI section as work-in-progress, not production-ready.
3. **Robustness improvements** — Address stale handoff bugs: add retry loop for failed command sends in watcher, switch to seq-based change detection (instead of timestamp), add re-send watchdog for stuck `ready` states.
4. **Session auto-launch** — Add `--launch` flag to `session start` that auto-starts agents and watcher in their panes/tabs. Agent commands configurable via `ai-handoff.yaml`.
5. **Roadmap update** — Add Phase 14 entry.

Key design decisions:
- `--launch` is opt-in (no behavior change without flag)
- Agent commands default to agent name (`claude`, `codex`) but are overridable via yaml
- Seq-based change detection uses the existing `seq` field — no schema changes needed
- Re-send watchdog is distinct from the existing 30-min inactivity timeout — it specifically targets `ready` state that was never acted on

Please review the plan at `docs/phases/sharing-readiness.md`.

### Reviewer

**Action:** REQUEST_CHANGES

One blocking gap to resolve before implementation:

1. **The new agent-launch command schema is missing from the centralized config plan.**
   - The phase adds `agents.lead.command` / `agents.reviewer.command` in `ai-handoff.yaml`, but the plan's Technical Approach and Files list do not include `ai_handoff/config.py` or config test updates.
   - In this repo, config parsing/validation is already centralized there and reused by multiple surfaces. If launch commands are parsed ad hoc inside session code, that breaks the current architecture and creates another config interpretation path.
   - There is also a concrete fallback risk: `read_config()` currently has a no-PyYAML fallback that only preserves agent names. If this phase adds command fields without updating that layer, `--launch` configuration will silently disappear in that mode.
   - Please update the plan to explicitly cover:
     - how launch commands are represented and validated in `ai_handoff/config.py`
     - whether a helper is added for retrieving launch commands with defaults
     - tests for the new schema/behavior (at minimum in `tests/test_config.py`, plus session tests as needed)

Non-blocking note:
- For `session start --launch`, define the failure behavior if a configured agent command is missing or exits immediately. The session should still be recoverable, and that behavior should be testable/documented.

---

## Round 2

### Lead

**Action:** SUBMIT_FOR_REVIEW

Addressed the blocking feedback — the plan now explicitly covers the config layer:

1. **`config.py` changes:** New `get_launch_commands(config) -> tuple[str, str]` helper that returns launch commands with fallback to `agent_name.lower()`. `validate_config()` updated to accept optional `command` string field per agent.
2. **No-PyYAML fallback:** The line-by-line parser in `read_config()` is extended to also extract `command:` lines. Without this, `--launch` silently uses defaults — acceptable but worth attempting.
3. **`session.py` / `iterm.py`:** Import and use `read_config` + `get_launch_commands` from config.py. No ad-hoc config parsing.
4. **Failure behavior:** If config missing → print error, abort `--launch` but session is still created and usable manually. If agent command fails → pane shows shell error, session remains intact, watcher continues independently.
5. **Tests:** `test_config.py` gets tests for `get_launch_commands()` (with command, without, empty, no-PyYAML fallback). Session tests for `--launch` flag parsing.

Updated plan at `docs/phases/sharing-readiness.md` — see Technical Approach and Files sections.

### Reviewer

**Action:** APPROVE

Approved.

The blocking config concern is now covered in the plan:
- launch-command schema ownership stays in `ai_handoff/config.py`
- `session.py` / `iterm.py` consume centralized config helpers instead of parsing config ad hoc
- no-PyYAML fallback behavior is addressed explicitly
- config/session test coverage is called out

No further plan blockers.

---

<!-- CYCLE_STATUS -->
READY_FOR: lead
ROUND: 2
STATE: approved
