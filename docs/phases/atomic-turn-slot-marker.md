# Phase 80: The turn slot marker is replaced atomically

## Status
- [x] Planning (approved round 1, 2026-11-16)
- [x] Implementation
- [ ] Implementation Review
- [ ] Complete

## Summary
The turn slot is one file, `.tagteam/turns/inflight.json`. `claim_turn_slot()` and
`update_turn_slot()` (`tagteam/headless.py`) write it with `Path.write_text`, which truncates the
file and then writes. Writers hold the project's writer lock, but readers do not: `read_inflight()`
is lock-free and is called from the cockpit, the hub, `tagteam tail`, the gate, the panel and
`slot_status()`. A reader that lands between the truncate and the write gets an empty file,
`json.loads` raises, `read_inflight()` returns `None`, and the slot reads as **free while a turn
holds it**.

The rewrite that readers actually meet is `update_turn_slot()`: every spawner calls it from
`on_spawn` to record the child's pid, a moment after the turn starts. That is exactly when a
client that just started a turn asks about it.

This is the "launchpad endpoint test flake" in the backlog. It is a product defect, not a test
defect.

Evidence:
- CI on `main`, 2026-09-25 (run 36201554429) and 2026-11-16, green on re-run both times:
  `tests/test_launchpad.py::TestServerEndpoints::test_watch_session_and_launch_endpoints` fails at
  `assert conv["slot"]["held"] and conv["slot"]["kind"] == "conversation"` with `assert (False)`.
  The line before it passed, so the turn row said `running` while the slot said free.
- Mechanism measured directly: one thread calling `update_turn_slot()` in a loop, another calling
  `slot_status()`. In 5 s, 20 of 580 reads reported the held slot as free.
- The CI failure reproduced on demand: with the truncate-to-write gap widened to 1 s and the
  server's slot read delayed 0.4 s (a pytest plugin in a scratch directory, nothing committed),
  the launchpad test fails with the identical assertion. Unmodified, it passed 5 of 5 here; the
  window is real and narrow.

What a wrong "free" does today:
- The cockpit and the hub show no turn in flight for that poll.
- `tagteam tail --follow` ends its follow loop when `read_inflight()` returns `None`, so it can
  stop while the turn is still running.
- The gate and the panel read the marker with `read_inflight(root) or {}` to describe who holds
  the slot, and get nothing.
- It cannot cause a double claim: `claim_turn_slot()` reads under the same lock the writers hold.

## Scope

### 1. One atomic writer for the marker
A private helper in `tagteam/headless.py` writes the marker to a temporary file in the same
directory and moves it over `inflight.json` with `os.replace`. `claim_turn_slot()` and
`update_turn_slot()` both use it; nothing else writes the marker (checked: `briefer.py` and
`controls.py` only read or unlink it).

- A reader now sees the whole old marker or the whole new one, never an empty or partial file.
- The temporary file (`.inflight.json.<pid>.<hex>.tmp`) is removed if the write or the replace
  fails. It is not invisible to `prune_turn_logs`, which counts every regular file in `turns/`
  except `inflight.json` (reviewer, round 1): a leftover from a killed process would count as one
  stem there and never be deleted by it. No pruning change in this phase.
- On failure the previous marker is left untouched. `update_turn_slot()` keeps its contract and
  returns `False`; `claim_turn_slot()` raises as it does today when the write fails.
- `os.replace` over a file another process has open can raise `PermissionError` on Windows.
  The helper retries that one error, on Windows only, 5 attempts 20 ms apart, then lets it
  propagate (`update_turn_slot()` returns `False`, `claim_turn_slot()` raises). No other `OSError`
  is retried, and on POSIX the first attempt always decides. Simulated failures test the retry
  logic; they do not establish Windows behaviour, and there is no Windows run for this phase.

### 2. Readers are unchanged
`read_inflight()` keeps returning `None` for a file it cannot parse. With atomic writes our own
writers can no longer produce one; a marker corrupted by something else still reads the way it
does today.

