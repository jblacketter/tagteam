# Phase 66: Roadmap dependency problems name their line

## Status
- [ ] Planning
- [ ] Implementation: branch `phase/roadmap-dependency-problems-name-their-line`
- [ ] Implementation Review
- [ ] Complete

## Summary
`tagteam roadmap check` reports a bad `Depends on:` reference without saying
where it is:

```
roadmap invalid (2 problem(s)):
  - private-repo-access-proposed: unknown dependency 'Phase 8 (credential code)'
  - private-repo-access-proposed: unknown dependency 'Dan's token for acceptance'
```

Both came from one line (bugalizer `docs/roadmap.md:176`, 2026-09-20) where
free text had been written after the phase reference; designwing had the same
mistake the same day. Each took a `grep` to find. Issue 9 in
`docs/tagteam-issues-release-and-venv-upgrade-2026-09-20.md`. The warnings
added in Phases 60 and 63 already carry `(line N)`; the fatal problems do not.

## Scope
**In** — `tagteam/roadmap.py` only:

1. `RoadmapPhase` gains two fields, appended after `suffix` so positional
   construction keeps working (the Phase 59 rule): `line: int = 0` — the
   1-based line of the phase heading — and `dep_lines: dict[str, int]` — for
   each entry of `depends_on` (resolved slug, or the verbatim text when
   unresolved), the 1-based line of the `Depends on:` line that carried it
   (the first one, when a reference is repeated).
2. `parse_roadmap()` fills both. `_split_dep_refs()` returns the line offset
   with each reference; the section's starting line turns it into a document
   line.
3. `validate_graph()` appends ` (line N)` to `unknown dependency` and
   `depends on itself` problems when the line is known (`> 0`). A phase built
   by hand without line data — as several tests and `worktree.py`'s text path
   do — produces exactly today's strings.
4. Everything that relays problems inherits it unchanged: `roadmap check` /
   `ready` / `queue` / `graph`, `RoadmapGraphError`, and the watcher's
   `pause_reason` (`roadmap invalid: b: unknown dependency 'ghost' (line 7)`).

**Out:**
- Line numbers on identity problems (`duplicate slug`, `duplicate phase
  number`, `empty name`). They already name the phases involved, and their
  exact strings are asserted in many places; a separate change if wanted.
- Cycle problems: a cycle is several lines; the slugs are the useful part.
- Any change to how references are split or resolved. Free text after a
  reference stays an unknown dependency — now one that says where it is.

## Technical Approach
- `_split_dep_refs(section) -> list[tuple[str, int]]`: `(ref, line offset
  within the section)`, offset from `section.count("\n", 0, m.start())`.
  De-duplication unchanged (first occurrence wins, so it keeps the first line).
- In `parse_roadmap()`: `heading_line = content.count("\n", 0, match.start())
  + 1`; the section starts at `match.end()`, which is on the heading's line,
  so `dep line = heading_line + offset`.
- Second pass: `dep_lines[value] = line` alongside `depends_on.append(value)`,
  keyed by the same value (`target.slug` or the verbatim ref).
- `validate_graph()`: `where = f" (line {n})" if (n := p.dep_lines.get(dep, 0))
  else ""`.

## Files
- `tagteam/roadmap.py`, `tests/test_roadmap.py`
- `docs/tagteam-issues-release-and-venv-upgrade-2026-09-20.md` (issue 9),
  `docs/roadmap.md`

## Success Criteria
1. The bugalizer line, reproduced as a fixture: both problems end `(line N)`
   with the correct N; `roadmap check` exits 1 and prints them.
2. `depends on itself` carries its line.
3. A reference repeated on two `Depends on:` lines reports the first line;
   several references on one line share it; CRLF input gives the same numbers.
4. A placeholder heading (Phase 63) between two phases does not shift the
   numbers; `dep_lines` for a resolved reference holds the right line too.
5. Hand-built `RoadmapPhase` objects (no line data) → the exact old strings;
   positional construction `RoadmapPhase("a", "A", "Not Started", 1, [], "")`
   still works.
6. The watcher's `pause_reason` carries the line (existing test updated — the
   one expectation in the suite that changes, listed in the submission).
7. Read-only, real roadmaps: `tagteam roadmap check` is still `ok` for this
   repo and for the registered projects that were ok before (the two Northstar
   repos are not run).
8. Gate: full suite green via `on_submit`.
