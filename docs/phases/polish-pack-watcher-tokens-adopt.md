# Phase: Polish Pack — Watcher Events, Token Experiment, Session Adopt

Bundled cycle covering three independent followups from the post-0.6.0 roadmap:

- **Sub-phase A (Phase 23):** Per-round files token-cost experiment
- **Sub-phase B (Phase 24):** Event-driven watcher (replace polling)
- **Sub-phase C (Phase 30 / Phase 29 followup):** `tagteam session adopt` for manually opened iTerm tabs

These ship together because each is small (estimated 50–200 LOC) and they share the watcher/session subsystem. They can land as one PR or three depending on review preference.

---

## Sub-phase A — Per-round files token experiment (Phase 23)

### Summary

The 2.0 proposal §9 deferred per-round files (one file per round vs. the current append-only JSONL) pending a measurement. With Phase 28's DB now in place, the "files vs. JSONL" question is moot for runtime — but for **prompt-cache-friendliness on the agent side** (when the agent reads `tagteam cycle rounds` output), the question is whether splitting output by round materially shrinks per-turn token cost.

### Scope (experiment, NOT implementation)

1. **Primary corpus:** `/Users/jackblacketter/projects/rankr` (read-only, 24 cycles / 85 rounds — same corpus used in the Phase 28 spike per `docs/phases/sqlite-spike-findings.md`). Pick the longest cycle (≥10 rounds with mixed REQUEST_CHANGES/AMEND).
2. **Fallback corpus** (if rankr is unavailable on the measurement host): this repo's own `phase-28-stage-2-db-readers_plan` cycle (2 rounds, smaller but real). Note in findings doc which corpus was used.
3. Measure `tagteam cycle rounds --phase X --type Y` output token count using `tiktoken` (cl100k_base) — installed only for the experiment via `pip install tiktoken` in a temporary venv. **Do not add tiktoken to `pyproject.toml` extras.**
4. Generate a hypothetical "tail-only" output: rounds N-2 through N. Measure its token count.
5. Generate a hypothetical "summary-only-for-old + full-for-recent" output. Measure.
6. Decision criterion: if tail-only saves ≥30% over full-render *and* the agent's stated need is "what does the reviewer want me to do next" (not full history), implement `--tail N` flag on `cycle rounds`. Otherwise defer Phase 23 indefinitely.
7. Document findings in `docs/phases/per-round-files-experiment-findings.md`. If verdict is "implement," follow up with a separate `--tail` PR.

### Files (experiment only)

- `docs/phases/per-round-files-experiment-findings.md` (new) — methodology, corpus used, numbers, verdict
- No code changes in this phase if verdict is "defer"; no runtime dependency changes regardless

### Success criteria (Sub-phase A)

- [ ] Findings document committed with measured token counts and a clear go/defer verdict
- [ ] If "go": follow-up issue/phase opened with the specific `--tail N` design

---

## Sub-phase B — Event-driven watcher (Phase 24)

### Summary

`tagteam/watcher.py` polls `handoff-state.json` every 1.5s (default). Replace the polling loop with `watchdog`-based filesystem events for lower idle CPU and tighter turn-flip latency. Fall back to polling on platforms where `watchdog` install fails or filesystem doesn't support events (e.g., NFS).

### Current state

`tagteam/watcher.py:watch()` (line 433) is a single function with inline state — it tracks `last_processed_seq` (line 489), resend timing, stuck-agent detection, roadmap auto-advance, repair polling, and per-mode send behavior in local variables. There is **no** `_handle_state_change()` callback today. State writes go through `tagteam.state.write_state()` (`state.py:153-161`) which writes `.handoff-state.tmp` then `replace()`s — atomic rename, not in-place modify.

### Scope (now ordered: refactor first, then events)

