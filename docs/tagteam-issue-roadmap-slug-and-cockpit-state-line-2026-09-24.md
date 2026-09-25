# tagteam issues: roadmap slugs ignore the `(slug)` in a heading; the cockpit state line prints `turn None`

Observed 2026-09-24 on the Liminal project (`~/projects/journaling/Liminal`), tagteam 3.14.9,
while starting Phase 32 (`answer-later`) from the cockpit. Written by the lead session (claude)
at Jack's request. Evidence is from the CLI and the package source; what was not exercised is
marked.

## 1. A roadmap phase's slug is the whole heading title, never the `(slug)` it names

Liminal's roadmap headings — every one of its 34 phases — carry the phase slug in
parentheses, and every cycle, phase doc and branch uses that short slug:

```
### Phase 32: Ask your words — Answer me later (answer-later)
### Phase 28: Ask Your Words — Personal Grimoire (personal-grimoire)
```

`parse_roadmap` slugifies the whole name (`tagteam/roadmap.py:160`, `slug = _slugify(name)`),
so the parser's slug for Phase 32 is `ask-your-words-answer-me-later-answer-later`:

```
$ tagteam roadmap queue            # with Phase 32 the only incomplete phase
ask-your-words-answer-me-later-answer-later

$ tagteam roadmap queue personal-grimoire
Error: Phase 'personal-grimoire' not found in docs/roadmap.md.
```

`_resolve_ref` (`roadmap.py:218`) has the same blind spot: a `Depends on:` written with the
short slug resolves only through the exact-name or `Phase N` forms, never through the slug the
project uses everywhere else.

**Consequences.**
- Single-phase mode (how Liminal runs) is unaffected in practice: the lead names the cycle
  with the short slug and nothing joins it to the roadmap entry. That is also why this went
  unnoticed: `tagteam roadmap check` reports `roadmap ok: 34 phase(s)` and warns about nothing.
- **Full-roadmap mode would break the convention (not exercised).** The queue's slugs become
  cycle phase names, so the watcher would start `ask-your-words-answer-me-later-answer-later`
  cycles and the contract would point the lead at `docs/phases/<long-slug>.md`. The project's
  plan is `docs/phases/answer-later.md`.
- `roadmap queue <slug>` / `roadmap worktree` cannot start from a phase by the name the
  project knows it by.

**Suggested fix.** When a heading name ends in `(<slug>)` and that slug already matches
`_slugify` of itself, use it as the phase slug and strip it from the display name. This is
additive: headings without the suffix keep today's slugs. Add a `roadmap check` note when
two phases' slugs collide under either form. Liminal's cycle history (`docs/handoffs/*`,
`handoff-state.json`) already uses the short slugs, so no migration is needed there.

## 2. The cockpit's lead prompt prints Python `None` and omits the result

The lead-chat preamble (`tagteam/lead_chat.py:189-198`, `_state_line`) rendered as:

```
Current handoff state: phase loom-experiments · impl · round 1 · status done · turn None.
```

- `turn None` is a Python `None` leaking into prose. When a cycle is `done` the turn is
  legitimately empty; `turn —` (what the handoff banner uses) or dropping the field would read
  correctly.
- `result` is not in the line. For a `done` cycle, `approved` vs `aborted` is what decides
  "start the next phase" vs "look at what went wrong", so the lead had to read
  `handoff-state.json` to learn it. Suggest `status done (approved)`.

Minor; it didn't cause a wrong action.

## Not observed

The rest of the session's handoff machinery worked as specified: `cycle init` for the plan,
the codex rounds, the SessionStart/resume hooks that reported the correct phase, type, round
and status, and `gate status` showing `Gatekeeper: off`.

*Correction (2026-09-25):* an earlier draft said two parser defects were still open: a Status
of `Deployed` not counting as terminal, and decimal phase headings. That claim came from a
stale session note. Both were fixed in Phase 60 (shipped in 3.13.0). Re-checked on 3.14.9:
`### Phase 9.1:` parses, and a `Deployed …` status keeps the phase out of `roadmap ready`.
