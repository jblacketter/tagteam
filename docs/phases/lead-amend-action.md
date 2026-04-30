# Phase: Lead-Side `AMEND` Action for Mid-Review Updates

## Summary

Fix Issue #5 from `docs/handoff-cycle-issues-2026-04-24.md` (low severity, lead-side friction): when the human arbiter answers an open question while the plan is out for review, the lead has no way to fold those answers into the record without either (a) waiting for the reviewer to come back so a round-2 SUBMIT_FOR_REVIEW can carry them, or (b) racing the reviewer's REQUEST_CHANGES with a manual edit. The cycle protocol needs an `AMEND` action — lead-only, mid-review-only, no round bump — that surfaces an in-place plan/impl update to the reviewer linearly.

## Scope

### In scope
- New `AMEND` action in `tagteam.cycle.VALID_ACTIONS`. Semantics:
  - **Role:** `lead` only. Reviewer attempts return an error.
  - **Cycle precondition:** the cycle must currently have `state == "in-progress"` AND `ready_for == "reviewer"` (i.e., a SUBMIT_FOR_REVIEW just happened and the reviewer hasn't answered yet). Otherwise error: "AMEND only valid mid-review (after SUBMIT_FOR_REVIEW, before REQUEST_CHANGES/APPROVE)".
  - **Round number:** the AMEND entry carries the same round number as the most recent SUBMIT_FOR_REVIEW. The status JSON's `round` field is **not** advanced.
  - **State transition:** none. `state` stays `"in-progress"`, `ready_for` stays `"reviewer"`. The reviewer is still up.
  - **Stale-round detection:** AMEND entries are **excluded** from `_count_stale_rounds()`'s lead-submission stream. They do not contribute to staleness counting.
- Append-only: AMEND entries are written to the JSONL log like any other round entry (`role: lead`, `action: AMEND`, `content`, `ts`).
- `parse_jsonl_rounds()` (in `tagteam/parser.py`) gains a `lead_amendments` field per round dict: list of `{"content": ..., "ts": ...}` from AMEND entries with that round number. Empty list when none. SUBMIT_FOR_REVIEW remains the canonical lead entry for the round.
- `tagteam cycle add --action AMEND` works through the existing CLI (`_cli_add` in `cycle.py`) with no new flags. Same `--phase --type --role lead --action AMEND --round N --content "..." --updated-by ...` shape.
- SKILL.md gets a short "Mid-review amendments" subsection under the lead-turn-actions block, plus an `AMEND` row in the action table.
- Tests:
  1. AMEND mid-review: round N stays at N, status `state == "in-progress"`, `ready_for == "reviewer"`, JSONL gains an `AMEND` line.
  2. AMEND when `ready_for == "lead"` (after REQUEST_CHANGES) → `add_round` raises ValueError with the precondition message; nothing written.
  3. AMEND with `role == "reviewer"` → ValueError; nothing written.
  4. AMEND before any SUBMIT_FOR_REVIEW (cycle barely exists) → ValueError; nothing written. (Edge case: cycle init writes the first SUBMIT_FOR_REVIEW, so this asserts the precondition still holds when the JSONL has only the init entry — i.e., the *normal* case still allows AMEND. The error case is when the cycle status is somehow malformed; reuse the same ValueError path.)
  5. Multiple AMENDs in a row do **not** trigger STALE_ROUND_LIMIT escalation. Specifically: drive 11 AMENDs after a single SUBMIT_FOR_REVIEW; assert `state == "in-progress"`, not `"escalated"`, and that `_count_stale_rounds()` returns 0.
  6. `parse_jsonl_rounds()` returns a `lead_amendments` list on the round whose `lead_action == "SUBMIT_FOR_REVIEW"`. Each entry has `content` and `ts` keys.
  7. `tagteam cycle rounds` output includes the `lead_amendments` field per round (even when empty `[]`, for schema stability).
  8. **Round-pinning (Codex round-1 concern):** call `add_round` with `--action AMEND --round 999` against a cycle whose active round is 1 → ValueError naming the active round; JSONL is unchanged.
  9. **Legacy markdown schema stability:** seed a legacy `{phase}_{type}_cycle.md` (no JSONL), call `read_cycle_rounds(...)`, assert each returned round dict has `lead_amendments == []`.
  10. **CLI clean-error contract (Codex round-2 concern):** invoke `cycle_command(["add", "--phase", ..., "--type", ..., "--role", "reviewer", "--action", "AMEND", ...])`. Assert `rc == 1`, stderr contains "AMEND requires role=lead", and no Python traceback fragments in stderr (`assert "Traceback" not in stderr`). Also assert the JSONL was not appended to.

### Out of scope
- Reviewer-side `AMEND` (e.g., reviewer wants to clarify their own REQUEST_CHANGES). The bug report only flags the lead side; reviewer can simply re-issue REQUEST_CHANGES if needed.
- Diff-style "what changed" tracking. The lead writes the AMEND `content` describing the change in plain text; we don't auto-derive a diff against the prior plan file. That could be a future enhancement.
- HTML rendering changes (`render_cycle()` / `render_html_dialogue()`). The plan covers structured data; rendering can pick up `lead_amendments` later.
- Watcher behavior changes. Since AMEND doesn't change `turn` or `state`, the top-level `handoff-state.json` doesn't change either, and the watcher correctly stays silent (it would have re-triggered the reviewer otherwise — undesirable). This is a deliberate design choice: AMEND is communicated to the reviewer through the cycle rounds, which the reviewer reads on their next `/handoff`.

## Technical Approach

### 1. Action registration (`tagteam/cycle.py`)

```python
VALID_ACTIONS = {
    "SUBMIT_FOR_REVIEW", "REQUEST_CHANGES", "APPROVE",
    "ESCALATE", "NEED_HUMAN", "AMEND",
}
```

No `_TRANSITIONS` entry for `AMEND` — the dispatch logic is special-cased in `add_round` because AMEND must not advance the round and must not change `state`/`ready_for`.

### 2. `add_round()` changes

Add a branch near the top of `add_round` (after the `VALID_ACTIONS` check, before the `_TRANSITIONS` lookup):

```python
if action == "AMEND":
    if role != "lead":
        raise ValueError("AMEND requires role=lead")
    status = read_status(phase, cycle_type, project_dir) or {}
    if status.get("state") != "in-progress" or status.get("ready_for") != "reviewer":
        raise ValueError(
            "AMEND only valid mid-review (after SUBMIT_FOR_REVIEW, "
            "before REQUEST_CHANGES/APPROVE)."
        )
    # Round-pinning (Codex round-1 concern): the entry must attach to the
    # active round, not whatever the caller passes. Reject mismatches loudly
    # rather than silently rewriting; this surfaces caller bugs and keeps
    # the JSONL audit trail truthful.
    active_round = status.get("round")
    if round_num != active_round:
        raise ValueError(
            f"AMEND --round {round_num} does not match the active round "
            f"({active_round}). Pass --round {active_round}."
        )
    # Append JSONL entry; do not touch status[round/state/ready_for];
    # do not run stale-round detection; do not call _derive_top_level_state
    # because nothing observable to the watcher has changed (round/turn/status
    # are all stable).
    entry = {"round": round_num, "role": role, "action": action,
             "content": content, "ts": now}
    rp = _rounds_path(phase, cycle_type, project_dir)
    with open(rp, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
    return status
```

Open question for reviewer: should AMEND also touch `status["updated_at"]` or some "amended_at" field? Plan: skip — the JSONL entry's `ts` is the source of truth; status JSON stays canonical for round/turn/state only. Flag if reviewer wants explicit visibility.

### 3. `_count_stale_rounds()` change

Currently iterates over `lead` + `SUBMIT_FOR_REVIEW` entries. Already filters by action, so AMEND entries are naturally excluded. **No code change needed**, but add a docstring note:

```
# AMEND entries are deliberately excluded — they reflect lead progress,
# not stuck-cycle behavior, and must never trigger auto-escalation.
```

Confirm in test #5 that this holds.

### 4. `_cli_add()` changes (`tagteam/cycle.py`)

The current `_cli_add` does **not** catch `ValueError` around `add_round()` (verified by inspection of `tagteam/cycle.py`); the existing pre-validation only checks `action not in VALID_ACTIONS` before dispatch. Without a fix, the new AMEND precondition / round-mismatch errors would surface as Python tracebacks rather than clean CLI exit-1 messages, violating the manual-verification expectation in this plan.

Add an explicit try/except around the `add_round()` call inside `_cli_add()`:

```python
try:
    add_round(phase=phase, cycle_type=cycle_type, role=role, action=action,
              round_num=round_num, content=content, updated_by=updated_by)
except ValueError as e:
    print(f"Error: {e}", file=sys.stderr)
    return 1
```

This handler covers all `ValueError` paths — the new AMEND ones plus the existing `Invalid action` / `Invalid role` raises in `add_round` that today are pre-empted by `_cli_add`'s own checks but that wouldn't hurt to harden against future drift. Add a regression test (test #10) that calls `cycle_command(["add", ...])` with an invalid AMEND (wrong role) and asserts `rc == 1` and stderr contains the precondition message — confirming the CLI path doesn't traceback.

### 5. Parser changes (`tagteam/parser.py`)

`parse_jsonl_rounds()`:

- After grouping by round number, separately collect AMEND entries (also grouped by round number) into `amendments_by_round: dict[int, list[dict]]`.
- For each round dict, add `"lead_amendments": amendments_by_round.get(round_num, [])`. Each entry shaped as `{"content": ..., "ts": ...}` from the JSONL line.

Update the docstring to list the new field.

`read_cycle_rounds()` (Codex round-1 concern):

- After dispatching, normalize the schema before returning so consumers can rely on `lead_amendments` always being present. Specifically: when the result came from `extract_all_rounds()` (legacy markdown path), backfill `lead_amendments = []` on every round dict. The markdown format predates this phase and cannot represent amendments, so an empty list is the truthful answer.
- Equivalent guard for the JSONL path: defensive `r.setdefault("lead_amendments", [])` before returning, so older JSONL written before this phase shipped still surfaces with the field present.

### 6. SKILL.md changes (`.claude/skills/handoff/SKILL.md`)

Under `#### As Lead (your turn)` add a short paragraph after step 3:

```
**Mid-review amendment.** If new info arrives (e.g., human answers an
open question) while the reviewer is still on your submission, run:

    tagteam cycle add --phase X --type plan|impl --role lead --action AMEND \
      --round N --updated-by <agent> --content "<what changed>"

This appends an amendment to the current round without bumping the round
number or returning the turn to you. The reviewer will see the amendment
in the rounds output on their next /handoff.
```

Also add an `AMEND` row to the implicit list (the SKILL.md doesn't have an action table, just narrative — drop the new paragraph in the lead-turn block).

### 7. Manual verification

```
# In a tmp tagteam project:
tagteam cycle init --phase amend-test --type plan --lead L --reviewer R \
  --updated-by L --content "Round 1 plan."
tagteam cycle add --phase amend-test --type plan --role lead --action AMEND \
  --round 1 --updated-by L --content "Human said X, folding in."
tagteam cycle status --phase amend-test --type plan
# Expect: state=in-progress, ready_for=reviewer, round=1 (unchanged).
tagteam cycle rounds --phase amend-test --type plan
# Expect: round 1 record with lead_amendments=[{content: "Human said X...", ts: ...}].
# Reviewer responds:
tagteam cycle add --phase amend-test --type plan --role reviewer --action APPROVE \
  --round 1 --updated-by R --content "Approved."
tagteam cycle status --phase amend-test --type plan
# Expect: state=approved.
```

Then the negative case:

```
# After APPROVE, ready_for is None — AMEND should now error.
tagteam cycle add --phase amend-test --type plan --role lead --action AMEND \
  --round 1 --updated-by L --content "too late"
# Expect: exit 1 with the precondition message.
```

## Files

- `tagteam/cycle.py` — `VALID_ACTIONS` += `AMEND`; `add_round()` early-branch; docstring note in `_count_stale_rounds()`.
- `tagteam/parser.py` — `parse_jsonl_rounds()` grows `lead_amendments` per round; docstring update.
- `.claude/skills/handoff/SKILL.md` — Add the mid-review amendment paragraph in the lead-turn block.
- `tests/test_cycle_amend.py` (new) — 7 regression tests above.

## Success Criteria

- [ ] `tagteam cycle add --action AMEND --role lead` mid-review appends a JSONL entry without advancing round, state, or ready_for.
- [ ] AMEND with `role == "reviewer"` raises ValueError with a clear message.
- [ ] AMEND when `ready_for != "reviewer"` raises ValueError with the precondition message.
- [ ] 11 consecutive AMENDs after a single SUBMIT_FOR_REVIEW do not auto-escalate (`_count_stale_rounds()` returns 0; `state` stays `"in-progress"`).
- [ ] `parse_jsonl_rounds()` returns a `lead_amendments` list per round; `tagteam cycle rounds` output includes the field.
- [ ] AMEND with `round_num != status.round` raises ValueError naming the active round; nothing written.
- [ ] Legacy markdown cycles surfaced via `read_cycle_rounds()` carry `lead_amendments == []` on every round (schema stability).
- [ ] CLI invocations of bad AMEND (wrong role / wrong cycle state / round mismatch) exit 1 with the error message on stderr and no Python traceback.
- [ ] All 10 new tests pass.
- [ ] Full existing test suite still passes.
- [ ] Manual repro shows AMEND working in the happy path and erroring after APPROVE.

## Open Questions

- Should AMEND propagate to the top-level `handoff-state.json` in any way (e.g., bumping `seq` or `updated_at` for a watcher heartbeat)? Plan: no — the watcher dispatches on `turn` changes, and AMEND doesn't change turn. If we bump `seq`/`updated_at`, the watcher might re-fire `/handoff` to the reviewer for nothing. Leave state untouched. Flag if reviewer disagrees.
- Should the CLI gain a friendlier wrapper like `tagteam cycle amend` (no `--action AMEND` boilerplate)? Defer to a separate UX phase if there's demand; this plan keeps the surface minimal.

## Risks

- **Reviewer races the amendment.** The reviewer might run `/handoff`, read rounds (without the new amendment), make a decision, and submit REQUEST_CHANGES/APPROVE before seeing the AMEND. Mitigation: documented as a known limitation — AMEND is best-effort, equivalent to "lead pings reviewer with new info while review is in flight". The lead can always SUBMIT_FOR_REVIEW with a new round number if they need stronger guarantees. This is the same risk surface as any non-blocking message in a review tool (GitHub PR comments mid-review have the same race).
- **Schema break for old consumers.** Adding `lead_amendments` to the round dict is additive; consumers that ignore unknown keys are unaffected. No existing code reads it, so this is safe.
- **Stale-round detection bypass.** A misbehaving lead could AMEND infinitely without ever re-submitting. Since AMEND is excluded from staleness, this would never auto-escalate. Mitigation: not a real attack surface (the lead can already do this by editing files without committing); if abuse becomes real, we can add a per-cycle AMEND cap, but defer that.