### 3. Tests
In `tests/test_headless.py`, beside the existing slot tests:
- During an update, at the moment just before the replace, the file on disk is still the complete
  previous marker and `slot_status()` says held (hook on `os.replace` as `headless` sees it).
  After the update it is the complete new marker.
- A replace that fails leaves the previous marker in place, `update_turn_slot()` returns `False`,
  and no temporary file is left in `turns/`.
- A temporary-file write that fails half way (reviewer, round 1) publishes nothing, leaves the
  previous marker and no temporary file.
- A failed first claim leaves no marker and no temporary file, and the slot is claimable after.
- Retry (reviewer, round 1): a transient `PermissionError` followed by success; exhaustion after
  exactly the bounded number of attempts; no retry where it does not apply.
- After claim, update and release, `turns/` holds no temporary file.
- A short two-thread run (updates against `slot_status()`): zero reads report free. This one is
  supporting evidence only; a race test that passes proves little, the two tests above are the
  ones that pin the behaviour.

The launchpad test itself is not changed.

## Out of scope
- `write_pidfile()` (`watcher.json`) has the same truncate-then-write shape. Phase 79 handled its
  interrupted-write case; making it atomic is a separate, smaller change with its own readers to
  check. Recorded in the backlog, not done here.
- Any change to what a corrupt marker means at claim time.
- Locking readers. Atomic replace removes the need.

## Technical Approach
```python
def _write_marker(path: Path, marker: dict) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp")
    try:
        tmp.write_text(json.dumps(marker, indent=2), encoding="utf-8")
        _replace_with_retry(tmp, path)
    except BaseException:
        try: tmp.unlink()
        except OSError: pass
        raise
```
`claim_turn_slot()` calls it where it calls `path.write_text` today. `update_turn_slot()` calls it
inside its existing `try/except OSError: return False`. Writers are already serialized by
`writer_lock`, so the unique temporary name only guards against a leftover from a killed process.

## Files
- `tagteam/headless.py` (helper; two call sites)
- `tests/test_headless.py` (new tests)
- `docs/roadmap.md` (Phase 80 entry; the backlog entry points here; backlog note for the pidfile),
  `docs/phases/atomic-turn-slot-marker.md`

## Success Criteria
1. No reader can observe `inflight.json` empty or partial while a turn holds the slot: shown by
   the before-replace test, not by a timing run.
2. A failed marker write leaves the previous marker intact and no temporary file.
3. The widened-window reproduction passes after the change. For this check the shim slows the
   real `Path.write_text` on marker files (truncate, wait 1 s, write) instead of truncating the
   live file itself, so it stays faithful to the new code path; the same shim fails the launchpad
   test on `main`.
4. The full suite passes in the gate's run, and CI is green on the PR.
5. No change to the marker's fields, the claim or release rules, or any reader.

## Closeout
Implemented as planned, with the reviewer's three additions (retry scoped to the replace and to
Windows with exhaustion propagated; a temp-write failure test; no claim that the temp file is
invisible to pruning).

- `tagteam/headless.py`: `_write_marker()` and `_replace_marker()`; `claim_turn_slot()` and
  `update_turn_slot()` call the helper. Nothing else changed.
- `tests/test_headless.py`: `TestMarkerIsReplacedAtomically`, 9 tests.

Verification before submission:
- Against the unfixed `headless.py`, 8 of the 9 new tests fail (the ninth only checks that
  nothing is left behind, which was already true).
- The CI reproduction (real `Path.write_text` slowed to truncate, 1 s, write on marker files; the
  server's slot read delayed 0.4 s): failed 3 of 3 on `main`, passes 3 of 3 with the fix.
- The five test files that use the slot (`test_headless`, `test_lead_chat`, `test_launchpad`,
  `test_briefer`, `test_gatekeeper`): 359 passed.
- Not verified: Windows.