1. **Refactor step (must land first, with passing tests):** extract a `_StateProcessor` class (or `process_state_tick()` function) from the body of `watch()` that takes the current state dict and last-processed seq, performs all the existing turn/escalation/resend/roadmap-advance/repair work, and returns the new last-processed seq. The polling loop and the future event loop both call this processor. Tests must prove poll-mode behavior is byte-identical to today's: re-run the existing watcher tests against the refactored code with no test changes.
2. Add `watchdog>=3.0` to a new `[event]` extras group in `pyproject.toml`.
3. New `tagteam/watcher_events.py` wrapping `watchdog.observers.Observer`. The handler **must subscribe to `on_modified`, `on_created`, AND `on_moved`** because `state.write_state()` writes a temp file then `replace()`s — depending on watchdog backend (FSEvents on macOS, inotify on Linux), this surfaces as `created`/`moved-into-place`, NOT a direct modify on the target path. For `on_moved`, compare `event.dest_path`; for `on_created` and `on_modified`, compare `event.src_path`.
4. De-dupe via `seq` — every event triggers a `state` re-read; if `state["seq"]` ≤ `last_processed_seq`, the processor no-ops. This handles event storms (rename can fire create+modify) without double-processing.
5. CLI dispatch in `watcher.py`: if `watchdog` importable and `--poll` not set, use events with a 30s heartbeat that also calls the processor (covers missed events on broken filesystems / NFS). If not importable, fall back to poll and log why.
6. Add `--poll` flag to force the legacy behavior.

### Technical approach

```python
# tagteam/watcher_events.py
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

class _StateFileHandler(FileSystemEventHandler):
    def __init__(self, callback, target_path):
        self._callback = callback
        self._target = str(target_path)

    def _maybe_fire(self, path):
        if path == self._target:
            self._callback()

    def on_modified(self, event):
        self._maybe_fire(event.src_path)

    def on_created(self, event):
        self._maybe_fire(event.src_path)

    def on_moved(self, event):
        # atomic rename of .handoff-state.tmp → handoff-state.json fires on_moved
        self._maybe_fire(event.dest_path)


def watch_with_events(state_path, processor_callback, heartbeat_s=30):
    observer = Observer()
    observer.schedule(_StateFileHandler(processor_callback, state_path), str(state_path.parent))
    observer.start()
    try:
        while True:
            time.sleep(heartbeat_s)
            processor_callback()  # safety net for missed events
    finally:
        observer.stop()
        observer.join()
```

The `processor_callback` reads the current state, calls the extracted `_StateProcessor.tick(state)`, and lets the seq de-dupe handle event storms internally.

### Files

- `pyproject.toml` — add `watchdog>=3.0` to a new `[project.optional-dependencies]` `event` group
- `tagteam/watcher.py` — refactor (Step 1) + dispatch between event and poll modes + `--poll` flag
- `tagteam/watcher_events.py` (new) — handler subscribed to modify/create/move + `watch_with_events`
- `tests/test_watcher.py` — extend with refactor regression tests (poll-mode behavior unchanged)
- `tests/test_watcher_events.py` (new) — mock `watchdog.Observer`; assert callback fires for `on_modified`, `on_created`, AND `on_moved`-with-matching-dest; assert seq-dedup prevents double-processing; assert heartbeat triggers processor even with no events

### Success criteria (Sub-phase B)

- [ ] `pip install tagteam[event]` enables event mode; `pip install tagteam` still works (poll-only)
- [ ] `tagteam watch` logs which mode it picked and why
- [ ] Event mode fires `_handle_state_change` within 100ms of a state file write (manual smoke test)
- [ ] Poll-mode behavior is unchanged when `--poll` is passed or `watchdog` is unavailable
- [ ] All existing watcher tests pass

---

## Sub-phase C — `tagteam session adopt` (Phase 29 followup)

### Summary

Phase 29 added watcher mode auto-detection but assumes `.handoff-session.json` was populated by `session start --launch`. Users who manually opened iTerm tabs have no way to register them. Add `tagteam session adopt --lead <session-id> --reviewer <session-id>` to write `.handoff-session.json` directly from existing iTerm session IDs.

### Current state

`tagteam/iterm.py:226-234` creates `.handoff-session.json` with this exact schema (verified in code):

```json
{
  "backend": "iterm2",
  "tabs": {
    "lead":     {"session_id": "<unique-id>"},
    "watcher":  {"session_id": "<unique-id>"},
    "reviewer": {"session_id": "<unique-id>"}
  }
}
```

All consumers (`iterm.get_session_id`, `iterm._any_session_alive`, watcher auto-detect from Phase 29, `state diagnose`, server log-tail) read `data["tabs"][role]["session_id"]`. **Adopt MUST write this exact shape — anything else breaks every consumer.**

The existing helper `iterm.session_id_is_valid(session_id)` (`iterm.py:349`) already iterates all sessions comparing against `unique ID of s`. Reuse it; do not add a parallel validator. The scripting term is `unique ID`, not `id`.

`session_command()` already exists in `tagteam/session.py:348` as the dispatcher for all `tagteam session ...` subcommands. New subcommands are wired into `session_command()`, NOT into `tagteam/cli.py` (cli.py only dispatches the top-level `session` command).

