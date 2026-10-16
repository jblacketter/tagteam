# Phase 76: Field fixes — roadmap `(slug)`, lead state line, iTerm2 `cd`

## Status
- [x] Planning: plan approved round 1 (2026-10-16)
- [x] Implementation
- [x] Implementation Review: approved round 2 (2026-10-16); gate: 2,693 passed, 5 skipped at `33c7e94`
- [x] Complete (merged 2026-10-16, PR #66)

## Summary
Three defects found while using tagteam on real projects, each logged with evidence and still
present on `main` @ `b5cfaa4` (3.14.10). One small phase, because each fix is local and none of
them changes a cycle state, a CLI flag or the contract.

1. **Roadmap slugs ignore the `(slug)` a heading names.** From
   `docs/tagteam-issue-roadmap-slug-and-cockpit-state-line-2026-09-24.md` §1 (Liminal, 3.14.9).
2. **The lead-chat state line prints `turn None` and omits the result.** Same note, §2.
3. **The iTerm2 backend's injected `cd` can be eaten by shell startup**, so the lead launches in
   `~`. From `docs/tagteam-issue-iterm-lead-tab-cd-dropped-2026-09-04.md` (sonicgrid, 3.11.0).

## Scope

### 1. A heading's trailing `(slug)` is the phase slug
`parse_roadmap()` sets `slug = _slugify(name)` (`tagteam/roadmap.py:160`), and
`validate_identities()` does the same independently (`roadmap.py:317`). For
`### Phase 32: Ask your words — Answer me later (answer-later)` the slug is
`ask-your-words-answer-me-later-answer-later`, so `roadmap queue answer-later` fails with
"not found", and a full-roadmap run would name its cycles and phase docs after the long slug.

**Rule.** When a phase heading's title ends in `` (`<s>`) `` with `<s>` matching
`^[a-z0-9]+(?:-[a-z0-9]+)*$`, or in a bare `(<s>)` with at least one hyphen
(`^[a-z0-9]+(?:-[a-z0-9]+)+$`) — already a slug, nothing for `_slugify` to change — then `<s>` is the phase slug and the display name is the title with that suffix (and
the whitespace before it) removed. Every other title keeps today's slug. The match is strict on
purpose: `(OAuth)`, `(B0)`, `(Performance)`, `(3.0 arc)` and `(optional, defer if …)` are not
slugs and stay part of the name.

*Implementation note:* the hyphen requirement for a bare suffix was added during implementation.
An existing test (`TestDependencyProblemLines`, from bugalizer's real roadmap) has the heading
`Private repo access (proposed)`: a one-word lowercase annotation, not a slug. Every bare suffix in
the registry is hyphenated (Liminal's 21), and perfalyzer's one single-word slug, `reports`, is
backticked, so the measured blast radius is unchanged.

**One helper, two callers.** A new `_heading_identity(title) -> (name, slug)` is used by both
`parse_roadmap()` and `validate_identities()`, so `roadmap check`'s duplicate-slug check sees
the slug the parser uses. `_resolve_ref` needs no change: it already matches `p.slug`, which is
now the short slug, so `- **Depends on:** answer-later` resolves. The exact-name form matches
the stripped name.

**Blast radius, measured** (2026-10-15, over the 43 projects in `~/.tagteam/projects.json`,
with the rule above): headings change slug in **Liminal (21)** and **perfalyzer (6, backticked)**
only. Neither project's `handoff-state.json` is on a phase whose slug changes, and neither has a
full-roadmap run in flight. Liminal's cycles and phase docs already use the short slugs, which is
the point of the fix. For any project, completed phases are matched by status, not by slug, so
the change does not reopen or skip any phase.

### 2. Lead-chat state line
`lead_chat._state_line()` (`tagteam/lead_chat.py:189`) renders
`… · status done · turn None`. Change it to:
- an empty turn renders as `turn —` (the handoff banner's convention);
- when `result` is set, the status reads `status done (approved)`;
- otherwise the line is unchanged.

### 3. iTerm2: no agent launches outside the project directory
`iterm.create_session()` writes `cd <abs_dir>` into each new tab before the shell reaches its
prompt. A startup read (oh-my-zsh's `[Y/n]` update prompt in the evidence) swallows the `c`,
`d /Users/…` fails, the tab stays in `~`, and the `claude` typed next starts there.

Two changes, both in `tagteam/iterm.py`:

a. **Verify each tab's directory before launching, and re-send the `cd` once.** iTerm2 reports a
   session's working directory as the variable `session.path`. Checked live on 2026-10-15: it
   answers without iTerm2 shell integration installed. After the tabs are created (and only when
   `launch` is set), poll each tab's `session.path` until it equals `abs_dir`, with a short
   timeout. A tab that is still elsewhere gets its `cd` re-sent once (by then the startup read
   has finished, so the re-send lands), and is polled again. If it is still wrong, `create_session`
   prints the tab's role, its path and its last screen lines, sends no launch command to any tab,
   and returns False. A wrong-directory tab is a session-start error, not a silent
   "No active cycle" a minute later. If `session.path` cannot be read at all (empty or an
   AppleScript error), there is nothing to verify against: print one `note:` and continue, so
   the change never blocks a setup where the check is unavailable. *Implementation note:* a
   brand-new tab reports `missing value` until its shell has started (observed live), so an
   empty path means "not known yet" and is polled until the deadline; only a path that never
   becomes readable gets the note. iTerm2 reports the path as the shell spells it (`/tmp`,
   not `/private/tmp`), so it is compared by `realpath` on both sides.

b. **Launch lines carry their own `cd`.** The lead, reviewer and watcher launch commands are
   sent as `cd <quoted abs_dir> && <command>`. If a swallowed character breaks the line, it
   breaks the whole line visibly (`command not found`), and nothing starts in the wrong
   directory. This is the guarantee; (a) is what makes the common case heal itself.

**Out of scope:** the Terminal.app backend (`tagteam/terminal.py`) types `cd` the same way and
may share the race. It was never observed there, and its window accounting is different, so it
is left for a follow-up if it shows up. tmux is unaffected (`-c <dir>`).

## Technical Approach
- `roadmap.py`: `_SLUG_SUFFIX_RE` + `_heading_identity()`; `parse_roadmap()` and
  `validate_identities()` call it. `RoadmapPhase.name` is the stripped name.
- `lead_chat.py`: `_state_line()` as above.
- `iterm.py`: a `_session_path(sid)` reader (one `osascript` call, returns `""` on failure); a
  `_wait_for_tab_dirs(ids, abs_dir, read_path, write, read_contents, timeout, poll)` helper that
  takes its I/O as arguments so tests drive it with fakes and a virtual clock; launch commands
  built with `shlex.quote(abs_dir)`.

## Files
- `tagteam/roadmap.py`, `tagteam/lead_chat.py`, `tagteam/iterm.py`
- `tests/test_roadmap.py` (or the existing roadmap test file), `tests/test_orders.py`
  (`test_state_line` lives there) or a lead-chat test, `tests/test_iterm.py`
- `CLAUDE.md` — one line on the roadmap `(slug)` rule
- `docs/roadmap.md`, this file

## Success Criteria
1. `### Phase 32: Ask your words — Answer me later (answer-later)` parses to slug
   `answer-later`, name `Ask your words — Answer me later`; the backticked form does too.
   `(OAuth)`, `(B0)`, `(3.0 arc)` and a heading with no suffix keep today's slug (tests).
2. `roadmap queue answer-later` starts from that phase, and a `Depends on: answer-later` line
   resolves (tests). Two phases whose slugs collide only under the new rule are reported by
   `roadmap check` as a duplicate slug (test).
3. Re-run the measurement script over the registry with the real parser: only Liminal and
   perfalyzer slugs change, and `roadmap check` stays clean on both (recorded in the impl
   submission). Read-only: nothing is written to either project.
4. `_state_line` renders `turn —` for an empty turn and `status done (approved)` when `result`
   is set (tests).
5. iTerm2 (fake I/O, virtual clock): a tab whose path is wrong at first and right after the
   re-sent `cd` launches normally; a tab that stays wrong blocks every launch and
   `create_session` returns False with the role and path in the output; an unreadable path
   prints a note and launches; launch commands start with `cd '<dir>' && ` (tests).
6. Live check on Jack's machine: `tagteam session start --launch` in a scratch project puts
   all three tabs in the project (verified with `session.path`). The oh-my-zsh race itself is
   not reproduced on demand; the fake-I/O tests carry that case.
7. Full suite green through the gate.
