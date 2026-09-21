# Phase 63: Roadmap placeholders are not phases

## Status
- [x] Planning: approved round 3 (2026-09-20) at `61f4a23`
- [x] Implementation: branch `phase/roadmap-placeholders-are-not-phases`
- [x] Implementation Review: approved round 1 (2026-09-20) at `c35e793`; gate 2,074 passed, 5 skipped
- [x] Complete: PR #47 merged 2026-09-20 (rebase; impl is `745cf0e` on main).

## Closeout
```
Phase report: roadmap-placeholders-are-not-phases — plan approved r3 · impl approved r1
  plan   3 rounds · 2 change requests · 0 bounces
  impl   1 round · 0 change requests · 0 bounces · gate 1 run, 7m 41s
  time   start→approve 16m 35s · implementation before first submit 3m 55s
         lead 1m 26s (2 spans, 2 unknown) · reviewer 3m 32s (4 spans) · gate 7m 42s (1 span)  (elapsed; includes relay wait)
  usage  no usage rows stored under this phase
  turns  matched 0 of 8 · no token data 0 · unmatched 6 · unknown 2
```
No reviewer notes at approval. **On release:** note that a `[Name]` heading is
no longer a phase — a roadmap that relied on one (none known) would lose it
from `roadmap ready`/`queue`, with a `warn:` from `roadmap check`.

## Summary
The roadmap `tagteam setup` seeds fails `tagteam roadmap check`. Reproduced on
3.14.1 in an empty directory (seed written by `framework.apply`, then the CLI):

```
roadmap invalid (2 problem(s)):
  - duplicate slug 'name': Phase 1, Phase 2, Phase 3
  - name: depends on itself
```

The seed (`tagteam/data/templates/roadmap.md`) has three headings
`### Phase N: [Name]`. `_slugify("[Name]")` is `name` for all three, so
`validate_identities()` reports a duplicate slug, and Phase 3's
`- **Depends on:** Phase 2` resolves to slug `name` — its own.

Every new project therefore starts with a roadmap tagteam itself calls
invalid. It stays that way until the owner renames all three: 8 of the 41
registered projects still carry the unedited seed and report exactly this
(agent-gate, agent-ledger, jobs/demoapp, northstar/clearpath-cloud,
screen_work, skill-forge, token-economy, token-mint); linkedin-articles, whose
first cycle ran without touching the roadmap, reports both lines. Ordinary
`/tagteam:handoff` cycles are unaffected; `roadmap ready` / `queue` / `graph`
and full-roadmap mode are refused. Logged as issue 8 in
`docs/tagteam-issues-release-and-venv-upgrade-2026-09-20.md`.

The seed also ends with a "Getting Started" list naming `/phase`,
`/plan create [phase]` and `/status` — commands that have not existed since
the plugin (they predate even the `/handoff-*` family the Phase 49 audit looks
for, which is why that audit never caught them).

**Two ways to fix it, and why this plan takes the second.** (a) Give the seed
three distinct placeholder titles. `check` passes — and `roadmap ready` then
offers `first-phase-name` as a startable phase, and full-roadmap mode would
open a plan cycle for it. It also fixes nothing in the 9 existing projects.
(b) Say what is true: a heading whose whole title is a bracketed placeholder
is not a phase yet. Existing projects become valid without anyone editing
them, and the owner is told which lines to rename.

## Scope
**In:**

1. **Placeholder headings are not phases** (`tagteam/roadmap.py`). A
   placeholder is a phase heading whose name, stripped, matches `^\[[^\]]*\]$`
   — the whole title is one bracketed token (`[Name]`, `[First phase]`). A
   title that merely contains brackets (`Parser [v2]`) is an ordinary phase.
   - `parse_roadmap()` does not return placeholders. Sections are still split
     on **every** heading, so a placeholder's `Status` / `Depends on` lines
     never bleed into the real phase above it.
   - `validate_identities()` ignores them (no slug, no number claim).
   - `parse_roadmap()`'s contract is otherwise unchanged: it still raises
     `ValueError` when there is no real phase — `launch.py`, `watcher.py` and
     `worktree.py` all catch that today, and an empty list would be a new
     shape for them. The message gains the cause when placeholders exist:
     `No phases found in … — 3 placeholder heading(s) ([Name]); rename them to
     make them phases.`
   - A real phase that depends on a placeholder (`Depends on: Phase 2` where
     Phase 2 is `[Name]`) is an unknown dependency, as it would be for any
     phase that does not exist. Unchanged behaviour, correct outcome.
