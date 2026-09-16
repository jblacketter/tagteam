# Phase 60: Roadmap parser: decimals, deployed, no silent drops

## Status
- [x] Planning: approved round 1 (2026-09-15)
- [x] Implementation: branch `phase/roadmap-parser-decimals-deployed-no-silent-drops`
- [x] Implementation Review: approved round 1 (2026-09-16) at `3f23845`
- [ ] Complete: PR open, merge pending

## Summary
Two parser defects found on 2026-09-15 while running tagteam against
`~/Projects/liminal`, plus the failure mode underneath both.

**1. A decimal phase number is invisible.** `_PHASE_HEADING_RE` requires
`Phase[ \t]+(\d+)([A-Za-z]?):` — on `### Phase 9.1:` the `\d+` matches `9`,
the optional letter matches empty, the pattern then demands `:` and finds `.`,
so the heading never matches. Reproduced against the editable install:

```
strict  headings: ['### Phase 9: Ordinary', '### Phase 10a: Letter suffix']
lenient headings: ['### Phase 9: Ordinary', '### Phase 10a: Letter suffix']
dep ref 'Phase 9.1' resolves: False
```

`_PHASE_HEADING_LENIENT_RE` misses it too, so the phase is absent from
duplicate-identity validation as well as from parsing — nothing anywhere
reports it. On Liminal: 28 `### Phase` headings, 26 parsed, silent for roughly
two months.

This is a direct sequel to Phase 59, which fixed the same bug for letters and
explicitly ruled dotted suffixes out of scope as "speculation". They were not
speculation.

**2. `Deployed` is not a terminal status.** `is_terminal_status("Deployed to
prod")` returns `False`, so a deployed phase stays in `roadmap ready` and
`roadmap queue` forever. Evidence in Liminal `docs/phases/roadmap-structure.md`
§ Findings for the Arbiter.

**3. The real defect is the silence.** Neither of the above announced itself.
A heading that looks like a phase and does not parse is dropped without a
word, which is why #1 survived two months on Liminal and why Phase 59 existed
at all. Fixing only the two regexes leaves the next unrecognized shape
(`### Phase 9-1:`, `### Phase IX:`) equally silent. Arbiter decision
(2026-09-15): fix the class, not just the two instances.

## Scope
**In** — `tagteam/roadmap.py` only:

1. `_PHASE_HEADING_RE` and `_PHASE_HEADING_LENIENT_RE` accept an optional
   dotted numeric suffix *or* the existing single letter:
   `Phase[ \t]+(\d+)((?:\.\d+)?|[A-Za-z]?):`. The letter form is unchanged.
2. `_PHASE_NUM_REF_RE` and `_PHASE_NUM_SLUG_REF_RE` accept the same widened
   suffix, so `- **Depends on:** Phase 9.1` resolves.
3. `_TERMINAL_STATUS_WORDS` gains `deployed`.
4. **New:** `unparsed_phase_headings(roadmap_text) -> list[tuple[int, str]]`
   returns `(line number, line)` for every line matching
   `^###[ \t]+Phase\b` that the *lenient* regex does not match. `roadmap check`
   prints these as `warn:` lines.

**Out:**
- Multi-level decimals (`9.1.2`), roman numerals, `9-1`. One decimal level
  covers the observed convention; the new warning makes any further shape
  loud instead of silent, which is the point.
- Ordering. `number` is still not a sort key; queue order stays document
  order / stable topological. `Phase 9.1` does not sort between `9` and `10`
  because nothing sorts on `number` at all.
- Any implied parent/child relationship between `Phase 9` and `Phase 9.1`.
  Dependencies stay explicit.
- Renumbering or editing any roadmap, here or on Liminal.
- `tagteam upgrade` reporting newly-visible phases (arbiter decision: ship as
  a plain bug fix, note the behavior change in the release notes).

## Technical Approach

### Identity
Phase identity is already the `(number, suffix)` pair from Phase 59, and
`suffix` is already `str`. A decimal is just another suffix value: `Phase 9.1`
is `(9, ".1")`. `Phase 9`, `Phase 9a` and `Phase 9.1` are three distinct
phases and none is a duplicate of another — which already falls out of the
existing pair key, with no change to `validate_identities`' logic.

`number` stays `int`. It is read in exactly two places repo-wide
(`_resolve_ref`'s pair comparison and `validate_identities`' pair key), both
of which already treat it as half of an opaque key.

The one ordering touch: `validate_identities` does `sorted(numbers.items())`,
which compares `(9, ".1")` against `(9, "a")` lexically. That is only for
stable error-message output, so lexical is fine — but the plan names it so the
reviewer does not have to rediscover it.

### One shared suffix fragment
Phase 59 widened `(\d+):` to `(\d+)([A-Za-z]?):` by editing four patterns
independently. This change touches the same four. Rather than edit them again
in parallel, the suffix becomes one module-level fragment, `_SUFFIX`, that all
four compose — so the next widening cannot reach three of the four sites.

Written `((?:\.\d+)|[A-Za-z])?` rather than `((?:\.\d+)?|[A-Za-z]?)`: the
latter's first branch matches empty, so `Phase 9a:` only reaches the letter by
backtracking. Two non-empty alternatives under one `?` says what is meant. The
group is optional, so `group(2)` can be `None` — the three call sites read it
as `(m.group(2) or "")`.