### Scope

1. New subcommand: `tagteam session adopt --lead <unique-id> [--reviewer <unique-id>] [--watcher <unique-id>] [--force]`
2. Validate each ID via existing `iterm.session_id_is_valid(sid)`. Refuse with a useful error if any ID isn't live.
3. Write `.handoff-session.json` in the **exact existing schema** (`{"backend": "iterm2", "tabs": {role: {"session_id": id}}}`). Refuse if file exists unless `--force`.
4. New subcommand: `tagteam session list-iterm` — lists current iTerm2 sessions as `<unique-id>  <tab-title>  (window <window-id>)`. The IDs printed are exactly what `adopt` accepts.

### Technical approach

```python
# tagteam/iterm.py
def list_iterm_sessions() -> list[dict]:
    """Return [{'unique_id': ..., 'tab_title': ..., 'window_id': ...}, ...]"""
    script = '''
    tell application "iTerm2"
      set out to ""
      repeat with w in windows
        repeat with t in tabs of w
          repeat with s in sessions of t
            set out to out & (unique ID of s) & "|" & (name of t) & "|" & (id of w) & linefeed
          end repeat
        end repeat
      end repeat
      return out
    end tell
    '''
    raw = _osascript(script)
    return [_parse_session_line(line) for line in raw.splitlines() if line.strip()]

# session_id_is_valid() already exists at iterm.py:349 — reuse, don't duplicate.
```

```python
# tagteam/session.py — add adopt() and wire into session_command()
def adopt(lead_id, reviewer_id, watcher_id, force):
    path = Path(_project_dir()) / ".handoff-session.json"
    if path.exists() and not force:
        sys.exit(f"{path} already exists. Pass --force to overwrite.")
    tabs = {}
    for role, sid in [("lead", lead_id), ("watcher", watcher_id), ("reviewer", reviewer_id)]:
        if sid:
            if not iterm.session_id_is_valid(sid):
                sys.exit(f"{role} session id {sid!r} is not a live iTerm2 session.")
            tabs[role] = {"session_id": sid}
    if "lead" not in tabs:
        sys.exit("--lead is required.")
    payload = {"backend": "iterm2", "tabs": tabs}
    path.write_text(json.dumps(payload, indent=2))
    print(f"Adopted iTerm2 sessions into {path}")

# In session_command(): add 'adopt' and 'list-iterm' branches alongside existing
# 'start' / 'kill' / etc. Argparse subparsers live here, not in cli.py.
```

### Files

- `tagteam/iterm.py` — `list_iterm_sessions()` only (validator exists; reuse)
- `tagteam/session.py` — `adopt()`, `list_iterm_sessions_cmd()`, wiring in `session_command()`
- `tests/test_session.py` — adopt-success-writes-correct-schema (assert exact `{"backend":..., "tabs":{...}}` shape), adopt-refuses-existing, adopt-validates-live-sessions (mock `iterm.session_id_is_valid`), `--force` overrides, missing `--lead` errors
- `tests/test_iterm.py` — `list_iterm_sessions()` parses osascript output correctly; smoke test that the printed unique IDs round-trip through `session_id_is_valid()` (mocked)
- **Watcher auto-detect smoke test:** after `adopt`, `_auto_detect_mode()` (Phase 29) returns `iterm2`. New test in `tests/test_watcher_auto_detect.py`.

### Success criteria (Sub-phase C)

- [ ] `tagteam session list-iterm` prints session IDs from a running iTerm2 with at least one tab
- [ ] `tagteam session adopt --lead X --reviewer Y` creates `.handoff-session.json` with the right schema
- [ ] `tagteam session adopt` with a dead session ID exits with a useful error
- [ ] `tagteam session adopt` refuses to overwrite without `--force`
- [ ] `tagteam watch` (auto-detect mode from Phase 29) picks `iterm2` after adopt — completing the "I opened tabs manually, now wire them up" flow
- [ ] All existing session/iterm tests pass

---

## Roadmap updates

After this cycle ships:
- Mark Phase 23 as Complete (with verdict) or Deferred (with findings doc link)
- Mark Phase 24 as Complete
- Add Phase 30 entry: `tagteam session adopt` — Complete

## What this does NOT include

- Per-round file split itself (only the experiment that determines whether it's worth doing)
- Cross-platform watcher (Windows-specific event APIs) — `watchdog` covers macOS/Linux; Windows users get poll mode
- `session adopt` for tmux backend — iTerm2 only for this cycle (tmux pane adoption is its own design)