2. **`roadmap check` says so.** New `placeholder_phase_headings(text) ->
   list[(line_no, line)]`, printed through Phase 60's warning channel:
   `warn: placeholder phase heading (line 12): ### Phase 1: [Name] — rename it
   to make it a phase`. Warnings change no exit code. When the roadmap has
   placeholders and no real phase — the fresh seed — `check` prints the
   warnings and `roadmap ok: 0 phase(s) — 3 placeholder heading(s) to rename`,
   exit 0. `ready` / `queue` / `graph` on that roadmap keep exiting 1 with the
   improved message: there is nothing to start.

   **Identity problems are never masked by that** (plan round 2, reviewer
   finding 1). `graph_problems()` computes `validate_identities()` and then
   calls `parse_roadmap()`, whose no-real-phase `ValueError` would discard the
   list — so `### Phase 1: [Name]` + a bare `### Phase 2:` (empty name, a real
   identity problem) would have been reported `ok`. The placeholder-only
   success is therefore decided on positive evidence, not by catching an
   exception: `check` runs `validate_identities()` itself first; any problem →
   `roadmap invalid`, exit 1, problems listed (warnings still printed). Only
   with zero identity problems, ≥1 placeholder and **no** heading the lenient
   pattern matches that is not a placeholder does it print the `ok: 0` line.
   Every other `ValueError` (no headings at all, …) is reported exactly as
   today.
3. **Seeds get their own directory.** `SEED_FILES` reads
   `data/templates/roadmap.md` and `data/templates/decision_log.md` — the same
   files that since Phase 61/62 are the *retired-path provenance*: a project's
   `templates/roadmap.md` is removed because it equals that package file. Edit
   the seed in place and every not-yet-upgraded project's copy stops matching
   and is kept as `custom`; the cleanup quietly breaks for other users. So:
   - new `tagteam/data/seeds/roadmap.md` and `seeds/decision_log.md`
     (`SEED_FILES` points there; `pyproject.toml` glob `data/seeds/*.md`);
   - `data/templates/*` and `data/checklists/*` become frozen, with a test
     that pins each to `git show v3.14.1:…` (skips without tags, like the
     Phase 62 table test).
4. **Fix the seed's dead commands** in `data/seeds/roadmap.md` only:
   "Getting Started" becomes `/tagteam:handoff start [phase]`,
   `/tagteam:handoff status`, `tagteam roadmap ready`, plus one line saying a
   `[Name]` heading is a placeholder until renamed. The three `[Name]`
   headings stay — with item 1 they are harmless and self-explanatory.
   `seeds/decision_log.md` is byte-identical to today's.
