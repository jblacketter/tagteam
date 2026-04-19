# Handoff Performance Recommendations

## Goal
Reduce end-to-end handoff turnaround time by making cycle reads and writes cheaper, without reducing review depth or forcing shallower analysis.

This document focuses on wall-clock improvements, not token minimization.

## Short Answer
Markdown is the wrong primary write format for the active handoff cycle.

Plain text would not help much. It is slightly lighter than markdown, but it still has the same core problems:
- full-file reads before each update
- full-file rewrites after each update
- fragile string-based edits
- ad hoc parsing for status, rounds, and actions

The better format is:
- **JSONL for cycle rounds**
- **small JSON for cycle status**
- **optional synthesized markdown for human viewing**

## Recommended Direction

### 1. Keep active cycle data in append-only structured files
Use:
- `docs/handoffs/{phase}_{type}_rounds.jsonl`
- `docs/handoffs/{phase}_{type}_status.json`

Example:

```jsonl
{"round":1,"role":"lead","action":"SUBMIT_FOR_REVIEW","content":"...","ts":"2026-03-21T12:00:00Z"}
{"round":1,"role":"reviewer","action":"REQUEST_CHANGES","content":"...","ts":"2026-03-21T12:05:00Z"}
```

```json
{"state":"in-progress","ready_for":"lead","round":1}
```

Why this is faster:
- adding a round is one append, not a document rewrite
- changing status is one tiny file write
- readers can load only what they need
- the format is stable enough for CLI, dashboard, and TUI code to share

### 2. Keep markdown only as a rendered view
Do not make agents edit markdown as the source of truth.

Instead:
- generate markdown on demand for the web dashboard or human inspection
- optionally preserve legacy `_cycle.md` files as read-only historical artifacts

This keeps human readability without paying markdown edit costs on every turn.

### 3. Add a small CLI surface for the hot path
The active path should be shell commands, not manual doc edits.

Recommended commands:
- `python -m tagteam cycle init`
- `python -m tagteam cycle add`
- `python -m tagteam cycle status`
- `python -m tagteam cycle rounds`
- `python -m tagteam cycle render`

That shifts the heavy work from the agent to deterministic code.

### 4. Centralize cycle discovery
There should be one function that lists cycles and handles precedence between:
- legacy `_cycle.md`
- new JSONL/JSON pairs

That avoids duplicate scans and keeps dashboard/TUI behavior consistent.

## Highest-ROI Changes

If the objective is simply to make handoffs faster, the order should be:

1. **Cycle storage migration**
   Replace markdown editing on active cycles with JSONL + JSON.

2. **CLI-based cycle writes**
   Agents should append rounds and update status through commands only.

3. **Synthesized markdown views**
   Preserve human readability without using markdown as the writable format.

4. **Shared read helpers**
   Parser, server, TUI, and web UI should all go through the same structured reader.

These changes attack the real bottleneck: repeated read/modify/write of markdown cycle files.

## Lower-ROI Changes

These are useful, but not the first thing to optimize:

### Roadmap format migration
Roadmap edits happen far less often than cycle writes.

If roadmap updates are CLI-driven only, a structured file is still reasonable, but it is not where most handoff time is spent. If this is split into phases, cycle storage should come first.

### Skill size reduction
Smaller instructions can help some, but the bigger win is avoiding manual file editing work. Storage and CLI changes matter more for wall-clock time.

## Format Comparison

### Markdown
Pros:
- best for humans to read directly

Cons:
- worst for repeated machine updates
- expensive to patch safely
- duplicated parsing logic

### Plain text
Pros:
- slightly simpler than markdown

Cons:
- still requires full-file rewrites
- no real structure
- parsing stays brittle

Conclusion:
- not enough improvement to justify switching

### YAML
Pros:
- more structured than markdown
- human-editable

Cons:
- still usually rewritten as a whole file
- better for configuration than append-heavy logs

Conclusion:
- acceptable for low-frequency metadata, not ideal for active cycle logs

### JSON
Pros:
- fast, deterministic, standard library support

Cons:
- whole-file rewrite if used for a growing round history

Conclusion:
- good for small status files

### JSONL
Pros:
- append-only
- easy to stream
- easy to parse
- good fit for round-by-round history

Cons:
- less pleasant for humans to read raw

Conclusion:
- best fit for active handoff rounds

### SQLite
Pros:
- strongest long-term data model
- good querying and concurrency story

Cons:
- more operational complexity
- heavier than needed for the current scale

Conclusion:
- keep as a future option, not the first move

## Recommended Final Shape

### For active cycles
- JSONL rounds
- JSON status
- CLI writes
- rendered markdown view when needed

### For roadmap
- keep current markdown if you want the smallest change set, or
- move to structured metadata later after the cycle path is fixed

If roadmap is migrated, JSON is the simplest performance-oriented option and YAML is the simplest human-editable option. Either is fine; this is less important than cycle storage.

## Suggested Rollout

### Phase A
- implement `cycle.py`
- implement `cycle add/status/rounds/init`
- update `/handoff` skill to use CLI commands
- update parser/server/TUI/web to read structured cycles
- keep markdown rendering as a compatibility view

### Phase B
- add migration commands for legacy cycle files
- add explicit tests for migration paths

### Phase C
- decide whether roadmap migration is still worth doing after Phase A lands

## Recommendation For Claude
If the goal is speed without shallower reviews, prioritize **cycle storage and CLI writes only** first.

Do **not** spend the first pass replacing every markdown document in the system.

The main gain comes from removing manual markdown editing from the active handoff loop.