### The warning channel (the crux)
`validate_identities` returns `problems`, and `graph_problems` → `check_graph`
**raises `RoadmapGraphError` on any non-empty problem list**. `roadmap ready`,
`queue` and `graph` all go through `check_graph` and print
`Error: roadmap invalid (…)` and exit 1.

So an unparsed heading must **not** become a problem. A project with a stray
`### Phase notes` line would go from "one phase quietly missing" to "roadmap
refused entirely" — strictly worse, and it would fire on upgrade with no
warning. The new check is a separate, non-fatal channel:

- `unparsed_phase_headings()` is its own function, not folded into
  `validate_identities`.
- `roadmap check` prints `warn:` lines and **does not change its exit code**
  for them. `roadmap ok: N phase(s), M edge(s)` still prints, still exits 0.
- `ready` / `queue` / `graph` are untouched.
- Warnings print **before** `graph_problems` runs. That call raises
  `ValueError("No phases found")` when *every* heading is unsupported, and
  returns problems for unrelated graph errors; in both cases the unsupported
  headings are the likeliest cause, so they must reach the screen first.
  Existing error exit codes are preserved exactly (reviewer note, round 1).

Suspect pattern is `^###[ \t]+Phase\b`. `\b` keeps `### Phases` and the `##
Phases` section heading out; `### ~~Cockpit hardening~~ → promoted to Phase 43`
does not start with `### Phase` and is likewise not flagged.

Sample output:

```
$ tagteam roadmap check
warn: unparsed phase heading (line 88): ### Phase IX: Roman numerals
roadmap ok: 60 phase(s), 14 dependency edge(s)
```

## Files
- `tagteam/roadmap.py` — the four changes above.
- `tests/test_roadmap.py` — new cases (see Success Criteria).
- `docs/roadmap.md` — Phase 60 entry.
- Release notes — the behavior-change note. There is no `CHANGELOG.md` in this
  repo; releases go out through `scripts/release.py`, so the note belongs in the
  GitHub release body for the version that ships this. Recorded here and in the
  roadmap entry so it is not lost between now and then.

## Success Criteria
1. `### Phase 9.1: Name` parses; the phase appears in `parse_roadmap`,
   `roadmap phases`, `roadmap queue` and `roadmap ready`.
2. `- **Depends on:** Phase 9.1` resolves to that phase; an edge appears in
   `roadmap graph`.
3. A roadmap holding `Phase 9`, `Phase 9a` and `Phase 9.1` reports **no**
   duplicate-number problem, and each resolves only to itself.
4. Existing letter-suffix behavior (Phase 59) is unchanged — its tests pass
   untouched.
5. `is_terminal_status("Deployed 2026-09-15")` is `True`; a `Deployed` phase
   drops out of `roadmap ready`.
6. `### Phase IX: Roman` produces a `warn:` line from `roadmap check` and
   `roadmap check` still exits **0**; `roadmap ready` still works.
7. A roadmap with no suspect lines prints no `warn:` line (no new noise on
   the 59 existing phases in this repo).
8. Full suite green via the gate.

## Verification
Gatekeeper is `on_submit: true`, so the impl submission's `cycle add` runs the
one full-suite run on the record. Focused runs on `tests/test_roadmap.py`
while working. Real-world check, run before the impl submission — **result: inconclusive, and
it does not support the fix.** `~/projects/journaling/Liminal/docs/roadmap.md`
now parses 29 of 29 headings with **no** unparsed headings and no non-terminal
statuses, on the pre-fix parser *and* the post-fix parser alike. The roadmap has
been edited since the 2026-09-15 finding was recorded (the decimal headings
appear to have been renamed as a workaround), so it no longer reproduces
anything. The defects are demonstrated by the recorded repro and the new tests,
not by Liminal.

## Risks
- **Behavior change on upgrade.** Projects with decimal phases will see those
  phases appear in `ready`/`queue` for the first time. Accepted by the arbiter
  as a plain bug fix; goes in the release notes.
- **New warning on existing roadmaps.** If a project has a `### Phase …` line
  that was never meant to be a phase, it now prints a `warn:`. Non-fatal by
  construction; that is the whole design of the separate channel.


## Closeout

```
Phase report: roadmap-parser-decimals-deployed-no-silent-drops — plan approved r1 · impl approved r1
  plan   1 round · 0 change requests · 0 bounces
  impl   1 round · 0 change requests · 0 bounces · gate 1 run, 5m 56s
  time   start→approve 12m 32s · implementation before first submit 4m 44s
         lead unknown (0 spans, 2 unknown) · reviewer 1m 51s (2 spans) · gate 5m 57s (1 span)  (elapsed; includes relay wait)
  usage  no usage rows stored under this phase
  turns  matched 0 of 4 · no token data 0 · unmatched 2 · unknown 2
```

Gate: 2,030 passed, 5 skipped (5m56s) at `3f23845`. Reviewer accepted the gate
without rerunning the suite and did a focused run of `tests/test_roadmap.py`
(102 passed) plus `git diff --check`.

**Carried forward — do not lose these:**

1. **Release note.** There is no `CHANGELOG.md`; whenever this ships, the GitHub
   release body must carry: *decimal-numbered phases that were previously
   omitted can now enter `roadmap ready` / `roadmap queue`.* Reviewer restated
   this requirement on approval.
2. **Liminal.** The live roadmap no longer reproduces the defect, and the
   reason is now confirmed rather than inferred: Liminal renumbered `9.1`/`9.2`
   to `9a`/`9b` to unblock itself, using the letter form Phase 59 had made work.
   That workaround can be reverted once this ships. Liminal's call, not this
   phase's.