5. **The audit learns the older family — with a narrow, pinned exemption**
   (plan round 2, reviewer finding 2). A new test in `TestShippedDocsAudit`
   rejects `` `/phase` ``, `` `/plan …` `` and `` `/status` `` written as
   commands in shipped `.md` files. A read-only search finds exactly two
   shipped files with them, both frozen provenance whose bytes must not change
   (they are what makes a project's old `templates/roadmap.md`
   *reconstructible* and therefore removable without git):
   - `tagteam/data/templates/roadmap.md` (the v3.4.0 – v3.14.1 source), and
   - `tagteam/data/history/v3.3.0/templates/roadmap.md` (the earlier one).

   The exemption is an explicit allowlist of those two **paths** — not a
   directory, not a pattern — and the test asserts that every allowlisted path
   is covered by a tag-pin test (`history/…` by Phase 62's table test,
   `data/templates/…` by item 3's freeze test), so an exempt file cannot drift.
   Neither file is edited. The existing `/handoff-*` audit is untouched: it
   keeps scanning everything, including both of these files.
6. **Docs:** `CLAUDE.md` (seeds vs frozen templates; placeholders);
   `tagteam/data/workflows.md` one sentence where the roadmap is described.
   This repo's `docs/workflows.md` refreshed by `tagteam setup` as the last
   implementation step.

**Out:**
- Editing any existing project's roadmap. Item 1 makes the 9 affected
  projects valid as they are.
- Line numbers in `unknown dependency` problems (issue 9) — same file,
  different defect; its own small change.
- `northstar-test-automation`'s duplicate phase numbers 7 and 8 — real
  duplicates, the owner's to renumber.
- Decoupling bench's checklist fallback from the frozen `data/checklists/`.
  Nothing needs to change there yet.

## Technical Approach
- `_PLACEHOLDER_NAME_RE = re.compile(r"^\[[^\]]*\]$")`; `_is_placeholder(name)`.
- `parse_roadmap()`: iterate all strict headings for section boundaries; skip
  appending when `_is_placeholder(name)`; raise as today when `phases` is
  empty, with the placeholder count in the message.
- `validate_identities()`: `continue` on placeholder names, before the
  number/slug bookkeeping.
- `placeholder_phase_headings()`: lenient regex, so it sees what
  `validate_identities` sees.
- `roadmap_command("check")` — one algorithm, the same one Scope item 2
  describes (plan round 3: the round-2 text ran identity validation only
  inside the `placeholder_only` branch, which `[Name]` + bare `### Phase 2:`
  never enters, so its identity problem was still lost):
  1. Print the unparsed-heading and placeholder warnings.
  2. `identity = validate_identities(text)` — always, up front, kept.
  3. `has_real_phase(text)`: some *strict*-pattern heading is not a
     placeholder — i.e. exactly the condition under which `parse_roadmap()`
     returns instead of raising.
  4. **No real phase** (the case where `graph_problems()` would raise and
     drop `identity`):
     - `identity` non-empty → `roadmap invalid (N problem(s))`, the problems
       listed, exit 1. Covers the placeholder + bare-heading fixtures.
     - `identity` empty and ≥1 placeholder → `roadmap ok: 0 phase(s) — N
       placeholder heading(s) to rename`, exit 0.
     - `identity` empty and no placeholder → fall through to step 5, which
       reports today's `No phases found` error, exit 1.
  5. **Otherwise** the existing `graph_problems()` path, unchanged: identity +
     graph problems aggregated, any `ValueError` / `FileNotFoundError` printed
     as `Error: …`, exit 1. No `except` ever produces a success.
- `framework.SEED_FILES` → `seeds/…`. `_retired_sources()` untouched.

## Files
- `tagteam/roadmap.py`, `tagteam/framework.py`
- `tagteam/data/seeds/roadmap.md`, `tagteam/data/seeds/decision_log.md`, `pyproject.toml`
- `tests/test_roadmap*.py`, `tests/test_framework.py`, `tests/test_plugin.py`
- `CLAUDE.md`, `tagteam/data/workflows.md`, `docs/workflows.md`, `docs/roadmap.md`

## Success Criteria
1. **The regression itself:** fresh directory → framework plan applied →
   `roadmap_command(["check"])` returns 0, prints three
   `warn: placeholder phase heading` lines and
   `roadmap ok: 0 phase(s) — 3 placeholder heading(s) to rename`.
2. The pre-Phase-63 seed bytes (what the 9 projects have) → same result: valid
   without an edit.
3. Two real phases + one `[Name]` placeholder carrying `Status` and
   `Depends on` lines → 2 phases; the placeholder's lines attach to neither;
   `check` ok with one warning; `ready` lists the real ones only.
4. `### Phase 4: Parser [v2]` is a phase (slug `parser-v2`). `[ ]` and `[]`
   are placeholders.
5. Real phase depending on a placeholder by number → `unknown dependency`,
   exit 1 (unchanged).
6. Two `[Name]` placeholders sharing a phase number with a real phase → no
   duplicate-number problem from the placeholders.
7. **Masking regression (finding 1):** `[Name]` placeholder + bare
   `### Phase 2:` → `check` exits 1 and lists `Phase 2: empty name`; two bare
   `### Phase 2:` headings + a placeholder → exit 1 with both the empty-name
   and duplicate-number problems; the untouched seed → exit 0.
8. Roadmap with no headings at all → the existing `No phases found` error,
   wording unchanged; `ready` on the fresh seed → exit 1 with the
   placeholder-count message.
9. `launch._actionable_phases`, `launch._next_after` and the watcher's
   roadmap read on a fresh seed behave exactly as on a roadmap with no phases
   today (`[]`, `(None, True)`, one problem string) — asserted, not assumed.
10. `data/templates/*` and `data/checklists/*` equal `git show v3.14.1:…`
   (skip without tags); the Phase 61/62 retire tests pass unchanged.
11. A fresh setup's `docs/roadmap.md` equals `data/seeds/roadmap.md` and names
    no dead command; a wheel contains `data/seeds/*.md`.
12. **Audit (finding 2):** the older-family test fails on a shipped `.md`
    outside the allowlist (proved with a temp file through the same scan
    function), passes on the tree, its allowlist is exactly the two paths
    above, each allowlisted path is tag-pinned, and the `/handoff-*` audit
    still scans both files and passes.
13. Real registry, read-only: `tagteam roadmap check` in the 9 affected
    projects, before → after, pasted into the impl submission
    (`northstar-test-automation` excluded — its problem is a different one).
14. Gate: full suite green via `on_submit`.

## Registry check (criterion 13) — read-only `tagteam roadmap check`, 2026-09-20
| Project | 3.14.1 | this branch |
|---|---|---|
| agent-gate, agent-ledger, jobs/demoapp, northstar/clearpath-cloud, screen_work, skill-forge, token-economy, token-mint | `roadmap invalid` — duplicate slug 'name' | `roadmap ok: 0 phase(s) — 3 placeholder heading(s) to rename` |
| linkedin-articles | `roadmap invalid` — duplicate slug 'name'; name: depends on itself | same `ok: 0 phase(s)` line |

Across all 41 registered projects on this branch, one roadmap is still
invalid: `northstar-test-automation` (duplicate phase numbers 7 and 8 — real
duplicates, out of scope).
